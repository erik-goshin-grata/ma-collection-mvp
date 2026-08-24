# Transactions V3 — Product Contract Release Manifest

**Product Contract:** Transactions V3 — `V3-PC-1.0`
**Status:** CURRENT · **Reconciled:** 2026-08-22 · **Supersedes:** — (initial release)
**MVP reference baseline:** `ma-collection-mvp` `origin/main` @ `2e2ccb7` · **Schema:** `010_v3_take_private_outcome.sql`
**Package:** Release Manifest · Change & Decision Register · Data Dictionary · Engineering Handoff

*Release identity and bill of materials. Not a specification.*

---

## Implementation context — read before any status in this package

`V3-PC-1.0` is the Product contract for the **target** Transactions V3 model.
**`ma-collection-mvp` is the Product/MVP reference implementation** used to develop and
validate that contract, and it is **separate from the Engineering production
implementation**. Prompt versions, migrations, tests, validation evidence and every reference
to implemented behaviour in this package describe the **MVP** unless explicitly stated
otherwise. The Engineering Handoff identifies adoption and alignment considerations for the
Engineering implementation; it is not an audit of what Engineering has built.

The MVP's job is to prove the contract is expressible and behaves as specified. It does not
prescribe Engineering's design, and MVP implementation detail is not a target requirement
unless the contract says so.

## What `V3-PC-1.0` is

The first consolidated Product contract for Transactions V3. Everything dated before this
release is working/reconciliation history; `V3-PC-1.0` is the line Engineering should build
against.

Until now the documentation stopped at the six-slice reconciliation. Two further slices
(S-G, S-H), two validation exercises on PredictLeads traffic, and roughly a dozen Product
rulings had landed in code with no documentary record. This release closes that gap. It does
not redesign anything: every semantic here was already settled, implemented, or explicitly
tabled.

## Versioning model

One shared Product Contract version covers the Product/data-model documentation. It does
**not** replace the version lines that already exist and are not merged into it:

| Line | Owner | Current |
| --- | --- | --- |
| **Product Contract** | this package | `V3-PC-1.0` |
| Prompt versions | each prompt, independently | see baseline below |
| Schema migrations | `schema/` | `010` |
| Decision IDs | `§T`, `§R`, `§A`, `§P`, `§S` | unchanged, never renumbered |
| Slice IDs | `S-A` … `S-H` | unchanged |

`V3-PC-1.0` deliberately starts a new number line rather than continuing the `v0.4.1` used
by the V2 inventory and data dictionary. Those belong to the V2 baseline this release
supersedes; reusing their number would imply continuity that does not exist.

## Canonical documentation artifacts

| # | Artifact | Role |
| --- | --- | --- |
| 1 | `docs/v3_release_manifest.md` | this page — release identity |
| 2 | `docs/v3_change_decision_register.md` | one row per Product decision; the Jira-facing surface |
| 3 | `docs/v3_data_dictionary.md` | authoritative current-state field contract |
| 4 | `docs/handoff_grata_transactions_eng.md` | what Engineering must implement or resolve |

Reading order: **Manifest → Register → Data Dictionary / Engineering Handoff → detailed
decision history.**

### Historical / source record — preserved, not superseded in substance

`docs/decisions.md` · `docs/grata_v2_inventory_and_recommendations.md` (§T/§R/§A/§P) ·
`docs/v3_slice_reconciliation.md` · `docs/grata_v2_data_dictionary.md` ·
`docs/spec_transaction_value_model.md` · `docs/funding_path_design.md`

These remain the authority for **why** a decision was taken. This package is the authority
for **what is true now**. Where they disagree on current state, this package wins; where
this package is silent on reasoning, they are the record.

## MVP reference — prompt-version baseline

| Prompt | Version | Prompt | Version |
| --- | --- | --- | --- |
| `relevancy_filter` | 0.8 | `deal_summary` | 0.16 |
| `deal_type_classifier` | 0.14 | `strategic_rationale` | 0.6 |
| `high_confidence_extraction` | 0.24 | `agreement_recitals` | 0.3 |
| `funding_hc_extraction` | 0.4 | `agreement_consideration` | 0.2 |
| `low_confidence_extraction` | 0.11 | `agreement_capitalization` | 0.2 |
| `aggregation` | 0.6 | `agreement_termination` | 0.2 |
| | | `agreement_conditions` | 0.2 |

`prompts/prompt_conventions.md` 0.5 is a convention document, not a delivered prompt.
Each stage's `_VERSION` constant matches its prompt; parity is asserted by
`scripts/test_prompt_stage_version_parity.py`.

The drafted Funding LC prompt never became an executable contract and is **not** part of
this inventory. The funding path uses specialized Funding HC plus the shared
deal-type-agnostic LC stage; no separate Funding LC stage is required. The draft is
retained at `docs/historical_funding_lc_extraction_prompt.md`.

