# Pipeline Orchestration Spec

**Version:** 0.1 (draft)
**Repo path:** `specs/pipeline.md`

---

## 1. Purpose

Defines the end-to-end orchestration of the MVP pipeline: the sequence of stages, the state machine that tracks each row's progress, idempotency and resume rules, run modes, and error handling posture.

The pipeline is a single Python entry point (`run.py`) orchestrating adapters and prompts against a SQLite database. No long-running processes, no queues, no distributed coordination.

---

## 2. Pipeline Stages

Twelve stages in strict sequence. Each stage reads rows in specific states and transitions them forward. Stages are idempotent — re-running a stage does not duplicate work or corrupt state.

| # | Stage | Input State | Output State | What Happens |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `scrape_pr_newswire` | N/A | `source_raw.source_status = FETCHED` | PR Newswire adapter runs; inserts new rows in `source_raw` |
| 2 | `relevancy_filter` | `source_status = FETCHED` | `source_status = RELEVANT` or `NOT_RELEVANT` | Haiku classifier on title+body excerpt |
| 3 | `deal_type_classify` | `source_status = RELEVANT` | `staging_extraction` row created, `status = CLASSIFIED` | Opus 7-type classifier |
| 4 | `high_confidence_extract` | `staging_extraction.status = CLASSIFIED` | `status = HC_EXTRACTED` | Opus extracts parties, dates, value, acquirer_type, target financials |
| 5 | `sec_trigger_detect` | `status = HC_EXTRACTED` | `status = SEC_TRIGGERED` or `SEC_NOT_TRIGGERED` | Python-side public-party detection on PR text |
| 6 | `sec_enrich` | `status = SEC_TRIGGERED` | `status = SEC_ENRICHED` | sec-api.io adapter pulls 8-K Item 1.01 and Exhibit 2.1 for the transaction |
| 7 | `low_confidence_extract` | `status = HC_EXTRACTED` or `SEC_ENRICHED` | `status = LC_EXTRACTED` | Opus extracts advisors, consideration_components, flags, termination_fees, go_shop |
| 8 | `entity_cluster` | `status = LC_EXTRACTED` | Rows linked to a `transaction_cluster_id` | Fuzzy name + date matching groups extractions belonging to the same real-world transaction. See `entity_resolution.md`. |
| 9 | `aggregate` | Clustered extractions | `transaction` rows created | Deterministic tier rules resolve fields; LLM called only on unresolved conflicts |
| 10 | `summarize` | `transaction` rows | `summary` rows created with `is_current = true` | Opus generates 80–150 word summary |
| 11 | `rationale_tag` | `transaction` rows with summary | `rationale_tag` rows created | Opus classifies primary + secondary rationales |
| 12 | `export` | All `is_current = true` transaction rows | CSV in `exports/` | Flattened transaction-level CSV for review |

Stages 5 and 6 run per-transaction (not per-source-row). Stages 1, 2, 3, 4, 7, 8, 9, 10, 11, 12 run in batches.

---

## 3. State Machine

### 3.1 `source_raw.source_status`

```
(fetch) → FETCHED → (relevancy filter) → RELEVANT
                                        → NOT_RELEVANT (terminal)
                                        → RELEVANCY_FAILED (re-runnable)
```

### 3.2 `staging_extraction.status`

```
CLASSIFIED → HC_EXTRACTED → SEC_TRIGGERED → SEC_ENRICHED → LC_EXTRACTED → CLUSTERED → AGGREGATED
                          → SEC_NOT_TRIGGERED → LC_EXTRACTED → CLUSTERED → AGGREGATED

any stage → PROMPT_FAILED (re-runnable; falls back to last valid state on retry)
```

### 3.3 Terminal vs re-runnable states

Terminal states (do not re-process on resume):
- `source_status = NOT_RELEVANT`
- `staging_extraction.status = AGGREGATED`

All `_FAILED` states are re-runnable. The orchestrator's default `resume` mode picks up failed rows and retries once.

---

## 4. Run Modes

Invoked via CLI flag on `run.py`:

| Mode | Flag | Behavior |
| :--- | :--- | :--- |
| Full run | `--mode=full` | All 12 stages in order. Fetches new PRs, extracts, clusters, aggregates, summarizes, tags, exports. |
| Resume | `--mode=resume` (default) | Skip scraping; process any rows with non-terminal states. Equivalent to running stages 2–12 on existing rows. |
| Scrape only | `--mode=scrape` | Stage 1 only. Adds new rows to `source_raw` without any LLM calls. Useful for bulk-fetch-then-review workflows. |
| Extract only | `--mode=extract` | Stages 2–7. Runs classification and extraction on fetched rows but stops before clustering. |
| Aggregate only | `--mode=aggregate` | Stages 8–9. Clusters and aggregates existing staging rows. |
| Generate only | `--mode=generate` | Stages 10–11. Summary and rationale for existing aggregated transactions. |
| Export only | `--mode=export` | Stage 12. Dumps current transactions to CSV. |
| Rerun prompt | `--mode=rerun-prompt --prompt=<name> --version=<new>` | Re-runs a specific prompt on affected rows; bumps `prompt_version` on new outputs; leaves old outputs in place (marked non-current). Enables prompt iteration without rerunning the whole pipeline. |

