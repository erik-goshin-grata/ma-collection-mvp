# Drop 3.31b Design - Observation Provenance and HC/LC Dual Writes

Date: 2026-06-02

Status: design only. This drop prepares the observation layer for Drop 3.31c.
It does not change Stage 9 read behavior, prompts, schemas outside
`transaction_field_observation`, aggregation rules, or agreement supersession
logic.

## Purpose

Drop 3.31a centralized priority constants and added a same-tier confidence
tiebreak in Stage 9. Drop 3.31b should make the observation table capable of
representing the same provenance that Stage 9 currently reconstructs from
`staging_extraction JOIN source_raw`.

The goal is to dual-write source-row extraction outputs into
`transaction_field_observation` while leaving the current canonical pipeline in
place. Drop 3.31c can then switch Stage 9 to read from the observation layer and
remove bypass writes with a corpus diff against the 3.31b-populated table.

Non-goals for 3.31b:

- No Stage 9 read-side rewrite.
- No agreement supersession decision logic.
- No prompt changes.
- No extraction semantics changes.
- No observation-layer replacement of `staging_extraction` yet.
- No changes to `transaction_security` aggregation.

## 1. Current Observation Schema

Current table from `schema/001_initial.sql`:

```sql
CREATE TABLE IF NOT EXISTS transaction_field_observation (
    observation_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL,
    field_name                  TEXT NOT NULL,
    field_value                 TEXT,
    field_value_numeric         REAL,
    source_document_id          INTEGER NOT NULL,
    source_section_id           INTEGER,
    observed_as_of_date         TEXT,
    filing_date                 TEXT,
    extracted_at                TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    extraction_prompt_version   TEXT,
    is_current                  INTEGER DEFAULT 1,
    FOREIGN KEY (transaction_id) REFERENCES transaction_record(transaction_id),
    FOREIGN KEY (source_document_id) REFERENCES transaction_document(document_id),
    FOREIGN KEY (source_section_id) REFERENCES transaction_document_section(section_id)
);

CREATE INDEX IF NOT EXISTS idx_observation_txn_field
    ON transaction_field_observation(transaction_id, field_name);

CREATE INDEX IF NOT EXISTS idx_observation_filing_date
    ON transaction_field_observation(filing_date);
```

