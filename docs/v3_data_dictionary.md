# Transactions Data Dictionary — Target Model

**Product Contract:** Transactions V3 — `V3-PC-1.0` · **Reconciled:** 2026-08-24

*What the target Transactions data model is, surface by surface, and what changes from
Grata today. Why a decision was taken lives in the audit layer — see the last section.*

---

## How to read this

**Three layers, kept distinct.**

| Layer | What it is | Role here |
| --- | --- | --- |
| **Grata today** | What Grata has now | The comparison baseline for every Action |
| **Target model** | What Engineering should implement | **The subject of this document** |
| Reference implementation | `ma-collection-mvp` | Evidence the semantics are expressible. Its columns, stages and compatibility names are **not** target structure |

**Action** — always relative to *Grata today*, never to a recommendation or to the reference
implementation:

`KEEP` exists and stays materially as-is · `ADD` absent today · `CHANGE` the concept exists but
its semantics, shape, vocabulary or placement change · `REMOVE` exists today and is retired ·
`DERIVE` computed rather than stored as an authored fact · `DEFER` not part of the current target

`BASELINE TO CONFIRM` means the **target requirement is settled** but the historical Grata
delta could not be established from available artifacts. It never blocks the definition.

**Population** — `Collected` from a source · `Derived` computed · `Researcher` entered ·
`System` assigned · `Relationship` carried by a link between records.

**Two rules that run through everything.**

1. **Null is not a negative.** A missing value means the fact was not established. It is never
   "no", never zero, and never "not disclosed".
2. **Don't manufacture one fact from another.** A side is not evidence of a participant, a
   participant is not evidence of a side, a public target is not evidence of a tender offer,
   and a private-equity buyer is not evidence of a take-private.

---

## What changed, and why the model looks like this

Nine decisions shape the target model. Everything in the tables follows from them.

**1. Separate what happened from how it was structured.** `event_type` says what kind of
transaction occurred. Merger, reverse merger and de-SPAC left that vocabulary and became
`combination_structure`, which qualifies an acquisition rather than competing with it. A deal
can be an acquisition *and* be effected as a merger; the old model made you choose.

**2. Separate what happened from what has happened since.** A transaction accumulates
lifecycle events — rumored, announced, amended, closed. Those are repeating event-history
rows, not a single status field. Grata already models it this way and the target keeps it. This
document calls the classifying attribute **event history type** to keep it distinct from
`event_type`; Grata's existing child column is named `type`, and nothing here asks for it to be
renamed.

**3. Target typing is structural; asset classification is subordinate.** `target_type` says
whether a company, a subsidiary, a business unit or assets changed hands. What *kind* of asset
lives in `asset_type`, which applies only when the target is assets. Deal wording never
determines the type — an "asset purchase" of a whole company is still a company.

**4. Collapse overlapping flags into explicit dimensions — where they are genuinely one
dimension.** Two booleans that cannot both be true are one field with two values plus null;
`is_up_round`/`is_down_round` could not express *flat*, and both-false meant either flat or
unknown. Applied case by case, not as a rule: genuinely orthogonal characteristics stay
separate.

**5. Represent consideration as typed components.** Cash, stock, earnout, CVR and assumed debt
are components of one transaction, not a proliferation of flags. Anything derivable from the
components is derived from them rather than authored twice.

**6. Separate observed facts from derived conclusions.** A source fact is what a document
said. A derived value is what the model computed from source facts. Nothing derived is ever
asked of a source, and nothing observed is silently overwritten by a computation.

**7. Distinguish economic scopes that look alike.** Transaction value, equity value, enterprise
value, round size, valuation and transaction size answer different questions about different
denominators. Conflating them is the most consequential error available in this domain, so each
carries an explicit basis saying how it was arrived at.

**8. Parties are the model.** Represent the actual participants and their roles. No synthetic
consortium entity — a multi-buyer side is the firms that make it up, with their roles and the
lead where stated. Advisor participations name the specialty and the specific client. Named
people are preserved with their relationship to a firm, and more than one person must fit.

**9. Preserve uncertainty.** Null means not established. `false` is a claim and requires
evidence. Where a fact is genuinely absent, the model says less rather than asserting a
negative.

---

# The target model, surface by surface

## 1. Transaction Detail

