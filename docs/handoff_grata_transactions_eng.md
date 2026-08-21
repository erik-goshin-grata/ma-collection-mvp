# Transactions → Grata Data Model: Engineering Handoff

**From:** Transactions data-model assessment
**To:** Grata / Transactions Engineering
**Baseline:** `main` @ `a24feb0`
**Status:** Product/data semantics settled for this pass. Remaining work is implementation,
external definitions, or evidence-triggered validation.

This is the **front door**, not the specification. Every claim here is summarised from the
detailed documents listed in §10; drill there for field-level detail and the evidence behind
each decision.

> ## ⚠️ **Transactions V3 is now the canonical target model.**
> **`docs/grata_v2_inventory_and_recommendations.md` §T is authoritative** and supersedes
> parts of §2, §4 and §5 below. V2/harness behaviour and the earlier Grata model are
> **inputs** to V3, not authorities over it — a V2 field does not automatically belong in V3,
> and there is no live Grata implementation whose behaviour must be preserved. Prompt
> versions (`0.5 (V2 alignment)`, `v2_event_type`) describe the implementation generation a
> prompt was built against, **not** the canonical model generation.
>
> V3 decisions recorded 2026-08-19: event taxonomy and the removal of canonical
> `event_category` · `combination_structure` · `target_type` · removal of `is_divestiture` ·
> party roles including `PARENT_ACQUIRER` / `SPONSOR_BUYER` / `SPONSOR_SELLER` · the advisor
> model · `sponsor_transaction_role` · recap flags · `acquirer_type`. Six
> implementation/migration consequences are recorded in `decisions.md`, **deliberately
> unrepaired**.
>
> **Validation status.** **V3 is an evolution of an already developed pipeline and data
> model, not a greenfield design.** Existing extraction, stages, schemas, tests and prior
> validation remain relevant evidence and should be preserved where V3 semantics do not
> change them. The **37/37** suite confirms the current regression suite remains green but
> does **not** by itself validate the V3 changes, which are not yet implemented. Before
> executable V3 changes land, each changed concept should have its **V2 → V3 path traced** —
> prompt → stage → validation → storage/aggregation → canonical output — with regression and
> real-transaction validation **added or rerun wherever the semantics or implementation path
> change.** A validation gate, not a backlog item.

---

## 0. V3 orientation — read this first

**Transactions V3 is the canonical target data model.** V2/harness behaviour and the earlier
Grata model are **inputs** to it, not authorities over it. This section is the standalone
orientation; **detailed field definitions live in the V3 dictionary and inventory §T** (§10),
which this document deliberately does not duplicate.

### 0.1 What V3 settles

| Domain | V3 semantics | §T |
| --- | --- | --- |
| Event taxonomy | `event_type` is the top-level answer to "what happened?". Canonical `event_category` **removed**; Product/FE families are **derived** from `event_type`. | T1 |
| Combination structure | `MERGER` / `REVERSE_MERGER` / `DE_SPAC` / null, **hierarchical** — query by implication, never equality. | T2 |
| Target type | `STANDALONE_COMPANY` / `SUBSIDIARY` / `BUSINESS_UNIT` / `ASSETS` / null. `SPINCO` removed — implied by `event_type`. | T3 |
| Divestiture | **Removed from V3.** Derive a *Divestitures* grouping from event types if wanted. | T4 |
| Party roles | `TARGET` · `BUYER` · `SELLER` · `INVESTOR` · `PARENT_ACQUIRER` · `PARENT_SELLER` · `SPONSOR_BUYER` · `SPONSOR_SELLER` · `ADVISOR` · `LENDER` · `JV_PARTNER` · `UNDERWRITER`. **Sponsor side is directly representable.** | T5 |
| Advisors | Role is `ADVISOR`; the participation references the **specific party advised**; `advisor_specialty` describes the service; **side is derived**. | T6 |
| Sponsor transaction role | `PLATFORM` / `ADD_ON` / null. `ADD_ON` needs **no literal wording** and is **never inferred from `acquirer_type`**. | T7 |
| Acquirer type | Retained as **extracted transaction context**, vocabulary purified to genuine economic/entity types. `PE_PORTFOLIO` / `MANAGEMENT` / `EMPLOYEE_GROUP` removed. | T8 |
| Attitude + Approach | **Two independent dimensions:** `deal_attitude` (`FRIENDLY`/`HOSTILE`/null) and `approach_type` (`SOLICITED`/`UNSOLICITED`/null). | T11 |
| Offer mechanism | `TENDER_OFFER` / null. Optional, and **orthogonal** to combination/merger structure. | T12 |
| Asset type | Eleven values, **subordinate to `target_type = ASSETS`**, single-valued, distinct from sector. | T13 |
| Funding round | Three concepts: `round_label` (verbatim) · `round` (canonical, keeps `SERIES_A2`) · `vc_stage` (**derived from `round`**). | T14 |

**Settled but extensible.** Each vocabulary can gain legitimate values from real cases without
reopening the semantic model. Six items remain explicitly undecided (§T10).

### 0.2 Material V2 → V3 changes

