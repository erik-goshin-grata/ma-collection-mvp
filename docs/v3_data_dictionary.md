# Transactions V3 — Data Dictionary

**Product Contract:** Transactions V3 — `V3-PC-1.0`
**Status:** CURRENT · **Reconciled:** 2026-08-22 · **Supersedes:** `docs/grata_v2_data_dictionary.md` v0.4.1
**MVP reference baseline:** `ma-collection-mvp` `origin/main` @ `2e2ccb7` · **Schema:** `010_v3_take_private_outcome.sql`
**Package:** Release Manifest · Change & Decision Register · Data Dictionary · Engineering Handoff

*Authoritative current-state field contract. Decision history lives in the Register.*

---

## How to read this

This is **current state only**. Why a field looks the way it does belongs in the Change &
Decision Register and, beneath it, in `docs/decisions.md` and
`docs/grata_v2_inventory_and_recommendations.md`.

> **MVP vs Engineering.** This is the field contract for the **target** model. Every
> `Owner`, stage reference, code line and behaviour note describes the
> **`ma-collection-mvp` reference implementation**, which is separate from the Engineering
> production implementation. MVP behaviour is evidence that the contract is expressible — it
> is not itself the contract, and it does not prescribe Engineering's design.

**Physical storage is not prescribed here.** The JSON nesting used by the extraction prompts
(`target`, `acquirer`, `deal`, `features`, `target_financials`) is a **prompt request/response
shape, not a storage model**. Engineering owns table placement and normalization; the contract
below is about meaning, type and nullability.

### Column conventions

- **Origin** — `EXTRACTED` (a model reads it from a source and it enters the observation
  ledger) or `DERIVED` (computed deterministically at aggregation from other canonical fields;
  never observed, never model-authored).
- **Owner** — the stage that authors the value.
- **Null semantics** — for every nullable field, `NULL` means **the fact is not established**.
  It is never "no", never zero, and never "not disclosed". Where a field departs from this,
  the row says so explicitly.
- **Amendable** — whether a researcher may amend the value. Marked `TBD (ENG-V3-011)` wherever
  the amendment/recomputation semantics are undecided, which is currently everywhere.

### The two universal rules

1. **`NULL` is not a negative.** A missing value means nothing was established. Deriving a
   negative assertion from absence is the failure mode that produced both integration blockers
   and the retired `hostile` boolean.
2. **Retired columns are not fields.** §4 lists columns that physically exist and are no longer
   written. A retained column is not a claim of continued authorship.

---

## 1. Source and classification

| Field | Definition | Type | Allowed values | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `source_status` | Stage-1 relevancy outcome | TEXT | `RELEVANT` / `NOT_RELEVANT` / … | — | EXTRACTED | Stage 2 `relevancy_filter` 0.8 | Reason codes are a **separate 24-value vocabulary** from `v2_event_type`, delivered inside the system prompt since 0.8 |
| `deal_type` | Legacy event label, retained as the column Stage 4 reads | TEXT | uppercase | not classified | EXTRACTED | Stage 3 | Raw model output; `v2_event_type` is the current dimension |
| `v2_event_type` | Current event type | TEXT | `ACQUISITION`, `MERGER`, `VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`, `SPIN_OFF`, `SPLIT_OFF`, `JOINT_VENTURE`, `RECAPITALIZATION`, … | not established | EXTRACTED | Stage 3 `deal_type_classifier` 0.13 | §T1 |
| `event_history_type` | Where the event sits in the deal's life | TEXT | `ANNOUNCED`, `CLOSED`, `RUMORED`, … | not established | EXTRACTED | Stage 3 | `RUMORED` is an **event-history value, not a transaction status** — see `ENG-V3-014` |
| `combination_structure` | How an acquisition is effected | TEXT | `MERGER`, `REVERSE_MERGER`, `DE_SPAC`, null | not established | EXTRACTED | Stage 3 | §T2. **Subordinate to** the event type; qualifies an ACQUISITION, never competes with it |
| `target_type` / `target_type_v2` | Structural nature of what is acquired | TEXT | `standalone_company`, `subsidiary`, `business_unit`, `assets` | not established | EXTRACTED | Stage 3 | §T3. `spinco` removed. Transaction **form** alone never determines this — "asset purchase" wording is not sufficient for `assets`. Legacy column holds raw output, `_v2` the normalized value |
| `target_status` | Target's public/private status **before** the transaction | TEXT | `PUBLIC`, `PRIVATE`, `SUBSIDIARY_OF_PUBLIC`, `SUBSIDIARY_OF_PRIVATE`, `UNKNOWN` | unknown | EXTRACTED | Stage 3 | **Pre-transaction only.** There is no post-transaction counterpart — that is what `is_going_private_outcome` supplies |
| `spin_split_type` / `_v2`, `distribution_mechanism`, `recap_type` | Spin/split and recap sub-dimensions | TEXT | see classifier | not established | EXTRACTED | Stage 3 | |
| `asset_type` | What kind of asset is transacted | TEXT | `REAL_ESTATE`, `INFRASTRUCTURE`, `ENERGY`, `NATURAL_RESOURCES`, `INTELLECTUAL_PROPERTY`, `DATA`, `FACILITY`, `EQUIPMENT`, `CONTRACTS_OR_RIGHTS`, `BRAND_OR_PRODUCT`, `OTHER` | not established | EXTRACTED | Stage 4 HC 0.24 | §T13. **Subordinate to `target_type = assets`**; null for every other target type, and a value supplied otherwise is dropped and logged. Answers what is transacted, not the target's sector |

