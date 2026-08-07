# Review: Bug #5 fix proposal (funding clustering) — feedback

Reviewer pass on the proposed `entity_cluster` funding branch, validated against the real
duplicate pairs in `data/pl_funding.db`. **Verdict: approve the shape** (recipient-centric
match, soft-conflict checks, separate `lead_investor` field, module-top tunables) with four
correctness fixes and one honest scoping note below.

## What the data proved
Pulled the actual duplicate rows. OLIX is the real stress test (5 rows, not 2):

| eid | v2_event_type | round_label | round_size | announced_date |
|-----|---------------|-------------|-----------|----------------|
| 25  | MINORITY_INVESTMENT | — | — | 2026-08-03 |
| 35  | VC_ROUND | Series B | $312.0M | 2026-08-03 |
| 48  | VC_ROUND | Series B | $312.0M | 2026-08-03 |
| 68  | VC_ROUND | Series B | $270.5M | 2026-08-03 |
| 106 | VC_ROUND | Series A | $220.0M | 2026-02 (month) |

Valar Atomics: 3 VC_ROUND rows, same date, sizes {$1B, $1B, null}, labels {Series B, null, null}.

The soft-conflict rules do the right thing:
- **Merge** OLIX 35+48+68 (Series B; $270.5M vs $312M = **13.3%**, inside a 0.15 tolerance).
- **Keep separate** OLIX 106 (label `Series A` ≠ `Series B`, and months earlier).
- **Merge** all 3 Valar rows (null label / null size just skip the soft checks).

So `label_compatible()` is load-bearing — it's the thing preventing a company's Series A from
collapsing into its Series B.

## Must-fix (correctness)
1. **Amount check must use `round_size`, not `value_amount`.** `value_amount` is NULL on all 68
   funding rows; `round_size` is the populated field. As written the amount conflict check would
   never fire.
2. **Date handling must tolerate precision + nulls.** eid 106's date is `'2026-02'`
   (`announced_date_precision='month'`) — `date.fromisoformat('2026-02')` throws; 1/68 rows has a
   null date. Make the date check **soft**: only block on a conflict when *both* rows are
   `precision='exact'`; skip (treat as compatible) for month/year/null. Do not make it a hard gate.
3. **Widen the Stage 8 SELECT.** Currently pulls only `target_name, acquirer_name,
   announced_date`. Add `v2_event_type, round_label, round_size, announced_date_precision`.
4. **Gate `funding_match` on both rows being funding.** Use it only when *both* `v2_event_type`
   ∈ {VC_ROUND, VENTURE_DEBT, GROWTH_EQUITY}; otherwise keep the existing target+acquirer logic.
   (Prevents a funding row and an M&A row from matching through the wrong branch.)

## Scoping note (state the acceptance test honestly)
OLIX eid 25 is `MINORITY_INVESTMENT` for the *same company/date* as the four VC_ROUND rows — the
same raise was typed two different ways by different sources. Because #5 clusters *within* the
funding path, eid 25 goes down the M&A path, so **OLIX yields two transactions** (one minority,
one VC_ROUND). That's a classifier-consistency issue, not a clustering bug — out of scope for #5.
State the acceptance criterion as: *funding-path duplicates collapse* — **not** "all OLIX rows
become one deal."

**Acceptance test:** after the fix, Valar's 3 rows → 1 transaction; OLIX's 3 Series B rows → 1
transaction; OLIX Series A (106) stays separate; OLIX MINORITY (25) stays separate; and no
funding row with a non-null `target_name` remains `LC_EXTRACTED`.

## Tunable calibration (from the observed deltas)
- `FUNDING_DATE_WINDOW_DAYS`: duplicates are all 0-day deltas here, so 21–30 is safe/unstressed;
  the real separator was label + precision, not the window.
- `FUNDING_AMOUNT_TOLERANCE`: **0.15 is the floor** — the legit $270.5M/$312M merge is 13.3%, so
  ≤0.13 wrongly splits it. 0.15–0.20 is right; don't go tighter.
- `label_compatible()`: closed-vocab lookup (good). **Treat null label as compatible** (soft) —
  Valar has a null-label row that must still merge with its Series B sibling.

## lead_investor
Keeping it out of `acquirer_name` is correct (avoids the #8 seam). Two practicals:
- `staging_investor.is_lead` is sparse → fall back to `lead_investor_rank`, then first-listed.
- It needs a landing column on `transaction_record` (none today) — coordinate with the #8 /
  funding review-sheet work so funding output shows investors, not an acquirer.
