# Architectural Decisions

This file records project-level decisions that affect future implementation
work. It is intentionally brief; implementation detail belongs in drop design
docs and code.

## 2026-06-03 - LLM Provider Abstraction

Status: accepted.

Decision:

- Route prompt execution through an internal provider abstraction.
- Preserve Anthropic as a provider.
- Add OpenAI as a provider behind `LLM_PROVIDER=openai`.
- Use OpenAI Responses API and Structured Outputs for JSON-returning prompt
  paths where practical.

Context:

- Codex Enterprise access does not make the local Python pipeline use OpenAI.
  The repo must call OpenAI explicitly.
- The project needs to switch providers without changing prompts, schemas, or
  extraction semantics.

Consequences:

- Anthropic remains the current supported live path.
- OpenAI live validation is deferred until a local API key is available.
- Prompt and schema changes should not be bundled with provider migration.

## 2026-06-03 - Shared Field Priority and Confidence Tiebreak

Status: accepted.

Decision:

- Centralize field filing-type priority and tier ordering in
  `lib/field_priority.py`.
- Keep existing priority rules.
- Apply deterministic same-tier model-confidence priority before invoking LLM
  conflict resolution.

Context:

- Drop 3.31a needed to reduce unnecessary conflict calls without changing
  schemas, prompts, or aggregation semantics.

Consequences:

- Same-tier `HIGH` observations beat same-tier `MEDIUM` observations before LLM
  conflict resolution.
- Remaining same-tier conflicts still use the existing LLM conflict path.

## 2026-06-03 - Observation Provenance as Stage 9 Read Substrate

Status: accepted.

Decision:

- Extend `transaction_field_observation` so source-row extraction observations
  carry the provenance Stage 9 needs:
  - `staging_extraction_id`
  - `source_raw_id`
  - `source_type`
  - `source_tier`
  - `model_confidence`
  - `source_published_date`
  - `filing_type`
  - `agreement_dated_as_of`
  - `observation_source_stage`
- Dual-write Stage 4 and Stage 7 source-row observations.
- Backfill existing copied DBs idempotently.

Context:

- Stage 9 originally reconstructed transient observations from
  `staging_extraction JOIN source_raw`.
- Moving aggregation toward durable observations required source-row
  provenance without changing Stage 9 behavior in Drop 3.31b.

Consequences:

- Stage 9 can now be validated against a durable observation layer.
- `staging_extraction` remains part of the current pipeline; 3.31b did not
  remove it or make observations the only source of truth.

## 2026-06-03 - Guarded Observation-Backed Stage 9 Read Path

Status: accepted.

Decision:

- Add `AGGREGATION_READ_SOURCE=staging|observation`.
- Keep `staging` as the default for now.
- Add an observation-backed Stage 9 loader that normalizes observations into
  the same in-memory shape as the staging loader.
- Limit the first observation-backed read path to source-row observations from
  `DT_CLASSIFY`, `HC_EXTRACT`, `LC_EXTRACT`, and `BACKFILL`.
- Exclude `AGREEMENT_EXTRACT` observations from Stage 9 routing.
- Preserve the staging read path as rollback/comparison path.

Context:

- Drop 3.31c was intended as a read-side parity switch, not a semantic rewrite.
- Agreement supersession is a separate design problem and should not be folded
  into the source-row parity switch.

Consequences:

- 3.31c can close because copied-real-DB parity passed with zero canonical
  transaction diffs and zero transaction-source diffs.
- A future decision is still needed before changing the default read path to
  `observation`.
- A future agreement-supersession drop is still needed before agreement
  observations participate in Stage 9 canonical routing.

