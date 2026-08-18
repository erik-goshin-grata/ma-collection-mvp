#!/usr/bin/env python3
"""
Headless URL body backfill for curated review corpora.

Reads the same CSV shape as scripts/ingest_csv_urls.py, skips URLs already in
source_raw by default, renders remaining pages with Playwright, extracts clean
article text, and inserts source_raw rows as WEB_URL/T2. This is an out-of-band
fetch helper; the downstream pipeline is unchanged.

Usage:
    DB_PATH=data/urlfirst.db python scripts/backfill_url_bodies_headless.py --input data/input_stories/urls.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trafilatura

from adapters.csv_url import _BROWSER_UA, _build_notes, _read_csv, extract_page_date, insert_source_raw
from config import ConfigurationError, get_config
from db import get_connection, init_db
from lib.body_quality import MIN_BODY_CHARS, body_rejection_reason
from logger import get_logger
from utils import content_hash as _content_hash


def _load_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Playwright is not installed in this environment. Install it with:\n"
            "  venv/bin/python -m pip install playwright\n"
            "  venv/bin/python -m playwright install chromium"
        ) from exc
    return sync_playwright, PlaywrightTimeoutError


def _visible_text(page) -> str | None:
    selectors = [
        "article",
        "main",
        "[role='main']",
        ".bw-release-main",
        ".release-body",
        ".article-body",
        "body",
    ]
    for selector in selectors:
        try:
            text = page.locator(selector).first.inner_text(timeout=2_000).strip()
        except Exception:
            continue
        if len(text) >= MIN_BODY_CHARS:
            return text
    return None


def _render_and_extract(page, url: str, *, timeout_ms: int, wait_ms: int) -> tuple[str | None, str | None, str | None]:
    response_status: str | None = None
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if response is not None:
            response_status = str(response.status)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10_000))
        except Exception:
            pass
        if wait_ms > 0:
            page.wait_for_timeout(wait_ms)
        raw_html = page.content()
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {exc}"

    # A rendered block/interstitial page yields plenty of extractable text, so a
    # length-only check accepts it. Gate both the primary extraction and the
    # visible-text fallback — the fallback reads the same DOM and would happily
    # return the same block message.
    clean = trafilatura.extract(raw_html)
    reason = body_rejection_reason(clean, raw_html=raw_html)
    if reason is None:
        return raw_html, clean, None

    fallback = _visible_text(page)
    fallback_reason = body_rejection_reason(fallback, raw_html=raw_html)
    if fallback_reason is None:
        return raw_html, fallback, None

    # A block verdict is more informative than a bare emptiness verdict.
    final_reason = reason if reason.startswith("block_page") else fallback_reason
    return raw_html, None, f"{final_reason}; status={response_status}"


def _proxy_from_args_or_env(args: argparse.Namespace) -> dict | None:
    server = (
        args.proxy_server
        or os.getenv("BRIGHTDATA_PROXY_SERVER")
        or os.getenv("BRIGHT_DATA_PROXY_SERVER")
        or os.getenv("PLAYWRIGHT_PROXY_SERVER")
    )
    host = (
        os.getenv("BRIGHTDATA_PROXY_HOST")
        or os.getenv("BRIGHT_DATA_PROXY_HOST")
    )
    port = (
        os.getenv("BRIGHTDATA_PROXY_PORT")
        or os.getenv("BRIGHT_DATA_PROXY_PORT")
    )
    if not server and host and port:
        server = f"http://{host}:{port}"
    if not server:
        return None

    proxy: dict[str, str] = {"server": server}
    username = (
        args.proxy_username
        or os.getenv("BRIGHTDATA_PROXY_USERNAME")
        or os.getenv("BRIGHT_DATA_PROXY_USERNAME")
        or os.getenv("BRIGHTDATA_USERNAME")
        or os.getenv("BRIGHT_DATA_USERNAME")
        or os.getenv("PLAYWRIGHT_PROXY_USERNAME")
    )
    password = (
        args.proxy_password
        or os.getenv("BRIGHTDATA_PROXY_PASSWORD")
        or os.getenv("BRIGHT_DATA_PROXY_PASSWORD")
        or os.getenv("BRIGHTDATA_PASSWORD")
        or os.getenv("BRIGHT_DATA_PASSWORD")
        or os.getenv("PLAYWRIGHT_PROXY_PASSWORD")
    )
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy


def _merge_recovery_notes(existing_notes: str | None, rec: dict, *, method: str) -> str:
    try:
        notes = json.loads(existing_notes or "{}")
    except json.JSONDecodeError:
        notes = {"previous_notes": existing_notes}
    notes.setdefault("csv_reference", {
        "source": rec.get("source"),
        "target": rec.get("target"),
        "acquirer": rec.get("acquirer"),
        "headline": rec.get("headline"),
    })
    notes["body_recovery"] = {
        "method": method,
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(notes)


def _upsert_source_raw(
    conn,
    *,
    rec: dict,
    existing_row,
    raw_html: str,
    clean: str,
    c_hash: str,
    published_date: str | None,
    fetched_at: str,
    update_existing: bool,
    recovery_method: str,
) -> int:
    if existing_row and update_existing:
        notes = _merge_recovery_notes(existing_row["notes"], rec, method=recovery_method)
        conn.execute(
            """
            UPDATE source_raw
               SET title = COALESCE(?, title),
                   published_date = COALESCE(?, published_date),
                   raw_html = ?,
                   clean_text = ?,
                   content_hash = ?,
                   source_status = 'FETCHED',
                   notes = ?,
                   fetched_at = ?
             WHERE source_raw_id = ?
            """,
            (
                rec["headline"],
                published_date,
                raw_html,
                clean,
                c_hash,
                notes,
                fetched_at,
                existing_row["source_raw_id"],
            ),
        )
        conn.commit()
        return existing_row["source_raw_id"]

    notes = _build_notes(rec)
    return insert_source_raw(
        conn,
        url=rec["url"],
        title=rec["headline"],
        published_date=published_date,
        raw_html=raw_html,
        clean_text=clean,
        c_hash=c_hash,
        notes=notes,
        fetched_at=fetched_at,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, metavar="PATH", help="CSV of URLs in ingest_csv_urls.py format.")
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    parser.add_argument("--include-existing", action="store_true", help="Refetch URLs already present in source_raw.")
    parser.add_argument("--update-existing", action="store_true", help="Replace existing source_raw bodies for matching URLs.")
    parser.add_argument("--headful", action="store_true", help="Run browser visibly for debugging.")
    parser.add_argument("--channel", default=None, help="Playwright browser channel, e.g. chrome.")
    parser.add_argument("--timeout-ms", type=int, default=45_000)
    parser.add_argument("--wait-ms", type=int, default=1_500)
    parser.add_argument("--proxy-server", default=None, help="Playwright proxy server, e.g. http://brd.superproxy.io:33335.")
    parser.add_argument("--proxy-username", default=None)
    parser.add_argument("--proxy-password", default=None)
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input file not found: {args.input}")

    try:
        cfg = get_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    sync_playwright, _timeout_error = _load_playwright()

    run_id = f"headless_url_backfill_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    log = get_logger("headless_url_backfill", run_id, level=cfg.log_level)
    log.info("Starting headless URL backfill %s input=%s db=%s", run_id, args.input, cfg.db_path)

    init_db(cfg.db_path)
    conn = get_connection(cfg.db_path)
    records = _read_csv(args.input, log)
    if args.limit is not None:
        records = records[:args.limit]

    inserted = updated = skipped_existing = skipped_duplicate = empty_body = errors = 0
    blocked_body = 0
    started = time.monotonic()
    try:
        with sync_playwright() as p:
            proxy = _proxy_from_args_or_env(args)
            browser = p.chromium.launch(
                headless=not args.headful,
                channel=args.channel,
                args=["--disable-http2"],
                proxy=proxy,
            )
            if proxy:
                log.info("Using Playwright proxy server=%s username_set=%s", proxy["server"], bool(proxy.get("username")))
            context = browser.new_context(user_agent=_BROWSER_UA, viewport={"width": 1365, "height": 900})

            for rec in records:
                url = rec["url"]
                existing_row = conn.execute(
                    "SELECT source_raw_id, notes FROM source_raw WHERE url = ?",
                    (url,),
                ).fetchone()
                if existing_row and not (args.include_existing or args.update_existing):
                    skipped_existing += 1
                    continue
                if existing_row and args.include_existing and not args.update_existing:
                    skipped_existing += 1
                    log.warning("URL exists; use --update-existing to replace body for %s", url)
                    continue

                page = context.new_page()
                raw_html, clean, error = _render_and_extract(
                    page,
                    url,
                    timeout_ms=args.timeout_ms,
                    wait_ms=args.wait_ms,
                )
                page.close()
                fetched_at = datetime.now(timezone.utc).isoformat()
                pub_date = rec["published_date"] or extract_page_date(raw_html)

                if clean is None:
                    # Recovery did NOT succeed. Never persist the rendered text and
                    # never emit an Inserted/Updated line for it — a block page
                    # logged as a successful body is exactly how an unusable row
                    # comes to look recovered.
                    is_blocked = bool(error) and error.startswith("block_page")
                    if is_blocked:
                        blocked_body += 1
                    else:
                        empty_body += 1
                    if raw_html is not None and not conn.execute("SELECT 1 FROM source_raw WHERE url = ?", (url,)).fetchone():
                        insert_source_raw(
                            conn,
                            url=url,
                            title=rec["headline"],
                            published_date=pub_date,
                            raw_html=raw_html,
                            clean_text=None,
                            c_hash=None,
                            notes=_build_notes(rec, rejection_reason=error),
                            fetched_at=fetched_at,
                        )
                    if is_blocked:
                        log.warning(
                            "Blocked/interstitial page — NOT recovered, body left unchanged for %s (%s)",
                            url, error,
                        )
                    else:
                        log.warning("No usable body for %s (%s)", url, error)
                    continue

                c_hash = _content_hash(clean)
                duplicate = conn.execute(
                    "SELECT source_raw_id FROM source_raw WHERE content_hash = ?",
                    (c_hash,),
                ).fetchone()
                if duplicate and (not existing_row or duplicate["source_raw_id"] != existing_row["source_raw_id"]):
                    skipped_duplicate += 1
                    log.info("Duplicate content_hash — skipping %s", url)
                    continue

                row_id = _upsert_source_raw(
                    conn,
                    rec=rec,
                    existing_row=existing_row,
                    raw_html=raw_html,
                    clean=clean,
                    c_hash=c_hash,
                    published_date=pub_date,
                    fetched_at=fetched_at,
                    update_existing=args.update_existing,
                    recovery_method="playwright_brightdata" if proxy else "playwright",
                )
                if existing_row and args.update_existing:
                    updated += 1
                    log.info("Updated source_raw_id=%s for %s", row_id, url)
                else:
                    inserted += 1
                    log.info("Inserted source_raw_id=%s for %s", row_id, url)

            context.close()
            browser.close()
    except Exception as exc:
        errors += 1
        log.error("Headless backfill failed: %s", exc, exc_info=True)
        raise
    finally:
        conn.close()

    print("\nHeadless URL backfill complete.")
    print(f"  Run ID:             {run_id}")
    print(f"  DB:                 {cfg.db_path}")
    print(f"  URLs seen:          {len(records)}")
    print(f"  Rows inserted:      {inserted}")
    print(f"  Rows updated:       {updated}")
    print(f"  Skipped existing:   {skipped_existing}")
    print(f"  Skipped duplicate:  {skipped_duplicate}")
    print(f"  Empty body:         {empty_body}")
    print(f"  Blocked body:       {blocked_body}")
    print(f"  Errors:             {errors}")
    print(f"  Runtime seconds:    {round(time.monotonic() - started, 1)}")


if __name__ == "__main__":
    main()
