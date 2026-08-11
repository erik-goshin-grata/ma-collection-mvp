# Spec: Unified Transaction Value & Valuation Model

**Status:** Reconciled against decisions of 2026-08-10. Sections 2 and 3 rewritten.
**Date:** 2026-08-10
**Supersedes/absorbs:** `docs/handoff_bug8_funding_value_semantics.md` (this generalizes it)
**Related:** `docs/decisions.md` (authoritative), `docs/qa_runbook_mergerlinks_2026_08_01.md`,
`mvp_goal_and_schema.md`

> **Source of truth.** `docs/decisions.md` is authoritative on what was decided. This spec
> elaborates and must conform to it. Any conflict resolves to `decisions.md`.

---

## 1. Purpose

Define a single, internally consistent model for transaction value that works identically
across control acquisitions (public and private), partial and minority stakes, and funding
rounds.

The model separates concepts that are currently conflated in extraction, derivation, and the
review sheets:

- **As-transacted values** — what actually changed hands. Deal-specific.
- **100%-basis values** — whole-company valuation, normalized for comparison.

The immediate trigger: audit of `exports/ML_worksheet.csv` and the bug-8 handoff confirmed
that funding and minority **check sizes are being labelled as the company's equity value at
extraction** — `value_type = EQUITY_VALUE` on the raise — and exported verbatim. The derived
`equity_value` column is unaffected; the defect is in the extracted label and what reaches the
review sheets. Stated valuations such as post-money also risk being promoted into the
deal-value field.

**Note on an earlier framing.** The prior version of this spec equated Transaction Value with
deal size, and treated implied equity value as a universal cross-deal comparable. Both were
revised on 2026-08-10. `transaction_value` and `transaction_size` are now distinct fields
(§2.1), and implied equity value is not produced for funding rounds (§2.11).

---

## 2. Value fields

### 2.1 Two tiers

Every value field belongs to exactly one tier. The tier determines whether it may be used as
a multiple numerator. No field appears in both.

#### Tier 1 — As-transacted

What actually changed hands. Deal-specific. **Never a multiple numerator.**

| Field | Definition | Scope |
|---|---|---|
| `equity_value` | Consideration for the stake acquired, at the stake level. Not grossed up. | M&A |
| `transaction_value` | As-reported where stated. Otherwise `equity_value` + total debt at `pct_acquired` ≥ 50, `equity_value` below it. Cash never netted. See §2.1.1. | M&A |
| `transaction_size` | Universal event magnitude across all deal types. See §2.4. | All deals |

#### 2.1.1 Debt follows control

`transaction_value` is recorded as-reported wherever a source states one. Where it is
calculated:

| Condition | `transaction_value` |
|---|---|
| `pct_acquired` < 50 | `equity_value` — no debt added |
| `pct_acquired` ≥ 50 | `equity_value` + total debt |
| `pct_acquired` ≥ 50, debt unknown, nothing stated | null |

Cash is never netted.

**Rationale — TV mirrors consolidation.** A controlling acquirer consolidates the target's
balance sheet and effectively takes on its debt, so adding total debt records something that
happened. A minority buyer takes on none of it; equity-method treatment consolidates nothing.

Below control, `transaction_value` = `equity_value` is a statement about the *transaction* —
no debt transferred — not a claim that the company is debt-free. Above control with debt
unknown, the field goes null rather than assuming debt = 0.

**The threshold is deliberately simple, and wrong in one case.** A step-up from a minority
position into control — 30% to 60%, so `pct_acquired` = 30 — reads as below control and adds
no debt when it should. That case is uncommon and the failure understates rather than
inflates. The alternative is a control-crossing test requiring pre-transaction ownership,
which sources state far less often than the stake acquired, plus a derived flag family with
no other consumer in this model.

**The 50–99% band mixes partial equity with full debt.** At 60%, the calculation adds the
whole company's debt to 60% of its equity. This is the market convention — CIQ's Total
Transaction Value does the same — and is accepted here rather than corrected, because
grossing up in that band would produce a 100%-basis figure duplicating
`implied_enterprise_value` up to cash. `pct_acquired` must be stamped alongside
`transaction_value` wherever it is displayed so the partiality is visible.

