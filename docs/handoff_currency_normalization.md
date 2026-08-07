# Handoff: currency normalization (cross-cutting — NOT funding-specific)

**Repo:** `ma-collection-mvp` · **Surfaced by:** DeepX (funding) during the #8 work, but the
defect spans **all deal types and financial items**. Severity: high for any non-USD deal —
produces silently-wrong values (e.g. a phantom multi-trillion valuation).

## Symptom
- **DeepX** (VC_ROUND): source (Yahoo Finance) states *"valued at ~3.14 trillion won ($2.2
  billion)… securing 42 billion won"*. Pipeline stored `post_money_valuation=3.14e12`,
  `round_size=4.2e10`, `valuation_currency=KRW` — correct won magnitudes — but the derived
  `equity_value`/`investment_amount` carry those magnitudes with **no currency**, so downstream
  reads **$3.14T / $42B** instead of **$2.2B / ~$30M**.
- **G City** (ACQUISITION): `value_currency=ILS`, `value_amount=2.55e9`, `equity_value=2.55e9` →
  read as **$2.55B**, actually ILS 2.55B (~$690M). *Plain M&A, no funding involved.*

## Scope (from data/pl_funding.db, all current transactions)
- `value_currency`: 11 USD, **3 INR, 1 JPY, 1 ILS, 1 EGP**, 74 null (+ DeepX KRW valuation,
  + 1 EUR funding round). Non-USD deals already appear across ACQUISITION / MINORITY_INVESTMENT
  / RECAPITALIZATION / VC_ROUND.
- **No `*_usd` / normalized columns exist** on `transaction_record`.

### In scope vs. out of scope
- **In scope — derived deal-value fields:** `equity_value`, `implied_equity_value`,
  `enterprise_value`, `investment_amount`. These drop the currency and are the actual defect.
- **OUT of scope — multiples:** already correct. `_compute_multiples` skips funding, requires
  ENTERPRISE_VALUE, and flags a value/financials **currency mismatch as `NM`**. A multiple is a
  dimensionless ratio, so same-currency pairs (even EUR/EUR) compute correctly with **no
  conversion and no tag needed**. (Minor edge: `currency_mismatch` only trips when *both*
  currencies are non-null; a null `financials_currency` relies on the plausible-range guard as
  backstop — optional hardening, not urgent.)
- **OUT of scope — raw financial items:** `target_revenue`/`target_ebitda` already carry
  `financials_currency`; they're tagged. Only their derived multiple is affected, and that's
  guarded (above).

## Root cause — where it lives (this is cross-cutting, not one stage)
| Layer | Gap |
|---|---|
| Schema | Raw monetary fields carry currency (`value_currency`, `valuation_currency`, `financials_currency`); **derived** fields (`equity_value`, `implied_equity_value`, `enterprise_value`, `investment_amount`) carry **none**. No USD-normalized variants. |
| Aggregate (`stages/aggregate.py`) | Derivation propagates the magnitude but not the currency for the deal-value fields. (Multiples are already correct — same-currency check + NM on mismatch; not part of this fix.) |
| Extraction (`high_confidence_extraction.md`, `funding_hc_extraction.md`) | Should **prefer a stated USD figure** when the source provides one (DeepX headline had "$2.2 billion") instead of the local-currency number. |
| Missing dependency | True USD conversion needs an **FX-rate source with an as-of date** — the repo has none (same class as the SEC share-price / market-data gaps in the runbook). |

## Decision: TAG-AND-DEFER (chosen)
Attach a currency to every derived monetary field and **never assume USD**; do **not** convert
yet. This is cheap because the currency is **already captured on the raw fields** — the work is
only to *propagate* it, not to source it:
- Derived-value currency source: `valuation_currency` for funding (post-money-based) values,
  `value_currency` for control-deal values. (For a minority pct-derived implied value, the
  currency follows the investment amount's currency.)
- Add a currency companion for the derived fields (either per-field `*_currency` columns or a
  single `deal_value_currency` on `transaction_record` — schema-shape TBD; per-field is safest
  if value and financials currencies can differ).
- Export / review sheet must render the currency next to every amount.
- Extraction should still **prefer a stated USD figure** when the source gives one (DeepX title
  had "$2.2 billion") — reduces how often a non-USD tag is even needed.

**Deferred (explicitly out of scope for tag-and-defer):** actual USD conversion / `*_usd`
fields / an FX-rate layer (rate source + as-of = announced_date). Revisit as a separate build
once an FX source is chosen; nothing above depends on it.

## Interim safety
Until this lands: any consumer of `equity_value` / `implied_equity_value` / `enterprise_value` /
`investment_amount` must NOT assume USD. The 6+ non-USD rows in the current fixture (DeepX KRW,
G City ILS, INR/JPY/EGP/EUR) are wrong-by-currency today.

## Note on #8
Bug #8's check-vs-equity logic is correct and independent of this — #8 faithfully derives from
whatever currency the primitives are in. This currency gap pre-dates #8 (equity_value/EV never
had a currency); funding + international coverage just made it visible. Fixing here does not
require reopening #8.