> **Added** `combination_structure` · `target_type` · `asset_type` · `offer_mechanism` ·
> `deal_attitude` · `approach_type` · `is_going_private_outcome` · `stake_transition_type` ·
> `transaction_terms_disclosure_status` · deal-process terms
> **Changed** `event_type` vocabulary · `is_take_private` becomes derived · sponsor flags collapse to one dimension
> **Removed** `event_category` · `is_divestiture` · `is_de_spac` · four recap flags ·
> `is_mbo` / `is_mbi` · `is_stock_for_stock`
> **Derived** `is_take_private` · `has_earnout` · `has_cvr` · `consideration_type`

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `transaction_id` | KEEP | Exists | Canonical transaction identifier | ID | System | | |
| `event_type` | CHANGE | Exists | What transaction occurred | ENUM — ACQUISITION · JOINT_VENTURE · VC_ROUND · GROWTH_EQUITY · VENTURE_DEBT · SPIN_OFF · SPLIT_OFF · RECAPITALIZATION | Collected | | The field survives; the vocabulary changes. Merger, reverse merger, de-SPAC and minority investment leave it |
| **event history** | KEEP | **Child table** — event id, transaction, `type`, date, date precision | What has happened to this transaction over time | RELATIONSHIP — repeating rows | Collected | | A transaction may carry rumored, announced, amended and closed events. **Not a single status field**. Grata already models this correctly and the target keeps it |
| `event_category` | REMOVE | Exists | — | — | — | | Broad families derive from `event_type`. A transaction should not have to choose between *M&A* and *divestiture* when it is both |
| `combination_structure` | ADD | Missing | How an acquisition is legally effected | ENUM — MERGER · REVERSE_MERGER · DE_SPAC · null | Collected | Null when not established | Absorbs three retired event types and `is_de_spac`. Nested: de-SPAC is a reverse merger is a merger. Store the most specific; query broader questions by implication |
| `is_de_spac` | REMOVE | Exists | — | — | — | | Rolls up from `combination_structure` |
| `target_type` | ADD | No direct equivalent — Grata's party typing classifies entities, not the target's structural role | Structural nature of what is acquired | ENUM — standalone_company · subsidiary · business_unit · assets | Collected | | Deal wording never determines it |
| `asset_type` | ADD | Missing | What kind of asset is transacted | ENUM — REAL_ESTATE · INFRASTRUCTURE · ENERGY · NATURAL_RESOURCES · INTELLECTUAL_PROPERTY · DATA · FACILITY · EQUIPMENT · CONTRACTS_OR_RIGHTS · BRAND_OR_PRODUCT · OTHER | Collected | **Only when `target_type = assets`**; null otherwise | Standalone real-estate transactions are in scope |
| `target_status` | **BASELINE TO CONFIRM** | `Public`/`Private` values exist; **their referent is not established by available artifacts** | The target's public/private status **before** the transaction | ENUM — PUBLIC · PRIVATE · SUBSIDIARY_OF_PUBLIC · SUBSIDIARY_OF_PRIVATE · UNKNOWN | Collected | Pre-transaction only | **Target requirement settled; Grata delta to confirm.** There is no post-transaction counterpart — that is `is_going_private_outcome` |
| `offer_mechanism` | ADD | Missing | Whether the acquisition is effected by an offer made directly to target securityholders | ENUM — TENDER_OFFER · null | Collected | Null is the common case | A public target is not evidence of a tender offer, and neither is a merger agreement |
| `deal_attitude` | ADD | Missing | The target board's posture toward the transaction | ENUM — FRIENDLY · HOSTILE · null | Collected | **Absence of hostile evidence is not FRIENDLY** | FRIENDLY needs positive support or recommendation evidence |
| `approach_type` | ADD | Missing | How the approach arose | ENUM — SOLICITED · UNSOLICITED · null | Collected | Null when not established | Independent of attitude — unsolicited is neither hostile nor friendly, and neither value is inferred from the other's absence |
| `is_take_private` | CHANGE / DERIVE | **Exists as a stored flag** | The transaction takes a public company private | FLAG | Derived | | Was authored; now derived from three required conditions — see Derived Fields |
| `is_going_private_outcome` | ADD | Missing | The source establishes that the target's equity ceases to be publicly held or traded | `true` · null — **never false** | Collected | Null = not established | The affirmative evidence behind `is_take_private`. Never inferred from buyer type or deal structure |
| `stake_transition_type` | ADD | Missing | Explicit ownership transition, where the source states enough to establish it | ENUM — 8 values | Collected | Null = insufficient explicit evidence | Never inferred from a percentage alone |
| `is_merger_of_equals` | KEEP | Exists | Explicit merger-of-equals characterisation | FLAG | Collected | Explicit or qualified evidence only | Not inferred from structure or similar size |
| `is_secondary_buyout` | KEEP | Exists | A sponsor-backed company acquired from another sponsor | FLAG | Derived | | |
| `sponsor_transaction_role` | CHANGE | `is_platform_investment` and `is_add_on` both exist | How the transaction relates to a sponsor's platform | ENUM — PLATFORM · ADD_ON · null | Collected | Null is expected to be common | A sponsor investment is a platform or an add-on, not both. Never derived from buyer type. Orthogonal to take-private — an add-on can also take a company private |
| `is_divestiture` | REMOVE | Exists | — | — | — | | Derivable from target type plus the divesting parent |
| `is_mbo` · `is_mbi` | REMOVE | Both exist | — | — | — | | The underlying facts are **participants**: the actual management buyers and their roles. Two booleans cannot express a buy-in management buy-out except as both-true, which is indistinguishable from an error |
| `is_stock_for_stock` | REMOVE / DERIVE | Exists | Consideration is entirely acquirer stock | FLAG | Derived | | From typed consideration components. No duplicate authored flag |
| `is_lbo` | KEEP | Exists | Leveraged buyout characteristic | FLAG | Collected | | |
| `recap_type` | KEEP | Exists | Recapitalisation sub-type | ENUM — DIVIDEND · EQUITY · LEVERAGED · SPONSOR_RECAP | Collected | | |
| four `is_*_recap` flags | REMOVE | All four exist | — | — | — | | Each is `recap_type = <value>` |
| `financials_disclosure_status` | KEEP | Exists | Disclosure state for company financial metrics | ENUM — DISCLOSED · PARTIALLY_DISCLOSED · UNDISCLOSED · UNKNOWN | Collected | `DISCLOSED` = at least one value stated, **not** that everything is known | Vocabulary as recorded in the Grata baseline. The reference implementation carries a three-value subset without `PARTIALLY_DISCLOSED`; that reconciliation is **open** and is not settled here |
| `transaction_terms_disclosure_status` | ADD | Missing | Disclosure state for deal economics, consideration and value terms | ENUM — same vocabulary as `financials_disclosure_status` | Collected | | **The second disclosure axis — the concept is settled.** Deal terms and company financials are disclosed independently. Its final representation is unresolved: the shared vocabulary inherits the open `PARTIALLY_DISCLOSED` question, and the reference implementation does not yet carry the field |
| `competing_bid` | ADD | Not evidenced in Grata | A competing or topping bid is referenced | FLAG | Collected | | |
| `regulatory_approvals_required` | ADD | Not evidenced in Grata | Specific regulatory approvals are called out | FLAG | Collected | | |
| `has_go_shop` · `go_shop_period_days` | ADD | Not evidenced in Grata | Go-shop provision and its duration | FLAG · NUMBER | Collected | Duration null when not stated | |
| termination fees — target and acquirer, amount and percentage | ADD | Not evidenced in Grata | Termination fees by side | NUMBER | Collected | | |
| `announced_date` · `closed_date` | **BASELINE TO CONFIRM** | Not evidenced in available artifacts | Announcement and completion dates, with stated precision | DATE + precision ENUM | Collected | | **Target requirement settled; Grata delta to confirm.** Dates also appear as event-history rows; the transaction-level values are the headline announcement and completion |
| `linked_filings_count` | KEEP | Exists | Operational source linkage | NUMBER | System | | |
| `has_cbi_data` | KEEP | Exists | Source-system metadata | FLAG | System | | Operational, not business semantics |
| `platform_transaction_id` | **HOLD — baseline semantics to confirm** | Exists | — | — | — | | The field exists but available materials do not define its semantics. Not reinterpreted and not removed without a supported definition |