Current post-creation migration in `db.py`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current
ON transaction_field_observation (source_section_id, field_name, field_value)
WHERE is_current = 1;
```

Current writer:

- `stages/agreement_extract.py::_write_observations(...)`
- Writes Stage 11 agreement-section observations only.
- Requires `source_document_id`.
- Usually has `source_section_id`.
- Writes scalar fields, securities as `shares_outstanding.*`, and consideration
  components as `consideration.{form}.{attr}`.
- Does not write Stage 3/4/7 source-row observations.

Current read-side usage:

- `stages/agreement_extract.py::_compute_observation_changes(...)` reads the
  table for agreement-source diff surfacing.
- `stages/agreement_extract.py::_clear_stale_canonical_fields(...)` reads
  current observation field names to null stale agreement-derived canonical
  fields.
- `stages/aggregate.py` does not read `transaction_field_observation`. It still
  builds transient observations from `staging_extraction JOIN source_raw`.

Current limitations:

- `transaction_id TEXT NOT NULL` assumes a transaction cluster exists.
- `source_document_id INTEGER NOT NULL` assumes every observation comes from a
  SEC deal document.
- The unique index is section-centric and does not protect source-row
  observations where `source_section_id` is NULL.
- The table cannot represent PR/SEC source-row observations from Stage 3/4/7
  without fabricating a `transaction_document` row.

## 2. Missing Provenance Fields

Drop 3.31b should add provenance columns needed to make each observation
self-sufficient for Stage 9 read-side aggregation.

Required additions:

| Column | Type | Nullable | Purpose |
|---|---:|---:|---|
| `staging_extraction_id` | `INTEGER` | yes | Links HC/LC source-row observations to the `staging_extraction` row that produced them. |
| `source_raw_id` | `INTEGER` | yes | Links PR/SEC source-row observations to the raw source. Required for Stage 9 source links and source text joins. |
| `source_type` | `TEXT` | yes | Denormalized `source_raw.source_type` or agreement document filing class for read-side prompts/debugging. |
| `source_tier` | `TEXT` | yes | Denormalized `source_raw.source_tier`; needed for `TIER_ORDER` without rejoining staging. |
| `model_confidence` | `TEXT` | yes | Observation-level confidence used by the 3.31a same-tier confidence tiebreak. This is the `confidence` field called out by 3.31a notes, named to match existing schema vocabulary. |
| `source_published_date` | `TEXT` | yes | `source_raw.published_date` for PR/source-row observations. Keeps Stage 9 prompt context available from observations. |
| `filing_type` | `TEXT` | yes | `transaction_document.filing_type` for agreement observations, or source-derived filing type when known. Enables shared filing-type priority rules. |
| `agreement_dated_as_of` | `TEXT` | yes | Nullable agreement effective/signing date for future agreement supersession in 3.31c. |
| `observation_source_stage` | `TEXT` | yes | `DT_CLASSIFY`, `HC_EXTRACT`, `LC_EXTRACT`, `AGREEMENT_EXTRACT`, or `BACKFILL`; supports debugging and idempotent backfill. |

Required constraint change:

- `transaction_id` should become nullable during pre-cluster source-row writes.
  It is backfilled from `staging_extraction.transaction_cluster_id` after
  clustering and before Stage 9.
- `source_document_id` should become nullable because Stage 3/4/7 observations
  originate from `source_raw`, not `transaction_document`.

Why not keep both columns NOT NULL:

- Stage 4 and Stage 7 run before entity clustering. They do not know
  `transaction_cluster_id` yet.
- PR source rows have no `transaction_document` row.
- Fabricating placeholder transaction/document rows would create worse
  provenance and make 3.31c harder to reason about.

Recommended indexes:

```sql
CREATE INDEX IF NOT EXISTS idx_observation_staging
    ON transaction_field_observation(staging_extraction_id);

CREATE INDEX IF NOT EXISTS idx_observation_source_raw
    ON transaction_field_observation(source_raw_id);

CREATE INDEX IF NOT EXISTS idx_observation_txn_field_current
    ON transaction_field_observation(transaction_id, field_name, is_current);

CREATE INDEX IF NOT EXISTS idx_observation_tier_confidence
    ON transaction_field_observation(transaction_id, field_name, source_tier, model_confidence);
```

Recommended uniqueness:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current_section
ON transaction_field_observation (source_section_id, field_name, field_value)
WHERE is_current = 1 AND source_section_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_observation_unique_current_staging
ON transaction_field_observation (staging_extraction_id, field_name, field_value)
WHERE is_current = 1 AND staging_extraction_id IS NOT NULL;
```

The existing `idx_observation_unique_current` can be replaced by
`idx_observation_unique_current_section` during a table rebuild, or left in
place if compatible. The staging-specific unique index is required because
SQLite treats NULLs as distinct and the existing section index will not prevent
duplicates for source-row observations.

## 3. Exact Dual-Write Locations

3.31b should keep all existing `staging_extraction` writes. Observation writes
are additive and idempotent.

### 3.1 Stage 4: `stages/high_confidence_extract.py`

Exact locations:

1. In the `i == 0` branch, immediately after the successful
   `UPDATE staging_extraction SET ... status = 'HC_EXTRACTED' ... WHERE extraction_id = ?`.
2. In the `i > 0` branch, immediately after the successful
   `INSERT INTO staging_extraction (...) VALUES (...)` for split transactions.

Required implementation detail:

- Capture the correct `extraction_id`.
  - For `i == 0`, it is the existing `eid`.
  - For `i > 0`, use `cursor.lastrowid` from the insert.
- Dual-write HC observations for the fields Stage 4 owns:
  - parties and domains/tickers
  - descriptions
  - sponsor/parent seller fields
  - dates
  - value fields
  - `pct_acquired`
  - target financials
