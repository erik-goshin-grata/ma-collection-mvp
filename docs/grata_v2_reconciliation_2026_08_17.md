# Grata V2 Reconciliation — Harness Delta as of 2026-08-17

**Status:** Redline against `grata_v2_inventory_and_recommendations.md` v0.3 and
`grata_v2_data_dictionary.md` v0.3, both written 2026-08-13.
**Basis:** repo state at `c2ef492`, plus the Path A corpus re-aggregation executed and
accepted 2026-08-17 (`docs/runbook_second_reaggregation.md` §8).
**Purpose:** separate what the harness has actually built and proven from what is still
a recommendation, so Grata ENG can act on the first and schedule the second.

**Scope discipline.** Harness-specific mechanics are not Grata requirements. The Stage 9
SQL rewrite, the observation table's physical shape, the body-quality ingestion filter,
and the `AGGREGATION_READ_SOURCE` flag are all implementation. Where one of them is cited
below it is because it forced a *semantic* rule to the surface — an ownership or
provenance principle that any implementation of this model has to satisfy — and the rule
is stated in those terms, not in SQL.

---

## 1. What changed since v0.3

v0.3 was written against a model that was specified but largely unexercised. Since then
the harness has landed six changes and run the whole corpus through them once. The
material effect on the Grata recommendations is in four places:

1. **Several v0.3 recommendations are now backed by working code and a corpus run**, not
   only by design argument. They move from "proposed" to "adopt, with evidence."
2. **Two v0.3 rules are incomplete as written.** The canonical EV rule (D2 / dictionary
   §7) requires period coherence but is silent on currency coherence, and it does not
   prohibit backsolving net debt. Both gaps are live in a schema that retains
   `ENTERPRISE_VALUE` and `EQUITY_VALUE` alongside the implied tier.
3. **Two basis vocabularies have drifted** between the Grata docs and the harness. One is
   missing a rung; the other names the same rungs differently in two places.
4. **The balance-sheet half of the model is built but has never run on real data.** Every
   debt/cash claim below is fixture-validated only, and is labelled as such. This is the
   single largest honesty caveat in the document.

---

## 2. Real-corpus evidence base

Everything in §3 labelled *validated* traces to this run. Everything not traceable to it
is labelled *fixture-only* or *not implemented*.

| Measure | Value |
|---|---|
| Transactions before / after Path A | 92 → **92** (no cluster merged or split) |
| Staging rows re-derived | 98 `AGGREGATED`, 1 `PROMPT_FAILED` held back |
| Read path | observation ledger (per-fact), corpus-wide |
| Rows carrying `net_debt` | **0** |
| Rows carrying `total_debt` or `cash_st` | **0** |
| Enterprise values | 4, **all `STATED`**; 0 calculated |
| Rows at `EQUITY_PLUS_TOTAL_DEBT` | **0** |
| Currency-gap quantifier at-risk rows | **0** |
| Qualifier losses from anchoring | **0** |

**Two of these numbers are easy to misread.**

*Zero anchoring losses is not evidence the anchoring rule is inert.* It means no row in
this corpus had a qualifier borrowed from a different source than its own amount. The
defect the rule prevents is reproduced on demand by
`scripts/test_currency_period_anchoring.py`, where a borrowed `period_end` manufactures a
5.0x multiple from figures that never described the same period.

*Zero debt rows is not a passing test of the debt path.* It means the path has never
executed. Prompt 0.17 can request debt and cash and Stage 4a now persists them, but every
existing staging row predates that capability, and re-extracting the corpus purely to
manufacture a case was rejected as unjustified (Path B deferred). The debt-inclusive
bases, the calculated EV bases, and the component-coherence guards are all
**fixture-validated only**.

**Positive evidence that is real.** Two rows show the typed-value fix working on live
data: Anysphere at $60.0B and Payoneer at $2.75B now carry `equity_value` with
`transaction_value_basis = EQUITY_VALUE_ONLY`, where the single-slot value object
previously collapsed them. `EQUITY_VALUE_ONLY` records *debt unknown*, never debt = 0 —
which is precisely why the basis stamp has to exist. Dahl retains a 0.76x multiple,
showing the anchoring rule does not indiscriminately destroy supported qualifiers.

---

## 3. The fourteen reconciliation items

