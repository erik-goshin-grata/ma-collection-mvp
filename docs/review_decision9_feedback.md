# Decision #9 — VC_ROUND vs GROWTH_EQUITY: resolution

**Outcome:** keep the current automated rule; the marquee-deal edge cases are handled by a
**researcher override in the research tool** (a separate layer), which is **not built yet** and
is deferred. No size/stage override goes into the classifier now.

## Decision
- **Automated default (unchanged):** investor-archetype + company-profitability test in
  `deal_type_classifier.md` (#9/#10), with the existing "when unclear → VC_ROUND" tie-break.
  Optionally add the **narrow tie-break nudge** — only when the investor signal is genuinely
  mixed *and* the rule would otherwise default to VC_ROUND, let `Series D+` **AND** a size floor
  flip it to GROWTH_EQUITY. Not a blanket override.
- **Base Power stays `VC_ROUND`** — clean VC/crossover cap table (Ribbit, Addition, Valor,
  JPMorgan SIG, Altimeter, D1, a16z/Lightspeed), pre-profit company. A pure size/stage override
  would flip it wrongly ("the number got big"), which is exactly the kind of marquee mislabel a
  customer would spot-check.
- **Researcher override = research-tool responsibility, deferred (not built).**

## Why this is safe to punt to a human (verified in code)
Reclassifying VC_ROUND ↔ GROWTH_EQUITY is **label-only** — same downstream code path:
- `stages/aggregate.py` `_FUNDING_EVENT_TYPES = {VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT}`.
- #5's funding-cluster branch and #8's non-control value-semantics both bucket the two together.
So a human flip doesn't change clustering or valuation — it only changes the label. Low-risk,
unlike a misclassification that would send a row down a different path.

## Current tool support (audit, 2026-08-06)
Scaffolding exists but no override write-path is implemented:
- Review columns (`review_status`/`reviewed_by`/`reviewed_at`/`researcher_notes`) exist on
  `entity` and `transaction_participant` — **but not on `transaction_record`, where `deal_type`
  lives**, and nothing writes them (unused scaffolding).
- `is_current` versioning retains prior versions (history is reconstructable).
- `net_debt` is a working precedent for a manual input preserved across re-aggregation.
- `aggregation_conflict_log.flagged_for_review` is a nearby review-queue pattern.

## Forward note (for when the research tool adds overrides)
Whichever layer writes the override, the **pipeline must not silently revert it on re-run.**
Recommended: a **general per-field override table** (`transaction_id, field, from_value,
to_value, who, when`) that aggregate consults-and-preserves — rather than cloning `net_debt`'s
per-field special-casing, which doesn't scale. Bonus: that from→to log is **empirical
calibration data** — if researchers consistently flip `Series D+ / ≥$X / no growth-PE name` to
GROWTH_EQUITY, that sets the tie-break floor from real data instead of a guessed number.

## Net
No classifier change required for #9 right now (optional narrow tie-break aside). Override is
research-tool future work. Nothing blocks the #5/#7/#8 fixes.
