"""
Ingest a CSV of M&A news URLs into source_raw for LLM-extraction testing.

This is an alternate Stage 1 for the pipeline: instead of discovering press
releases from PR Newswire, it scrapes a fixed list of URLs supplied in a CSV.
Once ingested, run the downstream LLM pipeline as usual:

    python scripts/ingest_csv_urls.py --input ~/Downloads/V8_urls.csv --limit 5
    python run.py --mode=resume        # Stages 2-14; SEC stages no-op on WEB_URL rows

Expected CSV columns (header row required):
    URL, Source, Target, Acquirer, Headline, Story date

Usage:
    python scripts/ingest_csv_urls.py --input PATH [--limit N]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

# Allow running as `python scripts/ingest_csv_urls.py` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adapters.csv_url as _adapter
from config import ConfigurationError, get_config
from db import get_connection, init_db
from logger import get_logger


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ingest_csv_urls.py",
        description="Ingest a CSV of URLs into source_raw (alternate Stage 1).",
    )
    parser.add_argument("--input", required=True, metavar="PATH",
                        help="Path to the CSV of URLs.")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Only ingest the first N URLs (smoke testing).")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        parser.error(f"input file not found: {args.input}")

    try:
        cfg = get_config()
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_id = f"csv_ingest_{ts}"
    log = get_logger("ingest_csv_urls", run_id, level=cfg.log_level)
    log.info("Starting CSV ingest run %s  input=%s limit=%s",
             run_id, args.input, args.limit)

    try:
        init_db(cfg.db_path)
        conn = get_connection(cfg.db_path)
    except Exception as exc:
        log.error("Database initialisation failed: %s", exc, exc_info=True)
        sys.exit(1)

    try:
        summary = _adapter.run(
            conn=conn, cfg=cfg, run_id=run_id,
            csv_path=args.input, limit=args.limit,
        )
    finally:
        conn.close()

    print("\nCSV ingest complete.")
    print(f"  Run ID:            {run_id}")
    print(f"  URLs seen:         {summary['urls_seen']}")
    print(f"  Rows inserted:     {summary['rows_inserted']}")
    print(f"  Skipped (dupes):   {summary['skipped_duplicates']}")
    print(f"  Robots-blocked:    {summary['robots_blocked']}")
    print(f"  Empty body:        {summary['empty_body']}")
    print(f"  Errors:            {len(summary['errors'])}")
    if summary["errors"]:
        for e in summary["errors"][:10]:
            print(f"    - {e}")
    print("\nNext: python run.py --mode=resume")


if __name__ == "__main__":
    main()
