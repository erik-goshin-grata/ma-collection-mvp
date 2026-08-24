# Transactions V3 — Change & Decision Register

**Product Contract:** Transactions V3 — `V3-PC-1.0`
**Status:** CURRENT · **Reconciled:** 2026-08-22 · **Supersedes:** — (initial release)
**MVP reference baseline:** `ma-collection-mvp` `origin/main` @ `2e2ccb7` · **Schema:** `010_v3_take_private_outcome.sql`
**Package:** Release Manifest · Change & Decision Register · Data Dictionary · Engineering Handoff

*One row per Product decision. The Jira-facing navigation surface.*

---

## How to read this

One row per **meaningful Product decision**, not per field, commit or prompt edit. Related
field changes are grouped into the decision they implement — the take-private work is one
row, not four.

> **MVP vs Engineering.** `V3-PC-1.0` is the Product contract for the **target** Transactions
> V3 model. `ma-collection-mvp` is the Product/MVP reference implementation used to develop
> and validate that contract, and is **separate from the Engineering production
> implementation**. Every status, reference, prompt version and piece of validation evidence
> in this register describes the **MVP** unless a row explicitly states otherwise. Nothing
> here asserts what Engineering has or has not implemented.

This register is navigation. It does not restate the field contract (see the Data Dictionary)
and it does not replace the decision history (see `docs/decisions.md` and
`docs/grata_v2_inventory_and_recommendations.md` §T/§R/§A/§P, which remain the authority for
*why*).

### Controlled vocabularies

**Product status** — `CURRENT` (settled and in force) · `SUPERSEDED` (replaced by a later
decision) · `TABLED` (Product decided **not to decide now**) · `OPEN` (raised, never
adjudicated). `TABLED` and `OPEN` are intentionally distinct: tabled work has a Product
position, open work does not.

**MVP Reference Status** — the state of the **`ma-collection-mvp` reference implementation**,
never a statement about Engineering's implementation:
`IMPLEMENTED` · `PARTIAL` · `NOT IMPLEMENTED` · `N/A`

`IMPLEMENTED` means implemented **in the MVP reference** unless a row says otherwise. It is
evidence that the Product contract is expressible and behaves as specified — it is not a claim
that Engineering has built it, and it does not imply Engineering must build it the same way.

**Engineering Handoff / Consideration** — what Engineering should know or align to for this
decision. It describes an *ask or consideration*, never Engineering's current state, and it
does not decompose delivery work.

**Validation status** — `TESTED` (MVP deterministic suite) · `INTEGRATION TESTED` (29-source
PL run in the MVP) · `MANUAL VALIDATED` (Collection-team review) · `NOT YET VALIDATED`.
Combinable with `+`. All validation evidence is MVP evidence. A validation status is never
asserted without a pointer in the Evidence column.

### Views

**Engineering Handoff View** — decisions whose target the MVP does not yet fully demonstrate,
so Engineering has something to resolve or align on:

> `Product Status = CURRENT` **AND** `MVP Reference Status ∈ {PARTIAL, NOT IMPLEMENTED}`

Rows at `IMPLEMENTED` still carry adoption considerations — the MVP demonstrates the contract,
it does not dictate Engineering's design. Read those rows for semantics, not for a build list.

**Product-decision view — not Engineering work:**

> `Product Status ∈ {OPEN, TABLED}`

These are deliberately separate. Tabled and open items must stay visible so they do not vanish,
but they must never read as actionable: an `OPEN` item has no Product position to build
against, and a `TABLED` one is parked on purpose.

**This register does not decompose work.** Engineering owns that. A row is a Product decision,
not a unit of delivery. The `Jira` column is a blank mapping slot for whatever decomposition
Engineering chooses.

### Identifiers

Existing identifiers are reused wherever one fits (`§T7`, `§R8`, `§A6.3`, `S-C`) and are
**never renumbered**. `ENG-V3-###` is minted only where no suitable identifier exists. Jira
issues are titled with the durable ID so it survives independently of the Jira key.

---

## A. Settled Product semantics — implemented in the MVP reference

