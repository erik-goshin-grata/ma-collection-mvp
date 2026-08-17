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

_OBSERVATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS transaction_field_observation (
    observation_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT,
    field_name                  TEXT NOT NULL,
    field_value                 TEXT,
    field_value_numeric         REAL,
    source_document_id          INTEGER,
    source_section_id           INTEGER,
    staging_extraction_id       INTEGER,
    source_raw_id               INTEGER,
    source_type                 TEXT,
    source_tier                 TEXT,
    model_confidence            TEXT,
    source_published_date       TEXT,
    filing_type                 TEXT,
    agreement_dated_as_of       TEXT,
    observed_as_of_date         TEXT,
    filing_date                 TEXT,
    extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    extraction_prompt_version   TEXT,
    observation_source_stage    TEXT,
    observation_fact_key        TEXT,
    is_current                  INTEGER DEFAULT 1,
    FOREIGN KEY (source_document_id) REFERENCES transaction_document(document_id),
    FOREIGN KEY (source_section_id) REFERENCES transaction_document_section(section_id),
    FOREIGN KEY (staging_extraction_id) REFERENCES staging_extraction(extraction_id),
    FOREIGN KEY (source_raw_id) REFERENCES source_raw(source_raw_id)
);
"""

_OBSERVATION_INDEX_SQL = """
DROP INDEX IF EXISTS idx_observation_unique_current;
DROP INDEX IF EXISTS idx_observation_unique_current_staging;

CREATE INDEX IF NOT EXISTS idx_observation_txn_field
    ON transaction_field_observation(transaction_id, field_name);
CREATE INDEX IF NOT EXISTS idx_observation_filing_date
    ON transaction_field_observation(filing_date);
CREATE INDEX IF NOT EXISTS idx_observation_staging
    ON transaction_field_observation(staging_extraction_id);
CREATE INDEX IF NOT EXISTS idx_observation_source_raw
    ON transaction_field_observation(source_raw_id);
CREATE INDEX IF NOT EXISTS idx_observation_txn_field_current
    ON transaction_field_observation(transaction_id, field_name, is_current);
CREATE INDEX IF NOT EXISTS idx_observation_tier_confidence
    ON transaction_field_observation(transaction_id, field_name, source_tier, model_confidence);
CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current_section
    ON transaction_field_observation (source_section_id, field_name, field_value)
    WHERE is_current = 1 AND source_section_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current_staging
    ON transaction_field_observation (staging_extraction_id, field_name, field_value, COALESCE(observation_fact_key, ''))
    WHERE is_current = 1 AND staging_extraction_id IS NOT NULL;
