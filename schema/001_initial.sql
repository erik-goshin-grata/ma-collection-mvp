-- =============================================================================
-- M&A Collection MVP — Initial Schema
-- Version: 0.2 (reflects Drop 2.1 prompt revisions)
-- Target: SQLite 3.x
-- =============================================================================
-- Design notes:
--   - Enumerations are enforced at the application layer, not via SQLite CHECK
--     constraints, to allow prompt-layer iteration without DDL migrations.
--     Comments next to each enum column list the accepted values as of v0.2.
--   - JSON blobs are stored as TEXT (SQLite native).
--   - Timestamps are ISO 8601 UTC strings.
--   - All tables have a created_at audit column; mutable tables add updated_at.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. source_raw — raw fetched content (PR Newswire releases, SEC filings)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_raw (
    source_raw_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type         TEXT NOT NULL,       -- PR_NEWSWIRE | SEC_8K_ITEM_101 | SEC_EXHIBIT_21
    source_tier         TEXT NOT NULL,       -- T1 | T2 | T3
    url                 TEXT NOT NULL UNIQUE,
    title               TEXT,
    published_date      TEXT,                -- ISO 8601 date
    raw_html            TEXT,
    clean_text          TEXT,
    content_hash        TEXT,                -- SHA-256 of normalized clean_text; enables dedup
    source_status       TEXT NOT NULL DEFAULT 'FETCHED',
                                             -- FETCHED | RELEVANT | NOT_RELEVANT | RELEVANCY_FAILED
    notes               TEXT,                -- JSON blob for adapter-specific context (e.g., sec_api trigger info)
    fetched_at          TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_raw_status   ON source_raw(source_status);
CREATE INDEX IF NOT EXISTS idx_source_raw_hash     ON source_raw(content_hash);
CREATE INDEX IF NOT EXISTS idx_source_raw_type     ON source_raw(source_type);
CREATE INDEX IF NOT EXISTS idx_source_raw_pubdate  ON source_raw(published_date);


-- -----------------------------------------------------------------------------
-- 2. staging_extraction — per-source extraction results before clustering
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging_extraction (
    extraction_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_raw_id               INTEGER NOT NULL REFERENCES source_raw(source_raw_id),
    status                      TEXT NOT NULL,
                                -- CLASSIFIED | HC_EXTRACTED | SEC_TRIGGERED | SEC_NOT_TRIGGERED
                                -- | SEC_ENRICHED | LC_EXTRACTED | CLUSTERED | AGGREGATED | PROMPT_FAILED

    -- Deal type classification outputs (from deal_type_classifier prompt)
    deal_type                   TEXT,
                                -- ACQUISITION | MERGER | SPIN_SPLIT | REVERSE_MERGER
                                -- | JOINT_VENTURE | MINORITY_INVESTMENT | UNKNOWN
    spin_split_type             TEXT,        -- SPIN_OFF | SPLIT | null (only for SPIN_SPLIT)
    distribution_mechanism      TEXT,        -- PRO_RATA | EXCHANGE_OFFER | null (only for SPIN_SPLIT)
    target_type                 TEXT,        -- STANDALONE_COMPANY | BUSINESS_UNIT | SUBSIDIARY | null
    event_type                  TEXT,        -- ANNOUNCEMENT | CLOSE | AMENDMENT | TERMINATION
    target_status               TEXT,
                                -- PUBLIC | PRIVATE | SUBSIDIARY_OF_PUBLIC | SUBSIDIARY_OF_PRIVATE | UNKNOWN

    -- High-confidence extraction outputs
    target_name                 TEXT,
    target_domain               TEXT,
    target_ticker               TEXT,
    acquirer_name               TEXT,
    acquirer_domain             TEXT,
    acquirer_ticker             TEXT,
    acquirer_type               TEXT,
                                -- STRATEGIC_CORPORATE | PRIVATE_EQUITY | VENTURE_CAPITAL
                                -- | SOVEREIGN_WEALTH_FUND | PENSION_FUND | HEDGE_FUND
                                -- | FAMILY_OFFICE | INDIVIDUAL | MANAGEMENT | EMPLOYEE_GROUP
                                -- | SPAC | CONSORTIUM | PE_PORTFOLIO | OTHER_FINANCIAL_SPONSOR | UNKNOWN
    parent_seller_name          TEXT,
    parent_seller_ticker        TEXT,
    announced_date              TEXT,        -- ISO 8601
    closed_date                 TEXT,        -- ISO 8601
    signing_date                TEXT,        -- ISO 8601
    value_amount                REAL,
    value_currency              TEXT,        -- ISO 4217
    value_type                  TEXT,        -- EQUITY_VALUE | TRANSACTION_VALUE | ENTERPRISE_VALUE | UNDISCLOSED
    value_type_confidence       TEXT,        -- HIGH | MEDIUM | LOW
    value_qualifier             TEXT,
    per_share_price             REAL,
    target_revenue              REAL,
    target_revenue_period_type  TEXT,        -- LTM | FY | TTM | CY | QUARTER | NTM | UNKNOWN
    target_revenue_period_end   TEXT,
    target_ebitda               REAL,
    target_ebitda_period_type   TEXT,
    target_ebitda_period_end    TEXT,
    financials_currency         TEXT,

    -- Low-confidence extraction outputs
    -- consideration_components stored as JSON array
    consideration_components    TEXT,        -- JSON: [{"form": "CASH", "amount": 500000000, ...}]
    -- Orchestrator-derived, not from prompt:
    consideration_type          TEXT,        -- CASH | STOCK | CASH_AND_STOCK | ELECTION | OTHER | null

    -- Flags (booleans)
    includes_earnout            INTEGER,     -- 0/1
    hostile                     INTEGER,
    competing_bid               INTEGER,
    regulatory_approvals_required INTEGER,

    -- Go-shop
    has_go_shop                 INTEGER,     -- 0/1
    go_shop_period_days         INTEGER,

    -- Termination fees (split by party)
    target_fee_amount           REAL,
    target_fee_percentage       REAL,
    acquirer_fee_amount         REAL,
    acquirer_fee_percentage     REAL,

    -- SEC enrichment status (set by stages 5 and 6)
    sec_lookup_status           TEXT,        -- NOT_TRIGGERED | TRIGGERED | NO_MATCH | ERROR
                                             -- NULL until stage 5 runs.
                                             -- Stage 5 sets NOT_TRIGGERED or TRIGGERED.
                                             -- Stage 6 leaves TRIGGERED on success, or sets NO_MATCH / ERROR.

    -- Metadata — one version column per prompt stage
    model_confidence            TEXT,        -- HIGH | MEDIUM | LOW
    dt_prompt_version           TEXT,        -- deal_type_classifier version, e.g., "0.2"
    hc_prompt_version           TEXT,        -- high_confidence_extraction version
    lc_prompt_version           TEXT,        -- low_confidence_extraction version
    transaction_cluster_id      TEXT,        -- assigned at clustering stage
    notes                       TEXT,
    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_staging_status          ON staging_extraction(status);
CREATE INDEX IF NOT EXISTS idx_staging_cluster         ON staging_extraction(transaction_cluster_id);
CREATE INDEX IF NOT EXISTS idx_staging_source          ON staging_extraction(source_raw_id);
CREATE INDEX IF NOT EXISTS idx_staging_announced       ON staging_extraction(announced_date);


-- -----------------------------------------------------------------------------
-- 3. advisor — advisor entries from low_confidence_extraction (per-extraction)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS advisor (
    advisor_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id       INTEGER NOT NULL REFERENCES staging_extraction(extraction_id),
    name                TEXT NOT NULL,
    type                TEXT NOT NULL,       -- FINANCIAL | LEGAL | OTHER
    advised_party       TEXT NOT NULL,       -- TARGET | ACQUIRER | PARENT_SELLER | BOTH | UNKNOWN
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_advisor_extraction ON advisor(extraction_id);


-- -----------------------------------------------------------------------------
-- 4. transaction — canonical aggregated deal records (one per cluster)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_record (
    transaction_id              TEXT PRIMARY KEY,    -- matches cluster_id; format: tc_xxxxxxxxxxxx
    deal_type                   TEXT,
    spin_split_type             TEXT,
    distribution_mechanism      TEXT,
    target_type                 TEXT,
    event_type                  TEXT,
    target_status               TEXT,

    -- Parties
    target_name                 TEXT,
    target_domain               TEXT,
    target_ticker               TEXT,
    acquirer_name               TEXT,
    acquirer_domain             TEXT,
    acquirer_ticker             TEXT,
    acquirer_type               TEXT,
    parent_seller_name          TEXT,
    parent_seller_ticker        TEXT,

    -- Dates
    announced_date              TEXT,
    closed_date                 TEXT,
    signing_date                TEXT,

    -- Value
    value_amount                REAL,
    value_currency              TEXT,
    value_type                  TEXT,
    per_share_price             REAL,

    -- Target financials
    target_revenue              REAL,
    target_revenue_period_type  TEXT,
    target_revenue_period_end   TEXT,
    target_ebitda               REAL,
    target_ebitda_period_type   TEXT,
    target_ebitda_period_end    TEXT,
    financials_currency         TEXT,

    -- Consideration
    consideration_type          TEXT,        -- derived from components
    consideration_components    TEXT,        -- JSON

    -- Flags
    includes_earnout            INTEGER,
    hostile                     INTEGER,
    competing_bid               INTEGER,
    regulatory_approvals_required INTEGER,

    -- Go-shop
    has_go_shop                 INTEGER,
    go_shop_period_days         INTEGER,

    -- Termination fees
    target_fee_amount           REAL,
    target_fee_percentage       REAL,
    acquirer_fee_amount         REAL,
    acquirer_fee_percentage     REAL,

    -- Derived flags (computed downstream from deal context)
    is_take_private             INTEGER,     -- target_status=PUBLIC + acquirer_type=PRIVATE_EQUITY/PE_PORTFOLIO
    is_add_on                   INTEGER,     -- acquirer_type=PE_PORTFOLIO
    is_divestiture              INTEGER,     -- target_type in (BUSINESS_UNIT, SUBSIDIARY)

    -- Metadata
    is_current                  INTEGER NOT NULL DEFAULT 1,  -- older versions flipped to 0 on re-aggregation
    aggregation_version         INTEGER NOT NULL DEFAULT 1,
    notes                       TEXT,
    created_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transaction_current  ON transaction_record(is_current);
CREATE INDEX IF NOT EXISTS idx_transaction_deal     ON transaction_record(deal_type);
CREATE INDEX IF NOT EXISTS idx_transaction_announced ON transaction_record(announced_date);


-- -----------------------------------------------------------------------------
-- 5. transaction_source — links transactions to their contributing source_raw rows
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_source (
    transaction_id      TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    source_raw_id       INTEGER NOT NULL REFERENCES source_raw(source_raw_id),
    role                TEXT,                -- PRIMARY | ENRICHMENT | CONFIRMATORY
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (transaction_id, source_raw_id)
);


-- -----------------------------------------------------------------------------
-- 6. summary — natural-language deal summaries
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS summary (
    summary_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    summary_text        TEXT NOT NULL,
    word_count          INTEGER,
    is_current          INTEGER NOT NULL DEFAULT 1,
    prompt_version      TEXT NOT NULL,       -- e.g., "deal_summary:0.2"
    model_confidence    TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_summary_txn     ON summary(transaction_id);
CREATE INDEX IF NOT EXISTS idx_summary_current ON summary(is_current);


-- -----------------------------------------------------------------------------
-- 7. rationale_tag — strategic rationale classification
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rationale_tag (
    rationale_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    primary_rationale       TEXT NOT NULL,
                            -- SCALE_CONSOLIDATION | GEOGRAPHIC_EXPANSION | PRODUCT_OR_TECH_CAPABILITY
                            -- | VERTICAL_INTEGRATION | MARKET_DIVERSIFICATION | TALENT_ACQUISITION
                            -- | FINANCIAL_OR_ARBITRAGE | OTHER
    secondary_rationales    TEXT,            -- JSON array of enum values
    supporting_excerpt_index INTEGER,
    model_confidence        TEXT,
    is_current              INTEGER NOT NULL DEFAULT 1,
    prompt_version          TEXT NOT NULL,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rationale_txn     ON rationale_tag(transaction_id);
CREATE INDEX IF NOT EXISTS idx_rationale_primary ON rationale_tag(primary_rationale);
CREATE INDEX IF NOT EXISTS idx_rationale_current ON rationale_tag(is_current);


-- -----------------------------------------------------------------------------
-- 8. prompt_version — registry of prompt versions used in production
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_version (
    prompt_name     TEXT NOT NULL,           -- e.g., "deal_type_classifier"
    version         TEXT NOT NULL,           -- e.g., "0.2"
    prompt_text_hash TEXT,                   -- SHA-256 of the prompt file content (for drift detection)
    first_used_at   TEXT NOT NULL,
    notes           TEXT,
    PRIMARY KEY (prompt_name, version)
);


-- -----------------------------------------------------------------------------
-- 9. run_log — one row per pipeline run for audit / resume
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_log (
    run_id          TEXT PRIMARY KEY,        -- e.g., "run_20260423_120000"
    mode            TEXT NOT NULL,           -- full | resume | scrape | extract | aggregate | generate | export | rerun-prompt
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    status          TEXT,                    -- RUNNING | COMPLETED | FAILED
    summary_json    TEXT,                    -- JSON with counts per stage
    error_message   TEXT
);


-- -----------------------------------------------------------------------------
-- 10. aggregation_conflict_log — records of LLM-resolved conflicts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS aggregation_conflict_log (
    conflict_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id          TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    field_name              TEXT NOT NULL,
    observations_json       TEXT NOT NULL,   -- JSON of all conflicting observations input to prompt
    chosen_observation_id   INTEGER,
    chosen_value            TEXT,
    aggregation_confidence  TEXT,
    conflict_severity       TEXT,            -- NONE | MINOR | MATERIAL | SEMANTIC
    flagged_for_review      INTEGER NOT NULL DEFAULT 0,
    reasoning               TEXT,
    prompt_version          TEXT,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_conflict_txn    ON aggregation_conflict_log(transaction_id);
CREATE INDEX IF NOT EXISTS idx_conflict_review ON aggregation_conflict_log(flagged_for_review);


-- -----------------------------------------------------------------------------
-- 11. extraction_failure_log — per-row prompt failure details
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS extraction_failure_log (
    failure_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_raw_id       INTEGER REFERENCES source_raw(source_raw_id),
    extraction_id       INTEGER REFERENCES staging_extraction(extraction_id),
    stage               TEXT NOT NULL,       -- name of the prompt stage that failed
    failure_type        TEXT,                -- PARSE_ERROR | API_ERROR | TIMEOUT | REFUSAL | SCHEMA_VIOLATION
    raw_response        TEXT,
    error_message       TEXT,
    prompt_version      TEXT,
    run_id              TEXT,
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_failure_source ON extraction_failure_log(source_raw_id);
CREATE INDEX IF NOT EXISTS idx_failure_stage  ON extraction_failure_log(stage);
CREATE INDEX IF NOT EXISTS idx_failure_run    ON extraction_failure_log(run_id);


-- -----------------------------------------------------------------------------
-- 12. gold_set — operator-labeled truth for evaluation
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_set (
    gold_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    source_raw_id           INTEGER NOT NULL REFERENCES source_raw(source_raw_id),
    gold_set_file           TEXT NOT NULL,   -- filename of the CSV this label came from
    labeled                 INTEGER NOT NULL DEFAULT 0,

    -- Gold-labeled fields (mirror staging_extraction structure)
    target_name             TEXT,
    acquirer_name           TEXT,
    parent_seller_name      TEXT,
    deal_type               TEXT,
    spin_split_type         TEXT,
    distribution_mechanism  TEXT,
    target_type             TEXT,
    target_status           TEXT,
    event_type              TEXT,
    value_amount            REAL,
    value_currency          TEXT,
    value_type              TEXT,
    announced_date          TEXT,
    closed_date             TEXT,
    consideration_type      TEXT,
    target_revenue          REAL,
    target_revenue_period   TEXT,
    target_ebitda           REAL,
    target_ebitda_period    TEXT,
    primary_rationale       TEXT,
    advisor_count           INTEGER,
    notes                   TEXT,
    created_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gold_source ON gold_set(source_raw_id);


-- =============================================================================
-- End of schema.
-- =============================================================================