| Change | Nature |
| --- | --- |
| `event_category` removed; families derived | Removal + derivation |
| `SHARE_PURCHASE` / `ASSET_PURCHASE` / `SPINCO` removed | Vocabulary removal — each was a duplicate or a named null |
| `is_divestiture` removed | Removal, **not** repair |
| Four recap flags removed; `recap_type` survives | Redundancy removal |
| `is_platform_investment` + `is_add_on` → `sponsor_transaction_role` | Collapse **with specified evidence semantics** |
| `acquirer_type` vocabulary purified | Three values removed; behaviour qualifiers dropped from definitions |
| `hostile` boolean → `deal_attitude` + `approach_type` | **One field fans out to two**; third fused fact not promoted |
| `merger_structure = TENDER_OFFER` → `offer_mechanism` | Field split — restores orthogonality V2 destroys |
| `asset_type` added | **New collection**, no V2 source |
| `round_stage_category` → `vc_stage`, derived from new canonical `round` | Derivation input changes from free text to a controlled vocabulary |
| `PARENT_ACQUIRER`, `SPONSOR_BUYER`, `SPONSOR_SELLER`, `ADVISOR` | Role additions and one reversal of a v0.4 collapse |

### 0.3 Product requirements vs ENG choices

**Product requires** — the semantics above: which facts are independent, which are derived,
which vocabularies apply, and what null means in each. Where a decision reads like a design,
it is a semantic requirement any design must meet.

**ENG chooses** — physical placement, storage shape, column vs child table, enum vs lookup,
casing and naming conventions, migration mechanics, and how the `combination_structure`
implication set is expressed, **provided Product semantics are unchanged**.

**The standing Engineering constraint holds:** prefer typed dimensions and derivation over
unnecessary flag proliferation — while **retaining independent facts and useful
Product/researcher distinctions**. A 2026-08-19 audit found that guidance had been
over-applied in three places, and §T5–T6 record the reversals. The distinguishing test:
collapsing a genuinely single dimension **adds** a representable state; collapsing distinct
roles **removes** one.

### 0.4 Where the rest is

**Known V2 implementation defects** → §6, *Recorded defects and follow-ups*.
**V3 migration/implementation consequences** → §6, *V3 implementation / migration consequences*.
**Validation requirements for changed executable paths** → the validation-status note above,
and §0.5.

### 0.5 Validation requirement, restated

V3 is an **evolution** of a developed pipeline. Existing tests and prior validation remain
relevant evidence **where V3 semantics do not change them**. For every concept that does
change, trace the **V2 → V3 path** — source evidence → extraction → validation →
canonical storage/derivation → Product/researcher use — and **add or rerun** regression and
real-transaction validation wherever the semantics or the implementation path change. A green
suite is a regression check on the current codebase, not evidence of V3.

**Two reasoning rules apply to any further evaluation** (§T15): export presence is **not**
evidence of canonical-model importance or priority, and upstream provider categories are
**not** canonical classification nor a proxy for what our pipeline can classify or test.

---

## 1. Executive summary

**What was assessed.** The Grata V2 transaction data model — every Gold table in the
supplied schema — compared against a working extraction harness that has processed a real
corpus end to end. The comparison covered event taxonomy, parties, values, consideration,
funding, metrics, currency, Spin/Split, advisors, observations and lifecycle, plus a
reconciliation of the MergerLinks vocabulary.

**What changed after Engineering review.** ENG returned nine directives, and a substantial
block of the v0.3 recommendations changed as a result — every changed row carries the **✱**
marker in inventory §P, which is the column to scan. (§P's summary line gives a row count
that no longer matches the table; see §9.4. The markers themselves are authoritative.) The
largest change is structural: event/feature modelling
moved from flags to **typed dimensions** wherever values are mutually exclusive, with flags
retained only where a characteristic is genuinely orthogonal. That test was applied case by
case and did **not** give a uniform answer — some flag pairs collapse, one group is
confirmed correctly orthogonal, and one collapse revealed a state the booleans could only
express by accident. Eleven flags were additionally identified as **derivable and not worth
storing**, under a precondition that matters: a flag is only safely removed if its deriving
input is present whenever the flag would have been set.

**Two domains were found missing after the first pass** — Summary and Strategic Rationale —
and reviewing them produced four further Product decisions (§5, R7–R10). The substantive one:
rationale must distinguish **source-stated** from **inferred**, per rationale, and the three
structure-derived defaults that silently produced inferred rationales are retired.

**Where this leaves us.** No Product decisions are open, and **the V3 taxonomy is closed** — see §0. Twenty-eight
items remain, none of them a Product choice: six ENG implementation items (schedulable now),
one prompt / legacy-compatibility review (a decision is owed before it can be scheduled), six
recorded defects and follow-ups, **eleven V3 implementation/migration consequences**, two
evidence-blocked items (cannot be scheduled), and two external-system asks.

> **Read §5 before reviewing.** The Product decisions listed there are settled. They can be
> revisited on new evidence, but they should not be reopened by default in an
> implementation review.

---

## 2. Data inventory — where Grata and Transactions align or differ

Field-level detail is in the inventory (§A–§N) and dictionary. Summary only:

