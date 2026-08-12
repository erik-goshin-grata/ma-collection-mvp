-- =============================================================================
-- M&A Collection MVP — Initial Schema
-- Version: 1.0 (reflects Drop 3.32a — organization participant schema)
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
    target_type                 TEXT,        -- STANDALONE_COMPANY | BUSINESS_UNIT | SUBSIDIARY | ASSETS | null
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
    target_description          TEXT,
    acquirer_description        TEXT,
    acquirer_sponsor_name       TEXT,        -- PE sponsor(s) backing the acquirer; comma-delimited when multiple. NULL when acquirer_type is not PE_PORTFOLIO or PRIVATE_EQUITY, or when sponsor not stated in source.
    parent_seller_description   TEXT,
    announced_date              TEXT,        -- ISO 8601
    closed_date                 TEXT,        -- ISO 8601
    signing_date                TEXT,        -- ISO 8601
    value_amount                REAL,
    value_currency              TEXT,        -- ISO 4217
    value_type                  TEXT,        -- EQUITY_VALUE | TRANSACTION_VALUE | ENTERPRISE_VALUE | UNDISCLOSED
    value_type_confidence       TEXT,        -- HIGH | MEDIUM | LOW
    value_qualifier             TEXT,
    per_share_price             REAL,
    pct_acquired                REAL,        -- e.g. 51.0 for 51% stake; NULL when implicit 100% or not stated
    stake_transition_type       TEXT,        -- nullable explicit ownership transition enum; current harness only
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

    -- Multi-transaction tracking
    multi_transaction_index     INTEGER DEFAULT 0,  -- 0-indexed position when one PR yields multiple transactions; 0 for single-transaction PRs
    multi_transaction_total     INTEGER DEFAULT 1,  -- total number of transactions extracted from this source_raw; 1 for single-transaction PRs

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
    transaction_status          TEXT,              -- PENDING | CLOSED | TERMINATED | RUMORED | UNKNOWN. Derived from event_type + closed_date.
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
    target_description          TEXT,
    acquirer_description        TEXT,
    acquirer_sponsor_name       TEXT,        -- PE sponsor(s) backing the acquirer; comma-delimited when multiple. NULL when acquirer_type is not PE_PORTFOLIO or PRIVATE_EQUITY, or when sponsor not stated in source.
    parent_seller_description   TEXT,

    -- Dates
    announced_date              TEXT,
    closed_date                 TEXT,
    signing_date                TEXT,

    -- Value
    value_amount                REAL,
    value_currency              TEXT,
    value_type                  TEXT,
    per_share_price             REAL,
    pct_acquired                REAL,        -- e.g. 51.0 for 51% stake; NULL when implicit 100% or not stated
    stake_transition_type       TEXT,        -- nullable explicit ownership transition enum; current harness only

    -- Target financials
    target_revenue              REAL,
    target_revenue_period_type  TEXT,
    target_revenue_period_end   TEXT,
    target_ebitda               REAL,
    target_ebitda_period_type   TEXT,
    target_ebitda_period_end    TEXT,
    financials_currency         TEXT,

    -- Valuation multiples (derived; populated when source financials and value_type permit)
    ev_to_revenue_ltm           REAL,        -- enterprise_value / target_revenue (when period_type = LTM/TTM)
    ev_to_revenue_ntm           REAL,        -- enterprise_value / target_revenue (when period_type = NTM)
    ev_to_ebitda_ltm            REAL,        -- enterprise_value / target_ebitda (when period_type = LTM/TTM)
    ev_to_ebitda_ntm            REAL,        -- enterprise_value / target_ebitda (when period_type = NTM)
    multiple_quality            TEXT,        -- CALCULATED | NM | NOT_CALCULABLE

    -- Derived valuations (finding #8; computed in aggregate from captured primitives)
    net_debt                    REAL,        -- manual collection input in the interim (NOT extracted); preserved across re-aggregation
    equity_value                REAL,        -- canonical equity value
    equity_value_basis          TEXT,        -- STATED | PER_SHARE_X_SHARES
    implied_equity_value        REAL,        -- equity grossed up to 100% (equity / (pct_acquired/100))
    enterprise_value            REAL,        -- canonical enterprise value
    enterprise_value_basis      TEXT,        -- STATED | EQUITY_PLUS_NET_DEBT
    investment_amount           REAL,        -- funding/minority check (round size); kept distinct from equity (bug #8)
    deal_value_currency         TEXT,        -- currency of the derived value fields (§4.7); null on a valuation/value mismatch
    total_debt                  REAL,        -- total debt, NOT net of cash. Manual interim input (extracted metric later); preserved across re-aggregation; input to transaction_value at pct_acquired>=50; net_debt is a different field (feeds implied_enterprise_value)
    transaction_value           REAL,        -- Tier-1 as-transacted value (§2.1.1); as-reported, else equity (+ total debt at pct>=50)
    transaction_value_basis     TEXT,        -- STATED | EQUITY_BELOW_CONTROL | EQUITY_PLUS_TOTAL_DEBT
    pct_acquired_source         TEXT,        -- §2.6: stated | assumed (100% default for control types when silent)

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
    is_take_private             INTEGER,     -- public standalone target acquired into private/non-public ownership; excludes public-acquirer mergers
    is_minority                 INTEGER DEFAULT 0,  -- 1 when buyer/investor remains below control after current transaction
    is_add_on                   INTEGER,     -- acquirer_type=PE_PORTFOLIO
    is_divestiture              INTEGER,     -- target_type in (BUSINESS_UNIT, SUBSIDIARY, ASSETS)
    is_de_spac                  INTEGER DEFAULT 0,  -- 1 when deal_type=REVERSE_MERGER AND acquirer_type=SPAC
    has_earnout                 INTEGER DEFAULT 0,  -- 1 when consideration_components contains a EARNOUT-form entry
    has_cvr                     INTEGER DEFAULT 0,  -- 1 when consideration_components contains a CVR-form entry
    linked_filings_count        INTEGER DEFAULT 0,  -- count of transaction_document rows for this transaction; set by sec_documents stage

    -- Agreement extraction fields (populated by Stage 11, agreement_extract)
    acquirer_merger_sub_name    TEXT,              -- name of acquisition vehicle / Merger Sub in 3-party structure
    merger_structure            TEXT,              -- DIRECT | FORWARD_TRIANGULAR | REVERSE_TRIANGULAR | TENDER_OFFER | UNKNOWN
    has_mac_clause              INTEGER DEFAULT 0,
    requires_target_shareholder_vote INTEGER,      -- 0 | 1 | null when not yet known
    target_vote_threshold       TEXT,              -- MAJORITY_OUTSTANDING | TWO_THIRDS | MAJORITY_VOTING | OTHER | null
    closing_conditions_summary  TEXT,              -- brief text of top conditions from CONDITIONS_TO_CLOSING section
    target_total_diluted_shares INTEGER,           -- sum of all security types from transaction_security; set by agreement_extract
    fully_diluted_calc_quality  TEXT,              -- COMPLETE | PARTIAL | NOT_AVAILABLE
    agreement_extraction_status TEXT,              -- NOT_TRIGGERED | EXTRACTED | NO_AGREEMENT_LINKED | EXTRACTION_FAILED

    -- Cross-source observation diff fields (populated by Stage 11, Drop 3.20b)
    has_observation_changes         INTEGER DEFAULT 0,  -- 1 when any tracked field has >1 distinct value across sources
    observation_changes_field_count INTEGER DEFAULT 0,  -- count of fields with diffs
    observation_changes_summary     TEXT,               -- JSON array of per-field diff entries; see transaction_field_observation

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
-- 5a. entity — organization master records for transaction participants
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 5b. entity_alias — source-supported organization aliases
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 5c. transaction_participant_group — consortium / investor / seller groupings
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 5d. transaction_participant — organization participants by transaction
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 13. transaction_document — full-text SEC filings linked to transactions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_document (
    document_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id        TEXT NOT NULL,                  -- FK to transaction_record.transaction_id
    filing_type           TEXT NOT NULL,
                          -- 8K_ITEM_201 | 8K_EXHIBIT_21 | DEFM14A | SC_TOT | S4 | OTHER
    sec_accession_number  TEXT,                           -- e.g. "0001193125-26-123456"
    sec_filing_url        TEXT,                           -- canonical SEC URL
    filer_cik             TEXT,
    filer_name            TEXT,
    filing_date           TEXT,                           -- YYYY-MM-DD
    document_title        TEXT,                           -- document's self-declared title extracted from first page
    raw_text              TEXT,                           -- full extracted text content
    raw_text_length       INTEGER,                        -- char count
    fetch_timestamp       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_current            INTEGER DEFAULT 1,
    UNIQUE (transaction_id, sec_accession_number, filing_type)
);

CREATE INDEX IF NOT EXISTS idx_transaction_document_txn  ON transaction_document(transaction_id);
CREATE INDEX IF NOT EXISTS idx_transaction_document_type ON transaction_document(filing_type);


-- -----------------------------------------------------------------------------
-- 14. transaction_document_section — heuristic section excerpts
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_document_section (
    section_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id           INTEGER NOT NULL REFERENCES transaction_document(document_id),
    section_type          TEXT NOT NULL,
                          -- DEFINITIONS | RECITALS | CONSIDERATION | CAPITALIZATION |
                          -- CONDITIONS_TO_CLOSING | TERMINATION_FEES | REPRESENTATIONS |
                          -- BACKGROUND_OF_MERGER | FAIRNESS_OPINION | OTHER
    heading_text          TEXT,                           -- actual heading found in document
    excerpt_text          TEXT,                           -- bounded section content
    excerpt_start_offset  INTEGER,                        -- char offset into raw_text
    excerpt_end_offset    INTEGER,
    confidence            TEXT                            -- HIGH | MEDIUM | LOW
);

CREATE INDEX IF NOT EXISTS idx_section_document ON transaction_document_section(document_id);
CREATE INDEX IF NOT EXISTS idx_section_type     ON transaction_document_section(section_type);


-- -----------------------------------------------------------------------------
-- 15. transaction_security — per-class outstanding securities from deal documents
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_security (
    security_id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id                TEXT NOT NULL,
    security_type                 TEXT NOT NULL,
                                  -- COMMON_STOCK | PREFERRED_STOCK | OPTIONS | RSU | PSU
                                  -- | DSU | SAR | WARRANT | CONVERTIBLE_NOTE | OTHER
    security_type_as_reported     TEXT,              -- verbatim from source
    security_class                TEXT,              -- "Class A", "Series C", null for single-class
    shares_outstanding            INTEGER,
    shares_outstanding_as_of      TEXT,              -- YYYY-MM-DD
    weighted_avg_strike_price     REAL,              -- for options/SARs/warrants
    consideration_treatment       TEXT,              -- CASH_OUT | CONVERSION | ASSUMED | CANCELLED | ROLLOVER | OTHER
    consideration_per_share       REAL,
    consideration_currency        TEXT,
    notes                         TEXT,
    is_current                    INTEGER DEFAULT 1,
    extraction_source_section_id  INTEGER,           -- FK to transaction_document_section
    extraction_source_document_id INTEGER,           -- FK to transaction_document (denormalized)
    extracted_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (transaction_id) REFERENCES transaction_record(transaction_id),
    FOREIGN KEY (extraction_source_section_id) REFERENCES transaction_document_section(section_id),
    FOREIGN KEY (extraction_source_document_id) REFERENCES transaction_document(document_id)
);

CREATE INDEX IF NOT EXISTS idx_security_transaction ON transaction_security(transaction_id);
CREATE INDEX IF NOT EXISTS idx_security_type        ON transaction_security(security_type);


-- -----------------------------------------------------------------------------
-- 16. transaction_field_observation — one row per (transaction, field, source)
-- -----------------------------------------------------------------------------
-- Each row captures one source document's claim about one field value.
-- Multiple rows per (transaction_id, field_name) when multiple source documents
-- disclose the same field.  Diff surfacing queries these rows to detect changes
-- (e.g., per_share_price bumped from $42 to $44.50 in a DEFA14A).
-- field_name conventions:
--   Primary scalars   : match transaction_record column names (e.g., "per_share_price")
--   Share counts      : "shares_outstanding.{security_type}[.{security_class}]"
--   Consideration     : "consideration.{form}.{attr}"  (e.g., "consideration.CASH.per_share_amount")
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_field_observation (
    observation_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT,                    -- nullable until Stage 8 cluster backfill
    field_name                  TEXT NOT NULL,
    field_value                 TEXT,                    -- stringified (cross-type compatible)
    field_value_numeric         REAL,                    -- populated for numeric values
    source_document_id          INTEGER,                 -- FK to transaction_document; NULL for source_raw observations
    source_section_id           INTEGER,                 -- FK to transaction_document_section (nullable)
    staging_extraction_id       INTEGER,                 -- FK to staging_extraction for Stage 3/4/7 observations
    source_raw_id               INTEGER,                 -- FK to source_raw for Stage 3/4/7 observations
    source_type                 TEXT,                    -- denormalized source_raw.source_type or filing type
    source_tier                 TEXT,                    -- denormalized source_raw.source_tier
    model_confidence            TEXT,                    -- observation-level model confidence
    source_published_date       TEXT,                    -- source_raw.published_date for source-row observations
    filing_type                 TEXT,                    -- transaction_document.filing_type or SEC source type
    agreement_dated_as_of       TEXT,                    -- deterministic agreement date when available
    observed_as_of_date         TEXT,                    -- "as of" date claimed by source (e.g. cap-table as-of)
    filing_date                 TEXT,                    -- date document was filed with SEC
    extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    extraction_prompt_version   TEXT,
    observation_source_stage    TEXT,                    -- DT_CLASSIFY | HC_EXTRACT | LC_EXTRACT | AGREEMENT_EXTRACT | BACKFILL
    is_current                  INTEGER DEFAULT 1,
    FOREIGN KEY (source_document_id) REFERENCES transaction_document(document_id),
    FOREIGN KEY (source_section_id) REFERENCES transaction_document_section(section_id),
    FOREIGN KEY (staging_extraction_id) REFERENCES staging_extraction(extraction_id),
    FOREIGN KEY (source_raw_id) REFERENCES source_raw(source_raw_id)
);

CREATE INDEX IF NOT EXISTS idx_observation_txn_field ON transaction_field_observation(transaction_id, field_name);
CREATE INDEX IF NOT EXISTS idx_observation_filing_date ON transaction_field_observation(filing_date);


-- =============================================================================
-- End of schema.
-- =============================================================================
