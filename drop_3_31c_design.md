# Drop 3.31c Design - Stage 9 Observation Read Path

Date: 2026-06-03

Status: design only. This drop switches Stage 9 aggregation to an
observation-backed read path after proving corpus parity against the existing
`staging_extraction` read path.

## Purpose

Drop 3.31b populated `transaction_field_observation` with the provenance needed
to represent Stage 3/4/7 source-row extraction outputs. Drop 3.31c should make
Stage 9 consume those observations instead of reconstructing transient
observations directly from `staging_extraction JOIN source_raw`.

The goal is a read-side switch with corpus-level proof that canonical
`transaction_record`, `transaction_source`, conflict-log counts, and LLM
conflict-call counts remain unchanged for the current source-row aggregation
surface.

## Non-Goals

- No prompt changes.
- No schema changes unless validation proves a field cannot be represented with
  the existing observation table.
- No OpenAI live-call validation.
- No agreement supersession logic.
- No agreement-observation canonical routing in this drop.
- No removal of Stage 11 canonical bypass writes.
- No change to 3.31a priority rules or confidence tiebreak behavior.
- No change to `transaction_security` aggregation.

Agreement observations written by Stage 11 remain available in
`transaction_field_observation`, but 3.31c should exclude them from the first
Stage 9 read switch. The first switch should be source-row parity only:
Stage 3/4/7 observations with `staging_extraction_id IS NOT NULL`.

## 1. Current Stage 9 Read Path

`stages/aggregate.py` currently loads clustered source rows with:

```sql
SELECT se.extraction_id, se.transaction_cluster_id, se.source_raw_id,
       ... Stage 3/4/7 extraction columns ...,
       se.model_confidence,
       sr.source_type, sr.source_tier, sr.published_date, sr.clean_text
FROM staging_extraction se
JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
WHERE se.status = 'CLUSTERED'
```

It then builds a transient observation list per `_FIELDS` entry:

```python
{
    "observation_id": i + 1,
    "source_type": m["source_type"],
    "tier": m["source_tier"],
    "published_date": m["published_date"] or "",
    "value": raw_val,
    "model_confidence": m["model_confidence"] or "MEDIUM",
    "source_text_excerpt": (m["clean_text"] or "")[:200],
}
```

That transient shape feeds `_pick_value(...)`, the 3.31a same-tier confidence
tiebreak, and `_call_agg_prompt(...)` for unresolved same-tier conflicts.

## 2. Target Read Model

3.31c should introduce an observation-backed loader that produces the same
in-memory aggregation shape as the current staging-backed loader.

Recommended internal shape:

```python
AggregationInput = {
    "clusters": {
        transaction_id: {
            "field_observations": {
                field_name: [observation, ...]
            },
            "deal_context": {
                "target_name": str | None,
                "acquirer_name": str | None,
                "deal_type": str | None,
                "announced_date": str | None,
            },
            "sources": [
                {
                    "source_raw_id": int,
                    "source_tier": str,
                    "staging_extraction_id": int | None,
                },
                ...
            ],
        }
    }
}
```

Both the existing staging path and the new observation path should normalize
into that shape before the selection loop. This keeps `_pick_value(...)`,
derived-field helpers, conflict logging, and record upsert behavior shared.

## 3. Observation Loader Scope

The first 3.31c observation loader should read only source-row observations:

```sql
SELECT
    tfo.observation_id,
    tfo.transaction_id,
    tfo.field_name,
    tfo.field_value,
    tfo.field_value_numeric,
    tfo.staging_extraction_id,
    tfo.source_raw_id,
    COALESCE(tfo.source_type, sr.source_type) AS source_type,
    COALESCE(tfo.source_tier, sr.source_tier) AS source_tier,
    COALESCE(tfo.source_published_date, sr.published_date) AS published_date,
    COALESCE(tfo.model_confidence, 'MEDIUM') AS model_confidence,
    sr.clean_text
FROM transaction_field_observation tfo
JOIN staging_extraction se
  ON se.extraction_id = tfo.staging_extraction_id
LEFT JOIN source_raw sr
  ON sr.source_raw_id = tfo.source_raw_id
WHERE tfo.is_current = 1
  AND tfo.transaction_id IS NOT NULL
  AND tfo.staging_extraction_id IS NOT NULL
  AND se.status = 'CLUSTERED'
  AND COALESCE(tfo.observation_source_stage, 'BACKFILL') IN (
      'DT_CLASSIFY',
      'HC_EXTRACT',
      'LC_EXTRACT',
      'BACKFILL'
  )
```

