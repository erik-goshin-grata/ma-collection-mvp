# Grata V2 Transaction Data Model — Master Inventory & Recommendations v0.4

**Status:** Engineering review incorporated  
**Scope:** Current Grata `enums.py` / `schemas.py` compared with the tested/accepted transaction harness model and the data-model decisions reviewed through 2026-08-13.  
**Out of scope for redesign:** MergerLinks/Valu8 schema reconciliation, recap/IPO redesign, collection workflow redesign, historical migration/backfill design.

> **v0.4, 2026-08-18 — Engineering review incorporated.** ENG reviewed the v0.3
> inventory and returned nine directives. They are answered in place below and summarised
> in **§P**, which is the table to read first: it carries a
> KEEP / CHANGE / ADD / REMOVE-DERIVABLE / DEFER / ENG DECISION verdict per concept and
> flags every row where ENG's review **changed** the v0.3 recommendation.
>
> The largest structural change is **§A6**: event/feature modeling moves to typed
> dimensions wherever values are mutually exclusive, and flags survive only where a
> characteristic is genuinely orthogonal. That test was applied case by case, not as a
> blanket rule, and it does not produce a uniform answer — some flag pairs collapse into
> one dimension, others are confirmed as correctly orthogonal.
>
> **No schema changes are implemented by this document.** It is a specification.

> **Redlined 2026-08-17 — see `docs/grata_v2_reconciliation_2026_08_17.md`.**
> That document reconciles this draft against what the harness has actually built and
> proven, and separates implemented-and-validated from recommended, already-adequate, and
> deferred. Four changes to *this* file are marked inline below (D2, D3 ×2, D1
> `cash_and_equivalents`); the rest of v0.3 stands. The largest caveat it records: the
> balance-sheet half of the value model — `total_debt`, `cash_and_equivalents`, both
> calculated EV bases — is **fixture-validated only**, with zero live rows.

## 0. Review conclusions

This v0.2 closes the main completeness gaps in v0.1.

Key corrections/additions:
- `target_type` is a **Grata gap / proposed addition**, not an existing Grata field.
- `acquirer_type` is primarily a **company/entity classification**, not necessarily a new `transaction_record` field.
- `investment_amount` is separated into transaction-level funding magnitude (`ROUND_SIZE`) vs. investor-level `transaction_party.investment_amount`.
- basis/provenance concepts (`transaction_size_basis`, transaction/implied-value basis) are now explicit.
- all current Gold tables are inventoried: `transaction_record`, `transaction_party`, `financial_metric`, `transaction_multiple`, `transaction_event_history`.
- current Silver compatibility issues are recorded separately.
- current CBI mappings that conflict with the tested model are explicitly identified.
- the data model now includes the full proposed repeating `consideration_component` structure and advisor-person cardinality requirement.

## 1. Modeling principles

- Separate **business semantics** from **physical schema placement**. Storage/table choices are ENG decisions unless cardinality requires a repeating relationship.
- Keep the product taxonomy clean; use orthogonal flags/mechanics for technical granularity.
- Preserve **source-stated / researcher-entered / calculated** provenance where it affects meaning.
- `NULL` means no canonical value is currently available. Do not require field-level null reasons in V1.
- Applicability should follow transaction structure; an inapplicable blank is not a data-quality failure.
- Sparse fields can still be structurally important.
- Canonical field names use snake_case.
- **A stored flag must earn its storage.** If a flag is computable from data the model
  already holds, it is a derivation, not a field — a second copy that can disagree with
  the first. *(v0.4)*
- **Derivability has a precondition:** a flag is only safely removed if its deriving input
  is present **whenever the flag would have been set**. A flag that a source can assert
  directly, without stating the value it would be derived from, is not derivable — it is
  independent evidence wearing a derived-looking name. *(v0.4, see §A7)*
- Current Grata names are retained where the recommendation is **KEEP**; new names are used only for explicit **ADD / CHANGE** recommendations.
- Track **requiredness** in the dictionary as `REQUIRED`, `CONDITIONAL`, or `OPTIONAL`. This is a semantic/QA requirement matrix; it does not require every field to become SQL `NOT NULL`.

## 2. Status labels

- **KEEP** — concept is correct as-is.
- **CHANGE** — retain the concept but change semantics/name/placement.
- **ADD** — missing capability supported by current requirements/testing.
- **LEGACY** — compatibility/old concept; should not be canonical.
- **DEFER** — retain but do not redesign in this phase.
- **VERIFY** — concept exists or is plausible, but end-to-end placement/population still needs confirmation.
- **REMOVE-DERIVABLE** *(v0.4)* — the concept is real but should not be **stored**, because
  it is computable from data the model already holds. Distinct from `LEGACY`: nothing is
  wrong with the concept, only with keeping a second copy of it that can drift.
- **ENG DECISION** *(v0.4)* — the semantic requirement is settled; the remaining question is
  physical or product-side and belongs to Engineering, not to this document.

---

# A. Core event and transaction record model

## A1. Event taxonomy

| Concept / Field | Shape | Current Grata | Decision | Recommendation / Definition |
|---|---|---|---:|---|
| `event_type = ACQUISITION` | ENUM | Exists | KEEP / BROADEN M&A SEMANTIC | Canonical M&A transaction type covering company, subsidiary, business unit, assets, equity stake, merger structures, reverse mergers and de-SPAC business combinations where the underlying economic event is an acquisition/business combination. Product grouping may simply be **M&A**. |
| `MERGER` | ENUM today | Exists | CHANGE | Move merger out of the top-level event taxonomy. **v0.4:** it becomes `combination_structure = MERGER`, not a flag — see §A6 group 1. |
| `REVERSE_MERGER` | ENUM today | Exists | CHANGE | `event_type = ACQUISITION` plus `combination_structure = REVERSE_MERGER`. |
| `SPAC_DE_SPAC` | ENUM today | Exists | CHANGE | `event_type = ACQUISITION` plus `combination_structure = DE_SPAC`. |
| `combination_structure` | **HIERARCHICAL TYPED DIMENSION** | Missing | **ADD (v0.4)** | `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`, plus non-chain siblings `SHARE_PURCHASE` / `ASSET_PURCHASE` / `NULL`. Store the **most specific** value; query broader questions **by implication, never by equality**. Not three peer values — the hierarchy is what preserves the nested facts. §A6. |
| `is_merger`, `is_reverse_merger`, `is_de_spac` | FLAG | `is_de_spac` exists | **REMOVE-DERIVABLE (v0.4)** | All three roll up from `combination_structure`. **Changes v0.3**, which proposed adding the first two and keeping the third. §A7. |
| `is_merger_of_equals` | FLAG | Implemented in current harness | KEEP / HARNESS IMPLEMENTED | Special merger characteristic; true only with explicit/qualified merger-of-equals evidence. |
| `MINORITY_INVESTMENT` | ENUM today | Exists | CHANGE | Remove from core event usage; minority is a transaction feature. |
| `is_minority` | FLAG | Missing | ADD | Shared transaction feature. Current-stake evidence takes precedence over ownership history. |
| `JOINT_VENTURE` | ENUM | Exists | KEEP / CLARIFY | Use for genuine JV formation/joint-control event. Purchase/buyout of an existing JV interest remains M&A. |
| `VC_ROUND` | ENUM | Exists | KEEP | Genuine venture financing event. |
| `GROWTH_EQUITY` | ENUM | Exists | KEEP | Genuine growth/private-equity financing event; PE buyouts remain M&A. |
| `VENTURE_DEBT` | ENUM | Exists | KEEP / VERIFY MAPPING | Genuine venture/growth debt only; avoid forcing generic debt/bridge financings here. |
| `SPIN_OFF`, `SPLIT_OFF` | ENUM | Exists | KEEP | Spin/Split family; detailed mechanics below. |
| `CARVE_OUT_IPO`, `RECAPITALIZATION`, `IPO`, `DIRECT_LISTING` | ENUM | Exists | DEFER | Preserve current model; not redesigned in this phase. |

## A2. `event_category`

Current enum values are `ma`, `divestiture`, `investment_funding`, `recapitalization`, `exit_liquidity`.

**Recommendation:** treat `event_category` as a broad product-family grouping, not a seller-side characteristic.

Proposed direction:
- `ma`
- `spin_split`
- `investment_funding`
- `recapitalization`
- `exit_liquidity`

`divestiture` should move out of `event_category`; it is already represented by `is_divestiture` within M&A. This avoids the same kind of taxonomy overlap we are removing for minority and merger.

Physical enum migration is an ENG decision; the semantic recommendation is that a transaction should not need to choose between `ma` and `divestiture` when it is both.

## A3. Target / acquirer classification

| Concept | Shape | Current Grata | Decision | Recommendation |
|---|---|---|---:|---|
| `target_type` | ENUM | **No direct equivalent in current Grata enums/schema** | ADD | Transaction-specific structural target classification: `STANDALONE_COMPANY`, `BUSINESS_UNIT`, `SUBSIDIARY`, `ASSETS`, or `NULL`. The current Grata `PartyType` classifies entities (PE firm, corporation, advisor, etc.) and is not equivalent. `target_type` preserves asset/business-unit/subsidiary granularity while keeping them in M&A `ACQUISITION`. |
| `acquirer_type` | Derived entity classification | `PartyType` exists on `transaction_party` | CHANGE FROM SEPARATE ENUM IDEA | Prefer deriving the acquirer's economic type from the buyer party's existing/canonical company type. Amend `PartyType` only where actual buyer classifications are missing; do not create a duplicate transaction-level acquirer taxonomy unless product performance requires denormalization. |

Current `PartyType` covers PE, VC, growth equity firm, corporation, individual, fund, family office, sovereign wealth fund, pension fund, lender, advisor, SPAC, search fund, government and unknown. Potential missing buyer archetypes should be reconciled against the canonical company model before adding them.

## A4. Existing `transaction_record` fields — completeness reconciliation