`transaction_value` is never a multiple numerator, so the convention does not propagate.

**Migration note:** this changes an existing field. The prior rule added total debt
unconditionally, so previously computed rows below 50% change value. See §4.2.

#### Tier 2 — 100% basis

Whole-company valuation, normalized for comparison. **The only legal multiple numerators.**

| Field | Definition | Scope |
|---|---|---|
| `implied_equity_value` | Equity value on a 100% basis. | M&A |
| `implied_enterprise_value` | `implied_equity_value` + debt − cash. | M&A |

> **"Implied" means 100%-basis, not "derived."**
> A source-stated figure populates these fields exactly as a computed one does.
> "Acquired 27% at an enterprise value of $1.5B" → `implied_enterprise_value` = 1,500,
> method = `as_reported`.
> This is the field most likely to be under-populated through misreading its name.

Funding rounds do not populate Tier 2. See §2.11.

---

### 2.2 One EV field

`enterprise_value` (stake-level) is **removed**. Partial equity plus full debt corresponds to
no economic quantity.

**The defect is currently latent, not live**
`[verified: stages/aggregate.py, stages/export.py, 2026-08-10]`. Multiples strike off the
*stated* enterprise value — `_compute_multiples` gates on `value_type == ENTERPRISE_VALUE`
and reads `value_amount` — so the derived figure never reaches a multiple. It is also absent
from `export.py`, and it only computes where a researcher has supplied `net_debt` manually.

Two changes would make it live, and neither should happen before the rewire:

- **Exporting the derived enterprise value**
- **Pointing multiples at the derived figure instead of the stated one**

All three routes to an EV converge on `implied_enterprise_value`:

| Route | Method flag |
|---|---|
| Source states an EV at a partial stake | `as_reported` |
| Source states an EV generally | `as_reported` |
| Computed from `implied_equity_value` + debt − cash | `calculated` |

At 100% the gross-up is a no-op, so the control case needs no separate field.

**Reconciliation identity (control deals at 100%):**
`transaction_value − cash = implied_enterprise_value`

The identity does not hold below control, where TV carries no debt by design (§2.1.1).

Equity keeps both tiers because a partial equity stake *is* a real quantity — it is what the
buyer paid for their shares. The asymmetry between equity and enterprise value is deliberate.

#### 2.2.1 Stated debt-inclusive figures

Where the source qualifies the basis, the figure routes directly. These are the well-behaved
cases — the qualifier is what distinguishes them from the unqualified figure in §2.4.1.

| Source wording | Control | Field | Method |
|---|---|---|---|
| "$500MM including assumed debt" | Yes | `transaction_value` = 500 | `as_reported` |
| "$500MM including net debt" / "enterprise value of $500MM" | Yes | `implied_enterprise_value` = 500 | `as_reported` |
| "$500MM including assumed debt" | No | `transaction_value` = 500; evidences §2.1.1 row 3 | `as_reported` |
| "enterprise value of $500MM" on a partial stake | No | `implied_enterprise_value` = 500 — describes the *company*, not the stake | `as_reported` |
| "$500MM including debt", gross/net unstated | Either | **Route to review** | — |

**Control decides what the qualifier describes.** Above control, a debt-inclusive figure
describes the transaction, because debt genuinely transferred. Below control it describes the
company, because the minority buyer took on none of it. Identical wording, different field,
resolved by the stake.

**The bare "including debt" case must not be guessed.** The gross/net difference is exactly
the cash balance, which is often material.

**A stated EV does not give you TV for free.**
`transaction_value = implied_enterprise_value + cash`, so TV is derivable only where the cash
balance is available — frequently not the case for private deals. Where cash is absent, TV is
null despite a good stated figure, which leaves `transaction_size` null as well. See §2.10.

#### 2.2.2 Derivation runs both directions

The bridge computes upward from equity as the root primitive. A stated debt-inclusive figure
inverts it:

```
Given TV  →  equity_value = transaction_value − total debt
Given EV  →  implied_equity_value = implied_enterprise_value + cash − debt
```

**Consequence:** `equity_value` may legitimately be null while `transaction_value` or
`implied_enterprise_value` are populated, when the balance sheet is unavailable to invert.
The derivation must permit this rather than treating equity as a required input. This is a
behavioural change from the current implementation, which derives only upward.

