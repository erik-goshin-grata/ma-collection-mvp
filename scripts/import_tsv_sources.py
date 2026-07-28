#!/usr/bin/env python3
"""Import local TSV story rows into source_raw for validation runs.

This utility intentionally leaves the pipeline stages unchanged. It loads
external story text into the same raw-source table shape the existing PR
Newswire scrape stage produces, so downstream validation can use
``run.py --mode=extract`` and later modes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import get_config
from db import get_connection, init_db
from utils import content_hash


DEFAULT_INPUT = (
    ROOT
    / "data"
    / "input_stories"
    / "news_events_full_review_2026-07-06_to_2026-07-08.tsv"
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _iso_date(value: str | None) -> str | None:
    value = _clean(value)
    if not value:
        return None
    # Preserve simple YYYY-MM-DD values; truncate ISO timestamps to date.
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    return value


def _unique_url(conn: sqlite3.Connection, base_url: str, event_id: str | None) -> str:
    if not conn.execute("SELECT 1 FROM source_raw WHERE url=?", (base_url,)).fetchone():
        return base_url
    if event_id:
        candidate = f"{base_url}#event_id={event_id}"
        if not conn.execute("SELECT 1 FROM source_raw WHERE url=?", (candidate,)).fetchone():
            return candidate
    return base_url


def _insert_row(
    conn: sqlite3.Connection,
    *,
    row: dict[str, str],
    row_number: int,
    source_path: Path,
    args: argparse.Namespace,
    fetched_at: str,
) -> str:
    title = _clean(row.get(args.title_column))
    body = _clean(row.get(args.body_column))
    source_url = _clean(row.get(args.url_column))
    event_id = _clean(row.get(args.id_column)) if args.id_column else None
    published_date = _iso_date(row.get(args.date_column))
    if not published_date and args.fallback_date_column:
        published_date = _iso_date(row.get(args.fallback_date_column))

    if not title or not body or not source_url:
        return "skipped_missing_required"

    c_hash = content_hash(body)
    if args.skip_duplicate_hash and conn.execute(
        "SELECT 1 FROM source_raw WHERE content_hash=?", (c_hash,)
    ).fetchone():
        return "skipped_duplicate_hash"

    url = _unique_url(conn, source_url, event_id)
    if url == source_url and conn.execute(
        "SELECT 1 FROM source_raw WHERE url=?", (url,)
    ).fetchone():
        return "skipped_duplicate_url"

    notes: dict[str, Any] = {
        "import_source": "tsv",
        "import_file": str(source_path.relative_to(ROOT)),
        "import_row_number": row_number,
    }
    for col in [
        args.id_column,
        args.category_column,
        "found_at",
        "effective_date",
        "source_published_at",
        "summary",
        "article_sentence",
        "party_1_name",
        "party_1_domain",
        "party_2_name",
        "party_2_domain",
    ]:
        if col and col in row:
            val = _clean(row.get(col))
            if val:
                notes[col] = val

    conn.execute(
        """
        INSERT INTO source_raw
            (source_type, source_tier, url, title, published_date,
             raw_html, clean_text, content_hash, source_status, notes, fetched_at)
        VALUES
            (?, ?, ?, ?, ?, NULL, ?, ?, 'FETCHED', ?, ?)
        """,
        (
            args.source_type,
            args.source_tier,
            url,
            title,
            published_date,
            body,
            c_hash,
            json.dumps(notes, sort_keys=True),
            fetched_at,
        ),
    )
    return "inserted"


def import_tsv(args: argparse.Namespace) -> dict[str, int]:
    source_path = Path(args.input).expanduser()
    if not source_path.is_absolute():
        source_path = ROOT / source_path
    source_path = source_path.resolve()

    cfg = get_config()
    db_path = args.db_path or cfg.db_path
    init_db(db_path)
    conn = get_connection(db_path)

    limit = args.limit if args.limit is not None else cfg.max_fetches

    counts: dict[str, int] = {
        "seen": 0,
        "inserted": 0,
        "skipped_missing_required": 0,
        "skipped_duplicate_hash": 0,
        "skipped_duplicate_url": 0,
    }
    fetched_at = datetime.now(timezone.utc).isoformat()

    with source_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        required = {args.title_column, args.body_column, args.url_column}
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise SystemExit(f"Missing required TSV columns: {', '.join(missing)}")

        for row_number, row in enumerate(reader, start=2):
            if limit is not None and counts["inserted"] >= limit:
                break
            counts["seen"] += 1
            result = _insert_row(
                conn,
                row=row,
                row_number=row_number,
                source_path=source_path,
                args=args,
                fetched_at=fetched_at,
            )
            counts[result] = counts.get(result, 0) + 1

    conn.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a TSV of story text into source_raw for local validation."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to TSV file.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum TSV rows to import. Defaults to .env MAX_FETCHES.",
    )
    parser.add_argument("--db-path", default=None, help="Override DB path; defaults to .env DB_PATH.")
    parser.add_argument("--title-column", default="source_title")
    parser.add_argument("--body-column", default="source_body_lite")
    parser.add_argument("--url-column", default="source_url")
    parser.add_argument("--date-column", default="effective_date")
    parser.add_argument("--fallback-date-column", default="source_published_at")
    parser.add_argument("--id-column", default="event_id")
    parser.add_argument("--category-column", default="category")
    parser.add_argument("--source-type", default="PR_NEWSWIRE")
    parser.add_argument("--source-tier", default="T2")
    parser.add_argument(
        "--allow-duplicate-hash",
        action="store_false",
        dest="skip_duplicate_hash",
        help="Insert rows even when clean_text hashes already exist.",
    )
    parser.set_defaults(skip_duplicate_hash=True)
    args = parser.parse_args()

    counts = import_tsv(args)
    print("TSV import complete")
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")


if __name__ == "__main__":
    main()