| Current field | Shape | Decision | Notes |
|---|---|---:|---|
| `transaction_id` | ID | KEEP | Canonical transaction identifier. |
| `event_type` | ENUM | CHANGE ENUM VOCABULARY | The field remains the canonical event classifier; the change is to allowed enum values/semantics, not removal of the field. |
| `event_category` | ENUM / derived | KEEP | Reconcile derivation after event changes. |
| `recap_type` | ENUM | KEEP / DEFER REDESIGN | Keep current recap structure. **v0.4:** this is the surviving representation — the four `is_*_recap` flags duplicate it and are removable. Redesign of the recap domain itself remains DEFER. §A6 group 4. |
| `spin_split_type` | ENUM | KEEP | `SPIN_OFF` / `SPLIT_OFF`. |
| `distribution_mechanism` | ENUM | CHANGE NAME | Prefer `spin_split_distribution_mechanism`; scope is Spin/Split-specific. |
| `financials_disclosure_status` | ENUM | KEEP / NARROW DEFINITION | Covers target/company financial metrics (revenue, EBITDA, debt, cash, etc.), not transaction economics. Add separate `transaction_terms_disclosure_status` below. |
| `pct_acquired` | DATA POINT | KEEP | Current transaction stake, not resulting ownership. |
| `per_share_price` | DATA POINT | KEEP | Per-target-security consideration. |
| `round_label` | DATA POINT | KEEP | Source label such as `Series B`, `Seed`, `Series C extension`; preserve source wording. |
| `round_stage_category` | ENUM | KEEP | Normalized analytical bucket derived from round label/context (`PRE_SEED`, `SEED`, `EARLY_STAGE`, `GROWTH`, `LATE_STAGE`). |
| `round_sequence_number` | DATA POINT | KEEP | Funding sequence. |
| `prior_round_id` | FK / relationship | KEEP | Prior funding round linkage. |
| `facility_size` | DATA POINT | KEEP | Funding/debt facility size. |
| `total_raised_to_date` | DATA POINT | KEEP | Cumulative capital raised. |
| `consideration_type` | DERIVED ENUM | KEEP / CHANGE SEMANTICS | Derive from normalized consideration components where available. |
| `is_take_private` | FLAG | KEEP | Orthogonal M&A feature. |
| `is_lbo` | FLAG | KEEP | Orthogonal M&A feature. |
| `is_mbo`, `is_mbi` | FLAG | **CHANGE (v0.4)** | Replace with `management_participation` ∈ `MBO` / `MBI` / `BIMBO` / `NULL`. The dimension names a real third state — buy-in management buy-out — that two booleans can only express as both-true, which is indistinguishable from an error. **Changes v0.3**, which kept both. §A6 group 2. |
| `is_platform_investment`, `is_add_on` | FLAG | **CHANGE (v0.4)** | Replace with `sponsor_investment_role` ∈ `PLATFORM` / `ADD_ON` / `NULL`. Mutually exclusive: a sponsor investment is the platform for a thesis or an add-on to one, not both. Evidence discipline unchanged — populated only on explicit/qualified evidence. **Changes v0.3.** §A6 group 5. |
| `is_secondary_buyout` | FLAG | KEEP / HARNESS IMPLEMENTED | Sponsor-to-sponsor secondary buyout flag; explicit evidence or side-qualified buyer/seller sponsor-party evidence. |
| `is_de_spac` | FLAG | **REMOVE-DERIVABLE (v0.4)** | `combination_structure = DE_SPAC`. **Changes v0.3**, which kept it stored. §A7. |
| `is_divestiture` | FLAG | KEEP | Seller-side characteristic. |
| `is_stock_for_stock` | FLAG | **REMOVE-DERIVABLE (v0.4)** | Component forms ⊆ {`ACQUIRER_STOCK`} with no `CASH` component. ENG's named candidate, confirmed. **Conditional on `consideration_component` being populated** — until then the flag carries evidence no derivation can reach. **Changes v0.3.** §A7. |
| `is_down_round`, `is_up_round` | FLAG | **CHANGE (v0.4)** | Replace with `round_price_direction` ∈ `UP` / `DOWN` / `FLAT` / `NULL`. Both-false currently conflates *flat* with *unknown*. Note the collection gap: the harness emits `is_down_round` only, so `UP`/`FLAT` need extraction vocabulary to move with the model. **Changes v0.3.** §A6 group 3. |
| `is_unicorn_round` | FLAG | KEEP | Funding feature. **v0.4:** assessed for derivation (post-money ≥ $1B) and **rejected** — sources assert unicorn status without stating post-money, so deriving it would drop the rows where the claim is the only evidence. §A7. |
| `is_extension_round` | FLAG | KEEP | Funding feature. |
| `cvc_participation` | FLAG | KEEP | Funding feature. |
| `is_dividend_recap`, `is_equity_recap`, `is_leveraged_recap`, `is_sponsor_recap` | FLAG | **REMOVE-DERIVABLE (v0.4)** | Each is `recap_type = <value>`. The only flags on this list removable **today** — `recap_type` already exists and is already populated, so there is no precondition. **Changes v0.3**, which preserved all four. §A7. |
| `linked_filings_count` | DATA POINT | KEEP | Operational/source linkage. |
| `has_earnout`, `has_cvr` | FLAG | **REMOVE-DERIVABLE (v0.4)** | Presence of a component with form `EARNOUT` / `CVR`. Same precondition as `is_stock_for_stock`: components must be populated first. **Changes v0.3.** §A7. |
| `is_merger_of_equals` | FLAG | KEEP / HARNESS IMPLEMENTED | True only with explicit/qualified merger-of-equals evidence. |
| `is_oversubscribed` | FLAG | KEEP | Funding feature. |
| `platform_transaction_id` | ID / relationship | VERIFY / DO NOT RECOMMEND YET | Field exists, but the supplied Grata materials do not define its semantics sufficiently. Clarify with ENG before documenting or building behavior around it. |
| `has_cbi_data` | FLAG | LEGACY / OPERATIONAL | Source-system metadata, not core business semantics. |

## A5. New transaction-level concepts

| Concept | Shape | Decision | Notes |
|---|---|---:|---|
| `is_minority` | FLAG | ADD | Tested replacement for minority core event. |
| `stake_transition_type` | ENUM FEATURE | ADD | Explicit ownership transition context. |
| `combination_structure` | **HIERARCHICAL TYPED DIMENSION** | ADD | `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`. Replaces the v0.3 `is_merger` / `is_reverse_merger` flag proposal without losing the nesting. §A6. |
| `management_participation` | **TYPED DIMENSION** | ADD | Replaces `is_mbo` / `is_mbi`. §A6. |
| `round_price_direction` | **TYPED DIMENSION** | ADD | Replaces `is_up_round` / `is_down_round`. §A6. |
| `sponsor_investment_role` | **TYPED DIMENSION** | ADD | Replaces `is_platform_investment` / `is_add_on`. §A6. |
| `target_type` | ENUM | ADD | Transaction fact. |
| `transaction_size` | DERIVED DATA POINT | ADD | Common product magnitude. |
| `transaction_size_basis` | ENUM / basis attribute | ADD | Identifies the underlying magnitude selected: e.g. `TRANSACTION_VALUE`, `EQUITY_VALUE`, `ROUND_SIZE`, `SOLE_INVESTOR_AMOUNT`, `SPIN_SPLIT_CONSIDERATION_VALUE`. Required wherever `transaction_size` is populated. |
| `transaction_terms_disclosure_status` | ENUM | ADD | Separate from financials disclosure; covers deal economics/consideration/value terms using the same `DISCLOSED / PARTIALLY_DISCLOSED / UNDISCLOSED / UNKNOWN` vocabulary. |
| ~~`is_reverse_merger`~~ | FLAG | **SUPERSEDED (v0.4)** | Rolls up from `combination_structure`; not stored. |

### `stake_transition_type`

- `NEW_MINORITY_STAKE`
- `NEW_MAJORITY_STAKE`
- `FULL_ACQUISITION`
- `MINORITY_ACQUIRING_MAJORITY`
- `MAJORITY_ACQUIRE_REMAINING`
- `MINORITY_ACQUIRING_REMAINING`
- `MAJORITY_INCREASING_STAKE`
- `MINORITY_INCREASING_STAKE`

`NULL` = insufficient explicit ownership-transition evidence.

## A6. Typed dimensions vs. flags *(v0.4 — ENG directive 1)*

ENG's directive: **rework the event/feature model around typed dimensions where values are
mutually exclusive, and retain flags only for genuinely orthogonal characteristics.**

### The test

A set of booleans should become one typed dimension when the values are **mutually
exclusive**, because a set of *n* mutually exclusive booleans encodes 2ⁿ states of which
only *n+1* are real. The surplus states are not merely unused — they are silently
ambiguous, and the ambiguity usually lands on the most common case.

The clearest instance is the funding pair below: `is_up_round = false` and
`is_down_round = false` means *either* "flat round" *or* "we do not know", and nothing in
the model distinguishes them. A typed dimension has to name both, so the information stops
being lost.

Conversely, flags are correct when characteristics **co-occur legitimately**. Collapsing
genuinely orthogonal flags into one enum forces a false choice and loses facts.

The test was applied to each group ENG named. **It does not produce a uniform answer**,
and that is the finding: two groups collapse entirely, one collapses partially, one is
duplication of a dimension that already exists, and one is confirmed correctly orthogonal.

### Group 1 — merger / reverse-merger / de-SPAC → **one dimension**

These are not parallel alternatives; they **nest**. A de-SPAC is a species of reverse
merger, and a reverse merger is a species of combination. v0.3 said the de-SPAC and
reverse-merger flags "may coexist when both are factually supported", which is true and is
exactly the symptom: coexistence there is not orthogonality, it is a taxonomy expressing
one fact at two levels of abstraction.

**Recommend `combination_structure`: a HIERARCHICAL typed dimension, not three peer
values.** This distinction is the whole point and must survive into implementation. Storing
`DE_SPAC`, `REVERSE_MERGER` and `MERGER` as unrelated alternatives would lose exactly the
nested facts the flags were carrying — a de-SPAC would stop being findable as a reverse
merger, and a reverse merger would stop being findable as a merger.

**The hierarchy, stated normatively:**

```
MERGER                          (broadest: a statutory combination)
  └── REVERSE_MERGER            (a merger in which a private operating company
       │                         becomes public through a public shell)
       └── DE_SPAC              (a reverse merger in which that shell is a SPAC)
```

`DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`. Each arrow is a **documented implication**, not a
naming convention:

| stored value | implies | does **not** imply |
| --- | --- | --- |
| `DE_SPAC` | `REVERSE_MERGER`, `MERGER` | — |
| `REVERSE_MERGER` | `MERGER` | `DE_SPAC` |
| `MERGER` | — | `REVERSE_MERGER`, `DE_SPAC` |

Values **outside** the merger chain, siblings of `MERGER` rather than members of it:

| value | meaning |
| --- | --- |
| `SHARE_PURCHASE` | ordinary purchase of shares; not a statutory combination |
| `ASSET_PURCHASE` | ordinary purchase of assets; not a statutory combination |
| `NULL` | structure not established from the source |

**Storage and query rules:**

1. **Store the most specific value the source supports.** A confirmed de-SPAC is stored
   `DE_SPAC`, never `REVERSE_MERGER` or `MERGER`, because the specific value carries the
   general ones and the reverse is not true.
2. **Query by implication, never by equality**, whenever the question is a broader one.
   "Is this a merger?" is `combination_structure IN (MERGER, REVERSE_MERGER, DE_SPAC)` —
   **not** `= MERGER`. An equality test against a hierarchical dimension is a bug, and it
   is the specific way this design fails if the hierarchy is treated as decoration.
