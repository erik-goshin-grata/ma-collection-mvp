"""
Ingest a PredictLeads News-Events TSV into source_raw for LLM-extraction testing.

Alternate Stage 1: instead of scraping URLs, this reads a PredictLeads export TSV
that ALREADY contains the article body (source_body_lite), and inserts each event
directly into source_raw as source_type=WEB_URL / source_status=FETCHED — the same
shape the PR Newswire / csv_url adapters produce — so the downstream LLM pipeline
(relevancy -> deal_type -> funding extraction -> ... -> export) runs unchanged.

No network fetch: we reuse PredictLeads' scraped text (cost lesson: never re-scrape).

The PredictLeads recipient/investor/category/financing_type/amount are stashed into
source_raw.notes under "predictleads_reference" as lightweight ground truth for a
downstream extraction review.

Expected TSV columns (from fetch_news_events.py, EVENT_COLUMNS): id, category,
amount, financing_type, company1_name, company1_domain, company2_name,
company2_domain, source_url, source_title, source_published_at, source_body_lite,
summary.

Usage:
    python scripts/ingest_predictleads.py --input PATH.tsv [--limit N]
Then:
    DB_PATH=data/pl_funding.db python run.py --mode=resume
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ConfigurationError, get_config
from db import get_connection, init_db
from logger import get_logger
from utils import content_hash as _content_hash

# PredictLeads news-event categories map company1/company2 to different roles.
_INVESTOR_FIRST = {"invests_into", "invests_into_assets"}


def _roles(row: dict) -> tuple[dict, dict]:
    """Return (recipient, investor) dicts of {name, domain} based on category."""
    c1 = {"name": (row.get("company1_name") or "").strip(), "domain": (row.get("company1_domain") or "").strip()}
    c2 = {"name": (row.get("company2_name") or "").strip(), "domain": (row.get("company2_domain") or "").strip()}
    if row.get("category") in _INVESTOR_FIRST:
        return c2, c1  # company1 = investor, company2 = recipient
    return c1, c2      # receives_financing: company1 = recipient


def _notes(row: dict) -> str:
    recipient, investor = _roles(row)
    return json.dumps({
        "predictleads_reference": {
            "event_id": (row.get("id") or "").strip(),
            "category": (row.get("category") or "").strip(),
            "financing_type": (row.get("financing_type") or "").strip(),
            "amount": (row.get("amount") or "").strip(),
            "recipient": recipient,
            "investor": investor,
        },
        "source": "predictleads",
    })


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingest_predictleads.py")
    parser.add_argument("--input", required=True, metavar="PATH", help="PredictLeads news-events TSV.")
    parser.add_argument("--limit", type=int, default=None, metavar="N")
    args = parser.parse_args()
    if not os.path.isfile(args.input):
        parser.error(f"input file not found: {args.input}")

    try:
        cfg = get_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"pl_ingest_{ts}"
    log = get_logger("ingest_predictleads", run_id, level=cfg.log_level)
    log.info("PredictLeads ingest %s  input=%s  db=%s  limit=%s", run_id, args.input, cfg.db_path, args.limit)

    init_db(cfg.db_path)
    conn = get_connection(cfg.db_path)

    inserted = skipped_dupe = empty_body = no_url = 0
    seen_hashes: set[str] = set()
    try:
        with open(args.input, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            rows = list(reader)
        if args.limit is not None:
            rows = rows[:args.limit]
        log.info("TSV rows to ingest: %d", len(rows))

        for row in rows:
            url = (row.get("source_url") or "").strip()
            body = (row.get("source_body_lite") or "").strip()
            if not url:
                no_url += 1
                continue
            if len(body) < 100:
                empty_body += 1
                continue
            if conn.execute("SELECT 1 FROM source_raw WHERE url = ?", (url,)).fetchone():
                skipped_dupe += 1
                continue
            c_hash = _content_hash(body)
            if c_hash in seen_hashes or conn.execute(
                "SELECT 1 FROM source_raw WHERE content_hash = ?", (c_hash,)
            ).fetchone():
                skipped_dupe += 1
                continue
            seen_hashes.add(c_hash)
            pub = (row.get("source_published_at") or "").strip()[:10] or None
            title = (row.get("source_title") or row.get("summary") or "").strip() or None
            conn.execute(
                """
                INSERT INTO source_raw
                    (source_type, source_tier, url, title, published_date,
                     raw_html, clean_text, content_hash, source_status, notes, fetched_at)
                VALUES ('WEB_URL','T2', ?, ?, ?, NULL, ?, ?, 'FETCHED', ?, ?)
                """,
                (url, title, pub, body, c_hash, _notes(row),
                 datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            inserted += 1
    finally:
        conn.close()

    print("\nPredictLeads ingest complete.")
    print(f"  Run ID:          {run_id}")
    print(f"  DB:              {cfg.db_path}")
    print(f"  Rows inserted:   {inserted}")
    print(f"  Skipped (dupes): {skipped_dupe}")
    print(f"  Empty body:      {empty_body}")
    print(f"  No URL:          {no_url}")
    print(f"\nNext: DB_PATH={cfg.db_path} python run.py --mode=resume")


if __name__ == "__main__":
    main()
