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
    Listed in drop order so migrations always run in dependency sequence.
    """
    def _existing(table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    # Drop 3.16 — has_earnout, has_cvr derived flags on transaction_record
    # Drop 3.18 — multi_transaction_index/total on staging_extraction
    # Drop 3.19 — linked_filings_count on transaction_record; document_title on transaction_document
    # Drop 3.20a — 9 agreement-extraction columns on transaction_record
    # Drop 3.20b — 3 observation-diff columns on transaction_record

    se_cols = _existing("staging_extraction")
    for col, col_type in [
        ("multi_transaction_index", "INTEGER DEFAULT 0"),
        ("multi_transaction_total", "INTEGER DEFAULT 1"),
    ]:
        if col not in se_cols:
            conn.execute(f"ALTER TABLE staging_extraction ADD COLUMN {col} {col_type}")

    tr_cols = _existing("transaction_record")
    for col, col_type in [
        # Drop 3.16
        ("has_earnout",                      "INTEGER DEFAULT 0"),
        ("has_cvr",                          "INTEGER DEFAULT 0"),
        # Drop 3.19
        ("linked_filings_count",             "INTEGER DEFAULT 0"),
        # Drop 3.20a
        ("acquirer_merger_sub_name",         "TEXT"),
        ("merger_structure",                 "TEXT"),
        ("has_mac_clause",                   "INTEGER DEFAULT 0"),
        ("requires_target_shareholder_vote", "INTEGER"),
        ("target_vote_threshold",            "TEXT"),
        ("closing_conditions_summary",       "TEXT"),
        ("target_total_diluted_shares",      "INTEGER"),
        ("fully_diluted_calc_quality",       "TEXT"),
        ("agreement_extraction_status",      "TEXT"),
        # Drop 3.20b
        ("has_observation_changes",          "INTEGER DEFAULT 0"),
        ("observation_changes_field_count",  "INTEGER DEFAULT 0"),
        ("observation_changes_summary",      "TEXT"),
    ]:
        if col not in tr_cols:
            conn.execute(f"ALTER TABLE transaction_record ADD COLUMN {col} {col_type}")

    td_cols = _existing("transaction_document")
    if "document_title" not in td_cols:
        conn.execute("ALTER TABLE transaction_document ADD COLUMN document_title TEXT")
    # Drop 3.22b — per-document extraction tracking
    if "agreement_extracted_at" not in td_cols:
        conn.execute("ALTER TABLE transaction_document ADD COLUMN agreement_extracted_at DATETIME")

    # Drop 3.20b: create transaction_field_observation table if not present
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "transaction_field_observation" not in existing_tables:
        conn.executescript("""
            CREATE TABLE transaction_field_observation (
                observation_id              INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id              TEXT NOT NULL,
                field_name                  TEXT NOT NULL,
                field_value                 TEXT,
                field_value_numeric         REAL,
                source_document_id          INTEGER NOT NULL,
                source_section_id           INTEGER,
                observed_as_of_date         TEXT,
                filing_date                 TEXT,
                extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                extraction_prompt_version   TEXT,
                is_current                  INTEGER DEFAULT 1,
                FOREIGN KEY (transaction_id) REFERENCES transaction_record(transaction_id),
                FOREIGN KEY (source_document_id) REFERENCES transaction_document(document_id),
                FOREIGN KEY (source_section_id) REFERENCES transaction_document_section(section_id)
            );
            CREATE INDEX IF NOT EXISTS idx_observation_txn_field
                ON transaction_field_observation(transaction_id, field_name);
            CREATE INDEX IF NOT EXISTS idx_observation_filing_date
                ON transaction_field_observation(filing_date);
        """)

    # Drop 3.25 — partial unique index on (source_section_id, field_name, field_value)
    # for idempotent observation inserts. Scoped to is_current=1 so soft-deleted
    # history rows are unconstrained. If existing rows contain duplicates the CREATE
    # will fail — wipe transaction_field_observation first, then run migrations.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current
        ON transaction_field_observation (source_section_id, field_name, field_value)
        WHERE is_current = 1
    """)

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