| Domain | Alignment |
| --- | --- |
| **Event taxonomy** | Materially different. Grata carries overlapping top-level types (`MERGER`, `REVERSE_MERGER`, `SPAC_DE_SPAC`, `MINORITY_INVESTMENT`) that are structures or features rather than distinct events. Target: one broad M&A event plus typed dimensions. |
| **Parties and roles** | Largely aligned. Gaps: no side-specific sponsor roles (`SELLER_SPONSOR` / `BUYER_SPONSOR`), and merger subs / acquisition vehicles need representation without contaminating acquirer type. |
| **Value / equity value / transaction size** | Aligned on concepts after this pass. `transaction_size` + `transaction_size_basis` is **built and live** in the harness. The documented basis vocabulary diverged across three files at the time of synthesis; **that conflict is resolved** — see §9. Shipped vocabulary: `{TRANSACTION_VALUE, ROUND_SIZE, SPIN_SPLIT_CONSIDERATION_VALUE}`. |
| **Consideration** | Materially different. Grata derives a flat `consideration_type` and defers the component table; the target requires a normalized repeating `consideration_component`. Harness extraction already produces component-level detail. |
| **Funding** | Aligned. `ROUND_SIZE` is the transaction magnitude; `investment_amount` stays investor-level on the party. Confirmed by a legacy remediation that corrected rows where a raise had been filed as a transaction value. |
| **Metrics / financials** | Aligned on shape, differ on policy. One normalized table is the **preferred** home for deal-value and company-financial metrics. Currency/period/provenance rules are stated once as seven normative rules rather than scattered. |
| **Currency / FX** | Materially different. Three distinct currency concepts are required (native, transaction/reference, normalized). Grata has `fx_rate` / `fx_rate_date` but no stated policy on when conversion may occur. |
| **Spin / Split** | Aligned on concepts. Placement changed after ENG review: transaction-level scalars plus the generalized security model, **no child table** — nothing is multi-valued except per-security-class counts. |
| **Advisors and people** | Materially different. Grata holds one advisor person per party row; many are needed. Specialty vocabulary is coarser in collection than in Grata, and fails in **both** directions. |
| **Observations / provenance** | Partly aligned. Per-fact provenance (source attribution + fact key) is needed on metric rows to distinguish corroboration from multiplicity. The harness ledger has this; Grata's `financial_metric` does not show it. |
| **Lifecycle** | Aligned, with one semantic now explicit: a later `CLOSE` or `TERMINATION` is a **related event, not a fact update**. |
| **Narrative / rationale** | **Omitted from the first pass; added 2026-08-19.** No Grata counterpart found for either. Summary is a derived narrative and is **not authoritative** for structured facts. Strategic Rationale needs a per-rationale **basis** (`SOURCE_STATED` / `INFERRED`) and durable evidence; three structure-derived defaults are retired. §4, §5, inventory §S. |
| **MergerLinks vocabulary** | Assessed as labels only — no ML definitions were available. Of 40 labels, ~55% map into the target model as it stands; 10 are unresolved pending definitions. |

---

## 3. Target semantic model — what Engineering needs to understand

Fourteen settled points. Full statements in inventory §A6, §A7, §C, §D, §E4, §R.

**Taxonomy and structure**

1. **Broad event family + typed dimensions.** One M&A event type; structure, stake,
   management participation, sponsor role and offer mechanics are separate dimensions.
2. **`DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER` is hierarchical, not three peers.** Store the most
   specific value; **query broader questions by implication, never by equality**. An
   equality test against this dimension is a bug, and it is the specific way the design
   fails if the hierarchy is treated as decoration. Ambiguity resolves **upward**.
3. **Typed dimensions where values are mutually exclusive; flags where genuinely
   orthogonal.** `is_take_private`, `is_lbo`, `is_secondary_buyout` are confirmed
   orthogonal — prior listing status, financing and seller identity are different axes, and
   one deal can be all three.

**Sources, events and reconciliation**

4. **Source ≠ transaction.** One source may evidence zero, one, or several.
5. **Multiple events in one source is normal.** Profiling only some is a *scope* choice;
   identifying them independently and attributing observations to the right transaction is
   a *capability* requirement. Blending is the failure mode — a financing figure read as an
   acquisition's consideration is a wrong value, not a gap.
6. **Lifecycle event ≠ supersession.** Treating a `CLOSE` release as superseding the
   announcement would silently delete the announced terms.
7. **Reconciliation is precedence/authority-based, not recency.** Source tiering is an
   input. **Fact identity is established first** — two figures that are not the same fact
   are not in competition. Human adjudication outranks automatic precedence.

**Values**

8. **Transaction value vs equity value vs transaction size.** Equity value is stake-level
   as-transacted; transaction value is transaction-level; `transaction_size` is a **derived
   rollup**, never a new economic definition, and never summed with the rows it derives
   from.
9. **Funding round size vs investor-level investment amount.** `ROUND_SIZE` is the event's
   magnitude; an investor's check is party-level and never rolls up into it.
10. **Consideration is a repeating component structure**, not a flat enum. Cash + stock +
    CVR coexist; `consideration_type` is derived from components.

**Metrics and currency**

11. **One normalized metric table is preferred** for deal-value and company-financial
    metrics, so currency, period, provenance and basis attach to the value itself.
12. **Three currency concepts:** native/source metric currency, transaction/reference
    currency, normalized/display currency. **A metric of unknown native currency never
    inherits the transaction currency** — inheritance manufactures a figure indistinguishable
    from a stated one.
