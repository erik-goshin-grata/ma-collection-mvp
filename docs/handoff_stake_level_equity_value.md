# Handoff: Stake-level `equity_value` + TV threshold + joint re-aggregation (spec §4.2)

**Status:** LATENT defect. **Unblocked** — schedule after `handoff_hc_value_capture.md`.
**Spec:** `docs/spec_transaction_value_model.md` §4.2, §2.1.1 (as amended)
**Decisions:** `docs/decisions.md` — "Transaction Value Follows Control" (as amended)
**Prerequisite:** the TV threshold amendments must land before this is implemented.

> **Previously blocked, now cleared.** An earlier version of this handoff waited on the M&A
> control features (`is_minority`, `pre_existing_control`, `acquires_remaining`), which do not
> exist in the repo. The TV rule now uses `pct_acquired ≥ 50`, which is already extracted, so
> those features are no longer a dependency. They may still be built for comps segmentation and
> filtering, but nothing here waits on them.

---

## Why this is cleanup, not urgent

`equity_value` is DB-only. `export.py` surfaces only `value_amount`, `value_currency`,
`value_type`, `per_share_price`, `consideration_type`, `consideration_components_json`
`[verified: stages/export.py, 2026-08-10]`.

The mixed-semantics column feeds only unexported fields, and multiples read the *stated*
enterprise value rather than any derived figure `[verified: _compute_multiples gates on
event type at stages/aggregate.py:288–290, then on value_type, 2026-08-10]`.

Nothing live consumes the wrong values. That makes this schedulable rather than urgent, and it
makes the re-aggregation cheap — there is no downstream to invalidate.

---

## Scope

Two changes that must ship **together**, because both alter the stored meaning of partial-stake
deals, then one operation:

1. `equity_value` becomes consistently stake-level on every path
2. `transaction_value` follows the `pct_acquired ≥ 50` threshold
3. Joint re-aggregation

**Out of scope. Do not touch:**

- The EV rewire — `_derive_enterprise_value`, `implied_enterprise_value`, the stake-level EV
  path. Parked, blocked on currency and period coherence (spec §2.10 items 1–2).
- `stages/export.py`
- `transaction_multiple.numerator_value_type`
- Deal-type taxonomy
- Named `*_as_reported` value fields — separate decision, no handoff yet
- The M&A control-flag family — no longer a dependency, and not part of this change

**Guard:** do not export derived valuations and do not repoint multiples at the derived
enterprise value. Either converts the parked EV defect from latent to live before its fix exists.

---

## Change 1 — `equity_value` consistently stake-level

The control path stores *stake-level* equity while the funding path stores the *100%* figure
(post-money). One column, two meanings, silently mixed
`[verified: stages/aggregate.py:399–406, 2026-08-10]`.

Required:

- `equity_value` is **always stake-level** — the consideration for the stake actually acquired,
  never grossed up, on every path.
- Post-money belongs in `post_money_valuation` and must not be written to `equity_value` on any
  path.
- Do not add or modify `implied_equity_value` / `implied_enterprise_value` derivation here.

Funding rounds do not populate `implied_equity_value` at all under the current model (spec
§2.11). This change removes the write to `equity_value`; it does not relocate it to the implied
tier.

---

## Change 2 — `transaction_value` threshold

| Condition | `transaction_value` |
|---|---|
| Source states one | As-reported. Takes precedence. |
| `pct_acquired` < 50 | `equity_value` — no debt added |
| `pct_acquired` ≥ 50 | `equity_value` + gross debt |
| `pct_acquired` ≥ 50, debt unknown, nothing stated | **null** — do not assume debt = 0 |

Cash is never netted.

Uses `pct_acquired` only. No pre-transaction ownership, no control flags.

**Gross debt, not net.** `implied_enterprise_value` consumes net debt; this consumes gross. A
row carrying only `net_debt` yields an EV and no calculated TV. That is expected, not a bug.

**Known limitation, do not "fix" it.** A step-up from a minority position into control — 30% to
60%, `pct_acquired` = 30 — reads as below control and adds no debt. Accepted deliberately: the
case is uncommon and the failure understates. And the 50–99% band adds full company debt to a
partial equity stake, which is the market convention rather than an oversight. Both are
documented in §2.1.1.

---

## Change 3 — Joint re-aggregation

**Decided: re-aggregate. Do not stamp.**

Both changes alter what stored rows mean for partial-stake deals. A deterministic re-run over
already-stored primitives produces one clean semantics; stamping leaves a permanently mixed
column that every future reader must decode.

- This is a re-run of the aggregation stage, **not** an LLM re-extraction.
- Manual `net_debt` is preserved across re-aggregation `[verified: stages/aggregate.py,
  2026-08-10]`. Confirm this holds before running.
- Run once, covering both changes. Do not re-aggregate between them — that produces an
  intermediate state with a third semantics.

Reserve stamping only if re-aggregation proves non-deterministic or too costly at current volume.

---

## Acceptance tests

### CCU / Aguas CCU-Nestlé — stake-level, and below the threshold

> CCU acquired the 49.9% held by Nestlé, taking it to 100%. The SPA considered a 100%
> enterprise value of ~CLP 322,377mm on a cash-free and debt-free basis, giving a purchase
> price at closing of ~CLP 164,597mm.

| Field | Expected |
|---|---|
| `equity_value` | 164,597 — **not** 329,853 |
| `transaction_value` | 164,597 — `pct_acquired` 49.9 < 50, no debt added |
| `pct_acquired` | 49.9, stated |
| `value_currency` | CLP |

Tests both changes. Note the threshold gets this right for a different reason than the
control-crossing test would have — the stake is below 50, and control was also already held.

### Funding round — no 100% figure in `equity_value`

> $200M raised at a $1B post-money valuation.

| Field | Expected |
|---|---|
| `equity_value` | null — **not** 1,000 |
| `post_money_valuation` | 1,000 |
| `transaction_value` | null — M&A-scope field |

### Control acquisition with debt known

> 100% acquisition. Equity consideration 200, gross debt 50, cash 10.

| Field | Expected |
|---|---|
| `equity_value` | 200 |
| `transaction_value` | 250 — debt added, cash not netted |
| `implied_enterprise_value` | 240 — if the EV path were live; out of scope here |

### Control acquisition with debt unknown

> 100% acquisition for 200. No debt figure, no debt-inclusive statement.

| Field | Expected |
|---|---|
| `equity_value` | 200 |
| `transaction_value` | **null** |

The null is the point. TV = 200 would assert debt = 0.

### Everlane — as-reported takes precedence, and reconciles

> "…total consideration of approximately US$80 including the repayment of US$74 of loan."

| Field | Expected here | After debt-netting lands |
|---|---|---|
| `transaction_value` | 80, as-reported | 80 |
| `equity_value` | **null — and never 80** | 6 (80 − 74) |

Deriving 6 requires inverting a debt-inclusive consideration (spec §2.2.2), which is out of
scope. Assert only that `equity_value` is never 80 — that still guards the 13x error. Promote
to `= 6` when debt-netting is implemented, at which point the calculated TV of 6 + 74 = 80
should reconcile with the as-reported 80.

---

## Verification after re-aggregation

- No `equity_value` on a funding-path row equals that row's `post_money_valuation`
- `transaction_value` equals `equity_value` on every row where `pct_acquired` < 50
- `transaction_value` is null on every row where `pct_acquired` ≥ 50, gross debt is absent,
  and no debt-inclusive figure was stated
- Manual `net_debt` values are unchanged from before the run
