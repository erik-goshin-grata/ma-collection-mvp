-- 006_v3_asset_type.sql
-- V3 slice S-C — Asset Type (inventory §T13) and target_type cleanup (§T3).
--
-- asset_type answers "what KIND of asset is being transacted?" and is SUBORDINATE to
-- target_type = 'assets'. For every other target type it must be null. It is not a
-- replacement for target_type, and it is not sector or industry: a pipeline is
-- INFRASTRUCTURE because a pipeline is the thing transacted, whoever buys it.
--
-- Vocabulary (application-layer, stages/high_confidence_extract.py):
--   REAL_ESTATE | INFRASTRUCTURE | ENERGY | NATURAL_RESOURCES | INTELLECTUAL_PROPERTY
--   | DATA | FACILITY | EQUIPMENT | CONTRACTS_OR_RIGHTS | BRAND_OR_PRODUCT | OTHER | null
--
-- FACILITY is deliberately distinct from REAL_ESTATE: an operating plant, mill or yard
-- is a different transaction object from property acquired principally as real estate.
--
-- No column is added or dropped for target_type. Removing `spinco` (§T3) is a vocabulary
-- change enforced in the application layer; stored rows carrying it are untouched, and
-- collapsing target_type/target_type_v2 is migration work that is out of scope here.
--
-- `is_divestiture` (§T4) stops being authored by Stage 9 in this slice. Its column and
-- its stored values remain; removal is migration work, and exports still read it.

ALTER TABLE staging_extraction ADD COLUMN asset_type TEXT;  -- see vocabulary above; null unless target_type = 'assets'
ALTER TABLE transaction_record ADD COLUMN asset_type TEXT;  -- see vocabulary above; null unless target_type = 'assets'