**Scope note.** Standalone real-estate transactions are **in scope** (`ENG-V3-017`).

## 2. Parties, structure and features

| Field | Definition | Type | Allowed values | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `target_name` / `_domain` / `_ticker` / `_description` | Target identity | TEXT | — | not stated | EXTRACTED | Stage 4 | |
| `acquirer_name` / `_domain` / `_ticker` / `_description` | Acquirer identity | TEXT | — | not stated | EXTRACTED | Stage 4 | Ticker presence is **irrelevant** to take-private status |
| `acquirer_type` / `acquirer_type_v2` | Buyer classification | TEXT | `strategic_corporate`, `private_equity`, `pe_portfolio`, `venture_capital`, `growth_equity`, `sovereign_wealth_fund`, `pension_fund`, `hedge_fund`, `family_office`, `individual`, `management`, `employee_group`, `spac`, `other_financial_sponsor`, `unknown` | not established | EXTRACTED | Stage 4 | §T8. `other_financial_sponsor` is the **residual** for an affirmatively established financial-sponsor / private-capital buyer that fits no more specific sponsor type; it qualifies for the take-private buyer-side condition. **`consortium` is not a V3-PC-1.0 value** — see §4.1. A multi-buyer side is represented by the actual participants and their roles |
| `acquirer_sponsor_name` | The PE / private-capital sponsor **associated with** the acquirer | TEXT | — | no sponsor established | EXTRACTED | Stage 4 | Names a sponsor *behind* the buyer. Where the sponsors are themselves the buyers, this is expected null — they are participants in their own right, not a sponsor of someone else. Not gated on `acquirer_type` |
| `parent_seller_name` / `_ticker` / `_description` | Divesting parent | TEXT | — | not applicable or not stated | EXTRACTED | Stage 4 | Populated for subsidiary / business-unit / asset targets |
| `pct_acquired` | Percentage acquired in **this** transaction | REAL | 0–100 | **Ambiguous by design: null means 100% *or* unstated** | EXTRACTED | Stage 4 | Documented "Null if 100% or unstated"; the model is told not to emit 100. Distinguishes prior ownership, the stake acquired now, and resulting ownership. **Cannot establish a full acquisition** |
| `pct_acquired_source` | How the resolved percentage was obtained | TEXT | `stated`, `assumed`, null | not resolvable | DERIVED | Stage 9 | `assumed` = the §2.6 control-event default of 100, which fires on every silent control acquisition. **Not evidence of anything** |
| `stake_transition_type` | Explicit ownership transition | TEXT | `NEW_MINORITY_STAKE`, `NEW_MAJORITY_STAKE`, `FULL_ACQUISITION`, `MINORITY_ACQUIRING_MAJORITY`, `MAJORITY_ACQUIRE_REMAINING`, `MINORITY_ACQUIRING_REMAINING`, `MAJORITY_INCREASING_STAKE`, `MINORITY_INCREASING_STAKE` | not established | EXTRACTED | Stage 4 | Populated **only** on explicit prior/current/resulting ownership evidence; never inferred from `pct_acquired`. Empirically sparse |
| `offer_mechanism` | Whether the acquisition is effected by an offer direct to securityholders | TEXT | `TENDER_OFFER`, null | not established | EXTRACTED | Stage 4 | §T12. Vocabulary deliberately **not** expanded — `MANDATORY_OFFER`, `SCHEME_OF_ARRANGEMENT`, `ONE_STEP_MERGER`, `TWO_STEP_MERGER` are excluded by decision. A public target is not evidence of a tender offer; neither is a merger agreement |
| `sponsor_transaction_role` | How the transaction relates to a sponsor's platform | TEXT | `PLATFORM`, `ADD_ON`, null | neither established | EXTRACTED | Stage 4 | §T7. Never derived from `acquirer_type`. Orthogonal to `is_secondary_buyout` **and** to `is_take_private` — an ADD_ON can also be a take-private |
| `is_secondary_buyout` | Sponsor-to-sponsor transaction | INTEGER | 1 / 0 | — | DERIVED | Stage 9 | Explicit source flag, **or** side-qualified buyer+seller sponsor parties. The side-qualified branch is currently unreachable — see `ENG-V3-008` |
| `is_merger_of_equals` | Explicit merger-of-equals evidence | BOOLEAN | true / null | not established | EXTRACTED | Stage 4 | Explicit/qualified wording only |
| **`is_going_private_outcome`** | **The source affirmatively establishes that the transaction results in the target's equity ceasing to be publicly held or traded** | INTEGER | **`1` or `NULL` — never `0`** | **not established** | EXTRACTED | Stage 4 HC 0.24 | Affirmative-evidence-only. Explicit going-private/delisting language qualifies; those exact words are not required when explicit mechanics establish the same outcome. Never inferred from a PE buyer, a public target, a merger or tender structure, `pct_acquired`, or an assumed 100%. A model-emitted `false` is normalized to NULL before persistence and logged — a persisted `0` would mean the normalization was bypassed, not that a negative was observed |
| `is_take_private` | The transaction takes a public company private | INTEGER | 1 / 0 | — | DERIVED | Stage 9 | **Three required conditions:** `v2_event_type = ACQUISITION` **and** `target_status = PUBLIC` **and** `target_type = standalone_company`; **and** `acquirer_type ∈ {private_equity, pe_portfolio, management, employee_group, other_financial_sponsor}`; **and** `is_going_private_outcome` established. Acquirer ticker is irrelevant. A private strategic buyer does **not** qualify. A multi-buyer PE transaction is evaluated from its actual participating buyers and ownership structure. Absence of affirmative outcome evidence is 0, by decision |
| `is_minority` | Non-control investment | INTEGER | 1 / 0 | — | DERIVED | Stage 9 | |
| `deal_attitude` | Board posture | TEXT | `FRIENDLY`, `HOSTILE`, null | **not established — absence of hostile evidence is NOT `FRIENDLY`** | EXTRACTED | Stage 7 LC 0.10 | §T11. `FRIENDLY` requires positive support/recommendation/agreement evidence |
| `approach_type` | How the approach arose | TEXT | `SOLICITED`, `UNSOLICITED`, null | not established | EXTRACTED | Stage 7 | §T11. **Independent of `deal_attitude`**; neither value is inferred from the absence of the other. Unsolicited is neither hostile nor friendly |
| `competing_bid`, `regulatory_approvals_required`, `has_go_shop`, `go_shop_period_days` | Process facts | BOOLEAN / INTEGER | — | see note | EXTRACTED | Stage 7 | These are legacy two-state booleans authored `false` by the LC prompt; a `0` here is an **authored** negative, not an absent one |
| `target_fee_*`, `acquirer_fee_*` | Termination fees | REAL | — | not stated | EXTRACTED | Stage 7 | |

