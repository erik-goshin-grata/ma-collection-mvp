# Session handoff — field coverage, value-model evidence, Grata memo framing
**2026-08-12.** Written for a cold pickup by a different tool and a different model family.

This document is self-contained on **framing and decisions taken in conversation**, because those
exist nowhere else. It **points rather than restates** for anything in the repo — restating the
repo is how this project has repeatedly produced confident, wrong claims.

---

## Read first, in order

1. `docs/decisions.md` — authoritative. Entries dated 2026-08-10 and 2026-08-11.
2. `docs/workorder_code_2026_08_11.md` — phase 1, steps 1–8, with guards and preconditions.
3. `docs/session_handoff_2026_08_10_value_model.md` — the value model and what preceded this.
4. `docs/spec_transaction_value_model.md` — elaborates the decisions. Conflicts resolve to
   `decisions.md`.

Source-of-truth order: `decisions.md` > spec > handoffs. Nothing lands until they agree.

---

## Working agreement — carry this over

Not optional. It exists because seven claims in an earlier session were asserted confidently,
endorsed downstream, and later found false — including a citation to a document not in the repo
and a table that does not exist.

- **Provenance tags on every factual claim:** `[verified: path, date]` / `[unverified — confirm]`.
- **Two roles.** ChatGPT authors design documents and decision/memo language; Codex lands code,
  runs operations, and maintains `project_state.md`. Two roles do not edit the same file in the
  same pass. This split caught real errors in both directions during this session and is worth
  preserving.
- **Don't cite line numbers in durable documents.** They drift. Quote the text.
- **The repo is authoritative.** Older project-knowledge copies of prompts are stale.
- **One workstream per session.**
- **Verify, don't infer.** Several findings this session were "obviously" one thing and turned out
  to be another. Cheap checks beat confident reasoning; where a pristine snapshot or a two-line
  script can settle it, use it.

---

## Three phases — where we are

**Phase 1 — put our own house in order.** Complete for the first §4.2 re-aggregation; one later
operational re-aggregation remains after `total_debt` + `Cash_ST`.
**Phase 2 — run fresh deals to confirm, and produce evidence.** Not started.
**Phase 3 — the Grata enum/schema suggestion.** Framed, not written.

Phase 3 does not start until 1 and 2 are done. That sequencing was deliberate: phase 3's argument
depends on evidence phase 2 produces.

---

## Phase 1 status

Steps 1–7 complete and committed for the two live DBs.

- **Step 7 ran on `pl_funding.db` and `ma_mvp.db`**. Both routed through `init_db`, asserted the
  new columns, reset `AGGREGATED` rows to `CLUSTERED` with row-count checks, and re-ran real Stage
  9. Snapshots include `data/pl_funding_pre_step5.db`, `data/pl_funding_pre_step7.db`,
  `data/pl_funding_pre_fix.db`, and `data/ma_mvp_pre_step7.db`.
- **The first §4.2 re-aggregation is discharged on the live DBs.** `ma_valu8.db` /
  `ma_grata.db` remain deferred because they are control-heavy fixtures, not live reads.

**`pl_funding.db` is a PredictLeads funding source experiment, not live data.** It was
re-aggregated anyway and the snapshot makes that harmless, but the governing principle is:
**re-aggregation is remediation of live data, not validation of code.** To exercise a code path,
write a test.

---

## Resolved blocking item — a decision violation found in the step-7 diff

**`implied_equity_value` was being grossed up from an unqualified `transaction_value`.**
`decisions.md` — "Transaction Size as Universal Magnitude" — forbids this explicitly: an
unqualified source figure populates `transaction_value` and `transaction_size` only, and must
never populate equity value or either implied value, because grossing up an unqualified number
manufactures a figure no source ever qualified.

Observed on `pl_funding.db`: KG Mobility `tc_616c` and TC Skyward `tc_2621` both have
`equity_value = NULL`, `transaction_value` with basis `STATED`, and a populated
`implied_equity_value` equal to `transaction_value / pct_acquired`. TC Skyward's 3.892B is a
number no source stated. `[verified: pl_funding.db post-step-7 — 2026-08-11]`

**Why it matters beyond the two rows:** `implied_equity_value` is one of the two legal multiple
numerators. No multiples exist in that DB, so nothing has computed off it — the guard held because
there was no denominator, not because the input was sound.

Resolved in `065a87d`: `implied_equity_value` now derives from `equity_value` only. The corrective
`pl_funding.db` re-aggregation nulled 9 manufactured implied values (2 tv-fallback + 7 post-money
branch values) and preserved 3 legitimate equity-grossed values. `ma_mvp.db` ran after the fix; it
prevented the Genesis tv-fallback value and preserved 3 legitimate equity-grossed values.

---

## Other open items

- **KG Mobility is double-counted.** Two `transaction_record` rows for the same Chery 10% deal from
  two source articles that failed to cluster (Stage 8). Independent of the value model; affects any
  count or aggregate. Log and address separately.