---

### 2.3 Multiple numerators

`transaction_multiple.numerator_value_type` changes:

```
- enterprise_value | equity_value
+ implied_enterprise_value | implied_equity_value
```

Both numerators are 100%-basis. As-transacted values are not addressable as numerators, so
the rule holds structurally rather than by convention.

**Minority-derived multiples are excluded from control comps by default.** No structural
separation is needed — `pct_acquired` and the M&A features already identify them. This is a
default-query decision: control deals carry a premium, so blending biases the comps set low.
Opt in explicitly.

---

### 2.4 `transaction_size` population

Derived in aggregation. **Never extracted** — no extractor decides what belongs in this field.

| Deal type | Waterfall | Basis stamp |
|---|---|---|
| M&A | `transaction_value` | `TRANSACTION_VALUE` |
| M&A | → `equity_value`, where equity is stated and debt unknown | `EQUITY_CONSIDERATION` |
| Funding | `round_size` | `ROUND_SIZE` |
| Funding | → sole investor's `investment_amount` | `SOLE_INVESTOR_CHECK` |
| Any | none of the above | null |

`transaction_size_basis` is **NOT NULL whenever `transaction_size` is populated**, and must
travel with the field in every export, sheet, and view.

A third rung — feeding `implied_enterprise_value` into `transaction_size`, control deals only
— is an open item. See §2.10.

#### 2.4.1 The unqualified figure is Tier 1 only

An unqualified "acquired for $500MM" routes to `transaction_value`, and from there to
`transaction_size`. That figure must **not** populate `equity_value`, `implied_equity_value`,
or `implied_enterprise_value`.

The failure mode is not cosmetic. An unqualified figure leaking into `equity_value` grosses up
into `implied_equity_value`, acquires net debt, and yields a multiple manufactured from a
number no source ever qualified — indistinguishable on screen from a real one.

So: **$500MM = `transaction_value` and `transaction_size`. Not equity value, not EV.**

The qualified/unqualified distinction is a property of `transaction_value` and belongs on its
`_method` flag; `transaction_size` inherits it rather than restating it.

#### 2.4.2 Why the funding fallback is restricted to sole-investor rounds

Per-investor disclosure is sparse — lead investor around 30%, other participants under 5%.
Summing whatever `investment_amount` rows happen to exist understates the round systematically
while presenting as a round size, which is worse than null because the shortfall is invisible.

A sole-investor round is the one safe case: the check is the round by definition.
Multi-investor rounds without a stated `round_size` go null.

#### 2.4.3 Aggregation and coverage

**Aggregation constraint:** `transaction_size` must not be summed across bases. A control
acquisition and a minority check are different events; their sum is not a deal-volume figure.
Enforce in the query layer, not in documentation.

**Coverage note:** unqualified figures route into `transaction_value`, so a share of
`TRANSACTION_VALUE`-basis rows carry a figure whose debt-inclusivity is assumed rather than
determined. Public and sponsor deals disclose debt and so are genuinely debt-inclusive;
private deals mostly are not. Ranking `transaction_size` without accounting for this places
private deals systematically low on disclosure, not on size. The `transaction_value._method`
flag is what makes the difference visible.

---

### 2.5 Mandatory companions

| Field | Requirement |
|---|---|
| `pct_acquired` | NOT NULL wherever a Tier 2 value is populated. It is a valuation input, not a descriptor. Defaults to 100 for control event types — see §2.6. |
| `pct_acquired_source` | `stated` \| `assumed`. Diagnostic only; does not suppress the value. |
| `transaction_size_basis` | NOT NULL wherever `transaction_size` is populated. |
| `_method` | Per value field: `as_reported` \| `calculated_from_components` \| `estimated`. |

---

### 2.6 `pct_acquired` is now load-bearing

Tier 2 values gross up by `pct_acquired`, so an error propagates directly into every multiple
struck off that deal.

**The 100% default is retained.** Where the event type conveys control and the source is
silent, default to 100. Silence on an acquisition means whole-company in the large majority of
cases — sources that mean partial nearly always say so. Withholding the default would forfeit
implied values across most of the M&A set to guard against a small error rate.

