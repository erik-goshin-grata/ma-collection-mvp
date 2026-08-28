# Product Validation Pass — Checkpoint & Handoff

**Date:** 2026-08-28
**Verified against:** `ma-collection-mvp` `origin/main` @ `2d1d2ef` (2026-08-28)
**Product Contract in force:** Transactions V3 — `V3-PC-1.0`
**Package baseline this pass started from:** `origin/main` @ `2e2ccb7` (2026-08-22) · schema `010`

---

## What this document is

A **checkpoint and handoff** for the Product validation pass that ran from the
`V3-PC-1.0` release to `2d1d2ef`. It records what was validated, what was
established, what was deliberately left undecided, and how to reproduce an
acceptance run.

**This is the repository freeze point for Engineering validation.** Documentation was
reconciled against the landed implementation on 2026-08-28; no implementation behaviour
moved in that reconciliation.

**It is not a specification, and it does not compete with one.** The canonical
package remains authoritative:

| Artifact | Role | Status |
| --- | --- | --- |
| `docs/v3_release_manifest.md` | release identity | authoritative |
| `docs/v3_change_decision_register.md` | one row per Product decision | authoritative |
| `docs/v3_data_dictionary.md` | current-state field contract | authoritative |
| `docs/handoff_grata_transactions_eng.md` | what Engineering must implement or resolve | authoritative |

Where this checkpoint and the canonical package disagree on a **contract**, the
package wins. This document's only independent claim is about **what has landed
in the reference implementation since the package was last reconciled**, and every
such claim below was read out of `origin/main` rather than recalled.

Of the four, only the **Data Dictionary** was touched in the closing reconciliation, and
only to bring its current-state field entries in line with what landed — which is the job
its own role statement gives it. The **Release Manifest** and the **Change & Decision
Register** are untouched: their baseline is the release identity of `V3-PC-1.0`, and
moving it is a Product act reserved for the next reconciliation (§8).

The same MVP/Engineering boundary the package sets applies verbatim here:
`ma-collection-mvp` is the **Product/MVP reference implementation** used to develop
and validate the contract. It is separate from the Engineering production
implementation. Nothing below asserts what Engineering has built, and no MVP
implementation detail is a target requirement unless the contract says so.

### How the facts here were verified

Every version, count and status was read from `origin/main` at `2d1d2ef`, not from
working-tree state or from session recollection:

- prompt versions from each prompt's own `**Version:**` line;
- stage versions from each stage's `_VERSION` constant;
- migrations from `git ls-tree origin/main schema/`;
- the deterministic suite executed from a clean clone checked out at `2d1d2ef`.

---

## 1. Acceptance evidence

### 1.1 Deterministic suite — the only result measured in this pass

**72 of 72 scripts in `scripts/test_*.py` pass at `origin/main` @ `2d1d2ef`**, executed
from a clean clone. The `V3-PC-1.0` baseline recorded 47; the pass added 25.

Four of those are cross-cutting parity checks, all green:
`test_prompt_stage_version_parity.py` · `test_response_slot_parity.py` ·
`test_reason_code_parity.py` · `test_aggregation_vocabulary_parity.py`.

The structural gate for any new or changed canonical field is unchanged from the
manifest and was applied to every field added in this pass:

```
extraction/staging → production observation writer → observation ledger
                   → configured aggregation read source → canonical transaction_record
```

with a demonstrated failure against the pre-change commit, an unchanged neighbouring
control field, the production observation field group, and the production `include_*`
path. Prompt/parser/staging-only tests do not satisfy it.

### 1.2 What this pass did *not* measure

**No new corpus run was executed in this pass, and no accuracy figure is produced by
it.** The validation evidence in `docs/v3_release_manifest.md` — the ~300-transaction
Collection-team review, the PL Relevancy 0.8 run, the 29-source PL integration run —
is unchanged, still stands, and is still **not** a statistical extraction-accuracy
measurement. It must not be quoted as one.

The manifest's standing caveat also still holds: **the 29-source integration corpus has
not been rerun** since the `ENG-V3-020` / `ENG-V3-021` remediations, and it has not been
rerun after this pass either. Confirmation is expected from the larger Collection-team
corpora, not from that run.

### 1.3 What acceptance now has that it did not

