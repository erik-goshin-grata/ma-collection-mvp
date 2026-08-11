#!/usr/bin/env python3
"""Assert schema/001_initial.sql CREATE TABLE and the db.py migration list agree.

Every column the db.py auto-migration would ALTER-ADD to transaction_record must
already exist in the 001_initial.sql CREATE TABLE, so a fresh DB and a migrated DB
end up the same shape. This catches migration-only columns — the drift that has
recurred each cycle (investment_amount, deal_value_currency were migration-only
before §4.2 folded them in).

No import of db.py (avoids the config/dotenv chain): the CREATE columns come from
executing the DDL into an in-memory DB; the migration columns are parsed from the
db.py transaction_record migration block.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def create_columns(table: str) -> set[str]:
    ddl = (ROOT / "schema" / "001_initial.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def migration_columns() -> set[str]:
    """Column names the db.py transaction_record migration loop would ALTER-ADD."""
    src = (ROOT / "db.py").read_text(encoding="utf-8")
    m = re.search(r"tr_cols\s*=.*?ALTER TABLE transaction_record", src, re.S)
    if not m:
        raise SystemExit("FAIL: could not locate the transaction_record migration block in db.py")
    return set(re.findall(r'\(\s*"(\w+)"\s*,\s*"[^"]+"\s*\)', m.group(0)))


def main() -> None:
    create = create_columns("transaction_record")
    migration = migration_columns()
    missing = migration - create
    if missing:
        print("FAIL: columns in the db.py migration but NOT in 001_initial.sql CREATE "
              "(migration-only drift):")
        for c in sorted(missing):
            print(f"  - {c}")
        raise SystemExit(1)
    print(f"PASS schema/migration parity: all {len(migration)} migration columns present in CREATE")


if __name__ == "__main__":
    main()
