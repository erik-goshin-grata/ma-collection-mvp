# V3 Slices S-A – S-F — Implementation and Validation Reconciliation

**Date:** 2026-08-20 · **Updated:** 2026-08-22 for `V3-PC-1.0`
**Product Contract:** Transactions V3 — `V3-PC-1.0` (this document is **historical detail**, not the contract)

One page covering what shipped, what was validated, what is known-broken, and what is
deliberately parked. Written after six vertical slices and five real-text validation runs;
updated at `V3-PC-1.0` to cover S-G and S-H and to record the two integration remediations.

> **Current state lives in the `V3-PC-1.0` package** — `docs/v3_release_manifest.md`,
> `docs/v3_change_decision_register.md`, `docs/v3_data_dictionary.md`. This document remains
> the slice-level record of how the work was validated. Where it disagrees with the Register
> on current state, the Register wins.

---

## 1. Two gates, and why the distinction matters

**Gate 1 — deterministic structural regression.** Proves the field survives the production
path. For any new or changed extracted canonical field the chain is:

```
extraction/staging → production observation writer → observation ledger
                   → configured aggregation read source → canonical transaction_record
```

Four requirements, all of which exist because something failed without them:

1. **A demonstrated pre-fix failure.** The test must fail against the commit before the change.
2. **An unchanged neighbouring control** carried through the same path, so a change that
   empties or restructures the path is caught.
3. **Production field groups and production `include_*` flags** — never a list duplicated into
   the test. S-A added two fields to `_FIELDS` but not to `LC_SCALAR_FIELDS`; the canonical
   columns would have been NULL forever, and no prompt or parser test would have noticed.
4. **The configured aggregation read source** (`DEFAULT_AGGREGATION_READ_SOURCE = "observation"`),
   not a hand-built alternative.

**Derived fields are tested through their real architecture.** A field Stage 9 computes is
tested by driving Stage 9, not by manufacturing observations for a field nothing observes.

**Gate 2 — representative real-text boundary validation.** Product-supplied source text through
the shipped prompts and stages. It is *representative*, not exhaustive: one source per
deterministic assertion is not the standard, and a case earns its place by sitting on a boundary
where a specific, predictable failure was available. Provenance is recorded per case —
**independent** (appears in no prompt), **partial** (an excerpt appears), **smoke** (the source
is a worked example). Only independent cases are evidence.

**Implementation status ≠ Product status.** A slice can be structurally complete and still
unvalidated (S-E, S-F), or validated and still carrying an open defect (S-A).

---

## 2. Slice status

| Slice | Field(s) | Prompt | Owning stage | Observation group | Gate 1 | Gate 2 |
| --- | --- | --- | --- | --- | --- | --- |
| S-A | `deal_attitude`, `approach_type` | `low_confidence_extraction:0.8` | 7 | `LC_SCALAR_FIELDS` | ✅ | ⚠️ **PARTIAL / OPEN** |
| S-B | `combination_structure` | `deal_type_classifier:0.11` | 3 | `STAGE3_FIELDS` | ✅ | ✅ **8/8** |
| S-C | `target_type`, `asset_type` | `deal_type_classifier:0.11`, `high_confidence_extraction:0.20` | 3, 4 | `STAGE3_FIELDS`, `HC_FIELDS` | ✅ | ✅ **7/7** |
| S-D | `offer_mechanism` | `high_confidence_extraction:0.20` | 4 (11 corroborates) | `HC_FIELDS` | ✅ | ✅ **4/4** |
| S-E | `round`, `vc_stage`, `round_price_direction` | `funding_hc_extraction:0.2` | 4b, 9 derives | `FUNDING_FIELDS` | ✅ | ⏳ pending real text |
| S-F | consideration forms / `CONTINGENT_CONSIDERATION` | `low_confidence_extraction:0.8` | 7 | LC path | ✅ | ⏳ pending real text |

`STAGE3_FIELDS` is written when **Stage 4** runs with `include_stage3=True` — Stage 3 writes no
observations of its own. Omitting a classifier field from that group strands it on staging.

---

### S-G and S-H — added at `V3-PC-1.0`