Before this pass an acceptance run was a remembered sequence of commands, reproducible
only from one machine's shell history. It is now a single command with a preflight
(§5). That is a **reproducibility** improvement, not evidence about extraction quality.

---

## 2. Contracts established in this pass

38 commits between `2e2ccb7` and `2d1d2ef`. Grouped by what each settled. Every prompt
version and migration below was read from `origin/main`.

### 2.1 Observation, reconciliation and canonical form

The pass settled a single shape for compound facts, and then applied it repeatedly:
a fact is written to the **observation ledger** under a composite `field_name`, is
**reconciled** through the generic resolver, and only then becomes **canonical**. An
unresolved conflict produces no canonical row — it does not become a second place
where disagreement is stored.

| Area | What was established | Migration | Prompt |
| --- | --- | --- | --- |
| As-reported multiples | A multiple the source itself states is captured, reconciled like every other collected fact, and written with `source_flag = 'as_reported'`. Nothing is back-solved in either direction | `012`, `013` | HC, aggregation |
| Normalized financial metrics | Source-stated revenue and EBITDA become rows carrying the currency anchored to their own amount, not the shared `financials_currency` | `014` | aggregation |
| Consideration typing | Consideration is typed by what was offered | — | aggregation |
| Value scope | Only figures that are actually deal values are captured; a currency equivalent is one fact, not a second observation | — | HC, aggregation |

### 2.2 Party cardinality and role coverage

| Area | What was established | Migration | Prompt |
| --- | --- | --- | --- |
| Cardinality | Two buyers stay two firms. `acquirers`, `buy_side_sponsors`, `parent_sellers` are arrays, one item per party; the display scalars are unchanged | `015` | HC |
| Role coverage | `PARENT_ACQUIRER` and `SPONSOR_SELLER` collected — mirrors of roles that already existed | `016` | HC |
| Financing | Financing participation separated from advice about financing; `advisor_specialty = financing_advisory` does not establish a financing provider | `017` | LC |
| Naming | `LENDER` → `FINANCING_PROVIDER`: the participation collected is broader than the instrument the old name asserted | `018` | LC |
| Seller | `SELLER` — the party actually disposing. A parent seller is not a substitute for a seller; owning is not selling; the target is not automatically the seller | `019` | HC |

### 2.3 Evidence discipline

| Area | What was established |
| --- | --- |
| `pct_acquired` | The assumed 100 is removed. Unstated is `None`, and `None` means the source did not say. Control-deal branch selection now takes a control boolean rather than a percentage — the same condition, without the invented figure |
| Structural target typing | The target is typed by what is transacted, not by how the deal is worded |
| Sale process | A search for a buyer is not a transaction |
| Disclosure | The target's operating financials and the deal's terms are disclosed independently, and are recorded on two axes rather than one. §3 |
| Buy-side coherence | HC no longer asserts a buy side that contradicts itself |
| Use of proceeds | Bounded to a vocabulary rather than free text, so it can be aggregated and compared |

### 2.4 Product visibility

`scripts/run_collection_validation.py` runs the real decision chain
(`source → Relevancy → Classifier → extraction path → downstream`) in an isolated
database and emits two review sheets plus a rejection list. Sheet version and column
counts are pinned by tests so a column set cannot drift silently.

**A consequence worth stating plainly:** `pct_acquired` no longer being assumed means a
control deal that states no percentage now yields no `implied_equity_value`, no implied
EV by that route, and no calculated multiple. Those figures rested on the assumption.
This is a deliberate loss of invented precision, not a regression.

---

## 3. The final Product correction — two disclosure axes

**Two independent disclosure axes** — the final correction from the acceptance review.
One field was carrying two questions: `financials_disclosure_status` was asked to
classify the *deal's* terms while the target model defines it as the disclosure state
for the *target's* operating financials. `transaction_terms_disclosure_status` separates
them, on the same three-value vocabulary.

**Landed on `origin/main` as `2d1d2ef` (2026-08-28).** Migration `020`; HC 0.36,
funding HC 0.7, aggregation 0.13, deal summary 0.17; review sheet 1.3. The versions in §4
are post-slice.