13. **Native facts are never overwritten by FX normalization.** Conversion is derived,
    optional, and must be reproducible from stored rate/date/basis.

**People**

14. **Person ↔ firm affiliation and transaction role must survive extraction**, even though
    identity mastering is owned elsewhere. Identity is another team's; *who acted for whom
    on this deal* exists only in this source and cannot be reconstructed later.

---

## 4. Recommendation matrix

Material items only. Full per-concept table with change markers is inventory **§P**.

| Area | Current state | Recommendation | Status | Owner / next action |
| --- | --- | --- | --- | --- |
| Event taxonomy | Overlapping top-level types | Broad M&A event + `combination_structure` hierarchy | **CHANGE** | ENG — schema |
| Merger / reverse-merger / de-SPAC flags | Flags proposed or present | Roll up from the hierarchy | **REMOVE-DERIVABLE** | ENG |
| MBO / MBI | Two flags | `management_participation` (incl. `BIMBO`) | **CHANGE** | ENG |
| Up / down round | Two flags | `round_price_direction` (incl. `FLAT`) | **CHANGE** | ENG + collection vocabulary |
| Platform / add-on | Two flags | `sponsor_investment_role` | **CHANGE** | ENG |
| Take-private / LBO / secondary-buyout | Flags | Keep — genuinely orthogonal | **KEEP** | — |
| Recap flags (×4) | Duplicate `recap_type` | Derive from `recap_type` | **REMOVE-DERIVABLE** | ENG — no precondition; removable today |
| `is_stock_for_stock`, `has_earnout`, `has_cvr` | Stored | Derive from components | **REMOVE-DERIVABLE** | ENG — **after** components land |
| Consideration components | Deferred child table | Normalized repeating structure | **ADD** | ENG — precondition for the row above |
| `transaction_size` / `_basis` | Missing in Grata; live in harness | Adopt, with basis required | **ADD** | ENG |
| Metric table | Two classes in one table | Preferred home for both, + `DERIVED_ROLLUP` class | **CHANGE** | ENG |
| FX / currency policy | Fields exist, no policy | Seven normative rules; three currency concepts | **ADD** | ENG |
| Spin/Split mechanics | Type + mechanism only | Transaction scalars + security model + metric rows | **CHANGE** | ENG — no child table |
| Advisor persons | One per party row | Repeating child; capture unresolved, resolve optionally | **ADD** | ENG |
| Advisor specialty | 5 values | + `tax`, `proxy_solicitation`, `information_agent` | **ADD** | ENG + collection vocabulary |
| Offer mechanics | Absent | `offer_mechanism` (tender / mandatory / scheme) | **ADD** | ENG |
| Deal attitude | Absent in Grata; boolean in harness | `deal_attitude` typed dimension | **ADD** | ENG |
| Synergy metrics | Absent | 3 metric types + `SYNERGY` class; `TOTAL` is a stated rollup | **ADD** | ENG |
| Summary (narrative artifact) | Absent from Grata and from the first pass | Adopt as a derived artifact; **never authoritative** for structured facts | **ADD** | ENG |
| `rationale_basis` per rationale | Absent — basis indistinguishable | `SOURCE_STATED` / `INFERRED` on primary **and every** secondary | **ADD** | ENG — placement not prescribed |
| Rationale evidence attribution | `supporting_excerpt_index` into an unpersisted list | Durable source reference | **CHANGE / REPLACE** | ENG — not derivable; capture at classification time |
| Structure-derived rationale defaults | PE · Spin/Split · Recap → `FINANCIAL_OR_ARBITRAGE` | Retire all three | **REMOVE** | ENG — prompt change + regression |
| `OTHER` rationale | Doubles as a missing-information bucket | Source-supported residual only; absence is NULL | **CHANGE** | ENG |
| Recap domain redesign, IPO/direct listing, P/TBV enrichment, related-transaction linkage | Various | Preserve, do not redesign this phase | **DEFER** | — |
| Silver/Gold placement | Open | Satisfy the §R6 invariants; layer not prescribed | **ENG DECISION** | ENG |
| Supersession key | Partly built | Semantics settled; key and implementation open | **ENG DECISION** | ENG |

---

## 5. Product decisions already settled

Inventory **§R** carries the full text. Summarised so an implementation review does not
reopen them by default:

| | Decision |
| --- | --- |
| **R1** | Reconciliation is precedence/authority-based, not recency. Fact identity precedes supersession. Lifecycle events are not fact updates. Human adjudication outranks automatic precedence. |
| **R2** | USD normalization is optional and derived; native facts are never overwritten; conversion must be reproducible from stored rate/date/basis. |
| **R3** | Three currency concepts. A metric of unknown native currency never inherits the transaction currency. |
| **R4** | Period coherence stays **exact**. Revisit only on a real case where a legitimate pair is rejected. |
| **R5** | Source ≠ transaction; one source may evidence several. |
| **R6** | Nine invariants define the system-of-record contract; Engineering chooses the layer. |
| **R7** | No approved structure-derived rationale inference. The PE, Spin/Split and Recap defaults are retired — unsupported inference or derivable restatements of structured facts. `INFERRED` survives as a basis value; nothing currently populates it. |
| **R8** | Rationale basis is carried **per rationale**, primary and every secondary. `SOURCE_STATED` requires durable evidence; `INFERRED` must be marked inferred and never given synthetic evidence. |
| **R9** | `OTHER` means a source-supported rationale exists but fits no named category. No determinable rationale is **NULL**, not `OTHER`. |
| **R10** | Summary is not an authoritative source of structured facts. No structured field may be populated or corrected solely by parsing it. |

**Standing decisions from earlier passes**, also settled: `EQUITY_VALUE_ONLY` records *debt
unknown*, never debt = 0; stake-level values are never multiple numerators; the funding
family derives no `transaction_value` or `equity_value`; PIPE is recognized but not
profiled; and a single stated qualified anchor ("over $140 million") is normalized to the
stated figure with the original wording preserved in provenance.

---

## 6. Engineering decisions / actionable work

Two groups, kept separate because they unblock differently. **Six items are schedulable
Engineering implementation.** One is a prompt / legacy-compatibility review that is **not**
schedulable implementation — a decision is owed before any edit. Product has deliberately not
prescribed implementation for any of them.

### Schedulable Engineering implementation (6)

1. **Reconciliation / supersession key and implementation.** Semantics settled by R1. The
   key is unlikely to be single-valued: a filed document is immutable once filed, a web
   source can change under the same URL, and the two want different scoping.
2. **Silver/Gold physical placement** satisfying the nine §R6 invariants. Product does not
   prescribe the layer; whether they are met in Silver, Gold or across both is ENG's call.
3. **`PER_SHARE_X_SHARES` wiring.** The share-count path is confirmed in code:
   `stages/agreement_extract.py` writes `transaction_security.shares_outstanding` per
   security class with a fully-diluted total and quality marker, while
   `stages/aggregate.py` hard-codes `sec_shares = None`. The rung is disconnected from data
   already collected in a richer form than a bare count. **Two caveats:** live population
   was not verified, and coverage is limited to agreement-bearing deals.
4. **`rationale_basis` — schema and placement.** Semantics settled by R8: basis is carried
   per rationale, on the primary and on **every** secondary. `secondary_rationales` is a bare
   JSON array of enum values today, so it cannot carry per-item attribution at all — whether
   that becomes a child relation, parallel arrays, or something else is ENG's call. Product
   requires only that basis be recoverable per rationale.
5. **Durable rationale evidence attribution.** Replaces `supporting_excerpt_index`, which
   indexes a prompt-time excerpt list that is never persisted. **This is a replacement, not a
   removal**: the value is not derivable from anything stored, so the evidence reference has to
   be captured at classification time or it cannot be reconstructed later at all.
6. **Retire the three structure-derived rationale defaults** from
   `prompts/strategic_rationale.md`. Unlike items 1–5 this changes a live model contract, so it
   needs its own regression. Expect gold-set movement in `eval/score.py`, which grades
   `primary_rationale` as a flat enum with no notion of basis — a correct consequence of the
   semantics changing, not a regression.

### Prompt / legacy-compatibility review (1)

**Not schedulable Engineering implementation.** It lands on ENG eventually, but a Product/data
decision is owed first, so it cannot be estimated or sequenced alongside the six above.

**Downstream prompts still enumerate `MINORITY_INVESTMENT` as a V2 event type** —
`prompts/strategic_rationale.md` (which states it as a *current* V2 enum value, and is
the strongest case), `prompts/aggregation.md` and `prompts/deal_summary.md`. **A decision
is owed before any edit, and this is not a stale-documentation fix.** Two questions are
being conflated by surface similarity: *what the classifier may emit* is settled —
classifier 0.7 removed the value, minority is a derived flag, and
`scripts/test_minority_core_classification.py` pins the rejection — while *what
downstream stages must still accept* is not. Legacy rows carrying the value remain in the
corpus and are handled deliberately (`stages/aggregate.py` `_NON_CONTROL_TYPES`, pinned
by `scripts/test_funding_value_family_gate.py`). These three files consume
already-classified rows, so naming the value may be **correct legacy tolerance rather
than drift**. The decision owed: should downstream prompts describe the *emitting*
vocabulary or the *accepted* one — and if the latter, should legacy values be labelled as
such in the prompt text? Prompt edits change live model contracts and need their own
regression.

### V3 implementation / migration consequences (11)

Recorded 2026-08-19 alongside the V3 taxonomy decisions. **Deliberately unrepaired** — current
V2 behaviour does not redefine V3 semantics. Full detail in `decisions.md`.