| Slice | Subject | Status | Evidence |
| --- | --- | --- | --- |
| **S-G** | Prompt provenance is caller-owned; `sponsor_transaction_role` replaces the `is_add_on` / `is_platform_investment` pair (§T7) | SHIPPED | `test_prompt_provenance_caller_owned.py`, `test_prompt_stage_version_parity.py`, `test_sponsor_transaction_role.py` |
| **S-H** | The 24-code relevancy `reason_code` vocabulary is delivered inside the §4 system prompt | SHIPPED | `test_reason_code_parity.py`; PL Relevancy 0.8 run, 745 of 746 unique sources classified |

### Post-slice remediations — `V3-PC-1.0`

| Item | Subject | Status | Evidence |
| --- | --- | --- | --- |
| `ENG-V3-020` | Take-private semantics + `is_going_private_outcome` | REMEDIATED | §7 below; `test_take_private_derivation.py` (33 + 8 + 3); 0/26 on the PL corpus |
| `ENG-V3-021` | Deal Summary funding transport and non-disclosure semantics | REMEDIATED | `test_summary_attitude_transport.py` — 4 live anchors, null-preservation, 2 controls |

Both were found by **reading integration artifacts**, not by the exception detector.

## 3. Gate 2 results

### S-B — combination structure — PASSED

8 independent Product-supplied sources. `event_type` 8/8 · `combination_structure` 8/8 ·
positive merger-of-equals 2/2 · **no** false-positive MoE 6/6 · reverse mergers 2/2 · De-SPAC 2/2.

Three cases could each have failed predictably and did not: a reverse merger the source calls a
"merger agreement"; a reverse merger where the **legal acquirer** is the economically acquired
party (Skye/Redx, a UK scheme); and two SPAC deals that resolved to the most specific value
`DE_SPAC` rather than stopping at `MERGER`.

**MoE evidence rule.** Explicit merger-of-equals characterisation, **or** sufficiently balanced
stated ownership, may establish it. Generic merge/combine language alone does not. **There is no
hard percentage threshold.** A negative expectation accepts `0` **or** `NULL` — the field is
populated only on explicit or qualified evidence, so absence is `NULL` by design, and demanding
`0` would pressure extraction into asserting a negative it never observed.

### S-C — target type and asset type — PASSED 7/7 after remediation

One defect found, fixed, and re-validated. On 0.10 the classifier returned `assets`/`OTHER` for
GMS / Evergreen from the phrase *"acquired the assets of"*, though the substance was an operating
business continuing under the buyer. The same wrong answer appeared on the lead sentence **and**
the full release, ruling out fixture truncation and locating the cause in the prompt.

`deal_type_classifier` 0.11 added one principle — transaction form alone does not determine
`target_type`. On the rerun **one case moved and six held**: `target_type` 7/7, `asset_type` 7/7,
and genuine `assets` recall **3/3** across intellectual property, real estate and natural
resources. That asymmetry was the point: a guardrail that merely biased away from `assets` would
have shown up as the genuine asset cases drifting.

`asset_type` needed no separate fix. It is subordinate to `target_type = assets` and returned to
NULL on its own once the target type was right.

### S-D — offer mechanism — PASSED 4/4

`TENDER_OFFER` positive 2/2 · false-positive 0/2 · mechanism-and-structure coexistence 1/1.
A UK scheme of arrangement described as a *"recommended cash **offer**"* correctly returned NULL —
offer wording did not produce the mechanism. A two-step deal recorded `TENDER_OFFER` **and**
`combination_structure = MERGER` on one transaction, which V2's single-valued `merger_structure`
could not represent.

### S-A — attitude and approach — PARTIAL / OPEN

Earlier independent validation of `FRIENDLY` and `UNSOLICITED` stands. The open defect:

| Field | Expected | `0.7` | `0.8` |
| --- | --- | --- | --- |
| `deal_attitude` | `HOSTILE` | `HOSTILE` | `HOSTILE` |
| `competing_bid` | `0` | `0` | `0` |
| `approach_type` | **`NULL`** | `UNSOLICITED` | `UNSOLICITED` |

A mandatory/regulatory offer does not by itself establish `UNSOLICITED`. `approach_type` describes
the **origin of the approach**; a statutory obligation to make an offer after crossing an ownership
threshold says nothing about whether the approach was solicited. The tested source contains the
word "unsolicited" zero times.

