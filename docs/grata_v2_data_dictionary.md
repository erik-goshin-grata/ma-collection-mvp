# Grata V2 Transaction Data Dictionary — Revised Draft v0.3

**Status:** Revised working draft after human redline  
**Basis:** Current Grata schemas/enums plus accepted/tested transaction-model decisions through 2026-08-13.  
**Important:** Physical storage/table placement remains an ENG decision unless cardinality requires a repeating child/relationship.

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
| `event_category` | High-level product/event family derived from event type. | ENUM | Derived | Recommended: `ma`, `spin_split`, `investment_funding`, `recapitalization`, `exit_liquidity`. `divestiture` is a feature (`is_divestiture`), not a competing category. |
| `target_type` | Structural type of target/object acquired. | ENUM | Source / researcher | Proposed: `STANDALONE_COMPANY`, `BUSINESS_UNIT`, `SUBSIDIARY`, `ASSETS`, `NULL`. |
| `acquirer_type` | Economic buyer classification. | ENUM / entity attribute | Entity data / researcher | Prefer canonical entity profile when available; not necessarily stored on transaction record. |
| `is_merger` | Transaction is structured as a merger. | FLAG | Source / researcher | Recommended M&A structure flag. |
| `is_reverse_merger` | Transaction uses a reverse-merger structure. | FLAG | Source / researcher | May coexist with `is_de_spac`. |
| `is_de_spac` | Transaction is a de-SPAC business combination. | FLAG | Source / researcher | Existing flag; recommended under M&A rather than separate canonical event type. |
| `is_merger_of_equals` | Merger-of-equals characteristic. | FLAG | Source / researcher | Existing. |
| `is_take_private` | Public target is taken private. | FLAG | Source / derived / researcher | Existing. |
| `is_lbo` | Leveraged-buyout characteristic. | FLAG | Source / derived / researcher | Existing. |
| `is_mbo` | Management buyout. | FLAG | Source / researcher | Existing. |
| `is_mbi` | Management buy-in. | FLAG | Source / researcher | Existing. |
| `is_platform_investment` | Sponsor/platform characteristic. | FLAG | Source / researcher | Existing. |
| `is_add_on` | Add-on acquisition. | FLAG | Source / researcher | Existing. |
| `is_secondary_buyout` | Sponsor-to-sponsor secondary buyout. | FLAG | Source / researcher | Existing. |
| `is_de_spac` | De-SPAC characteristic. | FLAG | Source / researcher | Existing / lower priority. |
| `is_divestiture` | Seller-side divestiture characteristic. | FLAG | Source / researcher | Existing. |
| `is_stock_for_stock` | Stock-for-stock consideration characteristic. | FLAG | Derived / source | Existing summary flag. |
| `financials_disclosure_status` | Disclosure state for target/company financial metrics and balance-sheet data. | ENUM | Source / researcher | `DISCLOSED`, `UNDISCLOSED`, `PARTIALLY_DISCLOSED`, `UNKNOWN`. |
| `transaction_terms_disclosure_status` | Disclosure state for deal economics, consideration and valuation terms. | ENUM | Source / researcher | Same vocabulary; independent of financials disclosure. |
| `linked_filings_count` | Number of linked supporting filings. | DATA POINT | System | Existing. |
| `platform_transaction_id` | Existing Grata field with insufficient supplied semantic definition. | FK / relationship | System | VERIFY with ENG before using in product/data rules. |

Funding/recap flags such as `is_down_round`, `is_up_round`, `is_unicorn_round`, `is_extension_round`, `cvc_participation`, `is_oversubscribed`, and recap flags remain existing/lower-priority fields.

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

# 6. Funding

| Field / Concept | Definition | Shape | Population | Notes |
|---|---|---|---|---|
| `round_label` | Source round label (e.g. Series B). | DATA POINT | Source / researcher | Existing. |
| `round_stage_category` | Normalized funding stage. | ENUM | Derived / researcher | Existing. |
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
| `transaction_size_basis` | Waterfall rung supplying transaction size. | ENUM / attribute | Derived | Examples: `TRANSACTION_VALUE`, `EQUITY_VALUE`, `ROUND_SIZE`, `SOLE_INVESTOR_AMOUNT`, `SPIN_SPLIT_CONSIDERATION_VALUE`. Required when populated. |
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
- otherwise calculate net debt only from period-coherent `total_debt - cash_and_equivalents`
- never assume missing debt/cash is zero