**Record how it was set.** `pct_acquired_source` ∈ {`stated`, `assumed`}. This suppresses
nothing; it exists so the error rate can be measured rather than debated.

**The default is scoped, not global.** It fires only for control event types. Inherently
partial types must never inherit it — there, silence means *unknown*, and defaulting to 100
converts a minority check into a whole-company purchase.

**Consequence:** the default's accuracy is inherited entirely from the deal-type classifier's
precision on the control/minority boundary. QA effort belongs there, not on the default.

**Guard:** partiality language in the source — *"a stake in"*, *"a majority interest in"*,
*"invested in"* — suppresses the default and routes to review.

---

### 2.7 Divergence as a control-premium signal

Where both a stated 100%-basis EV and a computed one exist, `_divergence_flag` acquires
diagnostic value. A gap means the stake did not price linearly — a control premium, a
preference stack, or an incorrect `pct_acquired`. Each is worth knowing and none is visible
from either number alone.

Route material divergence to review rather than silently preferring the higher-tier source.

**Compare like with like.** Divergence must compare a stated `implied_enterprise_value`
against a *computed* `implied_enterprise_value` — never against `implied_equity_value`. Those
are different concepts, and on a cash-free/debt-free deal the gap between them is exactly net
cash. Comparing across them throws a false positive on every CFDF transaction.

---

### 2.8 Worked examples

Company: 100% equity 1,000 · total debt 500 · cash 0 · EBITDA 150

| Scenario | `equity_value` | `transaction_value` | `implied_equity_value` | `implied_enterprise_value` | EV/EBITDA |
|---|---|---|---|---|---|
| 100% acquisition | 1,000 | 1,500 | 1,000 | 1,500 | 10.0x |
| 27% stake for 270 | 270 | 270 | 1,000 | 1,500 | 10.0x |
| Debt-free, cash-free, 100% | 1,000 | 1,000 | 1,000 | 1,000 | 6.7x |

Row 2 shows both corrections. `transaction_value` is 270, not 770 — no debt transfers below
control (§2.1.1). And the removed stake-level `enterprise_value` would have held 770, implying
5.1x against a true 10.0x.

The 5.1x is **illustrative of the field's incoherence, not of current output** — multiples do
not currently read that field (§2.2). It is what the number would mean if anything consumed
it, which is the reason to remove it rather than wait for something to.

**Public 100%: equity 200, total debt 50, cash 10**

| Field | Value |
|---|---|
| `equity_value` | 200 |
| `transaction_value` | 250 (200 + 50 total debt; cash not netted) |
| `implied_equity_value` | 200 |
| `implied_enterprise_value` | 240 (200 + 50 − 10) |
| `transaction_size` | 250 |

*Revised from the prior version, which gave TV as 240 or 200 depending on source wording. TV
is 250 — it adds total debt and does not net cash. 240 is the enterprise value.*

**Minority 27%, $600M check (Pinnacle Gas)**

| Field | Value |
|---|---|
| `transaction_size` | 600 |
| `transaction_value` | 600 (below the threshold — no debt added) |
| `equity_value` | 600 |
| `implied_equity_value` | ≈ 2,220, basis `GROSSED_UP` |

The gross-up assumes linear pricing and carries no control premium or preference adjustment.
It is an estimate and must be stamped as one.

**Funding: $200M raised at $1B post-money, 20% of the company**

| Field | Value |
|---|---|
| `transaction_size` | 200, basis `ROUND_SIZE` |
| `post_money_valuation` | 1,000 |
| `implied_equity_value` | **null** — see §2.11 |
| `implied_enterprise_value` | **null** — see §2.11 |
| `transaction_value` | null (M&A-scope field) |

`transaction_size` is never 1,000. A valuation does not enter an as-transacted field.

*Revised from the prior version, which mapped post-money to `implied_equity_value`.*

**Everlane — debt-inclusive consideration**

> "…acquire 100% of the equity interest of Everlane, Inc. … total consideration of
> approximately US$80 including the repayment of US$74 of loan."

| Field | Value |
|---|---|
| `transaction_value` | 80 |
| `equity_value` | 6 (80 − 74, by inversion — §2.2.2) |
| `implied_enterprise_value` | null — needs cash, and total debt |
| `transaction_size` | 80 |
| `value_qualifier` | "approximately" |