- Also materialize Stage 3 classification fields once per source row if they
  are needed by Stage 9:
  - `deal_type`
  - `spin_split_type`
  - `distribution_mechanism`
  - `target_type`
  - `event_type`
  - `target_status`

Rationale:

Stage 9's `_FIELDS` list includes Stage 3, Stage 4, and Stage 7 fields. If
3.31c is going to read from observations, 3.31b must populate observations for
all Stage 9 source fields, not only the fields whose prompt ran last.

### 3.2 Stage 7: `stages/low_confidence_extract.py`

Exact location:

- Immediately after the successful `UPDATE staging_extraction SET
  status = 'LC_EXTRACTED', ... WHERE extraction_id = ?` and before the final
  stage commit for that extraction.

Dual-write LC observations for:

- `consideration_components` as compound field names:
  - `consideration.{form}.per_share_amount`
  - `consideration.{form}.amount`
  - `consideration.{form}.exchange_ratio`
- `includes_earnout` *(retired — S-F. It meant "earnout OR CVR", a wider scope than the field it appeared to shortcut. `consideration_components` is authoritative; `has_earnout` derives from `EARNOUT` and `has_cvr` from `CVR`.)*
- `hostile` *(retired — V3 §T11, S-A. Split into `deal_attitude` and `approach_type`. Column retained, no longer written.)*
- `competing_bid`
- `regulatory_approvals_required`
- `has_go_shop`
- `go_shop_period_days`
- `target_fee_amount`
- `target_fee_percentage`
- `acquirer_fee_amount`
- `acquirer_fee_percentage`

Do not dual-write advisors in 3.31b. Advisors already have their own `advisor`
table keyed to `extraction_id`, and Stage 9 does not aggregate advisor fields.

### 3.3 Stage 8 Backfill Hook: `stages/entity_cluster.py`

Exact location:

- After `transaction_cluster_id` has been assigned and committed for all
  clustered `staging_extraction` rows, before Stage 9 runs.

Operation:

```sql
UPDATE transaction_field_observation
SET transaction_id = (
    SELECT se.transaction_cluster_id
    FROM staging_extraction se
    WHERE se.extraction_id = transaction_field_observation.staging_extraction_id
)
WHERE transaction_id IS NULL
  AND staging_extraction_id IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM staging_extraction se
      WHERE se.extraction_id = transaction_field_observation.staging_extraction_id
        AND se.transaction_cluster_id IS NOT NULL
  );
```

This can be implemented either as:

- a helper called at the end of `entity_cluster.run(...)`, or
- a small new stage between entity clustering and aggregation.

Preferred name if separated:

- `stages/observation_backfill.py`

### 3.4 Stage 11: `stages/agreement_extract.py`

Exact location:

- Extend existing `_write_observations(...)`; do not create a separate writer.

Add new provenance values for agreement observations:

- `source_document_id`
- `source_section_id`
- `filing_date`
- `filing_type`
- `source_tier = 'T1'`
- `observation_source_stage = 'AGREEMENT_EXTRACT'`
- `model_confidence = extraction_results.get('model_confidence')`
- `agreement_dated_as_of` when deterministically available

Do not change Stage 11 canonical bypass writes in 3.31b. They are removed or
rewired in 3.31c after the observation read path is validated.

## 4. Backfill Approach

Backfill has two jobs:

1. Populate new provenance columns for existing Stage 11 observation rows.
2. Materialize Stage 3/4/7 source-row observations from existing
   `staging_extraction` rows so the real corpus can be compared before 3.31c.

### 4.1 Existing Stage 11 Rows

For rows with `source_document_id IS NOT NULL`:

```sql
UPDATE transaction_field_observation
SET
    filing_type = (
        SELECT td.filing_type
        FROM transaction_document td
        WHERE td.document_id = transaction_field_observation.source_document_id
    ),
    source_tier = COALESCE(source_tier, 'T1'),
    observation_source_stage = COALESCE(observation_source_stage, 'AGREEMENT_EXTRACT')
WHERE source_document_id IS NOT NULL;
```

