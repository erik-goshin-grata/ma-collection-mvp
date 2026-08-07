# Handoff: Bug #8 — funding/minority "check size" mislabeled as equity value

**Repo:** `ma-collection-mvp` · **Stages:** 4 `high_confidence_extraction.md` (value_type) →
9 `stages/aggregate.py` (`_derive_equity_value` / `_derive_implied_equity`)
**Severity:** medium — produces a wrong valuation on minority/funding deals routed to the M&A path.

## Symptom
`10x Banking` raised **£40M (US$53.7M)** from AshGrove Capital. Routed to `MINORITY_INVESTMENT`,
its `transaction_record` came out as:
```
value_type = EQUITY_VALUE
value_amount = 53,700,000
equity_value = 53,700,000        # STATED
implied_equity_value = 53,700,000
consideration_type = CASH
```
i.e. the **amount raised** was recorded as the **company's equity value**. That's wrong — $53.7M
is the round/check size, not 10x Banking's valuation.

Contrast `KG Mobility` (Chery $75M convertible): `value_type = TRANSACTION_VALUE`,
`equity_value = None` — handled correctly. So the two diverge on `value_type`.

## Root cause — two layers
1. **Extraction (`high_confidence_extraction.md`)** assigns `value_type`. The prompt defines
   `EQUITY_VALUE` = "equity purchase price / per-share × shares aggregate" and `TRANSACTION_VALUE`
   = "total consideration". It has **no rule for a primary funding/round amount** (a capital
   injection into the company), so the model inconsistently tags the raise as `EQUITY_VALUE`
   (10x) or `TRANSACTION_VALUE` (KG).
2. **Aggregate derivation (`stages/aggregate.py`)** then trusts that:
   ```python
   def _derive_equity_value(value_amount, value_type, per_share_price, sec_shares):
       if value_type == "EQUITY_VALUE" and value_amount and value_amount > 0:
           return float(value_amount), "STATED"      # <-- check size becomes equity_value
       ...
   def _derive_implied_equity(equity_value, pct_acquired):
       if pct_acquired and 0 < pct_acquired < 100:
           return round(equity_value / (pct_acquired/100), 2)
       return equity_value                             # pct null -> unchanged (= check size)
   ```
   With `pct_acquired` null, `implied_equity_value` is left equal to the check size too.

## Why it matters
For a minority stake, the correct implied valuation is `check / pct_acquired` — and if no
percentage is stated, **no valuation is derivable** and `equity_value` should be NULL (what KG
did). Reporting the check as the company's equity value overstates nothing for a control deal
but is meaningless/misleading for minority and funding events.

## Fix direction
- **Extraction/classification:** for `MINORITY_INVESTMENT` and funding types, do **not** map the
  invested/round amount to `value_type = EQUITY_VALUE`. Capture it as the **investment/round
  amount** (funding path already has `round_size`; for minority, an `investment_amount` primitive
  + `pct_acquired`). Add an explicit prompt rule distinguishing "amount invested" from
  "company equity value".
- **Aggregate guard:** `_derive_equity_value` should not set `equity_value` from a minority
  investment's check absent a stated valuation. Prefer: derive `implied_equity_value =
  investment_amount / pct_acquired` only when `pct_acquired` is present; otherwise leave equity
  fields NULL. Consider gating STATED-equity to control deals (ACQUISITION/MERGER/TAKE_PRIVATE).

## Decision
How to represent minority/funding economics in the schema + export:
`investment_amount` (+ `pct_acquired` → implied valuation) vs the acquisition-shaped
`equity_value`/`implied_equity_value`. Ties into the funding review-sheet format
(recipient / round / amount / investors, **not** target/acquirer/EV).

## Verify
Re-aggregate `data/pl_funding.db`; confirm minority/funding rows no longer carry
`equity_value = value_amount` when no stake %/valuation is stated (KG-style behavior becomes the
rule, not the exception).

## Related
Compounds with #5 (funding never even reaches aggregate today) and #9 (VC vs GROWTH_EQUITY).
