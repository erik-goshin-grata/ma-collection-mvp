"""
SQLite connection management for the M&A Collection MVP pipeline.

Provides two public functions:
  init_db()       — runs the DDL script to create tables and indexes.
  get_connection() — returns a configured connection ready for use.

Both are idempotent. The schema uses CREATE TABLE IF NOT EXISTS throughout,
so init_db() is safe to call against an existing database. Callers are
responsible for closing connections returned by get_connection().

Spec references: schema/001_initial.sql, mvp_goal_and_schema.md §6,
                 specs/pipeline.md §5 (idempotency rules)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema" / "001_initial.sql"


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial CREATE TABLE statements.

    Uses PRAGMA table_info to check existence before ALTER TABLE so each
    migration is idempotent.  New columns are also added to the CREATE TABLE
    definitions in schema/001_initial.sql so fresh databases never need these.
    """
    def _existing(table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    tr_cols = _existing("transaction_record")
    for col, col_type in [
        ("acquirer_merger_sub_name",         "TEXT"),
        ("merger_structure",                 "TEXT"),
        ("has_mac_clause",                   "INTEGER DEFAULT 0"),
        ("requires_target_shareholder_vote", "INTEGER"),
        ("target_vote_threshold",            "TEXT"),
        ("closing_conditions_summary",       "TEXT"),
        ("target_total_diluted_shares",      "INTEGER"),
        ("fully_diluted_calc_quality",       "TEXT"),
        ("agreement_extraction_status",      "TEXT"),
    ]:
        if col not in tr_cols:
            conn.execute(f"ALTER TABLE transaction_record ADD COLUMN {col} {col_type}")

    td_cols = _existing("transaction_document")
    if "document_title" not in td_cols:
        conn.execute("ALTER TABLE transaction_document ADD COLUMN document_title TEXT")

    conn.commit()


def init_db(db_path: str) -> None:
    """Create all tables and indexes defined in schema/001_initial.sql.

    Safe to call on an existing database; existing tables are left untouched.
    Creates the parent directory of db_path if it does not exist.

    Parameters
    ----------
    db_path:
        File-system path to the SQLite database file.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(ddl)
        _apply_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: str) -> sqlite3.Connection:
    """Return an open SQLite connection with the project's standard settings.

    Settings applied:
    - row_factory = sqlite3.Row   (column access by name on cursor rows)
    - foreign_keys = ON           (enforces FK constraints at runtime)
    - journal_mode = WAL          (better read concurrency; safe for single-writer MVP)

    The caller is responsible for calling conn.close() when done.

    Parameters
    ----------
    db_path:
        File-system path to the SQLite database file.

    Returns
    -------
    sqlite3.Connection
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn
