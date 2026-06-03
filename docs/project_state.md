# M&A Collection MVP - Project State

**As of:** 2026-06-03  
**Repo:** `elgoshin11215/ma-collection-mvp`  
**Current commit:** `577364d drop 3.32a: add multi-party organization participant model`
**Current default aggregation read path:** `AGGREGATION_READ_SOURCE=staging`

## Purpose

This document preserves the working state of the project across sessions. Read
it first when resuming the repo, then consult `README.md`, the drop design docs,
and the validation scripts for implementation details.

## Current System Shape

The MVP is a single-operator M&A collection pipeline built around SQLite,
source-tiered extraction, deterministic clustering, LLM-assisted aggregation,
SEC document enrichment, agreement extraction, summarization, rationale tagging,
and export.

The pipeline still preserves the original Anthropic flow, but now has a provider
abstraction that can instantiate either Anthropic or OpenAI clients from
configuration. OpenAI live validation is deferred until an enterprise API key is
available to the local Python runtime.

Stage 9 aggregation now has two read paths:

- `staging`: legacy read from `staging_extraction JOIN source_raw`; still the
  default and rollback path.
- `observation`: new read from `transaction_field_observation`; parity validated
  against the staging path on copied real DBs.

The observation path is limited to source-row observations from
`DT_CLASSIFY`, `HC_EXTRACT`, `LC_EXTRACT`, and `BACKFILL`. Agreement extraction
observations are intentionally excluded from Stage 9 routing until a separate
agreement-supersession design is approved.

Drop 3.32a is committed as participant-centric multi-party organization
support. It adds normalized organization participant tables and an idempotent
backfill/validation path without changing Stage 9, prompts, exports, advisors,
or `transaction_record`.

## Recent Completed Work

| Commit | Drop / scope | Status |
|---|---|---|
| `577364d` | Drop 3.32a: multi-party organization participant model | Implemented and copied-real-DB validated |
| `bfdfba5` | Documentation state and handoff updates | Committed |
| `674ab04` | Drop 3.31c: observation-backed Stage 9 read path | Implemented and parity validated |
| `34c3dff` | Drop 3.31c design | Accepted |
| `e63b692` | OpenAI provider support while preserving Anthropic | Implemented; live OpenAI validation deferred |
| `dfa0be7` | Drop 3.31b: observation provenance and source-row dual writes | Implemented and accepted |
| `0bd7062` | Drop 3.31a: shared field priority and confidence tiebreak | Implemented and accepted |
| `de4c223` | Drop 3.26: `transaction_security` soft-delete + unique index | Implemented |
| `b8fcb05` | Drop 3.25: observation idempotency/savepoint correctness | Implemented |
| `6d020d2` | Drop 3.24a: capitalization sub-section descent | Implemented |

## Drop 3.31 Status

### Drop 3.31a

Closed.

- Shared field-priority logic lives in `lib/field_priority.py`.
- Stage 9 applies deterministic same-tier confidence priority before LLM
  conflict resolution.
- Corpus validation on copied real DB reduced conflict logs and LLM conflict
  calls from `116` to `113`.
- One canonical transaction changed, affecting three fields; all changes were
  explained by strict `HIGH` over `MEDIUM` confidence priority.
- No prompt, schema, or observation-architecture changes were made.

### Drop 3.31b

Closed and accepted.

- `transaction_field_observation` now supports source-row provenance needed for
  Stage 9 aggregation:
  - `staging_extraction_id`
  - `source_raw_id`
  - `source_type`
  - `source_tier`
  - `model_confidence`
  - `source_published_date`
  - `filing_type`
  - `agreement_dated_as_of`
  - `observation_source_stage`
- Stage 4 and Stage 7 dual-write source-row observations.
- Backfill is idempotent.
- Stage 9 behavior remained unchanged in 3.31b.

Copied-real-DB validation:

- Source-row observations after 3.31b: `7423`
- Missing source IDs: `0`
- Missing transaction IDs after clustering/backfill: `0`
- Stage 9 staging-read output unchanged:
  - `335` transactions
  - `414` transaction-source rows
  - `113` conflict logs/calls
  - `0` transaction diffs

### Drop 3.31c

Closed by parity validation; administrative merge/acceptance can proceed.

Implemented:

- `AGGREGATION_READ_SOURCE=staging|observation`
- Observation-backed Stage 9 loader.
- JSON-level `consideration_components` observations.
- Source marker observations using `__source_row_present`.
- Copied-DB parity validation harness.

Preserved:

- Existing prompts.
- Existing schemas for 3.31c itself.
- Existing aggregation rules and 3.31a priority behavior.
- Existing staging read path.
- Existing Stage 11 agreement extraction behavior.

Excluded by design:

- Agreement observation routing through Stage 9.
- Agreement supersession logic.
- OpenAI live API validation.

## Latest 3.31c Parity Validation

Validation target:

- Commit: `674ab04`
- Source DB: `/private/tmp/ma_mvp_331a_corpus_post.db`
- Staging copy: `/private/tmp/ma_331c_parity_staging_674ab04.db`
- Observation copy: `/private/tmp/ma_331c_parity_observation_674ab04.db`
- Production touched: no
- Live API calls: no