Both axes take the same vocabulary — `DISCLOSED · UNDISCLOSED · UNKNOWN`. `DISCLOSED`
means at least one relevant fact **on that axis**, never completeness; `UNDISCLOSED`
requires the source to say so; silence is `UNKNOWN`. A mixed answer is the common case in
either direction. `PARTIALLY_DISCLOSED` was deliberately not added, and
`value_type = UNDISCLOSED` is untouched.

The two documentation statements this slice falsified were corrected in the
reconciliation that follows it, not left owed: `docs/v3_data_dictionary.md` no longer
describes the field as one the reference implementation does not carry, and
`scripts/run_collection_validation.py`'s docstring no longer lists it as unavailable.
`seller_sponsor` stays on that list — `transaction_record` still carries no sell-side
sponsor name, only `sell_side_sponsors` at staging.

---

## 4. Version and migration state at `origin/main` @ `2d1d2ef`

| Prompt | Version | Stage `_VERSION` |
| --- | --- | --- |
| `relevancy_filter` | 0.9 | 0.9 |
| `deal_type_classifier` | 0.16 | 0.16 |
| `high_confidence_extraction` | 0.36 | 0.36 |
| `funding_hc_extraction` | 0.7 | 0.7 |
| `low_confidence_extraction` | 0.13 | 0.13 |
| `aggregation` | 0.13 | 0.13 |
| `deal_summary` | 0.17 | 0.17 |
| `strategic_rationale` | 0.6 | 0.6 |
| `agreement_*` (5) | 0.2 / 0.3 | — |
| `prompt_conventions` | 0.5 | n/a (convention document) |

Migrations `001`–`020`. Sentinel-guarded and hand-registered in `db.py::_apply_migrations`;
**the directory is not globbed, so a migration without a guard block never runs.**

---

## 5. Reproducible acceptance-runner workflow

```
python scripts/run_acceptance.py --urls acceptance_urls.xlsx --out out/acceptance_20260828
```

`scripts/run_acceptance.py` is **orchestration and nothing else**. It fetches nothing,
extracts nothing, and decides nothing about pages. Three tools already do the work and it
calls them in order:

| Step | Tool | Role |
| --- | --- | --- |
| 1 | `tools/page_harness.py` | fetches each URL, writes a capture directory |
| 2 | `tools/analyze_run.py` | diagnoses the capture fleet |
| 3 | `scripts/run_collection_validation.py` | seeds and runs the pipeline in URL-only mode |

**Inputs.** `--urls` takes a `.txt` (one URL per line) or an `.xlsx` — the acceptance
corpus lives in a workbook, and an Excel-to-text step done by hand before every run is a
step that will eventually be done wrong. The workbook is read with `zipfile`, adding no
dependency; that is how this repo already handles xlsx.

**Preflight, before a single page is fetched.** A model-auth check resolves the key
through the repo's own `get_config()` and verifies it with the cheapest authenticated
endpoint. It exists for a specific failure: `config.py` requires the key to be non-empty
and passes it explicitly, so a **stale key passes configuration, overrides whatever route
was intended, and then 401s at the first model call — after every page has been fetched**.
`--skip-auth-check` proceeds anyway. No credential behaviour is invented.

**Boundaries that are deliberate, and should stay deliberate if this is ported:**

- **The feeder owns the healthy/quarantine gate.** `ok and not suspect` lives in
  `load_url_captures`, and the runner *imports that function* rather than deciding for
  itself which captures are good. Two copies of that rule would eventually disagree.
- **The harness's exit code is reported, not obeyed.** It exits 1 when *any* URL fails,
  which is right for the harness and wrong as a gate here: one blocked page must not
  strand thirty good ones. The run stops only when **no** capture is usable — and it
  stops before the pipeline, not after paying for it.
- **A completed run is not destroyed.** An existing `collection.db` means a finished run;
  that is refused by default. `--force` archives prior outputs to
  `<out>/superseded-<stamp>/` — nothing is deleted.
- **Quarantined captures come back re-feedable.** `failed_urls.txt` is plain URLs with
  reasons as comments, so it can be handed straight back to `--urls`.

**Outputs.** `ma_review.csv`, `funding_review.csv`, `relevancy_rejections.csv`, plus a
run manifest carrying `review_sheet_version` so a sheet can be tied to the columns that
produced it.

---

## 6. Deliberately deferred — Product / company decisions