Categories: **[1] Implemented + validated** in the harness · **[2] Recommended for Grata
ENG** · **[3] Already adequate in Grata** · **[4] Deferred / not yet live-validated.**
An item can carry more than one, and several do — the point of the split is that
"we built it" and "Grata should adopt it" and "we proved it works" are three different
claims that v0.3 ran together.

### 1. Per-fact typed observations

**[1] [2]** Each extracted fact is stored with its own key —
`(staging_extraction_id, source_raw_id, observation_fact_key)` — so a source stating an
equity value *and* an enterprise value yields two independent facts rather than one
winner. Live on the whole corpus since Path A.

*Grata implication (genuine, not mechanical):* provenance must be recorded at **fact**
granularity, not row granularity. `financial_metric` is already a repeating table, so the
cardinality is there; what the dictionary does not show is per-row source attribution or
a fact key. Without them, two figures from one article are indistinguishable from the
same figure corroborated by two articles — a difference that changes confidence, not just
lineage. **ADD source attribution + fact key to `financial_metric`.**

### 2. Observation-backed aggregation

**[1] [4]** Canonical values derive from the retained observation set rather than from a
pre-collapsed extraction row. Default since `abd8464`; exercised corpus-wide in Path A.

*Grata implication:* the architecture is a harness choice and **not** a schema
requirement. The transferable principle is narrower and worth stating: **a canonical
value must remain re-derivable from retained observations**, so a rule change can be
replayed rather than re-collected. Grata's normalized `financial_metric` already permits
this. **[3] adequate structurally**; the gap is only whether collapsed Silver scalars are
treated as the system of record (see §5, open question 4).

### 3. Tier 1 value basis and provenance — `equity_value`, `transaction_value`

**[1] [2]** — with one **redline**.

The harness implements `transaction_value_basis` with **four** rungs:

| Rung | Meaning |
|---|---|
| `STATED` | source stated a transaction value |
| `EQUITY_BELOW_CONTROL` | `pct_acquired < 50`; equity consideration, no debt |
| `EQUITY_PLUS_TOTAL_DEBT` | `pct >= 50`, debt known, currencies known and equal |
| `EQUITY_VALUE_ONLY` | `pct >= 50`, debt unknown or unusable |

**Grata D3 lists three, and names one differently.** It omits `EQUITY_BELOW_CONTROL`
entirely, and writes `EQUITY_VALUE_PLUS_TOTAL_DEBT` where the harness has
`EQUITY_PLUS_TOTAL_DEBT`.

The omission matters more than the name. Below control there is no debt to add — the
figure is the stake consideration and nothing else — and folding that into
`EQUITY_VALUE_ONLY` merges "debt does not apply here" with "debt applies but is unknown."
Those are different data-quality states: the second is a research queue item, the first is
complete. **ADD the fourth rung; pick one spelling for the third.**

`equity_value_basis` (`STATED` / `PER_SHARE_X_SHARES`) exists in the harness and has **no
counterpart** in dictionary §7, which lists `transaction_value_basis` and
`implied_equity_value_basis` but no equity basis. **ADD it.** `PER_SHARE_X_SHARES` is
currently dormant in the harness (the SEC share count is not wired), so this is **[4]**
for validation but **[2]** for schema.

**Amended 2026-08-17 — `equity_value` scope is now enforced, and Grata needs the same
two rules.** The harness found that two writers could put a whole-company figure into
stake-level `equity_value`, and that nothing downstream could tell. Both are now closed,
and both generalise beyond this harness:

- **A market capitalization is not an equity value.** It is a property of the company,
  not of the transaction — nothing was bought at that price. It is now its own type,
  `MARKET_CAPITALIZATION`, retained as a fact but never routed into consideration. It
  does **not** belong in `implied_equity_value` either: that field is the valuation the
  *deal* implies, and conflating it with what the market says would make the Tier 2
  figure mean two different things.
  → **Grata: add `MARKET_CAPITALIZATION` to `MetricType`, classified `COMPANY_FINANCIAL`
  rather than `DEAL_VALUE`** (D0 / dictionary §8). That classification *is* the fix:
  it is a company property, so it inherits period semantics rather than value-basis
  semantics, and it can never be a canonical deal value.