- **Same figure classified two ways.** The two KG rows routed the same 75M into `equity_value` on
  one and `transaction_value` on the other. This is the motivating case for the "Named Value Fields
  Replace the Single Value Slot" decision — **decided, unmigrated** — appearing in real data for
  the first time. An accidental controlled experiment, courtesy of the duplicate.
- **Pre-existing observation-path conflict delta.** The observation loader resolves ~10 more LLM
  conflicts than staging, on six HC descriptive fields. Verified pre-existing against the pristine
  snapshot, so it does not trace to this work. It remains a latent-divergence note for any future
  decision to switch `AGGREGATION_READ_SOURCE` to `observation`: identical outputs reached through
  more LLM adjudication is not equivalence.
- **`total_debt` exists on no DB.** The §4.2 calculated debt branch has never fired against real
  data — `transaction_value_basis` values present are `STATED` and `EQUITY_BELOW_CONTROL` only,
  never `EQUITY_PLUS_TOTAL_DEBT`. This is concrete proof the second owed re-aggregation (after
  `total_debt` + `Cash_ST` extraction) is substantive, not a formality.
- **Step 7 is not exactly reproducible.** A full reset re-derives every cluster, invoking real
  Opus conflict adjudication. Another re-run can give another answer. Noted so nobody attempts one
  as a casual sanity check.

---

## Phase 2 — design

Runs fresh announcements end to end. **This is the only thing that tests the extraction layer** —
everything in phase 1 exercised aggregation, re-deriving from staging rows that already existed.

Specifically unproven until this runs: Stage 4b's live dual-write (backfill and a stubbed-LLM test
are not the same thing), and `round_currency`, which has never held a value from a real source.

**Choose the corpus; do not take recent news.** Ten to fifteen announcements:

1. **A non-USD funding round**, ideally with the raise and the valuation in different currencies.
   The only way to exercise `round_currency` end to end, and the long-deferred material for
   measuring what a third currency source does to the `deal_value_currency` null rate.
2. **A minority stake with a disclosed value** — the case that carries phase 3's argument.
3. **A control acquisition with disclosed equity and no debt figure** — should yield
   `transaction_value` NULL, confirming "do not assume debt = 0" fires rather than quietly zeroing.
4. **A funding round with a stated post-money** — confirms `equity_value` stays vacant and no
   multiple computes.

**Capture inputs and outputs per case as they run.** That record is phase 3's evidence base and is
far cheaper to keep than to reconstruct.

**Open question worth deciding at the time:** a corpus chosen to confirm the value model will
mostly show enums that fit. Finding where our vocabulary *fails* — carve-outs, asset sales,
converts with a valuation cap, multi-currency deals — is a different selection criterion and may
argue for a second small corpus.

---

## Phase 3 — the Grata memo

### What this is

`enums.py` and `schemas.py` belong to Grata — `grataio/datawarehouse`,
`DBX/data_financial_transaction/code/`. **They are not in this repo.** They arrived as uploads on
2026-08-11 and a new session will need them re-supplied.

**This repo is an LLM-extraction testbed, not the eng repo.** Its decisions are not requirements on
Grata's model, and Grata's schema is not a specification this pipeline must satisfy.

**`docs/enum_schema_gaps.md` (July 24) was our own suggestion to Grata, not their document.** They
acted on most of it: `EventCategory`, `RecapType`, `SpinSplitType`, `DistributionMechanism`,
`FinancialsDisclosureStatus`, `ConsiderationType`, `RoundStageCategory`, `RecordReviewStatus`,
`PeriodType`, `DatePrecision`, `PartyType`, `EntityResolutionStatus`, `AdvisorSpecialty`,
`AdvisedParty`, `LenderRole`, `PartySource`, `NumeratorValueType`, `MultipleSourceFlag`,
`MultipleQuality` are all now defined; `PartyRole` went 4 → 11; all five missing `MetricType`
members were added. **Round 1 worked, which raises the bar for round 2.**

### Structure — settled in conversation, exists only here

**Primary axis: Grata's enum file, top to bottom.** Someone opens `enums.py` and works down it.
July organized by table and produced 14 questions that mostly sat; the axis was not the problem,
the missing part was what to *do*.

**Every entry carries two tags.**

- **Action** — `ADD VALUE` / `RESTRICT` / `RENAME` / `NEW ENUM` / `CONFIRM INTENT`.
- **Evidence** — what in the testbed produced it: a deal that came out wrong, a field with nowhere
  to land. **An entry without evidence is preference and gets cut.**

**Four entry types.** Blurring them is what makes a memo unreadable.

- *Incomplete* — no home exists for something real.
- *Incorrect* — the model as written produces a wrong number.
- *Divergent* — we chose differently; they should know; not a defect.
- *Supersedes our July recommendation* — **most likely to confuse, so flag hardest.** These read as
  contradiction unless the reversal is explained.

**Exclusion list up front, with reasons.** Probably the single most valuable page, because it stops
the next round repeating this one:

- **`_v2` shadow columns** — local migration artifact. Zero occurrences in Grata's files. They are
  our half-finished expand-and-contract toward V2 vocabulary, nothing more.
