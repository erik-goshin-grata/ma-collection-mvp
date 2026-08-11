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
| Deal Type Classifier | `prompts/deal_type_classifier.md` | 0.6 | 2026-07-28 | V2-2026-07-28 | v2_event_type + event_history_type rename; SPIN_OFF/SPLIT_OFF top-level; RECAPITALIZATION added; target_type lowercase |
| High Confidence Extraction | `prompts/high_confidence_extraction.md` | 0.13 | 2026-08-10 | V2-2026-07-28 | 0.13: capital-raised precondition + `round_size` capture (primary capital → round_size, value fields null; spec §4.1 / gap 1). Prior: acquirer_type lowercase; period_type enum enforced; date_precision; rumor_date; financials_disclosure_status; consideration_type |
| Aggregation (Conflict Resolution) | `prompts/aggregation.md` | 0.4 | 2026-07-28 | V2-2026-07-28 | V2 vocabulary section added; LTM/NTM non-interchangeable rule; period type semantic conflict example |
| Deal Summary | `prompts/deal_summary.md` | 0.9 | 2026-07-28 | V2-2026-07-28 | Input fields updated to V2 names; RECAPITALIZATION framing added; NTM multiples in framing rule |
| Funding LC Extraction | `prompts/funding_lc_extraction.md` | 0.1 | 2026-07-28 | V2-2026-07-28 | New prompt — advisors, use of proceeds, board seats, pct_acquired, regulatory flags for funding events. |
| Funding HC Extraction | `prompts/funding_hc_extraction.md` | 0.1 | 2026-07-28 | V2-2026-07-28 | New prompt — VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT. Multi-investment source support. Sparse source handling. |

---

## Stage → Prompt Map

| Stage | Prompt | Triggered When |
|---|---|---|
| Stage 3 — Deal Type Classify | `deal_type_classifier` | Every relevant source row |
| Stage 4 — High Confidence Extract | `high_confidence_extraction` | Every CLASSIFIED row |
| Stage 9 — Aggregate | `aggregation` | Same-tier field conflicts only |
| Stage 10 — Summarize | `deal_summary` | Each transaction_record with no current summary |
| Stage 11 — Rationale Tag | `strategic_rationale` | Each transaction_record with a current summary |

---

## V2 Alignment History

| Date | Prompts Changed | Versions | Change | Enum Target |
|---|---|---|---|---|
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
