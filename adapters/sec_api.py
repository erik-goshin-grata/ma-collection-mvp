"""
sec-api.io adapter — Stages 5 & 6.

Stage 5 (sec_trigger_detect) calls detect_public_party() to determine whether
a PR source mentions a publicly traded party, then sets sec_lookup_status on
the staging_extraction row.

Stage 6 (sec_enrich) calls run_per_transaction() for each TRIGGERED row to
fetch the corresponding 8-K item text and, where present, Exhibit 2.1 and
Exhibit 99.x.  All are inserted into source_raw as T1 sources.

Spec: specs/adapter_sec_api.md (v0.2)
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
_ARCHIVE_HOST = "archive.sec-api.io"

_EXCHANGE_TICKER_RE = re.compile(
    r"\b(NYSE|NASDAQ|NYSE American|OTCQB|OTCQX|AMEX)\s*[:]\s*([A-Z]{1,5})\b"
)
_SEC_LANGUAGE = frozenset([
    "Form 8-K",
    "files with the SEC",
    "Securities and Exchange Commission",
    "Exchange Act",
    "Regulation FD",
])
_PUBLIC_BOILERPLATE = frozenset(["publicly traded", "common stock"])

# Exhibit matching — prefix-only, case-insensitive
_EX_21_RE = re.compile(r"^(EX-?2[-_.]?1|EXHIBIT\s+2\.?1|2\.1)\b", re.IGNORECASE)
_EX_99_RE = re.compile(
    r"^(EX-?99[-_.]?[1-9]|EXHIBIT\s+99\.[1-9]|99\.[1-9])\b", re.IGNORECASE
)

# Item code mappings: Query API dot syntax → Extractor dash syntax
_ITEM_EXTRACTOR_CODE: dict[str, str] = {
    "1.01": "1-1",
    "1.02": "1-2",
    "2.01": "2-1",
    "8.01": "8-1",
}
_ITEM_SOURCE_TYPE: dict[str, str] = {
    "1.01": "SEC_8K_ITEM_101",
    "1.02": "SEC_8K_ITEM_102",
    "2.01": "SEC_8K_ITEM_201",
    "8.01": "SEC_8K_ITEM_801",
}
# event_type → (primary_item, [fallback_items in priority order])
_EVENT_ITEM_PRIORITY: dict[str, tuple[str, list[str]]] = {
    "ANNOUNCEMENT": ("1.01", ["8.01"]),
    "CLOSE":        ("2.01", ["8.01"]),
    "TERMINATION":  ("1.02", ["8.01"]),
    "AMENDMENT":    ("1.01", ["2.01", "8.01"]),
}

# Extracts the numeric item code from full strings like "Item 1.01: Entry into ..."
_ITEM_CODE_RE = re.compile(r"\b(\d+\.\d+)\b")


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

def _parse_filing_items(filing: dict) -> list[str]:
    """Extract normalized item codes from a Filing Query response dict.

    The API returns items as full strings like
    "Item 1.01: Entry into a Material Definitive Agreement".
    This function extracts just the numeric code ("1.01") from each entry,
    deduplicating while preserving order.
    """
    raw = filing.get("items", "")
    candidates: list[str] = []
    if isinstance(raw, list):
        candidates = [str(i).strip() for i in raw if str(i).strip()]
    elif isinstance(raw, str):
        candidates = [i.strip() for i in raw.split(",") if i.strip()]

    result: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        m = _ITEM_CODE_RE.search(c)
        if m:
            code = m.group(1)
            if code not in seen:
                seen.add(code)
                result.append(code)
    return result


def _select_item(filing_items: list[str], event_type: str | None) -> str | None:
    """Select the best item to extract given available items and event_type.

    Returns the item code (e.g., '1.01') or None if no suitable item is present.
    """
    primary, fallbacks = _EVENT_ITEM_PRIORITY.get(event_type, ("1.01", ["8.01"]))
    for candidate in [primary] + fallbacks:
        if candidate in filing_items:
            return candidate
    return None


def _find_exhibits(filing: dict, pattern: re.Pattern) -> list[tuple[str, str]]:
    """Return [(label, url)] for exhibits whose label matches pattern (prefix match).

    Deduplicates by URL — same document will not appear twice even if
    both 'type' and 'description' fields match.
    """
    seen_urls: set[str] = set()
    results: list[tuple[str, str]] = []
    for doc in (filing.get("documentFormatFiles") or []):
        url = (doc.get("documentUrl") or doc.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        for field in ("type", "description"):
            label = (doc.get(field) or "").strip()
            if pattern.match(label):
                seen_urls.add(url)
                results.append((label, url))
                break
    return results


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

    items_clause = '(items:"1.01" OR items:"2.01" OR items:"8.01" OR items:"1.02")'
    parts = ['formType:"8-K"', items_clause]
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
                params={"token": cfg.sec_api_key},
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
# Item text extraction
# ---------------------------------------------------------------------------

def fetch_item_text(
    cfg: Config,
    session: requests.Session,
    filing_url: str,
    item_code: str,
    log,
) -> str | None:
    """Call the Item Extractor API and return text for the given item.

    Returns None on failure or when 'processing' persists past the retry budget.
    Returns an empty string when the Extractor returned an empty body (caller
    should insert a row with clean_text=NULL per spec §6).
    """
    params = {
        "url": filing_url,
        "item": _ITEM_EXTRACTOR_CODE[item_code],
        "type": "text",
        "token": cfg.sec_api_key,
    }

    def _one_call() -> str | None:
        time.sleep(cfg.sec_api_request_delay_seconds)
        for attempt in range(2):
            try:
                resp = session.get(_EXTRACTOR_URL, params=params, timeout=60)
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
            return resp.text
        return None

    raw = _one_call()
    if raw is None:
        return None

    delay_s = cfg.sec_extractor_retry_delay_ms / 1000.0
    retries_used = 0
    while raw.strip().lower() == "processing" and retries_used < cfg.sec_extractor_max_retries:
        log.warning(
            "Item Extractor 'processing' for item %s (retry %d/%d) — retrying in %.2fs",
            item_code, retries_used + 1, cfg.sec_extractor_max_retries, delay_s,
        )
        time.sleep(delay_s)
        raw = _one_call()
        if raw is None:
            return None
        retries_used += 1

    if raw.strip().lower() == "processing":
        log.warning(
            "Item Extractor still 'processing' after %d retries for item %s — skipping",
            cfg.sec_extractor_max_retries, item_code,
        )
        return None

    return raw.strip()  # may be empty string; None means failure


# ---------------------------------------------------------------------------
# Exhibit retrieval
# ---------------------------------------------------------------------------

def fetch_exhibit(
    cfg: Config,
    session: requests.Session,
    exhibit_url: str,
    log,
) -> tuple[str | None, str | None, str]:
    """Fetch an exhibit document.

    Returns (raw_html, clean_text, source_status) where source_status is:
      "FETCHED"    — normal content; insert the row
      "UNREADABLE" — PDF exhibit; insert with clean_text=NULL, source_status=UNREADABLE
      "SKIP"       — network/HTTP error; caller skips insert entirely
    """
    import trafilatura  # local import avoids startup cost when adapter is not used

    extra_params = {"token": cfg.sec_api_key} if _ARCHIVE_HOST in exhibit_url else {}

    time.sleep(cfg.sec_api_request_delay_seconds)
    try:
        resp = session.get(
            exhibit_url,
            params=extra_params if extra_params else None,
            timeout=60,
        )
    except requests.RequestException as exc:
        log.warning("Network error fetching exhibit %s: %s", exhibit_url, exc)
        return None, None, "SKIP"

    if resp.status_code == 404:
        log.warning("404 for exhibit %s — skipping", exhibit_url)
        return None, None, "SKIP"
    if resp.status_code != 200:
        log.warning("Status %s for exhibit %s — skipping", resp.status_code, exhibit_url)
        return None, None, "SKIP"

    content_type = resp.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or exhibit_url.lower().endswith(".pdf"):
        log.info("PDF exhibit %s — marking UNREADABLE", exhibit_url)
        return None, None, "UNREADABLE"

    if "html" in content_type:
        raw_html = resp.text
        clean = trafilatura.extract(raw_html)
        return raw_html, clean or None, "FETCHED"

    text = resp.text.strip()
    return None, text or None, "FETCHED"


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
    source_status: str = "FETCHED",
) -> int:
    """Insert a SEC row into source_raw.  Returns the new source_raw_id."""
    cur = conn.execute(
        """
        INSERT INTO source_raw
            (source_type, source_tier, url, title, published_date,
             raw_html, clean_text, content_hash, source_status, notes, fetched_at)
        VALUES
            (?, 'T1', ?, ?, ?,
             ?, ?, ?, ?, ?, ?)
        """,
        (
            source_type, url, title, published_date,
            raw_html, clean_text, c_hash, source_status, notes, fetched_at,
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Per-transaction entry point (Stage 6)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Drop 3.19 — expanded filing fetchers for sec_documents stage
# ---------------------------------------------------------------------------

# Form type → query string (some have spaces, all go in double quotes)
_FORM_TYPE_QUERY: dict[str, str] = {
    "DEFM14A": "DEFM14A",
    "SC_TOT":  "SC TO-T",
    "S4":      "S-4",
    "8-K":     "8-K",
}


def _extract_pdf_text(pdf_bytes: bytes, log) -> str | None:
    """Extract text from PDF bytes using pdfminer.six.

    Returns stripped text string or None if extraction fails or pdfminer
    is not installed.
    """
    try:
        import io
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams

        out = io.StringIO()
        extract_text_to_fp(io.BytesIO(pdf_bytes), out, laparams=LAParams())
        text = out.getvalue().strip()
        return text or None
    except ImportError:
        log.warning("pdfminer.six not installed — PDF text extraction unavailable; install with: pip install pdfminer.six")
        return None
    except Exception as exc:
        log.warning("PDF text extraction failed: %s", exc)
        return None


def query_filings_by_formtype(
    cfg: Config,
    session: requests.Session,
    filing_type_key: str,
    ticker: str | None,
    company_name: str | None,
    announced_date: str,
    log,
    window_days: int = 90,
    max_results: int = 3,
) -> list[dict]:
    """Query sec-api.io Filing Query API for a specific form type.

    filing_type_key: one of DEFM14A | SC_TOT | S4 | 8-K
    Returns up to max_results filing dicts sorted by date proximity to
    announced_date, or [] on error / no results.
    """
    import logging as _logging
    log = log or _logging.getLogger(__name__)
    form_str = _FORM_TYPE_QUERY.get(filing_type_key, filing_type_key)

    try:
        anchor = datetime.strptime(announced_date, "%Y-%m-%d")
    except ValueError:
        anchor = datetime.now(timezone.utc)

    # Drop 3.20a: window is announcement-date to +180 days only (no pre-announcement noise).
    # window_days param is retained for call-site compatibility but no longer controls the start.
    start = anchor.strftime("%Y-%m-%d")
    end = (anchor + timedelta(days=180)).strftime("%Y-%m-%d")

    parts = [f'formType:"{form_str}"']
    if ticker:
        parts.append(f'ticker:"{ticker}"')
    elif company_name:
        parts.append(f'companyName:"{company_name}"')
    parts.append(f"filedAt:[{start} TO {end}]")
    query_str = " AND ".join(parts)

    payload = {
        "query": {"query_string": {"query": query_str}},
        "from": "0",
        "size": str(max_results),
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    log.debug("query_filings_by_formtype payload: %s", json.dumps(payload))

    time.sleep(cfg.sec_api_request_delay_seconds)
    for attempt in range(2):
        try:
            resp = session.post(
                _FILING_QUERY_URL,
                json=payload,
                params={"token": cfg.sec_api_key},
                timeout=30,
            )
        except requests.RequestException as exc:
            if attempt == 0:
                log.warning("Network error on %s query: %s — retry in 10s", filing_type_key, exc)
                time.sleep(10)
                continue
            log.error("%s query failed after retry: %s", filing_type_key, exc)
            return []

        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"sec-api.io auth failure ({resp.status_code}) — check SEC_API_KEY"
            )
        if resp.status_code == 429:
            if attempt == 0:
                log.warning("429 on %s query — sleeping 30s, retry once", filing_type_key)
                time.sleep(30)
                continue
            log.warning("Persistent 429 on %s query — skipping", filing_type_key)
            return []
        if resp.status_code >= 500 and attempt == 0:
            log.warning("5xx (%s) on %s query — retry in 10s", resp.status_code, filing_type_key)
            time.sleep(10)
            continue
        if resp.status_code != 200:
            log.error("Unexpected status %s on %s query: %s", resp.status_code, filing_type_key, resp.text[:500])
            return []

        try:
            data = resp.json()
        except ValueError as exc:
            log.error("%s query returned non-JSON: %s", filing_type_key, exc)
            return []

        filings = data.get("filings") or data.get("hits", {}).get("hits", [])
        if not isinstance(filings, list):
            return []

        return filings

    return []


def fetch_filing_full_text(
    cfg: Config,
    session: requests.Session,
    filing: dict,
    log,
) -> tuple[str | None, str]:
    """Fetch the main document text for a non-8K filing (DEFM14A, S-4, SC TO-T).

    Uses linkToHtmlDocument from the filing dict (SEC EDGAR HTML document).
    Falls back to linkToFilingDetails if linkToHtmlDocument is absent.

    Returns (raw_text, source_status) where source_status is:
      "FETCHED"    — text extracted successfully
      "UNREADABLE" — PDF; extraction attempted but unavailable
      "SKIP"       — network/HTTP error or no usable URL
    """
    import logging as _logging
    import trafilatura
    log = log or _logging.getLogger(__name__)

    html_url = (filing.get("linkToHtmlDocument") or "").strip()
    if not html_url:
        # Fall back to linkToFilingDetails (the filing index page, less useful but better than nothing)
        html_url = (filing.get("linkToFilingDetails") or "").strip()
    if not html_url:
        log.warning("No document URL in filing %s — skipping", filing.get("accessionNo", "?"))
        return None, "SKIP"

    extra_params = {"token": cfg.sec_api_key} if _ARCHIVE_HOST in html_url else {}

    time.sleep(cfg.sec_api_request_delay_seconds)
    try:
        resp = session.get(
            html_url,
            params=extra_params if extra_params else None,
            timeout=120,
        )
    except requests.RequestException as exc:
        log.warning("Network error fetching %s: %s", html_url, exc)
        return None, "SKIP"

    if resp.status_code == 404:
        log.warning("404 for filing document %s", html_url)
        return None, "SKIP"
    if resp.status_code != 200:
        log.warning("Status %s for filing document %s", resp.status_code, html_url)
        return None, "SKIP"

    content_type = resp.headers.get("Content-Type", "").lower()

    if "pdf" in content_type or html_url.lower().endswith(".pdf"):
        text = _extract_pdf_text(resp.content, log)
        if text:
            log.info("PDF extracted via pdfminer for %s  chars=%d", html_url, len(text))
            return text, "FETCHED"
        log.info("PDF filing %s — pdfminer unavailable, marking UNREADABLE", html_url)
        return None, "UNREADABLE"

    if "html" in content_type or html_url.lower().endswith((".htm", ".html")):
        raw_html = resp.text
        clean = trafilatura.extract(raw_html)
        if clean:
            return clean.strip(), "FETCHED"
        # trafilatura gave up; try raw text as last resort
        return resp.text.strip() or None, "FETCHED" if resp.text.strip() else "SKIP"

    text = resp.text.strip()
    return (text, "FETCHED") if text else (None, "SKIP")


_TITLE_BOILERPLATE = re.compile(
    r"UNITED STATES SECURITIES AND EXCHANGE COMMISSION"
    r"|WASHINGTON,?\s*D\.?C\.?"
    r"|FORM\s+\d+-?[A-Z]?"
    r"|CURRENT REPORT"
    r"|AMENDMENT NO"
    r"|PROSPECTUS"
    r"|PURSUANT TO"
    r"|SECTION\s+\d+",
    re.IGNORECASE,
)

_TITLE_KEYWORDS = frozenset([
    "Agreement", "Plan", "Merger", "Purchase", "Tender", "Offer",
    "Proxy", "Registration", "Statement",
])


def extract_document_title(raw_text: str) -> str | None:
    """Extract the document's self-declared title from the first ~1500 chars.

    Looks for the first all-caps line (>85% uppercase alpha chars) or a
    Title-Case heading containing a known deal-document keyword.  Skips SEC
    filing cover-page boilerplate.  Returns None when no title can be found.
    """
    if not raw_text:
        return None
    for line in raw_text[:1500].split("\n"):
        line = line.strip()
        if not line or len(line) < 10 or len(line) > 150:
            continue
        if _TITLE_BOILERPLATE.search(line):
            continue
        alpha = [c for c in line if c.isalpha()]
        if not alpha:
            continue
        # All-caps test
        if sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.85:
            return line
        # Title-case + keyword test
        words = line.split()
        if len(words) >= 3:
            cap_ratio = sum(1 for w in words if w and w[0].isupper()) / len(words)
            if cap_ratio > 0.85 and any(kw in line for kw in _TITLE_KEYWORDS):
                return line
    return None


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
        "items_available": [],
        "item_extracted": None,
        "exhibit_21_fetched": False,
        "exhibit_99_fetched_count": 0,
        "exhibits_unreadable_count": 0,
        "rows_inserted": 0,
        "errors": [],
    }

    row = conn.execute(
        """
        SELECT se.extraction_id, se.target_name, se.acquirer_name,
               se.announced_date, se.target_ticker, se.acquirer_ticker,
               se.event_type,
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
    event_type = row["event_type"]  # may be None; _select_item handles None gracefully
    clean_text = row["clean_text"] or ""
    ticker = row["target_ticker"] or row["acquirer_ticker"]

    if trigger_info is None:
        trigger_info = detect_public_party(clean_text, target_name, acquirer_name)
        if not trigger_info:
            log.info("No public-party signal for extraction_id=%d", extraction_id)
            return result

    result["triggered"] = True
    result["trigger_signal"] = trigger_info.get("trigger_signal", "")

    if trigger_info.get("ticker"):
        ticker = trigger_info["ticker"]

    side = trigger_info.get("side", "BOTH")
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

    filing_items = _parse_filing_items(filing)
    result["filing_accession"] = accession
    result["items_available"] = filing_items
    log.info(
        "Selected filing %s (filer=%s filed=%s items=%s) for extraction_id=%d",
        accession, filer_name, filed_at, filing_items, extraction_id,
    )

    fetched_at = datetime.now(timezone.utc).isoformat()

    def _make_notes(item_extracted: str | None, exhibit_label: str | None, extra: dict | None = None) -> str:
        d: dict = {
            "triggered_by_extraction_id": extraction_id,
            "trigger_signal": trigger_info.get("trigger_signal", ""),
            "filing_accession": accession,
            "filer_cik": cik,
            "filer_name": filer_name,
            "filing_url": filing_url,
            "item_extracted": item_extracted,
            "exhibit_label": exhibit_label,
        }
        if extra:
            d.update(extra)
        return json.dumps(d)

    # Item text extraction (event_type-conditional with pre-check)
    selected_item = _select_item(filing_items, event_type)
    if selected_item is None:
        log.info(
            "No matching item in filing %s for event_type=%s — skipping item extraction",
            accession, event_type,
        )
    else:
        try:
            item_text = fetch_item_text(cfg, session, filing_url, selected_item, log)
        except RuntimeError as exc:
            result["errors"].append(str(exc))
            log.error("Item Extractor auth error for extraction_id=%d: %s", extraction_id, exc)
            item_text = None

        if item_text is None:
            log.warning(
                "Item %s unavailable for filing %s (processing timeout or HTTP error)",
                selected_item, accession,
            )
        elif item_text:
            c_hash = _content_hash(item_text)
            if not conn.execute(
                "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
            ).fetchone():
                insert_source_raw(
                    conn,
                    source_type=_ITEM_SOURCE_TYPE[selected_item],
                    url=f"{filing_url}#item={selected_item}",
                    title=f"8-K Item {selected_item} - {filer_name} - {filed_at}",
                    published_date=filed_at or None,
                    raw_html=None,
                    clean_text=item_text,
                    c_hash=c_hash,
                    notes=_make_notes(selected_item, None),
                    fetched_at=fetched_at,
                )
                result["rows_inserted"] += 1
                log.info(
                    "Inserted %s for extraction_id=%d",
                    _ITEM_SOURCE_TYPE[selected_item], extraction_id,
                )
            else:
                log.debug("Duplicate item content_hash for %s — skipping insert", selected_item)
            result["item_extracted"] = selected_item
        else:
            # Extractor returned empty body — insert row with clean_text=NULL per spec §6
            log.warning(
                "Item Extractor returned empty text for item %s in filing %s",
                selected_item, accession,
            )
            insert_source_raw(
                conn,
                source_type=_ITEM_SOURCE_TYPE[selected_item],
                url=f"{filing_url}#item={selected_item}",
                title=f"8-K Item {selected_item} - {filer_name} - {filed_at}",
                published_date=filed_at or None,
                raw_html=None,
                clean_text=None,
                c_hash=None,
                notes=_make_notes(selected_item, None, {"empty_reason": "Item Extractor returned empty text"}),
                fetched_at=fetched_at,
            )
            result["rows_inserted"] += 1
            result["item_extracted"] = selected_item

    # Exhibit 2.1
    for label, ex_url in _find_exhibits(filing, _EX_21_RE):
        log.debug("Exhibit 2.1 candidate: label=%r url=%s", label, ex_url)
        raw_html, ex_text, ex_status = fetch_exhibit(cfg, session, ex_url, log)
        if ex_status == "SKIP":
            continue
        if ex_status == "UNREADABLE":
            insert_source_raw(
                conn,
                source_type="SEC_EXHIBIT_21",
                url=ex_url,
                title=f"Exhibit 2.1 - {filer_name} - {filed_at}",
                published_date=filed_at or None,
                raw_html=None,
                clean_text=None,
                c_hash=None,
                notes=_make_notes(None, label),
                fetched_at=fetched_at,
                source_status="UNREADABLE",
            )
            result["rows_inserted"] += 1
            result["exhibits_unreadable_count"] += 1
            log.info("Inserted UNREADABLE Exhibit 2.1 (label=%r) for extraction_id=%d", label, extraction_id)
            continue
        if ex_text:
            c_hash = _content_hash(ex_text)
            if not conn.execute(
                "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
            ).fetchone():
                insert_source_raw(
                    conn,
                    source_type="SEC_EXHIBIT_21",
                    url=ex_url,
                    title=f"Exhibit 2.1 - {filer_name} - {filed_at}",
                    published_date=filed_at or None,
                    raw_html=raw_html,
                    clean_text=ex_text,
                    c_hash=c_hash,
                    notes=_make_notes(None, label),
                    fetched_at=fetched_at,
                )
                result["rows_inserted"] += 1
                result["exhibit_21_fetched"] = True
                log.info("Inserted SEC_EXHIBIT_21 (label=%r) for extraction_id=%d", label, extraction_id)
            else:
                log.debug("Duplicate Exhibit 2.1 content_hash — skipping")
                result["exhibit_21_fetched"] = True
        else:
            log.warning("Exhibit 2.1 fetch returned no text for %s", ex_url)

    # Exhibit 99.x (retrieve all matching exhibits)
    for label, ex_url in _find_exhibits(filing, _EX_99_RE):
        log.debug("Exhibit 99.x candidate: label=%r url=%s", label, ex_url)
        raw_html, ex_text, ex_status = fetch_exhibit(cfg, session, ex_url, log)
        if ex_status == "SKIP":
            continue
        if ex_status == "UNREADABLE":
            insert_source_raw(
                conn,
                source_type="SEC_EXHIBIT_99",
                url=ex_url,
                title=f"Exhibit {label} - {filer_name} - {filed_at}",
                published_date=filed_at or None,
                raw_html=None,
                clean_text=None,
                c_hash=None,
                notes=_make_notes(None, label),
                fetched_at=fetched_at,
                source_status="UNREADABLE",
            )
            result["rows_inserted"] += 1
            result["exhibits_unreadable_count"] += 1
            log.info("Inserted UNREADABLE Exhibit 99.x (label=%r) for extraction_id=%d", label, extraction_id)
            continue
        if ex_text:
            c_hash = _content_hash(ex_text)
            if not conn.execute(
                "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
            ).fetchone():
                insert_source_raw(
                    conn,
                    source_type="SEC_EXHIBIT_99",
                    url=ex_url,
                    title=f"Exhibit {label} - {filer_name} - {filed_at}",
                    published_date=filed_at or None,
                    raw_html=raw_html,
                    clean_text=ex_text,
                    c_hash=c_hash,
                    notes=_make_notes(None, label),
                    fetched_at=fetched_at,
                )
                result["rows_inserted"] += 1
                result["exhibit_99_fetched_count"] += 1
                log.info("Inserted SEC_EXHIBIT_99 (label=%r) for extraction_id=%d", label, extraction_id)
            else:
                log.debug("Duplicate Exhibit 99.x content_hash — skipping")
                result["exhibit_99_fetched_count"] += 1
        else:
            log.warning("Exhibit 99.x fetch returned no text for %s (label=%r)", ex_url, label)

    conn.execute(
        "UPDATE staging_extraction SET sec_lookup_status='TRIGGERED' WHERE extraction_id=?",
        (extraction_id,),
    )
    conn.commit()

    return result
