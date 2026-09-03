"""
PR Newswire adapter — Stage 1.

Scrapes the M&A / Acquisitions category listing on PR Newswire and fetches
the body text of each press release via trafilatura.  Produces rows in
source_raw with source_type=PR_NEWSWIRE, source_tier=T2.

Spec: specs/adapter_pr_newswire.md
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
import trafilatura
from bs4 import BeautifulSoup

from config import Config
from lib.source_authority import resolve_tier
from logger import get_logger
from utils import content_hash as _content_hash

_BASE_URL = "https://www.prnewswire.com"
_ROBOTS_URL = "https://www.prnewswire.com/robots.txt"
_LISTING_PATH = (
    "/news-releases/financial-services-latest-news/"
    "acquisitions-mergers-and-takeovers-list/"
)
_LISTING_BASE = _BASE_URL + _LISTING_PATH
_PAGESIZE = 25


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def fetch_robots_txt(cfg: Config, session: requests.Session, log) -> float:
    """Fetch robots.txt, persist to logs/, and return the effective request delay.

    Raises RuntimeError if the listing path is explicitly disallowed.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    try:
        resp = session.get(_ROBOTS_URL, timeout=15)
        robots_text = resp.text
    except Exception as exc:
        log.warning("robots.txt fetch failed: %s — proceeding with configured delay", exc)
        return cfg.request_delay_seconds

    robots_path = f"logs/robots_{ts}.txt"
    try:
        with open(robots_path, "w", encoding="utf-8") as fh:
            fh.write(robots_text)
        log.info("robots.txt written to %s", robots_path)
    except OSError as exc:
        log.warning("Could not write robots.txt to %s: %s", robots_path, exc)

    rp = RobotFileParser()
    rp.parse(robots_text.splitlines())

    if not rp.can_fetch("*", _LISTING_BASE):
        raise RuntimeError(
            f"robots.txt explicitly disallows crawling the listing path: {_LISTING_PATH}"
        )

    effective_delay = cfg.request_delay_seconds
    crawl_delay = rp.crawl_delay("*")
    if crawl_delay is not None and crawl_delay > effective_delay:
        log.info(
            "Crawl-delay directive (%ss) > configured delay (%ss); raising to match.",
            crawl_delay,
            effective_delay,
        )
        effective_delay = float(crawl_delay)

    return effective_delay


# ---------------------------------------------------------------------------
# Listing page parsing
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> str | None:
    """Parse 'Apr 22, 2026, 17:28 ET' → '2026-04-22'.  Returns None on failure."""
    cleaned = re.sub(r"\s+ET\s*$", "", raw.strip())
    for fmt in ("%b %d, %Y, %H:%M", "%b. %d, %Y, %H:%M", "%B %d, %Y, %H:%M"):
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _parse_listing_page(html: str, log) -> list[dict]:
    """Return list of {title, url, published_date} dicts from a listing page."""
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", class_=re.compile(r"newsreleaseconsolidatelink"))
    records: list[dict] = []
    for anchor in anchors:
        href = anchor.get("href", "").strip()
        if not href:
            continue
        url = urljoin(_BASE_URL, href)

        h3 = anchor.find("h3")
        if not h3:
            log.debug("No <h3> in anchor href=%s — skipping", href)
            continue

        small = h3.find("small")
        date_str: str | None = None
        if small:
            date_str = _parse_date(small.get_text())
            small.extract()

        title = re.sub(r"\s+", " ", h3.get_text(separator=" ")).strip()
        records.append({"title": title, "url": url, "published_date": date_str})

    return records


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_with_retry(
    session: requests.Session,
    url: str,
    delay: float,
    log,
    *,
    retry_backoff: float = 5.0,
) -> requests.Response | None:
    """Sleep `delay`, then GET `url` with one retry on network/5xx.

    Returns the Response (any status) or None on unrecoverable network failure.
    Raises RuntimeError on 403 (bot block).
    """
    time.sleep(delay)
    for attempt in range(2):
        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException as exc:
            if attempt == 0:
                log.warning("Network error %s: %s — retry in %.0fs", url, exc, retry_backoff)
                time.sleep(retry_backoff)
                continue
            log.error("Network error after retry %s: %s", url, exc)
            return None

        if resp.status_code == 403:
            raise RuntimeError(f"403 Forbidden — possible bot block: {url}")

        if resp.status_code >= 500 and attempt == 0:
            log.warning("5xx (%s) %s — retry in 10s", resp.status_code, url)
            time.sleep(10)
            continue

        return resp

    return None  # unreachable, but satisfies type checker