## 3. Value, financials and funding

### 3.1 Control-deal value

| Field | Definition | Type | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `value_amount` / `value_currency` / `value_type` | As-reported headline value | REAL / TEXT | not stated | EXTRACTED | Stage 4 | `value_type ∈ {EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, MARKET_CAPITALIZATION, UNDISCLOSED}`. **`UNDISCLOSED` is an affirmative value** — the source said terms were not disclosed |
| `per_share_price` | Per-share offer price | REAL | not stated | EXTRACTED | Stage 4 | |
| `equity_value` / `_basis` | Stake-level equity consideration | REAL / TEXT | not derivable | DERIVED | Stage 9 | Stake-level, **not** whole-company |
| `implied_equity_value` | Equity grossed up to 100% | REAL | not derivable | DERIVED | Stage 9 | `equity / (pct_acquired/100)` |
| `implied_enterprise_value` / `_basis` | 100%-basis enterprise value | REAL / TEXT | not derivable | DERIVED | Stage 9 | `STATED`, or `implied_equity_value + net_debt`. Both currencies must be known and equal or the sum is refused — no FX is attempted |
| `transaction_value` / `_basis`, `transaction_size` / `_basis`, `enterprise_value` / `_basis`, `investment_amount` | Tiered value outputs | REAL / TEXT | not derivable | DERIVED | Stage 9 | See `spec_transaction_value_model.md` |
| `net_debt`, `total_debt`, `cash_st`, `balance_sheet_as_of_date` (+ currencies) | Balance-sheet inputs | REAL / TEXT | not stated | EXTRACTED / manual | Stage 4 / manual interim | `net_debt` is computed from `total_debt − cash_st` only when both share one currency and one as-of date. The model is told **not** to compute net debt or enterprise value |
| `deal_value_currency` | Currency tag for derived values | TEXT | **currencies disagreed** | DERIVED | Stage 9 | Unanimity-or-null across `valuation_currency`, `value_currency`, `round_currency`. The null **is** the mismatch signal |

