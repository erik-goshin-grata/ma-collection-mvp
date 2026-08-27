-- 014 — V3 §5 / V2 §9: source-stated financial metrics as normalized rows.
--
-- Revenue and EBITDA are held today as flat columns on transaction_record --
-- target_revenue, its period type and period end, target_ebitda and the same three,
-- and ONE financials_currency shared between both. The canonical model records each
-- financial fact as a row carrying everything needed to interpret that fact on its own.
--
-- Two things the flat columns cannot express:
--
--   1. A currency per figure. The metric-row policy's first rule is that "currency
--      attaches to the value it qualifies; a row never inherits currency from another
--      row, the transaction, or the source's other figures." One shared column cannot
--      say that revenue was stated in USD and EBITDA in EUR; today that disagreement
--      collapses to NULL and BOTH figures lose a currency each of them had.
--   2. The precision of a period end. "2025" and "2025-12-31" are stored in one TEXT
--      column and are told apart only by looking at the string.
--
-- WHAT THIS TABLE HOLDS: canonical resolved facts, following R3.3. A row is written
-- from the value reconciliation already chose, never from a staging read, and an
-- unresolved conflict produces NO row. This is not a second observation store -- the
-- observations remain in transaction_field_observation and the disagreements in
-- aggregation_conflict_log.
--
-- SOURCE-STATED ONLY IN THIS SLICE. is_calculated is 0 on every row written today,
-- because revenue and EBITDA are collected, never computed. The column exists because
-- the canonical model requires it and because calculated rows are a later question;
-- nothing here invents a calculation to fill the table.
--
-- REVENUE AND EBITDA ONLY. Balance-sheet metrics -- TOTAL_DEBT, CASH_AND_EQUIVALENTS,
-- NET_DEBT -- are canonical metric types and are deliberately NOT written yet, pending
-- the two open balance-sheet findings. Product ruling recorded here for when they are:
-- their reporting-period context is Q or A, with the point-in-time date carried by the
-- balance-sheet as-of date. They must NOT be mapped into LTM / NTM / INTERIM_YTD
-- financial-period semantics -- a balance sheet covers no period.
--
--   A GAP, RECORDED NOT ENCODED: the canonical row has one period_type, which for a
--   balance-sheet metric is POINT_IN_TIME, and no second field for the Q or A
--   reporting context. V3 §5 maps the as-of date onto the row's period end and states
--   a balance sheet "is never labelled LTM, NTM, annual or quarterly"; V2's redline
--   names recording the filing's period label as the specific trap, since it describes
--   where a figure was found rather than what it measures. So Q/A has nowhere to go in
--   the canonical row as defined. It is reported rather than written into period_type,
--   which would be the mislabelling both documents forbid.
--
-- FX FIELDS STAY NULL, AND THAT IS CORRECT. fx_rate and fx_rate_date "record a
-- conversion performed; they never license one". No FX path exists here, so there is
-- no conversion to record. Populating them would be the invention the rule forbids.
--
-- Sentinel-guarded and hand-registered in db.py::_apply_migrations. This directory is
-- NOT globbed: a migration without a block in db.py never runs.

CREATE TABLE IF NOT EXISTS transaction_financial (
    financial_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT,
    metric_type                 TEXT NOT NULL,
                                -- REVENUE | EBITDA today. The canonical vocabulary is
                                -- wider (EBIT, NET_INCOME, ARR, MRR, FREE_CASH_FLOW,
                                -- GROSS_PROFIT, SHAREHOLDERS_EQUITY, TOTAL_DEBT,
                                -- CASH_AND_EQUIVALENTS, NET_DEBT ...); this
                                -- implementation authors none of the others, and a
                                -- metric it does not collect is a coverage gap, not a
                                -- reason to write an empty row.
    value_captured              REAL,
    value_currency              TEXT,              -- ISO 4217, belonging to THIS row's value
    period_type                 TEXT,
                                -- ANNUAL | LTM | NTM | QUARTERLY | INTERIM_YTD
                                -- | POINT_IN_TIME (balance-sheet types, not written yet)
    period_end_date             TEXT,              -- YYYY-MM-DD or YYYY, exactly as stated
    period_end_date_precision   TEXT,              -- exact | month | quarter | year
    fx_rate                     REAL,              -- a conversion performed, never one invented
    fx_rate_date                TEXT,
    margin_pct                  REAL,              -- canonical, unauthored here
    is_calculated               INTEGER NOT NULL DEFAULT 0,
    staging_extraction_id       INTEGER,
    source_raw_id               INTEGER,
    extraction_prompt_version   TEXT,
    is_current                  INTEGER NOT NULL DEFAULT 1,
    extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (staging_extraction_id) REFERENCES staging_extraction(extraction_id),
    FOREIGN KEY (source_raw_id) REFERENCES source_raw(source_raw_id)
);

CREATE INDEX IF NOT EXISTS idx_financial_transaction ON transaction_financial(transaction_id);
CREATE INDEX IF NOT EXISTS idx_financial_metric_type ON transaction_financial(metric_type);
CREATE INDEX IF NOT EXISTS idx_financial_current     ON transaction_financial(is_current);

-- One precision vocabulary, not two. 012 shipped transaction_multiple with
-- DAY | MONTH | QUARTER | YEAR; the canonical financial-metric vocabulary is
-- exact | month | quarter | year, and DAY has no canonical counterpart. Normalizing
-- here rather than leaving two vocabularies standing: this is spelling, not a new
-- semantic dimension, and no row changes what it means.
UPDATE transaction_multiple SET period_end_date_precision = 'exact'
    WHERE period_end_date_precision = 'DAY';
UPDATE transaction_multiple SET period_end_date_precision = 'month'
    WHERE period_end_date_precision = 'MONTH';
UPDATE transaction_multiple SET period_end_date_precision = 'quarter'
    WHERE period_end_date_precision = 'QUARTER';
UPDATE transaction_multiple SET period_end_date_precision = 'year'
    WHERE period_end_date_precision = 'YEAR';