The rule regenerates the source's own stated figure: 6 + 74 = 80. The trap is `equity_value` —
a naive read records 80, a 13x error. Note also that 74 is *the loan repaid*, not necessarily
total debt.

**CCU / Aguas CCU-Nestlé — partial stake, stated 100% valuation**

> CCU acquired the 49.9% held by Nestlé, taking it to 100%. The SPA considered a 100%
> enterprise value of ~CLP 322,377mm on a cash-free and debt-free basis, giving a purchase
> price at closing of ~CLP 164,597mm.

| Field | Value (CLP mm) |
|---|---|
| `equity_value` | 164,597 — stake level, not grossed up |
| `implied_equity_value` | 329,853 (164,597 ÷ 0.499), basis `GROSSED_UP` |
| `implied_enterprise_value` | 322,377, `as_reported` |
| `transaction_value` | 164,597 — `pct_acquired` 49.9 < 50, so no debt is added |
| `transaction_size` | 164,597 |

The two stated figures reconcile: 329,853 − 322,377 = **7,477 of net cash**, which follows
from the cash-free/debt-free basis.

CCU also illustrates why the simple threshold is tolerable. A control-crossing test would say
no debt attaches because CCU already held 50.1%; the threshold says no debt attaches because
49.9 < 50. Same answer, different reason. The two diverge only on a step-up from a minority
position into control — see §2.1.1.

---

### 2.9 Removed and renamed

| Field | Disposition |
|---|---|
| `enterprise_value` (stake-level) | Removed. Incoherent below 100%; redundant at 100%. Superseded by `implied_enterprise_value`. |
| `enterprise_value_as_reported` / `_calculated` / `_method` / `_divergence_flag` / `_conflict_flag` | Renamed to `implied_enterprise_value_*`. |

---

### 2.10 Open items

1. **Currency mismatch.** `implied_enterprise_value` adds consideration in deal currency to
   net debt in the target's reporting currency. A USD investment into a JPY-reporting target
   sums two currencies. Needs a defined conversion point and FX date *before* the addition.
   **Blocks the implied tier.**
2. **Period coherence.** Net debt anchors to announced date; the multiple denominator carries
   its own `period_basis`. Nothing currently requires them to agree.
3. **EV as a `transaction_size` rung.** A stated EV does not yield a TV without cash
   (§2.2.1), so a deal reporting a clean "$500MM including net debt" carries
   `transaction_size` = null wherever the cash balance is unavailable. A proposed third rung
   would feed `implied_enterprise_value` into `transaction_size` at basis `ENTERPRISE_VALUE`,
   **control deals only** — below control it is the grossed-up whole-company figure and would
   report Pinnacle Gas as 2.22B rather than 600M. Held pending the EV rewire.
4. **Convertible rounds.** §2.11 assumes a funding round has a post-money. Notes and SAFEs do
   not. They populate round size and a cap, which is not a post-money and must not be recorded
   as one. `transaction_security.security_type = CONVERTIBLE_NOTE` identifies them in
   principle; whether that table is populated on the funding path is unconfirmed.

---

### 2.11 Funding scope: post-money only

Funding rounds populate `post_money_valuation` and **not** `implied_equity_value`.

This is enforced structurally rather than by rule. `implied_enterprise_value` is defined as
`implied_equity_value + debt − cash`; with no implied equity for funding rounds, no EV can
compute. "Funding never produces an EV" holds because the inputs do not exist, not because a
rule is remembered.

**Two leaks this closes.** Neither is currently blocked:

1. `implied_enterprise_value` carries no event-type restriction, so post-money flowing into
   `implied_equity_value` would silently attach net debt to a VC post-money valuation.
   `[verified: stages/aggregate.py:399–406, 2026-08-10 — the current code does exactly this]`
2. `numerator_value_type` carries no event-type restriction, so a funding round could produce
   an EV multiple with nothing objecting.

**Consequence:** funding rounds produce no multiples of any kind, since neither legal
numerator is populated. If post-money-based multiples are wanted later,
`post_money_valuation` must be added to `numerator_value_type` as a deliberate third type. It
should not arrive by inheritance.

