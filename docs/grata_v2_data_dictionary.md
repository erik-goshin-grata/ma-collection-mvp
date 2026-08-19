# Grata V2 Transaction Data Dictionary — Revised Draft v0.4.1

**Status:** Engineering review incorporated  
**Basis:** Current Grata schemas/enums plus accepted/tested transaction-model decisions through 2026-08-13.  
**Important:** Physical storage/table placement remains an ENG decision unless cardinality requires a repeating child/relationship.

> ## ⚠️ **Transactions V3 is the canonical target. `docs/grata_v2_inventory_and_recommendations.md` §T is authoritative.**
>
> This dictionary carries the earlier Grata baseline plus the v0.3/v0.4/v0.4.1
> recommendations. **Where any entry here disagrees with inventory §T, §T wins.** Entries
> superseded by V3 decisions are marked in place rather than rewritten, so the reasoning
> stays auditable. V2/harness behaviour and the earlier Grata model are **inputs** to V3,
> not authorities over it.
>
> Rows superseded on 2026-08-19: `event_category` (§T1) · `combination_structure` sibling
> values (§T2) · `target_type` (§T3) · `is_divestiture` (§T4) · party/advisor roles
> (§T5–T6) · `sponsor_investment_role` → `sponsor_transaction_role` (§T7) · `acquirer_type`
> (§T8) · `deal_attitude` + new `approach_type` (§T11) · `offer_mechanism` (§T12) · new
> `asset_type` (§T13) · `round_label` / new `round` / `round_stage_category` → `vc_stage`
> (§T14).

> **v0.4, 2026-08-18 — Engineering review incorporated.** The authoritative record of the
> review is `docs/grata_v2_inventory_and_recommendations.md` **§P**. This dictionary is
> updated only where v0.3 entries would otherwise **contradict** it: the flag entries that
> become typed dimensions or derivations (§2), the metric-row policy (§9), Spin/Split
> placement (§11), and advisor specialty (§12/§13). Entries not touched below stand.

> **v0.4.1, 2026-08-18 — MergerLinks vocabulary reconciled.** Full mapping in
> `grata_v2_inventory_and_recommendations.md` **§Q**. The ML vocabulary was supplied as
> **labels only** — no ML source model, definitions or examples were available — so
> 10 of 40 labels are `UNRESOLVED` and several proposed fields below are **conditional on
> a §Q7 answer**, marked as such. A conditional entry is a hypothesis awaiting a
> definition, not a specification to build from.

> **Redlined 2026-08-17 — see `docs/grata_v2_reconciliation_2026_08_17.md`.**
> Inline redlines below touch §7 (canonical EV rule; missing `equity_value_basis`), §9
> (`POINT_IN_TIME` QA contract; FX semantics) and §10 (`value_usd_basis`). The rest of
> v0.3 stands. Caveat carried from that document: everything involving `total_debt`,
> `cash_and_equivalents` and the calculated EV bases is **fixture-validated only** — zero
> live rows.

## 1. Dictionary conventions

**Data shapes**
- `ID` / `FK`
- `ENUM`
- `FLAG`
- `DATA POINT`
- `DERIVED DATA POINT`
- `RELATIONSHIP`
- `REPEATING STRUCTURE`
- `DATE`

**Population modes**
- source-stated / extracted
- researcher-entered
- calculated / derived
- system / entity resolution

`NULL` means no canonical value is currently available. Applicability follows transaction structure; V1 does not require field-level null reasons.

**Requiredness** is documented as `REQUIRED`, `CONDITIONAL`, or `OPTIONAL` business/QA metadata; this does not automatically imply SQL `NOT NULL`.

---

# 2. Transaction record / event concepts