| ID | Area | Decision / change | Previous state | Current Product state | Product Status | MVP Reference Status | Engineering Handoff / Consideration | Contract | MVP Implementation Refs | Validation | Evidence | Data Dictionary | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| §T1 | Event model | `event_category` replaced by `v2_event_type` | Grata `event_category` | Single event-type dimension | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `deal_type_classifier` 0.13 | TESTED | `test_aggregation_vocabulary_parity.py` | Transaction Detail | inventory §T1 | |
| §T2 | Event model | Merger family moved to `combination_structure`, subordinate to acquisition/event semantics | `MERGER` / `REVERSE_MERGER` as event types | `combination_structure` qualifies an ACQUISITION; it does not compete with it | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `005_v3_combination_structure.sql`; classifier 0.13 | TESTED | `test_combination_structure.py` | Transaction Detail | inventory §T2 | |
| §T3 | Target model | `target_type` is structural; `spinco` removed | `spinco` a target type; asset wording drove type | `standalone_company` / `subsidiary` / `business_unit` / `assets`; transaction form alone never determines it | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | classifier 0.13 (S-C Gate 2) | TESTED + INTEGRATION TESTED | `test_asset_type.py`; PL run | Transaction Detail | inventory §T3 | |
| §T4 | Flags | `is_divestiture` removed from V3 | Derived divestiture flag | Not a V3 field; column retained, unwritten | SUPERSEDED | N/A | — | V3-PC-1.0 | — | N/A | — | Appendix | inventory §T4 | |
| §T5/§T6 | Parties | Party and advisor role vocabularies; participants are the representation of the buy and sell sides | Grata flat roles | Side-qualified roles with participant groups. **The target model represents the actual companies, investors and participants, with lead/primary designation where the source establishes it. It does not materialize a synthetic consortium company or acquirer** — doing so damages the underlying company/participant structure. A multi-buyer transaction, PE or otherwise, is read from its actual participating buyers and ownership structure | CURRENT | PARTIAL | see `ENG-V3-008` | V3-PC-1.0 | `lib/participant_backfill.py`; `schema/001_initial.sql:410-411` (`is_primary`, `is_lead`) | TESTED | loader case in `test_take_private_derivation.py` | Parties & Participants | inventory §T5–T6 | |
| §T7 | Sponsor | `sponsor_transaction_role = PLATFORM \| ADD_ON \| null`; `is_add_on` and `is_platform_investment` authorship retired | Two booleans, one derived from `acquirer_type` | Extracted, never derived from buyer type; orthogonal to `is_secondary_buyout` | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `009_v3_sponsor_transaction_role.sql`; HC 0.24; `deal_summary` 0.16 | TESTED + INTEGRATION TESTED | `test_sponsor_transaction_role.py`; PL run | Transaction Detail; appendix | inventory §T7 | |
| §T8 | Parties | `acquirer_type` vocabulary lowercased and expanded | Mixed-case, narrower set | 15 target-model values. `other_financial_sponsor` is the residual for an **affirmatively established** financial-sponsor / private-capital buyer that fits no more specific sponsor type; it qualifies for the take-private buyer-side condition. `consortium` is **not part of V3-PC-1.0** — prototype residue still accepted by code, retired under `ENG-V3-008` (Data Dictionary appendix) | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | HC 0.24 | TESTED | `test_aggregation_vocabulary_parity.py` | Parties & Participants | inventory §T8 | |
| §T11 | Attitude | `hostile` split into independent `deal_attitude` and `approach_type` | One fused boolean, false-by-default | Two nullable dimensions; absence of hostile evidence is **not** FRIENDLY | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `004_v3_attitude_approach.sql`; LC 0.10; `deal_summary` 0.16 | TESTED + INTEGRATION TESTED | `test_summary_attitude_transport.py`; PL run | Transaction Detail; appendix | inventory §T11 | |
| §T12 | Structure | `offer_mechanism = TENDER_OFFER \| null` from ordinary sources | Only on the SEC/agreement path | Extracted from any source; a public target is not evidence of a tender offer | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `007_v3_offer_mechanism.sql`; HC 0.24 | TESTED | `test_offer_mechanism.py` | Transaction Detail | inventory §T12 | |
| §T13 | Assets | `asset_type`, subordinate to `target_type = assets` | No asset typing | 11 values; null for every other target type | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `006_v3_asset_type.sql`; HC 0.24 | TESTED | `test_asset_type.py` | Transaction Detail | inventory §T13 | |
| §T14 | Funding | `round` / `vc_stage` / `round_price_direction`; `round_stage_category` and `is_down_round` retired | `round_stage_category`; DOWN-only boolean | `round_label` verbatim, `round` normalized, `vc_stage` derived, direction `UP\|DOWN\|FLAT\|null` | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `008_v3_funding_round.sql`; funding HC 0.3 | TESTED + INTEGRATION TESTED | `test_funding_*`; PL run | Funding Detail; appendix | inventory §T14 | |
| §A6.3 | Funding | Funding magnitude / value / valuation semantics | Round size conflated with equity value | Funding events derive **no** transaction value; round size, facility, valuation and cumulative total are distinct | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `stages/aggregate.py`; `deal_summary` 0.16 | TESTED + INTEGRATION TESTED | `test_summary_attitude_transport.py` funding anchors | Funding Detail | `funding_path_design.md` | |
| S-F | Consideration | Contingent consideration typed; `includes_earnout` boolean retired | Boolean asserting "no earnout" on every deal | `consideration_components` distinguishes `EARNOUT` / `CVR` / `CONTINGENT_CONSIDERATION`; `has_earnout`/`has_cvr` derived | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | LC 0.10; `deal_summary` 0.16 | TESTED | `test_typed_value_preservation.py` | Consideration; appendix | `v3_slice_reconciliation.md` §2 | |
| §T9/§T10 | Value | Transaction / equity / enterprise value semantics | Single conflated value | Stake-level equity vs 100%-basis EV; `transaction_value_basis`; no silent currency mixing | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `stages/aggregate.py` | TESTED + MANUAL VALIDATED | `test_equity_value_scope.py`; ~300-txn review | Transaction Value & Financials | `spec_transaction_value_model.md` | |