Default mode is `resume`. Operator typically runs `--mode=full` once, then `--mode=resume` to catch failed rows, then targeted modes for iteration.

---

## 5. Idempotency Rules

Every stage is idempotent. Guarantees:

- **Scrape:** duplicate detection via `source_raw.url` (exact match) and `source_raw.content_hash` (SHA-256 over normalized text). Re-running never creates duplicate rows.
- **Classification / extraction:** a row is processed only if its `status` is in the expected input set. Already-processed rows are skipped. To force reprocessing, use `rerun-prompt` mode.
- **SEC enrichment:** sec-api.io adapter checks `source_raw.content_hash` before insert. Multiple transactions referencing the same filing share the same `source_raw` row via the `transaction_source` link table (resolved at aggregation).
- **Clustering:** deterministic — same inputs produce same cluster IDs. Cluster IDs are content-derived, not sequential.
- **Aggregation:** aggregated transactions are keyed on cluster ID. Re-aggregation overwrites prior transaction row and marks old one non-current.
- **Summary / rationale:** new outputs marked `is_current = true`; prior outputs flipped to `is_current = false`. History preserved.

Practical implication: the operator can kill the pipeline at any point and re-run with `--mode=resume` without data loss or corruption.

---

## 6. Error Posture

**Never halt on a single-row error.** If an individual row fails any stage:

1. Log the failure to `logs/<stage>_<run_id>.log` with the row ID, stage, error type, and (for LLM failures) the raw response.
2. Mark the row's status with the appropriate `_FAILED` variant.
3. Continue to the next row.

**Only halt on infrastructure failures:**

- API authentication failure (401/403 from selected LLM provider or sec-api.io) — halt with clear error.
- Database connection failure — halt with clear error.
- Consecutive rate-limit errors beyond configured threshold — halt, log, exit with non-zero status.

Run summary at end of every `run.py` invocation:

```
Pipeline run complete.
  Duration: 1h 42m
  Sources fetched: 100
  Relevant: 78 / 100
  Extracted: 76 / 78
  Clustered: 71 clusters from 76 extractions (5 merged duplicates)
  Transactions created: 71
  Summaries generated: 71
  Rationales tagged: 71
  Failures: 2 (logs/extract_xxx.log)
  Export: exports/transactions_20260423_120000.csv
```

---

## 7. Configuration

From `.env`:

| Variable | Purpose |
| :--- | :--- |
| `LLM_PROVIDER` | LLM provider selector: `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | Anthropic calls when `LLM_PROVIDER=anthropic` |
| `OPENAI_API_KEY` | OpenAI Responses API calls when `LLM_PROVIDER=openai` |
| `SEC_API_KEY` | sec-api.io |
| `OPERATOR_CONTACT_EMAIL` | Used in User-Agent string |
| `OPUS_MODEL`, `HAIKU_MODEL` | Anthropic model IDs |
| `OPENAI_RELEVANCY_MODEL` | OpenAI model for relevancy filter |
| `OPENAI_CLASSIFICATION_MODEL` | OpenAI model for deal type, aggregation, and summaries |
| `OPENAI_EXTRACT_MODEL` | OpenAI model for high-confidence extraction |
| `OPENAI_LEGAL_EXTRACT_MODEL` | OpenAI model for agreement section extraction |
| `OPENAI_REASONING_MODEL` | OpenAI model for low-confidence extraction and rationale tagging |
| `MAX_FETCHES` | PR Newswire cap (default 100) |
| `DB_PATH` | SQLite file location (default `data/ma_mvp.db`) |
| `LOG_LEVEL` | INFO / DEBUG |
| `RUN_ID_PREFIX` | Optional, for labeling runs |
| `AGGREGATION_READ_SOURCE` | Stage 9 read path: `staging` or `observation` |

All pipeline stages log under a shared `run_id` (UTC timestamp + optional prefix). Every log file, every inserted row's audit fields, and the run summary reference the same `run_id`.

---

## 8. Schema SQL Compatibility

This pipeline expects the v0.2 schema enumerations from Drop 2.1:

- `deal_type → {ACQUISITION, MERGER, SPIN_SPLIT, REVERSE_MERGER, JOINT_VENTURE, MINORITY_INVESTMENT, UNKNOWN}`
- `value_type → {EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED}`
- `target_type → {STANDALONE_COMPANY, BUSINESS_UNIT, SUBSIDIARY}`
- `target_status → {PUBLIC, PRIVATE, SUBSIDIARY_OF_PUBLIC, SUBSIDIARY_OF_PRIVATE, UNKNOWN}`
- `acquirer_type → {STRATEGIC_CORPORATE, PRIVATE_EQUITY, VENTURE_CAPITAL, SOVEREIGN_WEALTH_FUND, PENSION_FUND, HEDGE_FUND, FAMILY_OFFICE, INDIVIDUAL, MANAGEMENT, EMPLOYEE_GROUP, SPAC, CONSORTIUM, PE_PORTFOLIO, OTHER_FINANCIAL_SPONSOR, UNKNOWN}`
- `consideration_components.form → {CASH, ACQUIRER_STOCK, TARGET_STOCK, EARNOUT, CVR, DEBT_ASSUMED, RETAINED_EQUITY, OTHER}`
- `termination_fees` split into `target_fee_amount`, `target_fee_percentage`, `acquirer_fee_amount`, `acquirer_fee_percentage`
- `go_shop` as `has_go_shop` + `go_shop_period_days`

The `schema/001_initial.sql` DDL reflects these. If DDL and prompts diverge, prompts are the source of truth — update DDL to match.

---

## 9. Pipeline Flow Diagram

```
                    ┌──────────────────┐
                    │ PR Newswire      │
                    │ (M&A category)   │
                    └──────────┬───────┘
                               │ (Stage 1: scrape)
                               ▼
                    ┌──────────────────┐
                    │ source_raw       │
                    │ source_status    │
                    │ = FETCHED        │
                    └──────────┬───────┘
                               │ (Stage 2: LLM relevancy)
                               ▼
                    ┌──────────────────┐
                    │ RELEVANT rows    │
                    └──────────┬───────┘
                               │ (Stage 3: LLM deal_type)
                               ▼
                    ┌──────────────────┐
                    │ staging_extract  │
                    │ status =         │
                    │ CLASSIFIED       │
                    └──────────┬───────┘
                               │ (Stage 4: LLM HC extract)
                               ▼
                    ┌──────────────────┐
                    │ status =         │
                    │ HC_EXTRACTED     │
                    └──────────┬───────┘
                               │ (Stage 5: Python SEC trigger detect)
                    ┌──────────┴───────┐
                    │                  │
          SEC_TRIGGERED          SEC_NOT_TRIGGERED
                    │                  │
           (Stage 6: sec-api)          │
                    │                  │
          ┌─────────▼─────────┐        │
          │ SEC sources added │        │
          │ to source_raw     │        │
          │ status =          │        │
          │ SEC_ENRICHED      │        │
          └─────────┬─────────┘        │
                    │                  │
                    └──────────┬───────┘
                               │ (Stage 7: LLM LC extract)
                               ▼
                    ┌──────────────────┐
                    │ status =         │
                    │ LC_EXTRACTED     │
                    └──────────┬───────┘
                               │ (Stage 8: entity cluster)
                               ▼
                    ┌──────────────────┐
                    │ transaction_     │
                    │ cluster_id       │
                    │ assigned         │
                    └──────────┬───────┘
                               │ (Stage 9: aggregate + conflict resolve)
                               ▼
                    ┌──────────────────┐
                    │ transaction rows │
                    │ (canonical)      │
                    └──────────┬───────┘
                               │
                    ┌──────────┴───────┐
          (Stage 10:           (Stage 11:
          Opus summary)        Opus rationale)
                    │                  │
                    ▼                  ▼
              summary           rationale_tag
                    │                  │
                    └──────────┬───────┘
                               │ (Stage 12: export)
                               ▼
                    ┌──────────────────┐
                    │ exports/*.csv    │
                    └──────────────────┘
```

---

## 10. Acceptance Criteria (for MVP run)

First production run against 100 PRs from PR Newswire:

| Metric | Target |
| :--- | :--- |
| Runtime (end-to-end) | < 2 hours |
| Relevancy accuracy (vs gold) | > 95% |
| Parties extracted correctly (vs gold) | > 95% |
| Deal type classified correctly (vs gold) | > 90% |
| Announced date extracted (vs gold) | > 98% |
| Value + value_type extracted (vs gold) | > 90% |
| Dedup precision (no false merges) | > 95% |
| Prompt failure rate | < 2% |

Gold set is graded by the operator (not by Claude — avoids the model agreeing with itself). Tiered coverage: full review on parties / deal_type / value; spot-check 20/100 on date / rationale. See `evaluation.md`.

---

## 11. Out of Scope for MVP

- Parallelism / concurrency. Sequential processing only. At 100-PR scale, runtime fits in the < 2h budget.
- Incremental scraping beyond the category listing (no historical backfill).
- Non-PR Newswire sources (BusinessWire, direct company feeds, etc.).
- Non-US SEC filings (Form 6-K, foreign private issuer forms).
- Per-security consideration model (pending T1 source review — see goal doc §8).
- Target financial hydration from XBRL.
- Premium calculation for take-privates (target pre-announcement market cap lookup).
- Entity resolution beyond name + date matching (no domain-based matching, no ticker-based matching in cluster logic yet).
- Real-time or scheduled runs. Operator triggers manually.
- User-facing UI. CSV export is the interface.

---

## 12. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