| Field / Concept | Definition | Shape | Population | Allowed Values / Notes |
|---|---|---|---|---|
| `transaction_id` | Canonical transaction identifier. | ID | System | Required. |
| `event_type` | Broad transaction event family/type. | ENUM | Source / researcher | Current enum; proposed cleanup removes minority core usage and may move merger to a flag. |
| ~~`event_category`~~ | **REMOVED from V3 — see inventory §T1.** Broad Product/FE families are derived from `event_type`; not an authored field. `divestiture` and `exit_liquidity` are not carried forward. *(v0.4 text retained below.)* High-level product/event family derived from event type. | ENUM | Derived | Recommended: `ma`, `spin_split`, `investment_funding`, `recapitalization`, `exit_liquidity`. `divestiture` is a feature (`is_divestiture`), not a competing category. |
| `asset_type` | **What kind of asset is being transacted.** Subordinate to `target_type = ASSETS`; not applicable otherwise. | ENUM | Source / researcher | **V3 (§T13) ADD.** `REAL_ESTATE` · `INFRASTRUCTURE` · `ENERGY` · `NATURAL_RESOURCES` · `INTELLECTUAL_PROPERTY` · `DATA` · `FACILITY` · `EQUIPMENT` · `CONTRACTS_OR_RIGHTS` · `BRAND_OR_PRODUCT` · `OTHER` · null. **Not** a replacement for `target_type`, sector or industry — `INFRASTRUCTURE`/`ENERGY` describe the **transacted asset**, while the company's sector is separate. **`FACILITY` is distinct from `REAL_ESTATE`**: an operating plant/mill/yard differs from property acquired principally as real estate. **Single-valued** to start; a portfolio of one class is one value. **Settled but extensible.** **No V2 migration source** — newly collected. |
| `target_type` | Structural type of target/object acquired. | ENUM | Source / researcher | **V3 (§T3): `STANDALONE_COMPANY` · `SUBSIDIARY` · `BUSINESS_UNIT` · `ASSETS` · unknown-null.** `SPINCO` is **not** a V3 value — it is implied by `event_type = SPIN_OFF / SPLIT_OFF`, and no replacement flag is created. One canonical column, not legacy + V2. Asset sub-classification when `target_type = ASSETS` is **not decided** (§T10). |
| `acquirer_type` | **Economic/entity type of the acquirer as established from the transaction source/context.** | ENUM | **Extracted at collection time** | **V3 (§T8) — retained as extracted; the v0.4 "prefer canonical entity profile" recommendation is superseded.** Vocabulary: `CORPORATION` · `PRIVATE_EQUITY` · `VENTURE_CAPITAL` · `GROWTH_EQUITY` · `HEDGE_FUND` · `FAMILY_OFFICE` · `PENSION_FUND` · `SOVEREIGN_WEALTH_FUND` · `OTHER_FINANCIAL_SPONSOR` · `INDIVIDUAL` · `SPAC` · `SEARCH_FUND` · `GOVERNMENT` · `UNKNOWN`. **Removed:** `PE_PORTFOLIO`, `MANAGEMENT`, `EMPLOYEE_GROUP`, `CONSORTIUM`. Definitions carry **no transaction-behaviour qualifiers** — `CORPORATION` is not "acquiring for strategic reasons"; `PRIVATE_EQUITY` is not "making a direct fund investment". Names not frozen until reconciled with Grata's Company vocabulary. |
| `combination_structure` | Structure of the combination. **Hierarchical**, stored at the most specific supported value. | **HIERARCHICAL TYPED DIMENSION** | Source / researcher | **v0.4.** `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`, **V3 (§T2): `SHARE_PURCHASE` and `ASSET_PURCHASE` are REMOVED; only `NULL` remains as a non-chain value.** *(v0.4 text:)* with `SHARE_PURCHASE` / `ASSET_PURCHASE` / `NULL` as non-chain siblings. **Not three mutually exclusive peers** — the implication set is what preserves the nested facts the flags carried. Store the most specific value; **query broader questions by implication, never by equality** (`is_merger` is `IN (MERGER, REVERSE_MERGER, DE_SPAC)`, not `= MERGER`). Ambiguity resolves **upward**: a reverse merger with no established SPAC shell stays `REVERSE_MERGER`. The implication set belongs in the dictionary, not in per-consumer application logic. Inventory §A6.1. |
| ~~`is_merger`~~ | | | | **v0.4 REMOVE-DERIVABLE** — `combination_structure IN (MERGER, REVERSE_MERGER, DE_SPAC)`. |
| ~~`is_reverse_merger`~~ | | | | **v0.4 REMOVE-DERIVABLE** — `combination_structure IN (REVERSE_MERGER, DE_SPAC)`. |
| ~~`is_de_spac`~~ | | | | **v0.4 REMOVE-DERIVABLE** — `combination_structure = DE_SPAC`. |
| `is_merger_of_equals` | Merger-of-equals characteristic. | FLAG | Source / researcher | Implemented in current harness from explicit/qualified source evidence only. |
| `is_take_private` | Public target is taken private. | FLAG | Source / derived / researcher | Existing. |
| `is_lbo` | Leveraged-buyout characteristic. | FLAG | Source / derived / researcher | Existing. |
| `management_participation` | Management's role in the buyout. | **TYPED DIMENSION** | Source / researcher | **v0.4.** `MBO` / `MBI` / `BIMBO` / `NULL`. Replaces `is_mbo` + `is_mbi`, which could express a buy-in management buy-out only as both-true. |
| `sponsor_transaction_role` *(v0.4 name: `sponsor_investment_role`)* | The transaction's role in the sponsor's thesis. Separate from `PartyRole` and `acquirer_type`. | **TYPED DIMENSION** | Source / researcher | **V3 — see inventory §T7 for the full evidence semantics.** `PLATFORM` / `ADD_ON` / `NULL`, mutually exclusive. **`ADD_ON` does not require literal add-on/bolt-on/tuck-in wording** and **must never be inferred from `acquirer_type`**; established sponsor-backed portfolio-company status making the acquisition is sufficient, and the sponsor need not be named. `PLATFORM` carries a deliberately higher bar: a PE firm being the direct buyer does not establish it. Generic VC backing is insufficient. `is_secondary_buyout` stays orthogonal. |
| `is_secondary_buyout` | Sponsor-to-sponsor secondary buyout. | FLAG | Source / researcher | Implemented in current harness from explicit evidence or side-qualified buyer/seller sponsor parties. |
| `offer_mechanism` | How control is acquired from shareholders. **Optional — most transactions have none.** | **TYPED DIMENSION** | Source / researcher | **V3 (§T12): `TENDER_OFFER` / null only.** `MANDATORY_OFFER` not adopted — no evidence it is a distinct mechanism rather than a regulatory qualifier. `SCHEME_OF_ARRANGEMENT` (statutory route), `ONE_STEP_MERGER` / `TWO_STEP_MERGER` (process/sequence) and squeeze-out mechanics are **excluded**. **Orthogonal** to combination/merger structure — a tender offer with a back-end merger is both. Null = mechanism **not established or not applicable**; there is no `NOT_TENDER_OFFER`, and absence of tender-offer language is not negative evidence. No `is_tender_offer` / `is_one_step` / `is_two_step` flags. *(v0.4.1 text follows:)* `TENDER_OFFER` / `MANDATORY_OFFER` / `SCHEME_OF_ARRANGEMENT` / `ONE_STEP_MERGER` / `NULL`. Orthogonal to `combination_structure`, which is the legal form of the combination — a two-step tender offer followed by a squeeze-out merger is both. A mandatory offer is regulatorily triggered by crossing a control threshold; collapsing it into tender offer loses why the offer exists. ML `Tender Offer` / `Mandatory Offer`. Inventory §Q5.1. |
| `approach_type` | **How the offer arrived.** | **TYPED DIMENSION** | Source / researcher | **V3 (§T11) ADD.** `SOLICITED` · `UNSOLICITED` · null. **Independent of `deal_attitude`** — a transaction may be unsolicited and later/currently friendly or recommended. **Not expanded** with auction/bilateral/process values now; extensible later on a concrete Product need. |
| `deal_attitude` | Target board's **posture** toward the approach. | **TYPED DIMENSION** | Source / researcher | **V3 (§T11): `FRIENDLY` / `HOSTILE` / null.** `UNSOLICITED` is **removed** — it is an *approach*, not an attitude, and now lives in `approach_type`. A deal may be unsolicited **and** friendly/recommended. **No `NEUTRAL`**; ML `Natural` not needed. **Absence of hostile evidence is not `FRIENDLY`.** `Initially Hostile` stays derived from history. **Migration:** V2 `hostile = 0` must **not** map to `FRIENDLY` — V2 coerces unstated to zero. *(v0.4.1 text follows:)* `FRIENDLY` / `HOSTILE` / `UNSOLICITED` / `NULL`. ML's `Natural` is **UNRESOLVED** and no value is proposed for it (§Q7.1). **Replaces the harness boolean `hostile`**, which Grata does not carry at all and which conflates three distinct facts — hostile, unsolicited, and proxy contest. Unsolicited is an approach, not an attitude; many unsolicited offers become recommended. `Initially Hostile` is **derived** from attitude history via `transaction_event_history`, never a stored enum value — storing a transition in a state field gives "was it hostile earlier?" two homes that can disagree. Inventory §Q5.2. |
| `transaction_geography` | Cross-border vs domestic. | **DERIVED — not stored** | Derived | **v0.4.1.** A **typed dimension, never a boolean pair** — `is_cross_border` + `is_domestic` would repeat the up/down-round failure, with both-false conflating "same country" and "country unknown". `CROSS_BORDER` / `DOMESTIC` / `UNKNOWN`, from buyer and target country via `transaction_party.party_company_id`. **Both countries must be known**; one unknown yields `UNKNOWN`, never `DOMESTIC`. A same-country default would silently label every incompletely-resolved deal domestic, and the error is invisible because domestic is the common case. ML `Cross-Border` / `Domestic`. Inventory §Q5.9. |
| `is_take_private`, `is_lbo`, `is_secondary_buyout` | Prior listing status, financing, seller identity. | FLAG | Source / derived / researcher | **v0.4: confirmed genuinely orthogonal.** One transaction can be all three; these are different axes and must not be collapsed. |
| ~~`is_divestiture`~~ | **REMOVED from V3 — see inventory §T4.** Not replaced by a flag and not repaired. Spin-Off/Split-Off are already explicit in `event_type`, and ordinary acquisitions of subsidiaries/business units/assets do not become a separate divestiture classification. A *Divestitures* grouping, if wanted, is derived from event types. | FLAG | — | *(v0.4: "Seller-side divestiture characteristic. Existing.")* |
| ~~`is_stock_for_stock`~~ | | | | **v0.4 REMOVE-DERIVABLE** — component forms ⊆ {`ACQUIRER_STOCK`} with no `CASH` component. **Conditional on `consideration_component` being populated**; until then the flag carries evidence no derivation can reach. Same for `has_earnout` / `has_cvr`. |
| `financials_disclosure_status` | Disclosure state for target/company financial metrics and balance-sheet data. | ENUM | Source / researcher | `DISCLOSED`, `UNDISCLOSED`, `PARTIALLY_DISCLOSED`, `UNKNOWN`. |
| `transaction_terms_disclosure_status` | Disclosure state for deal economics, consideration and valuation terms. | ENUM | Source / researcher | Same vocabulary; independent of financials disclosure. |
| `linked_filings_count` | Number of linked supporting filings. | DATA POINT | System | Existing. |
| `platform_transaction_id` | Existing Grata field with insufficient supplied semantic definition. | FK / relationship | System | VERIFY with ENG before using in product/data rules. |