## 2. Parties & Participants

> **Added** side-qualified sponsor roles · `PARENT_ACQUIRER` · advisor specialty expansion ·
> advised-participant identity · advised side · named people
> **Changed** the two side-specific advisor roles collapse to one `ADVISOR` role · acquirer type
> is owned by the participant
> **Removed** `BOTH` as an advised-party value · any synthetic consortium construct

A multi-buyer side is the firms that make it up. There is no consortium entity: representing
one as a synthetic company damages the underlying company and participant structure.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| participation identity — party id, transaction, party name, canonical company, unresolved name, resolution status | KEEP | All exist | One party's participation in one transaction, and how far it has been resolved to a canonical company | ID · FK · DATA POINT · ENUM | System / Relationship | Unresolved name preserved when resolution has not succeeded | |
| `role` | CHANGE | Exists | The participant's role in the transaction | ENUM — TARGET · BUYER · SELLER · INVESTOR · PARENT_ACQUIRER · PARENT_SELLER · SPONSOR_BUYER · SPONSOR_SELLER · ADVISOR · LENDER · JV_PARTNER · UNDERWRITER | Collected | | `ADVISOR` replaces the two side-specific advisor roles — side comes from the advised party. **`PARENT_ACQUIRER` added**: its absence was an omission, not a decision. Sponsor side is explicit because it is meaningful role information |
| acquirer type | CHANGE | Party typing exists **on the party** | The acquirer's economic type | ENUM — strategic_corporate · private_equity · pe_portfolio · venture_capital · growth_equity · sovereign_wealth_fund · pension_fund · hedge_fund · family_office · individual · management · employee_group · spac · other_financial_sponsor · unknown | Collected | | **Semantically owned by the acquirer participant.** A transaction-level projection of the primary acquirer may be convenient; it is not the owner. `other_financial_sponsor` is the residual for an affirmatively established financial sponsor that fits no more specific type |
| `is_lead` · `is_primary` | KEEP | `is_lead` exists | Lead or primary participant on its side | FLAG | Collected | | Carries the lead buyer or lead investor where the source names one |
| investor participation — round participation %, investment amount and currency, USD amount, new-investor flag | KEEP | All exist | What one investor contributed and on what terms | DATA POINT · FLAG | Collected | | An investor's cheque is **never** the round size |
| `target_ownership_pct_after` | KEEP | Exists | This party's resulting ownership of the target | DATA POINT | Collected / Derived | | |
| `lender_role` | KEEP | Exists | Financing-provider role | ENUM | Collected | | **A lender is not an advisor specialty.** Providing capital and advising on a transaction are different participations; the same firm doing both appears twice, independently |
| **advisor specialty** | CHANGE | `advisor_specialty` exists — five values | The advisory service, at the most specific level the source establishes | ENUM — financial_advisory · legal · accounting · fairness_opinion · regulatory · tax · proxy_solicitation · information_agent · communications | Collected | Null when the source does not establish the service | Four specialties were previously collapsed into a catch-all. No generic catch-all is authored when the source names the service |
| **advised participant** | CHANGE | `advised_party` exists — a role, including `BOTH` | The specific transaction participant the advisor acted for | RELATIONSHIP | Relationship | Null when the source does not identify the client | **`BOTH` is not a value.** One advisor serving two participants is two relationships |
| **advised side** | ADD | Not separable — fused into the advised-party role | The side advised, when that is what the source establishes | ENUM — BUY_SIDE · SELL_SIDE · null | Collected / Derived | Null when not established | Independent of participant identity. Once a participant is resolved, side follows from that participant's role — but a side alone never identifies a participant, and a participant alone never establishes a side |
| **named people** | ADD | A single person name and title per party row | Explicitly named people and their relationship to a firm or participation, with title or role where stated | RELATIONSHIP — repeating | Collected / Relationship | | **Multiple people must be representable** — expressly not one scalar pair. Generalizes beyond advisors: named bankers, lawyers, accountants, communications advisers and **board representatives**. The gap in Grata today is cardinality, not absence |
| advisor and party provenance — party source, source URL | KEEP | Both exist | Where the party assertion came from | ENUM · URL | System | | |

## 3. Ownership & Stake

