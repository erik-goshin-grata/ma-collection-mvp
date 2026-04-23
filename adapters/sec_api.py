"""
sec-api.io adapter — Stages 5 & 6.

Stage 5 (sec_trigger_detect) calls detect_public_party() to determine whether
a PR source mentions a publicly traded party, then sets sec_lookup_status on
the staging_extraction row.

Stage 6 (sec_enrich) calls run_per_transaction() for each TRIGGERED row to
fetch the corresponding 8-K Item 1.01 text and, where present, Exhibit 2.1.
Both are inserted into source_raw as T1 sources.

Spec: specs/adapter_sec_api.md
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from config import Config
from logger import get_logger
from utils import content_hash as _content_hash

_FILING_QUERY_URL = "https://api.sec-api.io"
_EXTRACTOR_URL = "https://api.sec-api.io/extractor"

_EXCHANGE_TICKER_RE = re.compile(
    r"\b(NYSE|NASDAQ|NYSE American|OTCQB|OTCQX)\s*[:]\s*([A-Z]{1,5})\b"
)
_SEC_LANGUAGE = frozenset([
    "Form 8-K",
    "files with the SEC",
    "Securities and Exchange Commission",
    "Exchange Act",
    "Regulation FD",
])
_PUBLIC_BOILERPLATE = frozenset(["publicly traded", "common stock"])

_EX_21_RE = re.compile(r"(EX-?2[-_.]?1|EXHIBIT\s*2\.?1|^2\.1)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Trigger detection (Stage 5)
# ---------------------------------------------------------------------------

def detect_public_party(
    clean_text: str,
    target_name: str | None,
    acquirer_name: str | None,
) -> dict | None:
    """Detect public-party signals in a PR Newswire clean_text.

    Returns a dict with keys {trigger_signal, side, ticker} if any trigger
    fires, or None when no signal is found.

    Side values: TARGET | ACQUIRER | BOTH
    """
    ticker_matches = list(_EXCHANGE_TICKER_RE.finditer(clean_text))
    sec_hit = next((p for p in _SEC_LANGUAGE if p in clean_text), None)
    bp_hit = next((p for p in _PUBLIC_BOILERPLATE if p in clean_text), None)

    if not ticker_matches and sec_hit is None and bp_hit is None:
        return None

    signals: list[str] = []
    side = "BOTH"
    ticker: str | None = None

    if ticker_matches:
        m = ticker_matches[0]
        exchange, tick = m.group(1), m.group(2)
        pos = m.start()

        near_target = False
        near_acquirer = False

        if target_name:
            idx = clean_text.find(target_name)
            if idx >= 0 and abs(pos - idx) <= 200:
                near_target = True

        if acquirer_name:
            idx = clean_text.find(acquirer_name)
            if idx >= 0 and abs(pos - idx) <= 200:
                near_acquirer = True

        if near_target and not near_acquirer:
            side = "TARGET"
        elif near_acquirer and not near_target:
            side = "ACQUIRER"
        else:
            side = "BOTH"

        ticker = tick
        signals.append(f"{exchange}:{tick} near entity name")

    if sec_hit:
        signals.append(f"SEC language: '{sec_hit}'")
    if bp_hit:
        signals.append(f"public boilerplate: '{bp_hit}'")

    return {
        "trigger_signal": "; ".join(signals),
        "side": side,
        "ticker": ticker,
    }


# ---------------------------------------------------------------------------
# Filing query helpers
# ---------------------------------------------------------------------------

def _build_query_string(
    ticker: str | None,
    company_name: str | None,
    announced_date: str,
    window_days: int,
) -> tuple[str, str, str]:
    """Build the Elasticsearch query string and date range.

    Returns (query_string, start_date, end_date) as ISO date strings.
    """
    try:
        anchor = datetime.strptime(announced_date, "%Y-%m-%d")
    except ValueError:
        anchor = datetime.now(timezone.utc)

    start = (anchor - timedelta(days=window_days)).strftime("%Y-%m-%d")
    end = (anchor + timedelta(days=window_days)).strftime("%Y-%m-%d")

    parts = ['formType:"8-K"', 'items:"1.01"']
    if ticker:
        parts.append(f'ticker:"{ticker}"')
    elif company_name:
        parts.append(f'companyName:"{company_name}"')
    parts.append(f"filedAt:[{start} TO {end}]")

    return " AND ".join(parts), start, end


def query_filings(
    cfg: Config,
    session: requests.Session,
    ticker: str | None,
    company_name: str | None,
    announced_date: str,
    log,
) -> list[dict]:
    """POST to the sec-api.io Filing Query API.

    Returns a list of filing dicts (may be empty).  Raises RuntimeError on
    401/403 (auth failure).
    """
    query_str, _, _ = _build_query_string(
        ticker, company_name, announced_date, cfg.sec_date_window_days
    )
    payload = {
        "query": {"query_string": {"query": query_str}},
        "from": "0",
        "size": "10",
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    log.debug("Filing query payload: %s", json.dumps(payload))

    time.sleep(cfg.sec_api_request_delay_seconds)
    for attempt in range(2):
        try:
            resp = session.post(
                _FILING_QUERY_URL,
                json=payload,
                headers={"Authorization": cfg.sec_api_key},
                timeout=30,
            )
        except requests.RequestException as exc:
            if attempt == 0:
                log.warning("Network error on Filing Query: %s — retry in 10s", exc)
                time.sleep(10)
                continue
            log.error("Filing Query failed after retry: %s", exc)
            return []

        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"sec-api.io auth failure ({resp.status_code}) — check SEC_API_KEY"
            )
        if resp.status_code == 429:
            if attempt == 0:
                log.warning("429 from Filing Query — sleeping 30s, retry once")
                time.sleep(30)
                continue
            log.warning("Persistent 429 from Filing Query — skipping")
            return []
        if resp.status_code >= 500 and attempt == 0:
            log.warning("5xx (%s) from Filing Query — retry in 10s", resp.status_code)
            time.sleep(10)
            continue
        if resp.status_code != 200:
            log.error(
                "Unexpected status %s from Filing Query: %s",
                resp.status_code, resp.text[:500],
            )
            return []

        try:
            data = resp.json()
        except ValueError as exc:
            log.error("Filing Query returned non-JSON: %s", exc)
            return []

        filings = data.get("filings") or data.get("hits", {}).get("hits", [])
        if not isinstance(filings, list):
            log.error("Unexpected Filing Query schema: %s", str(data)[:500])
            return []

        return filings

    return []


def _pick_closest_filing(filings: list[dict], announced_date: str) -> dict | None:
    """Return the filing whose filedAt is closest to announced_date."""
    if not filings:
        return None
    if len(filings) == 1:
        return filings[0]

    try:
        anchor = datetime.strptime(announced_date, "%Y-%m-%d")
    except ValueError:
        return filings[0]

    def _delta(f: dict) -> float:
        filed = f.get("filedAt", "")[:10]
        try:
            return abs((datetime.strptime(filed, "%Y-%m-%d") - anchor).days)
        except ValueError:
            return float("inf")

    return min(filings, key=_delta)


# ---------------------------------------------------------------------------
# Item 1.01 extraction
# ---------------------------------------------------------------------------

def fetch_item_101_text(
    cfg: Config,
    session: requests.Session,
    filing_url: str,
    log,
) -> str | None:
    """Call the Item Extractor API and return clean text for Item 1.01, or None."""
    params = {"url": filing_url, "item": "1-1", "type": "text"}
    time.sleep(cfg.sec_api_request_delay_seconds)
    for attempt in range(2):
        try:
            resp = session.get(
                _EXTRACTOR_URL,
                params=params,
                headers={"Authorization": cfg.sec_api_key},
                timeout=60,
            )
        except requests.RequestException as exc:
            if attempt == 0:
                log.warning("Network error on Item Extractor: %s — retry in 10s", exc)
                time.sleep(10)
                continue
            log.error("Item Extractor failed after retry: %s", exc)
            return None

        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"sec-api.io auth failure ({resp.status_code}) on Item Extractor"
            )
        if resp.status_code == 429 and attempt == 0:
            log.warning("429 from Item Extractor — sleeping 30s, retry once")
            time.sleep(30)
            continue
        if resp.status_code >= 500 and attempt == 0:
            log.warning("5xx (%s) from Item Extractor — retry in 10s", resp.status_code)
            time.sleep(10)
            continue
        if resp.status_code != 200:
            log.error("Item Extractor status %s — skipping", resp.status_code)
            return None

        text = resp.text.strip()
        return text if text else None

    return None


# ---------------------------------------------------------------------------
# Exhibit 2.1 retrieval
# ---------------------------------------------------------------------------

def _find_exhibit_21_url(filing: dict) -> str | None:
    """Return the URL of Exhibit 2.1 from a filing dict, or None."""
    doc_files = filing.get("documentFormatFiles") or []
    for doc in doc_files:
        label = (doc.get("type") or "") + " " + (doc.get("description") or "")
        if _EX_21_RE.search(label):
            return doc.get("documentUrl") or doc.get("url")
    return None


def fetch_exhibit_21(
    cfg: Config,
    session: requests.Session,
    exhibit_url: str,
    log,
) -> tuple[str | None, str | None]:
    """Fetch Exhibit 2.1.  Returns (raw_html, clean_text).

    raw_html is populated for HTML responses; clean_text is always the final text.
    """
    import trafilatura  # local import avoids circular at module load time

    time.sleep(cfg.sec_api_request_delay_seconds)
    try:
        resp = session.get(
            exhibit_url,
            headers={"Authorization": cfg.sec_api_key},
            timeout=60,
        )
    except requests.RequestException as exc:
        log.warning("Network error fetching exhibit %s: %s", exhibit_url, exc)
        return None, None

    if resp.status_code == 404:
        log.warning("404 for exhibit %s — skipping", exhibit_url)
        return None, None
    if resp.status_code != 200:
        log.warning("Status %s for exhibit %s — skipping", resp.status_code, exhibit_url)
        return None, None

    content_type = resp.headers.get("Content-Type", "")
    if "html" in content_type.lower():
        raw_html = resp.text
        clean = trafilatura.extract(raw_html)
        return raw_html, clean or None
    else:
        return None, resp.text.strip() or None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def insert_source_raw(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    url: str,
    title: str,
    published_date: str | None,
    raw_html: str | None,
    clean_text: str | None,
    c_hash: str | None,
    notes: str | None,
    fetched_at: str,
) -> int:
    """Insert a SEC row into source_raw.  Returns the new source_raw_id."""
    cur = conn.execute(
        """
        INSERT INTO source_raw
            (source_type, source_tier, url, title, published_date,
             raw_html, clean_text, content_hash, source_status, notes, fetched_at)
        VALUES
            (?, 'T1', ?, ?, ?,
             ?, ?, ?, 'FETCHED', ?, ?)
        """,
        (
            source_type, url, title, published_date,
            raw_html, clean_text, c_hash, notes, fetched_at,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Per-transaction entry point (Stage 6)
# ---------------------------------------------------------------------------

def run_per_transaction(
    conn: sqlite3.Connection,
    cfg: Config,
    run_id: str,
    extraction_id: int,
    trigger_info: dict | None = None,
) -> dict:
    """Enrich one transaction with SEC filings.

    `trigger_info` is the dict returned by detect_public_party().  If None,
    detection is re-run from the staging_extraction row.

    Returns a result dict matching the spec §5.3 shape.
    """
    log = get_logger("sec_enrich", run_id, level=cfg.log_level)
    session = requests.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    result: dict = {
        "triggered": False,
        "trigger_signal": "",
        "filings_found": 0,
        "filing_accession": None,
        "item_101_fetched": False,
        "exhibit_21_fetched": False,
        "rows_inserted": 0,
        "errors": [],
    }

    # Load extraction row
    row = conn.execute(
        """
        SELECT se.extraction_id, se.target_name, se.acquirer_name,
               se.announced_date, se.target_ticker, se.acquirer_ticker,
               sr.clean_text
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.extraction_id = ?
        """,
        (extraction_id,),
    ).fetchone()
    if not row:
        log.error("No staging_extraction row for extraction_id=%d", extraction_id)
        return result

    target_name = row["target_name"]
    acquirer_name = row["acquirer_name"]
    announced_date = row["announced_date"] or ""
    clean_text = row["clean_text"] or ""
    ticker = row["target_ticker"] or row["acquirer_ticker"]

    if trigger_info is None:
        trigger_info = detect_public_party(clean_text, target_name, acquirer_name)
        if not trigger_info:
            log.info("No public-party signal for extraction_id=%d", extraction_id)
            return result

    result["triggered"] = True
    result["trigger_signal"] = trigger_info.get("trigger_signal", "")

    # Prefer the ticker from detect_public_party if present
    if trigger_info.get("ticker"):
        ticker = trigger_info["ticker"]

    side = trigger_info.get("side", "BOTH")
    lookup_name: str | None = None
    if side == "TARGET":
        lookup_name = target_name
    elif side == "ACQUIRER":
        lookup_name = acquirer_name
    else:
        lookup_name = target_name or acquirer_name

    # Filing query
    try:
        filings = query_filings(cfg, session, ticker, lookup_name, announced_date, log)
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        log.error("Filing query fatal error for extraction_id=%d: %s", extraction_id, exc)
        return result

    result["filings_found"] = len(filings)
    if not filings:
        log.info("No 8-K filings found for extraction_id=%d", extraction_id)
        conn.execute(
            "UPDATE staging_extraction SET sec_lookup_status='NO_MATCH' WHERE extraction_id=?",
            (extraction_id,),
        )
        conn.commit()
        return result

    filing = _pick_closest_filing(filings, announced_date)
    accession = filing.get("accessionNo", "")
    filed_at = filing.get("filedAt", "")[:10]
    filer_name = filing.get("companyName", "")
    filing_url = filing.get("linkToFilingDetails", "")
    cik = filing.get("cik", "")

    result["filing_accession"] = accession
    log.info(
        "Selected filing %s (filer=%s filed=%s) for extraction_id=%d",
        accession, filer_name, filed_at, extraction_id,
    )

    fetched_at = datetime.now(timezone.utc).isoformat()
    notes_base = json.dumps({
        "triggered_by_extraction_id": extraction_id,
        "trigger_signal": trigger_info.get("trigger_signal", ""),
        "filing_accession": accession,
        "filer_cik": cik,
    })

    # Item 1.01 text
    try:
        item_text = fetch_item_101_text(cfg, session, filing_url, log)
    except RuntimeError as exc:
        result["errors"].append(str(exc))
        return result

    if item_text:
        c_hash = _content_hash(item_text)
        if not conn.execute(
            "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
        ).fetchone():
            insert_source_raw(
                conn,
                source_type="SEC_8K_ITEM_101",
                url=filing_url,
                title=f"8-K Item 1.01 - {filer_name} - {filed_at}",
                published_date=filed_at or None,
                raw_html=None,
                clean_text=item_text,
                c_hash=c_hash,
                notes=notes_base,
                fetched_at=fetched_at,
            )
            result["rows_inserted"] += 1
            result["item_101_fetched"] = True
            log.info("Inserted SEC_8K_ITEM_101 for extraction_id=%d", extraction_id)
        else:
            log.debug("Duplicate Item 1.01 content_hash — skipping insert")
            result["item_101_fetched"] = True
    else:
        log.warning("Item Extractor returned no text for filing %s", accession)
        # Still insert the row with clean_text=NULL per spec §4.2
        insert_source_raw(
            conn,
            source_type="SEC_8K_ITEM_101",
            url=filing_url,
            title=f"8-K Item 1.01 - {filer_name} - {filed_at}",
            published_date=filed_at or None,
            raw_html=None,
            clean_text=None,
            c_hash=None,
            notes=json.dumps({
                **json.loads(notes_base),
                "empty_reason": "Item Extractor returned no text",
            }),
            fetched_at=fetched_at,
        )
        result["rows_inserted"] += 1

    # Exhibit 2.1
    exhibit_url = _find_exhibit_21_url(filing)
    if exhibit_url:
        raw_html, ex_text = fetch_exhibit_21(cfg, session, exhibit_url, log)
        if ex_text:
            c_hash = _content_hash(ex_text)
            if not conn.execute(
                "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
            ).fetchone():
                insert_source_raw(
                    conn,
                    source_type="SEC_EXHIBIT_21",
                    url=exhibit_url,
                    title=f"Exhibit 2.1 - {filer_name} - {filed_at}",
                    published_date=filed_at or None,
                    raw_html=raw_html,
                    clean_text=ex_text,
                    c_hash=c_hash,
                    notes=notes_base,
                    fetched_at=fetched_at,
                )
                result["rows_inserted"] += 1
                result["exhibit_21_fetched"] = True
                log.info("Inserted SEC_EXHIBIT_21 for extraction_id=%d", extraction_id)
            else:
                log.debug("Duplicate Exhibit 2.1 content_hash — skipping insert")
                result["exhibit_21_fetched"] = True
        else:
            log.warning("Exhibit 2.1 fetch returned no text for %s", exhibit_url)

    conn.execute(
        "UPDATE staging_extraction SET sec_lookup_status='TRIGGERED' WHERE extraction_id=?",
        (extraction_id,),
    )
    conn.commit()

    return result
