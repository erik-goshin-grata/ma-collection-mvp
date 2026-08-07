# Review: Bug #7 fix proposal (relevancy reason-code drift) — feedback

Reviewer pass on the proposed `relevancy_filter` reason-code fix, verified against the actual
prompt/stage sets. **Verdict: approve** with one required correctness fix and one hardening note
on the parity test.

## Gap verified — the two additions are complete
Ran the diff Claude flagged (don't trust the handoff list): parsed all reason codes the prompt
declares and subtracted the stage's `_VALID_REASON_CODES` ∪ alias keys ∪ alias targets.

```
prompt-declared reason codes: 25
Declared in prompt but NOT covered by stage:
   - RECAPITALIZATION
   - VC_ROUND_OR_FUNDING
```

So the gap is **exactly** `{VC_ROUND_OR_FUNDING, RECAPITALIZATION}` — not just the two that
showed up in logs. Adding both to `_VALID_REASON_CODES` fully closes it. ✅

## Required fix before merge
**Keep the `classification` parameter on `_normalize_reason_code`.** The current signature is
`_normalize_reason_code(reason_code, classification)` and the terminal fallback depends on it:
RELEVANT → `AMBIGUOUS_BUT_LIKELY_DEAL`, NOT_RELEVANT → `OTHER_NOT_RELEVANT`. The proposed rewrite
`_normalize_reason_code(raw_code)` drops it, which breaks (or silently mis-defaults) that
side-aware fallback. Restore the param:
```python
def _normalize_reason_code(raw_code: str, classification: str) -> str:
    code = raw_code.strip().upper()
    if code in _VALID_REASON_CODES:
        return code
    if code in _REASON_CODE_ALIASES:
        return _REASON_CODE_ALIASES[code]
    if classification == "RELEVANT" and code.startswith("SERIES_"):
        return "VC_ROUND_OR_FUNDING"
    # existing _ANNOUNCEMENT-suffix logic ...
    return "AMBIGUOUS_BUT_LIKELY_DEAL" if classification == "RELEVANT" else "OTHER_NOT_RELEVANT"
```
(Gating the `SERIES_` prefix on the RELEVANT side is trivially correct and keeps the rule tidy.)

## Additions — approved
- `_VALID_REASON_CODES += {"VC_ROUND_OR_FUNDING", "RECAPITALIZATION"}`
- Aliases `FUNDING`, `VC_ROUND` → `VC_ROUND_OR_FUNDING`; `RECAP`, `DIVIDEND_RECAP` →
  `RECAPITALIZATION`, plus the `SERIES_*` prefix rule.

## Drift-guard test — strongly endorse, with a parse caveat
The parity test (`tests/test_reason_code_parity.py`) is the real fix — the enum additions just
clear today's symptom; the test stops the next prompt bump from silently reintroducing drift.
**Caveat:** raw backtick extraction from the prompt is noisy — `HIGH`, `MEDIUM`, `RELEVANT`,
`NOT_RELEVANT`, `PROMPT_FAILED` are also in backticks and are NOT reason codes. Two ways to keep
the test from false-positiving on every prompt edit:
- **Preferred:** add a demarcated, machine-parseable reason-code block to
  `prompts/relevancy_filter.md` (a fenced list, or `<!-- REASON_CODES: ... -->` markers) that both
  the test and humans read; parse that, not arbitrary backticks.
- Otherwise: have the test carry the same NON_CODES exclusion set used to run this diff.

Assert: every prompt-declared reason code is in `_VALID_REASON_CODES` directly **or** is an alias
target. (Alias keys don't need to be in the prompt; alias targets must be valid.)

## Optional (signal fidelity, not required)
Also map `SEED`, `PRE_SEED`, `PRE_SERIES_*`, `ANGEL`, `GROWTH_EQUITY` → `VC_ROUND_OR_FUNDING`.
The fallback already keeps these rows RELEVANT; this only sharpens the reason label.

## Verify
Re-run stage 2 on a funding batch (or reset a few funding `source_raw` rows to FETCHED); confirm
the `normalized off-enum reason_code 'VC_ROUND_OR_FUNDING' → ...` log line is gone and the code
persists as `VC_ROUND_OR_FUNDING` / `RECAPITALIZATION` on those rows.
