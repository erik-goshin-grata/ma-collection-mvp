# Handoff: Bug #5 — funding rounds never reach export (entity_cluster gate)

**Repo:** `ma-collection-mvp` · **Stage:** 8 `stages/entity_cluster.py` (+ minor `stages/aggregate.py`)
**Severity:** blocks the entire funding path from producing transactions.

## Symptom
On a 190-source PredictLeads funding run (`data/pl_funding.db`), **68 funding rounds
(62 VC_ROUND + 6 VENTURE_DEBT) extracted cleanly but produced 0 transactions.** They sit at
`staging_extraction.status = 'LC_EXTRACTED'` forever. Only the M&A-classified events
(MINORITY_INVESTMENT / ACQUISITION / MERGER / RECAP) reached `transaction_record` (31 rows).
The funding extractions themselves are good — `staging_investor` holds **239 investors across
58 rounds** with types, lead flags, new/existing flags — they just never get clustered or
aggregated.

## Root cause
`entity_cluster` (Stage 8) is built on a **target + acquirer** pair. A funding round has a
**recipient** (fills `target_name`) and **investors** (in `staging_investor`) — but **no
acquirer**, so `acquirer_name` is NULL. Two places assume acquirer is present:

1. **Eligibility gate** — `stages/entity_cluster.py` ~L142:
   ```python
   eligible = [r for r in rows if r["target_name"] and r["acquirer_name"]]
   # rows missing acquirer_name are logged "skipped" and left LC_EXTRACTED
   ```
   Every funding row is dropped here (acquirer_name is NULL).

2. **Match key** — ~L162-172: clustering requires BOTH a target-name match AND an
   acquirer-name match:
   ```python
   norm_t = [_normalize(r["target_name"]) ...]
   norm_a = [_normalize(r["acquirer_name"]) ...]
   ...
   t_match = fuzz.token_set_ratio(norm_t[i], norm_t[j]) >= 90
   a_match = fuzz.token_set_ratio(norm_a[i], norm_a[j]) >= 90
   if not (t_match and a_match):
       continue
   ```
   Even if the gate were relaxed, the pairing logic would still fail on NULL acquirer.

`aggregate.py` (Stage 9) is already tolerant — it defaults `acquirer_name` to `"Unknown"`
(~L495-496) — so it will NOT crash on a funding transaction; funding just needs to *reach* it.

## Fix direction
Give Stage 8 a **funding branch** keyed on the recipient, and let funding flow to aggregate:

1. Add `v2_event_type` to the Stage 8 SELECT (it's on `staging_extraction`).
2. Treat `FUNDING = {VC_ROUND, VENTURE_DEBT, GROWTH_EQUITY}` as **recipient-centric**:
   - **Gate:** require `target_name` only (drop the `acquirer_name` requirement) for funding types.
   - **Match key:** for funding rows, cluster on **recipient (`target_name`) + date window**;
     do not require `a_match`. (Optionally also tighten with `round_label` so two genuinely
     different rounds for the same company in the window don't merge.)
3. `aggregate.py`: funding transactions will aggregate with `acquirer_name = "Unknown"` as-is.
   For a correct funding record, represent the **investor list** (from `staging_investor`)
   rather than a single acquirer — this is also where the review/export format should show
   investors, lead investor, round label/size (ties to the funding review-sheet work).

## Nice side effect
Recipient+date clustering will also **dedupe** the duplicate funding extractions we saw
(e.g. `Valar Atomics` and `OLIX` each appear twice from multiple sources of the same round) —
today they can't dedupe because they never cluster.

## Decisions to make
- **Cluster key for funding:** recipient + date only, or recipient + `round_label` + date?
  (The latter avoids merging a Seed and a Series A for the same company in one window.)
- **Multiple rounds, same company, same window:** keep separate (round_label) or merge?
- How to model "acquirer" for funding downstream — leave "Unknown", or add a
  `lead_investor` concept the export uses in place of acquirer.

## Test fixture (ready)
`data/pl_funding.db` has the 68 stranded rows right now. After the fix:
```
DB_PATH=data/pl_funding.db python run.py --mode=aggregate   # stages 8–9 only
```
Expect the VC_ROUND/VENTURE_DEBT rows to cluster and appear in `transaction_record`
(and the Valar/OLIX-style duplicates to collapse). Verify none remain `LC_EXTRACTED`
with a non-null `target_name`.

## Related (context)
- **#6 (fixed locally):** `funding_hc_extract` multi-transaction INSERT supplied 36 params to
  a 34-column INSERT → crash. Patched with an explicit 34-tuple; verify + commit.
- **#7 (open):** relevancy stage `_VALID_REASON_CODES` lacks `VC_ROUND_OR_FUNDING` /
  `RECAPITALIZATION` (prompt 0.5 emits them) → normalized to `AMBIGUOUS_BUT_LIKELY_DEAL`.
- **#8 (open):** funding routed to MINORITY_INVESTMENT puts the *check size* into
  `equity_value`/`implied_equity_value` (should be round size, not valuation).
- **#9 (decision):** VC_ROUND vs GROWTH_EQUITY — should Series D+ / large rounds tip to
  GROWTH_EQUITY? Current rule ignores stage/size.