### 3.2 Target financials and multiples

| Field | Definition | Type | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `target_revenue`, `target_ebitda` (+ `_period_type`, `_period_type_v2`, `_period_end`), `financials_currency` | Stated target financials | REAL / TEXT | not stated | EXTRACTED | Stage 4 only | **Single-author fields.** The LC prompt has no target-financials block, so there is no recovery path if Stage 4 misses one. `ebitda_amount` accepts stated **or adjusted** EBITDA. Period basis is never assumed — "revenue of $50M" with no qualifier is `NULL` period type |
| `ev_to_revenue_ltm` / `_ntm`, `ev_to_ebitda_ltm` / `_ntm` | Valuation multiples | REAL | not calculable | DERIVED | Stage 9 | Require a whole-company `implied_enterprise_value`. TTM treated as LTM. Cross-currency pairs are NM without conversion |
| `multiple_quality` | Multiple calculability | TEXT | — | DERIVED | Stage 9 | `CALCULATED` / `NM` / `NOT_CALCULABLE`. **`NOT_CALCULABLE` conflates three causes** — no EV basis, a missing financial primitive, and an unusable period — and cannot distinguish them. Do not read it as evidence of restraint |
| `financials_disclosure_status` | Source-level disclosure classification | TEXT | — | EXTRACTED | Stage 4 / 4b | `DISCLOSED` = **at least one** financial value is stated — **not** that every term is known. `UNDISCLOSED` = the source explicitly said terms were not disclosed. `UNKNOWN` = the source is silent. Only `UNDISCLOSED` licenses non-disclosure language downstream |

### 3.3 Funding

Funding events (`VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`) derive **no transaction value at
all**: a round is primary capital into the company, so there is no purchase price. A null value
block on a funding event is **categorically inapplicable, not undisclosed**.