| Consequence | Note |
| --- | --- |
| `target_type` casing defect | Stage 9 compares uppercase; Stage 3 writes the model's raw lowercase. Affects `is_take_private`. |
| `acquirer_type` casing defect | Same pattern, wider: `is_add_on`, `is_de_spac`, and `is_take_private` (which needs **both** to match). |
| `SELLER_SPONSOR` dead path | Queried for `is_secondary_buyout`, never written. **V3 terminology is `SPONSOR_SELLER`.** |
| `parent_seller` conditioned on `target_type` | Cannot serve as independent seller-side evidence. |
| `is_take_private` private-buyer proxy | Uses acquirer-type membership; four of those values leave the V3 enum, so a proper private-vs-listed input is needed. An MBO of a listed company *is* a take-private. |
| `MANAGEMENT` removal sequencing | `management_participation` is decided but **not built**; it must land with or before the removal. |
| `hostile` fans out to two dimensions | V2 coerces unstated to zero, so **`hostile = 0` must not migrate to `FRIENDLY`**; `hostile = 1` cannot be resolved into which of three fused facts fired. |
| `offer_mechanism` has no ordinary-source extraction | V2 captures tender offer only via `merger_structure` on the trigger-gated SEC path. **V3 must not depend on it.** |
| `asset_type` has no V2 migration source | No asset sub-classification exists anywhere in V2; every value is newly collected. |
| `round_label` → canonical `round` | Free text with no validator; mapping is a normalization exercise **with a residue**, not a mechanical migration. |
| `vc_stage` must derive from `round` | The V2 substring derivation returns null for Series H+ and `Bridge Round`, and collides `Series AA` into `EARLY_STAGE`. No test exercises it. |

### Recorded defects and follow-ups (6)

Known, deliberately not actioned in this pass. None blocks the items above.

| Follow-up | Note |
| --- | --- |
| **Summary gates and feeds Strategic Rationale** | `rationale_tag` joins `summary … is_current = 1`, so no summary means no rationale row, and `summary_text` is passed in as evidentiary input. A narrative generated without source text is load-bearing evidence for a classification R8 requires to be source-grounded. Independent of item 5 — fixing evidence attribution does not remove the dependency. |
| **Summary prompt-contract mismatch** | The prompt asks for "the source PR's own framing"; the stage supplies no PR text. Recorded, not redesigned. |
| `summary.word_count` unenforced | Stored, never validated against the prompt's 80–150 word contract. |
| No behavioral regression coverage for Stages 12–13 | Neither `summarize.py` nor `rationale_tag.py` is exercised by any test. |
| `specs/pipeline.md` stage numbering | Still uses a pre-`sec_documents`/`agreement_extract` scheme — it numbers export as Stage 12 where `run.py` has 14. The 2026-08-21 prompt-trust re-audit found the log strings disagree too: `summarize.py` logs "Stage 10", `rationale_tag.py` logs "Stage 11", and `export.py:268` logs "Stage 13" while `:287` logs "Stage 14". Three layers, three schemes. |
| **"Processed, no rationale found" has no durable state** | `rationale_tag.primary_rationale` is `NOT NULL`, so a transaction processed with no source-supported rationale writes no row — and the stage's re-run gate keys on the absence of a current row, so it is re-processed every run. **Not idempotent for that outcome.** An ENG/design consideration, not a defect to patch: writing `OTHER` is forbidden by R9, and no table redesign is proposed. The requirement is only that *not yet processed* and *processed, none found* be distinguishable. Inventory §S2.1. |


### Prompt trust re-audit — accepted non-blocking limitations (5)

From the 2026-08-21 re-audit of every live prompt contract, asserted against the **delivered**
system prompts (`load_prompt_file(...)`), not the Markdown files. Thirteen of fourteen live
prompts came back CLEAN or explicitly accepted. The one blocker — the relevancy `reason_code`
vocabulary living in §6 and therefore never reaching the model — was remediated in
`relevancy_filter` 0.8. These five are **accepted, non-blocking for S-H**, and none affects
extraction semantics.

| Limitation | Where | Note |
| --- | --- | --- |
| Agreement family absent from the model-tier table | `prompts/prompt_conventions.md` §2 | The table lists 7 stages plus a 4b parenthetical. The five `agreement_*` prompts appear only in the OpenAI hierarchy, though they run on `model="opus"` (`stages/agreement_extract.py:417`) — a third of the directory has no tier recorded in the document that governs it. |
| Surplus legacy `.format()` kwargs | `summarize.py:173`, `low_confidence_extract.py:165` (`deal_type` + `event_type`); `rationale_tag.py:144` (`deal_type`) | No template consumes them; `str.format` ignores extras, so it is silent. Dead since the V2 alignment removed the placeholders. |
| Unused `_VALID_LEGACY_EVENT_TYPES` | `stages/deal_type_classify.py:91` | Defined, never referenced. `AMENDMENT` and `TERMINATION` appear nowhere in the classifier prompt. |
| Stage numbering | see the row above | Recorded once, in §6. |
| Empty fenced JSON block | `prompts/low_confidence_extraction.md:60` | The sole unparseable block of 101 across all prompts. Pre-existing — confirmed byte-identical before the caller-owned-provenance sweep, so not sweep-induced. |

**Method note.** Two findings in this audit — the relevancy vocabulary and the Strategic
Rationale prompt's Example 3 — share one root cause: `load_prompt_file` delivers only the §4
and §5 fences, so anything outside them is documentation, and a test asserting on it certifies
nothing about model behaviour. `test_reason_code_parity.py` returned 24 == 24 for the prompt's
entire history while the model was shown none of the codes in an authoritative list. **Prompt-
contract tests must read the delivered string.**

**Strategic Rationale** is tabled as a single implementation item spanning §R7 + §R9 + §S2.1,
with an open Product question on whose rationale the field represents. Recorded in full at
`docs/v3_slice_reconciliation.md` §10.

---

## 7. External asks