**None of these is Engineering work.** A `TABLED` item is parked on purpose; an `OPEN`
item has no Product position to build against. They are listed so they stay visible, and
they must not be sized or scheduled alongside `CURRENT` work.

### 6.1 Carried forward from `V3-PC-1.0`, unchanged by this pass

`TABLED` — Strategic Rationale representation (`§R7 + §R9 + §S2.1`) and the unanswered
rationale-**owner** question · rationale evidence excerpts (`ENG-V3-016`) · canonical
casing / read-tolerance cleanup (`ENG-V3-012`).

`OPEN` — QIP treatment (`ENG-V3-015`) · rumor intake vs the `RUMORED` event-history path
(`ENG-V3-014`) · derived-source tier for digest decomposition (`ENG-V3-009`) ·
entity/domain linking (`ENG-V3-010`) · SEC/source tiering (`ENG-V3-013`) · researcher
amendment and recomputation semantics (`ENG-V3-011`).

Re-verified against `origin/main`: the three structure-derived rationale defaults are
still live in `prompts/strategic_rationale.md`, and `secondary_rationales` is still a
bare JSON array that cannot carry per-item attribution.

### 6.2 Newly deferred in this pass

| Item | Position taken | Why it is not closed |
| --- | --- | --- |
| **`PARTIALLY_DISCLOSED`** | Deliberately **not** added on either disclosure axis | The Grata baseline records it and the reference implementation carries a three-value subset. The reconciliation is genuinely open, and adding a fourth value on the strength of an open question would freeze the answer. `docs/v3_data_dictionary.md:133-134` |
| **`value_qualifier` reaching the canonical row** | Not built | Verified: `value_qualifier` exists on `staging_extraction` and in the ledger, and `transaction_record` has **no** `*_qualifier` column at all. A researcher convention was adopted instead — a single stated figure under a lower-bound qualifier normalizes to the stated anchor — and it is a convention about what the canonical record holds, not a claim about what the source said. Adding a review column would have implied a chain that does not exist |
| **Balance-sheet normalized metrics** | Deferred | `transaction_financial` carries `REVENUE` and `EBITDA` only. Debt and cash are collected but not normalized into rows |
| **Calculated multiple rows** | Not written | Only `source_flag = 'as_reported'` rows are written. The four flat `ev_to_*` columns are computed and exported exactly as before |
| **`JV_PARTNER`, `UNDERWRITER`** | Stay unauthored | The target model lists them and defines neither. Authoring a role with no qualifying test would mean inventing it. Recorded openly in the dictionary: a party underwriting a financing commitment may fall inside `FINANCING_PROVIDER`, which would make `UNDERWRITER` narrower than it appears |
| **Per-share value destination** | No destination ruling made | Raised during the `pct_acquired` work and deliberately left to its own slice |
| **`value_type = UNDISCLOSED`** | Untouched | It remains an affirmative signal on the value axis. Redesigning it against the new two-axis model belongs in its own change, not in the change that introduced the second axis |

---

## 7. Engineering-owned responsibilities

Unchanged by this pass and restated for the handoff, not re-decided.
`docs/handoff_grata_transactions_eng.md` §6 and the Register section D remain
authoritative; each item below was re-verified present at `origin/main`.

**Schedulable (5).** `ENG-V3-001` reconciliation / supersession key ·
`ENG-V3-002` Silver/Gold physical placement meeting the nine `§R6` invariants ·
`ENG-V3-003` `PER_SHARE_X_SHARES` share-count wiring · `ENG-V3-004` `rationale_basis`
schema and placement · `ENG-V3-005` durable rationale evidence attribution.
(`ENG-V3-006` is the sixth item in that section but is **TABLED**, blocked on
`§R7 + §R9 + §S2.1`, and is therefore not schedulable.)

**Alignment, not a build list.** `ENG-V3-008` participant / entity representation.
The target requirement is to preserve the actual participant entities and their roles,
including lead/primary designation where the source establishes it, and **not** to
materialize a synthetic consortium entity. The MVP's `consortium` residue is prototype
leftover and **must not be propagated**.

**A decision is owed before any edit (1).** `ENG-V3-012` — downstream prompts still
enumerate `MINORITY_INVESTMENT`. Naming it may be correct *legacy tolerance* rather than
drift; what the classifier may emit is settled, what downstream stages must still accept
is not.