## B. New in this cycle — S-G, S-H and the PL integration remediations

| ID | Area | Decision / change | Previous state | Current Product state | Product Status | MVP Reference Status | Engineering Handoff / Consideration | Contract | MVP Implementation Refs | Validation | Evidence | Data Dictionary | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ENG-V3-020 | Take-private | `is_take_private` requires **three** conditions: public standalone target **+** qualifying private-ownership buyer **+** affirmative going-private outcome | Public standalone target + broad acquirer type, with an acquirer-ticker guard standing in for "buyer is private" | Qualifying buyers: `private_equity`, `pe_portfolio`, `management`, `employee_group`, `other_financial_sponsor`. Ticker presence is **irrelevant**. Outcome supplied by the extracted `is_going_private_outcome` (`true \| null`), never inferred. A private strategic buyer does **not** qualify merely because the target ceases to be public. A multi-buyer PE transaction is evaluated from its actual participating buyers and ownership structure, not from a consortium value | CURRENT | IMPLEMENTED | none (consortium split out) | V3-PC-1.0 | `010_v3_take_private_outcome.sql`; HC 0.24; `stages/aggregate.py::_derive_is_take_private` | TESTED + INTEGRATION TESTED | `test_take_private_derivation.py` (33 unit + 8 production-path + 3 normalization); 0/26 on the PL corpus | Transaction Detail; derived fields | `v3_slice_reconciliation.md` §7 | |
| ENG-V3-021 | Deal Summary | Canonical funding facts reach Deal Summary; non-disclosure language requires an affirmative signal | 14 funding fields fetched and dropped; a null value block read as UNDISCLOSED, producing false "Financial terms were not disclosed" on 4 of 7 funding transactions | `FUNDING` block carries all 14 fields uncoerced; `FINANCIALS DISCLOSURE` carries `financials_disclosure_status` for every deal type. Canonical NULL = not established, never non-disclosure. Only `UNDISCLOSED` licenses the claim; `DISCLOSED` means *at least one* value stated, not completeness | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `deal_summary` 0.16; `stages/summarize.py` | TESTED + INTEGRATION TESTED | `test_summary_attitude_transport.py` (4 live anchors + null-preservation + 2 controls) | Funding Detail; Summary & Rationale | this register | |
| S-H | Relevancy | The authoritative 24-code `reason_code` vocabulary is delivered to the model | Vocabulary lived outside the §4/§5 fences and was never sent; a parity test passed on 24==24 while the model saw none of it | Vocabulary inside the delivered system prompt; prompt-contract tests must read `load_prompt_file(...)`, not the Markdown | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `relevancy_filter` 0.8 | TESTED + INTEGRATION TESTED | `test_reason_code_parity.py`; PL 745/746 run | — | `v3_slice_reconciliation.md` §11 | |
| S-G | Provenance | Prompt provenance is caller-owned | The model was asked to echo `prompt_version`, so a version that never ran could be recorded | The stage passes the authoritative version to `call_prompt` and stamps it | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | all 14 prompts; `prompts/prompt_conventions.md` 0.5 | TESTED | `test_prompt_provenance_caller_owned.py`, `test_prompt_stage_version_parity.py` | — | `prompt_conventions.md` 0.5 | |