| Ask | Owner | Blocks |
| --- | --- | --- |
| **14 MergerLinks definition/example questions** (inventory §Q7) | MergerLinks | 10 unresolved vocabulary labels and several conditional schema candidates |
| **`value_usd_basis` semantics** | Grata | Whether it means *source-stated in USD* or *a conversion Grata performed* — opposite meanings, and the field name does not disambiguate. The field does not exist on our side. |

---

## 8. Evidence-triggered follow-ups

**Neither is a current implementation blocker.**

| Follow-up | Trigger |
| --- | --- |
| Multi-event **value contamination** — live or theoretical? | The Ensysce / Cy Biopharma source text. If the only monetary figure in such a release belongs to the financing, M&A extraction has nothing stopping it reading that figure as consideration. |
| Debt / cash path validation, and the period-coherence tolerance question | The first natural debt/cash case. Extraction and both calculated EV bases are fixture-validated only, with zero corpus rows. No tolerance is invented before one exists. |

---

## 9. Documentation conflicts — found in synthesis, corrected in the sources

**Status: resolved, before this handoff was issued.** This section originally reported an
open divergence. Writing the handoff is what surfaced it — synthesis forces the same claim
to be read across several documents at once, which is the one activity that reliably exposes
disagreement between them. The conflict was reported rather than silently resolved, and the
source documents were corrected first, as a separate reviewable change, so that nothing in
this handoff rests on a document known to be wrong.

**Read the current source documents as the authority, not this section.** §10 lists them.
The narrative below explains what was wrong and why it mattered, which a corrected document
no longer shows on its face; it is not a substitute for the documents themselves. Commit
provenance, where useful: the corrections landed on `main` as `a24feb0`.

### 9.1 The conflict, as found

`transaction_size_basis` and the D4 waterfall disagreed across three documents and the code.

| Source | Said, at the time of synthesis | Now |
| --- | --- | --- |
| `stages/aggregate.py` (**ground truth — shipped**) | `{TRANSACTION_VALUE, ROUND_SIZE, SPIN_SPLIT_CONSIDERATION_VALUE}`. No equity rung, no sole-investor rung. | Unchanged — this was always correct. Its *comment* was not, and was corrected. |
| `decisions.md` (later entry) | `SOLE_INVESTOR_AMOUNT` **removed** from the waterfall, the basis vocabulary and the Grata recommendation. Matched the code. | Unchanged. |
| `decisions.md` (earlier entry) | `SOLE_INVESTOR_AMOUNT` is "reserved but not live". Superseded, but unmarked. | **Marked superseded**, and the whole rationale beneath it struck and replaced, not just the heading. |
| `grata_v2_data_dictionary.md` | Listed `EQUITY_VALUE` and `SOLE_INVESTOR_AMOUNT` as examples. Stale. | **Corrected.** Leads with the shipped vocabulary; Grata's two extra values marked recommended-for-removal. |
| `grata_v2_inventory_and_recommendations.md` §D4 | Listed the M&A equity fallback and the sole-investor fallback under "Accepted waterfall", with no divergence marker. | **Status banner added**, separating the Grata spec *as supplied* from the Transactions target *as shipped*, and naming the delta. |

### 9.2 The substantive correction

The disagreement was not merely about which values a list contains. The superseded documents
explained the missing rung as **deferred for want of a source field** — "`transaction_participant`
has no per-investor amount column, so there is nowhere to store it."

That is wrong on both halves. The rung was **removed on semantics**: an investor's check is
never the event's magnitude, at any disclosure level, so no storage change would make it
correct. And the premise was false — per-investor amounts *are* storable at
`staging_investor.investment_amount` (`schema/003_funding_path.sql`), which is where the
funding prompt's per-investor asks land. **Availability was never the binding constraint.**

This matters beyond tidiness. A reader who accepted the old rationale would conclude the rung
was merely unbuilt and could be switched on once the plumbing existed. Any future revisit has
to argue the semantics instead.

One clarification also belongs here, because the original draft of this section could be read
the other way: `transaction_size` is **not implemented in Grata** — it is marked Missing/ADD in
both inventory §A5 and §D1. The `EQUITY_VALUE` and `SOLE_INVESTOR_AMOUNT` fallbacks are
therefore part of the **Grata spec as supplied**, not current Grata behaviour. The §D4 banner
uses that framing.

### 9.3 What the follow-up sweep added

Correcting the reported five surfaced more of the same defect. A sweep for text still
presenting superseded `transaction_size` semantics as current found, and `a24feb0` corrected:

| Also corrected | Defect |
| --- | --- |
| `docs/project_state.md` | Called `SOLE_INVESTOR_AMOUNT` "reserved in the vocabulary" — it is not in the vocabulary at all — and repeated the false availability rationale. |
| `docs/spec_transaction_value_model.md` §2.4 / §2.4.2 | The superseded waterfall stood as current. §2.4.2 additionally argues a disclosure-threshold case; the marker records that the rung died on **semantics**, so that section's reasoning was not what failed. |
| `docs/CONTEXT.md` | Dated snapshot listing `MINORITY_INVESTMENT` as a current `v2_event_type`, omitting `PIPE`, citing classifier 0.6 against an actual 0.8, and marking a since-resolved relevancy DRIFT. **Snapshot tables left unedited**; a banner names the drifted rows. |
| `docs/funding_path_design.md` | Header still read "Status: Design — not yet implemented"; the funding path shipped. Its §10 open question on `MINORITY_INVESTMENT` routing was dissolved by classifier 0.7, not still deferred. **Draft body left unedited**; banner added. |
| `grata_v2_inventory_and_recommendations.md` §Q | Instructed the reader to settle two rung *spellings* "before implementation starts". Both premises obsolete: implementation shipped, and both disputed rungs were removed. |
| `stages/aggregate.py` | The comment above `TRANSACTION_SIZE_BASES` claimed two reserved values, contradicting its own note fifteen lines below. |
| `scripts/test_transaction_size.py` | Module docstring contradicted the assertion in the same file, which enforces the value's **absence**. |
| `stages/deal_type_classify.py` | Comment claimed the classifier prompt "does not yet offer" `PIPE`; 0.8 does. |

