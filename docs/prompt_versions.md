# Prompt Versions

**Product Contract:** Transactions V3 — `V3-PC-1.0` · **Reconciled:** 2026-08-22

Single-page view across all pipeline prompts. Each prompt maintains its own versioning table
and few-shot history internally; this doc tracks the cross-prompt state at a glance.

Prompt versions are **independent of the Product Contract version** and are not merged into
it. A prompt version says which contract generation that prompt was built against; it is not
a statement about the canonical model.

> **The "V2 Enum Target" column has been retired.** Every production prompt now targets V3,
> so the column no longer discriminated anything. Per-prompt V3 decision references (§T…)
> live in the Notes column and in `docs/v3_change_decision_register.md`.

Parity between each prompt's declared version and its stage's `_VERSION` constant is asserted
by `scripts/test_prompt_stage_version_parity.py`.

---

## Current State

| Prompt | File | Current Version | Stage `_VERSION` | Last Changed | Notes |
|---|---|---|---|---|---|
| Relevancy Filter | `prompts/relevancy_filter.md` | **0.8** | 0.8 | 2026-08-21 | 0.8: the authoritative 24-code `reason_code` vocabulary is delivered **inside the §4 system prompt** — it previously lived outside the delivered fences and the model never saw it (S-H). Reason codes are a separate vocabulary from `v2_event_type` |
| Deal Type Classifier | `prompts/deal_type_classifier.md` | **0.13** | 0.13 | 2026-08-21 | §T1/§T2/§T3. 0.11: transaction form alone does not determine `target_type`. 0.10: `spinco` removed. 0.9: merger family → `combination_structure`. 0.8: `PIPE` recognized-not-profiled (see `ENG-V3-018`) |
| High Confidence Extraction | `prompts/high_confidence_extraction.md` | **0.24** | 0.24 | 2026-08-22 | 0.24: `is_going_private_outcome` added to `features` (`ENG-V3-020`). 0.21: `sponsor_transaction_role` (§T7). 0.20: `offer_mechanism` (§T12). 0.19: `asset_type` (§T13). 0.18: `EQUITY_VALUE` is stake-level only |
| Funding HC Extraction | `prompts/funding_hc_extraction.md` | **0.3** | 0.3 | 2026-08-21 | §T14 / §A6.3 round, stage and price-direction semantics |
| Low Confidence Extraction | `prompts/low_confidence_extraction.md` | **0.10** | 0.10 | 2026-08-21 | §T11 attitude/approach split; typed contingent consideration (S-F) |
| Funding LC Extraction | `prompts/funding_lc_extraction.md` | **0.2** | — | 2026-08-21 | |
| Aggregation (Conflict Resolution) | `prompts/aggregation.md` | **0.6** | 0.6 | 2026-08-21 | Resolver vocabulary aligned to the values it actually resolves |
| Deal Summary | `prompts/deal_summary.md` | **0.16** | 0.16 | 2026-08-22 | 0.16: canonical funding fields reach the summary; non-disclosure language requires an affirmative signal (`ENG-V3-021`). 0.13: `sponsor_transaction_role`. 0.12: attitude/approach replace `hostile` |
| Strategic Rationale | `prompts/strategic_rationale.md` | **0.6** | 0.6 | 2026-08-21 | Three structure-derived defaults remain live — **tabled**, see §R7+§R9+§S2.1 and `ENG-V3-006` |
| Agreement — Recitals | `prompts/agreement_recitals.md` | **0.3** | — | 2026-08-21 | |
| Agreement — Consideration | `prompts/agreement_consideration.md` | **0.2** | — | 2026-08-21 | |
| Agreement — Capitalization | `prompts/agreement_capitalization.md` | **0.2** | — | 2026-08-21 | |
| Agreement — Termination | `prompts/agreement_termination.md` | **0.2** | — | 2026-08-21 | |
| Agreement — Conditions | `prompts/agreement_conditions.md` | **0.2** | — | 2026-08-21 | |
| *(conventions)* | `prompts/prompt_conventions.md` | **0.5** | n/a | 2026-08-21 | Convention document, not a delivered prompt. 0.5: prompt provenance is caller-owned (S-G) |

> **Delivered vs documented.** `prompts/base.py::load_prompt_file` extracts **only** the §4
> (`system`) and §5 (`user_template`) fences. Everything outside them — §3, §6, §7, the
> failure-mode table, the changelog — is documentation the model never receives. Prompt-contract
> tests must assert on `load_prompt_file(...)`, not on the Markdown file. This root cause
> produced two separate defects and is the reason the relevancy vocabulary went undelivered for
> the prompt's entire history while a parity test passed on 24 == 24.

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