- **Company-fact vocabularies** (`acquirer_type_v2`, `investor_type`) — entity resolution owns
  these in Grata's world; `PartyType`'s own docstring says it is derived from the canonical company
  profile. We extract them only because this MVP has no company data. **July's failure was listing
  these as gaps in Grata's model.**
- **Operational plumbing** (`source_type`, `source_tier`, `sec_lookup_status`,
  `agreement_extraction_status`, `observation_source_stage`, run modes, `failure_type`).

**The transaction-fact / company-fact split is the organizing test.** Only transaction facts belong
in the memo. Note the asymmetry: `acquirer_type` is a company attribute and is out; **target-object
type is a transaction attribute and is in** — whether a buyer took a whole company, a division, or
assets is not in any company profile.

### Entries with evidence so far

- **`NumeratorValueType` — RESTRICT, incorrect, supersedes July.** Currently
  `enterprise_value | equity_value`, which is verbatim our July proposal. The two-tier model says
  the opposite: as-transacted values must never be multiple numerators, so the values should be
  `implied_enterprise_value | implied_equity_value`. Evidence: the 27%-for-270-against-500-of-debt
  case producing 5.1x against a true 10.0x, now reinforced by the manufactured-implied-value
  finding above — a figure nobody qualified is currently numerator-eligible.
- **`MetricType.ENTERPRISE_VALUE` — REMOVE, supersedes July.** Stake-level enterprise value
  corresponds to no economic quantity. All routes converge on `IMPLIED_ENTERPRISE_VALUE`.
- **Implied values need a basis stamp — incomplete.** `FINANCIAL_METRIC_SCHEMA` carries
  `is_calculated`, one boolean, collapsing three distinct states: source-stated, grossed up from a
  *qualified* figure, and grossed up from an *unqualified* one. The third is manufactured. Evidence:
  TC Skyward. Use `_basis` (which rung fired, including `STATED`), not `_method`.
- **No event-type gate on multiples — incomplete.** Nothing in `TRANSACTION_MULTIPLE_SCHEMA` or
  `MultipleType` prevents a funding round carrying one. Our gate held on real data and the
  structural reason — no implied equity for funding, therefore nothing to compute from — is
  confirmed rather than asserted.
- **Consideration decomposition — incomplete, and an addition rather than a correction.** Our
  agreement prompt yields `consideration_components[]` — `{form, per_share_amount, currency,
  exchange_ratio, trigger_description, election}`. Grata collapses this to one `ConsiderationType`
  scalar plus `per_share_price` plus `has_earnout` / `has_cvr` / `is_stock_for_stock` booleans;
  `consideration_component` is deferred. Exchange ratio, per-form breakdown, election mechanics and
  trigger terms have nowhere to land. **The `form` vocabulary — `CASH`, `ACQUIRER_STOCK`, `CVR`,
  `EARNOUT` — is undeclared on both sides**, existing only inside JSON examples in our prompts. A
  clean `NEW ENUM` candidate rather than a design exercise.
- **Target-object type — incomplete.** `standalone_company | subsidiary | business_unit | assets |
  spinco` has no counterpart in `enums.py`. Sits behind Grata's own open question #5 (do asset sale
  / business unit / carve-out warrant a distinct `event_category`).

### Where we adopt from them

**Named value fields.** Grata's silver header carries named scalars — `deal_value`, `reported_ev`,
`amount_raised`, `post_evaluation` — pivoting into `financial_metric` rows in gold. No single
classify-or-lose slot. Our `value.amount` + `value.type` pair is the outlier, and the KG duplicate
demonstrated the cost: same deal, same figure, two articles, two different routings. Saying this
plainly in the memo is worth more than it costs — it buys credibility for the entries that push
back.

### Still open from July, unactioned

#4 (silver `reported_ebitda` / `reported_revenue` still period-untagged — `PeriodType` exists but
the silver header has nowhere to put it), #5, #6, #7 (no multiple display precedence rule), #14
(`RecordReviewStatus` exists as an enum; the field is still absent from
`TRANSACTION_RECORD_SCHEMA`, as its own docstring notes). `deal_status`, `acquirer_type`, headline
denormalization, and the hostile / bankruptcy / termination-fee families remain absent.

---

## Immediate next actions

1. Build the review export value-model surface: expose current value-model and funding fields
   without exporting `_v2` shadow columns as Grata-facing enum fields.
2. Review relevancy reason codes together with the minority-as-flag draft: public minority deals,
   VC vs growth breakout, and whether reason codes should remain source-level hints rather than
   event taxonomy.
3. Phase 2 corpus design, then the run.
4. Phase 3 Grata memo after phase 2 produces evidence.
5. Track KG Mobility as a Stage 8 clustering miss separately from the value model.

Also outstanding, lower priority: the field parity test
(`docs/spec_field_parity_test.md`, checks 2–4). **Its ON HOLD gate is lifted** — those checks test
this repo's internal consistency and never depended on Grata. Phase 1 fixed the current instances
by hand; the test is what stops them recurring after a future migration `004`.