- **`PER_SHARE_X_SHARES` is 100%-basis by construction** — per-share price times the
  target's *total* diluted count prices the whole company. It is admitted only at
  `pct_acquired == 100`, and is **never scaled** by pct below that, because the pipeline
  holds total shares and never acquired shares.
  → **Grata: a calculated equity value must record which scope its inputs had.** Any
  `share_count x price` derivation needs the same gate, or the basis stamp asserts a
  scope the number does not have.

The general principle, and the one worth carrying into the dictionary: **a field with a
declared scope must have every writer be that scope by construction.** `equity_value`
feeds a gross-up that divides by pct, so a whole-company input is grossed a second time
— 2.2B at pct 27 becomes 8.15B of implied equity. `value_type` was already the natural
scope discriminator; it was merely under-specified, which is why the fix needed no new
column.

### 4. Tier 2 value basis and provenance — `implied_equity_value`, `implied_enterprise_value`

**[1] [3] [4]** The harness vocabulary matches Grata D3 and dictionary §7 exactly:
`STATED` / `GROSSED_UP_FROM_EQUITY_VALUE` for implied equity, and `STATED` /
`IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT` / `IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT` for
implied EV. **No change recommended — the v0.3 recommendation is correct.**

Validation is split: the `STATED` rung is live (4 corpus rows). Both calculated rungs are
**fixture-only** — they cannot fire without debt or cash, and the corpus has neither.

The load-bearing rule underneath is validated independently: stake-level `equity_value` is
a **terminal Tier 1 leaf** and never reaches the implied tier or a multiple numerator.
This is what makes Grata's `NumeratorValueType` recommendation (F1: `implied_*` rather
than `equity_value` / `enterprise_value`) correct rather than cosmetic — a stake-level
numerator over a whole-company denominator is a category error, not a rounding issue.

### 5. `transaction_size` + `transaction_size_basis`

**[1] [2] — implemented 2026-08-17.** Grata D1 marks it ADD/Missing, which is right;
the harness now has it. **The vocabulary drift below was resolved in Grata's favour**,
so the two models now agree — with two substantive corrections *to* the Grata waterfall,
below.