# ---------------------------------------------------------------------------
# Body fetch
# ---------------------------------------------------------------------------

def fetch_body(
    url: str,
    session: requests.Session,
    delay: float,
    log,
    *,
    consecutive_429s: list[int],
) -> tuple[str | None, str | None]:
    """GET a press release body.  Returns (raw_html, clean_text).

    `consecutive_429s` is a mutable single-element list so the caller can track
    the counter across calls without passing it by reference.

    Raises RuntimeError on 403 or 5 consecutive 429s.
    """
    resp = _get_with_retry(session, url, delay, log)
    if resp is None:
        return None, None

    if resp.status_code == 404:
        log.error("404 for body %s — skipping", url)
        return None, None

    if resp.status_code == 429:
        log.warning("429 for body %s — sleeping 60s, retrying once", url)
        time.sleep(60)
        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException:
            consecutive_429s[0] += 1
            if consecutive_429s[0] >= 5:
                raise RuntimeError("5 consecutive 429s on body fetches")
            return None, None
        if resp.status_code == 429:
            consecutive_429s[0] += 1
            log.warning("Persistent 429 for body %s (consecutive: %d)", url, consecutive_429s[0])
            if consecutive_429s[0] >= 5:
                raise RuntimeError("5 consecutive 429s on body fetches")
            return None, None
    else:
        consecutive_429s[0] = 0

    if resp.status_code != 200:
        log.warning("Unexpected status %s for body %s — skipping", resp.status_code, url)
        return None, None

    raw_html = resp.text
    clean = trafilatura.extract(raw_html)
    if not clean or len(clean) < 100:
        return raw_html, None

    return raw_html, clean


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
    notes: str | None,
    fetched_at: str,
) -> int:
    """Insert a PR_NEWSWIRE row into source_raw.  Returns the new source_raw_id."""
    # A subscribed PR Newswire feed is a known first-party announcement source
    # (see lib/source_authority.py) -- resolved and stamped at ingestion, not
    # left for Relevancy to (re)infer. Relevancy still runs for relevance, but
    # skips source_character/tier inference once it sees this already set.
    source_character = "FIRST_PARTY_ANNOUNCEMENT"
    source_tier = resolve_tier(source_character=source_character)
    cur = conn.execute(
        """
        INSERT INTO source_raw
            (source_type, source_tier, source_character, url, title, published_date,
             raw_html, clean_text, content_hash, source_status, notes, fetched_at)
        VALUES
            ('PR_NEWSWIRE', ?, ?, ?, ?, ?,
             ?, ?, ?, 'FETCHED', ?, ?)
        """,
        (source_tier, source_character, url, title, published_date,
         raw_html, clean_text, c_hash, notes, fetched_at),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Stage 1: scrape PR Newswire listing and fetch press release bodies.

    Returns a result dict for the run summary.
    """
    log = get_logger("scrape_pr_newswire", run_id, level=cfg.log_level)

    session = requests.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    try:
        delay = fetch_robots_txt(cfg, session, log)
    except RuntimeError as exc:
        log.error("Pre-flight check failed: %s", exc)
        return {
            "sources_fetched": 0,
            "pages_fetched": 0,
            "listings_seen": 0,
            "bodies_fetched": 0,
            "rows_inserted": 0,
            "skipped_duplicates": 0,
            "errors": [str(exc)],
            "runtime_seconds": 0.0,
        }

    start = time.monotonic()
    rows_inserted = 0
    skipped_duplicates = 0
    bodies_fetched = 0
    listings_seen = 0
    pages_fetched = 0
    errors: list[str] = []
    consecutive_listing_errors = 0
    consecutive_429s = [0]      # mutable box for fetch_body
    consecutive_listing_429s = 0
    consecutive_parse_failures = 0

    page = 1
    stop = False

    while not stop and rows_inserted < cfg.max_fetches:
        listing_url = f"{_LISTING_BASE}?page={page}&pagesize={_PAGESIZE}"
        log.info("Listing page %d: %s", page, listing_url)

        resp = _get_with_retry(session, listing_url, delay, log)

        if resp is None:
            consecutive_listing_errors += 1
            errors.append(f"page {page}: network failure after retry")
            if consecutive_listing_errors >= 10:
                log.error("10 consecutive listing errors — stopping")
                break
            page += 1
            continue

        if resp.status_code == 404:
            log.info("404 on listing page %d — end of pagination", page)
            break

        if resp.status_code == 429:
            consecutive_listing_429s += 1
            log.warning(
                "429 on listing page %d (consecutive: %d) — sleeping 60s",
                page, consecutive_listing_429s,
            )
            if consecutive_listing_429s >= 5:
                errors.append("5 consecutive 429s on listing pages")
                log.error("5 consecutive 429s — stopping")
                break
            time.sleep(60)
            continue  # retry same page

        if resp.status_code != 200:
            consecutive_listing_errors += 1
            log.warning("Status %s on listing page %d", resp.status_code, page)
            if consecutive_listing_errors >= 10:
                log.error("10 consecutive listing errors — stopping")
                break
            page += 1
            continue

        consecutive_listing_errors = 0
        consecutive_listing_429s = 0
        pages_fetched += 1

        records = _parse_listing_page(resp.text, log)
        if not records:
            consecutive_parse_failures += 1
            log.warning(
                "No records on listing page %d (consecutive parse failures: %d)",
                page, consecutive_parse_failures,
            )
            if consecutive_parse_failures >= 2:
                log.error(
                    "Two consecutive parse failures — listing structure may have changed"
                )
            break
        else:
            consecutive_parse_failures = 0

        listings_seen += len(records)
        page_new = 0
        page_skipped = 0

        for rec in records:
            if rows_inserted >= cfg.max_fetches:
                stop = True
                break

            # Idempotency check by URL
            if conn.execute(
                "SELECT 1 FROM source_raw WHERE url = ?", (rec["url"],)
            ).fetchone():
                log.debug("URL already in DB — skipping %s", rec["url"])
                page_skipped += 1
                skipped_duplicates += 1
                continue

            try:
                raw_html, clean = fetch_body(
                    rec["url"], session, delay, log,
                    consecutive_429s=consecutive_429s,
                )
            except RuntimeError as exc:
                log.error("Fatal error fetching body: %s", exc)
                errors.append(str(exc))
                stop = True
                break

            bodies_fetched += 1
            fetched_at = datetime.now(timezone.utc).isoformat()

            if raw_html is None and clean is None:
                errors.append(f"null body: {rec['url']}")
                continue

            notes: str | None = None
            if clean is None:
                notes = "trafilatura returned empty or <100 chars; clean_text=NULL"
                c_hash = None
            else:
                c_hash = _content_hash(clean)
                if conn.execute(
                    "SELECT 1 FROM source_raw WHERE content_hash = ?", (c_hash,)
                ).fetchone():
                    log.debug("Duplicate content_hash — skipping %s", rec["url"])
                    page_skipped += 1
                    skipped_duplicates += 1
                    continue

            insert_source_raw(
                conn,
                url=rec["url"],
                title=rec["title"],
                published_date=rec["published_date"],
                raw_html=raw_html,
                clean_text=clean,
                c_hash=c_hash,
                notes=notes,
                fetched_at=fetched_at,
            )
            rows_inserted += 1
            page_new += 1
            log.info("Inserted source_raw #%d for %s", rows_inserted, rec["url"])

        log.info("Page %d: new=%d skipped=%d", page, page_new, page_skipped)
        page += 1

    runtime = time.monotonic() - start
    log.info(
        "PR Newswire scrape done — pages=%d listings=%d bodies=%d inserted=%d "
        "skipped=%d errors=%d runtime=%.1fs",
        pages_fetched, listings_seen, bodies_fetched, rows_inserted,
        skipped_duplicates, len(errors), runtime,
    )

    return {
        "sources_fetched": rows_inserted,
        "pages_fetched": pages_fetched,
        "listings_seen": listings_seen,
        "bodies_fetched": bodies_fetched,
        "rows_inserted": rows_inserted,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
        "runtime_seconds": round(runtime, 1),
    }
