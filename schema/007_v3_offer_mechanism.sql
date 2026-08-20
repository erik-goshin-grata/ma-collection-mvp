-- 007_v3_offer_mechanism.sql
-- V3 slice S-D — Offer Mechanism (inventory §T12).
--
-- Whether the acquisition is effected through an offer made directly to target
-- securityholders. TENDER_OFFER | null.
--
-- Before this, the fact existed only as `merger_structure = TENDER_OFFER` on the Stage 11
-- agreement path, which is gated on an SEC filing being present. Any transaction without
-- one — private, non-US, or simply unfiled — could not record a tender offer at all,
-- however plainly the press release described it. §T12 requires ordinary-source
-- extraction, so high_confidence_extraction owns this field.
--
-- The SEC path is retained, not replaced. `merger_structure` keeps its column and all four
-- of its values; Stage 11 additionally emits an `offer_mechanism` observation when it sees
-- TENDER_OFFER, so agreement evidence corroborates the canonical fact instead of being
-- discarded. DIRECT / FORWARD_TRIANGULAR / REVERSE_TRIANGULAR stay merger-mechanics
-- observations only and never populate this field — §T2 defers those, and absorbing them
-- here would invent a V3 destination that was deliberately not decided.
--
-- Vocabulary is one value plus null by decision, not by omission. MANDATORY_OFFER,
-- SCHEME_OF_ARRANGEMENT, ONE_STEP_MERGER and TWO_STEP_MERGER are excluded by §T12.
--
-- Enums are application-layer (stages/high_confidence_extract.py), not CHECK constraints,
-- matching every other typed dimension in this schema.

ALTER TABLE staging_extraction ADD COLUMN offer_mechanism TEXT;   -- TENDER_OFFER | null
ALTER TABLE transaction_record ADD COLUMN offer_mechanism TEXT;   -- TENDER_OFFER | null