Funding flags. **v0.4:** `is_down_round` / `is_up_round` are replaced by
`round_price_direction` ∈ `UP` / `DOWN` / `FLAT` / `NULL` — both-false previously conflated
*flat* with *unknown*. The collection vocabulary must move with it: the harness emits
`is_down_round` only, so `UP` and `FLAT` have no extraction path today.

`is_unicorn_round` **stays a stored flag**: it looks derivable from post-money ≥ $1B and is
not, because sources assert unicorn status without stating post-money. `is_extension_round`,
`cvc_participation`, `is_oversubscribed` remain flags — each co-occurs freely with any price
direction.

Recap flags (`is_dividend_recap` / `is_equity_recap` / `is_leveraged_recap` /
`is_sponsor_recap`) are **v0.4 REMOVE-DERIVABLE** — each is `recap_type = <value>`. These are
the only flags removable immediately; `recap_type` already exists and is already populated,
so there is no precondition. Redesign of the recap domain itself remains deferred.

---

# 3. Ownership and stake

| Field | Definition | Shape | Population | Notes |
|---|---|---|---|---|
| `pct_acquired` | Percentage acquired in the current transaction. | DATA POINT | Source / researcher / calculated | Never substitute post-transaction ownership. |
| `is_minority` | Current transaction contains a minority-stake characteristic. | FLAG | Derived / explicit evidence | Proposed shared feature replacing minority core event usage. |
| `stake_transition_type` | Explicit ownership transition context. | ENUM | Source when explicit | `NULL` = insufficient explicit transition evidence. |
| `target_ownership_pct_after` | Party-level resulting target ownership after transaction. | DATA POINT | Source / researcher | Existing on transaction party. |

`stake_transition_type` values:
`NEW_MINORITY_STAKE`, `NEW_MAJORITY_STAKE`, `FULL_ACQUISITION`,
`MINORITY_ACQUIRING_MAJORITY`, `MAJORITY_ACQUIRE_REMAINING`,
`MINORITY_ACQUIRING_REMAINING`, `MAJORITY_INCREASING_STAKE`,
`MINORITY_INCREASING_STAKE`.

---

# 4. Security and share mechanics

| Field | Definition | Shape | Population | Applicability |
|---|---|---|---|---|
| `target_security_type` | Target security/interest being acquired. | ENUM | Source / researcher | Security/equity transactions. Enum TBD from actual SEC/agreement vocabulary. |
| `per_share_price` | Consideration per target security. | DATA POINT | Source / researcher | Security transactions. |
| `exchange_ratio` | Acquirer securities received per target security. | DATA POINT | Source / researcher | Stock consideration. |
| `target_shares_outstanding` | Relevant target security denominator. | DATA POINT | Source / researcher | Sparse/public deals. |
| `target_shares_acquired` | Target securities acquired in current transaction. | DATA POINT | Source / calculated / researcher | Security transactions. |
| `acquirer_shares_issued` | Acquirer securities issued as consideration. | DATA POINT | Source / calculated / researcher | Stock consideration. |
| `acquirer_security_price` | Price used to value acquirer securities in stock consideration. | DATA POINT | Source / market/reference / researcher | Requires an explicit/accepted price basis. |
| `acquirer_security_price_date` | Date of the acquirer security price used. | DATE | Source / market/reference / researcher | Required for a calculated stock component value. |

---

# 5. Consideration component

One transaction may have many `consideration_component` rows.

| Field | Definition | Shape | Population / Dependency |
|---|---|---|---|
| `consideration_component_id` | Unique component identifier. | ID | System |
| `transaction_id` | Parent transaction. | FK | Required |
| `target_security_ref` | Reference to the target security receiving this treatment, if securities are normalized. | FK / relationship | Preferred when a reusable security child model exists; otherwise use `target_security_type`. |
| `consideration_form` | Economic form of component. | ENUM | Required |
| `per_share_amount` | Monetary amount per target security. | DATA POINT | Cash/monetary component |
| `currency` | Monetary component currency. | ISO 4217 string | When monetary |
| `exchange_ratio` | Acquirer securities per target security. | DATA POINT | Stock component |
| `acquirer_security_ref` | Reference to acquirer security issued, if securities are normalized. | FK / relationship | Preferred when reusable security facts exist; otherwise use `acquirer_security_type`. |
| `target_security_count` | Target securities represented by component. | DATA POINT | Source / researcher |
| `acquirer_security_count` | Acquirer securities issued. | DATA POINT | Source / calculated |
| `component_value` | Aggregate value of component. | DATA POINT | Source / calculated |
| `value_basis` | Whether component value is stated or calculated. | ENUM | `AS_REPORTED`, `CALCULATED` |
| `trigger_description` | Trigger/contingency terms. | TEXT | CVR/earnout/etc. |
| `election` | Election mechanics/terms. | TEXT / structured attribute | When present |
| `is_prorated` | Proration applies. | FLAG | When applicable |
| `notes` | Unnormalized component terms. | TEXT | Optional |