**The 0.8 clarification is semantically correct and did not change the behaviour.** It stated the
rule explicitly, and the rerun returned `UNSOLICITED` anyway — on a prompt that already said
*"null otherwise"*, *"null is a first-class outcome"* and *"do not infer either value from the
absence of the other"*. **Restating a rule the prompt already carries is not a remediation.** That
is the transferable lesson; the S-C fix worked because it named a *cue* the prompt had never
addressed, not because it repeated an existing rule more firmly.

**The `approach_type` evidence rule.** `UNSOLICITED` requires the source to state or establish an
unsolicited bid. Neither value is inferred from the absence of the other, and **null is a
first-class outcome that is expected to be the most common one.**

**`SOLICITED` is evidence-required.** Populate it only where the source **affirmatively
establishes an organised or invited process**:

- an auction;
- a formal sale process or strategic review;
- a bankruptcy, administrator or receiver process;
- a marketed sale, outreach, or an invitation to bid;
- explicit solicited language.

**Friendly negotiation, board recommendation, or signing an agreement does not by itself
establish `SOLICITED`.** Those facts do not establish approach origin. `deal_attitude` remains
independently determined under its own evidence rule. **NULL is a first-class outcome and is
expected to be very common.**

**Known gap between this rule and the live prompt, recorded not fixed.**
`low_confidence_extraction` 0.8 lists *"a sale process, auction, strategic review or outreach, or
an invitation to bid"* but does **not** name a bankruptcy, administrator or receiver process, and
carries **no exclusion** for friendly negotiation, recommendation or signing. The prompt is left
unchanged for now: `SOLICITED` has never been exercised, so there is no observed behaviour to
remediate, and the 0.8 `approach_type` attempt is direct evidence that adding prose to a rule the
prompt already states does not by itself change model behaviour. **Future real-text `SOLICITED`
validation determines whether prompt remediation is necessary.**

`SOLICITED` remains **unexercised** in validation — no source establishing an organised process
has been supplied.

---

## 4. The HOSTILE rule — Product, preserved exactly

> Target rejection or opposition **plus** bidder persistence, maintenance, return, or a new or
> improved unsolicited bid; **or** an explicit hostile characterisation.
> **A single rejection does not by itself establish HOSTILE.**

**Mandatory-offer caveat.** In a statutorily compelled offer, the offer's continued availability
may be **legally required rather than evidence of bidder persistence**, so the second limb cannot
be read off the offer remaining open.

The Kontron source returned `HOSTILE` on both prompt versions and appears in no prompt. That is
recorded as a **boundary observation, not proof of the general rule** — it must not be read as
establishing that HOSTILE reduces to "the board recommends rejection."

---

## 5. Known prompt/model coupling risk — `approach_type`

Recorded as a risk, **not** an instruction to add examples now:

- `deal_attitude` and `approach_type` are **independent** dimensions.
- A mandatory/regulatory offer does not establish approach origin.
- `low_confidence_extraction` 0.8 says so explicitly and did not change the tested behaviour.
- **All three worked examples that populate `approach_type` return `UNSOLICITED`.**
- **There is no `SOLICITED` worked example.**
- **Example 9 is the only worked case where `deal_attitude` and `approach_type` appear together,
  and there they co-vary** — a board rejecting a bid, paired with `UNSOLICITED`. That is the
  tested source's exact surface shape, and the prose asserting independence is not backed by any
  worked case showing the two apart.

Researcher review remains available for ambiguous or misclassified secondary fields.

---

## 6. Accumulated implementation corrections

- **S-B.** `MERGER` and `REVERSE_MERGER` are removed as `v2_event_type` values and are invalid
  new output; both are `combination_structure`, hierarchical `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`,
  valid only when `v2_event_type = ACQUISITION`. Store the most specific value and **query by
  implication, never equality**. Stored legacy rows keep both values — read tolerance in
  `aggregate.py` is deliberate.
- **MoE evidence rule.** As in §3 above. No percentage threshold.
- **S-C.** Transaction form alone does not determine `target_type`; `assets` not chosen *solely*
  from asset-purchase wording.
- **Retained column ≠ continued authorship.** `is_de_spac`, `is_divestiture`, `hostile`,
  `includes_earnout`, `is_down_round` and `round_stage_category` keep their columns and their
  stored history, and simply stop being written. A retained column is not a claim that Stage 9
  still owns the field.