`model_confidence` for historical rows should remain NULL unless it can be
recovered from a stored prompt response. Do not fabricate confidence from
section confidence; section confidence describes heuristic section selection,
not model confidence.

`agreement_dated_as_of` backfill:

- Use a deterministic bounded regex over the first page / first N characters of
  `transaction_document.raw_text`.
- Accept only clear forms such as `dated as of Month D, YYYY`.
- Leave NULL when ambiguous.
- Do not call an LLM and do not change prompts.

### 4.2 Existing Stage 3/4/7 Source Rows

Backfill source-row observations from `staging_extraction` joined to
`source_raw`.

Eligible rows:

```sql
WHERE se.transaction_cluster_id IS NOT NULL
  AND se.status IN ('LC_EXTRACTED', 'CLUSTERED', 'AGGREGATED')
```

For each eligible row:

- `transaction_id = se.transaction_cluster_id`
- `staging_extraction_id = se.extraction_id`
- `source_raw_id = se.source_raw_id`
- `source_type = sr.source_type`
- `source_tier = sr.source_tier`
- `source_published_date = sr.published_date`
- `model_confidence = se.model_confidence`
- `observation_source_stage = 'BACKFILL'`
- `extraction_prompt_version`:
  - `dt_prompt_version` for Stage 3 fields
  - `hc_prompt_version` for Stage 4 fields
  - `lc_prompt_version` for Stage 7 fields

Field groups:

- Stage 3 classification fields:
  - `deal_type`
  - `spin_split_type`
  - `distribution_mechanism`
  - `target_type`
  - `event_type`
  - `target_status`
- Stage 4 high-confidence fields:
  - all party, date, value, and financial fields used by Stage 9 `_FIELDS`
- Stage 7 low-confidence fields:
  - flags, go-shop, fee fields, and consideration components

Null handling:

- Skip NULL values.
- For booleans, store `field_value` as `0`/`1` and `field_value_numeric` as
  `0.0`/`1.0`.
- For numeric fields, store stringified value plus `field_value_numeric`.
- For JSON `consideration_components`, write compound observations as Stage 11
  already does.

Idempotency:

- Use `INSERT OR IGNORE`.
- Add the staging-specific unique partial index before running backfill.
- Backfill can be rerun safely.

### 4.3 Pending Observations Written Before Clustering

If Stage 4/7 dual-write observations before clustering, they will have
`transaction_id IS NULL`.

After Stage 8 assigns `transaction_cluster_id`, run the transaction-id backfill
query from section 3.3.

Stage 9 in 3.31b should continue reading `staging_extraction`, so pending
observations do not affect canonical output in this drop. 3.31c should assert
that all observations needed for aggregate have non-null `transaction_id`
before switching the read path.

## 5. Migration Plan

SQLite cannot relax `NOT NULL` constraints in place. Because 3.31b needs
nullable `transaction_id` and nullable `source_document_id`, the safest
migration is a table rebuild.

### 5.1 Preflight

On a copied DB:

```sql
PRAGMA foreign_keys = ON;
PRAGMA foreign_key_check;

SELECT COUNT(*) FROM transaction_field_observation;
SELECT COUNT(*) FROM transaction_field_observation WHERE is_current = 1;
```

Record:

- total rows
- current rows
- duplicate candidates by `(source_section_id, field_name, field_value)`
- rows with missing referenced documents or sections

### 5.2 Rebuild Table

1. Begin transaction.
2. Rename existing table to `transaction_field_observation_old`.
3. Create the new table with the added provenance columns.
4. Make `transaction_id` nullable.
5. Make `source_document_id` nullable.
6. Copy all existing columns into the new table.
7. Populate default provenance for copied Stage 11 rows where possible.
8. Recreate indexes.
9. Recreate unique partial indexes.
10. Run `PRAGMA foreign_key_check`.
11. Drop old table after validation.
12. Commit.

### 5.3 Fresh Schema

Update `schema/001_initial.sql` so fresh databases get the 3.31b observation
table directly.