"""

_PARTICIPANT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entity (
    entity_id              TEXT PRIMARY KEY,
    entity_kind            TEXT NOT NULL DEFAULT 'ORGANIZATION',
    canonical_name         TEXT NOT NULL,
    normalized_name        TEXT NOT NULL,
    legal_name             TEXT,
    display_name           TEXT,
    entity_type            TEXT,
    entity_subtype         TEXT,
    domain                 TEXT,
    ticker                 TEXT,
    cik                    TEXT,
    lei                    TEXT,
    country                TEXT,
    region                 TEXT,
    website_url            TEXT,
    review_status          TEXT DEFAULT 'UNREVIEWED',
    reviewed_by            TEXT,
    reviewed_at            TEXT,
    researcher_notes       TEXT,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_entity_normalized_name ON entity(normalized_name);
CREATE INDEX IF NOT EXISTS idx_entity_ticker          ON entity(ticker);
CREATE INDEX IF NOT EXISTS idx_entity_domain          ON entity(domain);
CREATE INDEX IF NOT EXISTS idx_entity_cik             ON entity(cik);

CREATE TABLE IF NOT EXISTS entity_alias (
    alias_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id              TEXT NOT NULL REFERENCES entity(entity_id),
    alias_name             TEXT NOT NULL,
    normalized_alias       TEXT NOT NULL,
    alias_type             TEXT,
    source_raw_id          INTEGER REFERENCES source_raw(source_raw_id),
    source_document_id     INTEGER REFERENCES transaction_document(document_id),
    source_section_id      INTEGER REFERENCES transaction_document_section(section_id),
    first_observed_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_current             INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_entity_alias_entity ON entity_alias(entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_alias_norm   ON entity_alias(normalized_alias);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_alias_unique_current
    ON entity_alias(entity_id, normalized_alias, COALESCE(alias_type, ''))
    WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS transaction_participant_group (
    group_id               TEXT PRIMARY KEY,
    transaction_id         TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    side                   TEXT NOT NULL,
    group_type             TEXT NOT NULL,
    group_label            TEXT,
    disclosed_group_name   TEXT,
    source_raw_id          INTEGER REFERENCES source_raw(source_raw_id),
    source_document_id     INTEGER REFERENCES transaction_document(document_id),
    source_section_id      INTEGER REFERENCES transaction_document_section(section_id),
    model_confidence       TEXT,
    review_status          TEXT DEFAULT 'UNREVIEWED',
    notes                  TEXT,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_participant_group_txn
    ON transaction_participant_group(transaction_id);
CREATE INDEX IF NOT EXISTS idx_participant_group_type
    ON transaction_participant_group(transaction_id, group_type);

CREATE TABLE IF NOT EXISTS transaction_participant (
    participant_id         TEXT PRIMARY KEY,
    transaction_id         TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    entity_id              TEXT NOT NULL REFERENCES entity(entity_id),
    group_id               TEXT REFERENCES transaction_participant_group(group_id),
    side                   TEXT NOT NULL,
    participant_role       TEXT NOT NULL,
    role_detail            TEXT,
    is_primary             INTEGER,
    is_lead                INTEGER,
    is_existing_investor   INTEGER,
    is_new_investor        INTEGER,
    source_raw_id          INTEGER REFERENCES source_raw(source_raw_id),
    source_document_id     INTEGER REFERENCES transaction_document(document_id),
    source_section_id      INTEGER REFERENCES transaction_document_section(section_id),
    source_stage           TEXT,
    model_confidence       TEXT,
    evidence_text          TEXT,
    review_status          TEXT DEFAULT 'UNREVIEWED',
    reviewed_by            TEXT,
    reviewed_at            TEXT,
    researcher_notes       TEXT,
    is_current             INTEGER DEFAULT 1,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_participant_txn
    ON transaction_participant(transaction_id);
CREATE INDEX IF NOT EXISTS idx_participant_entity
    ON transaction_participant(entity_id);
CREATE INDEX IF NOT EXISTS idx_participant_txn_role_side
    ON transaction_participant(transaction_id, participant_role, side, is_current);
CREATE UNIQUE INDEX IF NOT EXISTS idx_participant_unique_current
    ON transaction_participant(
        transaction_id,
        entity_id,
        participant_role,
        side,
        COALESCE(group_id, '')
    )
    WHERE is_current = 1;
"""

_OBSERVATION_COLUMNS = (
    "observation_id",
    "transaction_id",
    "field_name",
    "field_value",
    "field_value_numeric",
    "source_document_id",
    "source_section_id",
    "staging_extraction_id",
    "source_raw_id",
    "source_type",
    "source_tier",
    "model_confidence",
    "source_published_date",
    "filing_type",
    "agreement_dated_as_of",
    "observed_as_of_date",
    "filing_date",
    "extracted_at",
    "extraction_prompt_version",
    "observation_source_stage",
    "is_current",
)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _column_info(conn: sqlite3.Connection, table_name: str) -> dict[str, sqlite3.Row]:
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _observation_needs_rebuild(conn: sqlite3.Connection) -> bool:
    if not _table_exists(conn, "transaction_field_observation"):
        return False
    cols = _column_info(conn, "transaction_field_observation")
    required = {
        "staging_extraction_id",
        "source_raw_id",
        "source_type",
        "source_tier",
        "model_confidence",
        "source_published_date",
        "filing_type",
        "agreement_dated_as_of",
        "observation_source_stage",
        "observation_fact_key",
    }
    if required - set(cols):
        return True
    for fk in conn.execute("PRAGMA foreign_key_list(transaction_field_observation)"):
        if fk["from"] == "transaction_id":
            return True
    return bool(cols["transaction_id"][3]) or bool(cols["source_document_id"][3])


