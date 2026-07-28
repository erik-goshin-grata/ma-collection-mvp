# Decisions — 2026-07-28

---

## Decision: V2 EventType vocabulary adopted in pipeline

**Context:** Eng RFC confirmed V2 EventType enum. Product spec and pipeline
were using slightly different vocabulary (SPIN_SPLIT vs SPIN_OFF/SPLIT_OFF,
ANNOUNCEMENT vs ANNOUNCED, etc.).

**Decision:** Adopt V2 vocabulary across all prompts. Legacy field names
(`deal_type`, `event_type`) retained as transitional aliases during migration.
New canonical fields: `v2_event_type`, `event_history_type`.

**Status:** Accepted. Implemented in all prompts and stage code.

---

## Decision: VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT as classifiable event types

**Context:** These were previously routed to UNKNOWN in the classifier pending
a funding path. With the funding path now designed and partially implemented,
these should be classifiable.

**Decision:** Add all three to the classifier as top-level event types. Funding
extraction (Stage 4b) handles them downstream. `MINORITY_INVESTMENT` stays on
the M&A path for now — corporate strategic minorities have M&A-like control
provisions; revisit if QA shows systematic misclassification.

**Status:** Accepted. Implemented in classifier v0.6 and stage code.

---

## Decision: Separate funding extraction prompt (not shared with M&A)

**Context:** Considered extending `high_confidence_extraction.md` with
conditional funding fields vs. creating a separate `funding_hc_extraction.md`.

**Decision:** Separate prompt. Party shape (investor array vs. acquirer/target),
financial fields (round size/valuation vs. deal value), and round metadata are
too different for a shared prompt without excessive conditional logic.

**Status:** Accepted. `funding_hc_extraction.md` v0.1 written and wired.

---

## Decision: Funding investor storage — `staging_investor` table

**Context:** Three options for storing the investor array from funding events:
(A) additive columns on `staging_extraction`, (B) separate
`staging_funding_extraction` table, (C) `staging_investor` table mirroring the
`advisor` table pattern.

**Decision:** Option C — `staging_investor` table. Mirrors the existing
`advisor` table pattern (one row per investor per extraction, FK to
`staging_extraction`). Normalizes the array naturally. Maps cleanly to V2
`transaction_party` rows at promotion time.

**Status:** Accepted. Table created in `003_funding_path.sql`.

---

## Decision: Orchestrator branching on v2_event_type (no new router stage)

**Context:** Considered adding a lightweight event category router stage
(Stage 3a) between relevancy and classification, vs. branching in the
orchestrator on classifier output.

**Decision:** Branch in orchestrator on `v2_event_type`. The classifier
already outputs funding event types. No new stage needed. `run.py`
`_EXTRACTION_STAGES` routes funding rows to Stage 4b, M&A rows to Stage 4a.

**Status:** Accepted. Implemented in `run.py`.

---

## Decision: CLOSED is canonical terminal status (not COMPLETED)

**Context:** Spec doc used `completed`; pipeline (`_derive_transaction_status`)
and eng RFC both use `CLOSED`.

**Decision:** `CLOSED` is canonical. Spec doc updated. No pipeline or eng
changes needed.

**Status:** Accepted. Resolved.

---

## Decision: Form D as T1 source for funding events

**Context:** Form D (SEC Reg D exempt offering notice) is the authoritative
filing for private funding rounds — analogous to 8-K Item 1.01 for M&A.
sec-api.io supports Form D via the same query API used for 8-K enrichment.

**Decision:** Extend `adapters/sec_api.py` to trigger on Form D for funding
events (same CIK matching pattern, different form type filter). Form D is T1
for `closed_date` and `round_size`. Does not provide investor names or
valuation.

**Status:** Accepted in principle. Implementation deferred.

---

## Decision: Multiples skipped for funding events

**Context:** EV/Revenue and EV/EBITDA multiples are not applicable for
VC/growth/debt events. PMV/Revenue multiples require NTM data not reliably
extractable from press releases.

**Decision:** `_compute_multiples()` returns NOT_CALCULABLE for all funding
event types. PMV/Revenue multiples deferred to a later workstream.

**Status:** Accepted. Implemented in `aggregate.py`.
