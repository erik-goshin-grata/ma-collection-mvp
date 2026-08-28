-- 020 — two disclosure axes, because a source discloses them independently.
--
-- One field was carrying two different questions. The extraction contract asked it to
-- "classify whether financial terms are disclosed", and the summary prompt used it to
-- license the sentence "Financial terms were not disclosed" -- both of which are about
-- the DEAL's value and terms. The target model defines the same field as the disclosure
-- state for COMPANY FINANCIAL METRICS, which is the target's operating financials. Those
-- are different facts about different things, and a release routinely settles them
-- differently: "terms of the transaction were not disclosed" alongside a quoted revenue
-- figure is an ordinary sentence, and under one field it could only be recorded by
-- choosing an axis and being wrong about the other.
--
-- The target model already names the second axis and calls the concept settled. It was
-- simply never carried here. This adds it.
--
--   financials_disclosure_status          the TARGET's operating financials --
--                                         revenue, EBITDA, ARR and the like
--   transaction_terms_disclosure_status   the DEAL's economics -- value, consideration,
--                                         price, and the terms of the transaction
--
-- Same vocabulary on both, and the same meanings, which do not change:
--
--   DISCLOSED    at least one relevant fact on THAT axis is stated. It has never meant
--                complete disclosure and still does not.
--   UNDISCLOSED  the source AFFIRMATIVELY says so on that axis. Silence is not a denial.
--   UNKNOWN      the source is silent on that axis -- neither stating nor denying.
--
-- PARTIALLY_DISCLOSED is deliberately NOT added. The baseline records it and the
-- reconciliation is open; adding a fourth value on the strength of an open question
-- would freeze the answer.
--
-- `value_type = UNDISCLOSED` is untouched. It remains an affirmative signal on the value
-- object and is not redesigned here -- the summary contract still reads it, and removing
-- or narrowing it in the same change would put two corrections in one place.
--
-- The columns are added to BOTH stages' staging table and to the canonical record,
-- mirroring how the first axis was added, so the new one is not a second-class field
-- that stops at staging.
--
-- Sentinel-guarded and hand-registered in db.py::_apply_migrations. This directory is
-- NOT globbed: a migration without a block in db.py never runs.

ALTER TABLE staging_extraction ADD COLUMN transaction_terms_disclosure_status TEXT;
ALTER TABLE transaction_record ADD COLUMN transaction_terms_disclosure_status TEXT;

CREATE INDEX IF NOT EXISTS idx_staging_terms_disclosure
    ON staging_extraction(transaction_terms_disclosure_status);
