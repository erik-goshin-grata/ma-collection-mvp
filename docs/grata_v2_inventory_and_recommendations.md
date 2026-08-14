# Grata V2 Transaction Data Model — Master Inventory & Recommendations v0.3

**Status:** Revised working draft after human redline  
**Scope:** Current Grata `enums.py` / `schemas.py` compared with the tested/accepted transaction harness model and the data-model decisions reviewed through 2026-08-13.  
**Out of scope for redesign:** MergerLinks/Valu8 schema reconciliation, recap/IPO redesign, collection workflow redesign, historical migration/backfill design.

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
- Current Grata names are retained where the recommendation is **KEEP**; new names are used only for explicit **ADD / CHANGE** recommendations.
- Track **requiredness** in the dictionary as `REQUIRED`, `CONDITIONAL`, or `OPTIONAL`. This is a semantic/QA requirement matrix; it does not require every field to become SQL `NOT NULL`.

## 2. Status labels

- **KEEP** — concept is correct as-is.
- **CHANGE** — retain the concept but change semantics/name/placement.
- **ADD** — missing capability supported by current requirements/testing.
- **LEGACY** — compatibility/old concept; should not be canonical.
- **DEFER** — retain but do not redesign in this phase.
- **VERIFY** — concept exists or is plausible, but end-to-end placement/population still needs confirmation.

---

# A. Core event and transaction record model

## A1. Event taxonomy

| Concept / Field | Shape | Current Grata | Decision | Recommendation / Definition |
|---|---|---|---:|---|
| `event_type = ACQUISITION` | ENUM | Exists | KEEP / BROADEN M&A SEMANTIC | Canonical M&A transaction type covering company, subsidiary, business unit, assets, equity stake, merger structures, reverse mergers and de-SPAC business combinations where the underlying economic event is an acquisition/business combination. Product grouping may simply be **M&A**. |
| `MERGER` | ENUM today | Exists | CHANGE | Move merger out of the top-level event taxonomy into `is_merger`; merger is a legal/transaction structure within M&A. |
| `is_merger` | FLAG | Missing | ADD | True when the transaction is structured as a merger. |
| `REVERSE_MERGER` | ENUM today | Exists | CHANGE | Canonical `event_type = ACQUISITION` plus `is_reverse_merger = true`. A transaction may also be a de-SPAC. |
| `is_reverse_merger` | FLAG | Missing | ADD | Reverse-merger structure. |
| `SPAC_DE_SPAC` | ENUM today | Exists | CHANGE | Canonical `event_type = ACQUISITION` plus existing `is_de_spac = true`. De-SPAC and reverse-merger flags may coexist when both are factually supported. |
| `is_de_spac` | FLAG | Exists | KEEP | De-SPAC structure/feature. |
| `is_merger_of_equals` | FLAG | Exists | KEEP | Special merger characteristic. |
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
| `recap_type` | ENUM | DEFER | Keep current recap structure. |
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
| `is_mbo` | FLAG | KEEP | Keep. |
| `is_mbi` | FLAG | KEEP | Keep. |
| `is_platform_investment` | FLAG | KEEP | Keep. |
| `is_add_on` | FLAG | KEEP | Keep. |
| `is_secondary_buyout` | FLAG | KEEP | Keep. |
| `is_de_spac` | FLAG | KEEP / DEFER | Existing feature. |
| `is_divestiture` | FLAG | KEEP | Seller-side characteristic. |
| `is_stock_for_stock` | FLAG | KEEP | Summary stock-consideration feature. |
| `is_down_round` | FLAG | KEEP | Funding feature. |
| `is_up_round` | FLAG | KEEP | Funding feature. |
| `is_unicorn_round` | FLAG | KEEP | Funding feature. |
| `is_extension_round` | FLAG | KEEP | Funding feature. |
| `cvc_participation` | FLAG | KEEP | Funding feature. |
| `is_dividend_recap` | FLAG | DEFER | Preserve. |
| `is_equity_recap` | FLAG | DEFER | Preserve. |
| `is_leveraged_recap` | FLAG | DEFER | Preserve. |
| `is_sponsor_recap` | FLAG | DEFER | Preserve. |
| `linked_filings_count` | DATA POINT | KEEP | Operational/source linkage. |
| `has_earnout` | FLAG | KEEP | Summary flag; detailed term belongs in consideration components. |
| `has_cvr` | FLAG | KEEP | Summary flag; detailed term belongs in consideration components. |
| `is_merger_of_equals` | FLAG | KEEP | Keep. |
| `is_oversubscribed` | FLAG | KEEP | Funding feature. |
| `platform_transaction_id` | ID / relationship | VERIFY / DO NOT RECOMMEND YET | Field exists, but the supplied Grata materials do not define its semantics sufficiently. Clarify with ENG before documenting or building behavior around it. |
| `has_cbi_data` | FLAG | LEGACY / OPERATIONAL | Source-system metadata, not core business semantics. |

