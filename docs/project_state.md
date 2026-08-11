# Project State
**Updated:** 2026-07-28
**Last commit:** `2f05aa6`
**Branch:** `main`

---

## Current Drop: V2 Alignment + Funding Path

Pipeline is fully operational with V2 prompt vocabulary and a new funding
event extraction path. All 14 stages are wired and running.

---

## Schema

Three migrations applied to `data/ma_mvp.db`:

| Migration | Applied | Contents |
|---|---|---|
| `001_initial.sql` | Yes | Base schema — 16 tables including `staging_extraction`, `transaction_record`, `transaction_field_observation`, `staging_investor` (3.32a participant tables), etc. |
| `002_v2_prompt_alignment.sql` | Yes | 12 new nullable V2 columns on `staging_extraction` and `transaction_record` |
| `003_funding_path.sql` | Yes | `staging_investor` table + funding scalar columns on `staging_extraction` and `transaction_record` |

---

## Prompt Versions

See `docs/prompt_versions.md` for full cross-prompt version table.

Current versions:
- `relevancy_filter` 0.5
- `deal_type_classifier` 0.6
- `high_confidence_extraction` 0.12
- `funding_hc_extraction` 0.1 (NEW)
- `funding_lc_extraction` 0.1 (NEW — stage code not yet written)
- `low_confidence_extraction` 0.5
- `aggregation` 0.4
- `deal_summary` 0.9
- `strategic_rationale` 0.5
- `prompt_conventions` 0.3
- Agreement prompts (5): unchanged at current versions

---

## Stage Status

| Stage | Module | Notes |
|---|---|---|
| 1 | `scrape_pr_newswire` | Running |
| 2 | `relevancy_filter` | v0.5 — funding events in scope |
| 3 | `deal_type_classify` | v0.6 — V2 event types, funding types classifiable |
| 4a | `high_confidence_extract` | v0.12 — V2 vocabulary, new fields |
| 4b | `funding_hc_extract` | v0.1 — NEW; routes VC_ROUND/GROWTH_EQUITY/VENTURE_DEBT |
| 5 | `sec_trigger_detect` | Running |
| 6 | `sec_enrich` | Extended lookback/lookahead window |
| 7 | `low_confidence_extract` | v0.5 |
| 8 | `entity_cluster` | Running |
| 9 | `aggregate` | Extended for V2 + funding fields; multiples skip for funding |
| 10 | `sec_documents` | Running |
| 11 | `agreement_extract` | Running |
| 12 | `summarize` | v0.9 — V2 input fields; M&A framing only (funding framing v0.10 pending) |
| 13 | `rationale_tag` | v0.5 |
| 14 | `export` | Running |

---

## Configuration

Key config flags:
- `AGGREGATION_READ_SOURCE=staging` — default. Switch to `observation` after next validation run.
- `LLM_PROVIDER=anthropic` — default. OpenAI provider available via `LLM_PROVIDER=openai`.
- `opus_model=claude-opus-4-7`
- `haiku_model=claude-haiku-4-5-20251001`
- `SEC_LOOKBACK_DAYS=30` / `SEC_LOOKAHEAD_DAYS=7`

---

## Known Gaps / Deferred Work

**Funding path (partial):**
- `stages/funding_lc_extract.py` — not written; prompt exists
- `adapters/sec_api.py` Form D extension — deferred
- `deal_summary` funding framing — deferred to v0.10

**V2 schema alignment (deferred):**
- `transaction_participant` → `transaction_party` rename (3.32a tables)
- `financial_metric` as deal value write target
- `transaction_event_history` as date write target
- Agreement observation supersession

**Awaiting eng:**
- 13 open questions in `docs/enum_schema_gaps.md`
- Key: `POST_MONEY_VALUATION` in MetricType, `round_stage_category` enum,
  `PartyRole` additions, multiple display precedence rule

**Validation:**
- V2 prompt changes not yet validated on known sources
- Funding path test corpus not yet built
- `SILVER_TRANSACTION_HEADER_SCHEMA` conformance not yet tested

---

## Next Steps

1. Validate V2 prompt changes — run July 22 validation DB through v0.6/v0.12
2. Build funding test corpus — 10-15 announcements across event types
3. Check eng responses on `docs/enum_schema_gaps.md`
4. Write `stages/funding_lc_extract.py`
5. Write `deal_summary` v0.10 funding framing block
6. Apply `AGGREGATION_READ_SOURCE=observation` after successful validation run

## Pending re-aggregation — §4.2 (2026-08-10)

§4.2 landed as CODE only (equity_value now stake-level; transaction_value threshold;
+ total_debt / transaction_value / transaction_value_basis / pct_acquired_source columns).
Aggregation is **incremental** (only CLUSTERED rows are derived; existing AGGREGATED rows
keep old semantics), so a normal run does NOT re-derive existing rows. **Two deliberate
re-aggregations are owed — track here, not just in conversation; forgetting leaves a
permanently mixed column, which is exactly what "re-aggregate, don't stamp" avoids:**

1. **After §4.2:** re-aggregate the affected clusters once (both changes together — never
   between them). Route through `run.py` (or call `init_db()` first) so `_apply_migrations`
   adds the new columns before writing; assert the columns are present before running.
2. **After total_debt + cash extraction (the next piece):** re-aggregate again to populate
   the transaction_value gross-debt branch (dormant until then) and derive net_debt from
   gross − cash.

Expect **unattributable diffs** from re-aggregation: the DB holds several historical
derivation semantics (aggregation has always been incremental), not just the two §4.2
creates. Diffs that don't trace to §4.2 are expected, not regressions.

Schema drift is now guarded by `scripts/test_schema_convergence.py`: `init_db` brings any DB
(a fresh one, a 001-base one, or a historical `data/*.db`) to one canonical schema — verified
across all 6 historical DBs.

_Retracted:_ an earlier note here flagged `v2_event_type` as undocumented CREATE/reality drift.
That was a false positive from reading `001_initial.sql` alone — it is defined in
`schema/002_v2_prompt_alignment.sql:18,69`. The schema of record is `schema/*.sql` collectively
(001 + 002 + 003) plus the db.py migration list; see decisions.md "Schema Sources of Record".
