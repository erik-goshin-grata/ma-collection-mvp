-- 005_v3_combination_structure.sql
-- V3 slice S-B — Combination Structure (inventory §T2).
--
-- MERGER and REVERSE_MERGER stop being top-level event types. Acquisition is the broad
-- event; the structure through which it is effected becomes a separate typed dimension:
--
--     MERGER | REVERSE_MERGER | DE_SPAC | NULL,  with DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER
--
-- Store the MOST SPECIFIC supported value. Query broader questions by implication, never
-- by equality: "is this a merger?" is `IN (MERGER, REVERSE_MERGER, DE_SPAC)`, not
-- `= MERGER`. Ambiguity resolves upward — a reverse merger with no established SPAC
-- shell stays REVERSE_MERGER.
--
-- NULL means the source does not establish any of the three. That is the ordinary
-- acquisition, including one effected through a share or asset purchase.
--
-- Subordinate to event_type = ACQUISITION, enforced in the application layer
-- (stages/deal_type_classify.py), like every other typed dimension in this schema.
--
-- Legacy MERGER / REVERSE_MERGER rows are untouched. No migration or backfill here:
-- `is_de_spac` keeps its column and its stored values, and simply stops being written.

ALTER TABLE staging_extraction ADD COLUMN combination_structure TEXT;  -- MERGER | REVERSE_MERGER | DE_SPAC | null
ALTER TABLE transaction_record ADD COLUMN combination_structure TEXT;  -- MERGER | REVERSE_MERGER | DE_SPAC | null
