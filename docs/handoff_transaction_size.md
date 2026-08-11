# Handoff: Build `transaction_size` + surface the value fields

**Status:** Not started. Sequenced **after** §4.2.
**Spec:** `docs/spec_transaction_value_model.md` §2.4, §7
**Decisions:** `docs/decisions.md` — "Transaction Size as Universal Magnitude"

Delivers the review-sheet fix that the value-model work exists to enable. Lower priority than
§4.1 and §4.2, but it is the only item in the batch a reviewer ever sees.

---

## Dependency

**Do not start before §4.2 lands.** The M&A waterfall reads `transaction_value`, whose rule
changes in §4.2. Building against the current behaviour means rebuilding after.

`handoff_stake_level_equity_value.md` is the prerequisite. Its joint re-aggregation should
complete first, so `transaction_size` derives from settled inputs.

---

## Scope

**In scope:** the `transaction_size` derivation in `stages/aggregate.py`, its basis stamp, the
DB column, and the export surface described below.

**Out of scope:**

- The EV rewire — still parked on currency and period coherence (spec §2.10 items 1–2)
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
| `transaction_size_basis` | `EQUITY_CONSIDERATION` |

TV is null here (§2.1.1 — do not assume debt = 0), so the waterfall falls to the second rung.
This is the case that shows why the basis stamp is mandatory: 200 and 250 above are the same
kind of number only if you know which rung produced them.

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