3. **Ambiguity resolves upward, not downward.** A source establishing a reverse merger
   without establishing a SPAC shell is stored `REVERSE_MERGER`. Never infer the more
   specific value.
4. The implication set is **part of the dictionary**, not application logic to be
   rediscovered per consumer. Whether ENG expresses it as a lookup table, a generated
   closure, or materialized rollup columns is an **ENG DECISION**; that it is expressed
   *somewhere shared* is not optional.

The former flags are then answered by rollup rather than storage:
`is_reverse_merger := combination_structure IN (REVERSE_MERGER, DE_SPAC)`;
`is_merger := combination_structure IN (MERGER, REVERSE_MERGER, DE_SPAC)`;
`is_de_spac := combination_structure = DE_SPAC`.

This **changes v0.3**, which proposed adding `is_merger` and `is_reverse_merger` as stored
flags alongside the existing `is_de_spac`. Under the hierarchical dimension all three
become derivations — and, unlike three peer enum values, the dimension loses nothing the
flags could express. `event_type = ACQUISITION` is unaffected — that decision stands.

`is_merger_of_equals` is **kept as a flag**: it qualifies a merger rather than competing
with it, and folding it in would force `MERGER_OF_EQUALS` to be a fourth structure value
and re-break the rollup. It gains a conditional-applicability rule — meaningful only when
`combination_structure = MERGER`. Whether ENG prefers the constraint expressed in schema or
in QA is an **ENG DECISION**.

### Group 2 — MBO / MBI → **one dimension, and it exposes a missing state**

`is_mbo` (incumbent management buys the business) and `is_mbi` (an external management team
buys in) are close to exclusive — but not strictly. A **BIMBO** (buy-in management buy-out)
is a real, named structure in which both occur, and today it is representable only as both
flags true, which is indistinguishable from a data error.

**Recommend `management_participation`** ∈ `MBO` | `MBI` | `BIMBO` | `NULL`.

This is the case that shows why the exercise is worth doing: the typed dimension does not
just tidy two booleans, it forces the model to name a third real state the booleans could
only express by accident.

### Group 3 — funding up / down → **one dimension**, highest-value change in this section

Strictly mutually exclusive, and the both-false state is genuinely ambiguous between *flat*
and *unknown*.

**Recommend `round_price_direction`** ∈ `UP` | `DOWN` | `FLAT` | `NULL` (unknown).

Note the collection asymmetry: the harness funding prompt emits `is_down_round` and has no
`is_up_round` at all, so today a Grata up-round can only ever be inferred, never collected.
Whichever representation ENG adopts, the **collection vocabulary has to move with it** or
the new `UP`/`FLAT` values will be permanently unpopulated.

`is_extension_round`, `is_bridge_round`, `is_oversubscribed`, `cvc_participation` and
`is_unicorn_round` stay flags — each can co-occur with any price direction and with each
other.

### Group 4 — recap types → **the dimension already exists; the flags duplicate it**

`recap_type` (ENUM) and `is_dividend_recap` / `is_equity_recap` / `is_leveraged_recap` /
`is_sponsor_recap` model the same fact twice. This is not a design question but a
redundancy: **keep `recap_type`, remove the four flags as derivable** (§A7).

One genuine wrinkle, deliberately **not** solved here: a dividend recap is normally also
debt-funded, so `DIVIDEND` and `LEVERAGED` describe the same transaction from different
angles, and `SPONSOR` describes *who drove it* rather than *how it was funded* — a
different axis again. Whether that needs a compound value, or a second orthogonal
`is_sponsor_driven` flag, is an **ENG DECISION** and should be settled from real recap
examples. The recap domain remains **DEFER** for redesign; removing duplication is not a
redesign.

### Group 5 — take-private / LBO / add-on / platform / secondary-buyout → **mostly orthogonal; one pair collapses**

This group is where a blanket rule would have destroyed information. Four of the five
describe **different axes of the same transaction**:

| flag | what it is a fact about |
| --- | --- |
| `is_take_private` | the target's prior listing status |
| `is_lbo` | how the purchase was financed |
| `is_secondary_buyout` | who the seller was |
| `is_platform_investment` / `is_add_on` | the buyer's sequence/thesis |

A public-to-private LBO bought from another sponsor and used as the platform for a new
thesis is **all four simultaneously**, each independently true and independently useful.
Forcing them into one enum would require picking one and discarding three.

**`is_take_private`, `is_lbo`, `is_secondary_buyout`: KEEP as flags.** This confirms v0.3.

**`is_platform_investment` / `is_add_on`: CHANGE to one dimension** —
`sponsor_investment_role` ∈ `PLATFORM` | `ADD_ON` | `NULL`. A sponsor investment is either
the platform for a thesis or an add-on to an existing one; it is not both, and both-true is
meaningless rather than merely rare.

---

## A7. Flags that should not be stored *(v0.4 — ENG directive 2)*

ENG asked which stored flags can be removed as derivable, naming `is_stock_for_stock` as
the first candidate from consideration components. It is, and it is not alone — but the
list has a hard precondition attached, and the precondition is the substance of this
section.

### The precondition

> A flag is safely removable only if its deriving input is present **whenever the flag
> would have been set.**

Removing a flag whose input is often absent does not simplify the model, it **deletes
evidence**. A source that says "the transaction is an all-stock merger" without itemising
consideration supports `is_stock_for_stock` and supports no derivation at all. The flag
must therefore outlive the decision to derive it, until components are actually populated.

### Candidates

| flag | derivation | verdict | precondition |
| --- | --- | --- | --- |
| `is_stock_for_stock` | component forms ⊆ {`ACQUIRER_STOCK`} and no `CASH` component | **REMOVE-DERIVABLE** | `consideration_component` populated. ENG's named candidate, confirmed. |
| `has_earnout` | any component with form `EARNOUT` | **REMOVE-DERIVABLE** | same |
| `has_cvr` | any component with form `CVR` | **REMOVE-DERIVABLE** | same |
| `consideration_type` | §C3 aggregation over components | **REMOVE-DERIVABLE** | same — already documented as derived in v0.3; listed here for consistency |
| `is_merger` | `combination_structure IN (MERGER, REVERSE_MERGER, DE_SPAC)` — the full implication set, **not** `= MERGER` | **REMOVE-DERIVABLE** | §A6 hierarchy adopted. **Changes v0.3**, which proposed adding it. |
| `is_reverse_merger` | `combination_structure IN (REVERSE_MERGER, DE_SPAC)` — includes the more specific value | **REMOVE-DERIVABLE** | as above. **Changes v0.3.** |
| `is_de_spac` | `combination_structure = DE_SPAC` | **REMOVE-DERIVABLE** | as above. **Changes v0.3**, which kept it stored. |
| `is_dividend_recap` | `recap_type = DIVIDEND` | **REMOVE-DERIVABLE** | none — `recap_type` already exists and is already populated |
| `is_equity_recap` | `recap_type = EQUITY` | **REMOVE-DERIVABLE** | none |
| `is_leveraged_recap` | `recap_type = LEVERAGED` | **REMOVE-DERIVABLE** | none |
| `is_sponsor_recap` | `recap_type = SPONSOR` | **REMOVE-DERIVABLE** | none |

The four recap flags are the only ones removable **today**. Every other row waits on
`consideration_component` or on the §A6 dimension.

### Rejected: `is_unicorn_round`

`is_unicorn_round` looks derivable — post-money valuation ≥ $1B — and **is not**, because
it fails the precondition in a way worth recording. Sources routinely assert unicorn status
without stating a post-money valuation, so deriving it would silently drop every row where
the claim is the only evidence. It is independent evidence wearing a derived-looking name.

**KEEP as a stored flag.** This is the general shape to watch for elsewhere: derivability
is a property of the *data*, not of the *definition*.

### Not assessed

`is_minority`, `is_divestiture`, `is_take_private`, `is_lbo`, `is_secondary_buyout`,
`is_merger_of_equals`, `has_go_shop`, `has_mac_clause` — each is a primary transaction fact
with no candidate derivation in the current model. They remain stored flags.

---

---

# B. Transaction security and share mechanics

| Concept | Shape | Population | Current Grata | Decision |
|---|---|---|---|---:|
| `target_security_type` | ENUM | Source / researcher | Missing | ADD |
| `per_share_price` | DATA POINT | Source / researcher | Exists | KEEP |
| `exchange_ratio` | DATA POINT | Source / researcher | Missing general M&A home | ADD |
| `target_shares_outstanding` | DATA POINT | Source / researcher | Missing | ADD |
| `target_shares_acquired` | DATA POINT | Source / calculated / researcher | Missing | ADD |
| `acquirer_shares_issued` | DATA POINT | Source / calculated / researcher | Missing | ADD |
| `acquirer_security_price` | DATA POINT | Source / market/reference / researcher | Missing | ADD |
| `acquirer_security_price_date` | DATE | Source / market/reference / researcher | Missing | ADD |

Requirements:
- These are reusable transaction/security facts. The consideration model should **reference/reuse** them rather than independently creating conflicting copies.
- Support multiple security classes without duplicating the transaction.
- Applicable classes may include common/ordinary shares, preferred, units, options, RSUs, PSUs, etc.; exact `target_security_type` vocabulary should be reconciled against SEC/agreement extraction before finalizing.
- For stock consideration, a calculated component value may use `exchange_ratio × target_security_count × acquirer_security_price`, but only when the share-price date/basis is accepted and explicit. Do not silently assume a price date.
- Security/share mechanics may be populated from PRs, transaction filings/agreements, researcher entry, market/reference data, or accepted calculations.
- General SEC financial-statement mining remains out of scope; transaction/merger documents and relevant exhibits can be used.

**Normalization option:** ENG may prefer a repeating `transaction_security` child object for security type / issuer / price / price date. That would reduce duplication across M&A consideration and Spin/Split. The semantic requirement is reuse; the physical implementation remains open.

# C. Consideration model

## C1. Recommendation

A normalized/repeating `consideration_component` structure is a **high-priority V1 capability**.

Current Grata already anticipates `consideration_type` being derived from component rows, but the component table is deferred. The harness/agreement extraction already produces component-level form, per-share amount, currency, exchange ratio, trigger description and election mechanics.

A child structure is warranted because:
- one transaction may contain cash + stock + CVR/earnout;
- different target security classes may receive different treatment;
- stock consideration requires an exchange ratio, not merely a cash-equivalent value;
- Common / Options / RSUs / PSUs must aggregate into one transaction, not duplicate it.

## C2. Proposed `consideration_component`