> **Added** `stake_transition_type` (see Transaction Detail)
> **Changed** nothing structural
> **Removed** nothing — `is_minority` was a recommendation, never a Grata field; it is not
> carried into the target. See the note below

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `pct_acquired` | KEEP | Exists | The stake acquired in **this** transaction | NUMBER | Collected | **Null is ambiguous by construction** — the source may have stated nothing, or the transaction may be a whole-company purchase | Not resulting ownership, and not prior ownership. Where a buyer acquires "the remaining 20%", the value is 20 |
| `stake_transition_type` | ADD | Missing | The explicit ownership transition, where the source establishes prior, current and resulting ownership | ENUM — NEW_MINORITY_STAKE · NEW_MAJORITY_STAKE · FULL_ACQUISITION · MINORITY_ACQUIRING_MAJORITY · MAJORITY_ACQUIRE_REMAINING · MINORITY_ACQUIRING_REMAINING · MAJORITY_INCREASING_STAKE · MINORITY_INCREASING_STAKE | Collected | Null = insufficient explicit evidence | Never inferred from `pct_acquired` |
| participant ownership after | KEEP | `target_ownership_pct_after` exists on the party | Resulting ownership by participant | DATA POINT | Collected / Derived | | Lives with the participant, where ownership actually attaches |
| *minority status* | **not a target field** | Missing | — | — | Derived on demand | | Fully answerable from `pct_acquired`, `stake_transition_type` and participant ownership. Minority is a **question about the ownership facts**, not an additional fact — persisting it would duplicate them and create a second thing to keep true |

## 4. Consideration

> **Added** typed consideration components as the authoritative representation
> **Changed** `consideration_type` becomes derived from components
> **Removed** `has_earnout` · `has_cvr` · `is_stock_for_stock` as authored flags

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| consideration component | ADD | Component structure anticipated but not present | One element of what the buyer gives: its form, amount, currency, per-share amount, exchange ratio, trigger and election mechanics | RELATIONSHIP — repeating | Collected | | One transaction may carry cash **and** stock **and** a contingent element. A single scalar cannot say that |
| component form | ADD | Missing | What kind of consideration this component is | ENUM — CASH · ACQUIRER_STOCK · TARGET_STOCK · EARNOUT · CVR · CONTINGENT_CONSIDERATION · DEBT_ASSUMED · RETAINED_EQUITY · OTHER | Collected | | Earnout and CVR are distinct instruments, not one "contingent" bucket |
| `consideration_type` | KEEP / DERIVE | Exists | Rolled-up consideration structure | ENUM — CASH · STOCK · CASH_AND_STOCK · OTHER | Derived | Null when no component is established | Derived from the components rather than authored alongside them |
| `has_earnout` · `has_cvr` | REMOVE / DERIVE | Both exist | Presence of a contingent component of that form | FLAG | Derived | | The component carries the fact; the flag restated it. Same reasoning as `is_stock_for_stock` |

## 5. Transaction Value & Financials

> **Added** `transaction_size` and its basis · `implied_equity_value` · `implied_enterprise_value`
> and their bases · balance-sheet inputs
> **Changed** enterprise value stops competing as an independently authored canonical output ·
> equity value is explicitly stake-level
> **Removed** nothing
> **Derived** every value marked below

Grata records financial values as **normalized metric rows** — one row per metric, carrying its
own period, precision, currency, FX rate and calculated indicator. **The target keeps that
representation.** Flat value columns in the reference implementation are extraction mechanics.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| metric row — metric type, period type, period end, period precision, value, currency, FX rate and date, calculated indicator | KEEP | Exists | One financial fact with everything needed to interpret it | RELATIONSHIP — repeating | Collected / Derived / Researcher | | The shape that makes period and currency unambiguous per value |
| `transaction_value` | KEEP | `TRANSACTION_VALUE` metric | The transaction-level value as reported or derived | METRIC | Collected / Derived | | |
| `equity_value` | KEEP | `EQUITY_VALUE` metric | Equity consideration **for the stake actually acquired** | METRIC | Collected / Derived | | **Stake-level.** Never a multiple numerator |
| `implied_equity_value` | ADD | Missing | Equity grossed up to a 100% basis | METRIC | Derived | Null when the acquired percentage is unknown | |
| `implied_enterprise_value` | ADD | Missing | Enterprise value on a 100% basis | METRIC | Derived | Null when no path is satisfiable | The multiple numerator |
| `enterprise_value` | CHANGE | `ENTERPRISE_VALUE` metric, source observation | A source-stated whole-company enterprise value | METRIC | Collected | | Retained as an observation. It no longer competes as a separate canonical output — the canonical 100%-basis figure is `implied_enterprise_value` |
| `transaction_size` | ADD | Missing | One comparable magnitude for the event, whatever its type | METRIC | Derived | Null when no rung applies | Lets an acquisition and a funding round sit in one column honestly |
| `transaction_size_basis` | ADD | Missing | Which source field supplied the magnitude | ENUM — TRANSACTION_VALUE · ROUND_SIZE · SPIN_SPLIT_CONSIDERATION_VALUE | Derived | Required wherever `transaction_size` is populated | Names the **source field**, which is what keeps the enum one-dimensional |
| value bases — for transaction value, equity value and enterprise value | ADD | Missing | How each derived value was arrived at | ENUM per value | Derived | | A value without its basis is not interpretable |
| `net_debt` | KEEP | `NET_DEBT` metric | Debt net of cash, at a point in time | METRIC | Collected / Derived / Researcher | | Computed only from components that agree on currency and as-of date |
| `total_debt` · `cash_and_equivalents` | KEEP | Both are metric types | Gross debt and cash at a stated balance-sheet date | METRIC | Collected / Researcher | | Total debt is **not** net of cash |
| balance-sheet as-of date | ADD | Period end exists on the metric row | The date a balance-sheet figure is stated as of | DATE | Collected | | A balance sheet covers no period, so it is never labelled LTM, NTM, annual or quarterly |
| target financials — revenue, EBITDA and their periods | KEEP | `REVENUE`, `ADJ_EBITDA` and period fields on the metric row | Stated target financials with their period basis | METRIC | Collected | Period null when the source states no basis — **never assumed** | Adjusted EBITDA is captured as EBITDA. ARR is **not** revenue and is a separate metric |
| `ARR` · `MRR` | KEEP | Both are metric types | Recurring-revenue metrics | METRIC | Collected | | Already available in Grata's metric vocabulary. Recording ARR as revenue would produce a false revenue multiple |
| currency per value | KEEP | `value_currency` on the metric row | The currency of the value it belongs to | ISO 4217 | Collected | | Deal currency, valuation currency and round currency are distinguished by **which metric row** carries each. No conversion without an explicit rate and date |

