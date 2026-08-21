# Prompt Versions

Single-page view across all pipeline prompts. Each prompt maintains its own
versioning table and few-shot history internally. This doc tracks the
cross-prompt state at a glance, including V2 enum alignment status.

**V2 Enum Target:** The enum/schema version a prompt was written against.
`pre-V2` means the prompt predates V2 alignment work and uses legacy
vocabulary. `V2-2026-07-28` means aligned to the V2 enum/schema state as of
July 28, 2026.

---

## Current State

| Prompt | File | Current Version | Last Changed | V2 Enum Target | Notes |
|---|---|---|---|---|---|
| Deal Type Classifier | `prompts/deal_type_classifier.md` | **0.11** | 2026-08-20 | V3 §T2/§T3 | 0.11: transaction form alone does not determine `target_type` — `assets` is not chosen *solely* because a source says "asset purchase" (S-C Gate 2). 0.10: `spinco` removed from `target_type`. 0.9: merger family moved to `combination_structure`. 0.8: `PIPE` recognized-not-profiled |
| Relevancy Filter | `prompts/relevancy_filter.md` | 0.6 | 2026-08-18 | V2-2026-07-28 | 0.6: adds `PIPE` to the RELEVANT reason_code enum (24 codes). Reason codes are a **separate vocabulary** from `v2_event_type` — `MERGER_ANNOUNCEMENT` and `REVERSE_MERGER` remain valid *hints* after §T2 removed them as event types, which is what `overrides_relevancy_hint` exists for. Parity asserted by `scripts/test_reason_code_parity.py` |
| High Confidence Extraction | `prompts/high_confidence_extraction.md` | **0.20** | 2026-08-20 | V3 §T12/§T13 | 0.20: `offer_mechanism` (`TENDER_OFFER`/null) extracted from ordinary sources, not only SEC filings. 0.19: `asset_type`, subordinate to `target_type = assets` and null for every other target type. 0.18: `EQUITY_VALUE` is stake-level only |
| Low Confidence Extraction | `prompts/low_confidence_extraction.md` | **0.8** | 2026-08-20 | V3 §T11 | **Was absent from this table.** 0.8: a mandatory/regulatory offer does not by itself establish `approach_type = UNSOLICITED` — see the known-issue note in `docs/v3_slice_reconciliation.md`, this clarification did not change the observed behaviour. 0.7: `CONTINGENT_CONSIDERATION` added, `includes_earnout` retired. 0.6: fused `hostile` split into `deal_attitude` + `approach_type` |
| Funding HC Extraction | `prompts/funding_hc_extraction.md` | **0.2** | 2026-08-20 | V3 §T14 | 0.2: `round_price_direction` (UP/DOWN/FLAT/null) replaces `is_down_round`, which could only ever record DOWN. 0.1 validated 2026-08-17: 8/8 on real-source fixtures |
| Funding LC Extraction | `prompts/funding_lc_extraction.md` | 0.1 | 2026-07-28 | V2-2026-07-28 | Advisors, use of proceeds, board seats, pct_acquired, regulatory flags for funding events |
| Aggregation (Conflict Resolution) | `prompts/aggregation.md` | 0.4 | 2026-07-28 | V2-2026-07-28 | V2 vocabulary section; LTM/NTM non-interchangeable rule |
| Deal Summary | `prompts/deal_summary.md` | **0.12** | 2026-08-20 | V3 §T2/§T11 | 0.12: `flags.hostile` replaced by `flags.deal_attitude` + `flags.approach_type` — the retired key had been arriving permanently false. 0.11: `flags.includes_earnout` removed. 0.10: consumer update for `combination_structure` |
| Strategic Rationale | `prompts/strategic_rationale.md` | 0.5 | 2026-07-28 | V2-2026-07-28 | **Was absent from this table.** Stage 13 (`stages/rationale_tag.py`) |
| Agreement — Recitals | `prompts/agreement_recitals.md` | 0.2 | — | SEC path | **Was absent from this table.** All five agreement sub-prompts are versioned in `stages/agreement_extract.py::_VERSIONS` |
| Agreement — Consideration | `prompts/agreement_consideration.md` | 0.1 | — | SEC path | **Was absent from this table.** |
| Agreement — Capitalization | `prompts/agreement_capitalization.md` | 0.1 | — | SEC path | **Was absent from this table.** |
| Agreement — Termination | `prompts/agreement_termination.md` | 0.1 | — | SEC path | **Was absent from this table.** |
| Agreement — Conditions | `prompts/agreement_conditions.md` | 0.1 | — | SEC path | **Was absent from this table.** |

> **Reconciled 2026-08-20** against the live prompt headers and stage `_VERSION` constants.
> This table had drifted three releases behind on the classifier (0.8 vs 0.11) and omitted
> seven prompts entirely. Prompt/stage parity is asserted by the test suite; this page is a
> reading aid, not the source of truth.

---

## Stage → Prompt Map