All three code-file changes are **documentation-only — zero executable diff**, verified by AST
comparison with docstrings stripped, by classifying every changed line as a comment or
docstring, and by confirming no code reads `__doc__` as data in those files. Suite 36/36.

Two dated documents — `CONTEXT.md` and `funding_path_design.md` — were **banner-marked rather
than rewritten**, deliberately. Each is a faithful record of its own date, and editing the
tables would destroy that value while fixing nothing a banner does not.

### 9.4 An unresolved count discrepancy — reported, not fixed

Separate subject, and **not** resolved here. Inventory §P's introduction says "Fourteen rows
changed", but **32 rows in that table carry the ✱ marker** (26 in the v0.4 matrix, 6 more
added by the v0.4.1 MergerLinks section). One of the two is stale, and which one depends on
what the sentence was counting — several ✱ rows are two halves of one conceptual change
(`management_participation` **ADD** paired with `is_mbo`, `is_mbi` **CHANGE**, for instance),
so a count of *changes* and a count of *rows* legitimately differ.

Not corrected, because the fix depends on that intent and guessing it would replace a visible
inconsistency with an invisible one. **The ✱ markers are authoritative either way** — they
mark the rows individually, and every claim in §4 of this handoff was checked against the
marked rows, not against the summary sentence.

### 9.5 What remains

**No unresolved documentation conflict remains on the `transaction_size_basis` subject.** The
source documents agree with the code and with each other, and this handoff was written against
the corrected versions. The re-sweep's remaining hits were each triaged as legitimate: two
passages that *recommend* the strike (current, not stale), one historical corpus count, and two
relevancy **reason code** occurrences where `MINORITY_INVESTMENT` is live and correct — a
different vocabulary from `v2_event_type`.

Two things are outstanding, and neither is a documentation conflict:

**1. The downstream-prompt `MINORITY_INVESTMENT` question is an open legacy-compatibility and
design decision** (§6 item 4). It is filed that way deliberately, because two separate concerns
share a surface:

- **What the classifier may emit** — settled. Classifier 0.7 removed `MINORITY_INVESTMENT` from
  the core output vocabulary; minority is a derived flag, and
  `scripts/test_minority_core_classification.py` pins the rejection.
- **What downstream stages must still accept** — *not* settled, and not the same question.
  Legacy rows carrying the value remain in the corpus and are handled on purpose
  (`stages/aggregate.py` `_NON_CONTROL_TYPES`, pinned by
  `scripts/test_funding_value_family_gate.py`).

`prompts/strategic_rationale.md`, `prompts/aggregation.md` and `prompts/deal_summary.md` all
consume already-classified rows, so naming the value there may be **correct legacy tolerance
rather than drift**. The sources do not disagree; the question has not been decided. Treating
it as a documentation conflict and editing it away in passing would settle a live model
contract using an argument about a different layer — the exact error this section exists to
avoid. No prompt edit until it is decided.

**2. The §P count discrepancy** (§9.4) — reported, deliberately not fixed, and immaterial to
every recommendation in this handoff.

## 10. Detailed reference documents

| Document | What it holds |
| --- | --- |
| `docs/grata_v2_inventory_and_recommendations.md` | The master inventory. §A–§N per-domain field inventory; §A6/§A7 typed dimensions and derivable flags; §E4 metric policy; §O cross-cutting questions with status; §P full recommendation table with change markers; §Q MergerLinks reconciliation and §Q7 the ask list; **§R the settled Product semantics**. |
| `docs/grata_v2_data_dictionary.md` | Field-level definitions, shapes, population sources and requiredness. |
| `docs/grata_v2_reconciliation_2026_08_17.md` | The evidence base — what the harness actually built and proved, separated from what is recommended, already adequate, or deferred. Carries the Adopt/Keep/Defer list, and the two Grata asks from the transaction-size work — since **closed as stale**: our side is implemented, so they are notifications awaiting acknowledgement rather than decisions we need. |
| `docs/decisions.md` | Chronological decision record with the reasoning and the failures behind each. The place to go when a recommendation looks arbitrary. |
| `docs/project_state.md` | Current status and live backlog by owner. |
| `docs/decisions.md` §"Source-of-Truth Consistency Correction (2026-08-18)" | The sweep behind §9: what was corrected, the semantics-not-availability finding, and the prompt / legacy-compatibility item recorded as §6 item 4. |

**Where to start.** Inventory §P for the shape of the change set, §R for what is settled,
then this document's §6 for the three items that can be scheduled now.