Do not include `AGREEMENT_EXTRACT` observations in this drop. That keeps the
3.31c comparison limited to the behavior Stage 9 already has today.

## 4. Field Value Conversion

The observation loader must convert row values back to the same Python values
Stage 9 sees from `staging_extraction`.

Rules:

- For `number` fields, use `field_value_numeric` when non-null, otherwise parse
  `field_value` as float.
- For `boolean` fields, use `field_value_numeric` when non-null and coerce to
  `0` or `1`; otherwise accept string values `"0"`, `"1"`, `"false"`,
  `"true"`.
- For `json` fields, parse `field_value` as JSON when present.
- For `date` and `string` fields, use `field_value` unchanged.
- Preserve null skipping exactly as `_pick_value(...)` currently does.

Observation IDs passed to `_call_agg_prompt(...)` should use the real
`transaction_field_observation.observation_id`, not an ephemeral per-member
index.

## 5. Consideration Components Gap

Stage 9 currently aggregates `consideration_components` as a JSON field.
Drop 3.31b wrote consideration observations as compound fields:

- `consideration.{form}.per_share_amount`
- `consideration.{form}.amount`
- `consideration.{form}.exchange_ratio`

Those compound rows are useful for field-level diffing, but they are not a
lossless representation of the original JSON array when a source has multiple
components with the same `form`.

Required 3.31c data-completeness step:

- Add a JSON-level observation with `field_name = 'consideration_components'`
  for each Stage 7 source row where the staging JSON is non-null.
- Store the original JSON string in `field_value`.
- Leave `field_value_numeric` null.
- Use the same provenance as other Stage 7 observations.
- Keep the existing compound consideration observations. Do not remove them.
- Backfill this JSON-level observation for existing eligible rows.

This does not require a schema change and allows the Stage 9 observation path to
match the current staging path exactly.

## 6. Transaction Source Links

Current Stage 9 writes `transaction_source` from cluster members:

- `PRIMARY` when `source_tier == 'T1'`
- `CONFIRMATORY` otherwise
- Additional `ENRICHMENT` links from `source_raw.notes.triggered_by_extraction_id`

The observation path should reproduce this from distinct observation sources:

```sql
SELECT DISTINCT
    transaction_id,
    staging_extraction_id,
    source_raw_id,
    source_tier
FROM transaction_field_observation
WHERE is_current = 1
  AND transaction_id = ?
  AND staging_extraction_id IS NOT NULL
  AND source_raw_id IS NOT NULL
```

Then preserve the current enrichment lookup:

```sql
SELECT source_raw_id
FROM source_raw
WHERE json_extract(notes, '$.triggered_by_extraction_id') = ?
```

Use the distinct `staging_extraction_id` values from observations for that
enrichment lookup.

Implementation note:

- Some clustered source rows may contain no non-null Stage 9 aggregate fields.
  They still need transaction-source links and status transitions.
- Write a non-aggregate source marker observation, e.g.
  `field_name = '__source_row_present'`, for every source row materialized into
  observations.
- The Stage 9 observation loader should use marker rows for source membership
  but ignore them for field aggregation.

## 7. Read-Path Control

3.31c should ship behind an explicit read-source control:

```text
AGGREGATION_READ_SOURCE=staging|observation
```

Recommended default during implementation:

```text
AGGREGATION_READ_SOURCE=staging
```

After corpus parity is proven and reviewed, the default can move to
`observation` in the same drop or in a follow-up stabilization commit. The
staging path should remain available for rollback until at least one full
observation-backed corpus run is accepted.

Do not implement a live in-place `compare` mode inside normal `run.py`, because
Stage 9 has side effects. Comparison should run on copied DBs.

## 8. Comparison Harness

Add or keep a script-level validation harness that:

1. Copies the same real DB into two temp files.
2. Ensures both copies have 3.31b/3.31c observations backfilled.
3. Clears aggregate outputs in both copies:

```sql
DELETE FROM aggregation_conflict_log;
DELETE FROM transaction_source;
DELETE FROM transaction_record;
UPDATE staging_extraction
SET status = 'CLUSTERED'
WHERE status = 'AGGREGATED';
```

4. Runs Stage 9 on copy A with `AGGREGATION_READ_SOURCE=staging`.
5. Runs Stage 9 on copy B with `AGGREGATION_READ_SOURCE=observation`.
6. Stubs `_call_agg_prompt(...)` for deterministic no-live-API comparison, or
   reuses recorded conflict choices if a replay fixture exists.
7. Compares:
   - Stage 9 summary dicts
   - `transaction_record` rows excluding timestamps
   - `aggregation_conflict_log` count
   - LLM conflict-call count
   - `transaction_source` rows
   - clustered/aggregated staging status counts

Expected result:

- Zero `transaction_record` diffs.
- Equal conflict-log count.
- Equal LLM conflict-call count.
- Equal `transaction_source` row set.
- No failed clusters.

## 9. Coverage Checks

Before the Stage 9 observation read path is allowed, run:

```sql
SELECT COUNT(*)
FROM staging_extraction se
WHERE se.status = 'CLUSTERED'
  AND se.transaction_cluster_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM transaction_field_observation tfo
      WHERE tfo.staging_extraction_id = se.extraction_id
        AND tfo.transaction_id = se.transaction_cluster_id
        AND tfo.is_current = 1
  );
```

Expected: `0`.

Check required source provenance:

```sql
SELECT COUNT(*)
FROM transaction_field_observation
WHERE staging_extraction_id IS NOT NULL
  AND is_current = 1
  AND (
      transaction_id IS NULL
      OR source_raw_id IS NULL
      OR source_tier IS NULL
      OR model_confidence IS NULL
  );
```

Expected: `0` for rows needed by clustered Stage 9 inputs.

Check JSON-level consideration coverage:

```sql
SELECT COUNT(*)
FROM staging_extraction se
WHERE se.status IN ('CLUSTERED', 'AGGREGATED')
  AND se.consideration_components IS NOT NULL
  AND se.consideration_components != '[]'
  AND NOT EXISTS (
      SELECT 1
      FROM transaction_field_observation tfo
      WHERE tfo.staging_extraction_id = se.extraction_id
        AND tfo.field_name = 'consideration_components'
        AND tfo.is_current = 1
  );
```

Expected: `0`.

## 10. Implementation Plan

1. Add `aggregation_read_source` to config, backed by
   `AGGREGATION_READ_SOURCE`, defaulting to `staging`.
2. Refactor Stage 9 into shared aggregation execution over a normalized
   aggregation-input structure.
3. Keep the existing staging loader as one implementation.
4. Add an observation loader that reads current source-row observations.
5. Add JSON-level `consideration_components` observation writes/backfill.
6. Preserve `_pick_value(...)`, conflict logging, derived fields, source links,
   and status transitions.
7. Add a copied-DB comparison script or documented validation command.
8. Run corpus comparison with no live LLM calls.
9. Switch the default to `observation` only after parity is proven and reviewed.

## 11. Acceptance Criteria

- Stage 9 can run with `AGGREGATION_READ_SOURCE=staging`.
- Stage 9 can run with `AGGREGATION_READ_SOURCE=observation`.
- Corpus comparison on a copied real DB shows no `transaction_record` diffs.
- `aggregation_conflict_log` count is identical between read paths.
- LLM conflict-call count is identical between read paths.
- `transaction_source` rows are identical between read paths.
- No live API calls are required for validation.
- No prompts, extraction schemas, or priority rules change.
- Agreement observations remain excluded from Stage 9 canonical routing.

## 12. Deferred

- Agreement supersession using `agreement_dated_as_of`.
- Routing Stage 11 agreement observations through Stage 9.
- Removing Stage 11 canonical bypass writes.
- Making observation read path the only Stage 9 read path.
- QA flags beyond current conflict logging.
