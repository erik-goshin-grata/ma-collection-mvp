-- =============================================================================
-- M&A Collection MVP — Migration 003: Funding Path
-- Applies to: staging_extraction, transaction_record
-- Adds tables and columns for the funding event extraction path:
--   funding_hc_extraction:0.1
--   funding_lc_extraction:0.1
-- All new columns are nullable — existing M&A rows are unaffected.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- staging_extraction — funding scalar fields (from funding_hc_extraction)
-- -----------------------------------------------------------------------------

-- Round classification
ALTER TABLE staging_extraction ADD COLUMN round_label TEXT;
-- "Series B", "Seed Extension", "Bridge Round" — as stated in source

ALTER TABLE staging_extraction ADD COLUMN round_stage_category TEXT;
-- PRE_SEED | SEED | EARLY_STAGE | GROWTH | LATE_STAGE
-- Derived by Stage 9 from round_label; not extracted by prompt

-- Round size and valuation
ALTER TABLE staging_extraction ADD COLUMN round_size REAL;
ALTER TABLE staging_extraction ADD COLUMN pre_money_valuation REAL;
ALTER TABLE staging_extraction ADD COLUMN post_money_valuation REAL;
ALTER TABLE staging_extraction ADD COLUMN valuation_currency TEXT;

-- Venture debt specific
ALTER TABLE staging_extraction ADD COLUMN facility_size REAL;
-- For VENTURE_DEBT: total debt facility size

-- Round metadata
ALTER TABLE staging_extraction ADD COLUMN total_raised_to_date REAL;
ALTER TABLE staging_extraction ADD COLUMN is_extension_round INTEGER DEFAULT 0;
ALTER TABLE staging_extraction ADD COLUMN is_down_round INTEGER DEFAULT 0;
ALTER TABLE staging_extraction ADD COLUMN is_bridge_round INTEGER DEFAULT 0;

-- -----------------------------------------------------------------------------
-- staging_extraction — funding LC fields (from funding_lc_extraction)
-- -----------------------------------------------------------------------------

ALTER TABLE staging_extraction ADD COLUMN use_of_proceeds TEXT;
-- Brief description of how proceeds will be used; null when not stated

ALTER TABLE staging_extraction ADD COLUMN has_board_seat INTEGER;
-- 0/1: investor taking board seat or observer right; null when not mentioned

ALTER TABLE staging_extraction ADD COLUMN board_seat_notes TEXT;
-- Description of board arrangement when has_board_seat = 1

-- Note: pct_acquired, regulatory_approvals_required already exist on
-- staging_extraction — reused for funding events, no new columns needed.

-- -----------------------------------------------------------------------------
-- staging_investor — investor array from funding HC extraction
-- Mirrors the advisor table pattern (one row per investor per extraction)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS staging_investor (
    investor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id INTEGER NOT NULL REFERENCES staging_extraction(extraction_id),
    name TEXT NOT NULL,
    domain TEXT,
    investor_type TEXT,
    -- vc_firm | growth_equity | corporate_vc | family_office | hedge_fund |
    -- sovereign_wealth_fund | angel | accelerator | lender | unknown
    is_lead INTEGER DEFAULT 0,         -- 0/1
    lead_investor_rank INTEGER,        -- 1 = lead, 2 = second, etc.
    investment_amount REAL,
    investment_currency TEXT,          -- ISO 4217
    is_new_investor INTEGER,           -- 0/1
    is_existing_investor INTEGER,      -- 0/1
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_staging_investor_extraction
    ON staging_investor(extraction_id);

-- -----------------------------------------------------------------------------
-- transaction_record — funding scalar fields (aggregated from staging)
-- -----------------------------------------------------------------------------

ALTER TABLE transaction_record ADD COLUMN round_label TEXT;
ALTER TABLE transaction_record ADD COLUMN round_stage_category TEXT;
ALTER TABLE transaction_record ADD COLUMN round_size REAL;
ALTER TABLE transaction_record ADD COLUMN pre_money_valuation REAL;
ALTER TABLE transaction_record ADD COLUMN post_money_valuation REAL;
ALTER TABLE transaction_record ADD COLUMN valuation_currency TEXT;
ALTER TABLE transaction_record ADD COLUMN facility_size REAL;
ALTER TABLE transaction_record ADD COLUMN total_raised_to_date REAL;
ALTER TABLE transaction_record ADD COLUMN is_extension_round INTEGER DEFAULT 0;
ALTER TABLE transaction_record ADD COLUMN is_down_round INTEGER DEFAULT 0;
ALTER TABLE transaction_record ADD COLUMN is_bridge_round INTEGER DEFAULT 0;
ALTER TABLE transaction_record ADD COLUMN use_of_proceeds TEXT;
ALTER TABLE transaction_record ADD COLUMN has_board_seat INTEGER;
ALTER TABLE transaction_record ADD COLUMN board_seat_notes TEXT;

-- -----------------------------------------------------------------------------
-- Indexes
-- -----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_staging_v2_round_label
    ON staging_extraction(round_label);

CREATE INDEX IF NOT EXISTS idx_staging_round_stage
    ON staging_extraction(round_stage_category);

CREATE INDEX IF NOT EXISTS idx_transaction_round_stage
    ON transaction_record(round_stage_category);

CREATE INDEX IF NOT EXISTS idx_transaction_round_label
    ON transaction_record(round_label);

-- =============================================================================
-- aggregate.py _FIELDS additions (not SQL — for reference):
--
-- Add to _FIELDS list in stages/aggregate.py:
--   ("round_label", "string"),
--   ("round_stage_category", "string"),
--   ("round_size", "number"),
--   ("pre_money_valuation", "number"),
--   ("post_money_valuation", "number"),
--   ("valuation_currency", "string"),
--   ("facility_size", "number"),
--   ("total_raised_to_date", "number"),
--   ("is_extension_round", "boolean"),
--   ("is_down_round", "boolean"),
--   ("is_bridge_round", "boolean"),
--   ("use_of_proceeds", "string"),
--   ("has_board_seat", "boolean"),
--   ("board_seat_notes", "string"),
--
-- round_stage_category is derived by Stage 9 from round_label, not aggregated
-- directly. Add to _derive_flags() equivalent for funding events.
--
-- _compute_multiples() should skip when v2_event_type IN
-- (VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT).
--
-- V2 promotion path:
--   staging_investor rows → transaction_party rows (role=INVESTOR)
--   round_size → financial_metric (metric_type=ROUND_SIZE)
--   pre_money_valuation → financial_metric (metric_type=PRE_MONEY_VALUATION)
--   post_money_valuation → financial_metric (metric_type=POST_MONEY_VALUATION)
--   facility_size → financial_metric (metric_type=FACILITY_SIZE)
-- =============================================================================