Initial known `consideration_form` values:
`CASH`, `ACQUIRER_STOCK`, `CVR`, `EARNOUT`.

Retained/target equity, debt assumed, and other forms should be reconciled against the actual agreement/SEC extraction vocabulary before the enum is finalized.

`consideration_type` is a transaction-level **derived summary**: `cash`, `stock`, `cash_and_stock`, `election`, `other`.

Component calculations:
- cash: `per_share_amount × target_security_count` when aggregate cash is not stated
- stock: `exchange_ratio × target_security_count × acquirer_security_price` only with an accepted price date/basis
- prefer source-stated aggregate component values where qualified
- do not automatically include debt assumed in stake-level `equity_value`
- do not assume maximum CVR/earnout value or calculate across incompatible currencies

---


## v0.4.1 — MergerLinks candidates *(CONDITIONAL — definitions required)*

**None of the entries in this sub-section is settled.** They were derived from ML labels
with no accompanying definitions or examples (inventory §Q0), and each depends on a §Q7
answer. They are recorded so the candidate shape is visible, **not** so it can be built.

What is *not* conditional: `Cash`, `Ordinary Shares` and `Preference Shares` map into
existing fields with no addition at all — the ordinary/preference distinction is carried by
`acquirer_security_type` in §4.

| Field / value | Definition | Notes |
|---|---|---|
| `consideration_form = ACQUIRER_DEBT_SECURITY` *(conditional — §Q7.5)* | Debt securities newly issued by the acquirer to sellers as consideration. | **Only needed if ML's `Loan Notes` means acquirer-issued paper.** If it means the target's existing or assumed notes, `DEBT_ASSUMED` already covers it and no form is required. The distinction matters: **not `DEBT_ASSUMED`** — Debt assumed is the *target's existing* liability; loan notes are *new acquirer paper*. Mapping one to the other puts a liability in a consideration slot. ML `Loan Notes`. |
| `consideration_form = ASSET_EXCHANGE` *(conditional — §Q7.6)* | Consideration paid in assets or businesses rather than cash or securities. | **Only needed if ML's `Asset Swap` denotes consideration** rather than an exchange transaction type. Where both parties exchange businesses, whether that is one exchange or two transactions is the multi-event question — inventory §O6, not a consideration question. |
| `consideration_form = TARGET_SPECIAL_DIVIDEND` *(conditional — §Q7.7)* | A pre-closing special dividend paid by the target to its own shareholders. | **Who pays it and when decides everything, and ML's label states neither.** If target-paid and pre-closing, it is **excluded from acquirer-paid consideration aggregation by default**, mirroring `DEBT_ASSUMED` in §C3. Economically part of what shareholders receive, but paid by the target from its own cash — summing it into stake-level `equity_value` double-counts whenever the headline price was struck net of it. ML `Special Dividend`. |
| `payment_timing` *(conditional — §Q7.4)* | `AT_CLOSING` / `DEFERRED` / `CONTINGENT` / `NULL`. | **Only needed if ML's `Contingent Deferred Consideration` spans several mechanics.** If it means an earnout, `EARNOUT` already exists; if a CVR, `CVR` does. The gap is real only for a fixed amount payable later with no contingency. The label collapses two orthogonal axes — *is it contingent?* and *is it deferred?* Performance-contingent is `EARNOUT`; a security issued to holders is `CVR`; a fixed amount payable later with no contingency has no representation today. A typed attribute closes the gap without multiplying forms. |

**Not added, deliberately:** `PREFERENCE_SHARES` and `PARTIAL_SHARE_ALTERNATIVE`.
Ordinary-vs-preference is carried by `acquirer_security_type` (§4), and a Partial Share
Alternative is `election` / `is_prorated` mechanics over ordinary `CASH` + `ACQUIRER_STOCK`
components. Both would duplicate structure that already exists.

# 6. Funding

