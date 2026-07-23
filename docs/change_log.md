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
