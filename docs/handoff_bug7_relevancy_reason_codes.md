# Handoff: Bug #7 — relevancy reason-code drift (funding/recap squashed)

**Repo:** `ma-collection-mvp` · **Stage:** 2 `stages/relevancy_filter.py` vs `prompts/relevancy_filter.md` (0.5)
**Severity:** low (rows are NOT dropped) but a correctness/traceability gap — funding & recap
lose their reason label at the relevancy layer.

## Symptom
Run log, repeatedly, on funding rows:
```
source_raw_id=146 normalized off-enum reason_code 'VC_ROUND_OR_FUNDING' → 'AMBIGUOUS_BUT_LIKELY_DEAL'
```
The rows stay RELEVANT (good), but every funding round is labeled `AMBIGUOUS_BUT_LIKELY_DEAL`
instead of `VC_ROUND_OR_FUNDING`, so you cannot triage/count funding at the relevancy layer.

## Root cause — prompt ↔ stage enum drift
The **prompt** (`relevancy_filter.md` 0.5) declares these RELEVANT-side reason codes (among others):
`VC_ROUND_OR_FUNDING`, `RECAPITALIZATION`, `ASSET_PURCHASE`.

The **stage** validator does not list the first two:
```python
# stages/relevancy_filter.py ~L49
_VALID_REASON_CODES = frozenset({
    "ACQUISITION_ANNOUNCEMENT", "MERGER_ANNOUNCEMENT", "CARVE_OUT_OR_DIVESTITURE",
    "SPIN_OFF_OR_SPLIT", "TAKE_PRIVATE", "REVERSE_MERGER", "JOINT_VENTURE",
    "MINORITY_INVESTMENT", "DEAL_CLOSE_OR_COMPLETION", "DEAL_AMENDMENT_OR_TERMINATION",
    "AMBIGUOUS_BUT_LIKELY_DEAL",
    # NOT_RELEVANT side ...
})
# NOTE: no VC_ROUND_OR_FUNDING, no RECAPITALIZATION
```
When the model emits an off-enum code, `_normalize_reason_code` tries the alias table, then a
`_ANNOUNCEMENT` suffix, then falls back to `AMBIGUOUS_BUT_LIKELY_DEAL` (RELEVANT) /
`OTHER_NOT_RELEVANT`. `VC_ROUND_OR_FUNDING` and `RECAPITALIZATION` match none → fall back to
`AMBIGUOUS_BUT_LIKELY_DEAL`. `ASSET_PURCHASE` is handled (alias → `ACQUISITION_ANNOUNCEMENT`).

This is the same authored-vs-run split as prior bugs: the prompt was updated to 0.5 with these
codes, the stage validator wasn't.

## Fix
Add the two missing RELEVANT-side codes to `_VALID_REASON_CODES`:
```python
"VC_ROUND_OR_FUNDING", "RECAPITALIZATION",
```
Nothing downstream keys on `reason_code` for control flow (the stage comments it as "secondary
metadata"), so this is a safe additive change. Optionally add defensive aliases
(`FUNDING`, `VC_ROUND`, `SERIES_*` → `VC_ROUND_OR_FUNDING`; `RECAP`, `DIVIDEND_RECAP` →
`RECAPITALIZATION`).

## Verify
Re-run relevancy on a funding batch (or reset a few funding `source_raw` rows to FETCHED and
re-run stage 2); confirm the log no longer normalizes `VC_ROUND_OR_FUNDING` away and the code
persists on the row.

## Related
Sibling of the older `ASSET_PURCHASE` reason-code gap (already aliased). Keep prompt 0.5's full
reason-code list and the stage `_VALID_REASON_CODES` in lockstep going forward (see the enum
reconciliation in `docs/CONTEXT.md` §4).