## 6. Multiples

> **Added** nothing structural
> **Changed** the numerator is an explicit canonical family; quality states are retained
> **Removed** nothing
> **Derived** the multiple value, where it is calculated rather than reported

Grata records multiples as **normalized rows** linked to their denominator metric. The target
keeps that.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| multiple row — id, transaction, period end, period precision | KEEP | All exist | One multiple for one transaction on one denominator period | ID · FK · DATE · ENUM | System / Derived | | |
| `multiple_type` | KEEP | Exists | Which multiple this is | ENUM — EV_REVENUE · EV_EBITDA · EV_EBIT · EV_FCF · PE · PB · PTBV | Derived / Collected | | |
| `multiple_value` | KEEP | Exists | The multiple | DATA POINT | Collected / Derived | | Reported or calculated — `source_flag` says which |
| `period_basis` | KEEP | Exists | The denominator's period basis | ENUM — LTM · NTM · ANNUAL · QUARTERLY | Derived | | A multiple is uninterpretable without it |
| `numerator_value_type` | CHANGE | Exists | Which canonical value is the numerator | ENUM — implied_enterprise_value · implied_equity_value | Derived | | Made explicit so a stake-level figure can never silently become a numerator |
| `denominator_financial_id` | KEEP | Exists | The metric row used as the denominator | FK | Relationship | Expected on calculated rows | Makes a calculated multiple reproducible |
| `source_flag` | KEEP | Exists | Reported by a source, or calculated | ENUM — as_reported · calculated | System | | |
| `quality` | KEEP | Exists | Whether the multiple is meaningful | ENUM — CALCULATED · NM · NOT_CALCULABLE | Derived | | `NOT_CALCULABLE` is not a single cause — see Derived Fields |

## 7. Funding Detail

> **Added** canonical `round` · `use_of_proceeds`
> **Changed** the normalized stage grouping is re-derived from the canonical round ·
> up/down/flat becomes one dimension · board representation moves to Parties
> **Removed** `is_up_round` · `is_down_round`
> **Derived** `round` · normalized stage

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `round_label` | KEEP | Exists | The round as the source names it, verbatim | DATA POINT | Collected | | Preserve the source's wording — "Series A Extension", "Bridge Round" |
| `round` | ADD | Missing | Canonical, filterable round classification | ENUM — PRE_SEED · SEED · ANGEL · SERIES_A … | Derived / Researcher | Null when the label does not normalize | The canonical layer between the label and the broad stage |
| normalized stage | CHANGE | `round_stage_category` exists | Broad stage grouping | ENUM — PRE_SEED · SEED · EARLY_STAGE · GROWTH · LATE_STAGE | Derived | | Same five values, now derived from the canonical round rather than matched against the label text |
| `round_size` | KEEP | `ROUND_SIZE` metric | Total amount raised in **this** round | METRIC | Collected | | Never an individual investor's cheque |
| `pre_money_valuation` · `post_money_valuation` | KEEP | Both are metric types | Equity valuation before and after the financing, 100% basis | METRIC | Collected | Funding events only | **A valuation is not the amount raised** |
| `facility_size` | KEEP | Exists | A financing or debt facility alongside the round | DATA POINT | Collected | | A separate instrument. Never folded into the round size and never summed with it |
| `total_raised_to_date` | KEEP | Exists | Cumulative capital raised across all rounds | DATA POINT | Collected | | **Cumulative.** Never this round's size |
| `round_price_direction` | CHANGE | `is_up_round` **and** `is_down_round` both exist | Whether the round priced up, down or flat | ENUM — UP · DOWN · FLAT · null | Collected | Null when not established | Replaces the pair. Both-false could not distinguish *flat* from *unknown* |
| `round_sequence_number` · `prior_round_id` | KEEP | Both exist | Where this round sits in the sequence, and its predecessor | DATA POINT · FK | Collected / System | | |
| `is_extension_round` | KEEP | Exists | The round extends a prior round | FLAG | Collected | | |
| `is_bridge_round` | **BASELINE TO CONFIRM** | Not evidenced in available artifacts | The round is a bridge financing | FLAG | Collected | | **Target requirement settled; Grata delta to confirm** |
| `is_unicorn_round` | KEEP | Exists | The round establishes or confirms unicorn status | FLAG | **Collected** | | **Collected, not derived.** Sources assert unicorn status without stating a post-money valuation, so deriving it would discard the rows where the claim is the only evidence |
| `cvc_participation` | KEEP | Exists | A corporate venture arm participated | FLAG | Collected | | |
| `is_oversubscribed` | KEEP | Exists | The round was oversubscribed | FLAG | Collected | | |
| `use_of_proceeds` | ADD | Not evidenced in available artifacts | Source-stated intended use of the capital raised | DATA POINT | Collected | Null = not stated | In scope. The reference implementation's funding path does not yet author it — a collection-coverage gap, not a reason to drop the field |
| round currency · valuation currency | **BASELINE TO CONFIRM** | `value_currency` exists on the metric row | The currency of the value it belongs to | ISO 4217 | Collected | | **Target requirement settled; Grata delta to confirm.** In the metric-row model each value carries its own currency |
| **board representation** | **→ Parties & Participants** | — | The investor and the named representative | RELATIONSHIP | Relationship | | Not a funding flag plus free text. It is a participant fact and follows the named-people principle |