## C. Reconciliation decisions R1–R10

| ID | Area | Decision / change | Previous state | Current Product state | Product Status | MVP Reference Status | Engineering Handoff / Consideration | Contract | MVP Implementation Refs | Validation | Evidence | Data Dictionary | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| §R1 | Reconciliation | Precedence-based reconciliation and supersession | Ad hoc | Settled semantics; the key is not single-valued — a filed document is immutable, a web source can change under the same URL | CURRENT | NOT IMPLEMENTED | `ENG-V3-001` | V3-PC-1.0 | `is_current` / `content_hash` exist; no supersession key | NOT YET VALIDATED | — | Provenance & Operational | inventory §R1 | |
| §R2 | Currency | Derived USD normalization is optional, never destructive | Implicit conversion risk | Tag-and-defer; no FX applied without a date the pipeline does not carry | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `stages/aggregate.py` currency unanimity rule | TESTED | `test_currency_*` | Transaction Value & Financials | inventory §R2 | |
| §R3 | Currency | Three distinct currency concepts | One currency field | `value_currency` (control deals) · `valuation_currency` (post-money) · `round_currency` (round amounts); unanimity-or-null for derived values | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `stages/aggregate.py` | TESTED | `test_currency_normalization` | Transaction Value & Financials | inventory §R3 | |
| §R4 | Financials | Exact period coherence required for multiples | Loose period matching | LTM/TTM interchangeable; a recent ANNUAL actual may fill the LTM slot only when date-aligned; cross-currency pairs are NM | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `stages/aggregate.py::_compute_multiples` | TESTED | `test_multiples_*` | Transaction Value & Financials | inventory §R4 | |
| §R5 | Provenance | A source is not a transaction | Source/transaction conflated | One source may yield N transactions; one transaction may draw on N sources | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `transactions[]` envelope; entity clustering | TESTED + INTEGRATION TESTED | PL run: 29→31→26; INVL and MPS decomposition | Provenance & Operational | inventory §R5 | |
| §R6 | Architecture | Nine invariants the physical model must satisfy | — | Invariants settled; Silver/Gold placement is ENG's call | CURRENT | N/A | `ENG-V3-002` | V3-PC-1.0 | — | NOT YET VALIDATED | — | Provenance & Operational | inventory §R6 | |
| §R7 + §R9 + §S2.1 | Rationale | Strategic Rationale representation and the structure-derived defaults | Three defaults infer rationale from deal structure; `acquirer_type` unavailable to the prompt | **Parked as one item.** Product does not approve inventing a sentinel or routing no-rationale cases to OTHER. The rationale-**owner** question (acquirer's / seller's / the transaction's) is unanswered | TABLED | NOT IMPLEMENTED | `ENG-V3-006` | V3-PC-1.0 | `prompts/strategic_rationale.md` 0.6 lines 111/113/115 — all three defaults still live | NOT YET VALIDATED | — | Summary & Rationale | `v3_slice_reconciliation.md` §10 | |
| §R8 | Rationale | `rationale_basis` is carried per rationale — primary and every secondary | Not represented | `secondary_rationales` is a bare JSON array today and cannot carry per-item attribution | CURRENT | NOT IMPLEMENTED | `ENG-V3-004` | V3-PC-1.0 | `stages/rationale_tag.py:198` | NOT YET VALIDATED | — | Summary & Rationale | inventory §R8 | |
| §R10 | Summary | The generated summary is not authoritative | Ambiguous | Narrative derived from canonical fields; never a source of truth | CURRENT | IMPLEMENTED | — | V3-PC-1.0 | `deal_summary` 0.16 | TESTED | `test_summary_attitude_transport.py` | Summary & Rationale | inventory §R10 | |

## D. Target requirements the MVP does not yet fully demonstrate

Every row below was re-verified against the MVP working tree at this baseline. `MVP Reference
Status` describes **`ma-collection-mvp` only**; the Engineering Handoff column states the ask
or consideration, never Engineering's current state.

| ID | Area | Decision / change | Previous state | Current Product state | Product Status | MVP Reference Status | Engineering Handoff / Consideration | Contract | MVP Implementation Refs | Validation | Evidence | Data Dictionary | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ENG-V3-001 | Reconciliation | Supersession / reconciliation key | `is_current` flag and `content_hash` only | Semantics settled by §R1; key design outstanding | CURRENT | NOT IMPLEMENTED | design + implement | V3-PC-1.0 | `schema/001_initial.sql:28,293` | NOT YET VALIDATED | verified present at baseline | Provenance & Operational | inventory §R1 | |
| ENG-V3-002 | Architecture | Silver/Gold physical placement meeting the §R6 invariants | — | Product does not prescribe the layer | CURRENT | N/A | ENG decision | V3-PC-1.0 | — | NOT YET VALIDATED | — | Provenance & Operational | inventory §R6 | |
| ENG-V3-003 | Value | `PER_SHARE_X_SHARES` share-count wiring | Rung disconnected | `stages/agreement_extract.py` writes `transaction_security.shares_outstanding`; `stages/aggregate.py:1990` still hard-codes `sec_shares = None` | CURRENT | PARTIAL | connect the rung | V3-PC-1.0 | `aggregate.py:1986-1990, 828-881` | NOT YET VALIDATED | **re-verified: still `sec_shares = None`**; live population unverified; coverage limited to agreement-bearing deals | Transaction Value & Financials | handoff §6.3 | |
| ENG-V3-004 | Rationale | `rationale_basis` schema and placement | Not representable | Basis recoverable per rationale; structure is ENG's call | CURRENT | NOT IMPLEMENTED | schema design | V3-PC-1.0 | `stages/rationale_tag.py:198` | NOT YET VALIDATED | **re-verified: `secondary_rationales` still a bare JSON array** | Summary & Rationale | inventory §R8 | |
| ENG-V3-005 | Rationale | Durable rationale evidence attribution | `supporting_excerpt_index` indexes a prompt-time list that is never persisted | A replacement, not a removal — the value is not derivable from anything stored | CURRENT | PARTIAL | capture at classification time | V3-PC-1.0 | `stages/rationale_tag.py:31,199,207` | NOT YET VALIDATED | **re-verified: still written, still unresolvable** | Summary & Rationale | handoff §6.5 | |
| ENG-V3-006 | Rationale | Retire the three structure-derived rationale defaults | Three defaults live | Blocked on the §R7/§R9/§S2.1 Product decision — **not schedulable** | TABLED | NOT IMPLEMENTED | await Product | V3-PC-1.0 | `prompts/strategic_rationale.md` 0.6 lines 111, 113, 115 | NOT YET VALIDATED | **re-verified: all three defaults present**; changing them moves the `eval/score.py` gold set | Summary & Rationale | `v3_slice_reconciliation.md` §10 | |
| ENG-V3-007 | Parties | ~~PE/sponsor consortium representation~~ — **withdrawn; never a target-model gap** | A proposal to establish PE/sponsor character for `acquirer_type = consortium` | Product and Engineering are already aligned that the target model does not use a consortium construct, so there is nothing to design and no gap to close. Retained only so the published identifier does not dangle. The target principle lives in `§T5/§T6`; the MVP residue is `ENG-V3-008` | SUPERSEDED | N/A | none | V3-PC-1.0 | — | N/A | — | Parties & Participants | inventory §T5–T6 | |
| ENG-V3-008 | Parties | Participant / entity representation | Grata flat roles | **Target requirement:** preserve the actual participant entities and their roles, including lead/primary designation where the source establishes it; do **not** materialize a synthetic consortium entity or structure. Product and Engineering are aligned on this principle. **MVP reference state:** partial — `entity.entity_type` exists in schema but is never written; participants are all written as the undifferentiated role `ACQUIRER`; `SELLER_SPONSOR` is queried by `is_secondary_buyout` but written by nothing; `participant_backfill` is absent from every `run.py` stage list, so `transaction_participant` is empty during a pipeline run. **MVP/prototype residue that must not be propagated:** the `consortium` value in the extraction and aggregation vocabularies and the parser allowlist, the synthetic `CONSORTIUM` group and its "Acquirer consortium" label, and a stale explanatory comment in `aggregate.py`. `is_lead` / `is_primary` columns already exist to carry the lead | CURRENT | PARTIAL | Align on the target participant/entity representation. **Do not adopt the MVP's consortium residue** — it is prototype leftover, not a model to port | V3-PC-1.0 | `schema/001_initial.sql:327,410-411`; `lib/participant_backfill.py:31-34,488-517,634`; `stages/aggregate.py:176-185,1544`; `prompts/high_confidence_extraction.md:177,684`; `prompts/aggregation.md:112`; `stages/high_confidence_extract.py:64,112` | TESTED | MVP: re-verified at this baseline; `consortium` pinned non-qualifying in `test_take_private_derivation.py` | Parties & Participants; appendix | inventory §T5–T6 | |
| ENG-V3-025 | Deal Summary | The take-private framing rule is **not delivered to the model** | Assumed to govern summary behaviour | `prompts/deal_summary.md` §3 carries "**Derived / Take-Private flag:** … describe the transaction as a take-private … Do not infer take-private framing solely from a public target if the flag is false". §3 sits **outside the §4/§5 fences**, so `load_prompt_file` never sends it. The delivered system prompt mentions take-private only in one VALUE FRAMING line. Pre-existing; not introduced by 0.16. The same §3 text also still describes the **pre-fix** derivation, saying the flag "includes private strategic buyers … and private consortiums", which is no longer true | CURRENT | PARTIAL | move the rule inside §4 and correct the stale description; **prompt change, deliberately not made in this documentation pass** | V3-PC-1.0 | `prompts/deal_summary.md:145-149` (§3, lines 34–153) | NOT YET VALIDATED | verified via `load_prompt_file` at this baseline | Summary & Rationale | this register | |

## E. Open — raised, never adjudicated

Product-decision view only. **Not Engineering work**: there is no Product position to build
against.

| ID | Area | Question | Current state | Product Status | MVP Reference Status | Engineering Handoff / Consideration | Contract | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ENG-V3-009 | Provenance | Derived-source tier for digest/roundup decomposition | Settled: a multi-event digest may be decomposed into independently processable source items; derived items retain provenance to the original and carry **lower authority**. **The exact tier is unresolved.** | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | this register | |
| ENG-V3-010 | Entities | Entity / domain linking | Raised; no Product position | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | handoff §7 | |
| ENG-V3-011 | Workflow | Researcher amendment and recomputation semantics | What a researcher may amend, and what re-aggregation may overwrite, is undefined | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | inventory §P | |
| ENG-V3-013 | Sources | SEC / source tiering and reconciliation | Raised; no Product position | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | handoff §7 | |
| ENG-V3-014 | Intake | Rumor intake vs the `RUMORED` event-history path | `RUMORED` is an **event-history value**, not a primary transaction status. Whether rumour-stage sources should be admitted at intake is a separate, unanswered question. The existing Stage-1 rumour rule is unchanged pending an end-to-end trace | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | this register | |
| ENG-V3-015 | Scope | QIP treatment | An Indian QIP has **not** been established as a PIPE. Mechanics were to be compared against the settled PIPE definition; that comparison is not recorded anywhere | OPEN | NOT IMPLEMENTED | await Product | V3-PC-1.0 | this register | |

## F. Tabled — Product decided not to decide now

| ID | Area | Item | Current state | Product Status | MVP Reference Status | Contract | Detail | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| §R7+§R9+§S2.1 | Rationale | Strategic Rationale representation, the three structure-derived defaults, and the rationale-**owner** question | Parked as one item. Product does not approve inventing a sentinel or routing no-rationale cases to OTHER merely to close the cleanup | TABLED | NOT IMPLEMENTED | V3-PC-1.0 | `v3_slice_reconciliation.md` §10 | |
| ENG-V3-016 | Rationale | Rationale evidence excerpts | Consideration only: retaining concise source excerpts per classified rationale for researcher review and provenance, including whose rationale the language represents. **No storage or extraction design is approved** | TABLED | NOT IMPLEMENTED | V3-PC-1.0 | this register | |
| ENG-V3-012 | Canonical form | Canonical casing / read-tolerance cleanup | Stage 3 and Stage 4 write raw model output to the legacy column and normalized output to `_v2`; derivations read the legacy column and case-fold locally. Read tolerance for stored uppercase rows must not be broken | TABLED | NOT IMPLEMENTED | V3-PC-1.0 | `v3_slice_reconciliation.md` §8 | |

## G. Scope rulings and standing findings

| ID | Area | Ruling / finding | Product Status | MVP Reference Status | Contract | Evidence | Jira |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ENG-V3-017 | Scope | **Standalone real-estate transactions are IN SCOPE.** Not previously stated either way in the documentation | CURRENT | IMPLEMENTED | V3-PC-1.0 | PL Stage-1 review: reclassified two false negatives; corpus-wide impact ~1–2 misses, not a systemic class | |
| ENG-V3-018 | Scope | **PIPE — two layers, deliberately different.** *Product semantics:* a transaction is a PIPE when the source facts establish the structure; the literal word "PIPE" is not inherently required. *Pipeline behaviour and safety:* recognition is **terminal** — recognize/tag PIPE → terminate profiling → **no HC, no LC, no downstream transaction profiling**. Because a false positive suppresses an otherwise processable transaction, the recognizer may remain deliberately conservative. **That narrower threshold is an implementation safety choice, not the Product definition of PIPE.** QIP is **not** settled by this row → `ENG-V3-015` | CURRENT | **IMPLEMENTED** | V3-PC-1.0 | verified at this baseline: Stage 3 sets `RECOGNIZED_NOT_PROFILED` (`deal_type_classify.py:9,12`); Stage 4 and Stage 4b gate on `status = 'CLASSIFIED'` (`:306`, `:107`); Stage 7 gates on `status IN ('HC_EXTRACTED','SEC_NOT_TRIGGERED','SEC_ENRICHED')` (`:152`) — a recognized PIPE reaches none of them; `test_pipe_recognition.py` | |
| ENG-V3-019 | Workflow | **Stage-1 model confidence is not suitable for workflow gating**, on the evidence of the PL Relevancy 0.8 run | CURRENT | N/A | V3-PC-1.0 | PL 745/746 run | |
| ENG-V3-022 | Validation | Partial same-sentence HC extraction miss — revenue captured, adjusted EBITDA missed from the same sentence. Contract, block entry and period logic all verified working; a single-metric omission | CURRENT | N/A | V3-PC-1.0 | PL integration run; held for quantification in the larger validation corpora. **Not a contract defect** | |
| ENG-V3-023 | Cleanup | Five accepted trust-audit limitations: agreement family has no model-tier row · surplus legacy `.format()` kwargs · unused `_VALID_LEGACY_EVENT_TYPES` · stage numbering disagrees across layers · one empty fenced JSON block | CURRENT | NOT IMPLEMENTED | V3-PC-1.0 | `v3_slice_reconciliation.md` §11 | |

---

## Jira mapping

The `Jira` column is left blank by design. Engineering owns decomposition; this register owns
the Product decisions and their current status.

Two things are worth preserving through whatever decomposition is chosen:

1. **The durable ID.** Carrying `ENG-V3-###`, `§T…`, `§R…` or the slice ID into the Jira
   summary keeps the link from ticket back to Product decision after this register is
   re-reconciled.
2. **The two views stay distinct.** `OPEN` and `TABLED` rows should not be sized or scheduled
   alongside `CURRENT` work — one has no Product position, the other is parked deliberately.

The next reconciliation increments to `V3-PC-1.1` (statuses move) or `V3-PC-2.0` (a settled
decision is reversed), filling in `Supersedes:`.