| Field | Shape | Required? | Definition |
|---|---|---:|---|
| `consideration_component_id` | ID | Yes | Unique component identifier. |
| `transaction_id` | FK | Yes | Parent transaction. |
| `target_security_ref` | FK / relationship | No | Preferred reference to the applicable target-security record if ENG normalizes securities; otherwise use `target_security_type`. |
| `consideration_form` | ENUM | Yes | Economic form of component. |
| `per_share_amount` | DATA POINT | No | Per-target-security cash/monetary amount when applicable. |
| `currency` | ISO 4217 string | No | Currency of monetary component. |
| `exchange_ratio` | DATA POINT | No | Acquirer securities received per target security. |
| `acquirer_security_ref` | FK / relationship | No | Preferred reference to acquirer security when normalized; otherwise use `acquirer_security_type`. |
| `target_security_count` | DATA POINT | No | Target securities represented by this component where the count is component-specific. |
| `acquirer_security_count` | DATA POINT | No | Acquirer securities issued for this component, reported or calculated. |
| `component_value` | DATA POINT | No | Aggregate component value, reported or calculated. |
| `value_basis` | ENUM / attribute | No | `AS_REPORTED` / `CALCULATED`. |
| `trigger_description` | TEXT | No | Trigger/contingency terms for CVR, earnout, etc. |
| `election` | TEXT / structured attribute | No | Shareholder election terms where present. |
| `is_prorated` | FLAG | No | Proration applies. |
| `notes` | TEXT | No | Terms not yet normalized. |

If a separate `transaction_security` structure is not adopted, `target_security_type`, `acquirer_security_type`, and relevant security-price fields can remain directly on the component. The requirement is to avoid two unrelated sources of truth.

### Initial `consideration_form` vocabulary

Known required forms:
- `CASH`
- `ACQUIRER_STOCK`
- `CVR`
- `EARNOUT`

Additional forms to reconcile with SEC/agreement extraction before final enum freeze:
- retained/target equity
- debt assumed
- other

## C3. Aggregation rules

- `consideration_type` is a derived transaction-level summary:
  - cash only → `cash`
  - stock only → `stock`
  - cash + stock → `cash_and_stock`
  - election structure → `election` when election is the defining summary
  - otherwise → `other`
- Prefer a source-stated aggregate `component_value` when explicitly qualified.
- Cash component, if not stated in aggregate: `per_share_amount × target_security_count`.
- Stock component, if not stated in aggregate: `exchange_ratio × target_security_count × acquirer_security_price`, only with an accepted price date/basis.
- Sum component values into stake-level `equity_value` only when all included components are economically qualified as equity consideration and currency-compatible.
- `CVR` / `EARNOUT`: include in a calculated equity consideration only when the source supplies a fixed/determinable value under the accepted valuation rule; do not assume maximum payout or invent present value.
- `DEBT_ASSUMED`: preserve as a consideration/transaction term when disclosed, but do **not** automatically add it into stake-level `equity_value`. `transaction_value` handles debt through its accepted rule or through a source-stated transaction value.
- Retained/rollover equity should not be mechanically summed into cash-paid equity consideration unless its value semantics are explicitly defined.
- Elections/proration are retained as terms; do not calculate a final aggregate allocation until the required election/count evidence exists.
- Do not aggregate cross-currency components without an explicit FX rule.
- Security classes are component rows/relationships, not separate transactions.
- Consideration amendment/version history is **DEFER / Potential V2**. V1 may retain only current canonical terms.

# D. Values and transaction size

## D0. Deal valuation vs. company financials

The current Grata `financial_metric` table mixes **deal valuation metrics** and **company financial metrics** in one normalized row shape.

**v0.4 — ENG directive 3.** v0.3 said a second physical table "is not required". ENG has
gone further and made this a **preference**, which is a stronger and better claim:
`financial_metric` is the **preferred home for both classes**, and a value that belongs in
it should not also live as a scalar elsewhere.

The reason is not tidiness. Every property that makes a monetary value trustworthy —
currency, period, precision, FX treatment, per-fact provenance, calculated-vs-reported
basis — has to be attached to *that value*. A scalar column on `transaction_record` has
nowhere to put them, so each scalar either loses them or grows a private set of companion
columns that drift from the table's. One row shape, one policy: see **§E4**.

The dictionary should explicitly classify each `MetricType` into a derived/static category:

**DEAL_VALUE**
- `EQUITY_VALUE`
- `TRANSACTION_VALUE`
- `ROUND_SIZE`
- `PRE_MONEY_VALUATION`
- `POST_MONEY_VALUATION`
- `IMPLIED_EQUITY_VALUE`
- `IMPLIED_ENTERPRISE_VALUE`

**COMPANY_FINANCIAL**
- `REVENUE`, `GROSS_PROFIT`, `EBITDA`, `EBIT`, `NET_INCOME`, `FREE_CASH_FLOW`
- `TOTAL_DEBT`, `CASH_AND_EQUIVALENTS`, `NET_DEBT`, `SHAREHOLDERS_EQUITY`
- `ARR`, `MRR`

**DERIVED_ROLLUP** *(v0.4)*
- `TRANSACTION_SIZE` — see §E4. Never summed with either class above, never a multiple
  numerator, always carries `transaction_size_basis`.

This can be a dictionary/code mapping rather than a stored `metric_category` column because `metric_type` determines it uniquely. It allows different QA/applicability rules: company financials need period/date semantics; deal values need transaction/value basis semantics.

## D1. Canonical value concepts

| Concept | Current Grata Home | Population | Scope | Decision |
|---|---|---|---|---:|
| `equity_value` | `MetricType.EQUITY_VALUE` | Source / calculated / researcher | Stake-level | KEEP |
| `transaction_value` | `MetricType.TRANSACTION_VALUE` | Source / calculated / researcher | Transaction-level | KEEP |
| `transaction_size` | Missing | Derived | Common transaction magnitude | ADD |
| `round_size` | `MetricType.ROUND_SIZE` | Source / researcher | Funding transaction | KEEP |
| `pre_money_valuation` | `MetricType.PRE_MONEY_VALUATION` | Source / researcher | Funding 100%-basis equity valuation | KEEP |
| `post_money_valuation` | `MetricType.POST_MONEY_VALUATION` | Source / researcher | Funding 100%-basis equity valuation | KEEP |
| `implied_equity_value` | `MetricType.IMPLIED_EQUITY_VALUE` | Source / calculated / researcher | 100%-basis equity | KEEP |
| `total_debt` | `MetricType.TOTAL_DEBT` | Source / researcher | Whole company / point-in-time | KEEP |
| `cash_and_equivalents` | `MetricType.CASH_AND_EQUIVALENTS` | Source / researcher | Whole company / point-in-time | KEEP / DEFINE ECONOMIC SCOPE |
| `net_debt` | `MetricType.NET_DEBT` | Source / calculated / researcher | Whole company / point-in-time | KEEP |
| `implied_enterprise_value` | `MetricType.IMPLIED_ENTERPRISE_VALUE` | Source / calculated / researcher | 100%-basis EV | KEEP / CANONICAL |
| `enterprise_value` | `MetricType.ENTERPRISE_VALUE` | Source observation today | Whole company | CHANGE / REMOVE AS COMPETING CANONICAL OUTPUT |

### `cash_and_equivalents` scope

Use the Grata field name. The economic definition should be explicit enough to align the net-debt bridge with the chosen comps convention; the harness used a broader cash + equivalents + short-term/marketable-investments bucket. Exact scope should be documented rather than creating a second cash field.

**Redline 2026-08-17 — scope note closed.** Adopt the harness definition verbatim:
*cash and cash equivalents plus short-term and marketable investments, as one combined
figure, not split into components.* The harness column is named `cash_st`; treat it as the
same concept under a local name, not a second field. Two rules belong on this row rather
than only in D2, because both fail silently: **missing cash is never zero**, and its
counterpart **`total_debt` is gross, never net** — a net figure entered as total debt
corrupts every downstream derivation with no signal that anything went wrong.

## D2. Canonical EV rule

Use one canonical whole-company EV: `implied_enterprise_value`.

- source-stated whole-company EV → `implied_enterprise_value`
- otherwise, when supported: `implied_equity_value + net_debt`
- reported `net_debt` is preferred
- otherwise `net_debt = total_debt - cash_and_equivalents` only when both are available and period-coherent
- missing debt/cash is never assumed to be zero

**Redline 2026-08-17 — two rules are missing here.** Both are implemented and
fixture-validated in the harness; see the reconciliation §3 items 10 and 12.

- **Currency coherence, alongside period coherence.** Every calculation that mixes
  consideration with a balance-sheet figure — `total_debt - cash_and_equivalents`,
  `equity_value + total_debt`, `implied_equity_value + net_debt` — requires **both
  currencies known and equal**. Unknown on either side does not calculate; known but
  differing does not calculate. *An unknown currency is insufficient evidence, not
  permission to assume agreement* — there is no plausible-range check on EV that would
  catch a JPY balance sheet added to a USD purchase price. No conversion is attempted:
  that needs an FX date the model does not carry. A `STATED` value is exempt, being one
  source-stated figure rather than a sum.
- **Never backsolve `net_debt` from `EV - equity_value`.** The schema makes this
  available and plausible, and it is wrong twice: `EQUITY_VALUE` is stake-level while
  `ENTERPRISE_VALUE` is whole-company (below control the difference is mostly the
  un-acquired stake, not debt), and even at 100% the two figures typically come from
  different sources and dates, so their difference is a residual of every inconsistency
  between them. Once written, a backsolved net debt is indistinguishable from a reported
  one.

`MetricType.ENTERPRISE_VALUE` may remain temporarily as an input/compatibility observation type, but should not compete with `IMPLIED_ENTERPRISE_VALUE` as a canonical output.

## D3. Basis / provenance

`*_basis` answers **which rung of the accepted value waterfall produced the canonical value**. It is more specific than simply `AS_REPORTED` / `CALCULATED`.

Recommended examples:

- `transaction_value_basis`
  - `STATED`
  - `EQUITY_BELOW_CONTROL` — *added by redline 2026-08-17*
  - `EQUITY_VALUE_ONLY`
  - `EQUITY_VALUE_PLUS_TOTAL_DEBT`
- `transaction_size_basis`
  - `TRANSACTION_VALUE`
  - `EQUITY_VALUE`
  - `ROUND_SIZE`
  - `SOLE_INVESTOR_AMOUNT`
  - `SPIN_SPLIT_CONSIDERATION_VALUE`
- `implied_equity_value_basis`
  - `STATED`
  - `GROSSED_UP_FROM_EQUITY_VALUE`
- `implied_enterprise_value_basis`
  - `STATED`
  - `IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT`
  - `IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT`

These names are semantic recommendations; ENG may implement field-specific basis enums or a normalized generic basis model. A plain `is_calculated` flag is not enough to tell which derivation path fired.

**Redline 2026-08-17 — three corrections (reconciliation §3 items 3 and 5).**

