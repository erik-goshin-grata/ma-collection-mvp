"""
Drop 3.32a organization participant backfill CLI.

Usage:
    python3 scripts/backfill_332a_participants.py --db-path /path/to/copy.db --dry-run
    python3 scripts/backfill_332a_participants.py --db-path /path/to/copy.db
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from lib.participant_backfill import backfill_participants


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Drop 3.32a organization participants from flat transaction fields."
    )
    parser.add_argument(
        "--db-path",
        default=os.environ.get("DB_PATH", "data/ma_mvp.db"),
        help="SQLite DB path. Defaults to DB_PATH or data/ma_mvp.db.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan rows and report counts without writing participant rows.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if args.dry_run and not db_path.exists():
        parser.error(f"--db-path does not exist: {db_path}")

    if not args.dry_run:
        init_db(str(db_path))

    conn = get_connection(str(db_path))
    try:
        summary = backfill_participants(conn, dry_run=args.dry_run)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    output = {
        "db_path": str(db_path),
        "summary": summary,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
