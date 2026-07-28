# Change Log

This log records focused project changes that affect pipeline behavior,
prompt behavior, or product-facing derived fields. It complements Git history
with a plain-language map of what changed and where.

## 2026-07-22 - Announcement vs Close Prompt Semantics

Commit: `9a75e9e5c65c4d69eb974d5e652ffb38cd01c1e5`

Changed files:

- `prompts/deal_type_classifier.md`
- `prompts/high_confidence_extraction.md`
- `stages/deal_type_classify.py`
- `stages/high_confidence_extract.py`
- `docs/decisions.md`
- `docs/prompt_announcement_close_validation_2026_07_22.md`

Behavioral change:

- `CLOSE` is reserved for a separate later release that explicitly references
  a previously announced transaction.
- First-observed same-day completed private acquisitions and advisor tombstone
  releases remain `ANNOUNCEMENT`.
- High-confidence extraction can populate both `announced_date` and
  `closed_date` for completed same-day announcements when no pending-close
  language is present.
- Pending-close language wins over completed-sounding headlines.

Validation:

- Local DB: `data/prompt_validation_20260722_r2.db`
- Run ID: `prompt_validation_20260722_r2_run_20260722_154146`
- Result: 6/6 sources relevant, 6/6 classified, 6/6 original source rows
  extracted, 0 prompt failures.

## 2026-07-22 - Take-Private Derived Flag Broadening

Commit: see the Git commit that introduced this entry.

Changed files:

- `stages/aggregate.py`
- `stages/summarize.py`
- `stages/rationale_tag.py`
- `prompts/aggregation.md`
- `prompts/deal_summary.md`
- `prompts/strategic_rationale.md`
- `schema/001_initial.sql`
- `scripts/test_take_private_derivation.py`
- `docs/change_log.md`
- `docs/decisions.md`

Behavioral change:

- Stage 9 derives `is_take_private` for public standalone targets acquired into
  private or non-public ownership, including private strategic buyers and
  private consortiums.
- Public acquirer tickers block the take-private flag, reducing public-public
  merger/acquisition false positives.
- Summary generation now receives `flags.is_take_private` directly instead of
  re-deriving take-private framing from raw fields.

Validation:

- Script: `scripts/test_take_private_derivation.py`
- Result: PASS.
## 2026-07-28 - V2 Prompt Alignment

Commit: *(to be filled on push)*

Changed files:

- `prompts/deal_type_classifier.md` (0.5 → 0.6)
- `prompts/high_confidence_extraction.md` (0.11 → 0.12)
- `prompts/aggregation.md` (0.3 → 0.4)
- `prompts/deal_summary.md` (0.8 → 0.9)
- `prompts/strategic_rationale.md` (0.4 → 0.5)
- `docs/prompt_versions.md` (updated)
- `schema/002_v2_prompt_alignment.sql` (new migration)
- `stages/deal_type_classify.py` (parser updates for v0.6 output)
- `stages/high_confidence_extract.py` (parser updates for v0.12 output)

Behavioral changes:

**Classifier (0.6):**
- `v2_event_type` added as primary deal classification output (V2 EventType
  vocabulary). `deal_type` retained as transitional alias — same value.
- `event_type` renamed to `event_history_type` (eliminates V2 field name
  collision). `event_type` accepted during rollout period.
- `SPIN_SPLIT` split into `SPIN_OFF` and `SPLIT_OFF` as top-level event types.
  `spin_split_type` discriminator retained for backward compatibility.
- `RECAPITALIZATION` added as a top-level event type with `recap_type`
  discriminator (DIVIDEND | EQUITY | LEVERAGED | SPONSOR_RECAP).
- `target_type` values lowercased (V2 vocabulary); `spinco` added for
  spin/split targets. Legacy uppercase values normalized in parser.
- VC/funding rounds return UNKNOWN with a routing note — pending funding path.
- `ANNOUNCEMENT` / `CLOSE` renamed to `ANNOUNCED` / `CLOSED` in
  `event_history_type`.

**HC Extraction (0.12):**
- `acquirer.type` now uses V2 lowercase vocabulary. Legacy uppercase values
  normalized in parser during migration.
- `revenue_period_type` and `ebitda_period_type` aligned to V2 period_type
  enum (LTM | NTM | ANNUAL | QUARTERLY | INTERIM_YTD). Legacy values (FY,
  TTM, CY, QUARTER, UNKNOWN) normalized in parser. null explicitly required
  when period not stated — do NOT assume LTM.
- `date_precision` fields added for `announced_date`, `closed_date`,
  `signing_date` (exact | month | quarter | year).
- `rumor_date` added — date of first media report for rumored deals.
- `financials_disclosure_status` added as required field
  (DISCLOSED | UNDISCLOSED | UNKNOWN).
- `consideration_type` now extracted directly by prompt as interim field
  pending `consideration_component` table (cash | stock | cash_and_stock |
  election | other).

**Aggregation (0.4):**
- V2 vocabulary section added. LTM and NTM explicitly non-interchangeable
  in conflict resolution — period type disagreement flagged as SEMANTIC.

**Deal Summary (0.9) / Strategic Rationale (0.5):**
- Input field names updated to V2. RECAPITALIZATION framing added. NTM
  multiples referenced in framing rules.

Schema changes:

- `staging_extraction`: 12 new nullable columns (v2_event_type,
  event_history_type, recap_type, target_type_v2, spin_split_type_v2,
  acquirer_type_v2, target_revenue_period_type_v2,
  target_ebitda_period_type_v2, announced_date_precision,
  closed_date_precision, signing_date_precision, rumor_date,
  financials_disclosure_status).
- `transaction_record`: 12 matching new nullable columns.
- Legacy columns retained — no data loss.

Validation:

- *(run prompt validation against a local DB before merging)*
- Suggested: rerun the July 22 6-source validation DB to confirm no
  regressions on existing classifier and HC extract behavior.
- New fields to spot-check: `financials_disclosure_status` populated on
  all rows; `period_type` null when period not stated in source;
  `acquirer_type_v2` lowercase on all rows.
