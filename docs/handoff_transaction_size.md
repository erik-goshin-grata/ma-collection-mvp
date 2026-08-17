# Handoff: Build `transaction_size` + surface the value fields

**Status:** **LANDED 2026-08-17.** Retained as the reasoning of record; the shipped
contract is in `docs/decisions.md`, "transaction_size: Family-Keyed Waterfall, Two Rungs
Reserved". Three things changed between this plan and what shipped, each for a reason
recorded there: the **M&A equity fallback rung was removed** (every safe case already
produces `transaction_value`; the rung's reachable set was exactly the cases with
unknown transaction scope), the **basis vocabulary adopted the Grata spellings**
(`ROUND_SIZE`, `SOLE_INVESTOR_AMOUNT`, `SPIN_SPLIT_CONSIDERATION_VALUE`), and the
**sole-investor rung is reserved rather than live** because no per-investor amount
column exists.
**Spec:** `docs/spec_transaction_value_model.md` §2.4, §7
**Decisions:** `docs/decisions.md` — "Transaction Size as Universal Magnitude"

Delivers the review-sheet fix that the value-model work exists to enable. Lower priority than
§4.1 and §4.2, but it is the only item in the batch a reviewer ever sees.

---

## Dependency — SATISFIED 2026-08-17

The blocker was: *do not start before §4.2 lands*, because the M&A waterfall reads
`transaction_value`, whose rule changes in §4.2, and building against the pre-§4.2 behaviour
means rebuilding after.

**Discharged.** §4.2 code landed, and the owed second re-aggregation was executed and accepted
as Path A on 2026-08-17 (`docs/runbook_second_reaggregation.md` §8): 92 → 92 transactions, all
99 staging rows re-derived under the observation read path, stake-level `equity_value` and the
typed-value picker live on the whole corpus. `transaction_value` now behaves as the waterfall
assumes, so the waterfall can be built against settled inputs.

`handoff_stake_level_equity_value.md` was the prerequisite; its joint re-aggregation is the
same Path A run.

**One caveat carries forward.** Aggregation is still incremental — Path A re-derived the corpus
as it stood, but nothing prevents future rows from being derived under different semantics. A
`transaction_size` backfill should not assume every row was produced by one derivation vintage.

**Not discharged, and not a blocker for this work:** the balance-sheet half. The corpus carries
zero `total_debt` / `cash_st` values (Path B deferred), so the debt-inclusive
`transaction_value` rung has never fired on real data. The waterfall's *behaviour* on that rung
is specified and fixture-tested; only its live exercise is outstanding.

---

## Scope

**In scope:** the `transaction_size` derivation in `stages/aggregate.py`, its basis stamp, the
DB column, and the export surface described below.

**Out of scope:**

- **EV as a `transaction_size` rung (spec §2.10 item 3) — still parked.** §2.10 items 1–2
  landed on 2026-08-17: currency mismatch is now guarded (an amount and its qualifier are
  anchored to the same fact and source; debt-inclusive arithmetic refuses unless both
  currencies are known and equal; no internal FX), and period coherence is enforced
  (`POINT_IN_TIME` plus an exact `balance_sheet_as_of_date` shared by both components).
  **That does not unpark item 3.** It was held for an independent reason — below control,
  `implied_enterprise_value` is the grossed-up whole-company figure and would report Pinnacle
  Gas as 2.22B rather than 600M. The gross-up problem is unaffected by currency or period
  work and remains unresolved, so **do not add the EV rung** (see Change 1).
- Named `*_as_reported` value fields — decided, no handoff
- Deal-type taxonomy
- Any change to how `equity_value`, `transaction_value` or the implied tier are computed

---

## Change 1 — Derive `transaction_size`

Computed in aggregation. **Never extracted** — no extractor decides what belongs in it.

| Deal type | Waterfall | `transaction_size_basis` |
|---|---|---|
| M&A | `transaction_value` | `TRANSACTION_VALUE` |
| M&A | → `equity_value`, where equity is stated and debt unknown | `EQUITY_CONSIDERATION` |
| Funding | `round_size` | `ROUND_SIZE` |
| Funding | → sole investor's `investment_amount` | `SOLE_INVESTOR_CHECK` |
| Any | none of the above | null |

`transaction_size_basis` is **NOT NULL wherever `transaction_size` is populated.**

