-- =============================================================================
-- M&A Collection MVP — Migration 002: V2 Prompt Alignment
-- Applies to: staging_extraction, transaction_record
-- Adds columns for new fields introduced in prompt versions:
--   deal_type_classifier 0.6
--   high_confidence_extraction 0.12
-- All new columns are nullable — existing rows are unaffected.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- staging_extraction — new columns from classifier v0.6
-- -----------------------------------------------------------------------------

-- v2_event_type: V2 EventType enum (ACQUISITION, MERGER, SPIN_OFF, SPLIT_OFF,
-- REVERSE_MERGER, JOINT_VENTURE, MINORITY_INVESTMENT, RECAPITALIZATION,
-- VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT, UNKNOWN)
-- Mirrors deal_type during migration; deal_type retained for backward compat.
ALTER TABLE staging_extraction ADD COLUMN v2_event_type TEXT;

-- event_history_type: replaces event_type for source observation classification.
-- ANNOUNCED | CLOSED | AMENDED | TERMINATED
-- Mirrors event_type during migration; event_type retained for backward compat.
ALTER TABLE staging_extraction ADD COLUMN event_history_type TEXT;

-- recap_type: discriminator when v2_event_type = RECAPITALIZATION
-- DIVIDEND | EQUITY | LEVERAGED | SPONSOR_RECAP | null
ALTER TABLE staging_extraction ADD COLUMN recap_type TEXT;

-- target_type_v2: lowercase V2 target_type values
-- standalone_company | subsidiary | business_unit | assets | spinco
ALTER TABLE staging_extraction ADD COLUMN target_type_v2 TEXT;

-- spin_split_type_v2: SPIN_OFF | SPLIT_OFF (replaces SPIN_OFF | SPLIT)
ALTER TABLE staging_extraction ADD COLUMN spin_split_type_v2 TEXT;

-- -----------------------------------------------------------------------------
-- staging_extraction — new columns from high_confidence_extraction v0.12
-- -----------------------------------------------------------------------------

ALTER TABLE staging_extraction ADD COLUMN announced_date_precision TEXT;
-- exact | month | quarter | year

ALTER TABLE staging_extraction ADD COLUMN closed_date_precision TEXT;

ALTER TABLE staging_extraction ADD COLUMN signing_date_precision TEXT;

-- rumor_date: date of first media report when deal was rumored before announcement
ALTER TABLE staging_extraction ADD COLUMN rumor_date TEXT;

-- financials_disclosure_status: DISCLOSED | UNDISCLOSED | UNKNOWN
ALTER TABLE staging_extraction ADD COLUMN financials_disclosure_status TEXT;

-- acquirer_type_v2: lowercase V2 acquirer_type vocabulary
-- strategic_corporate | private_equity | pe_portfolio | venture_capital |
-- growth_equity | sovereign_wealth_fund | pension_fund | hedge_fund |
-- family_office | individual | management | employee_group | spac |
-- consortium | other_financial_sponsor | unknown
ALTER TABLE staging_extraction ADD COLUMN acquirer_type_v2 TEXT;

-- V2 period_type columns: LTM | NTM | ANNUAL | QUARTERLY | INTERIM_YTD | null
-- Replaces legacy values (FY | TTM | CY | QUARTER | UNKNOWN)
ALTER TABLE staging_extraction ADD COLUMN target_revenue_period_type_v2 TEXT;
ALTER TABLE staging_extraction ADD COLUMN target_ebitda_period_type_v2 TEXT;

-- -----------------------------------------------------------------------------
-- transaction_record — parallel V2 columns
-- -----------------------------------------------------------------------------

ALTER TABLE transaction_record ADD COLUMN v2_event_type TEXT;
ALTER TABLE transaction_record ADD COLUMN event_history_type TEXT;
ALTER TABLE transaction_record ADD COLUMN recap_type TEXT;
ALTER TABLE transaction_record ADD COLUMN target_type_v2 TEXT;
ALTER TABLE transaction_record ADD COLUMN spin_split_type_v2 TEXT;
ALTER TABLE transaction_record ADD COLUMN acquirer_type_v2 TEXT;
ALTER TABLE transaction_record ADD COLUMN target_revenue_period_type_v2 TEXT;
ALTER TABLE transaction_record ADD COLUMN target_ebitda_period_type_v2 TEXT;
ALTER TABLE transaction_record ADD COLUMN financials_disclosure_status TEXT;
ALTER TABLE transaction_record ADD COLUMN rumor_date TEXT;
ALTER TABLE transaction_record ADD COLUMN announced_date_precision TEXT;
ALTER TABLE transaction_record ADD COLUMN closed_date_precision TEXT;

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_staging_v2_event_type
    ON staging_extraction(v2_event_type);

CREATE INDEX IF NOT EXISTS idx_staging_financials_disclosure
    ON staging_extraction(financials_disclosure_status);

CREATE INDEX IF NOT EXISTS idx_transaction_v2_event_type
    ON transaction_record(v2_event_type);

-- =============================================================================
-- Migration notes:
--
-- 1. Legacy columns are RETAINED. New _v2 columns are populated by v0.6+
--    classifier and v0.12+ HC extraction. Aggregation reads _v2 columns when
--    non-null, falls back to legacy. Allows mixed-version runs during migration.
--
-- 2. consideration_type already exists on staging_extraction. No change needed.
--
-- 3. Apply: sqlite3 <db_path> < schema/002_v2_prompt_alignment.sql
--    SQLite will error if column already exists. Use a migration runner that
--    checks column existence (e.g. scripts/apply_migration.py).
-- =============================================================================
