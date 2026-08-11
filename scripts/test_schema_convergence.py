#!/usr/bin/env python3
"""Assert init_db converges any database to one canonical schema.

Tests the OUTCOME — the post-migration column sets on transaction_record and
staging_extraction — not the migration bookkeeping. This is immune to how a column
arrives (the 001 CREATE, the 002/003 executescripts inside _apply_migrations, or the
db.py ALTER list) and stays correct when a 004 is added.

It replaces an earlier list-comparison whose premise was wrong: db.py executescripts
002/003 (db.py:308-312) rather than listing their columns, so no single list — and no
single .sql file — describes the schema. See decisions.md "Schema Sources of Record".
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db  # clean import: sqlite3 + pathlib only, no config/dotenv chain

TABLES = ("transaction_record", "staging_extraction")


def columns(path: str, table: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def schema_of(path: str) -> dict[str, set[str]]:
    return {t: columns(path, t) for t in TABLES}


def main() -> None:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # Canonical = a fresh DB after init_db.
        fresh = str(tmp / "fresh.db")
        db.init_db(fresh)
        canon = schema_of(fresh)

        # 1. init_db is idempotent — a second run changes nothing.
        db.init_db(fresh)
        for t in TABLES:
            if columns(fresh, t) != canon[t]:
                problems.append(f"init_db not idempotent on {t}")

        # 2. A DB at the initial (001-only) schema must converge to canonical, and
        #    init_db must not crash (e.g. a folded-into-001 column also ALTER-added
        #    by a 002/003 executescript would raise duplicate column).
        base = str(tmp / "base001.db")
        conn = sqlite3.connect(base)
        conn.executescript((ROOT / "schema" / "001_initial.sql").read_text(encoding="utf-8"))
        conn.close()
        db.init_db(base)
        for t in TABLES:
            missing = canon[t] - columns(base, t)
            extra = columns(base, t) - canon[t]
            if missing or extra:
                problems.append(f"001-base DB diverges on {t}: missing={sorted(missing)} extra={sorted(extra)}")

        # 3. Real historical DBs (data/*.db), when present — the genuine drift test.
        #    Each is copied and migrated; must reach at least the canonical columns.
        data = ROOT / "data"
        real = sorted(data.glob("*.db")) if data.exists() else []
        for src in real:
            dst = str(tmp / f"real_{src.name}")
            shutil.copy(src, dst)
            db.init_db(dst)
            for t in TABLES:
                missing = canon[t] - columns(dst, t)
                if missing:
                    problems.append(f"{src.name} after init_db still missing on {t}: {sorted(missing)}")

    if problems:
        for p in problems:
            print("FAIL:", p)
        raise SystemExit(1)
    print(f"PASS schema convergence: fresh == 001-base == {len(real)} historical DB(s) "
          f"on {', '.join(TABLES)}")


if __name__ == "__main__":
    main()
