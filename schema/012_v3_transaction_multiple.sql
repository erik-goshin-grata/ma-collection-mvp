-- 012 — V3 §6: multiples as normalized rows.
--
-- The reference implementation records multiples as four fixed columns on
-- transaction_record -- ev_to_revenue_ltm, ev_to_revenue_ntm, ev_to_ebitda_ltm,
-- ev_to_ebitda_ntm -- plus one multiple_quality shared across all four. The canonical
-- model (V3 §6; V2 §F1) records them as rows. Three things the columns cannot express:
--
--   1. A multiple the SOURCE stated. Every one of the four columns is computed by
--      stages/aggregate.py::_compute_multiples from implied_enterprise_value. There is
--      no as-reported concept at any layer -- no column, no prompt field, no
--      observation. A source stating "approximately 11.5x anticipated 2026 adjusted
--      EBITDA" is captured nowhere at all, and the figure is lost.
--   2. Five of the seven multiple types. EV_EBIT, EV_FCF, PE, PB and PTBV have no column.
--   3. A period basis other than LTM or NTM. ANNUAL exists today only as an eligibility
--      route INTO the ltm slot, never as a stored basis; QUARTERLY cannot be said at all.
--
-- This migration adds the table and nothing else. It is deliberately unwired: no stage
-- writes it yet, the four flat columns keep being computed and exported exactly as
-- before, and no reader changes. Wiring arrives in the two commits after this one.
--
-- AS-REPORTED ROWS DO NOT NEED A DENOMINATOR. `denominator_financial_id` is, in the
-- canonical model's own words, "Expected on calculated rows" (V3 §6) / "Expected for
-- calculated rows" (V2 §F1) -- a scoped expectation whose scope excludes as-reported
-- rows, not a NOT NULL constraint. A source that states a multiple and no EBITDA gives
-- us nothing to link, and V2 §F's rule is "Preserve both as-reported and calculated
-- rows". So the column is nullable, and an as-reported row with no denominator is a
-- complete row, not a defective one. Nothing here back-solves the missing denominator:
-- purchase price divided by a stated multiple is arithmetic, not extraction.
--
-- `numerator_value_type` NAMES A FAMILY, IT IS NOT A POINTER. It says which canonical
-- value would be the numerator, so a stake-level figure can never silently become one.
-- It is populated on an as-reported row whose transaction has no computed
-- implied_enterprise_value, because the source's claim is about the EV family whether
-- or not we can independently compute the EV.
--
-- `quality` IS NULL ON AS-REPORTED ROWS. Its three values -- CALCULATED, NM,
-- NOT_CALCULABLE -- all describe the outcome of a calculation, and V3 marks the field
-- Population = Derived. Nothing is derived on an as-reported row, so there is nothing
-- for the field to say. NOT_CALCULABLE would be actively wrong: it reads as "we tried
-- and failed" about a row that is fully populated from the source.
--
-- WHAT THIS TABLE CANNOT SAY. Two source-reported variants of the same multiple_type
-- and period -- Maverick's headline 11.5x and its tax-adjusted 10.5x -- are separable
-- here only by multiple_id and by their differing values. No field records that one is
-- the tax-adjusted variant. This is a gap in the canonical model, not an omission in
-- this migration: V2 §F1's 13 fields carry no qualifier, V3 §6 states it "Added nothing
-- structural", and §E4 rule 6's per-fact provenance ("source attribution plus a fact
-- key") is scoped by its own words to every row in `financial_metric` -- the multiple
-- model has no fact key. Recorded, deliberately not invented here.
--
-- Sentinel-guarded and hand-registered in db.py::_apply_migrations, like every
-- migration in this directory. This directory is NOT globbed: a migration without a
-- block in db.py never runs.

CREATE TABLE IF NOT EXISTS transaction_multiple (
    multiple_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT,              -- nullable until Stage 8 cluster backfill
    multiple_type               TEXT NOT NULL,
                                -- EV_REVENUE | EV_EBITDA | EV_EBIT | EV_FCF | PE | PB | PTBV
    multiple_value              REAL,
    period_basis                TEXT,              -- LTM | NTM | ANNUAL | QUARTERLY
    period_end_date             TEXT,              -- YYYY-MM-DD or YYYY
    period_end_date_precision   TEXT,              -- DAY | MONTH | QUARTER | YEAR
    numerator_value_type        TEXT,
                                -- implied_enterprise_value | implied_equity_value.
                                -- The canonical family, never a stake-level figure.
    denominator_financial_id    INTEGER,           -- expected on calculated rows; NULL on as-reported
    source_flag                 TEXT NOT NULL,     -- as_reported | calculated
    quality                     TEXT,              -- CALCULATED | NM | NOT_CALCULABLE; NULL when as_reported
    multiple_as_reported        TEXT,              -- the source's verbatim wording, e.g. "approximately 11.5x"
    staging_extraction_id       INTEGER,           -- FK to staging_extraction for collected rows
    source_raw_id               INTEGER,           -- FK to source_raw for collected rows
    extraction_prompt_version   TEXT,
    is_current                  INTEGER NOT NULL DEFAULT 1,
    extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (staging_extraction_id) REFERENCES staging_extraction(extraction_id),
    FOREIGN KEY (source_raw_id) REFERENCES source_raw(source_raw_id)
);

CREATE INDEX IF NOT EXISTS idx_multiple_transaction ON transaction_multiple(transaction_id);
CREATE INDEX IF NOT EXISTS idx_multiple_type        ON transaction_multiple(multiple_type);
CREATE INDEX IF NOT EXISTS idx_multiple_source_flag ON transaction_multiple(source_flag);
CREATE INDEX IF NOT EXISTS idx_multiple_current     ON transaction_multiple(is_current);
