-- 004_v3_attitude_approach.sql
-- V3 slice S-A — Attitude + Approach (inventory §T11).
--
-- Replaces the fused `hostile` boolean with two independent nullable dimensions.
-- `hostile` conflated three distinct facts (board posture, how the approach arrived,
-- and proxy contest) into one bit, and Stage 7 wrote `1 if flags.get("hostile") else 0`,
-- so "the source said nothing" and "the source said friendly" both stored 0.
--
-- NULL is meaningful and load-bearing for both new columns: the source did not
-- establish the fact. Absence of hostile evidence is NOT FRIENDLY.
--
-- The legacy `hostile` columns are deliberately RETAINED and simply stop being written
-- by new extractions. Existing rows keep their values. Historical migration/backfill is
-- out of scope for this slice: V2 `hostile = 0` must NOT be read as FRIENDLY, so a
-- mechanical backfill would manufacture facts that were never established.
--
-- Enums are application-layer (stages/low_confidence_extract.py), not CHECK constraints,
-- matching every other typed dimension in this schema.

ALTER TABLE staging_extraction ADD COLUMN deal_attitude TEXT;   -- FRIENDLY | HOSTILE | null
ALTER TABLE staging_extraction ADD COLUMN approach_type TEXT;   -- SOLICITED | UNSOLICITED | null

ALTER TABLE transaction_record ADD COLUMN deal_attitude TEXT;   -- FRIENDLY | HOSTILE | null
ALTER TABLE transaction_record ADD COLUMN approach_type TEXT;   -- SOLICITED | UNSOLICITED | null