## 8. Security & Share Mechanics

> **Added** everything except per-share price
> **Changed** nothing · **Removed** nothing

**The surface is a settled target concept; its placement is not.** Scope is limited to what the
target model actually needs: per-share consideration, stock-for-stock exchange, and the
share-count path that lets a per-share offer be valued. Not every earlier recommendation is
carried forward.

Whether these facts live in their own security structure or directly on the consideration
component is **unresolved** and is an Engineering representation choice. The Product
requirement is only that there is **one** source of truth for them — the consideration model
must reference these facts, not keep a second copy.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `per_share_price` | KEEP | **Exists** | Consideration per target security | DATA POINT | Collected | Security transactions | The one existing field on this surface |
| `target_security_type` | ADD | Missing | The target security or interest being acquired | ENUM — vocabulary to be reconciled against agreement extraction before it is frozen | Collected / Researcher | Security and equity transactions | Multiple classes must be representable without duplicating the transaction |
| `exchange_ratio` | ADD | **Missing a general M&A home** | Acquirer securities received per target security | DATA POINT | Collected | Stock consideration | **A settled Product concept.** Its target home and wiring are **unresolved** — the baseline records no general M&A home, and today the value survives only as free text |
| `target_shares_outstanding` | ADD | Missing | The relevant target security denominator | DATA POINT | Collected / Researcher | Sparse; mainly public deals | Input to valuing a per-share offer |
| `target_shares_acquired` | ADD | Missing | Target securities acquired in this transaction | DATA POINT | Collected / Derived | Security transactions | |
| `acquirer_shares_issued` | ADD | Missing | Acquirer securities issued as consideration | DATA POINT | Collected / Derived | Stock consideration | |
| `acquirer_security_price` · `acquirer_security_price_date` | ADD | Missing | The price used to value acquirer securities, and its date | DATA POINT · DATE | Collected / Researcher | Stock consideration | **A calculated stock component requires an explicit, accepted price date.** Never silently assumed |

## 9. Spin / Split Detail

> **Added** distribution dates, percentages and ratios
> **Changed** the distribution mechanism is named for its scope
> **Removed** nothing