1. **`EQUITY_BELOW_CONTROL` added above.** Below control there is no debt to add; the
   figure is the stake consideration and nothing else. Folding that into
   `EQUITY_VALUE_ONLY` merges "debt does not apply" with "debt applies but is unknown" —
   the first is complete, the second is a research queue item.
2. **Spelling to settle.** This document writes `EQUITY_VALUE_PLUS_TOTAL_DEBT`; the
   harness writes `EQUITY_PLUS_TOTAL_DEBT`. Pick one.
3. **`equity_value_basis` is missing entirely** from this list and from dictionary §7,
   which carries bases for transaction value and implied equity but none for equity value.
   Add it: `STATED` / `PER_SHARE_X_SHARES`.

The `transaction_size_basis` vocabulary here also disagrees with
`docs/handoff_transaction_size.md` on two rungs (`EQUITY_VALUE` vs `EQUITY_CONSIDERATION`,
`SOLE_INVESTOR_AMOUNT` vs `SOLE_INVESTOR_CHECK`), and the handoff omits
`SPIN_SPLIT_CONSIDERATION_VALUE`. Recommended resolution: **the Grata spellings win** —
`transaction_size` is a Grata product concept — and the Spin/Split rung is added to the
harness waterfall. Settle this **before** implementation starts; it is a rename now or a
data migration later.

The Tier 2 vocabularies (`implied_equity_value_basis`, `implied_enterprise_value_basis`)
need no change: the harness implements them exactly as written here.

## D4. `transaction_size`

`transaction_size` is a derived common/product magnitude, not a new economic value definition.

Accepted waterfall:
- M&A → `transaction_value`
- M&A fallback → `equity_value` where equity is stated and debt is unknown under the accepted rule
- Growth / VC → `round_size`
- Funding fallback → one sole investor's `transaction_party.investment_amount` only when it is the only safe disclosed check
- Spin/Split → `spin_split_consideration_value` when available

`transaction_size_basis` identifies exactly which source field/rung supplied the magnitude and is required whenever `transaction_size` is populated.

## D5. Funding `investment_amount`

`investment_amount` should remain an **investor-level** field on `transaction_party`.

Canonical funding transaction magnitude is `ROUND_SIZE`.

The harness's aggregate transaction-level `investment_amount` was useful internally, but it is redundant in the proposed Grata model:
- transaction-level funding amount → `round_size`
- investor-specific check → `transaction_party.investment_amount`
- common product magnitude → `transaction_size`

Do not add a second transaction-level `investment_amount` metric unless a concrete product use case later requires it.

# E. Financial metric model

## E1. Current `FINANCIAL_METRIC_SCHEMA`

| Current field | Shape | Decision | Recommendation |
|---|---|---:|---|
| `financial_id` | ID | KEEP | Metric-row identifier. |
| `transaction_id` | FK | KEEP | Parent transaction. |
| `metric_type` | ENUM | CHANGE SELECT VALUES | Keep enum; rename adjusted-only canonical metric types as below. |
| `period_type` | ENUM | KEEP | Required for usable period metrics. |
| `period_end_date` | DATE | KEEP | Metric-specific date. |
| `period_end_date_precision` | ENUM | KEEP | Exact/month/quarter/year. |
| `value_captured` | DATA POINT | KEEP | Metric amount/value. |
| `value_currency` | ISO string | KEEP | Currency where monetary. |
| `fx_rate` | DATA POINT | KEEP | FX metadata. |
| `fx_rate_date` | DATE | KEEP | FX metadata. |
| `margin_pct` | DATA POINT | KEEP | Margin when applicable. |
| `is_calculated` | FLAG | CHANGE / AUGMENT | Useful but insufficient for full valuation basis/provenance. |

## E2. `MetricType`

### Keep
- `EQUITY_VALUE`
- `TRANSACTION_VALUE`
- `REVENUE`
- `GROSS_PROFIT`
- `FREE_CASH_FLOW`
- `TOTAL_DEBT`
- `CASH_AND_EQUIVALENTS`
- `NET_DEBT`
- `SHAREHOLDERS_EQUITY`
- `ARR`
- `MRR`
- `POST_MONEY_VALUATION`
- `PRE_MONEY_VALUATION`
- `ROUND_SIZE`
- `IMPLIED_EQUITY_VALUE`
- `IMPLIED_ENTERPRISE_VALUE`

### Change
- `ADJ_EBITDA` → `EBITDA`
- `ADJ_EBIT` → `EBIT`
- `ADJ_NET_INCOME` → `NET_INCOME`
- `ENTERPRISE_VALUE` → remove/deprecate as competing canonical metric; routes converge to `IMPLIED_ENTERPRISE_VALUE`

If adjusted-vs-reported variants need to be preserved later, represent adjustment status separately rather than making “adjusted” the only canonical metric.

### Equity multiples / tangible book

Current Grata `MultipleType` already includes **`PE`, `PB`, and `PTBV`**. `MetricType` includes `SHAREHOLDERS_EQUITY` and `ADJ_NET_INCOME` (recommended rename to `NET_INCOME`).

Therefore:
- P/E already exists; verify the `NET_INCOME` denominator path.
- P/B already exists; calculate against `SHAREHOLDERS_EQUITY` where appropriate.
- Keep `PTBV` for source-reported multiples, but do **not** add a new tangible-book financial metric or extraction requirement in this phase. Calculated P/TBV remains unsupported unless a usable denominator already exists.

## E3. Period continuity

`period_type` exists at Gold, but current Silver `reported_revenue` / `reported_ebitda` scalars are period-untagged. This remains a real end-to-end model gap: LTM vs. NTM must survive collection/reconciliation, not be reconstructed after the fact.

---

# E4. Metric-row policy: FX, currency and provenance *(v0.4 — ENG directive 3)*

ENG asked for a **consistent FX/provenance policy across all metric rows**. v0.3 had the
components scattered across five items (§D3, D-item 9, 10, 11, 13). Stated once, as
normative rules that apply to **every** row in `financial_metric` regardless of class:

1. **Currency attaches to the value it qualifies.** A metric row never inherits currency
   from another row, from the transaction, or from the source's other figures. Unstated
   currency is `NULL`, never a default and never the neighbouring row's.
2. **The same rule governs period.** No row inherits `period_type` or `period_end_date`
   from another row.
3. **No implicit conversion, ever.** `fx_rate` / `fx_rate_date` **record a conversion that
   was performed**; their presence is a fact about the row, never a licence to convert one.
   A row with no accepted conversion stays in its stated currency.
4. **Source-stated USD is preferred over converted USD**, and the two must remain
   distinguishable. This is the open `value_usd_basis` question (§O).
5. **Debt-inclusive arithmetic requires both currencies known and equal.** Unknown is not a
   match. Refuse and emit `NULL` rather than compute across an unknown pair.
6. **Per-fact provenance on every row** — source attribution plus a fact key — so two
   figures from one article are distinguishable from one figure corroborated by two.
7. **Basis is not a boolean.** `is_calculated` records *that* a value was derived;
   the basis attribute records *how*. Both are needed; the boolean alone is insufficient.

Rules 1, 2 and 3 are the ones that fail quietly. A missing currency that inherits a
neighbour's produces a plausible wrong number rather than a visible gap, which is the
failure class the harness hit and the reason these are stated as refusals rather than
preferences.

## Should `transaction_size` be a metric row? *(ENG directive 3)*

**Assessment: yes — with one condition, and the condition is the whole answer.**

For: it is a monetary value with a currency, and every rule in §E4 applies to it exactly as
it applies to `TRANSACTION_VALUE`. Leaving it as a bare scalar on `transaction_record`
reproduces precisely the problem §D0 identifies — a value with nowhere to record its
currency, provenance or basis.

Against, and this is real: `transaction_size` is **derived from other rows in the same
table**. Any consumer that sums `DEAL_VALUE` rows would count the same money twice, once as
`TRANSACTION_VALUE` and again as `TRANSACTION_SIZE`. §D4 already forbids summing across
bases; making it a peer row inside `DEAL_VALUE` turns a documented prohibition into a trap.

**Recommendation:** admit it as a metric row with `metric_type = TRANSACTION_SIZE`, and
classify it into a **third class, `DERIVED_ROLLUP`**, rather than into `DEAL_VALUE`. The
two-class split in §D0 exists to drive different QA rules; a rollup needs a third set —
never summed, never a multiple numerator, always carrying `transaction_size_basis`, and
always traceable to the row it was selected from. `transaction_size_basis` becomes that
row's basis attribute, satisfying rule 7 without a bespoke column.

Whether the scalar is *also* retained on `transaction_record` as a cached denormalization
for product read paths is an **ENG DECISION**. The semantic requirement is that the metric
row is the source of truth and the scalar, if kept, is a copy that Engineering owns keeping
fresh — not a second place the value can be authored.

---

# F. Transaction multiple model

## F1. Current `TRANSACTION_MULTIPLE_SCHEMA`

| Current field | Shape | Decision | Recommendation |
|---|---|---:|---|
| `multiple_id` | ID | KEEP | Row identifier. |
| `transaction_id` | FK | KEEP | Parent transaction. |
| `multiple_type` | ENUM | KEEP | EV/Revenue, EV/EBITDA, EV/EBIT, EV/FCF, PE, PB, PTBV. |
| `multiple_value` | DATA POINT | KEEP | Reported or calculated multiple. |
| `period_basis` | ENUM | KEEP | LTM/NTM/ANNUAL/QUARTERLY. |
| `period_end_date` | DATE | KEEP | Denominator period end. |
| `period_end_date_precision` | ENUM | KEEP | Date precision. |
| `numerator_value_type` | ENUM | CHANGE VALUES | Use canonical 100%-basis values. |
| `denominator_financial_id` | FK | KEEP | Calculated denominator link. |
| `source_flag` | ENUM | KEEP | `as_reported` / `calculated`. Researcher entry may still represent an as-reported multiple. |
| `quality` | ENUM | KEEP / VERIFY | Preserve current quality semantics. |
| `value_usd_basis` | DATA POINT | KEEP / VERIFY | Confirm exact intended meaning/name. |
| `is_calculated` | FLAG | REVIEW REDUNDANCY | Overlaps `source_flag`; keep during migration unless ENG simplifies. |

### `NumeratorValueType`

Current:
- `enterprise_value`
- `equity_value`

Recommend:
- `implied_enterprise_value`
- `implied_equity_value`

As-transacted `transaction_value` and stake-level `equity_value` are never multiple numerators.

### Multiple families

- Enterprise-value multiples: EV/Revenue, EV/EBITDA, EV/EBIT, EV/FCF
- Equity-value multiples: P/E, P/B, P/TBV

P/B is already supported at enum level through `PB` + `SHAREHOLDERS_EQUITY`; verify the calculation path rather than adding a new multiple type.

P/TBV remains lower priority.

### As-reported vs. calculated

