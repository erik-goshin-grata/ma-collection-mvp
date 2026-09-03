-- 022 — source_character: whose voice a source is in, for deterministic tier resolution.
--
-- Two independent things establish a source_raw row's authority tier: known
-- document identity (a SEC regulatory/operative filing -- source_tier is
-- already final at insert, no source_character involved) and source
-- CHARACTER -- whose voice the content is in. Character is either declared
-- deterministically by an ingestion path that already knows it (PR
-- Newswire's own issuer feed; an SEC EX-99.x exhibit sec_api.py's own regex
-- classifier already identified as a company press release; a future
-- portfolio/tombstone/news crawler) or, only when no acquisition path
-- already knows it (a generic discovered WEB_URL row), inferred by the
-- existing Relevancy content-reading gate. See lib/source_authority.py.
--
-- NULL means "not yet known" -- the ordinary state for a freshly-inserted
-- WEB_URL row before Relevancy runs. A non-NULL value already present at
-- Relevancy time means an acquisition path already answered this question;
-- Relevancy's own inference for that row is then computed but discarded,
-- never overwriting a known answer.

ALTER TABLE source_raw ADD COLUMN source_character TEXT;