### 5.4 Idempotent Runtime Migration

Update `db.py::_apply_migrations(...)` to:

- detect whether new columns exist
- detect whether table rebuild is already complete
- perform the rebuild exactly once
- create the new indexes idempotently

Avoid multiple ALTER-only migrations because the NOT NULL relaxation requires
a rebuild anyway.

### 5.5 Validation

Required checks after migration and backfill:

```sql
PRAGMA foreign_key_check;

SELECT COUNT(*) FROM transaction_field_observation WHERE is_current = 1;

SELECT COUNT(*)
FROM transaction_field_observation
WHERE staging_extraction_id IS NOT NULL
  AND source_raw_id IS NULL;

SELECT COUNT(*)
FROM transaction_field_observation
WHERE staging_extraction_id IS NOT NULL
  AND transaction_id IS NULL;

SELECT transaction_id, field_name, COUNT(*)
FROM transaction_field_observation
WHERE is_current = 1
GROUP BY transaction_id, field_name
HAVING COUNT(*) > 1
ORDER BY COUNT(*) DESC
LIMIT 20;
```

Run Stage 9 before and after 3.31b on copied DBs. Expected:

- no canonical output changes from 3.31b alone
- no prompt calls added
- observation counts increase
- no duplicate-current rows for the same staging source/field/value

## 6. How Each Change Enables 3.31c

| 3.31b change | Direct 3.31c enablement |
|---|---|
| Add `staging_extraction_id` | Lets Stage 9 trace each observation back to its source extraction row and preserves one observation per extracted source-row claim. |
| Add `source_raw_id` | Lets Stage 9 create `transaction_source` links and retrieve source text without reading `staging_extraction`. |
| Add `source_type` | Preserves current aggregation prompt context (`Source: ...`) when observations become the read source. |
| Add `source_tier` | Lets Stage 9 apply `TIER_ORDER` directly from observations. |
| Add `model_confidence` | Lets the 3.31a same-tier confidence tiebreak work after Stage 9 stops reading `staging_extraction.model_confidence`. |
| Add `source_published_date` | Preserves current prompt context and enables deterministic source ordering without joining staging. |
| Add `filing_type` | Lets 3.31c apply `FIELD_FILING_TYPE_PRIORITY` from `lib/field_priority.py` against observations. |
| Add `agreement_dated_as_of` | Provides the field needed for agreement supersession logic in 3.31c. |
| Make `transaction_id` nullable until cluster backfill | Allows Stage 4/7 to dual-write before Stage 8 assigns cluster IDs. |
| Make `source_document_id` nullable | Allows PR/source-row observations that have no `transaction_document`. |
| Add staging-specific uniqueness | Makes Stage 4/7 dual writes and backfill rerunnable without duplicate current observations. |
| Backfill existing corpus observations | Gives 3.31c a real corpus to compare against before switching Stage 9 reads. |
| Extend Stage 11 writer with provenance | Lets Stage 9 read agreement observations through the same table as source-row observations. |
| Keep Stage 9 read path unchanged in 3.31b | Separates data-population risk from aggregation-behavior risk, making 3.31c validation cleaner. |

## Proposed 3.31b Acceptance Criteria

- `transaction_field_observation` supports source-row and agreement-document
  observations without fabricated documents.
- Existing Stage 11 observations survive migration.
- Stage 4/7 dual writes are idempotent.
- Backfill produces observations for all Stage 9 `_FIELDS` where existing
  `staging_extraction` rows have non-null values.
- Stage 9 output is unchanged when still reading `staging_extraction`.
- `PRAGMA foreign_key_check` passes on a copied real DB.
- 3.31c can be implemented as a read-side switch rather than a data-model
  migration.

## Deferred to 3.31c

- Stage 9 reads from `transaction_field_observation`.
- Stage 11 canonical bypass writes are removed or routed through aggregation.
- Agreement supersession logic uses `agreement_dated_as_of`.
- Cross-source disagreement QA flags beyond current conflict logging.
- Priority-rule correctness review beyond the verbatim 3.31a rules.