def _create_observation_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(_OBSERVATION_INDEX_SQL)


def _create_participant_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_PARTICIPANT_SCHEMA_SQL)


def _rebuild_observation_table_for_331b(conn: sqlite3.Connection) -> None:
    tmp_table = "transaction_field_observation_331b_old"
    if _table_exists(conn, tmp_table):
        raise RuntimeError(f"{tmp_table} already exists; resolve interrupted 3.31b migration first")

    conn.executescript("""
        DROP INDEX IF EXISTS idx_observation_txn_field;
        DROP INDEX IF EXISTS idx_observation_filing_date;
        DROP INDEX IF EXISTS idx_observation_staging;
        DROP INDEX IF EXISTS idx_observation_source_raw;
        DROP INDEX IF EXISTS idx_observation_txn_field_current;
        DROP INDEX IF EXISTS idx_observation_tier_confidence;
        DROP INDEX IF EXISTS idx_observation_unique_current;
        DROP INDEX IF EXISTS idx_observation_unique_current_section;
        DROP INDEX IF EXISTS idx_observation_unique_current_staging;
    """)
    conn.execute(f"ALTER TABLE transaction_field_observation RENAME TO {tmp_table}")
    conn.executescript(_OBSERVATION_TABLE_SQL)

    old_cols = set(_column_info(conn, tmp_table))
    copy_cols = [col for col in _OBSERVATION_COLUMNS if col in old_cols]
    col_sql = ", ".join(copy_cols)
    conn.execute(
        f"""
        INSERT INTO transaction_field_observation ({col_sql})
        SELECT {col_sql}
        FROM {tmp_table}
        """
    )
    conn.execute(f"DROP TABLE {tmp_table}")
    _create_observation_indexes(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial CREATE TABLE statements.

    Uses PRAGMA table_info to check existence before ALTER TABLE so each
    migration is idempotent.  New columns are also added to the CREATE TABLE
    definitions in schema/001_initial.sql so fresh databases never need these.
    Listed in drop order so migrations always run in dependency sequence.
    """
    def _existing(table: str) -> set:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    # Base migrations 002 (V2 alignment) and 003 (funding path) add columns to
    # staging_extraction / transaction_record and the staging_investor table.
    # They were never applied to freshly-created DBs (only 001 + the drop-ALTERs
    # below ran), so a new DB was missing v2_event_type / round_size / etc. and
    # the pipeline crashed at classify. Apply each once, guarded by a sentinel
    # column so this is idempotent on already-migrated DBs.
    _mig_dir = Path(__file__).parent / "schema"
    if "v2_event_type" not in _existing("staging_extraction"):
        conn.executescript((_mig_dir / "002_v2_prompt_alignment.sql").read_text(encoding="utf-8"))
    if "round_size" not in _existing("staging_extraction"):
        conn.executescript((_mig_dir / "003_funding_path.sql").read_text(encoding="utf-8"))

    # Drop 3.16 — has_earnout, has_cvr derived flags on transaction_record
    # Drop 3.18 — multi_transaction_index/total on staging_extraction
    # Drop 3.19 — linked_filings_count on transaction_record; document_title on transaction_document
    # Drop 3.20a — 9 agreement-extraction columns on transaction_record
    # Drop 3.20b — 3 observation-diff columns on transaction_record

    se_cols = _existing("staging_extraction")
    for col, col_type in [
        ("multi_transaction_index", "INTEGER DEFAULT 0"),
        ("multi_transaction_total", "INTEGER DEFAULT 1"),
        # Round-object currency (2026-08-11). One tag from the funding prompt's
        # `round.currency`, covering the round-object amounts: round_size,
        # facility_size, total_raised_to_date. Deliberate simplification — it is NOT
        # authoritative for total_raised_to_date (cumulative across prior rounds, may
        # historically span currencies) and facility_size is a different instrument
        # (venture debt); it records the currency the round amounts were stated in.
        # Separate from valuation_currency (pre/post-money). Decision:
        # "Round Currency Enters the Derived-Value Currency Tag".
        ("round_currency", "TEXT"),
        # Balance-sheet extraction (2026-08-17). total_debt and Cash_ST are
        # point-in-time items — no LTM/TTM concept and deliberately no period-type
        # field, only an as-of date. They live here as extracted target_financials
        # metrics (decision "Debt and Cash Inputs"), with currency and as-of date
        # anchored per source so debt from one source cannot borrow cash's currency
        # or balance-sheet date from another. total_debt is TOTAL debt, not net of
        # cash. Cash_ST is cash + equivalents + short-term investments, one field.
        ("total_debt", "REAL"),
        ("total_debt_currency", "TEXT"),
        ("cash_st", "REAL"),
        ("cash_st_currency", "TEXT"),
        ("balance_sheet_as_of_date", "TEXT"),
        # Nullable harness-only ownership transition enum. Populated only when
        # prior/current/resulting ownership evidence is explicit in the source.
        ("stake_transition_type", "TEXT"),
        # Explicit source-backed transaction feature flags. Aggregation owns
        # canonical resolution; HC only supplies evidence-bearing primitives.
        ("is_platform_investment", "INTEGER"),
        ("is_secondary_buyout", "INTEGER"),
        ("is_merger_of_equals", "INTEGER"),
        # Structured HC value facts. Preserves multiple independently typed deal
        # values from one source while legacy value_amount/value_type remain the
        # primary compatibility pair.
        ("value_observations", "TEXT"),
    ]:
        if col not in se_cols:
            conn.execute(f"ALTER TABLE staging_extraction ADD COLUMN {col} {col_type}")

    tr_cols = _existing("transaction_record")
    for col, col_type in [
        # Drop 3.16
        ("has_earnout",                      "INTEGER DEFAULT 0"),
        ("has_cvr",                          "INTEGER DEFAULT 0"),
        # Minority-as-flag foundation — derived in aggregate like is_take_private.
        ("is_minority",                      "INTEGER DEFAULT 0"),
        ("is_platform_investment",           "INTEGER DEFAULT 0"),
        ("is_secondary_buyout",              "INTEGER DEFAULT 0"),
        ("is_merger_of_equals",              "INTEGER DEFAULT 0"),
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
        # Drop 3.32 — derived valuations (finding #8). The LLM captures primitives;
        # aggregate derives these. net_debt is a manual collection input in the
        # interim (not extracted) and is preserved across re-aggregation.
        ("net_debt",                         "REAL"),
        ("equity_value",                     "REAL"),
        ("equity_value_basis",               "TEXT"),
        ("implied_equity_value",             "REAL"),
        ("implied_enterprise_value",         "REAL"),
        ("implied_enterprise_value_basis",   "TEXT"),
        ("enterprise_value",                 "REAL"),
        ("enterprise_value_basis",           "TEXT"),
        # bug #8 — funding/minority "amount invested" (round size / check), kept
        # distinct from equity_value so a check is never recorded as a valuation.
        ("investment_amount",                "REAL"),
        # currency tag-and-defer — the currency the derived value fields
        # (equity_value/implied_equity_value/enterprise_value/investment_amount) are
        # expressed in. NOT converted to USD; downstream must not assume USD.
        ("deal_value_currency",              "TEXT"),
        # §4.2 — Tier-1 transaction_value (control-conditional) + its basis; total_debt
        # (total debt, NOT net of cash; manual interim input like cash_st/net_debt, an
        # extracted metric later), preserved across re-aggregation. reported net_debt
        # or total_debt - cash_st feeds implied_enterprise_value. pct_acquired_source stamps §2.6.
        ("total_debt",                       "REAL"),
        ("cash_st",                          "REAL"),
        # §2.10 items 1-2 — currency and period anchors for the balance-sheet
        # inputs. implied_enterprise_value adds consideration in deal currency to
        # net debt in the target's reporting currency; without a recorded currency
        # that sum cannot be checked. Manual interim inputs like the amounts they
        # qualify, preserved across re-aggregation, and unpopulated until
        # total_debt/Cash_ST extraction lands. balance_sheet_as_of_date is the
        # period anchor those figures are stated as of, for coherence against the
        # announced date and the multiple denominator's own period.
        ("net_debt_currency",                "TEXT"),
        ("total_debt_currency",              "TEXT"),
        ("cash_st_currency",                 "TEXT"),
        ("balance_sheet_as_of_date",         "TEXT"),
        ("transaction_value",                "REAL"),
        ("transaction_value_basis",          "TEXT"),
        ("pct_acquired_source",              "TEXT"),
        ("stake_transition_type",            "TEXT"),
        # Observation coverage (2026-08-11): signing_date_precision reached
        # staging in 002 but never received a transaction_record column, unlike
        # announced_date_precision / closed_date_precision. Added so the now-wired
        # _FIELDS read has a home to write. (Decision: "Observation Write Path
        # Must Cover Every Field Aggregation Reads".)
        ("signing_date_precision",           "TEXT"),
        # Round-object currency, propagated by aggregation alongside round_size.
        # See staging_extraction.round_currency for scope + caveats.
        ("round_currency",                   "TEXT"),
    ]:
        if col not in tr_cols:
            conn.execute(f"ALTER TABLE transaction_record ADD COLUMN {col} {col_type}")

    td_cols = _existing("transaction_document")
    if "document_title" not in td_cols:
        conn.execute("ALTER TABLE transaction_document ADD COLUMN document_title TEXT")
    # Drop 3.22b — per-document extraction tracking
    if "agreement_extracted_at" not in td_cols:
        conn.execute("ALTER TABLE transaction_document ADD COLUMN agreement_extracted_at DATETIME")

    # Drop 3.31b — rebuild transaction_field_observation so source-row
    # observations can be written before clustering and without a deal document.
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "transaction_field_observation" not in existing_tables:
        conn.executescript(_OBSERVATION_TABLE_SQL)
    elif _observation_needs_rebuild(conn):
        _rebuild_observation_table_for_331b(conn)
    _create_observation_indexes(conn)

    # Drop 3.32a — additive organization participant schema, no backfill.
    _create_participant_schema(conn)

    from lib.observation_writer import (
        backfill_agreement_observation_provenance,
        backfill_observation_transaction_ids,
        backfill_staging_observations,
    )

    backfill_agreement_observation_provenance(conn)
    backfill_staging_observations(conn)
    backfill_observation_transaction_ids(conn)

    # Drop 3.26 — partial expression unique index on transaction_security.
    # COALESCE on security_class and shares_outstanding_as_of normalises NULLs to ''
    # so that identical rows with NULL-valued columns still collide. Plain column
    # names would not catch the duplicate because SQLite treats NULLs as distinct
    # in unique indexes. Scoped to is_current=1; soft-deleted history unconstrained.
    # Wipe transaction_security before migrating if duplicate rows already exist.
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_security_unique_current
        ON transaction_security (
            extraction_source_document_id,
            security_type,
            COALESCE(security_class, ''),
            COALESCE(shares_outstanding_as_of, '')
        )
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
    conn.row_factory = sqlite3.Row
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