**Prompt-contract discipline — the standing methodological finding.**
`prompts/base.py::load_prompt_file` delivers **only** the §4 and §5 fences. Anything
outside them is documentation and reaches no model, so a test asserting on the Markdown
certifies nothing about model behaviour. `test_reason_code_parity.py` returned 24 == 24
for the prompt's entire history while the model was shown none of the codes.
**Prompt-contract tests must read the delivered string.** `ENG-V3-025` is a live instance:
the Deal Summary take-private framing rule sits in §3, outside the delivered fences.

A structural corollary was added during this pass and is worth carrying: **a key a stage
*requires* must be named in the text that stage *delivers***. A stage can otherwise be
made to require a field its own prompt never asks for — a validator that rejects every
extraction, with a fully green suite, because nothing tied the two together.

---

## 8. Documentation drift — what was corrected, and what was deliberately not

Every row was verified against `origin/main` @ `2d1d2ef`.

**Corrected**, because the true value is mechanical and readable from the repository:

| Document | Drift, and the correction |
| --- | --- |
| `docs/prompt_versions.md` | Its Current State table had gone stale on six prompts — it carried HC 0.24, funding HC 0.4, aggregation 0.6, relevancy 0.8, classifier 0.14, LC 0.11. Now read from the prompt headers and the stage `_VERSION` constants. **Why it drifted:** `test_prompt_stage_version_parity.py` asserts prompt ↔ stage parity and does not read this document, so nothing holds the table to the repository — it can drift again, and the document now says so |
| `docs/v3_data_dictionary.md` | The two disclosure axes described the terms axis as one the reference implementation did not carry, and inherited an open `PARTIALLY_DISCLOSED` question into the field vocabulary. Both axes now carry their landed definitions and the three-value vocabulary, with the `PARTIALLY_DISCLOSED` reconciliation moved to the open-items list where it belongs. `use_of_proceeds` is no longer described as unauthored by the funding path |
| `scripts/run_collection_validation.py` | Its docstring listed `transaction_terms_disclosure_status` as unavailable and neither proxied nor invented. It is now captured and canonical. `seller_sponsor` stays on that list, verified: `transaction_record` carries no sell-side sponsor name |

**Deliberately not corrected:**

| Document | Why it is left alone |
| --- | --- |
| `docs/v3_release_manifest.md` · `docs/v3_change_decision_register.md` | Their baseline block (`2e2ccb7` / schema `010` / 47 tests) is the **release identity of `V3-PC-1.0`** — a bill of materials for a dated release, not a claim about today. Moving it would mint a new contract version, which is a Product act and not documentation reconciliation. They also carry no row for the decisions settled in this pass; adding rows and statuses is the reconciliation's job, not a docs sweep's |
| `docs/project_state.md` | Already banner-marked as a dated 2026-08-14 snapshot and accurate as labelled |
| `docs/change_log.md` · `docs/*handoff*` · `docs/session_handoff_*` · `docs/grata_v2_*` · `docs/decisions*.md` | Historical records and superseded documents. Their values are correct **as of their own dates**, and rewriting them would destroy the record this package explicitly preserves as the authority for *why* |

The register's own versioning rule governs what happens next: the next reconciliation
increments to `V3-PC-1.1` (statuses move) or `V3-PC-2.0` (a settled decision is reversed).
**Nothing in this pass reverses a settled decision**, so `V3-PC-1.1` is the expected
increment, and the manifest's baseline moves there.

---

## 9. What this checkpoint does not do

- It does **not** restate the field contract. `docs/v3_data_dictionary.md` does that.
- It does **not** replace decision history. `docs/decisions.md`,
  `docs/grata_v2_inventory_and_recommendations.md` (§T/§R/§A/§P) and
  `docs/v3_slice_reconciliation.md` remain the authority for **why**.
- It does **not** mint identifiers, renumber anything, or move any Product status. The
  decisions listed in §2 are recorded here as landed implementation; giving them Register
  rows and statuses is the next reconciliation's job.
- It does **not** decompose Engineering work, and the Register's `Jira` column stays blank
  by design.
- It asserts **nothing** about the Engineering production implementation.