Results:

| Check | Staging | Observation |
|---|---:|---:|
| Clusters total | 335 | 335 |
| Transactions created | 335 | 335 |
| Transactions upserted | 335 | 335 |
| Failed clusters | 0 | 0 |
| `transaction_record` rows | 335 | 335 |
| `transaction_source` rows | 414 | 414 |
| `aggregation_conflict_log` rows | 113 | 113 |
| Stubbed LLM conflict calls | 113 | 113 |
| Flagged for review | 0 | 0 |

Diff and coverage checks:

- `transaction_record` diffs excluding audit timestamps: `0`
- `transaction_source` diffs: `0`
- Canonical `consideration_components` diffs: `0`
- JSON-level `consideration_components` observations: `392`
- Source marker observations: `402`
- Clustered/aggregated rows without observations: `0`
- Observation rows missing required provenance: `0`
- Missing JSON-level `consideration_components` observations: `0`
- Agreement observations routed through Stage 9: `0`

Recommendation: close 3.31c.

## Drop 3.32a Status

Implemented and committed in `577364d`.

Design decision:

- 3.32a is participant-centric, not relationship-centric.
- `entity_relationship` was removed from the active 3.32a scope.
- Relationship-style concepts such as `PORTFOLIO_COMPANY_OF`,
  `SPONSORED_BY`, `CORPORATE_VC_ARM_OF`, `MANAGED_BY`, and `SUBSIDIARY_OF`
  are not written in 3.32a.
- Sponsors, platforms, parents, merger subs, investors, and issuers are
  represented through transaction-context participant roles.

Approved active tables:

- `entity`
- `entity_alias`
- `transaction_participant`
- `transaction_participant_group`

Explicit non-goals preserved:

- No people extraction.
- No advisor redesign.
- No generic participant attribute table.
- No Stage 9 changes.
- No prompt changes.
- No export changes.
- No live API calls.

Copied-real-DB validation:

- Source DB: `/private/tmp/ma_331c_parity_staging_674ab04.db`
- Validation DB: `/private/tmp/ma_332a_patch3_validation.db`
- Full JSON report: `/private/tmp/ma_332a_patch3_validation.json`
- Result: `PASS`
- Production touched: no
- Live API calls: no

Validation results:

- `entity` rows inserted: `802`
- `entity_alias` rows inserted: `802`
- `transaction_participant` rows inserted: `803`
- `transaction_participant_group` rows inserted: `20`
- Duplicate current participants: `0`
- Duplicate groups: `0`
- Synthetic group entities: `0`
- Foreign key issues: `0`
- `transaction_record` unchanged: `335` rows, digest matched source
- `advisor` unchanged: `340` rows, digest matched source
- Idempotency second run inserted: `0`

Role counts:

- `TARGET`: `335`
- `ACQUIRER`: `278`
- `BUYER_PLATFORM`: `54`
- `BUYER_SPONSOR`: `66`
- `PARENT_SELLER`: `70`

Group counts:

- `CONSORTIUM`: `12`
- `INVESTOR_GROUP`: `5`
- `SELLER_GROUP`: `3`

Coverage note:

- Strict acquirer misses: `3`
- All three are generic consortium-label exceptions stored as group labels, not
  synthetic entities.

## OpenAI Provider State

Provider abstraction is implemented and Anthropic support remains available.

Configuration surface includes:

- `LLM_PROVIDER=openai|anthropic`
- `OPENAI_API_KEY`
- `OPENAI_RELEVANCY_MODEL`
- `OPENAI_CLASSIFICATION_MODEL`
- `OPENAI_EXTRACT_MODEL`
- `OPENAI_LEGAL_EXTRACT_MODEL`
- `OPENAI_REASONING_MODEL`

Pending:

- Obtain a usable OpenAI API key for the local pipeline.
- Run provider smoke validation with OpenAI selected.
- Run a small copied-DB or limited-slice live validation only after explicit
  API configuration.

## Current Operational Defaults

Use staging read path unless explicitly testing the new path:

```text
AGGREGATION_READ_SOURCE=staging
```

Use observation read path only on copied DBs or controlled validation runs until
one accepted operational run confirms the new path outside the parity harness:

```text
AGGREGATION_READ_SOURCE=observation
```

## Recommended Next Work

1. Keep staging as default until an accepted observation-backed operational run
   is completed on a copied real DB.
2. Validate OpenAI live provider behavior when an enterprise API key becomes
   available to the local runtime.
3. Design the agreement observation supersession drop separately; do not fold it
   back into 3.31c.
4. Decide later whether to switch the default Stage 9 read path to
   `observation`.

## Deferred / Known Follow-Ups

- Agreement observations are written but not yet part of Stage 9 canonical
  routing.
- The observation path is ready for source-row aggregation parity, not full
  agreement supersession.
- Gold-set labeling remains the path for acceptance scoring beyond parity.
- OpenAI live validation remains blocked on local API key access.
- Staging read path should remain available as a rollback path for now.
- 3.32a does not yet extract investors/issuers from new prompts; those roles
  are reserved for future Growth Equity / Venture Capital support.