The shipped contract (`docs/decisions.md`, "transaction_size: Family-Keyed Waterfall,
Two Rungs Reserved"):

| Family | Rung | Basis |
|---|---|---|
| M&A (`ACQUISITION`/`MERGER`/`REVERSE_MERGER`) | `transaction_value` | `TRANSACTION_VALUE` |
| Funding (`VC_ROUND`/`GROWTH_EQUITY`/`VENTURE_DEBT`) | `round_size` | `ROUND_SIZE` |
| Spin/Split | *reserved, no live rung* | — |
| everything else | — | null |

**Correction 1 to Grata D4 — strike the M&A equity fallback.** D4 lists "M&A fallback →
`equity_value` where equity is stated and debt is unknown". That condition cannot be
reached: whenever equity is stated and `pct_acquired` is known, `transaction_value` is
already populated (debt-unknown merely stamps it `EQUITY_VALUE_ONLY`), so the primary
rung fires and returns the same number. The *only* states with `transaction_value` null
and `equity_value` known are those where **`pct_acquired` is unknown** — i.e. the
transaction scope is unknown, so the equity figure may be the whole company. The rung's
reachable set is exactly the unsafe complement of its intended one. **Recommend Grata
remove it.**

**Correction 2 — strike `SOLE_INVESTOR_AMOUNT` from the `transaction_size_basis`
vocabulary entirely.** Grata D3 lists it and D4 gives it a rung ("Funding fallback → one
sole investor's `transaction_party.investment_amount` only when it is the only safe
disclosed check"). **Remove both.** An investor's check is not the event's magnitude:
reporting a $50M check as a $100M round's size is wrong regardless of how many investors
disclosed, so this was never a disclosure-threshold problem that a sole-investor
restriction could fix. When the round total is undisclosed, the honest `transaction_size`
is **null**.

This does not diminish D5 — `investment_amount` remains correct as **investor-level
supplemental detail on `transaction_party`**, expected null for most deals. It simply
never rolls up. The settled contract: a $100M round with Firm A investing $50M gives
`round_size = 100M`, `transaction_size = 100M` at basis `ROUND_SIZE`, and Firm A's
`investment_amount = 50M` — nothing added, nothing substituted, in either direction.

`SPIN_SPLIT_CONSIDERATION_VALUE` remains reserved for the opposite reason: the concept is
right but no source field exists yet, and it additionally yields null for every pure
pro-rata spin, since nothing changes hands for value there.

Two rules both models already share, and neither should weaken: the basis is **required
wherever the size is populated** (Grata J2), and the size **must not be summed across
bases** (D4). One caveat worth adding to Grata's version of the latter: **the basis
alone does not separate a below-control M&A from a control one** — both stamp
`TRANSACTION_VALUE` — so a grouping key that wants that distinction needs `is_minority`
or `pct_acquired` alongside it.

**Redline: the basis vocabulary was inconsistent across three documents — RESOLVED
2026-08-17 in Grata's favour.** The harness shipped the Grata spellings, so the table
below is now historical. It is retained because it shows what was reconciled and why the
Grata names won: every canonical value names the **source field** that supplied the
magnitude, which `EQUITY_CONSIDERATION` and `SOLE_INVESTOR_CHECK` did not.

| Rung | Grata D3 / D4 / dictionary §7 | `handoff_transaction_size.md` |
|---|---|---|
| M&A primary | `TRANSACTION_VALUE` | `TRANSACTION_VALUE` |
| M&A fallback | `EQUITY_VALUE` | `EQUITY_CONSIDERATION` |
| Funding primary | `ROUND_SIZE` | `ROUND_SIZE` |
| Funding fallback | `SOLE_INVESTOR_AMOUNT` | `SOLE_INVESTOR_CHECK` |
| Spin/Split | `SPIN_SPLIT_CONSIDERATION_VALUE` | *(absent)* |

Two rungs disagree and one is missing from the harness plan. **Recommend adopting the
Grata spellings** — `transaction_size` is a Grata product concept and the harness is the
implementer, so Grata's vocabulary should win — and adding the Spin/Split rung to the
harness waterfall before it is built, not after. Flagged now precisely because the
implementation has not started; this is the last cheap moment to settle it.

The equity rung in that table (`EQUITY_VALUE` / `EQUITY_CONSIDERATION`) was **removed
entirely** rather than renamed — see Correction 1 above. The naming dispute on it
dissolved with the rung.

**Still parked, and not unblocked by anything in this pass:** the proposed EV rung (spec
§2.10 item 3). Below control, `implied_enterprise_value` is the grossed-up whole-company
figure and would report a 27%-for-$600M deal as $2.22B. The currency and period work
landed; the gross-up problem is independent of both and remains open.

### 6. Target financial metrics vs deal-value metrics

**[3] [1]** Grata D0 / dictionary §8 classify each `MetricType` as `DEAL_VALUE` or
`COMPANY_FINANCIAL`, derived from `metric_type` rather than stored. **This is correct and
needs no change.** The harness enforces the same separation structurally, and Path A
confirmed the practical consequence D0 predicts: the two classes need different
applicability rules. Company financials are meaningless without period metadata; deal
values are meaningless without basis metadata. A single "is this filled in?" QA rule over
both classes would be wrong in both directions.

### 7. `total_debt` / `cash_st` / `net_debt`

**[1] [3] [4]** All three exist in Grata's `MetricType` and are marked KEEP. The harness
now extracts total debt and cash (prompt 0.17), persists them (Stage 4a), and derives net
debt with reported-preferred precedence. **Fixture-validated only** — zero live rows.

Two rules worth carrying into the Grata dictionary as explicit text, because both are
error modes that fail *silently*:

- **`total_debt` is gross, never net.** The prompt refuses a net figure offered as total
  debt and routes it to notes instead. A net figure landing in `total_debt` corrupts
  every downstream derivation with no signal that anything went wrong.
- **Missing cash is never zero.** Grata D2 already says this; it should also appear on
  the `cash_and_equivalents` dictionary row, where a reader looking up the field will
  actually see it.

**Naming and scope.** Grata D1 marks `cash_and_equivalents` "KEEP / DEFINE ECONOMIC
SCOPE." The harness definition is: *cash and cash equivalents plus short-term and
marketable investments, as one combined figure, not split into components.* **Recommend
Grata adopt this definition verbatim** under its own field name `cash_and_equivalents`;
the harness column is `cash_st` and should be treated as the same concept under a
different local name, not a second field. This closes D1's open scope note.

### 8. `POINT_IN_TIME` + exact as-of date, never LTM/TTM

**[1] [3]** Grata's `period_type` enum already includes `POINT_IN_TIME` (dictionary §9),
so **no enum change is needed**.

What is missing is the **QA contract**: a balance-sheet metric type
(`TOTAL_DEBT`, `CASH_AND_EQUIVALENTS`, `NET_DEBT`, `SHAREHOLDERS_EQUITY`) must carry
`period_type = POINT_IN_TIME` with an **exact** `period_end_date` and
`period_end_date_precision = exact`. A balance sheet covers no period — it is a position
on one date — so `LTM`, `TTM`, `NTM`, `ANNUAL`, and `QUARTERLY` are all category errors on
these types. The trap the harness prompt names explicitly is the seductive one: recording
the *filing's* period label (annual, quarterly) instead of the balance sheet's own date.
That describes where the figure was found, not what it measures.

The harness **derives** `POINT_IN_TIME` rather than extracting it, so the model cannot
mislabel it. **Recommend the same for Grata: derive from `metric_type`, do not collect.**
Add the rule to J2's requiredness matrix.

### 9. Currency attached to the value it qualifies

**[1] [2]** A currency or period travels with the amount it qualifies, anchored to that
amount's own fact and source. Unstated means null — never borrowed from a sibling.
Live corpus-wide since Path A.

*Grata status:* `financial_metric.value_currency` is per-row, so the structure is
**[3] already correct**. The gap is a derivation rule, not a column: **a metric row must
never inherit a currency, period type, or period end date from another row**, including
rows on the same transaction and rows from the same source. Silence is null.

This is the item with the most direct evidence behind it. The fixture in
`scripts/test_currency_period_anchoring.py` shows a `period_end` borrowed across sources
manufacturing a 5.0x multiple out of figures that never described the same period — a
number that looks entirely ordinary in a review sheet and is unfalsifiable without going
back to both sources.

**This is also exactly Grata's own Silver parity finding K1** — `reported_revenue` and
`reported_ebitda` are period-untagged scalars. The harness hit the same defect class and
found it produces plausible wrong numbers rather than visible gaps. **Recommend raising
K1 from a parity item to a correctness item.**

### 10. No debt-inclusive arithmetic on unknown or mismatched currency

**[1] [2] — redline: this rule is missing from Grata D2 and dictionary §7.**

Both documents require net debt components to be *period-coherent*. Neither requires them
to be **currency-coherent**, and neither says what to do when a currency is unknown.

The harness gate is a single predicate used by every calculation that mixes consideration
with a balance-sheet figure: **both currencies known, and equal.** Unknown on either side
does not calculate. Known-but-differing does not calculate. Applied to
`total_debt − cash_and_equivalents`, to `equity_value + total_debt`, and to
`implied_equity_value + net_debt`.

The asymmetry is deliberate and should be stated in the Grata rule: **an unknown currency
is insufficient evidence, not permission to assume agreement.** The tempting reading —
"probably both USD" — is the one that produces a wrong number with no marker on it. There
is no plausible-range check on enterprise value that would catch a JPY balance sheet added
to a USD purchase price.

A `STATED` value is exempt, because it is one source-stated figure rather than a sum. The
guard applies to arithmetic, not to observation.

**Recommend: add currency coherence alongside period coherence in D2 and dictionary §7,
with the unknown-is-not-a-match clause spelled out.**

### 11. No internal FX conversion

**[1] [2]** The harness performs no currency conversion anywhere — not in extraction, not
in derivation. A conversion needs an FX rate and an FX date, and inventing either
manufactures precision the sources do not support.

*Grata status:* `financial_metric` carries `fx_rate` and `fx_rate_date`, both KEEP. **No
schema change needed.** The recommendation is semantic: **those fields record a conversion
the source or a researcher actually performed, never one the pipeline invented.** A
derived canonical value must not be produced by an implicit conversion — where currencies
differ and no stated conversion exists, the correct output is null.

Stated plainly because the fields' mere existence reads as a licence to convert.

### 12. No backsolving `net_debt = EV − equity_value`

**[1] [2] — redline: not prohibited anywhere in Grata v0.3.**

The harness derives net debt by exactly two paths: reported, or
`total_debt − cash_and_equivalents` under the coherence guards. There is no third path,
and the arithmetic inverse is never attempted.

**This needs to be an explicit prohibition in Grata, not an omission**, because the schema
makes the backsolve available and plausible: `ENTERPRISE_VALUE` survives as a
compatibility observation type, `EQUITY_VALUE` is canonical, and subtracting one from the
other looks like free data. It is wrong twice over:

1. `EQUITY_VALUE` is **stake-level** and `ENTERPRISE_VALUE` is **whole-company**. The
   difference is not net debt; on a minority deal it is mostly the un-acquired stake.
2. Even at 100%, a stated EV and a stated equity value routinely come from different
   sources and different dates. Their difference is a residual containing every
   inconsistency between them, and it would be stored as a balance-sheet fact.

A backsolved net debt is indistinguishable from a reported one once written. **Recommend
adding the prohibition to D2 and the `net_debt` dictionary row.**

### 13. Source-stated USD preference

**[1] [2]** Where a source states the same figure in both a local currency and USD —
*"3.14 trillion won ($2.2 billion)"* — the harness takes the **stated USD figure** and
sets the currency to USD. It never converts to produce one. Applies to deal values and
balance-sheet figures alike.

This is the constructive complement to item 11: it is how the pipeline gets comparable
USD figures without an FX engine. The source did the conversion, at a rate and date it
chose, and stating that it did is a fact about the source.

*Grata status:* no equivalent rule in v0.3. **[2] ADD as a collection rule.** It also
gives `value_usd_basis` (dictionary §10, "exact semantics to verify") a candidate
definition — see §5, open question 3.

### 14. Preservation of multiple independently sourced facts

**[1] [3]** Two independently sourced facts of the same kind survive independently rather
than one overwriting the other. This is what the per-fact source key buys, and it is what
the typed-value fix restored: Anysphere and Payoneer are live evidence, previously
collapsed by the single-slot value object.

*Grata status:* `financial_metric` is repeating, so the model **already permits** this —
**[3] adequate**. Two things still need to be true and are not visible in v0.3:

- Rows must carry enough provenance to tell *corroboration* (two sources, same figure)
  from *multiplicity* (one source, two different figures). Both are legitimate; they mean
  opposite things about confidence. This is item 1's fact key and source attribution.
- **Supersession must be a defined operation.**

  > **CORRECTED 2026-08-18.** This paragraph previously read: *"The harness ledger is
  > append-oriented, so a re-extracted value adds a row rather than replacing one, and
  > there is no `is_current` handling."* **That was wrong on both counts**, and the error
  > understated what the harness had already decided. Verified against the code:
  > `transaction_field_observation` **has** an `is_current` column
  > (`schema/001_initial.sql`); `stages/agreement_extract.py` **writes** it, soft-deleting
  > a document's prior observations inside a savepoint before re-extracting
  > (`UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?`);
  > and Stage 9 **honours** it (`WHERE tfo.is_current = 1`).

  The accurate statement: supersession **is** a defined operation in the harness, but it is
  defined for **one producer and scoped to `source_document_id`**. Re-extracting an
  agreement document supersedes that document's facts atomically. There is **no equivalent
  keyed on `source_raw_id`** — press-release re-extraction, which is the Path B case.

  So the question Grata faces is not "append or supersede?" but **"what is the supersession
  key?"** — and the two keys behave differently: a filed document is immutable once filed,
  a web source is not. See §5 open question 1, and
  `grata_v2_inventory_and_recommendations.md` §O1.

---

## 4. Recommendations to Grata ENG — Adopt / Keep / Defer

### Adopt

1. **Currency coherence in the canonical EV rule.** Add to D2 and dictionary §7, beside
   period coherence: debt-inclusive arithmetic requires both currencies known and equal.
   *Unknown is not a match.* — item 10
2. **Prohibit backsolving net debt** from `EV − equity_value`, explicitly, on the
   `net_debt` dictionary row. Stake-level minus whole-company is not a balance-sheet
   fact. — item 12
3. **Fourth `transaction_value_basis` rung: `EQUITY_BELOW_CONTROL`.** "Debt does not
   apply" and "debt unknown" are different data-quality states and must not merge into
   `EQUITY_VALUE_ONLY`. Settle the `EQUITY_PLUS_TOTAL_DEBT` /
   `EQUITY_VALUE_PLUS_TOTAL_DEBT` spelling at the same time. — item 3
4. **`equity_value_basis`** (`STATED` / `PER_SHARE_X_SHARES`) — dictionary §7 has bases
   for transaction value and implied equity but none for equity value. — item 3
4b. **`MARKET_CAPITALIZATION` as a `MetricType`, classified `COMPANY_FINANCIAL`.** A
   market cap is a property of the company, not the transaction; classifying it outside
   `DEAL_VALUE` is what structurally prevents it becoming a canonical deal value. Pair
   it with the scope rule: every writer into a scope-declared field must be that scope
   by construction, and a `share_count x price` derivation is 100%-basis unless gated
   to full acquisition. — item 3 (amended)
5. **Per-fact provenance on `financial_metric`**: source attribution plus a fact key, so
   corroboration is distinguishable from multiplicity. — items 1, 14
6. **The qualifier-anchoring rule as a derivation constraint**: a metric row never
   inherits currency or period from another row. Unstated is null. — item 9
7. **`cash_and_equivalents` economic scope**, closing D1's open note: cash and cash
   equivalents plus short-term and marketable investments, one combined figure, not split.
   — item 7
8. **Source-stated USD preference** as a collection rule; never a pipeline conversion.
   — items 11, 13
9. **`POINT_IN_TIME` as a derived QA contract** on balance-sheet metric types, with exact
   date and `precision = exact`, in J2. Derive from `metric_type`; do not collect. — item 8
10. **Raise Silver parity item K1** (period-untagged `reported_revenue` / `reported_ebitda`)
    from parity to correctness. The harness hit this defect class and it produces plausible
    wrong multiples, not visible gaps. — item 9

### Keep — correct in v0.3, no change

- **Tier 2 basis vocabularies** (D3, dictionary §7). The harness matches them exactly.
  — item 4
- **`implied_enterprise_value` as the single canonical whole-company EV**, with
  `ENTERPRISE_VALUE` demoted to a compatibility observation type. — item 4
- **`NumeratorValueType` → `implied_*`.** Stake-level values are never multiple
  numerators. — item 4
- **DEAL_VALUE / COMPANY_FINANCIAL classification derived from `metric_type`**, no stored
  category column. — item 6
- **`total_debt` / `cash_and_equivalents` / `net_debt` as `MetricType` members**, reported
  net debt preferred, missing never zero. — item 7
- **`period_type` including `POINT_IN_TIME`.** The enum is right; only the QA rule is
  missing. — item 8
- **`fx_rate` / `fx_rate_date`.** Right fields; add the semantic that they record a
  performed conversion, never license an implicit one. — item 11
- **`transaction_size_basis` required whenever `transaction_size` is populated** (J2), and
  **never summed across bases** (D4). — item 5
- **`investment_amount` investor-level on `transaction_party`**, `ROUND_SIZE` as the
  funding transaction magnitude. — D5, unchanged by this pass.

### Defer

- ~~**`transaction_size` implementation.**~~ **Built 2026-08-17** with the Grata
  vocabulary; see item 5 for the two corrections it implies *to* Grata's D4 waterfall
  (strike the M&A equity fallback; treat the two reserved rungs as blocked on their
  source fields). — item 5
- **The EV `transaction_size` rung** (spec §2.10 item 3). Currency and period coherence
  landed and did **not** unpark it — the below-control gross-up problem is independent and
  unresolved. — item 5
- **Live validation of every debt/cash path.** Extraction, derivation, and both calculated
  EV bases are fixture-validated only. Zero corpus rows. Defer the claim, not the code.
  — items 7, 10
- **`PER_SHARE_X_SHARES` validation.** Implemented but dormant; the SEC share count is not
  wired into equity derivation. — item 3
- **Observation-ledger supersession — for `source_raw_id`-keyed observations only.**
  *(Corrected 2026-08-18: not "undecided in the harness".)* Document-scoped supersession is
  **built and live** — `is_current`, written by agreement re-extraction, honoured by
  Stage 9. What is deferred is the source-scoped key, which press-release re-extraction
  needs and which does not exist. Grata meets the same split on first re-collection.
  — item 14
- **P/TBV denominator enrichment, recap/IPO redesign, field-level null reasons** — v0.3's
  §M deferrals stand unchanged; nothing in this pass touches them.

---

## 5. Remaining unresolved Grata schema questions

1. **Supersession — the open question is the KEY, not the operation.**
   *(Restated 2026-08-18. The earlier framing — "the harness ledger appends and has no
   `is_current`" — was factually wrong; see item 14 for the correction and the code
   references.)*

   The harness **has** supersession: `transaction_field_observation.is_current`, written by
   agreement re-extraction scoped to `source_document_id`, and honoured by Stage 9's
   aggregation read. What it does not have is a supersession key for `source_raw_id`
   observations, so re-extracting a press release adds rows and supersedes nothing.

   The question for both sides is therefore: **when a transaction is re-collected and a
   metric changes, what scope does the new value supersede?** A filed document is immutable
   and document-scoped supersession is safe; a web source can change under the same URL and
   needs a different rule. This still blocks any large re-extraction and is still the item
   most likely to need a decision before the next one runs — but the decision is narrower
   than previously stated, and half of it is already made.

2. **Period-coherence tolerance.** D2 requires `total_debt` and `cash_and_equivalents` to
   be period-coherent but does not define the tolerance. The harness currently requires an
   **exact** shared as-of date, deliberately — no tolerance was invented in the absence of
   evidence. Real sources may state debt and cash from filings days apart, at which point
   exact-match yields null where a human would accept the pair. **This cannot be settled
   without live cases**, and the corpus has none. Resolve it after the first real
   debt/cash sample, not before.

3. **`value_usd_basis`.** Dictionary §10 flags its semantics as unverified. Item 13
   supplies a candidate: *the figure the source itself stated in USD, not a converted one.*
   Confirm whether that is the intended meaning or whether it denotes a conversion Grata
   performed — the two are opposite, and the field name does not disambiguate.

4. **Is Silver the system of record for financials?** If collapsed period-untagged Silver
   scalars feed Gold, the per-fact provenance recommended in item 1 is lost before Gold
   sees it. Whether Gold can re-derive from retained observations or only from Silver's
   collapse determines how much of items 1, 9, and 14 is achievable at all.

5. **Cross-currency transactions end to end.** The harness refuses to calculate and emits
   null. Grata carries FX fields but no stated policy on when a conversion may occur, who
   performs it, and what date anchors it. Until that is defined, cross-currency deals will
   be silently absent from EV-based analyses rather than visibly incomplete — an
   availability problem disguised as a data problem.

6. ~~**`transaction_size_basis` vocabulary.**~~ **Resolved 2026-08-17** — the harness
   adopted the Grata spellings and reserved the Spin/Split rung. Two questions replace it,
   both on Grata's side: does D4 accept striking the unreachable M&A equity fallback, and
   does it accept striking the `SOLE_INVESTOR_AMOUNT` rung outright? The second is a
   semantic removal, not a sequencing one — investor checks never roll up into event
   magnitude, whatever the disclosure level.

---

## 6. Next code task — DONE 2026-08-17

**`transaction_size` + `transaction_size_basis` shipped**, with the Grata vocabulary and
two corrections *to* the Grata waterfall (§3 item 5). The section below is retained as
the reasoning that made it the right next task.

Its §4.2 dependency is now **satisfied**: Path A discharged the owed re-aggregation on
2026-08-17, so `transaction_value`, stake-level `equity_value`, and the typed-value picker
are settled corpus-wide and the waterfall can be built against them rather than rebuilt
after.

Nothing else in the backlog outranks it. The debt/cash work is blocked on live data that
does not exist and will not be manufactured. The EV rewire is parked on the below-control
gross-up, which this pass did not touch. `transaction_size` is deterministic, needs no
network, no model calls, and no live DB, and it is the only remaining item a reviewer
actually sees.

**The vocabulary was settled before writing code, not after** — it cost three document
edits instead of a data migration. What replaced it as the open question is on Grata's
side now: whether D4 accepts striking the unreachable M&A equity fallback, and whether
D4 accepts striking the `SOLE_INVESTOR_AMOUNT` rung outright — investor checks never roll
up into event magnitude, at any disclosure level.