| Field | Definition | Type | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `round_label` | The source's own wording | TEXT | not stated | EXTRACTED | Stage 4b | e.g. "Series A Extension". Preferred when naming the round |
| `round` | Normalized round | TEXT | not derivable | DERIVED | Stage 9 | From `round_label` |
| `vc_stage` | Broad grouping | TEXT | not derivable | DERIVED | Stage 9 | From `round`, never from `round_label`. §T14 |
| `round_size` | Amount raised in **this** round | REAL | not stated | EXTRACTED | Stage 4b | The headline figure |
| `round_currency` | Currency of `round_size` and `facility_size` | TEXT | not stated | EXTRACTED | Stage 4b | Distinct from `value_currency` and `valuation_currency` (§R3) |
| `pre_money_valuation`, `post_money_valuation`, `valuation_currency` | What the company is **valued at** | REAL / TEXT | not stated | EXTRACTED | Stage 4b | Never the amount raised |
| `facility_size` | A **separate** facility or instrument alongside the round | REAL | not stated | EXTRACTED | Stage 4b | Debt, credit line, revolver. Never folded into or summed with `round_size` |
| `total_raised_to_date` | **Cumulative** across all rounds | REAL | not stated | EXTRACTED | Stage 4b | Never this round's size |
| `round_price_direction` | Up / down / flat round | TEXT | not established | EXTRACTED | Stage 4b | `UP` / `DOWN` / `FLAT` / null. §T14 replaced `is_down_round`, which could only ever record DOWN. **There is no `is_down_round` field** |
| `is_extension_round`, `is_bridge_round` | Round character | INTEGER | see note | EXTRACTED | Stage 4b | Declared plain `boolean` by the funding prompt, whose examples emit `false`, so **a stored `0` is an authored negative, not an absent one**. `true` licenses positive framing; `false` licenses **silence**, never an affirmative "this was not an extension". Both columns additionally carry `DEFAULT 0` in `003_funding_path.sql`, but Stage 9 writes them explicitly, so an unobserved value reaches canonical as NULL |
| `use_of_proceeds` | Stated use of proceeds | TEXT | not stated | EXTRACTED | Stage 4b | |
| `has_board_seat`, `board_seat_notes` | Investor board representation | INTEGER / TEXT | not established | EXTRACTED | Stage 4b | |

### 3.4 Consideration

| Field | Definition | Type | Null semantics | Origin | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| `consideration_components` | Typed consideration breakdown | JSON | not stated | EXTRACTED | Stage 7 | Forms include `CASH`, `ACQUIRER_STOCK`, `TARGET_STOCK`, `EARNOUT`, `CVR`, `CONTINGENT_CONSIDERATION`, `ASSUMED_DEBT` |
| `consideration_type` | Rolled-up structure | TEXT | not derivable | DERIVED | Stage 9 | `CASH` / `STOCK` / `CASH_AND_STOCK` / `OTHER` |
| `has_earnout`, `has_cvr` | Contingent-consideration presence | INTEGER | — | DERIVED | Stage 9 | From `consideration_components`. Replaced the retired `includes_earnout` boolean, which asserted "no earnout" on every deal |

## 4. Retired fields — columns physically retained, no longer written

**These are not fields.** Each column exists in the schema with its stored history intact and
is written by nothing. A retained column is not a claim of continued authorship; nothing is
backfilled and exports are untouched. Do not read any of them as current Product state, and do
not "clean them up" — the read tolerance is deliberate.

| Retired column | Replaced by | Retired at | Why |
| --- | --- | --- | --- |
| `hostile` | `deal_attitude` + `approach_type` | §T11 | Conflated posture, approach and proxy contest; false-by-default made "unstated" indistinguishable from "friendly" |
| `is_add_on` | `sponsor_transaction_role` | §T7 | Derived from `acquirer_type`, which is not evidence of a sponsor role |
| `is_platform_investment` | `sponsor_transaction_role` | §T7 | Accepted only explicit platform wording — the narrower half of what §T7 asks |
| `is_divestiture` | — (removed from V3) | §T4 | Derivable from `target_type` + `parent_seller` |
| `is_down_round` | `round_price_direction` | §T14 | Could only ever record DOWN; UP and FLAT were unrepresentable |
| `round_stage_category` | `vc_stage` | §T14 | Renamed and re-derived from normalized `round` |
| `is_de_spac` | `combination_structure = DE_SPAC` | §T2 | Merger family became one typed dimension |
| `includes_earnout` | `consideration_components` + `has_earnout` | S-F | Permanently false once Stage 7 stopped producing it — a flag asserting "no earnout" on every deal |

### 4.1 Vocabulary values not part of `V3-PC-1.0`

Enum values the prototype implementation still accepts that are **not** part of the target
model. Listed so a reader does not infer them from code, and so the residue is visible as
cleanup rather than as contract.