**Why post-money is not a comparable valuation.** Most rounds are priced in preferred stock,
not common. A $1B post-money on participating preferred with a 2x liquidation preference is
not $1B of common-equivalent value. Because preferred is the base case rather than the
exception, treating post-money as comparable to a control equity value would be wrong on most
rounds, not a few.

**Trade-off accepted:** there is no cross-deal-type equity valuation comparison. Funding
carries a stated post-money; M&A carries implied equity; the two are not placed in a common
column.

**Two mechanisms enforce this, both already true.** `_compute_multiples()` skips calculation
outright when the event type is `VC_ROUND`, `GROWTH_EQUITY` or `VENTURE_DEBT`
`[verified: stages/aggregate.py:288–290, 2026-08-10]`, independently of whether a numerator is
populated. Documented here so the gate is not removed later on the reasoning that the model
constraint covers it. Both should stand.

---

## 3. The bridge

**Merged into §2.** Retained as a numbered heading so that external references to §4 onward
continue to resolve. The two-tier structure in §2.1 and the derivation rules in §2.2.2 replace
what this section described.

---

## 4. Current-state gaps (what to fix)

Line numbers drift. Locate by content, and re-confirm against the repo before editing.

**Live vs latent.** `export.py` surfaces only `value_amount`, `value_currency`, `value_type`,
`per_share_price`, `consideration_type`, `consideration_components_json`
`[verified: stages/export.py, 2026-08-10]`. Gaps touching those fields are live; gaps in
derived valuation fields are latent, because nothing downstream consumes them yet.

1. **Prompt has no rule for a primary capital amount. — LIVE. Highest priority.**
   The `value.type` vocabulary in `prompts/high_confidence_extraction.md` offers four values —
   `EQUITY_VALUE`, `TRANSACTION_VALUE`, `ENTERPRISE_VALUE`, `UNDISCLOSED` — all of which
   describe the purchase of a company. None describes capital invested *into* one. A growth or
   venture raise has no correct option, so the model force-fits, and `EQUITY_VALUE` is the
   natural wrong choice because the money did buy equity.

   **Fix:** route these amounts out before the type vocabulary is reached, via a
   capital-flow precondition — *did this money go into the company, or to a selling
   shareholder?* Phrase the rule on capital flow, **not** on deal type: an M&A purchase of a
   minority stake *should* map its consideration to `EQUITY_VALUE`, so a deal-type-keyed rule
   would break the correct case along with the broken one. A capital-flow rule also survives
   the open Decision #9 boundary without revision.

   **Prerequisite:** the amount must have a capture field before anything is nulled.
   `high_confidence_extraction.md` has none; the funding primitives live in
   `funding_hc_extraction.md` (`round.size`, `round.pre_money_valuation`,
   `round.post_money_valuation`). Confirm whether growth deals route to funding stage 4b before
   adding a field. See `docs/handoff_bug5_funding_clustering.md`.

   Live because `value_amount` and `value_type` export — this is how bug 8 reached
   `ML_worksheet.csv`.

2. **`equity_value` is path-dependent. — LATENT.**
   In `stages/aggregate.py`, the control path stores *stake-level* equity while the funding
   path stores the *100%* figure (post-money). Same column, two meanings.
   **Fix:** make `equity_value` consistently stake-level on every path; post-money belongs in
   `post_money_valuation`.

   Latent because `equity_value` is DB-only and feeds only unexported fields.

   **Migration:** re-aggregate rather than stamp. A deterministic re-run over stored
   primitives, with manual `net_debt` preserved, beats a permanently mixed column that every
   reader must decode. Apply **jointly with the `transaction_value` redefinition** (§2.1.1),
   since both alter partial-stake meaning.

3. **`implied_enterprise_value` does not exist in code. — LATENT. Parked.**
   `_derive_enterprise_value` returns `equity_value + net_debt` with no control gate, fed a
   stake-level equity. Coherent only at 100%.
   **Fix (when unparked):** feed it `implied_equity_value`, rename the output to
   `implied_enterprise_value`, delete the stake-level path, and make the tier boundary
   structural — stake-level `equity_value` is a terminal leaf that never reaches EV or
   multiples.

   **Blocked on §2.10 items 1 and 2** (currency, period coherence), which the tier model names
   as dependencies. Latent today; **the guard is not to export the derived EV and not to
   repoint multiples at it** before the rewire lands.