- **S-E rationale, corrected.** An earlier commit message said leaving fields in
  `_STAGE9_OWNED_COLUMNS` would "write NULL over stored values on every re-aggregation." That was
  **wrong**: `insert_observation` is idempotent and never deletes, so legacy observations persist.
  The decision was unaffected; the reasoning was.
- **S-F.** `CONTINGENT_CONSIDERATION` added for consideration the source states is contingent,
  deferred or milestone-based where neither `EARNOUT` nor `CVR` is established. **Most-specific
  supported form**, in both directions: do not fall back to the generic form when the source
  supports a subtype, and do not promote a vague additional payment to `EARNOUT` because earnouts
  are common. Contingent components are **additive**; they never replace base consideration.
  `includes_earnout` is retired entirely.
- **`is_stock_for_stock` is excluded from V3** unless a future Product requirement emerges.

---

## 7. `is_take_private` — RESOLVED at `V3-PC-1.0`

**This section described a live defect. It has been remediated.** Retained for the record;
the current contract is in the Data Dictionary derived-fields section and the Register row `ENG-V3-020`.

**What was wrong.** The derivation reached 1 on a public standalone target plus a broad
acquirer type, with an acquirer-ticker guard standing in for "the buyer is private". Both
halves were wrong. The type test admitted `strategic_corporate` and eleven other types that
establish no going-private structure. The ticker guard was never a proxy for private
ownership — a listed sponsor taking a company private is a genuine take-private, and the guard
returned 0 for every one of them, making it a false-negative source rather than a safeguard.

**How it surfaced.** All three positives in the 29-source PL integration run were wrong:
Monte dei Paschi twice (both `strategic_corporate` acquirers) and Pontiac/Ottawa Bancorp. One
propagated into a generated summary asserting the deal was "structured as a take-private".

**Why a new primitive was needed.** No existing field could establish the ownership outcome.
`pct_acquired` is documented "Null if 100% or unstated", so its null is ambiguous by
construction; the §2.6 resolver's assumed 100 fires on every silent control acquisition;
`stake_transition_type` is populated only on explicit ownership evidence and is sparse;
`offer_mechanism` is `TENDER_OFFER | null` and most take-privates are one-step mergers;
`target_status` is pre-transaction with no post-transaction counterpart. The HC prompt's own
worked public take-private (Example 2) emits none of them — any rule built from current
primitives would have scored the reference example negative.

**What shipped.** Three required conditions: public standalone target; a qualifying
private-ownership buyer (`private_equity`, `pe_portfolio`, `management`, `employee_group`,
`other_financial_sponsor`); and the affirmative extracted `is_going_private_outcome`
(`true | null`, never persisted as 0). The ticker guard is gone. Migration `010`, HC 0.24.

**Note on the regression.** The earlier version of `scripts/test_take_private_derivation.py`
**certified the behaviour Product later ruled wrong** — it asserted
`private_strategic_take_private = 1` and `public_acquirer_blocks_flag = 0` — and would have
blocked the fix. Both are now inverted. Six of seven decisive cases fail against the
pre-change derivation, in both directions.

**Consortium.** `consortium` is not a qualifying buyer type, and it is not a V3 acquirer
type at all: the target model represents the actual participating firms and investors with
their roles, including the lead where the source establishes one. The `consortium` value and
the synthetic `CONSORTIUM` group in the prototype implementation are residue retired under
`ENG-V3-008`. A multi-buyer PE transaction is evaluated from its actual buyers and ownership
structure.

## 8. Deliberate legacy compatibility — do not "clean up"

- `_MA_EVENT_TYPES` in `aggregate.py` includes `MERGER`/`REVERSE_MERGER` — read tolerance for
  stored rows.
- Relevancy **reason codes** (`MERGER_ANNOUNCEMENT`, `REVERSE_MERGER`, `MINORITY_INVESTMENT`) are
  a **separate vocabulary** from event types. They remain valid; `overrides_relevancy_hint` exists
  because a hint may disagree with the classification.
- Retained schema columns for retired fields, with their `schema/*.sql` notes.
- Negative assertions naming retired values (`spinco`, `includes_earnout`) in tests and stage
  comments — these prove the value is rejected.

**Later cleanup, not reconciliation:** `stages/export.py` and `scripts/export_review_xlsx.py`
still emit retired columns; `stages/summarize.py` still logs "Stage 10" though `run.py` runs it as
Stage 12.