| Value | Where it still appears | Target model | Disposition |
| --- | --- | --- | --- |
| `acquirer_type = consortium` | `prompts/high_confidence_extraction.md:177,684` · `prompts/aggregation.md:112` · `stages/high_confidence_extract.py:64,112` | **Not a V3 acquirer type.** A consortium is not a company and is not an acquirer. The buy side is the actual participating firms and investors, with lead/primary designation where established | Retire with the participant/entity work (`ENG-V3-008`). Already non-qualifying for `is_take_private`, and that behaviour is pinned by test |
| `transaction_participant_group.group_type = CONSORTIUM` | `lib/participant_backfill.py:488-495` | Not target model — materializing a synthetic consortium structure damages the underlying company/participant representation | Retire with `ENG-V3-008` |

## 5. Entities, participants and rationale

| Field | Definition | Status | Notes |
| --- | --- | --- | --- |
| `entity.entity_type` | Entity classification | **Column exists, never written** (`ENG-V3-008`) | `schema/001_initial.sql:327`. `participant_backfill` inserts only id/kind/names/review status |
| `transaction_participant.participant_role` | Side-qualified party role | PARTIAL | Written: `TARGET`, `ACQUIRER`, `BUYER_PLATFORM`, `PARENT_SELLER`, `BUYER_SPONSOR`, `MERGER_SUB`, `PARENT_ACQUIRER`. **`SELLER_SPONSOR` is queried but written by nothing** |
| `transaction_participant` population | — | **Not populated during a pipeline run** (`ENG-V3-008`) | `participant_backfill` is invoked only by two standalone scripts and appears in no `run.py` stage list |
| `transaction_participant_group.group_type` | Party grouping | PARTIAL | `SELLER_GROUP`, `INVESTOR_GROUP`. The synthetic `CONSORTIUM` group and its "Acquirer consortium" label are prototype residue, not target model — see §4.1 and `ENG-V3-008` |
| `transaction_participant.is_lead` / `is_primary` | Lead / primary designation | Columns exist | The target model preserves the lead buyer or investor where the source establishes one. Currently unpopulated (`ENG-V3-008`) |
| `primary_rationale`, `secondary_rationales` | Strategic rationale | CURRENT, with tabled representation | `secondary_rationales` is a bare JSON array and cannot carry per-item basis (`ENG-V3-004`) |
| `rationale_basis` | Basis per rationale | **Not represented** (`ENG-V3-004`) | Semantics settled by §R8 |
| `supporting_excerpt_index` | Rationale evidence pointer | **Written but unresolvable** (`ENG-V3-005`) | Indexes a prompt-time excerpt list that is never persisted |
| Rationale **owner** | Whose rationale the value represents | **TABLED, unanswered** | Acquirer's, seller/target's, or the transaction's — see §R7+§R9+§S2.1 |

## 6. Provenance and record management

| Field | Definition | Type | Origin | Notes |
| --- | --- | --- | --- | --- |
| `transaction_id` | Cluster identity | TEXT | DERIVED | Stage 8 clustering |
| `is_current` | Current-version flag | INTEGER | DERIVED | Older versions flip to 0 on re-aggregation |
| `aggregation_version`, `updated_at` | Aggregation provenance | TEXT | DERIVED | |
| `transaction_field_observation` | The observation ledger | table | — | Every extracted field lands here first. `DEFAULT_AGGREGATION_READ_SOURCE = "observation"`. **A NULL staging value writes no observation row at all** — "not established" is the absence of a row, not a stored null |
| `source_raw.content_hash` | Source dedup | TEXT | — | Not a supersession key — see `ENG-V3-001` |

**A source is not a transaction** (§R5). One source may yield N transactions; one transaction
may draw on N sources. Both directions were exercised in the 29-source integration run.

## 7. What this dictionary does not settle

Carried so nothing is mistaken for decided: derived-source tier for digest decomposition
(`ENG-V3-009`) · entity/domain linking (`ENG-V3-010`) · researcher amendment and recomputation
semantics (`ENG-V3-011`, which is why every **Amendable** answer is `TBD`) · SEC/source tiering
(`ENG-V3-013`) · rumour intake (`ENG-V3-014`) · QIP treatment (`ENG-V3-015`) · canonical
casing/read-tolerance cleanup (`ENG-V3-012`) · Strategic Rationale representation and owner
(§R7+§R9+§S2.1).