## MVP reference — schema / migration baseline

`001_initial` · `002_v2_prompt_alignment` · `003_funding_path` · `004_v3_attitude_approach` ·
`005_v3_combination_structure` · `006_v3_asset_type` · `007_v3_offer_mechanism` ·
`008_v3_funding_round` · `009_v3_sponsor_transaction_role` · `010_v3_take_private_outcome`

Migrations are sentinel-guarded in `db.py`; the directory is not globbed, so a migration
without a guard block never runs.

## MVP reference — deterministic-test baseline

**47 scripts in `scripts/test_*.py`, all passing at this baseline.**

The structural gate for any new or changed canonical field is unchanged:

```
extraction/staging → production observation writer → observation ledger
                   → configured aggregation read source → canonical transaction_record
```

with a demonstrated failure against the pre-change commit, an unchanged neighbouring control
field, the production observation field group, and the production `include_*` path.
Prompt/parser/staging-only tests do not satisfy it.

## Validation evidence — all from the MVP reference

Recorded as evidence of pipeline behaviour. **None of it is a statistical extraction-accuracy
measurement, and it must not be quoted as one.**

| Exercise | What it covered | What it establishes |
| --- | --- | --- |
| ~300-transaction prompt validation | manually reviewed by three Collection teams | Product-level review of extraction output at scale |
| Deterministic regression suite | 47 scripts | structural correctness of the canonical path |
| PL Relevancy 0.8 run | 936 event rows → 746 unique sources → 745 parseable outputs + 1 multi-event truncation case | Stage-1 behaviour on natural PredictLeads traffic |
| PL 29-source integration run | 29 sources → 31 staging extractions → 26 canonical transactions | end-to-end behaviour of the real transaction path |
| — clustering | expected multi-source groups **3/3** | no unexpected merges or splits |
| — decomposition | one-source/multiple-transaction worked for INVL and MPS | multi-transaction envelope is real |
| — fact loss | **zero** observed-non-null → canonical-NULL | the ledger→canonical path loses nothing |

Also part of the validation history, recorded without statistical claim: **targeted
Gate/boundary testing** performed throughout the V3 slice work — structural Gate 1 proofs for
every new canonical field, and Gate 2 boundary cases on real source text.

**Outcome: successful validation with two blockers found and subsequently remediated on
`main`** — take-private semantics (`ENG-V3-020`) and Deal Summary funding transport
(`ENG-V3-021`). Both were found by reading artifacts, not by the exception detector.

**The 29-source integration corpus was not rerun after those fixes.** The remediations carry
targeted deterministic and production-path regression evidence — a demonstrated pre-change
failure in both directions, mutation coverage, and the full chain from staging through the
observation ledger to canonical. Broader post-change confirmation will come from the upcoming
larger Collection-team corpora, not from this run.

One non-blocking finding remains open for quantification in the larger validation corpora: a
partial same-sentence HC extraction miss (revenue captured, adjusted EBITDA missed from the
same sentence). It is an extraction-quality data point, not a contract defect.

**Not in scope for this release:** PL export optimization. That is separate tooling work.

## Tabled and open areas

Carried explicitly so they cannot be mistaken for settled. Full detail in the Register.

**TABLED** — Product decided not to decide now: Strategic Rationale representation
(§R7 + §R9 + §S2.1) and the unanswered rationale-owner question · rationale evidence excerpts ·
canonical casing / read-tolerance cleanup.

**OPEN** — raised, never adjudicated: QIP treatment · rumor intake vs the `RUMORED`
event-history path · derived-source tier for digest decomposition · entity/domain linking ·
SEC/source tiering · researcher amendment and recomputation semantics.

**Prototype residue, not a Product question.** The target model represents the actual
companies, investors and participants, with lead/primary designation where established; it
does not materialize a synthetic consortium entity. Product and Engineering are aligned on
this and always have been. The `consortium` vocabulary value and the synthetic `CONSORTIUM`
group still present in the prototype implementation are **not part of `V3-PC-1.0`** and are
retired as part of the participant/entity work (`ENG-V3-008`). See the Data Dictionary appendix.

## Superseded and retired

**Superseded documents** — retained with a banner, content unchanged:
`docs/grata_v2_data_dictionary.md` · `docs/grata_v2_inventory_and_recommendations.md`.

**Retired fields, columns physically retained and unwritten:** `is_platform_investment` ·
`is_add_on` · `hostile` · `is_divestiture` · `is_down_round` · `round_stage_category` ·
`is_de_spac` · `includes_earnout`. A kept column is not a claim of continued authorship;
stored history is untouched and nothing is backfilled. See the Data Dictionary appendix.
