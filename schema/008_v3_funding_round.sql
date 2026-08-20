-- 008_v3_funding_round.sql
-- V3 slice S-E — Funding Round + Stage + Price Direction (inventory §T14, §A6.3).
--
-- Three distinct concepts, previously two:
--
--   round_label            verbatim source wording          UNCHANGED, still extracted
--   round                  canonical normalized round       NEW, derived from round_label
--   vc_stage               broad grouping                   NEW NAME, derived from `round`
--   round_price_direction  UP | DOWN | FLAT | null          NEW, extracted
--
-- `round` and `vc_stage` are DERIVED in Stage 9 and are not observed, which is the same
-- shape the V2 round_stage_category used. They are deliberately absent from FUNDING_FIELDS:
-- creating observations for them merely to satisfy the generic extracted-field gate would
-- assert a path that does not exist by design.
--
-- The V2 derivation substring-matched free text and carried four established defects:
-- Series H and beyond returned null (the branch enumerated "series d".."series g"
-- literally), "Series AA" collided into EARLY_STAGE because "series a" is a substring of
-- it, "Bridge Round" returned null with no signal, and the whole thing read round_label
-- rather than a canonical value. Normalization is now anchored parsing over a bounded
-- generative shape: PRE_SEED, SEED, ANGEL, SERIES_<letter>, SERIES_<letter><int>.
-- Anything outside that shape yields NULL rather than a guess, and round_label keeps the
-- original wording either way.
--
-- round_price_direction replaces is_down_round, which could only ever record DOWN --
-- is_up_round never existed anywhere in the codebase -- so 0 fused up, flat and unknown
-- into one bit. NULL stays distinct from FLAT: "not stated" and "unchanged" differ.
--
-- The legacy round_stage_category and is_down_round columns are RETAINED and simply stop
-- being written. Both were removed from the Stage 9 write rather than left owned: leaving
-- them owned with no source field would write NULL over stored values on every
-- re-aggregation, destroying legacy data. No migration or backfill is performed here.
--
-- Enums are application-layer, not CHECK constraints, matching the rest of this schema.

ALTER TABLE staging_extraction ADD COLUMN round_price_direction TEXT;  -- UP|DOWN|FLAT|null

ALTER TABLE transaction_record ADD COLUMN round TEXT;                  -- canonical, derived
ALTER TABLE transaction_record ADD COLUMN vc_stage TEXT;               -- derived from round
ALTER TABLE transaction_record ADD COLUMN round_price_direction TEXT;  -- UP|DOWN|FLAT|null

CREATE INDEX IF NOT EXISTS idx_transaction_round    ON transaction_record(round);
CREATE INDEX IF NOT EXISTS idx_transaction_vc_stage ON transaction_record(vc_stage);