---

## 9. Later / parked

- LOI date distinct from definitive signing date.
- Contingent consideration interaction with Transaction Value / Enterprise Value.
- **SEC architecture review.** The current architecture remains authoritative. A later review may
  consider decomposing a filing package into separately provenance-bearing Item 1.01 / 99.1 / 2.1 /
  10.1 components; a related question is canonical V3 observations versus `sec_*` enrichment.
  **No SEC architecture change is in scope for reconciliation.**
- S-A `SOLICITED` real-text validation.
- S-E representative funding Gate 2.
- S-F explicit CVR and generic contingent Gate 2.

---

## 10. Strategic Rationale — tabled implementation item (§R7 + §R9 + §S2.1)

**TABLED — Product-accepted limitation. Not a blocker for S-H.** Recorded here because the
three inventory decisions it depends on are settled individually and unimplementable
individually, and that fact previously existed only in conversation.

### Why the three cannot be separated

| | |
| --- | --- |
| **§R7** | The three structure-derived defaults are retired: PE acquirer on `ACQUISITION`, `SPIN_OFF`/`SPLIT_OFF`, and `RECAPITALIZATION`, each → `FINANCIAL_OR_ARBITRAGE`. Transaction structure and buyer type must not manufacture rationale. |
| **§R9** | Settled semantically: `OTHER` means a source-supported rationale exists but fits no named category. No determinable source-supported rationale is **NULL**, not `OTHER`. |
| **§S2.1** | The physical representation of "processed, none found" is deliberately left to Engineering — a durable marker, a nullable rationale, or a separate processing-state record are all open. |

Retiring the §R7 defaults while leaving the prompt's current `OTHER` rule in place would
route no-evidence cases into `OTHER` — precisely the missing-information bucket §R9 exists to
eliminate — converting a removal into a silent relabelling. The defaults were the only thing
guaranteeing a `rationale_tag` row for the deals that state no rationale, so removing them
widens the §S2.1 gap rather than causing it.

**Product has not approved** inventing a response-contract sentinel for "no rationale", nor
knowingly routing no-rationale cases to `OTHER` in order to close the cleanup. Either would
trade a recorded defect for an unrecorded one.

### The mechanical facts, verified

- `rationale_tag.primary_rationale` is `TEXT NOT NULL` (`schema/001_initial.sql:471`). No
  migration has altered it. NULL is not storable.
- `stages/rationale_tag.py::_validate` rejects every null form — `None`, `""`, and an omitted
  key alike — so the stage `continue`s without inserting. **"No rationale" is row absence, not
  a NULL value.**
- The re-run gate is `NOT EXISTS (… rationale_tag … is_current = 1)` (`:99–102`). A transaction
  that legitimately yields no rationale is therefore reprocessed on **every** subsequent run,
  paying the model cost indefinitely. **The stage is not idempotent for that outcome**, and
  *not yet processed* is indistinguishable from *processed, none found*.
- Both consumers already `LEFT JOIN` (`stages/export.py:260`, `eval/score.py:112`), and
  `eval/score.py::_compare` scores gold-blank against pipeline-NULL as a match. **No schema
  migration is required** for NULL semantics at the consumer level; what is missing is a way
  to *record* the no-rationale outcome.
- Rationale is not an aggregated field: `grep rationale stages/aggregate.py
  lib/observation_writer.py` returns nothing. Stage 13 writes `rationale_tag` directly, so the
  canonical-field structural gate does not apply to this work.

### Open Product question — whose rationale is this?

**Unresolved.** Whether `strategic_rationale` represents **acquirer** rationale,
**seller/target** rationale, or **overall transaction** rationale has never been decided. It is
especially material for PE transactions, where much announcement language describes
seller/target benefits — shareholder value, certainty of consideration, freedom from quarterly
reporting — rather than buyer motivation. It bears directly on §R7: the retired PE default
assumed a buyer-motivation reading of language that frequently is not about the buyer at all.

### Tabled consideration — rationale evidence excerpts

When the rationale model is revisited, consider retaining concise source excerpts supporting
each classified rationale, for researcher review and provenance — including the ability to
inspect **whose** rationale the quoted language represents. **No storage or extraction design
is approved.** Recorded as a consideration only; it interacts with §R8's requirement that
`SOURCE_STATED` carry durable evidence attribution, which the current
`supporting_excerpt_index` cannot satisfy because it indexes a prompt-time list that is never
persisted.

