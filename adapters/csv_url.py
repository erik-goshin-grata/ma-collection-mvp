"""
CSV URL adapter — alternate Stage 1 ingest for LLM-extraction testing.

Reads a CSV of M&A news URLs (with known Source/Target/Acquirer/Headline
reference columns) and fetches the body text of each URL via trafilatura,
producing rows in source_raw with source_type=WEB_URL, source_tier=T2 —
the same shape the PR Newswire adapter produces, so the entire downstream
LLM pipeline (Stages 2-14) runs unchanged.

This is a testing ingest, not a production discovery source. The point is to
push arbitrary scraped press-release text through the extraction/classification
prompts and review how they perform. The CSV's known Source/Target/Acquirer
values are stashed into source_raw.notes under "csv_reference" so a downstream
review can grade extractions against them.

Expected CSV columns (header row required):
    URL, Source, Target, Acquirer, Headline, Story date

Rows with a blank URL are skipped (source_raw.url is NOT NULL UNIQUE).

Spec references: mirrors adapters/pr_newswire.py; specs/pipeline.md §2 (Stage 1)
"""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
import trafilatura

from config import Config
from logger import get_logger
from utils import content_hash as _content_hash

_SOURCE_TYPE = "WEB_URL"
_SOURCE_TIER = "T2"

# Many corporate PR domains reject an obvious-crawler User-Agent with a 403.
# Since this is a testing ingest of a curated URL list, present a standard
# browser UA to reduce blocks. Sites with hard bot-protection (Akamai/Cloudflare
# 403) still need a real headless browser and are handled out-of-band.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _read_csv(path: str, log) -> list[dict]:
    """Read the CSV into a list of normalised record dicts.

    Returns dicts with keys: url, source, target, acquirer, headline,
    published_date. Rows with a blank URL are dropped (logged).
    """
    records: list[dict] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        # Case-insensitive header lookup so "URL"/"url"/"Story date" all resolve.
        field_map = {(k or "").strip().lower(): k for k in (reader.fieldnames or [])}

        def col(row: dict, name: str) -> str | None:
            key = field_map.get(name)
            if key is None:
                return None
            val = (row.get(key) or "").strip()
            return val or None

        for i, row in enumerate(reader, start=2):  # row 1 is the header
            url = col(row, "url")
            if not url:
                log.info("CSV row %d has no URL — skipping", i)
                continue
            records.append({
                "url": url,
                "source": col(row, "source"),
                "target": col(row, "target"),
                "acquirer": col(row, "acquirer"),
                "headline": col(row, "headline"),
                "published_date": col(row, "story date"),
            })
    return records


# ---------------------------------------------------------------------------
# robots.txt (per-domain, cached)
# ---------------------------------------------------------------------------