4. **`net_debt` is manual and unsplit.** Not extracted or derived; researcher-supplied and
   preserved across re-aggregation. **Open:** single `net_debt` vs separate `debt` + `cash`.
   Note that period anchoring is unresolved either way — debt and cash must come from the same
   period, anchored to the announced date, or the bridge is incoherent.

5. **Share count dormant.** The `per_share_price × shares` path is inert because `sec_shares`
   is hardcoded `None`. Public-deal equity value depends on diluted share count from the SEC
   stages. **Fix:** wire SEC share count into the equity derivation.

6. **Valuation fields not exported.** `post_money_valuation` is captured and consumed in
   derivation but absent from `stages/export.py`, as are the derived valuations. **Fix:**
   surface them — but note this is the change that converts gaps 2 and 3 from latent to live,
   so it must follow them, not precede them.

7. **`deal_value_currency` — RESOLVED (§4.7).** Now computed with a mismatch guard and
   persisted in the INSERT. A single currency tag on the derived value fields (precedence
   `valuation_currency` → `value_currency`); **null on a genuine valuation/value mismatch —
   the null is itself the queryable signal, no flag column.** Per-field `*_currency` deferred
   to the §2.10 currency work. See `docs/decisions.md`, "deal_value_currency: single currency
   tag on derived values."

8. **Enum name drift.** `mvp_goal_and_schema.md` says `TOTAL_TRANSACTION_VALUE`; prompt and
   CSVs use `TRANSACTION_VALUE`. **Fix:** pick one canonical enum and align spec, prompt,
   derivation, export. Note that `financials_disclosure_status` (`DISCLOSED` / `UNDISCLOSED` /
   `UNKNOWN`) already exists in `high_confidence_extraction.md`, so the separate disclosure
   axis proposed in QA runbook finding #7 is partly built.

9. **The value object is a single slot. — LIVE.**
   `prompts/high_confidence_extraction.md` carries one `value.amount` and one `value.type`
   `[verified: 2026-08-10]`. Where a source states more than one figure — an equity value and
   an enterprise value, say — the model picks one and the rest is dropped, with nothing
   recording that a choice was made.

   Live for the same reason as gap 1: `value_amount` and `value_type` export.

   **Fix:** named as-reported fields per value type, matching the shape
   `prompts/funding_hc_extraction.md` already uses for funding primitives. See
   `docs/decisions.md`, "Named Value Fields Replace the Single Value Slot."

   This is the mechanism behind gap 1 rather than a separate defect — a single slot forces
   the model to classify rather than record. Gap 1 stops the worst mislabel; this removes the
   thing that produces it.

---

## 5. Extracted vs computed vs manual (target state)

| Field | Origin |
|---|---|
| `value_amount`, `value_currency`, `value_type`, `per_share_price`, `pct_acquired` | LLM-extracted (primitives) |
| `round.size`, `round.pre_money_valuation`, `round.post_money_valuation`, `round.currency` | LLM-extracted (funding primitives) |
| `equity_value` (stake-level), `implied_equity_value`, **`implied_enterprise_value`**, `transaction_size` | **Computed** in `aggregate.py`, each with a `_method` and `_basis` flag |
| `transaction_value` | **Both** — as-reported when a source states one, otherwise computed per §2.1.1 |
| `net_debt` (or `debt` + `cash`) | **Manual** researcher input, preserved across re-aggregation |

The LLM never does arithmetic (locked decision, QA runbook). All aggregates are deterministic
derivations.

`enterprise_value` (stake-level) is removed from this table — see §2.9.

---

## 6. Canonical field question — RESOLVED

*Previously open: whether to keep as-reported deal value canonical, or promote a normalized
`transaction_value` with a waterfall.*

**Resolved by the tier split (§2.1).** Both, in separate roles, with no single scalar:

- As-reported values stay canonical in Tier 1, honest to the deals where a lone untyped
  number is all the source gives.
- Normalized values live in Tier 2 as first-class companions, never overwriting Tier 1.
- `transaction_size` provides the cross-type magnitude with a mandatory basis stamp, so the
  normalization is visible rather than implied.