**The sole-investor restriction is deliberate.** Per-investor disclosure runs around 30% for
leads and under 5% for other participants, so summing whatever `investment_amount` rows exist
understates the round while presenting as a round size — worse than null, because the shortfall
is invisible. Use the check only where the round has exactly one disclosed investor.

**Do not add an `implied_enterprise_value` rung.** It was proposed and parked (spec §2.10 item
3). Below control it is the grossed-up whole-company figure and would report Pinnacle Gas as
2.22B rather than 600M.

---

## Change 2 — Aggregation constraint

`transaction_size` **must not be summed across bases.** A control acquisition and a minority
check are different events; their sum is not a deal-volume figure.

Enforce in the query layer, not in documentation. Either block the aggregate or group by basis.

---

## Change 3 — Export

Surface four fields, each with its stamp:

| Column | Stamp |
|---|---|
| `transaction_size` | `transaction_size_basis` |
| `equity_value` | — |
| `implied_equity_value` | basis (`GROSSED_UP` / `STATED` / …) |
| `implied_enterprise_value` | `_method` (`as_reported` / `calculated`) |

Plus the inputs a reviewer needs to recompute: `pct_acquired` with its `stated`/`assumed`
source, `round_size`, `post_money_valuation`, and `net_debt` or `debt` + `cash`.

**Nulls in `implied_enterprise_value` are the researcher work queue.** `as_reported` means
done, `calculated` means derived, null means that row needs debt and cash supplied. The export
should make that legible rather than leaving researchers to hunt.

**Guard — do not export the derived enterprise value.** `implied_enterprise_value` is the
Tier 2 field; the stake-level `enterprise_value` is the parked defect and must stay unexported
until the rewire lands. Exporting it is one of the two changes that turns that defect from
latent to live.

---

## Change 4 — Sheet definitions

Per spec §7, embed definitions in the sheet itself, not only in the prompt:

> **Transaction size** — what actually changed hands. For a funding round this is the raise,
> NOT the valuation.
>
> **Implied equity value** — 100%-basis valuation. A valuation never goes in an as-transacted
> field. Not produced for funding rounds.

`transaction_value` and `transaction_size` are one word apart. The inline definitions are what
prevent that reproducing the original failure at the column level — the naming alone will not.

Add `R_mapping_ok` as a distinct reviewer check: *"Is the deal-size figure the
consideration/check — not a valuation/post-money?"* Separate from `R_value_ok`, which conflates
number-correct with mapping-correct.

---

## Acceptance tests

### Funding round

> $200M raised at $1B post-money.

| Field | Expected |
|---|---|
| `transaction_size` | 200,000,000 |
| `transaction_size_basis` | `ROUND_SIZE` |

Never 1,000,000,000. A valuation does not enter an as-transacted field.

### Minority stake acquisition

> 27% acquired for $600M.

| Field | Expected |
|---|---|
| `transaction_size` | 600,000,000 |
| `transaction_size_basis` | `TRANSACTION_VALUE` |

600, not the grossed-up 2.22B.

### Control acquisition, debt known

> 100% acquisition. Equity 200, total debt 50.

| Field | Expected |
|---|---|
| `transaction_size` | 250 |
| `transaction_size_basis` | `TRANSACTION_VALUE` |

### Control acquisition, debt unknown

> 100% acquisition for 200. No debt figure.

| Field | Expected |
|---|---|
| `transaction_size` | 200 |
| `transaction_size_basis` | `TRANSACTION_VALUE` |

TV is 200 here with `transaction_value_basis=EQUITY_VALUE_ONLY` (§2.1.1 — do not assume
debt = 0), so the waterfall consumes transaction value normally. This is the case that
shows why the TV basis stamp is mandatory: 200 and 250 above are the same kind of number
only if you know which rung produced them.

### Multi-investor round, no stated round size

> Three investors named, one check disclosed at $40M, no total stated.

| Field | Expected |
|---|---|
| `transaction_size` | **null** |

Not 40,000,000. The sole-investor fallback does not apply.

---

## Verification

- `transaction_size_basis` is non-null on every row where `transaction_size` is populated
- No funding row carries a `transaction_size` equal to its `post_money_valuation`
- No M&A row below `pct_acquired` 50 carries a `transaction_size` exceeding its `equity_value`
- The export contains no stake-level `enterprise_value` column