Transaction-level fields, not a child structure — assessed field by field, none is multi-valued
per transaction except per-security-class share counts, which belong to Security & Share
Mechanics.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spin_split_type` | KEEP | Exists | Whether the parent retains a residual stake or distributes fully | ENUM — SPIN_OFF · SPLIT_OFF | Collected | Spin/split events only | |
| `spin_split_distribution_mechanism` | CHANGE | `distribution_mechanism` exists | How shares reach shareholders | ENUM — PRO_RATA · EXCHANGE_OFFER | Collected | Spin/split events only | Renamed for scope — the field is spin/split-specific, and the unqualified name reads as general |
| `spin_split_record_date` · `spin_split_distribution_date` | ADD | Missing | Record and distribution dates | DATE | Collected | Spin/split events only | |
| `spin_split_pct_distributed` | ADD | Missing | Percentage of the entity distributed | DATA POINT | Collected | Spin/split events only | |
| `spin_split_distribution_ratio` | ADD | Missing | Shares received per parent share held | DATA POINT | Collected | Spin/split events only | |
| `split_off_pct_parent_shares_exchanged` | ADD | Missing | Percentage of parent shares exchanged | DATA POINT | Collected | Split-off only | |
| spin/split consideration value | ADD | Missing | Value associated with the distribution | METRIC | Collected / Derived | Spin/split events only | A metric row like any other value. **`transaction_size` reserves a basis for it**, so the magnitude has a home once the value is collected |

## 10. Summary & Rationale

> **Added** nothing structural
> **Changed** the summary is explicitly non-authoritative
> **Removed** nothing

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| transaction summary | KEEP | Narrative artifact exists | A readable narrative describing the transaction | DATA POINT | Derived | | **Not authoritative.** Generated from canonical fields; never a source of truth, and never the place a fact first appears |
| `primary_rationale` | KEEP | Exists | The principal strategic rationale | ENUM | Collected | | |
| `secondary_rationales` | KEEP | Exists | Further rationales | RELATIONSHIP — repeating | Collected | | |
| rationale basis | ADD | Missing | What each rationale is grounded in, per rationale | ENUM | Collected | | Carried on the primary **and every** secondary — a single scalar cannot attribute a list |
| rationale evidence | **TABLED** | Missing | Reference to the source text supporting each rationale | RELATIONSHIP | Collected | | Under consideration; no design approved |
| rationale owner | **OPEN** | — | Whose rationale the value represents — acquirer's, seller's or the transaction's | — | — | | Unanswered. Do not assume |

## 11. Provenance & Operational

> Product-relevant provenance only. Pipeline mechanics are not target model.

| Field / Concept | Action | Current Grata | Definition | Type / Values | Population | Applicability / Null | Replaces / Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| source record | KEEP | Source linkage exists | The document or article a fact came from | RELATIONSHIP | System | | **A source is not a transaction.** One source may yield several transactions; one transaction may draw on several sources |
| derived-source provenance | **OPEN** | Missing | A source item decomposed from a container, retaining a link to the original | RELATIONSHIP | System | | A digest may be decomposed into independently processable items; derived items carry **lower authority**. The exact tier is unresolved |
| reconciliation / supersession key | ADD | Content hash and a current-version flag exist | How a later observation supersedes an earlier one | — | System | | The key is unlikely to be single-valued: a filed document is immutable once filed, a web source can change under the same URL |
| `record_review_status` | **HOLD** | Enum exists; physical field to verify | Transaction review or triage state | ENUM | Researcher | | |
| researcher amendment | **OPEN** | — | What a researcher may amend, and what re-derivation may overwrite | — | Researcher | | Undecided, which is why no field in this document states an amendability rule |

---

# Derived fields

Everything below is computed from other canonical facts. None is ever asked of a source, and
none may be authored alongside the facts it derives from. Where a derivation cannot be
satisfied, the result is null or zero as stated — never a guess.

## `is_take_private`

**The transaction takes a public company private.** Three conditions, all required.

| # | Condition |
| --- | --- |
| 1 | The event is an acquisition, the target was **public** before the transaction, and the target is a **standalone company** |
| 2 | The buyer satisfies the private-ownership condition: private equity · a PE portfolio company · management · an employee group · the financial-sponsor residual |
| 3 | The source **affirmatively establishes** that the target ceases to have publicly held or traded equity |

Any one failing yields 0.

**Never inferred from:** a private-equity buyer alone · a public target alone · a merger or
tender-offer structure · the percentage acquired · an unstated percentage treated as 100%.

**The acquirer's own listing status is irrelevant.** A listed sponsor taking a company private
is a genuine take-private.

A private **strategic** buyer does not qualify: the target stops being independent, which is not
the same as ceasing to be publicly traded. A multi-buyer transaction is evaluated from its
actual participating buyers, not from a collective label.

**Null / zero:** absence of affirmative outcome evidence is 0, by decision. Orthogonal to the
sponsor platform role — an add-on can also be a take-private.

## `transaction_size` and its basis

**One comparable magnitude for the event, whatever kind of event it is**, with a basis naming
the source field it came from. Naming the source field rather than a computation is what keeps
the vocabulary one-dimensional.

| Rung | Basis | Applies to |
| --- | --- | --- |
| 1 | `TRANSACTION_VALUE` | M&A events |
| 2 | `ROUND_SIZE` | funding events |
| 3 | `SPIN_SPLIT_CONSIDERATION_VALUE` | spin/split events, once that value is collected |

**Deliberately excluded.** Equity value: the cases where a stake-level figure could safely stand
in already produce a transaction value; the rest have unknown scope. Enterprise value: below
control it is a grossed-up whole-company figure, so a 27%-for-$600M deal would report as
$2.22bn. An individual investor's cheque: it is not the event's magnitude, and reporting a $50M
cheque as a $100M round is wrong however many investors disclosed. When a round total is
undisclosed the honest magnitude is null.

## Equity, implied equity and enterprise value

Three questions with three denominators. Each derived value carries a basis.

| Value | Question | Basis says |
| --- | --- | --- |
| `equity_value` | What was paid for the equity **actually acquired**? | whether debt was known, and whether the figure is below control |
| `implied_equity_value` | What would 100% of the equity be worth at that price? | how the gross-up was performed |
| `implied_enterprise_value` | What is the whole company worth including debt? | which rung produced it |

**`implied_enterprise_value`, in order:**

1. A source states a whole-company enterprise value → `STATED`
2. Implied equity value **+ reported** net debt
3. Implied equity value **+ calculated** net debt

Rungs 2 and 3 add a consideration figure to a balance-sheet figure. **Both currencies must be
known and equal, or the sum is refused** — an unknown currency is insufficient evidence, not
licence to assume agreement, and no conversion is attempted without an explicit rate and date. A
stated enterprise value is a single figure, so the guard does not apply to it.

**Equity value never becomes a multiple numerator.** Transaction value and stake-level equity
describe what changed hands; a multiple needs a whole-company numerator.

**Null:** no rung satisfied, the currency guard refuses, or there is no equity figure to build on.

## `net_debt`

Debt net of cash at a point in time. Computed from total debt minus cash and equivalents **only
when both share one currency and one balance-sheet date**. A source-stated net figure is
recorded as stated; a stated net figure is never used as though it were gross debt. Neither
component is ever assumed to be zero — unknown is null, not nil.

## Multiples

**Require a whole-company enterprise value.** With none, no multiple is calculable regardless of
how complete the financials are.

The denominator must have a usable period basis — trailing or forward. A recent annual actual may
fill the trailing slot when it is date-aligned to the announcement, without relabelling the
underlying period. Cross-currency numerator and denominator are flagged not-meaningful rather than
divided.

**`NOT_CALCULABLE` has three distinct causes** and does not distinguish them: no enterprise-value
basis; the financial primitive is missing; the period basis is unusable. Reading the quality
value alone cannot tell you which — that needs the numerator, the metric and the period together.

## Canonical round and normalized stage

Two derivations over one collected fact.

`round_label` is the source's verbatim wording. **`round`** normalizes it to a canonical,
filterable classification. **Normalized stage** groups the canonical round into a broad bucket —
derived from `round`, never matched against the label text, so "Series AA" and "Series A
extension" resolve on the round rather than on a substring.

Null at either step when the label does not normalize. A null canonical round does not mean the
round is unknown — the label is still there.

## `consideration_type`, `has_earnout`, `has_cvr`, stock-for-stock

All four derive from the typed consideration components. `consideration_type` rolls the component
forms up: all cash is cash, all stock is stock, both is cash-and-stock, anything else is other.
The three flags are the presence of a component of the relevant form.

None is authored. A flag maintained alongside the components it summarizes is a second thing to
keep true, and the first thing to fall out of step.

**Sequencing.** The derivation is only sound once typed consideration components are actually
populated. Until then the stored flags carry evidence no derivation can reach, so retiring them
is **conditional on the components being in place** — not a change that can be made first.

## `is_secondary_buyout`

A sponsor-backed company acquired from another sponsor. Explicit source evidence, or sponsor
participants on **both** the buy and sell sides. A private-equity buyer alone does not establish
it — that is a buyer, not a sponsor-to-sponsor transaction. Orthogonal to the sponsor platform
role: a secondary buyout may also be a platform investment.

---

# Open and tabled

Short by design. These must not be read as settled.

**Open — raised, no Product position yet.** Whether a rumour-only source should be admitted at
intake, and how that relates to the rumored lifecycle event · the treatment of an Indian QIP,
which is **not** settled by the PIPE decision · the authority tier for source items decomposed
from a digest · entity and domain linking · source and filing tiering · what a researcher may
amend and what re-derivation may overwrite.

**Open — concept settled, representation unresolved.** These are **not** absent Product work.
Each names a target concept the record settles, whose final shape or wiring is still open, so a
reader does not mistake an unresolved representation for a concept Product never addressed.

- **Security & Share Mechanics placement.** The surface is settled (§8). Whether its facts live
  in a dedicated security structure or directly on the consideration component is unresolved.
  The Product requirement is one source of truth, not a particular placement.
- **`exchange_ratio` home and wiring.** A settled concept with no general M&A home in the
  baseline and no target home chosen.
- **`target_security_type` vocabulary.** The field is settled; its value set is to be reconciled
  against agreement extraction before it is frozen.
- **Second transaction-terms disclosure axis.** `transaction_terms_disclosure_status` is a
  settled concept with prior grounding. Its final vocabulary is unresolved — the baseline
  records `PARTIALLY_DISCLOSED` on both disclosure fields and the reference implementation does
  not carry it.
- **Agreement `merger_structure` reconciliation.** Agreement extraction carries a single-valued
  `merger_structure`. The target splits that information across `combination_structure` and
  `offer_mechanism`. How the agreement field reconciles against the two target fields is
  unresolved. `merger_structure` is **not** a target field.

**Tabled — a position exists, deliberately parked.** Strategic Rationale representation, including
the unanswered question of whose rationale a value represents · retaining source excerpts as
rationale evidence · canonical casing and read-tolerance cleanup.

**Settled elsewhere, recorded here so it is not re-litigated.** Standalone real-estate transactions
are **in scope**. A PIPE is recognized and tagged, and profiling then stops — no further
extraction. A conservative literal recognizer in the reference implementation is an implementation
safety choice, because over-recognition would suppress an otherwise processable transaction; it is
not the Product definition of a PIPE.

Detail for every item — evidence, history and identifiers — is in
`docs/v3_change_decision_register.md`.


---

# Appendix — retired concepts and reference-implementation residue

**These are not fields.** Nothing here is part of the target model. They are listed so that a
column found in an existing system is not mistaken for a current Product concept.

## Retired concepts

Each exists in Grata today and is **not** carried into the target model. The main tables mark
them `REMOVE`; this is the consolidated list.

| Retired | Replaced by | Why |
| --- | --- | --- |
| `event_category` | derived from `event_type` | A transaction should not have to choose between *M&A* and *divestiture* when it is both |
| `is_divestiture` | `target_type` + the divesting parent | Derivable from facts already collected |
| `is_de_spac` | `combination_structure = DE_SPAC` | The merger family became one typed dimension |
| four `is_*_recap` flags | `recap_type` | Each was that field's value restated as a boolean |
| `is_up_round` · `is_down_round` | `round_price_direction` | The pair could not express *flat*, and both-false meant flat **or** unknown |
| `is_mbo` · `is_mbi` | management participants and their roles | Two booleans cannot express a buy-in management buy-out except as both-true, which is indistinguishable from an error. The fact belongs to the participants |
| `is_stock_for_stock` | typed consideration components | The components carry the fact |
| `has_earnout` · `has_cvr` | typed consideration components | Same |
| `BOTH` as an advised-party value | two advisor relationships | One row cannot say which two participants an advisor served |
| the two side-specific advisor roles | `ADVISOR` + the advised participant | Side follows from the advised party's role |

## Reference-implementation residue

Values and structures the reference implementation still accepts that are **not** part of the
target model. Listed so nobody infers them from prototype code.

| Residue | Target position |
| --- | --- |
| a consortium value on acquirer type | **Not a target acquirer type.** A consortium is not a company and not an acquirer; the buy side is the actual participating firms with their roles and the lead where stated |
| a synthetic consortium participant group | Not target model — materializing one damages the underlying company and participant representation |
| a generic catch-all advisor specialty | Not authored when the source establishes one of the supported specialties. Historical values remain readable: they prove a specialty outside the two named ones was observed, even though the old contract discarded which |
| a single lifecycle status field | The target keeps repeating event-history rows |
| flat financial and valuation columns | The target keeps normalized metric rows |
| a flat board-representation flag and note | Board representation is a participant relationship with the named representative |

---

# Where to look for why

| Question | Document |
| --- | --- |
| What is in this release, and what validated it? | `docs/v3_release_manifest.md` |
| Why was a decision taken, and what is its status? | `docs/v3_change_decision_register.md` |
| What must Engineering resolve or align to? | `docs/handoff_grata_transactions_eng.md` |
| What was the reasoning at the time? | `docs/decisions.md` and the V2 reconciliation documents |

Those answer **why**. This document answers **what the model is**.
