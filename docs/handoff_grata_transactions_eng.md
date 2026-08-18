# Transactions → Grata Data Model: Engineering Handoff

**From:** Transactions data-model assessment
**To:** Grata / Transactions Engineering
**Baseline:** `main` @ `a24feb0`
**Status:** Product/data semantics settled for this pass. Remaining work is implementation,
external definitions, or evidence-triggered validation.

This is the **front door**, not the specification. Every claim here is summarised from the
detailed documents listed in §10; drill there for field-level detail and the evidence behind
each decision.

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

**Where this leaves us.** No Product decisions are open. Eight items remain, none of them a
Product choice: three ENG implementation items (schedulable now), one prompt /
legacy-compatibility review (a decision is owed before it can be scheduled), two
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

**Standing decisions from earlier passes**, also settled: `EQUITY_VALUE_ONLY` records *debt
unknown*, never debt = 0; stake-level values are never multiple numerators; the funding
family derives no `transaction_value` or `equity_value`; PIPE is recognized but not
profiled; and a single stated qualified anchor ("over $140 million") is normalized to the
stated figure with the original wording preserved in provenance.

---

## 6. Engineering decisions / actionable work

Four items. Product has deliberately not prescribed implementation for any of them.

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
4. **Downstream prompts still enumerate `MINORITY_INVESTMENT` as a V2 event type** —
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