Preserve both. Display precedence when both exist for the same type/period is a product rule and remains open.

---

# G. Spin / Split model

## G1. Existing / event-specific

Keep:
- `spin_split_type`
- rename `distribution_mechanism` → `spin_split_distribution_mechanism`

**v0.4 — ENG directive 4: prefer transaction-level scalars over a child table.** ENG
asked whether the mechanics justify their own child structure, preferring transaction-level
scalar mechanics plus generalized security/share mechanics plus financial metrics unless
cardinality actually requires one. Assessed field by field, **it does not**:

| mechanic | cardinality per transaction | placement |
| --- | --- | --- |
| record date, distribution date | one | transaction-level scalar |
| pct distributed, distribution ratio | one *(but see multi-class below)* | transaction-level scalar |
| pct parent shares exchanged (split-off) | one | transaction-level scalar |
| parent / distributed / tendered share counts | **one per security class** | generalized security & share mechanics (§B) |
| `spin_split_share_price` | one per security | the referenced SpinCo security price — never duplicated |
| `spin_split_consideration_value` | one | `financial_metric` row (§E4) |

The only genuine cardinality above one is **multi-class distributions** — a parent with
Class A and Class B shares can carry a different ratio and count per class. That is not a
Spin/Split problem, it is a security problem, and §B's generalized security/share mechanics
already exist to solve it. Building a Spin/Split-specific child table would create a second
security model for one event family, which §G2 already forbids for exactly this reason.

**Recommendation: CHANGE from v0.3.** No `spin_split_mechanics` child structure. Scalars on
the transaction, share mechanics in the security model, values in `financial_metric`. If a
future mechanic turns out to be genuinely multi-valued *and* not security-shaped, revisit —
but nothing in the current set is.

*(v0.3 text retained below for the field list; the child-structure suggestion in it is
superseded.)*

| Field | Shape | Population | Decision |
|---|---|---|---:|
| `spin_split_record_date` | DATE | Source / researcher | ADD |
| `spin_split_distribution_date` | DATE | Source / researcher | ADD |
| `spin_split_pct_distributed` | DATA POINT | Source / researcher | ADD |
| `spin_split_distribution_ratio` | DATA POINT | Source / researcher | ADD |
| `split_off_pct_parent_shares_exchanged` | DATA POINT | Source / calculated | ADD |

## G2. Reuse security/share mechanics

Do not create a second independent security model for Spin/Split.

- Parent and SpinCo security type / price / price date should reuse the generalized transaction-security capability where practical.
- `spin_split_parent_shares`, `spin_split_distributed_shares`, and `split_off_shareholder_shares_tendered` are event-specific security counts. **v0.4:** they belong in the generalized security/share mechanics keyed by security class, not on a Spin/Split child table — that is the one place multi-class cardinality is real.
- `spin_split_share_price` should preferably be represented by the referenced SpinCo security price rather than duplicated.
- `spin_split_consideration_value` is a derived event value. **v0.4:** it belongs in `financial_metric` under the §E4 policy like any other monetary value; its primary common-product consumer is `transaction_size`.

Rules:
- target = SpinCo
- no acquirer is required
- missing SpinCo domain is valid until one exists
- record/distribution dates are mechanics dates, not lifecycle events

# H. Transaction party model

## H1. Current `TRANSACTION_PARTY_SCHEMA`

| Current field | Shape | Decision | Recommendation |
|---|---|---:|---|
| `party_id` | ID | KEEP | Party-participation identifier. |
| `transaction_id` | FK | KEEP | Parent transaction. |
| `party_name` | DATA POINT | KEEP | Name/fallback display. |
| `party_type` | ENUM / entity attribute | KEEP / REVIEW PLACEMENT | Prefer canonical entity profile when resolved. |
| `role` | ENUM | KEEP | Transaction role. |
| `is_lead` | FLAG | KEEP | Lead participant. |
| `party_company_id` | FK | KEEP | Canonical company entity. |
| `entity_resolution_status` | ENUM | KEEP | Resolution state. |
| `unresolved_party_name` | DATA POINT | KEEP | Fallback when unresolved. |
| `round_participation_pct` | DATA POINT | KEEP | Investor participation. |
| `target_ownership_pct_after` | DATA POINT | KEEP | Party-level resulting ownership. |
| `investment_amount` | DATA POINT | KEEP | Investor-specific check, not transaction round size. |
| `investment_currency` | ISO string | KEEP | Investor check currency. |
| `is_new_investor` | FLAG | KEEP | Existing current field; if a future `new_existing_investor` enum is desired, treat as a separate rename/design decision. |
| `investment_amount_usd` | DATA POINT | KEEP | Converted investor check. |
| `advisor_specialty` | ENUM | KEEP / EXPAND AFTER MAPPING | Current set is incomplete for all possible advisory services. |
| `advised_party` | ENUM / relationship | KEEP / IMPROVE GRANULARITY | Preserve which client/participant is advised. |
| `advisor_person_name` | DATA POINT | CHANGE MODEL | Single person is insufficient. |
| `advisor_person_title` | DATA POINT | CHANGE MODEL | Move to repeating advisor-person child relationship. |
| `lender_role` | ENUM | KEEP | Actual lender/financing-provider role. |
| `party_source` | ENUM | KEEP | Provenance. |
| `party_source_url` | URL | KEEP | Provenance link. |

## H2. Current `PartyRole`

Keep:
- `TARGET`
- `BUYER`
- `SELLER`
- `INVESTOR`
- `SPONSOR`
- `PARENT_SELLER`
- `ADVISOR_BUY_SIDE`
- `ADVISOR_SELL_SIDE`
- `LENDER`
- `JV_PARTNER`
- `UNDERWRITER`

Need:
- Current Grata has generic `SPONSOR` but **does not have `SELLER_SPONSOR` / `BUYER_SPONSOR` roles**. Preserve side/participant granularity either through a `sponsored_party_id` / relationship or, if ENG prefers flat roles, explicit side-specific sponsor roles. Relationship is preferred because it scales to multiple buyers/sellers.
- Legal acquisition vehicles / merger subs must be representable as transaction-context participants without contaminating `acquirer_type`.

The harness already uses richer transaction-context roles such as buyer/seller sponsor, buyer/seller platform, parent acquirer/seller and merger sub. Exact Grata representation is an ENG decision; the semantic relationships should not be lost.

## H3. Advisor people

One advisor firm participation may have many advisor people.

Recommended child capability:

`transaction_advisor_person`
- `advisor_person_id`
- advisor-firm participation FK
- optional canonical `person_id` where matched
- person name
- title/seniority
- person specialty where available
- optional lead indicator
- source/provenance

**Cardinality rule:** many people never create duplicate advisor-firm participations.

### v0.4 — ENG directive 6: the extraction-layer requirement

ENG expanded this from a Grata-schema note into a **collection requirement**: extraction
must preserve **person ↔ firm affiliation** and **transaction role** wherever the source
states them, *even though canonical people/entity ownership belongs to another team.*

That division is the point. Two different things are being conflated when "we don't own
people" is used to justify dropping them:

- **Identity** — who this person is, canonically, across transactions. Another team's.
- **Affiliation and role in this transaction** — that Sanjay Chadda acted for Canaccord
  Genuity on the sell side of *this* deal. That is a **transaction fact**, it exists only in
  this source, and if extraction discards it nobody can reconstruct it later. No amount of
  downstream entity resolution recovers a name that was never captured.

The requirement is therefore: **capture unresolved, resolve optionally.** Store person name,
title/seniority, the firm participation they are attached to, and the side/role, with
`person_id` left null until and unless resolution succeeds.

**Current state — this is a real gap, not a refinement.** The harness `advisor` table holds
`name` (the *firm*), `type` and `advised_party` and has **no person fields at all**, while
the LC extraction prompt's own review note already identifies the shortfall and quotes a
real source: *"Canaccord Genuity (sell-side), led by Sanjay Chadda and Lexia Schwartz…
Juan Mejia at BrightTower, buy-side."* Everything needed is in the text and none of it is
retained. Grata's single `advisor_person_name` / `advisor_person_title` pair would truncate
that example to one of the three people.

**Scope beyond advisors.** The same shape applies to investor-side people — a partner named
as leading a round or taking a board seat is a person ↔ firm ↔ role fact of exactly this
kind. The requirement is stated at the participation level, not the advisor level, so it
generalizes without a second design.

**Not in scope here:** canonical person entities, deduplication across transactions, and
person profile attributes. Those stay with the owning team; this document asks only that the
transaction-level facts survive collection so that team has something to resolve.

Current Grata already has `advisor_person_name` and `advisor_person_title`, but only one pair per advisor party row; the gap is **cardinality**, not complete absence. Person matching should be optional: store an unresolved person name/title first and attach a canonical `person_id` when/if entity resolution succeeds.

## H4. `advisor_specialty` and advised-party role *(v0.4 — ENG directive 7)*