## A5. New transaction-level concepts

| Concept | Shape | Decision | Notes |
|---|---|---:|---|
| `is_minority` | FLAG | ADD | Tested replacement for minority core event. |
| `stake_transition_type` | ENUM FEATURE | ADD | Explicit ownership transition context. |
| `is_merger` | FLAG | ADD candidate | If merger is removed from top-level M&A event taxonomy. |
| `target_type` | ENUM | ADD | Transaction fact. |
| `transaction_size` | DERIVED DATA POINT | ADD | Common product magnitude. |
| `transaction_size_basis` | ENUM / basis attribute | ADD | Identifies the underlying magnitude selected: e.g. `TRANSACTION_VALUE`, `EQUITY_VALUE`, `ROUND_SIZE`, `SOLE_INVESTOR_AMOUNT`, `SPIN_SPLIT_CONSIDERATION_VALUE`. Required wherever `transaction_size` is populated. |
| `transaction_terms_disclosure_status` | ENUM | ADD | Separate from financials disclosure; covers deal economics/consideration/value terms using the same `DISCLOSED / PARTIALLY_DISCLOSED / UNDISCLOSED / UNKNOWN` vocabulary. |
| `is_reverse_merger` | FLAG | ADD | Reverse-merger structure under M&A. |

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

The current Grata `financial_metric` table mixes **deal valuation metrics** and **company financial metrics** in one normalized row shape. That can work; a second physical table is not required merely to separate the concepts.

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

## D2. Canonical EV rule

Use one canonical whole-company EV: `implied_enterprise_value`.

- source-stated whole-company EV → `implied_enterprise_value`
- otherwise, when supported: `implied_equity_value + net_debt`
- reported `net_debt` is preferred
- otherwise `net_debt = total_debt - cash_and_equivalents` only when both are available and period-coherent
- missing debt/cash is never assumed to be zero

`MetricType.ENTERPRISE_VALUE` may remain temporarily as an input/compatibility observation type, but should not compete with `IMPLIED_ENTERPRISE_VALUE` as a canonical output.

## D3. Basis / provenance

`*_basis` answers **which rung of the accepted value waterfall produced the canonical value**. It is more specific than simply `AS_REPORTED` / `CALCULATED`.

Recommended examples:

- `transaction_value_basis`
  - `STATED`
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

Event-specific mechanics should live together conceptually (a `spin_split_mechanics` child structure is a reasonable physical option):

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
- `spin_split_parent_shares`, `spin_split_distributed_shares`, and `split_off_shareholder_shares_tendered` are event-specific security counts and may live on `spin_split_mechanics` while referencing the relevant parent/SpinCo securities.
- `spin_split_share_price` should preferably be represented by the referenced SpinCo security price rather than duplicated.
- `spin_split_consideration_value` is a derived event value. It may be kept for detail/auditability; its primary common-product consumer is `transaction_size`.

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

Current Grata already has `advisor_person_name` and `advisor_person_title`, but only one pair per advisor party row; the gap is **cardinality**, not complete absence. Person matching should be optional: store an unresolved person name/title first and attach a canonical `person_id` when/if entity resolution succeeds.

## H4. `advisor_specialty`

Current:
- `financial_advisory`
- `legal`
- `accounting`
- `fairness_opinion`
- `regulatory`

Potential additions should be based on actual source/collection mappings before enum freeze (e.g. PR/communications, tax, restructuring, financing/debt advisory, capital markets).

`LENDER` remains distinct from a financing/debt advisor.

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

| Concept | Current state | Decision |
|---|---|---:|
| `financials_disclosure_status` | Exists on `transaction_record` | KEEP / NARROW | Covers company/target financial metrics and balance-sheet information. |
| `transaction_terms_disclosure_status` | Missing | ADD | Covers deal economics/consideration/value terms. Reuse `DISCLOSED / PARTIALLY_DISCLOSED / UNDISCLOSED / UNKNOWN`. |
| `RecordReviewStatus` enum | Exists, field absent from `TRANSACTION_RECORD_SCHEMA` | DEFER / VERIFY |
| field-level null reasons | Missing | DEFER / NOT REQUIRED |
| detailed researcher-review workflow | Not core data model | DEFER |

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
