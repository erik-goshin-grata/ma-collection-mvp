# Handoff: Stake-level `equity_value` + joint re-aggregation (spec §4.2)

**Status:** LATENT defect. Cleanup — schedule after the primary-capital rule ships.
**Spec:** `docs/spec_transaction_value_model.md` §4.2, §2.1.1
**Decisions:** `docs/decisions.md` — "Transaction Value Follows Control", and the
`equity_value` migration entry
**Split from:** the combined §4.1/§4.2 handoff. §4.1 is live and has its own doc —
`handoff_hc_value_capture.md`. **Do that one first.**

> **BLOCKED.** Change 2 depends on the M&A control features (`is_minority`,
> `pre_existing_control`, `acquires_remaining`), which are **absent from the repo** — decided in
> `docs/decisions.md` but never built. Because Changes 1 and 2 must re-aggregate jointly, the
> whole of §4.2 waits on them. See "Blocking dependency" below.

---

## Why this is cleanup, not urgent

`equity_value` is DB-only. `export.py` surfaces only `value_amount`, `value_currency`,
`value_type`, `per_share_price`, `consideration_type`, `consideration_components_json`
`[verified: stages/export.py, 2026-08-10]`.

So the mixed-semantics column feeds only unexported fields, and multiples read the *stated*
enterprise value rather than any derived figure `[verified: _compute_multiples gates on
value_type == ENTERPRISE_VALUE and reads value_amount, stages/aggregate.py, 2026-08-10]`.

Nothing live consumes the wrong values. That is what makes this schedulable rather than urgent
— and also what makes the re-aggregation cheap, since there is no downstream to invalidate.

---

## Scope

Two changes that must ship **together**, because both alter the stored meaning of partial-stake
deals:

1. `equity_value` becomes consistently stake-level on every path
2. `transaction_value` becomes control-conditional

Then one operation:

3. Joint re-aggregation

**Out of scope. Do not touch:**

- The EV rewire — `_derive_enterprise_value`, `implied_enterprise_value`, the stake-level EV
  path. Parked, blocked on currency and period coherence (spec §2.10 items 1–2).
- `stages/export.py`
- `transaction_multiple.numerator_value_type`
- Deal-type taxonomy

**Guard:** do not export derived valuations and do not repoint multiples at the derived
enterprise value. Either change converts the parked EV defect from latent to live before its
fix exists.

---

## Change 1 — `equity_value` consistently stake-level

Currently the control path stores *stake-level* equity while the funding path stores the
*100%* figure (post-money). One column, two meanings, silently mixed.
`[verified: stages/aggregate.py:399–406, 2026-08-10]`

Required:

- `equity_value` is **always stake-level** — the consideration for the stake actually
  acquired, never grossed up, on every path.
- Post-money belongs in `post_money_valuation` and must not be written to `equity_value` on
  any path.
- Do not add or modify `implied_equity_value` / `implied_enterprise_value` derivation as part
  of this change.

Note that funding rounds do not populate `implied_equity_value` at all under the current model
(spec §2.11). This change removes the write to `equity_value`; it does not relocate it to the
implied tier.

---

## Change 2 — `transaction_value` follows control

| Condition | `transaction_value` |
|---|---|
| Control obtained by this transaction | `equity_value` + gross debt |
| Below control, or control already held | `equity_value` |
| Below control, source states debt assumed/refinanced | `equity_value` + stated assumed debt |

Cash is **not** netted. `transaction_value` = `equity_value` + gross debt, so
`transaction_value − cash = implied_enterprise_value` at 100% control.

**The control test is not `pct_acquired ≥ 50`.** A holder at 50.1% acquiring the remaining
49.9% obtains no control and consolidates nothing new. The test is whether *this transaction*
causes the acquirer to newly obtain control.

### Blocking dependency

The M&A features this depends on — `is_minority`, `pre_existing_control`, `acquires_remaining`
— are recorded in `docs/decisions.md` but **absent from the repo entirely**
`[verified: 2026-08-10]`. Without them the control test cannot be evaluated, so Change 2
cannot compute. And because Changes 1 and 2 must re-aggregate jointly (running between them
produces a third semantics), **all of §4.2 is blocked**, not just Change 2.

**Do not substitute a `pct_acquired ≥ 50` threshold.** That threshold is the thing this change
exists to stop using — the CCU test below is the case it gets wrong.

**Decision required:** build the control features first, or defer §4.2 entirely. Neither is the
implementer's call.

Debt in row 3 must come from stated deal terms, not from pulling `TOTAL_DEBT` off the balance
sheet because the company carries some.

---

## Change 3 — Joint re-aggregation

**Decided: re-aggregate. Do not stamp.**

Both changes alter what stored rows mean for partial-stake deals. A deterministic re-run over
already-stored primitives produces one clean semantics; stamping leaves a permanently mixed
column that every future reader must decode.

- This is a re-run of the aggregation stage, **not** an LLM re-extraction.
- Manual `net_debt` is preserved across re-aggregation `[verified: stages/aggregate.py,
  2026-08-10]`. Confirm this holds before running.
- Run it once, covering both changes. Do not re-aggregate between them — that produces an
  intermediate state with a third semantics.

Reserve stamping only if re-aggregation proves non-deterministic or too costly at current
volume.

---

## Acceptance tests

### CCU / Aguas CCU-Nestlé — stake-level, not grossed up

> CCU acquired the 49.9% held by Nestlé, taking it to 100%. SPA considered a 100% enterprise
> value of ~CLP 322,377mm on a cash-free and debt-free basis, giving a purchase price at
> closing of ~CLP 164,597mm.

| Field | Expected |
|---|---|
| `equity_value` | 164,597 — **not** 329,853 |
| `transaction_value` | 164,597 — no debt attaches; CCU already held 50.1% |
| `pct_acquired` | 49.9, stated |
| `value_currency` | CLP |

Tests both changes at once, and is the canonical case for the control test not being a 50%
threshold.

### Funding round — no 100% figure in `equity_value`

> $200M raised at a $1B post-money valuation.

| Field | Expected |
|---|---|
| `equity_value` | null — **not** 1,000 |
| `post_money_valuation` | 1,000 |

### Everlane — guard assertion only

> "…total consideration of approximately US$80 including the repayment of US$74 of loan."

| Field | Expected here | After debt-netting lands |
|---|---|---|
| `equity_value` | **null — and never 80** | 6 (80 − 74) |

Deriving 6 requires inverting a debt-inclusive consideration (spec §2.2.2), which is out of
scope. Assert only that `equity_value` is never 80 — that still guards the 13x error, and the
"including" construction producing it recurs constantly in IFRS disclosures. Promote to `= 6`
when debt-netting is implemented.

---

## Verification after re-aggregation

- No `equity_value` exceeds its deal's `post_money_valuation` where both are present
- No `equity_value` on a funding-path row equals that row's `post_money_valuation`
- `transaction_value` equals `equity_value` on every row where control was not obtained
- Manual `net_debt` values are unchanged from before the run