`ENTERPRISE_VALUE` may remain as an observation/compatibility type during migration, but should not be a competing canonical output.

---

# 8. Deal-value vs company-financial metric classes

The current Grata `financial_metric` physical table can remain a single normalized fact table, but the dictionary should distinguish two semantic classes derived from `metric_type`:

- **DEAL_VALUE:** `EQUITY_VALUE`, `TRANSACTION_VALUE`, `ROUND_SIZE`, `PRE_MONEY_VALUATION`, `POST_MONEY_VALUATION`, `IMPLIED_EQUITY_VALUE`, `IMPLIED_ENTERPRISE_VALUE`
- **COMPANY_FINANCIAL:** revenue/earnings, debt/cash, shareholders' equity, ARR/MRR, etc.

This distinction controls applicability: period metadata is essential for company financials; value-basis metadata is essential for deal values. No stored `metric_category` is required if it is deterministically mapped from `metric_type`.

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
| `value_usd_basis` | USD numerator/value basis metadata. | DATA POINT | Existing; exact semantics/name to verify. |
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
| `advisor_specialty` | Advisor service. | ENUM | Current vocabulary needs source-mapping review before expansion. |
| `advised_party` | Party/client receiving advisory service. | ENUM / relationship | Existing; may need more precise participant/body linkage. |
| `advisor_person_name` | Advisor individual name. | DATA POINT | Current one-person field is insufficient; move to child relationship. |
| `advisor_person_title` | Advisor individual title. | DATA POINT | Same. |
| `lender_role` | Lender/financing-provider role. | ENUM | Existing. |
| `party_source` | Party assertion provenance. | ENUM | Existing. |
| `party_source_url` | Provenance URL. | URL | Existing. |

Current `PartyRole` values:
`TARGET`, `BUYER`, `SELLER`, `INVESTOR`, `SPONSOR`, `PARENT_SELLER`,
`ADVISOR_BUY_SIDE`, `ADVISOR_SELL_SIDE`, `LENDER`, `JV_PARTNER`, `UNDERWRITER`.

Current Grata does **not** distinguish `BUYER_SPONSOR` / `SELLER_SPONSOR`. Preferred unified treatment is generic `SPONSOR` plus a relationship to the sponsored transaction participant; flat side-specific roles remain an implementation alternative.

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

# 15. Operational / disclosure concepts

| Field / Concept | Definition | Decision |
|---|---|---:|
| `financials_disclosure_status` | Disclosure state for company financials/balance-sheet metrics. | KEEP |
| `transaction_terms_disclosure_status` | Disclosure state for deal economics, consideration and valuation terms. | ADD |
| `record_review_status` | Transaction review/triage state. | DEFER / VERIFY physical field; enum exists |
| field-level null reasons | Reason a specific field is null. | NOT REQUIRED / DEFER |

Both disclosure fields use `DISCLOSED`, `PARTIALLY_DISCLOSED`, `UNDISCLOSED`, `UNKNOWN` and are independent. Example: terms undisclosed + revenue disclosed is valid.

# 16. Requiredness / QA contract

- `transaction_id` — REQUIRED
- canonical `event_type` — REQUIRED at Gold
- target/issuer/primary subject party — CONDITIONAL REQUIRED by event family
- acquirer/buyer — CONDITIONAL REQUIRED for acquisition-style M&A; not applicable to Spin/Split or funding
- `announcement_date` — CONDITIONAL REQUIRED for surfaced announced/closed/terminated records
- `close_date` — REQUIRED when status is `CLOSED`
- `termination_date` — REQUIRED when status is `TERMINATED`
- `transaction_size_basis` — REQUIRED when `transaction_size` is populated
- financial metric `period_type` / `period_end_date` — CONDITIONAL REQUIRED for period-based company financials

This is semantic/QA requiredness, not necessarily database `NOT NULL`.

# 17. Deferred / lower-priority

- consideration amendment/version history
- detailed P/TBV denominator collection
- related-transaction linkage
- field-level null reasons
- detailed researcher-review workflow
- recap/IPO redesign
- broad SEC financial-statement mining