| Stage | Prompt | Triggered When |
|---|---|---|
| Stage 1 — Relevancy Filter | `relevancy_filter` | Every scraped source row |
| Stage 3 — Deal Type Classify | `deal_type_classifier` | Every relevant source row |
| Stage 4 — High Confidence Extract | `high_confidence_extraction` | Every CLASSIFIED row |
| Stage 4b — Funding HC Extract | `funding_hc_extraction` | Funding-typed rows |
| Stage 7 — Low Confidence Extract | `low_confidence_extraction` | Every HC_EXTRACTED row |
| Stage 9 — Aggregate | `aggregation` | Same-tier field conflicts only |
| Stage 11 — Agreement Extract | `agreement_*` (five sub-prompts) | Transactions with unextracted agreement documents |
| Stage 12 — Summarize | `deal_summary` | Each transaction_record with no current summary |
| Stage 13 — Rationale Tag | `strategic_rationale` | Each transaction_record with a current summary |

> Stage numbers reconciled against `run.py`, which is authoritative. Summarize is **Stage 12**
> and Rationale Tag is **Stage 13**; this table previously showed 10 and 11. Note that
> `stages/summarize.py` still *logs* "Stage 10" — a stale string in executable code, left for a
> separate commit rather than fixed here.

---

## V2 Alignment History

| Date | Prompts Changed | Versions | Change | Enum Target |
|---|---|---|---|---|
| 2026-08-17 | High Confidence Extraction | hc_extract 0.18 | `EQUITY_VALUE` narrowed to the equity purchase price for the stake actually acquired; market capitalization is no longer an `EQUITY_VALUE` and gets its own `MARKET_CAPITALIZATION` type, captured so the fact survives but never used as deal consideration | V2-2026-07-28 + equity scope |
| 2026-08-17 | High Confidence Extraction | hc_extract 0.17 | Balance-sheet capture: `total_debt` and `cash_st` as point-in-time figures with their own currency and an exact `balance_sheet_as_of_date`; POINT_IN_TIME is the only period framing; prefer a source-stated USD figure and never self-convert | V2-2026-07-28 + balance-sheet capture |
| 2026-08-12 | Deal Type Classifier, High Confidence Extraction | classifier 0.7, hc_extract 0.14 | Harness minority cleanup: minority is derived flag, not core event type; explicit `stake_transition_type` added for ownership step-ups including Lumina/TNQTech | V2-2026-07-28 + harness minority cleanup |
| 2026-07-28 | All five | classifier 0.6, hc_extract 0.12, aggregation 0.4, deal_summary 0.9, strategic_rationale 0.5 | V2 vocabulary alignment — event type rename, lowercase acquirer_type and target_type, period_type enum enforcement, RECAPITALIZATION added, date precision fields, rumor_date, financials_disclosure_status, consideration_type interim field | V2-2026-07-28 |

---

## Pre-V2 Change History (Summary)

| Date | Prompts Changed | Change |
|---|---|---|
| 2026-07-22 | classifier 0.5, hc_extract 0.11 | Announcement vs Close semantics — CLOSE reserved for separate later releases |
| 2026-07-22 | aggregation 0.3, deal_summary 0.8, strategic_rationale 0.4 | Take-private derived flag — prompts receive is_take_private directly |
| 2026-04-23 | All | RESPONSE FORMAT block added inline in system prompts |
| 2026-04-22 | All | Initial drafts |

---

## Pipeline Code Changes Required for V2 Prompt Output

The prompt changes introduce new output fields that need corresponding
pipeline changes before they can be written to the DB:

| New Prompt Field | Stage | Pipeline Change Needed |
|---|---|---|
| `v2_event_type` (classifier) | Stage 3 | Read `v2_event_type` alongside `deal_type`; migrate Stage 9 to use `v2_event_type` |
| `event_history_type` (classifier) | Stage 3 | Read `event_history_type` alongside `event_type` |
| `recap_type` (classifier) | Stage 3 | Add `recap_type` column to `staging_extraction`; write from classifier output |
| `announced_date_precision`, `closed_date_precision`, `signing_date_precision` (HC extract) | Stage 4 | Add precision columns to `staging_extraction`; write from HC output |
| `rumor_date` (HC extract) | Stage 4 | Add `rumor_date` column to `staging_extraction` |
| `financials_disclosure_status` (HC extract) | Stage 4 | Add column to `staging_extraction`; aggregate to `transaction_record` |
| `consideration_type` (HC extract) | Stage 4 | Already aggregated via `_derive_consideration_type`; new direct extraction path to cross-check |
| Lowercase `acquirer_type` values (HC extract) | Stage 4 | Parser validation update — reject uppercase legacy values |
| `revenue_period_type`, `ebitda_period_type` V2 values (HC extract) | Stage 4 | Validation update to enforce V2 enum; reject UNKNOWN, accept LTM/NTM/ANNUAL/QUARTERLY/INTERIM_YTD/null |

These are tracked in `pipeline_prompt_todo.md` Phase 3 (Schema / Pipeline Changes).

---

## Pending Work

**Funding path (new workstream — see `docs/funding_path_design.md`):**
- `funding_hc_extraction` v0.1 — written, not yet in pipeline
- `stages/funding_hc_extract.py` — not yet written
- `run.py` branch on `v2_event_type` for funding routing — not yet implemented
- `schema/003_funding_path.sql` — not yet written
- `adapters/sec_api.py` Form D extension — not yet implemented
- `prompts/deal_summary.md` v0.10 — funding framing block needed
- VC/funding event types in classifier (`VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`) — pending funding path design
- Multi-party array output from HC extraction — pending `transaction_party` schema finalization
- `transaction_participant` → `transaction_party` rename in pipeline
- `financial_metric` table as write target for deal values
- `transaction_event_history` table as write target for dates
