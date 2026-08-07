# Review: Bug #8 fix proposal (funding value semantics) — feedback

Reviewer pass on the proposed aggregate + prompt fix, verified against `data/pl_funding.db`.
**Verdict: approve the direction** (type-keyed derivation; `investment_amount` as its own field;
no fallback to check-size). It fixes the real bug (10x confirmed below). But one claim in the
spec is wrong, and there are four correctness items — most from checking the schema/data Claude
asked us to check.

## Audit answers (the "check first, don't guess" items)
| field | staging_extraction | transaction_record | notes |
|---|---|---|---|
| `pre_money_valuation` / `post_money_valuation` | **yes** | **yes** | already added by schema 003 |
| `pct_acquired` | yes | yes | HC prompt extracts it (`Null if 100% or unstated`) |
| `round_size` | yes | yes | funding path |
| `investment_amount` | **NO** | **NO** | must be added — needs a migration |
| `v2_event_type` | yes | yes | aggregate `_FIELDS` carries it |

So: **post/pre-money already exist** — do NOT invent them; wire the existing fields.
**BUT** they're only populated by the **funding** prompt (`funding_hc_extraction.md` L135-139).
The **M&A HC prompt** (`high_confidence_extraction.md`) does **not** extract them, so every
`MINORITY_INVESTMENT` row has `post_money_valuation = NULL` (confirmed: both KG and 10x). ⇒
Claude's prompt point (2) is **required, not conditional** — add pre/post-money extraction to
`high_confidence_extraction.md` too, or the `STATED_POST_MONEY` path can never fire for
minority deals.

`investment_amount` is genuinely new → **add a migration** (e.g. `004_*.sql`) adding it to
`transaction_record` (derived at aggregate, like equity_value/implied_equity_value which also
live only on transaction_record).

The aggregate loader already carries the inputs the new function reads (`v2_event_type`,
`pct_acquired`, `round_size`, `pre/post_money_valuation` are all in `_FIELDS`). Good — no loader
change for inputs, only to add `investment_amount` as an output.

## ❗ Claude's KG regression claim is wrong
Spec says: *"Confirm KG Mobility is unchanged (it already routes through the control-deal
branch)."* KG is **`MINORITY_INVESTMENT`**, which is in Claude's own `NON_CONTROL_INVESTMENT_TYPES`
— so it routes through the **new** branch, not the control branch. Its fixture values:
```
KG Mobility: MINORITY_INVESTMENT, value_amount=$75M, round_size=None, pct_acquired=10.0, post_money=None
```
Under the new logic: no stated valuation, but pct=10 + investment=$75M ⇒
`implied = 75M / 0.10 = $750M` (basis IMPLIED_FROM_PCT). **KG changes** from today's
`equity_value=None / implied=None` to a derived ~$750M. That's arguably *correct* (a $75M-for-10%
convertible does imply ~$750M), but it is **not "unchanged"** — KG is the poster child for the
pct-derivation path, not a no-op regression check. Restate the expected result accordingly.

## ✅ 10x confirmed (the actual bug fix)
```
10x Banking: MINORITY_INVESTMENT, value_amount=$53.7M, round_size=None, pct_acquired=None, post_money=None
```
New logic: no valuation, no pct ⇒ `equity_value=None`, `investment_amount=$53.7M`. Exactly the
intended fix (today it wrongly shows equity_value=implied=$53.7M).

## Correctness items
1. **Reuse the existing type-set.** `stages/aggregate.py` L46 already defines
   `_FUNDING_EVENT_TYPES = {VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT}` (used at L285 to early-return
   in EV derivation). Don't add a parallel `NON_CONTROL_INVESTMENT_TYPES`; define it as
   `_FUNDING_EVENT_TYPES | {"MINORITY_INVESTMENT"}` and keep the EV branch consistent.
2. **equity_value vs implied_equity_value — avoid a double gross-up.** The spec puts the
   pct-derived number (`investment/pct` = whole-company value) into `equity_value` **and** leaves
   the existing `_derive_implied_equity(equity_value, pct)` in place — which would divide by pct
   **again** (`investment/pct/pct`). Decide the semantics explicitly:
   - `investment_amount` = the check (the stake's cost).
   - `equity_value` = stated valuation (post-money) if given, else **None** — do NOT put the
     pct-derived figure here.
   - `implied_equity_value` = post-money if stated, else `investment/pct` if pct present, else None.
   Consolidate so the pct gross-up happens **once**. (The spec says "replace the two derivation
   functions" but only shows one — specify what happens to `_derive_implied_equity`.)
3. **Signature change → update call sites.** Current funcs take positional args
   (`_derive_equity_value(value_amount, value_type, per_share_price, sec_shares)`); the rewrite
   takes `row`. Update the call site (~L846) and anything else that calls them.
4. **M&A HC prompt** must add pre/post-money extraction (see audit) — otherwise minority deals
   with a stated valuation still can't populate `STATED_POST_MONEY`.

## Testing note
`MINORITY_INVESTMENT` rows (KG, 10x) reach aggregate **today**, so #8 is testable now without
#5. The `VC_ROUND`/`VENTURE_DEBT` rows only reach aggregate **after #5 lands** — so the
funding-type half of #8's verify is gated on #5. Sequence #5 → then full #8 verify.

## Corrected acceptance test
- **10x** → `investment_amount=$53.7M`, `equity_value=None`, `implied_equity_value=None`.
- **KG** → `investment_amount=$75M`, `implied_equity_value≈$750M` (IMPLIED_FROM_PCT),
  `equity_value=None` (no stated post-money). *(Not "unchanged.")*
- **Base Power** (#9, VC_ROUND, funding path — after #5): `post_money=$13B` stated ⇒
  `equity_value=$13B` (STATED_POST_MONEY), `investment_amount=$1B`. Good end-to-end check that
  stated-valuation and check-size are kept distinct.
- No row where `equity_value == investment_amount` for a non-control type absent a stated
  valuation.