### Attached findings — carried with this item, not fixed

- **Summary-only fallback.** When the keyword scan yields no excerpts, `rationale_tag.py:142`
  substitutes `"(none — classifying from summary only)"` and calls the model anyway,
  contradicting the prompt's own evidence rule and §R10.
- **Stale stage labels.** `:109` and `:223` log "Stage 11"; this is Stage 13.
- **Surplus legacy kwarg.** `:94` selects `tr.deal_type` and `:145` passes `deal_type=` to
  `.format()`, but §5 has had no `{deal_type}` placeholder since 0.5.
- **Stale downstream claim.** `docs/funding_path_design.md:400–402` asserts funding events
  default to `FINANCIAL_OR_ARBITRAGE` "already handled by the PE/sponsor rule" — no funding
  rule exists, and its stated justification is the rule §R7 retires.
- **Gold-set coverage.** `eval/gold_set_test.csv` has 3 labelled rows and **no blank-rationale
  case**, so NULL semantics cannot be validated by the current gold set. Row `source_raw_id=8`
  (SOLAI going-private) expects `FINANCIAL_OR_ARBITRAGE` from structure-derived framing and is
  the one row in the affected family.
- **Unenforced contract.** §6 declares `notes` "Required when rationale is OTHER"; `_validate`
  does not check it.
- **Orphaned changelog row.** 0.4 records "Updated take-private note to derived flag
  reference"; no take-private or `is_take_private` reference survives in the prompt body.

### Also recorded: §7 examples are not delivered to the model

`prompts/base.py::load_prompt_file` extracts only the §4 and §5 fences. The prompt's
Few-Shot Examples are documentation, not behaviour. Two consequences for this file: Example 3
("Take-private with PE acquirer, default financial") cannot influence any classification, and
the failure-mode row claiming "Example 3 addresses" take-private misclassification is false as
written. Both are documentation corrections belonging to the tabled slice, not defects on
their own.

**No Strategic Rationale behaviour is implemented or proposed by this record.**

---

## 11. Accepted non-blocking limitations — prompt trust re-audit

The full prompt-contract re-audit found six issues. One — the relevancy `reason_code`
vocabulary never reaching the model — was remediated in `relevancy_filter` 0.8. The remaining
five are **accepted as non-blocking for S-H** and belong on the Engineering-handoff cleanup
list:

| Finding | Where | Disposition |
| --- | --- | --- |
| Agreement family has no model-tier row | `prompts/prompt_conventions.md` §2 lists 7 stages plus a 4b note; the five `agreement_*` prompts appear only in the OpenAI list, though they run on `model="opus"` (`stages/agreement_extract.py:417`) | Accepted |
| Surplus legacy `.format()` kwargs | `summarize.py:173` and `low_confidence_extract.py:165` pass `deal_type` + `event_type`; `rationale_tag.py:144` passes `deal_type`. No template consumes them; `str.format` ignores extras silently | Accepted |
| Unused `_VALID_LEGACY_EVENT_TYPES` | `stages/deal_type_classify.py:91` — defined, never referenced; `AMENDMENT` and `TERMINATION` appear nowhere in the classifier prompt | Accepted |
| Stage numbering disagrees across layers | `specs/pipeline.md:33,74,80` calls export Stage 12 and the pipeline 12 stages; docstrings say 12/13/14; `summarize` logs "Stage 10", `rationale_tag` logs "Stage 11", and `export.py:268` logs "Stage 13" while `:287` logs "Stage 14" | Accepted |
| Empty fenced JSON block | `prompts/low_confidence_extraction.md:60` — the sole unparseable block of 101 across all prompts; pre-existing, confirmed byte-identical before the provenance sweep | Accepted |

**Method note worth keeping.** Both the relevancy finding and the Example 3 finding come from
the same root cause: content outside the §4/§5 fences is never delivered, and a test that
asserts on it certifies nothing about model behaviour. `test_reason_code_parity.py` passed on
24 == 24 for the prompt's entire history while the model was shown none of the codes in an
authoritative list. Prompt-contract tests must read `load_prompt_file(...)`, not the Markdown.