Type and basis stamps are retained throughout. No collapse to a single scalar.

**Resolved at the aggregation layer only.** Extraction still carries a single `value` slot,
so a source stating multiple figures still forces a choice at capture time. The tier split
governs how captured values are derived and presented; it does not remove the single-slot
constraint upstream of it. See §4 gap 9.

---

## 7. Review-sheet design for the next round

The audit found the review CSVs presented value as a single number with **no inline
definition**, so reviewers picked the biggest figure. Next round:

- **Split the value column into labeled roles** — `transaction_size` (event magnitude),
  `transaction_value` (as-transacted, M&A) and `implied_equity_value` (valuation) — so
  confusion is structurally impossible.
- **Embed definitions in the sheet**, not just in the prompt:
  - *Transaction size = what actually changed hands. For a funding round this is the raise,
    NOT the valuation.*
  - *Implied equity value = 100%-basis valuation. A valuation never goes in an as-transacted
    field. Not produced for funding rounds.*
- **Show the inputs** so reviewers can recompute: `pct_acquired` (and whether stated or
  assumed), round size, post-money, `net_debt` (or `debt` + `cash`), and the basis stamps.
- **Add a dedicated `R_mapping_ok` check** asking the bug-8 question directly:
  *"Is the deal-size figure the consideration/check — not a valuation/post-money?"*
  Distinct from `R_value_ok`, which conflates number-correct with mapping-correct.

`transaction_value` and `transaction_size` are one word apart. Inline definitions are what
prevents that from reproducing the original failure at the column level — the naming alone
will not.

### Open decisions

1. **Net-debt inputs:** single `net_debt` (lower burden) vs `debt` + `cash` separately
   (auditable). See §4.4.
2. **Scope of next round:** funding and minority only (targets the known bug) vs all deal
   types (full accuracy read).

*Previously listed third: whether the system pre-computes implied values or reviewers enter
them. Resolved — implied values are computed in aggregation with a basis flag (§5); reviewers
validate rather than enter.*

---

## 8. Sequencing

Ordered by live-vs-latent, not by section number.

1. **Land this spec and the decisions log together**, so the repo is never internally
   contradictory.
2. **§4.1 — prompt rule. Live, urgent.** Stops new mislabels. Depends only on resolving the
   capture-field prerequisite.
3. **§4.2 + re-aggregation. Latent, cleanup.** Consistent stake-level `equity_value`, applied
   jointly with the `transaction_value` redefinition.
4. **§2.10 items 1 and 2 — currency and period coherence.** These unblock the implied tier.
5. **§4.3 — EV rewire.** Parked until step 4 resolves.
6. **§4.6 — export the valuation fields.** Must follow steps 3 and 5; it is the change that
   makes the latent gaps live.
7. **§4.5, §4.7, §4.8** — share count, currency insert, enum alignment.
8. **Regenerate review sheets** with the new mapping (§7).

---

## Appendix: load-bearing code locations

Verified 2026-08-10. Locate by content; line numbers drift.

- Value type vocabulary: `prompts/high_confidence_extraction.md` — `value.type`, four values
- Funding primitives: `prompts/funding_hc_extraction.md` — `round.size`,
  `round.pre_money_valuation`, `round.post_money_valuation`, `facility_size`
- Downstream value consumer: `prompts/low_confidence_extraction.md` — `DEAL VALUE:` template
  and component reconciliation
- Derivation: `stages/aggregate.py` — `_derive_equity_value`, `_derive_implied_equity`,
  `_derive_enterprise_value`, `_derive_investment_amount`, `_compute_multiples`
- Target securities: `schema/001_initial.sql` — `transaction_security` (`security_type`,
  `security_class`, `consideration_treatment`, `consideration_per_share`). Note a
  `consideration_components` JSON column also exists on `transaction_record`, so consideration
  data has two homes — a divergence risk worth tracking.
- Export: `stages/export.py` — value primitives only; no derived valuations
- Bug-8 origin: `docs/handoff_bug8_funding_value_semantics.md`
- VC/Growth boundary: `docs/handoff_decision9_vc_vs_growth.md` — Decision #9, open
- QA architecture decision: `docs/qa_runbook_mergerlinks_2026_08_01.md`