def _robots_allows(
    url: str,
    session: requests.Session,
    cache: dict[str, RobotFileParser | None],
    log,
) -> bool:
    """Return True if robots.txt for the URL's host permits fetching it.

    Results are cached per host. On any error fetching/parsing robots.txt we
    fail open (allow) — mirroring the PR adapter's posture of proceeding on a
    robots fetch failure — but a definitive Disallow blocks the fetch.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    if host not in cache:
        robots_url = f"{parsed.scheme}://{host}/robots.txt"
        try:
            resp = session.get(robots_url, timeout=15)
            if resp.status_code == 200 and resp.text:
                rp = RobotFileParser()
                rp.parse(resp.text.splitlines())
                cache[host] = rp
            else:
                cache[host] = None  # no usable robots.txt → allow
        except Exception as exc:
            log.warning("robots.txt fetch failed for %s: %s — allowing", host, exc)
            cache[host] = None

    rp = cache[host]
    if rp is None:
        return True
    return rp.can_fetch("*", url)


# ---------------------------------------------------------------------------
# Body fetch
# ---------------------------------------------------------------------------

def fetch_body(
    url: str,
    session: requests.Session,
    delay: float,
    log,
) -> tuple[str | None, str | None]:
    """GET a URL body. Returns (raw_html, clean_text).

    One retry on network error or 5xx. Non-fatal: any failure returns
    (None, None) or (raw_html, None) so the run continues to the next URL.
    """
    time.sleep(delay)
    resp: requests.Response | None = None
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException as exc:
            if attempt == 0:
                log.warning("Network error %s: %s — retry in 5s", url, exc)
                time.sleep(5)
                continue
            log.error("Network error after retry %s: %s", url, exc)
            return None, None

        if resp.status_code >= 500 and attempt == 0:
            log.warning("5xx (%s) %s — retry in 10s", resp.status_code, url)
            time.sleep(10)
            continue
        break

    if resp is None:
        return None, None

    if resp.status_code != 200:
        log.warning("Status %s for %s — skipping", resp.status_code, url)
        return None, None

    raw_html = resp.text
    clean = trafilatura.extract(raw_html)
    if not clean or len(clean) < 100:
        return raw_html, None

    return raw_html, clean


def extract_page_date(raw_html: str | None) -> str | None:
    """Return the article's publish date (YYYY-MM-DD) from page metadata, or None.

    Uses trafilatura's metadata extraction (opengraph/JSON-LD/meta tags/dateline).
    Lets downstream extraction fall back to a real publication date when the CSV
    does not supply one — otherwise announced_date is left null for date-less rows.
    """
    if not raw_html:
        return None
    try:
        md = trafilatura.extract_metadata(raw_html)
    except Exception:
        return None
    date = getattr(md, "date", None) if md else None
    return date or None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def insert_source_raw(
    conn: sqlite3.Connection,
    *,
    url: str,
    title: str | None,
    published_date: str | None,
    raw_html: str | None,
    clean_text: str | None,
    c_hash: str | None,
    notes: str,
    fetched_at: str,
) -> int:
    """Insert a WEB_URL row into source_raw. Returns the new source_raw_id."""
    cur = conn.execute(
        """
        INSERT INTO source_raw
            (source_type, source_tier, url, title, published_date,
             raw_html, clean_text, content_hash, source_status, notes, fetched_at)
        VALUES
            (?, ?, ?, ?, ?,
             ?, ?, ?, 'FETCHED', ?, ?)
        """,
        (_SOURCE_TYPE, _SOURCE_TIER, url, title, published_date,
         raw_html, clean_text, c_hash, notes, fetched_at),
    )
    conn.commit()
    return cur.lastrowid


def _build_notes(rec: dict) -> str:
    """Build the source_raw.notes JSON, embedding the CSV reference values."""
    return json.dumps({
        "csv_reference": {
            "source": rec.get("source"),
            "target": rec.get("target"),
            "acquirer": rec.get("acquirer"),
            "headline": rec.get("headline"),
        }
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    conn: sqlite3.Connection,
    cfg: Config,
    run_id: str,
    *,
    csv_path: str,
    limit: int | None = None,
) -> dict:
    """Ingest a CSV of URLs into source_raw. Returns a result summary dict."""
    log = get_logger("scrape_csv_url", run_id, level=cfg.log_level)

    records = _read_csv(csv_path, log)
    if limit is not None:
        records = records[:limit]
    log.info("CSV ingest: %d URLs to fetch from %s", len(records), csv_path)

    session = requests.Session()
    session.headers.update({"User-Agent": _BROWSER_UA})
    delay = cfg.request_delay_seconds
    robots_cache: dict[str, RobotFileParser | None] = {}

    start = time.monotonic()
    rows_inserted = 0
    skipped_duplicates = 0
    robots_blocked = 0
    empty_body = 0
    errors: list[str] = []

    for rec in records:
        url = rec["url"]

        # Idempotency by URL.
        if conn.execute("SELECT 1 FROM source_raw WHERE url = ?", (url,)).fetchone():
            log.debug("URL already in DB — skipping %s", url)
            skipped_duplicates += 1
            continue

        if not _robots_allows(url, session, robots_cache, log):
            log.warning("robots.txt disallows — skipping %s", url)
            robots_blocked += 1
            continue

        try:
            raw_html, clean = fetch_body(url, session, delay, log)
        except Exception as exc:  # non-fatal: keep going
            log.error("Unexpected error fetching %s: %s", url, exc)
            errors.append(f"{url}: {exc}")
            continue

        fetched_at = datetime.now(timezone.utc).isoformat()
        # Prefer the CSV's date; fall back to the page's own publish metadata so
        # date-less CSVs still populate published_date (→ announced_date).
        pub_date = rec["published_date"] or extract_page_date(raw_html)

        if raw_html is None and clean is None:
            errors.append(f"null body: {url}")
            continue

        notes = _build_notes(rec)

        if clean is None:
            # No usable text — insert so the failure is visible, but Stage 2
            # will not pick it up (it requires non-null clean_text).
            log.warning("trafilatura returned <100 chars for %s — clean_text=NULL", url)
            empty_body += 1
            insert_source_raw(
                conn, url=url, title=rec["headline"],
                published_date=pub_date, raw_html=raw_html,
                clean_text=None, c_hash=None, notes=notes, fetched_at=fetched_at,
            )
            continue

        c_hash = _content_hash(clean)
        if conn.execute(
            "SELECT 1 FROM source_raw WHERE content_hash = ?", (c_hash,)
        ).fetchone():
            log.debug("Duplicate content_hash — skipping %s", url)
            skipped_duplicates += 1
            continue

        insert_source_raw(
            conn, url=url, title=rec["headline"],
            published_date=pub_date, raw_html=raw_html,
            clean_text=clean, c_hash=c_hash, notes=notes, fetched_at=fetched_at,
        )
        rows_inserted += 1
        log.info("Inserted source_raw #%d for %s", rows_inserted, url)

    runtime = time.monotonic() - start
    log.info(
        "CSV ingest done — urls=%d inserted=%d dup=%d robots_blocked=%d "
        "empty_body=%d errors=%d runtime=%.1fs",
        len(records), rows_inserted, skipped_duplicates, robots_blocked,
        empty_body, len(errors), runtime,
    )

    return {
        "sources_fetched": rows_inserted,
        "urls_seen": len(records),
        "rows_inserted": rows_inserted,
        "skipped_duplicates": skipped_duplicates,
        "robots_blocked": robots_blocked,
        "empty_body": empty_body,
        "errors": errors,
        "runtime_seconds": round(runtime, 1),
    }