Expansion is **accepted**. v0.3 listed candidate additions speculatively ("e.g. PR/…, tax,
restructuring…"); ENG asked for current vs proposed enumerated **from the actual extraction
vocabulary**. Enumerated below from `prompts/low_confidence_extraction.md` and
`schema/001_initial.sql`, not from imagination.

### What is actually collected today

| layer | field | values |
| --- | --- | --- |
| harness extraction | `advisor_type` | `FINANCIAL`, `LEGAL`, `OTHER` |
| harness extraction | `advised_party` | `TARGET`, `ACQUIRER`, `PARENT_SELLER`, `BOTH`, `UNKNOWN` |
| harness storage | `advisor.type`, `advisor.advised_party` | same three / same five |
| Grata | `advisor_specialty` | `financial_advisory`, `legal`, `accounting`, `fairness_opinion`, `regulatory` |
| Grata | `PartyRole` | `ADVISOR_BUY_SIDE`, `ADVISOR_SELL_SIDE` |

### The finding: the two vocabularies fail in opposite directions

**Collection is coarser than Grata.** Three values against five. And the prompt's own
definition of `OTHER` names the missing specialties explicitly: *"'OTHER' covers fairness
opinion providers, proxy solicitors, info agents, and accounting/tax advisors."* Four
distinct specialties are being collapsed into one bucket **by written instruction** — the
evidence is in the text and is discarded at the enum. That is the concrete case for
expansion, and it is stronger than a speculative list because the sources demonstrably
carry the distinction.

**Grata has values collection cannot produce.** `regulatory` has no extraction path at all,
and `accounting` cannot be separated from tax because both land in `OTHER`. Expanding the
Grata enum without moving the extraction vocabulary would add values that stay permanently
empty — the same trap flagged for `round_price_direction` in §A6.

### Proposed `advisor_specialty`

| value | status | grounded in |
| --- | --- | --- |
| `financial_advisory` | KEEP | maps from `FINANCIAL` |
| `legal` | KEEP | maps from `LEGAL` |
| `fairness_opinion` | KEEP | named in the `OTHER` definition |
| `accounting` | KEEP | named in the `OTHER` definition |
| `tax` | **ADD** | named in the `OTHER` definition, currently inseparable from accounting |
| `proxy_solicitation` | **ADD** | named in the `OTHER` definition |
| `information_agent` | **ADD** | named in the `OTHER` definition |
| `regulatory` | KEEP / **VERIFY** | exists in Grata, **no extraction path** — confirm a source of population or accept it as researcher-only |
| `restructuring`, `capital_markets`, `communications` | **DEFER** | plausible, but no current extraction evidence. Do not freeze into the enum on speculation — add when a real source produces one. |

`LENDER` remains distinct from a financing/debt advisor. Providing capital and advising on
obtaining it are different participations.

### Advised-party granularity

`advised_party = BOTH` has **no Grata equivalent** — `ADVISOR_BUY_SIDE` and
`ADVISOR_SELL_SIDE` are side-specific and cannot express one advisor serving both. Either
Grata needs a both-sides representation or collection must stop emitting `BOTH`; silently
mapping it to one side would assert a fact the source did not state. **ENG DECISION.**

`PARENT_SELLER` maps to sell-side but loses the parent/seller distinction that
`PartyRole.PARENT_SELLER` preserves elsewhere in the model — worth keeping aligned.

---

# I. Transaction event history and status

## I1. Current `TRANSACTION_EVENT_HISTORY_SCHEMA`

| Field | Decision |
|---|---:|
| `event_id` | KEEP |
| `transaction_id` | KEEP |
| `type` | KEEP / CLARIFY current-status mapping |
| `date` | KEEP |
| `date_precision` | KEEP |

Current event-history enum includes:
- status-bearing: `RUMORED`, `ANNOUNCED`, `CLOSED`, `TERMINATED`
- milestones: `EXPECTED_CLOSE`, `FILED`, `EFFECTIVE`, `AMENDED`, `UPDATED`, `SOURCED`

Product/current status should remain date-driven, with the primary transaction states:
- `ANNOUNCED`
- `CLOSED`
- `TERMINATED`

`RUMORED` may remain in history without necessarily becoming a primary product status.

`signing_date` is an independent transaction fact and may precede announcement. Grata currently has no dedicated signed event/date in this schema; preserve the semantic requirement and let ENG decide placement.

Consideration changes do not change deal status.

---

# J. Review / disclosure / operational fields

| Concept | Current state | Decision | Notes |
|---|---|---:|---|
| `financials_disclosure_status` | Exists on `transaction_record` | KEEP / NARROW | Covers company/target financial metrics and balance-sheet information. |
| `transaction_terms_disclosure_status` | Missing | ADD | Covers deal economics/consideration/value terms. Reuse `DISCLOSED / PARTIALLY_DISCLOSED / UNDISCLOSED / UNKNOWN`. |
| `RecordReviewStatus` enum | Exists, field absent from `TRANSACTION_RECORD_SCHEMA` | DEFER / VERIFY | |
| field-level null reasons | Missing | DEFER / NOT REQUIRED | |
| detailed researcher-review workflow | Not core data model | DEFER | |

Example: a PR may state “transaction terms were not disclosed” while also disclosing revenue. That should resolve to:
- `transaction_terms_disclosure_status = UNDISCLOSED`
- `financials_disclosure_status = DISCLOSED` or `PARTIALLY_DISCLOSED`

Conversely, a deal may disclose price/EV but no target financials. The two statuses are intentionally independent.

The data model should not require a reason for every blank field.

# J2. Conditional requiredness

Requiredness should be documented as business/QA rules even if the physical schema remains nullable.

Initial matrix:
- `transaction_id` — **REQUIRED**
- canonical `event_type` — **REQUIRED** once the record reaches canonical Gold
- target/issuer/primary subject party — **CONDITIONAL REQUIRED** by event family
- acquirer/buyer — **CONDITIONAL REQUIRED** for acquisition-style M&A when known; **NOT APPLICABLE** to Spin/Split and funding events
- `announcement_date` — **CONDITIONAL REQUIRED** for announced/closed/terminated surfaced transactions
- `close_date` — **REQUIRED when current status = CLOSED**
- `termination_date` — **REQUIRED when current status = TERMINATED**
- `transaction_size_basis` — **REQUIRED when `transaction_size` is populated**
- financial metric `period_type` / `period_end_date` — **CONDITIONAL REQUIRED** for period-based company financials

This is a data-quality contract, not a mandate to make every field SQL `NOT NULL`.

# K. Silver / collection-layer parity issues

These are implementation-continuity items, not new business concepts.

1. `SILVER_TRANSACTION_HEADER_SCHEMA.reported_revenue` and `.reported_ebitda` are period-untagged even though Gold requires `period_type`. This blocks reliable LTM/NTM preservation.
2. Silver contains `enterprise_value` / `reported_ev`; canonical Gold treatment should converge to `IMPLIED_ENTERPRISE_VALUE`.
3. Silver funding scalar `post_evaluation` should reconcile to canonical `POST_MONEY_VALUATION`.
4. Silver `amount_raised` should reconcile to canonical `ROUND_SIZE`.
5. Silver party schema currently omits advisor/lender fields; add only once a source actually populates those parties.
6. Silver `ownership_status` is legacy/undefined and should be reviewed before long-term retention.
7. Migration fields `type`, `type_original`, `event_type` coexist intentionally; event-taxonomy changes must update mappings consistently.

---

# L. Source mapping changes implied by the model

Current `CBI_EVENT_TYPE_MAP` contains mappings that conflict with the tested/accepted direction:

| Current source mapping | Issue / recommended review |
|---|---|
| `private_equity -> GROWTH_EQUITY` | PE buyouts should be M&A `ACQUISITION`; true growth financing remains `GROWTH_EQUITY`. |
| `merger -> MERGER` / `portfolio_merger -> MERGER` | Revisit if merger moves to `is_merger` under M&A. |
| `corporate_round -> MINORITY_INVESTMENT` | Minority core type is being removed; route by underlying financing/acquisition event and set `is_minority`. |
| `pipe -> MINORITY_INVESTMENT` | Same; public primary issuance should not be forced to Growth/VC unless underlying event supports it. |
| `debt_financing`, `bridge` -> `VENTURE_DEBT` | Too broad; verify genuine venture/growth debt. |

Mappings for `asset_sale` and `unit_acquisition` to `ACQUISITION` are directionally consistent with `target_type` providing the structural granularity.

---

# O. Cross-cutting open questions *(v0.4 — ENG directives 8 and 9)*

Carried forward from `grata_v2_reconciliation_2026_08_17.md` §5, plus one new entrant.
None is resolvable inside a single section of this document; each cuts across the model.

### O1. Observation supersession — **corrected; this supersedes the earlier framing**

`grata_v2_reconciliation_2026_08_17.md` previously stated, in three places, that the
harness ledger is append-only and has no `is_current` handling. **All three have been
corrected in that document** (item 14, the Defer list, and §5 open question 1), and
`runbook_path_b_reextraction.md` has been scoped so its append-oriented claim reads as the
Path-B-specific statement it always was. This section is the account of record; no
contradictory text remains.

The claim was: *"The harness ledger appends and has no `is_current`."*
**Both halves are incorrect**, verified against the code:

- `transaction_field_observation` **has** an `is_current` column
  (`schema/001_initial.sql`).
- It is **written**: `stages/agreement_extract.py` soft-deletes a document's prior
  observations inside a savepoint before re-extracting it —
  `UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?`.
- Stage 9 **honours** it: the aggregation read is gated on `WHERE tfo.is_current = 1`.

So supersession is not undecided in the harness. It is **decided for one producer and
scoped to `source_document_id`**: re-extracting an agreement document supersedes that
document's facts, atomically. What does **not** exist is any equivalent keyed on
`source_raw_id` — press-release re-extraction, which is exactly the Path B case.

This sharpens the question rather than closing it. The right framing for Grata is not
"append or supersede?" but **"what is the supersession key?"** The harness answers
*document*, and has no answer for *source*. A model that re-collects from a changing source
needs both, and they behave differently: a document is immutable once filed, a web source
is not.

**Status: ENG DECISION**, and the most likely blocker on any large re-collection.

### O2. Silver / Gold financial system-of-record

If period-untagged Silver scalars feed Gold, the per-fact provenance recommended in §E4
rule 6 is destroyed before Gold ever sees it. Whether Gold can re-derive from retained
observations, or only from Silver's collapse, **determines how much of §E4 is achievable at
all** — this question gates the metric policy rather than sitting beside it. Should be
answered first.

### O3. `value_usd_basis`

Semantics unverified. Candidate meaning: *the figure the source itself stated in USD, not a
converted one.* The alternative — that it denotes a conversion Grata performed — is the
**opposite** meaning, and the field name does not disambiguate. §E4 rule 4 depends on the
answer.

### O4. FX / conversion policy

Grata carries `fx_rate` / `fx_rate_date` but states no policy on when a conversion may
occur, who performs it, and what date anchors it. §E4 rule 3 is this document's position;
it needs Grata-side adoption. Until then cross-currency transactions are silently absent
from EV-based analyses rather than visibly incomplete — an availability problem disguised
as a data problem.

### O5. Period-coherence tolerance

D2 requires `total_debt` and `cash_and_equivalents` to be period-coherent without defining a
tolerance. The harness requires an **exact** shared as-of date, deliberately, because no
tolerance was invented in the absence of evidence. Real filings may state debt and cash days
apart. **Not resolvable without live cases**, and the corpus has none. Resolve after the
first real sample.

### O6. Multiple economic events per source *(new — from the PIPE / Ensysce finding)*

A single source can carry more than one independently profileable economic event. The
concrete case: an Ensysce release announcing **an in-scope acquisition and a concurrent
private placement of convertible preferred**. See decisions.md, "PIPE: Unresolved
Architecture and Product Findings".

The model question is whether a transaction record can represent, or link, an acquisition
and a concurrent financing **independently** — rather than forcing one source to yield one
transaction, which is what both the harness and the current Grata shape assume.

Two failure modes follow from the assumption, and they are different:

1. **Loss** — the second event is simply not represented.
2. **Contamination** — worse, and non-obvious: if the only monetary figure in such a source
   belongs to the financing, extraction built for the acquisition has nothing stopping it
   reading that figure as the acquisition's consideration. A wrong value, not a gap.

The harness currently avoids the acquisition being *displaced* by not acting — PIPE
recognition never overrides `ACQUISITION` — but that is suppression by omission, not
component-level handling, and it does nothing about contamination.

**Status: ENG DECISION / architecture.** Explicitly **not** a PIPE patch. It touches the
transaction/event cardinality assumption, related-transaction linkage (currently §M
deferred), and per-event value attribution. Whether the contamination risk is live depends
on source text that was not readable when this was written.

---

# M. Deferred / lower-priority domains

Keep current structures but do not redesign in this phase:
- recapitalization
- IPO / Direct Listing / Carve-out IPO
- detailed P/TBV denominator enrichment
- consideration amendment/version history
- related-transaction linkage
- field-level null reasons
- detailed researcher-review workflow
- broad SEC financial-statement mining

---

# N. Current likely ENG change set

1. **Event semantics**
   - minority → `is_minority`
   - merger → `is_merger` under M&A
   - reverse merger → `is_reverse_merger` under M&A
   - de-SPAC → existing `is_de_spac` under M&A rather than separate canonical event type
   - move divestiture from event category to `is_divestiture`; add/recognize Spin/Split as its own broad category
   - clarify JV formation boundary
   - update source mappings accordingly

2. **Ownership**
   - add `is_minority`
   - add `stake_transition_type`

3. **Target/security/share mechanics**
   - add `target_type`
   - add `target_security_type`
   - add `exchange_ratio`
   - add target/acquirer share-count mechanics

4. **Consideration**
   - implement normalized repeating/security-level `consideration_component`
   - derive `consideration_type` summary from components

5. **Spin/Split**
   - explicit mechanics beyond type/mechanism

6. **Values**
   - add common `transaction_size` + explicit waterfall `transaction_size_basis`
   - converge canonical EV on `implied_enterprise_value`
   - keep investor-level `investment_amount`; use `ROUND_SIZE` for funding transaction amount
   - distinguish deal-value metrics from company financial metrics semantically
   - preserve valuation derivation basis beyond a single `is_calculated` boolean

7. **Financials / multiples**
   - neutral metric names (`EBITDA`, `EBIT`, `NET_INCOME`)
   - numerator enum → implied EV / implied equity
   - preserve period metadata end-to-end
   - verify P/E, P/B denominator paths

8. **Parties**
   - advisor firm → many advisor people; person matching optional
   - preserve sponsor/client relationship granularity, including seller-side sponsors
   - expand advisor specialty only after source-value mapping

9. **Disclosure**
   - keep `financials_disclosure_status` for company financials
   - add `transaction_terms_disclosure_status` for deal terms/value/consideration

This remains an incremental extension and semantic cleanup of Grata V2, not a wholesale redesign.

---

# P. v0.4 recommendation table *(post-Engineering review)*

Verdicts: **KEEP** · **CHANGE** · **ADD** · **REMOVE-DERIVABLE** · **DEFER** · **ENG DECISION**.

The **Δ v0.3** column is the one to scan: ✱ marks every row where Engineering's review
changed the prior recommendation. Fourteen rows changed; the rest of v0.3 stands.

## Event and feature model

| Concept | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| `event_type = ACQUISITION` as the M&A umbrella | KEEP | | Unchanged. |
| `combination_structure` — **hierarchical**, `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER` | **ADD** | ✱ | v0.3 proposed three parallel flags. They nest rather than compete, so the dimension is hierarchical with documented implication — **not** three mutually exclusive peers, which would lose the nested facts. Query by implication, never equality. §A6.1 |
| `is_merger`, `is_reverse_merger`, `is_de_spac` | **REMOVE-DERIVABLE** | ✱ | Roll up from `combination_structure`. v0.3 added the first two, kept the third. §A7 |
| `is_merger_of_equals` | KEEP | | Qualifies a merger rather than competing with it. Gains a conditional-applicability rule. |
| `management_participation` (`MBO`/`MBI`/`BIMBO`) | **ADD** | ✱ | v0.3 kept `is_mbo` + `is_mbi`. The dimension names a real third state the booleans could only express as both-true. §A6.2 |
| `is_mbo`, `is_mbi` | **CHANGE** | ✱ | Replaced by the above. |
| `round_price_direction` (`UP`/`DOWN`/`FLAT`/`NULL`) | **ADD** | ✱ | v0.3 kept both flags. Both-false conflates *flat* with *unknown*. §A6.3 |
| `is_up_round`, `is_down_round` | **CHANGE** | ✱ | Replaced by the above. Collection vocabulary must move with it — the harness emits `is_down_round` only. |
| `sponsor_investment_role` (`PLATFORM`/`ADD_ON`) | **ADD** | ✱ | Mutually exclusive; both-true is meaningless. §A6.5 |
| `is_platform_investment`, `is_add_on` | **CHANGE** | ✱ | Replaced by the above. |
| `is_take_private`, `is_lbo`, `is_secondary_buyout` | KEEP | | **Confirmed orthogonal.** Prior status, financing, and seller identity are different axes; one deal can be all three. §A6.5 |
| `recap_type` | KEEP | | Surviving representation of recap mechanism. Domain redesign stays DEFER. |
| `is_dividend_recap`, `is_equity_recap`, `is_leveraged_recap`, `is_sponsor_recap` | **REMOVE-DERIVABLE** | ✱ | Duplicate `recap_type`. v0.3 preserved all four. The **only** flags removable today — no precondition. §A7 |
| `DIVIDEND` vs `LEVERAGED` co-occurrence; `SPONSOR` as a separate axis | **ENG DECISION** | | Settle from real recap examples; do not invent now. |
| `is_unicorn_round` | KEEP | | Assessed for derivation and **rejected** — sources assert it without stating post-money. §A7 |
| `is_minority`, `stake_transition_type`, `target_type` | ADD | | Unchanged from v0.3. |

## Consideration and derivable summaries

| Concept | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| `consideration_component` normalized child | **ADD** | | Accepted, unchanged. Now also the precondition for four removals below. |
| `is_stock_for_stock` | **REMOVE-DERIVABLE** | ✱ | ENG's named candidate, confirmed. **Conditional on components being populated.** §A7 |
| `has_earnout`, `has_cvr` | **REMOVE-DERIVABLE** | ✱ | Same derivation, same precondition. |
| `consideration_type` | REMOVE-DERIVABLE | | Already documented as derived in v0.3; relabelled for consistency. |
| Consideration semantics, aggregation rules (§C3) | KEEP | | **Accepted by ENG**, unchanged. |

## Values and metrics

| Concept | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| `financial_metric` as the **preferred** home for deal-value **and** company-financial metrics | **CHANGE** | ✱ | v0.3 said a second table "is not required"; ENG makes it a preference. Stronger claim. §D0 |
| Unified FX / currency / provenance policy across all metric rows | **ADD** | ✱ | v0.3 had the components scattered across five items. Stated once as seven normative rules. §E4 |
| `TRANSACTION_SIZE` as a metric row | **ADD** | ✱ | Assessed at ENG's request: yes, **but classified `DERIVED_ROLLUP`, not `DEAL_VALUE`** — as a peer row it would be double-counted by any consumer summing deal values. §E4 |
| `transaction_size` scalar retained on `transaction_record` as a cache | **ENG DECISION** | | Metric row is the source of truth; a cached scalar is a copy ENG owns keeping fresh. |
| `transaction_size` / `transaction_value` / `equity_value` semantics, `transaction_size_basis` | KEEP | | **Accepted by ENG**, unchanged. |
| `transaction_party.investment_amount` investor-level | KEEP | | **Accepted by ENG**, unchanged. |
| Multiples model, `NumeratorValueType` → `implied_*` | KEEP | | **Accepted by ENG**, unchanged. |

## Spin / Split

| Concept | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| `spin_split_mechanics` child table | **CHANGE** | ✱ | **Not adopted.** No mechanic is multi-valued except per-security-class counts, which belong to the security model. §G |
| Record/distribution dates, pct distributed, ratio, pct exchanged | ADD (transaction-level scalars) | ✱ | Placement changed from child table to transaction scalars. |
| Parent / distributed / tendered share counts | ADD (generalized security mechanics) | ✱ | The one real cardinality case, handled by §B. |
| `spin_split_consideration_value` | **CHANGE** | ✱ | Becomes a `financial_metric` row under §E4. |
| `spin_split_type`, `spin_split_distribution_mechanism` rename | KEEP / CHANGE NAME | | Unchanged. |

## Parties, people, advisors

| Concept | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| `transaction_party` recommendations | KEEP | | **Accepted by ENG**, unchanged. |
| Person ↔ firm affiliation + transaction role preserved at **extraction** | **ADD** | ✱ | Expanded from a Grata-schema cardinality note to a collection requirement. Capture unresolved, resolve optionally. Harness currently stores **no person fields at all**. §H3 |
| Investor-side people (round lead, board seat) | ADD | ✱ | Same shape; requirement stated at participation level so it generalizes. |
| Canonical person entities / dedup / profiles | DEFER | | Another team's ownership; out of scope here. |
| `advisor_specialty` expansion | **ADD** | | Accepted. Now enumerated from real extraction vocabulary rather than speculation. §H4 |
| `tax`, `proxy_solicitation`, `information_agent` | **ADD** | ✱ | Each named explicitly in the extraction prompt's own `OTHER` definition — evidence exists and is discarded at the enum. |
| `regulatory` | KEEP / **VERIFY** | ✱ | Exists in Grata with **no extraction path**. Confirm a source of population or accept as researcher-only. |
| `restructuring`, `capital_markets`, `communications` | **DEFER** | ✱ | v0.3 listed them as candidates. No extraction evidence; do not freeze on speculation. |
| `advised_party = BOTH` with no Grata equivalent | **ENG DECISION** | ✱ | Needs a both-sides representation, or collection stops emitting it. Mapping it to one side would assert an unstated fact. |

## Cross-cutting

| Question | Verdict | Δ v0.3 | Note |
| --- | --- | :---: | --- |
| Observation supersession | **ENG DECISION** | ✱ | **Reconciliation corrected in three places** — `is_current` exists, is written by agreement re-extraction, and is honoured by Stage 9. Real question is the *supersession key*: document-scoped exists and is live, source-scoped does not. §O1 |
| Silver / Gold financial system-of-record | ENG DECISION | | **Gates §E4** rather than sitting beside it. Answer first. §O2 |
| `value_usd_basis` semantics | ENG DECISION | | Two candidate meanings that are opposites. §O3 |
| FX / conversion policy | ENG DECISION | | §E4 rule 3 is this document's position; needs Grata-side adoption. §O4 |
| Period-coherence tolerance | DEFER | | Not resolvable without live cases; corpus has none. §O5 |
| **Multiple economic events per source** | **ENG DECISION** | ✱ | **New.** From the PIPE/Ensysce finding. Two failure modes — loss and, worse, value contamination. Not a PIPE patch. §O6 |

## What did not change

Everything not marked ✱ carries its v0.3 recommendation forward. In particular ENG
**accepted as-is**: consideration semantics and aggregation rules, transaction-size and
transaction-value semantics, investor-level `investment_amount`, the multiples model, and
the transaction-party recommendations.