| Field / Concept | Definition | Shape | Population | Notes |
|---|---|---|---|---|
| `round_label` | **Verbatim source-reported wording**, preserved as stated (e.g. "Series B", "Seed Extension", "Bridge Round"). | DATA POINT | Source | **V3 (§T14): unchanged in role.** One of three distinct concepts; the canonical classification is `round`. |
| `round` | **Canonical, detailed, researcher-selectable/filterable round classification.** | ENUM | Researcher / normalized from source | **V3 (§T14) ADD.** Preserves meaningful numbered variants — `SERIES_A1`, `SERIES_A2`, `SERIES_A3` — **`SERIES_A2` is not normalized down to `SERIES_A`**. `ANGEL` is its own canonical round, **not** folded into Seed. **Settled but extensible**; no Series-G ceiling. Venture debt and convertible notes are **not** rounds — they describe financing/event/instrument structure. |
| `vc_stage` *(v2 name: `round_stage_category`)* | **Broad normalized grouping**, derived from canonical `round`. | ENUM | **Derived from `round`** | **V3 (§T14).** `PRE_SEED` · `SEED` · `EARLY_STAGE` · `GROWTH` · `LATE_STAGE` · null. Mapping: Pre-Seed → `PRE_SEED`; Seed/**Angel** → `SEED`; Series A family **incl. A1/A2** → `EARLY_STAGE`; Series B/C → `GROWTH`; **Series D and beyond** → `LATE_STAGE`. **Derived from canonical `round`, never substring-matched against `round_label`** — explicit normalization logic, not inference from arbitrary source strings. Extension and Bridge stay **orthogonal characteristics**, not stages. |
| `round_sequence_number` | Funding round sequence. | DATA POINT | Source / researcher | Existing. |
| `prior_round_id` | Previous funding round relationship. | FK | System / researcher | Existing. |
| `facility_size` | Financing/debt facility size. | DATA POINT | Source / researcher | Existing. |
| `total_raised_to_date` | Cumulative capital raised. | DATA POINT | Source / researcher | Existing. |
| `round_size` | Total financing round amount. | FINANCIAL METRIC | Source / researcher | Canonical transaction-level funding magnitude. |
| `pre_money_valuation` | Equity valuation before financing. | FINANCIAL METRIC | Source / researcher | Funding only. |
| `post_money_valuation` | Equity valuation after financing. | FINANCIAL METRIC | Source / researcher | Funding only. |

Primary/secondary funding decomposition is optional/lower priority unless the source/product requirement warrants it.

`transaction_party.investment_amount` is an **investor-specific check**. `ROUND_SIZE` is the canonical transaction-level funding amount. A separate transaction-level `investment_amount` is not recommended.

---

# 7. Values and valuation

| Field / Concept | Definition | Shape | Population | Scope |
|---|---|---|---|---|
| `equity_value` | Equity consideration for the stake actually acquired. | FINANCIAL METRIC | Source / calculated / researcher | Stake-level Tier 1. |
| `transaction_value` | As-transacted transaction value. | FINANCIAL METRIC | Source / calculated / researcher | Transaction-level Tier 1. |
| `transaction_value_basis` | Derivation/source basis for transaction value. | ENUM / attribute | System | Basis vocabulary to align with accepted calculation rules. |
| `transaction_size` | Common transaction magnitude across transaction families. | DERIVED DATA POINT | Derived | Product/common transaction level. |
| `transaction_size_basis` | Waterfall rung supplying transaction size. | ENUM / attribute | Derived | **Target vocabulary (Transactions, shipped 2026-08-17): `TRANSACTION_VALUE` · `ROUND_SIZE` · `SPIN_SPLIT_CONSIDERATION_VALUE`** (the last reserved — no source field exists yet). Required whenever `transaction_size` is populated, and **never summed across bases**. The Grata spec additionally lists `EQUITY_VALUE` and `SOLE_INVESTOR_AMOUNT`; **both are recommended for removal** and are not part of the target vocabulary — see inventory §D4 and reconciliation §3 item 5. |
| `implied_equity_value` | Canonical 100%-basis equity valuation. | FINANCIAL METRIC | Source / calculated / researcher | Tier 2. |
| `implied_equity_value_basis` | Waterfall rung producing implied equity. | ENUM / attribute | System | Recommended: `STATED`, `GROSSED_UP_FROM_EQUITY_VALUE`. |
| `total_debt` | Whole-company total debt. | FINANCIAL METRIC | Source / researcher | Point-in-time/deal-relevant. |
| `cash_and_equivalents` | Cash/equivalent balance used in net-debt bridge. | FINANCIAL METRIC | Source / researcher | Economic scope must be documented consistently with comps convention. |
| `net_debt` | Total debt less cash/equivalents. | FINANCIAL METRIC | Source / calculated / researcher | Reported preferred. |
| `implied_enterprise_value` | One canonical 100%-basis enterprise value. | FINANCIAL METRIC | Source / calculated / researcher | Tier 2. |
| `implied_enterprise_value_basis` | Waterfall rung producing implied EV. | ENUM / attribute | System | Recommended: `STATED`, `IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT`, `IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT`. |

Canonical EV rule:
- source-stated whole-company EV → `implied_enterprise_value`
- otherwise `implied_equity_value + net_debt` when supported
- reported net debt preferred
- otherwise calculate net debt only from period-coherent **and currency-coherent** `total_debt - cash_and_equivalents`
- never assume missing debt/cash is zero
- **never backsolve `net_debt` from `implied_enterprise_value - equity_value`**

`ENTERPRISE_VALUE` may remain as an observation/compatibility type during migration, but should not be a competing canonical output.

**Redline 2026-08-17 (reconciliation §3 items 3, 10, 12).**

- **Currency coherence.** Every calculation mixing consideration with a balance-sheet
  figure requires **both currencies known and equal**. Unknown does not calculate; known
  but differing does not calculate; no conversion is attempted. *Unknown is insufficient
  evidence, not permission to assume agreement.* `STATED` values are exempt — one
  source-stated figure is not a sum.
- **No backsolve.** `EQUITY_VALUE` is stake-level, `ENTERPRISE_VALUE` is whole-company;
  below control their difference is mostly the un-acquired stake, and even at 100% it is a
  residual of every inconsistency between two sources. A backsolved net debt is
  indistinguishable from a reported one once stored.
- **Missing row — `equity_value_basis`.** This table gives `transaction_value_basis` and
  `implied_equity_value_basis` but no basis for `equity_value`. Add:

| Field / Concept | Definition | Shape | Population | Scope |
|---|---|---|---|---|
| `equity_value_basis` | Waterfall rung producing stake-level equity value. | ENUM / attribute | System | Recommended: `STATED`, `PER_SHARE_X_SHARES`. |

- **`transaction_value_basis` vocabulary** needs a fourth rung, `EQUITY_BELOW_CONTROL`
  (`pct_acquired < 50`: stake consideration, no debt applicable). Without it, "debt does
  not apply" collapses into `EQUITY_VALUE_ONLY`, which means "debt applies but is
  unknown" — a research queue item rather than a complete record.

---

# 8. Deal-value vs company-financial metric classes

**v0.4:** `financial_metric` is the **preferred home** for both classes — v0.3 said only
that a second table was "not required". A monetary value that belongs in it should not also
be authored as a scalar elsewhere, because a scalar column has nowhere to carry currency,
period, FX treatment, provenance or basis, and each one either loses them or grows private
companion columns that drift.

Three semantic classes, derived from `metric_type`:

- **DEAL_VALUE:** `EQUITY_VALUE`, `TRANSACTION_VALUE`, `ROUND_SIZE`, `PRE_MONEY_VALUATION`, `POST_MONEY_VALUATION`, `IMPLIED_EQUITY_VALUE`, `IMPLIED_ENTERPRISE_VALUE`
- **COMPANY_FINANCIAL:** revenue/earnings, debt/cash, shareholders' equity, ARR/MRR, etc.
- **DERIVED_ROLLUP** *(v0.4)*: `TRANSACTION_SIZE`. Admitted as a metric row so the §9 policy
  applies to it, but deliberately **not** classed `DEAL_VALUE`: it is derived from other rows
  in the same table, so as a peer row any consumer summing deal values would count the same
  money twice. Never summed, never a multiple numerator, always carries
  `transaction_size_basis`, always traceable to the row it was selected from.

This distinction controls applicability: period metadata is essential for company financials; value-basis metadata is essential for deal values; a rollup needs neither but must never be aggregated. No stored `metric_category` is required if it is deterministically mapped from `metric_type`.

## v0.4.1 — synergy metric types

| Metric type | Definition | Notes |
|---|---|---|
| `REVENUE_SYNERGIES` | Reported or projected revenue synergies from the transaction. | ML `Reported Revenue Synergies`. |
| `COST_SYNERGIES` | Reported or projected cost synergies. | ML `Cost Synergies`. |
| `TOTAL_SYNERGIES` | A **stated** total. | **Never `REVENUE + COST`, in either direction.** Sources frequently state only the total or only one component, so reconstructing it by addition fabricates a figure; and a stated total often includes categories beyond revenue and cost — capex, tax, financing — so the parts do not sum to it even when all three are present. Never derive `TOTAL` from parts; never sum `TOTAL` with parts. |

**Class: `SYNERGY`** — a fourth class alongside `DEAL_VALUE`, `COMPANY_FINANCIAL` and
`DERIVED_ROLLUP`. Synergies are projected or realized *outcomes* of the transaction, never
a value of it and never a company financial, and must not be aggregated into either. The
estimate-vs-actual distinction is carried by the existing basis and `is_calculated`
semantics (rule 7 below), not by more metric types.

**`Date Synergies Achieved` is UNRESOLVED — ML definition or example required** — and is deliberately not placed here. Three
readings, and they land in different places: a transaction-level realization date; an
achievement date on an individual synergy metric; or a *target* date by which synergies are
expected to be achieved — "Achieved" may describe the projection rather than an outcome.
Inventory §Q4.

## Metric-row policy — applies to every row *(v0.4)*

1. Currency attaches to the value it qualifies; a row never inherits currency from another row, the transaction, or the source's other figures. Unstated is `NULL`.
2. The same rule governs `period_type` / `period_end_date`.
3. No implicit conversion. `fx_rate` / `fx_rate_date` **record a conversion performed**; they never license one.
4. Source-stated USD is preferred over converted USD, and the two must stay distinguishable (see `value_usd_basis`, open).
5. Debt-inclusive arithmetic requires both currencies known and equal. Unknown is not a match — refuse and emit `NULL`.
6. Per-fact provenance on every row: source attribution plus a fact key.
7. Basis is not a boolean. `is_calculated` records *that* a value was derived; the basis attribute records *how*.

Rules 1–3 are the ones that fail quietly: an inherited currency or period produces a
plausible wrong number rather than a visible gap. Inventory §E4.

# 9. Financial metrics

## Current row structure

| Field | Definition | Shape | Notes |
|---|---|---|---|
| `financial_id` | Metric-row identifier. | ID | Existing. |
| `transaction_id` | Parent transaction. | FK | Existing. |
| `metric_type` | Financial metric vocabulary. | ENUM | Existing; selected names change below. |
| `period_type` | Financial period type. | ENUM | `ANNUAL`, `LTM`, `NTM`, `QUARTERLY`, `INTERIM_YTD`, `POINT_IN_TIME`. |
| `period_end_date` | Metric period/date. | DATE | Existing. |
| `period_end_date_precision` | Precision of period end. | ENUM | `exact`, `month`, `quarter`, `year`. |
| `value_captured` | Metric value. | DATA POINT | Existing. |
| `value_currency` | Metric currency. | ISO 4217 string | Monetary metrics. |
| `fx_rate` | FX rate used. | DATA POINT | Existing. |
| `fx_rate_date` | FX rate date. | DATE | Existing. |
| `margin_pct` | Margin percentage. | DATA POINT | When applicable. |
| `is_calculated` | Calculated indicator. | FLAG | Existing; insufficient alone for full valuation-basis provenance. |

## Canonical metric names

Keep:
`EQUITY_VALUE`, `TRANSACTION_VALUE`, `REVENUE`, `GROSS_PROFIT`,
`FREE_CASH_FLOW`, `TOTAL_DEBT`, `CASH_AND_EQUIVALENTS`, `NET_DEBT`,
`SHAREHOLDERS_EQUITY`, `ARR`, `MRR`, `POST_MONEY_VALUATION`,
`PRE_MONEY_VALUATION`, `ROUND_SIZE`, `IMPLIED_EQUITY_VALUE`,
`IMPLIED_ENTERPRISE_VALUE`.

Change:
- `ADJ_EBITDA` → `EBITDA`
- `ADJ_EBIT` → `EBIT`
- `ADJ_NET_INCOME` → `NET_INCOME`

Deprecate/reconcile:
- `ENTERPRISE_VALUE` → converge canonical output on `IMPLIED_ENTERPRISE_VALUE`

**Redline 2026-08-17 — three semantics to state explicitly (reconciliation §3 items 1, 8,
9, 11, 14).** None require a new column.

- **`POINT_IN_TIME` is derived, not collected.** Balance-sheet metric types — `TOTAL_DEBT`,
  `CASH_AND_EQUIVALENTS`, `NET_DEBT`, `SHAREHOLDERS_EQUITY` — must carry
  `period_type = POINT_IN_TIME`, an exact `period_end_date`, and
  `period_end_date_precision = exact`. A balance sheet covers no period; it is a position
  on one date, so `LTM`, `TTM`, `NTM`, `ANNUAL` and `QUARTERLY` are category errors on
  these types. The specific trap is recording the *filing's* period label — that describes
  where the figure was found, not what it measures. Derive from `metric_type` so it cannot
  be mislabelled, and add the rule to §16.
- **Qualifiers never inherit.** A row's `value_currency`, `period_type` and
  `period_end_date` belong to that row's own amount and must never be taken from a sibling
  row — including rows on the same transaction and rows from the same source. Unstated is
  null. The harness fixture for this shows a borrowed period end manufacturing a 5.0x
  multiple from figures that never described the same period: a number that reads as
  entirely ordinary and is unfalsifiable without returning to both sources. This is the
  same defect class as Silver parity item K1.
- **`fx_rate` / `fx_rate_date` record a conversion that was actually performed** by the
  source or a researcher — never one the pipeline invented. Where currencies differ and no
  stated conversion exists, the correct canonical output is null. Complementing this:
  where a source states the same figure in both a local currency and USD
  (*"3.14 trillion won ($2.2 billion)"*), prefer the **stated** USD figure and set the
  currency to USD. That is how comparable USD figures are obtained without an FX engine.
- **Per-fact provenance.** Rows need source attribution and a fact key, so *corroboration*
  (two sources, same figure) is distinguishable from *multiplicity* (one source, two
  different figures). Both are legitimate and they mean opposite things about confidence.
  Supersession on re-collection is an open question — see reconciliation §5.

---

# 10. Transaction multiples

| Field | Definition | Shape | Notes |
|---|---|---|---|
| `multiple_id` | Multiple-row identifier. | ID | Existing. |
| `transaction_id` | Parent transaction. | FK | Existing. |
| `multiple_type` | Multiple type. | ENUM | `EV_REVENUE`, `EV_EBITDA`, `EV_EBIT`, `EV_FCF`, `PE`, `PB`, `PTBV`. |
| `multiple_value` | Multiple value. | DATA POINT | Reported or calculated. |
| `period_basis` | Denominator period basis. | ENUM | LTM/NTM/ANNUAL/QUARTERLY. |
| `period_end_date` | Denominator period end. | DATE | Existing. |
| `period_end_date_precision` | Denominator date precision. | ENUM | Existing. |
| `numerator_value_type` | Canonical numerator family. | ENUM | Recommend `implied_enterprise_value`, `implied_equity_value`. |
| `denominator_financial_id` | Linked financial metric denominator. | FK | Expected for calculated rows. |
| `source_flag` | Source-stated vs calculated. | ENUM | `as_reported`, `calculated`. |
| `quality` | Multiple quality/state. | ENUM | Current: `CALCULATED`, `NM`, `NOT_CALCULABLE`; verify naming. |
| `value_usd_basis` | USD numerator/value basis metadata. | DATA POINT | Existing; exact semantics/name to verify. **Redline 2026-08-17:** candidate definition — *the USD figure the source itself stated*, not one converted downstream. Confirm whether that is the intent or whether it denotes a conversion Grata performed; the two are opposite and the name does not disambiguate. |
| `is_calculated` | Calculated indicator. | FLAG | Overlaps `source_flag`; review redundancy. |

Rules:
- EV multiples use `implied_enterprise_value`.
- Equity multiples use `implied_equity_value`.
- Preserve both as-reported and calculated rows.
- Researcher entry is a collection method; a researcher-entered source multiple remains `as_reported`.
- P/B already exists as `PB` and uses `SHAREHOLDERS_EQUITY`.
- P/E (`PE`), P/B (`PB`) and P/TBV (`PTBV`) already exist in Grata. Use `NET_INCOME` for P/E and `SHAREHOLDERS_EQUITY` for P/B. Keep P/TBV for source-reported multiples, but do not add tangible-book extraction/calculation in this phase.

---

# 11. Spin / Split

> **v0.4 placement.** These are **transaction-level scalars**, not a `spin_split_mechanics`
> child table. Assessed field by field: none is multi-valued per transaction except
> per-security-class share counts, which belong to the generalized security/share model.
> `spin_split_consideration_value` becomes a `financial_metric` row under the §9 policy.
> Inventory §G.

Event-specific fields:
- `spin_split_type`
- `spin_split_distribution_mechanism`
- `spin_split_record_date`
- `spin_split_distribution_date`
- `spin_split_pct_distributed`
- `spin_split_distribution_ratio`
- `split_off_pct_parent_shares_exchanged`

Security/share facts should reuse the generalized transaction-security capability where practical:
- parent / SpinCo security type
- security price and price date
- relevant parent shares
- distributed SpinCo shares
- split-off shares tendered

`spin_split_consideration_value` is a reported/derived event value that may feed common `transaction_size`.

Rules:
- target = SpinCo
- acquirer may legitimately be null
- SpinCo domain may legitimately be null before one exists
- record/distribution dates are mechanics dates, not lifecycle status events

# 12. Transaction parties

## Current fields

| Field | Definition | Shape | Notes |
|---|---|---|---|
| `party_id` | Party-participation identifier. | ID | Existing. |
| `transaction_id` | Parent transaction. | FK | Existing. |
| `party_name` | Party display/source name. | DATA POINT | Existing. |
| `party_type` | Entity classification. | ENUM / entity attribute | Prefer canonical entity profile when resolved. |
| `role` | Transaction role. | ENUM | Existing. |
| `is_lead` | Lead participant. | FLAG | Existing. |
| `party_company_id` | Canonical company ID. | FK | Existing. |
| `entity_resolution_status` | Resolution state. | ENUM | `resolved`, `proposed_match`, `unresolved`, `na_natural_person`. |
| `unresolved_party_name` | Fallback unresolved party name. | DATA POINT | Existing. |
| `round_participation_pct` | Investor's participation percentage. | DATA POINT | Existing. |
| `target_ownership_pct_after` | Resulting target ownership for party. | DATA POINT | Existing. |
| `investment_amount` | Investor-specific investment/check amount. | DATA POINT | Do not confuse with round size. |
| `investment_currency` | Investor-check currency. | ISO 4217 string | Existing. |
| `is_new_investor` | Investor is new to the company/round context. | FLAG | Existing current field. |
| `investment_amount_usd` | Converted investor check. | DATA POINT | Existing. |
| `advisor_specialty` | Advisor service. | ENUM | **v0.4 expansion accepted**, enumerated from actual extraction vocabulary. Keep `financial_advisory`, `legal`, `fairness_opinion`, `accounting`. **Add** `tax`, `proxy_solicitation`, `information_agent` — each named explicitly in the LC extraction prompt's own `OTHER` definition, so the evidence exists in sources and is discarded at the enum. `regulatory` exists in Grata with **no extraction path** — VERIFY a source of population or accept as researcher-only. `restructuring` / `capital_markets` / `communications` DEFER: no extraction evidence, do not freeze on speculation. Inventory §H4. |
| `bidder_role` | The bidder's role, or defense posture, in a contested situation. | ENUM | **v0.4.1 ADD — shape open.** Includes `WHITE_KNIGHT`. *Context, not an ML definition:* the term conventionally denotes a friendly counter-bidder invited by the target to defeat a hostile bid; ML's own definition was not available. Whether the right shape is a bidder role, a defense-posture attribute, or both is open. **A party property, not a transaction attitude:** a white knight is always friendly, so placing it in `deal_attitude` would make it mutually exclusive with `FRIENDLY`, which is exactly backwards. Presupposes a competing hostile bid; the harness already collects `competing_bid`, and linking the two bids is the related-transaction concept deferred in inventory §M. ML `White Knight`. Inventory §Q5.3. |
| `advised_party` | Party/client receiving advisory service. | **relationship** | **V3 (§T6): an advisor participation references the specific transaction party or parties advised; side is derived from that party's role. `BOTH` needs no special encoding — it is two party references.** Where a source establishes only side and not the party, preserve as partial evidence; do not manufacture a relationship. *(v0.4 text:)* Existing; may need finer granularity. **v0.4:** harness collects `TARGET` / `ACQUIRER` / `PARENT_SELLER` / `BOTH` / `UNKNOWN`. **`BOTH` has no Grata equivalent** — `ADVISOR_BUY_SIDE` / `ADVISOR_SELL_SIDE` are side-specific. Either add a both-sides representation or stop emitting it; mapping it to one side asserts a fact the source did not state. ENG DECISION. Inventory §H4. |
| `advisor_person_name` | Advisor individual name. | DATA POINT | Current one-person field is insufficient; move to child relationship. |
| `advisor_person_title` | Advisor individual title. | DATA POINT | Same. |
| `lender_role` | Lender/financing-provider role. | ENUM | Existing. |
| `party_source` | Party assertion provenance. | ENUM | Existing. |
| `party_source_url` | Provenance URL. | URL | Existing. |

**V3 `PartyRole` (§T5) — decided 2026-08-19:**

`TARGET` · `BUYER` · `SELLER` · `INVESTOR` · `PARENT_ACQUIRER` · `PARENT_SELLER` ·
`SPONSOR_BUYER` · `SPONSOR_SELLER` · `ADVISOR` · `LENDER` · `JV_PARTNER` · `UNDERWRITER`.

- **`SPONSOR_BUYER` / `SPONSOR_SELLER` are explicit roles.** Sponsor side is meaningful
  transaction-role information and is directly representable.
- **`PARENT_ACQUIRER` is added** — its absence from the earlier baseline was an inventory
  omission, not a modelling decision.
- **`ADVISOR` replaces `ADVISOR_BUY_SIDE` / `ADVISOR_SELL_SIDE`** (§T6). Side is derived from
  the advised party's role.
- **`MERGER_SUB`, `BUYER_PLATFORM`, `SELLER_PLATFORM` are deliberately not included** —
  acquisition-vehicle concepts are considered separately (§T10).

> **SUPERSEDED (2026-08-19).** *v0.4 text:* "Current `PartyRole` values: `TARGET`, `BUYER`,
> `SELLER`, `INVESTOR`, `SPONSOR`, `PARENT_SELLER`, `ADVISOR_BUY_SIDE`, `ADVISOR_SELL_SIDE`,
> `LENDER`, `JV_PARTNER`, `UNDERWRITER`. Current Grata does **not** distinguish
> `BUYER_SPONSOR` / `SELLER_SPONSOR`. Preferred unified treatment is generic `SPONSOR` plus a
> relationship to the sponsored transaction participant; flat side-specific roles remain an
> implementation alternative."
>
> That preference is **reversed** by §T5. A relationship-only treatment loses side whenever
> the sponsor party is unresolved — which is the common case.

Current `AdvisorSpecialty`:
`financial_advisory`, `legal`, `accounting`, `fairness_opinion`, `regulatory`.

Potential additional specialties should be driven by actual mappings (e.g. PR/communications, tax, restructuring, financing/debt advisory).

---

# 13. Advisor-person child relationship

Current Grata stores one `advisor_person_name` / `advisor_person_title` pair on an advisor party row. The required change is cardinality: one advisor-firm participation may have many people.

Conceptual `transaction_advisor_person` fields:
- `advisor_person_id`
- advisor-firm participation FK
- optional canonical person ID where matched
- person name
- title / seniority
- person specialty
- `is_lead` where applicable
- source/provenance

Many people must never create duplicate advisor-firm participations. Person/entity matching is optional; unresolved person name/title can be stored first and matched later.

> **v0.4 — extraction requirement, not only a schema shape.** Person ↔ firm affiliation and
> transaction role must be **preserved at extraction** wherever the source states them, even
> though canonical people ownership sits with another team. Identity is theirs; *affiliation
> and role in this transaction* are transaction facts that exist only in this source and
> cannot be reconstructed downstream if collection discards them. Capture unresolved,
> resolve optionally.
>
> Current state is a gap, not a refinement: the harness `advisor` table stores the **firm**
> name, type and advised party, and **no person fields at all**. The same shape applies to
> investor-side people (round lead, board seat), so the requirement is stated at the
> participation level. Inventory §H3.

---

# 14. Transaction event history and status

| Field | Definition | Shape |
|---|---|---|
| `event_id` | Event-history row ID. | ID |
| `transaction_id` | Parent transaction. | FK |
| `type` | Lifecycle/milestone event type. | ENUM |
| `date` | Event date. | DATE |
| `date_precision` | Exact/month/quarter/year. | ENUM |

Current history values:
`RUMORED`, `ANNOUNCED`, `CLOSED`, `TERMINATED`, `EXPECTED_CLOSE`,
`FILED`, `EFFECTIVE`, `AMENDED`, `UPDATED`, `SOURCED`.

Primary current product status:
`ANNOUNCED`, `CLOSED`, `TERMINATED`.

`signing_date` is an independent transaction fact and may precede announcement; physical placement is an ENG decision.

Consideration updates do not change transaction status.

---

# 15. Narrative and rationale artifacts

Two domains omitted from v0.3/v0.4. Neither has a Grata counterpart in the supplied
material. Full treatment in inventory §S; approved semantics in §R7–§R10.

| Field / Concept | Definition | Shape | Population | Decision |
|---|---|---|---|---:|
| `summary_text` | Free-prose transaction narrative. **Derived** from the canonical `transaction_record` row plus an advisor rollup; the producing stage reads **no source text**. | TEXT | Derived (LLM) | ADD |
| `word_count` | Length of `summary_text`. Stored today but **never validated** against the prompt's 80–150 word contract. | INTEGER | Derived | ADD / VERIFY |
| `primary_rationale` | Why the transaction occurred. One of eight values. | ENUM | Source-stated, or inferred by an approved method | KEEP |
| `secondary_rationales` | Additional rationales. **Each element requires its own basis and evidence** — a bare array of enum values is insufficient (§R8). | repeating | as above | CHANGE |
| `rationale_basis` | `SOURCE_STATED` \| `INFERRED` (or equivalent). Carried **per rationale**, primary and every secondary. Physical placement not prescribed. | ENUM | Derived from how the classification was reached | **ADD** |
| rationale evidence attribution | Durable source reference supporting a `SOURCE_STATED` rationale. | reference | Captured at classification time | **ADD** |
| `supporting_excerpt_index` | Ephemeral index into a prompt-time excerpt list that is **never persisted**. | INTEGER | — | **CHANGE / REPLACE** by the row above — not derivable, the referent is simply gone |

**`OTHER` (§R9)** means a source-supported rationale exists but fits no named category.
Absence of a determinable source-supported rationale is **NULL**, not `OTHER`.

**Summary authority (§R10).** No structured field may be populated or corrected solely by
parsing or mining `summary_text`.

# 16. Operational / disclosure concepts

| Field / Concept | Definition | Decision |
|---|---|---:|
| `financials_disclosure_status` | Disclosure state for company financials/balance-sheet metrics. | KEEP |
| `transaction_terms_disclosure_status` | Disclosure state for deal economics, consideration and valuation terms. | ADD |
| `record_review_status` | Transaction review/triage state. | DEFER / VERIFY physical field; enum exists |
| field-level null reasons | Reason a specific field is null. | NOT REQUIRED / DEFER |

Both disclosure fields use `DISCLOSED`, `PARTIALLY_DISCLOSED`, `UNDISCLOSED`, `UNKNOWN` and are independent. Example: terms undisclosed + revenue disclosed is valid.

# 17. Requiredness / QA contract

- `transaction_id` — REQUIRED
- canonical `event_type` — REQUIRED at Gold
- target/issuer/primary subject party — CONDITIONAL REQUIRED by event family
- acquirer/buyer — CONDITIONAL REQUIRED for acquisition-style M&A; not applicable to Spin/Split or funding
- `announcement_date` — CONDITIONAL REQUIRED for surfaced announced/closed/terminated records
- `close_date` — REQUIRED when status is `CLOSED`
- `termination_date` — REQUIRED when status is `TERMINATED`
- `transaction_size_basis` — REQUIRED when `transaction_size` is populated
- financial metric `period_type` / `period_end_date` — CONDITIONAL REQUIRED for period-based company financials
- `rationale_basis` — REQUIRED whenever any rationale is populated, primary or secondary
- rationale evidence attribution — REQUIRED when `rationale_basis` = `SOURCE_STATED`

This is semantic/QA requiredness, not necessarily database `NOT NULL`.

# 18. Deferred / lower-priority

- consideration amendment/version history
- detailed P/TBV denominator collection
- related-transaction linkage
- field-level null reasons
- detailed researcher-review workflow
- recap/IPO redesign
- broad SEC financial-statement mining
