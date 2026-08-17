# M&A Collection MVP

A scalable MVP pipeline for collecting and structuring M&A **and funding** transactions from public sources.

Discovery from PR Newswire's M&A category (plus CSV-URL and PredictLeads ingest adapters); enrichment from SEC 8-K Item 1.01 filings and Exhibit 2.1 merger agreements via sec-api.io; extraction, classification, and summarization via the configured LLM provider (Anthropic or OpenAI), with per-stage model tiering.

## Purpose & positioning

This repo is a **validation harness and scalable MVP**, not the production system. Its job is to test LLMs, prompts, and data sources for extraction quality — proving out the taxonomy, the value model, and the source coverage on real deals before those decisions harden.

It runs **in parallel to the engineering team's production build**. The two tracks share goals and inform each other, but this repo is not a build target for eng and eng is not required to match it. **Interim differences between this MVP and the production system are expected** — schema names, enum sets, and field semantics will diverge at any given moment. When they matter, differences are recorded in `docs/` (see `docs/ML_differences_2026_08_01.md` and the decision/handoff docs).

Because the recurring failure mode here is *authored-but-never-run* drift, the code — `prompts/`, `stages/`, `schema/`, `run.py` — is authoritative for what actually runs. Design docs describe intent; the repo describes reality.

---

## Status

**Operational.** The full pipeline is implemented and has completed production and funding-path runs. Active workstream is the **transaction value model** (two-tier as-transacted vs 100%-basis valuation); see `docs/`.

Authoritative current-state docs, in freshness order:

| Doc | Role |
| :--- | :--- |
| `docs/project_state.md` | Living state, including discharged and owed re-aggregations |
| `docs/grata_v2_inventory_and_recommendations.md` | Newest Grata V2 inventory/recommendation reconciliation; recommendations are not automatically implemented |
| `docs/grata_v2_data_dictionary.md` | Newest Grata V2 transaction data dictionary draft |
| `docs/session_handoff_2026_08_12_field_coverage.md` | Latest handoff: field coverage, value-model evidence, Grata memo framing |
| `docs/decisions.md` | Authoritative decision log — source of truth |
| `docs/CONTEXT.md` | Code-derived pipeline contract (all stages, enums, bugs) |

`mvp_goal_and_schema.md` is the **original v0.1 scope doc and is superseded** — it describes a 100-deal proof loop and tables that were ultimately built as columns. Kept for history; do not treat as current.

| Component | Status |
| :--- | :--- |
| Discovery adapters (PR Newswire, CSV-URL, PredictLeads) | Implemented |
| SEC enrichment (8-K Item 1.01, Exhibit 2.1) | Implemented |
| Extraction / classification / summarization stages (14 + funding branch) | Implemented |
| Anthropic + OpenAI providers, per-stage model tiering | Implemented |
| SQLite schema (migrations 001–003) | Implemented |
| Funding path (VC / growth / venture debt) | Implemented; `funding_lc_extract` stage still pending |
| Two-tier value model | Design + §4.1/§4.2/§4.7 code landed; canonical `implied_enterprise_value` rewire implemented; first §4.2 re-aggregation discharged on live DBs; second re-aggregation owed after broader `total_debt` + `Cash_ST` extraction |

---

## Directory Structure

```
ma-collection-mvp/
├── README.md                          # This file
├── run.py                             # Primary entry point (pipeline orchestrator)
├── config.py                          # Config flags, model tiers, provider selection
├── db.py                              # SQLite access + migration application
├── adapters/                          # Source ingest
│   ├── pr_newswire.py                 #   PR Newswire M&A category scraper
│   ├── csv_url.py                     #   CSV-of-URLs test-ingest harness
│   └── sec_api.py                     #   sec-api.io SEC enrichment
├── stages/                            # 14 pipeline stages + funding branch (4b)
│   ├── relevancy_filter.py            #   Stage 2 — in-scope gate
│   ├── deal_type_classify.py          #   Stage 3 — v2_event_type classifier
│   ├── high_confidence_extract.py     #   Stage 4 — M&A extraction (excludes funding)
│   ├── funding_hc_extract.py          #   Stage 4b — funding extraction (VC/growth/venture debt)
│   ├── ... (sec_*, low_confidence, entity_cluster, aggregate, agreement_extract, summarize, rationale_tag, export)
├── prompts/                           # Versioned prompt files (.md) + loader (base.py)
├── lib/                               # Shared helpers (llm_client, field_priority, exhibit navigator, observation writer, ...)
├── schema/                            # SQLite DDL — 001_initial, 002_v2_prompt_alignment, 003_funding_path
├── scripts/                           # Reprocessors, backfills, validators, tests
├── specs/                             # Design specs (adapters, pipeline, entity resolution, evaluation)
├── docs/                              # Decisions, handoffs, current-state (see Status above)
├── eval/                             # Gold set methodology + scoring (score.py)
├── .env.example                       # Required environment variables
└── .gitignore                         # Excludes .env, DB, logs, generated CSVs

Generated at runtime (not committed):
  data/ma_mvp.db                       # SQLite database (DB_PATH env overrides)
  exports/transactions_<run_id>.csv    # CSV exports of canonical transactions
  logs/                                # Per-stage, per-run log files
```

---

## Prerequisites

- Python 3.11 or newer
- An Anthropic API key or OpenAI API key for LLM calls
- A sec-api.io API key (Personal & Startups tier or better)
- A working Git installation

---

## Setup

### 1. Clone the repo locally
```bash
git clone git@github.com:<username>/ma-collection-mvp.git
cd ma-collection-mvp
```

### 2. Create a Python virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies
Dependencies will be specified in `requirements.txt` at implementation time. Expected:
- `anthropic` — Claude API client
- `openai` — OpenAI Responses API client
- `requests` — HTTP client for PR Newswire and sec-api.io
- `trafilatura` — HTML-to-clean-text extraction
- `rapidfuzz` — fuzzy string matching for entity resolution
- `python-dotenv` — .env loader

```bash
pip install -r requirements.txt
```

### 4. Configure environment
```bash
cp .env.example .env
# Edit .env with your actual API keys and contact email
```

Required variables:
- `LLM_PROVIDER` — `anthropic` or `openai`; defaults to `anthropic`
- `ANTHROPIC_API_KEY` — required when `LLM_PROVIDER=anthropic`
- `OPENAI_API_KEY` — required when `LLM_PROVIDER=openai`
- `SEC_API_KEY` — from sec-api.io dashboard
- `OPERATOR_CONTACT_EMAIL` — used in the User-Agent header when scraping

SEC 8-K enrichment uses `SEC_LOOKBACK_DAYS` and `SEC_LOOKAHEAD_DAYS` around the
extracted announcement date. The default is 30 days back and 7 days forward so
news-derived events can still find issuer filings that predate the article.
`SEC_DATE_WINDOW_DAYS` remains as a legacy fallback for older local configs.

To migrate a local run from Anthropic to OpenAI, set:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Codex Enterprise access does not automatically authenticate this local Python
pipeline with OpenAI. The pipeline calls the OpenAI API directly through the
OpenAI SDK and requires an API key in `.env`.

OpenAI model defaults can be overridden per stage:

| Variable | Default | Used for |
| :--- | :--- | :--- |
| `OPENAI_RELEVANCY_MODEL` | `gpt-5-nano` | Relevancy filter |
| `OPENAI_CLASSIFICATION_MODEL` | `gpt-5-mini` | Deal type, aggregation, summaries |
| `OPENAI_EXTRACT_MODEL` | `gpt-5-mini` | High-confidence extraction |
| `OPENAI_LEGAL_EXTRACT_MODEL` | `gpt-5.2` | Agreement section extraction |
| `OPENAI_REASONING_MODEL` | `gpt-5.2` | Low-confidence extraction and rationale tagging |

The OpenAI provider uses the Responses API with Structured Outputs for the
shared JSON-returning prompt path. Existing prompts, prompt versions, validation
rules, and aggregation rules remain unchanged.

Offline provider smoke test:

```bash
python scripts/validate_llm_provider.py
```

Stage 9 aggregation reads the observation ledger by default:

```bash
AGGREGATION_READ_SOURCE=observation
```

This runs Stage 9 from `transaction_field_observation`. It is the only read path
that carries a per-fact source key, so it is the only one that can keep multiple
independently typed values from a single source distinct.

The legacy staging read remains available for rollback or debugging:

```bash
AGGREGATION_READ_SOURCE=staging
```

### 5. Initialize the database
```bash
mkdir -p data
python -c "import sqlite3; conn=sqlite3.connect('data/ma_mvp.db'); conn.executescript(open('schema/001_initial.sql').read()); conn.close()"
```

(The implementation will wrap this in a CLI command, but the one-liner above is sufficient for initial setup.)

---

## Running the Pipeline

The primary entry point is `run.py --mode=<mode>` (default `resume`). Modes map to stage ranges in `run.py` (`_MODE_STAGES`); `specs/pipeline.md` has the design-level reference.

| Mode | Stages run |
| :--- | :--- |
| `full` | Full pipeline, all stages |
| `resume` *(default)* | Extraction stages onward (2→14); SEC stages no-op on non-SEC rows |
| `scrape` | Discovery only, no LLM calls |
| `extract` | Extraction stages (relevancy → SEC enrich, incl. funding 4b) |
| `aggregate` | Cluster + aggregate (8–9) |
| `sec-documents` | Expanded SEC filing fetch (10) |
| `agreement-extract` / `agreement-rerun` | Merger-agreement extraction (11); rerun picks up unextracted docs |
| `generate` | Summaries + rationale (12–13) |
| `export` | CSV export (14) |
| `rerun-prompt --prompt <name> --version <v>` | Re-run one prompt at a new version |

```bash
# Examples
python run.py --mode=full
python run.py --mode=resume
python run.py --mode=scrape
python run.py --mode=rerun-prompt --prompt=deal_type_classifier --version=0.3
```

The DB path defaults to `data/ma_mvp.db`; override with the `DB_PATH` env var to run against a test corpus (e.g. `data/pl_funding.db`).

---

## Reviewing Output

### CSV export
After a successful run, `exports/transactions_<run_id>.csv` contains one row per canonical transaction, flattened from the `transaction_record` table.

### Raw source texts
The `source_raw` table contains the original PR text, 8-K Item 1.01 text, and Exhibit 2.1 text. For the post-first-run T1 review (per goal doc §8), query:

```sql
SELECT source_raw_id, source_type, title, length(clean_text) AS chars
FROM source_raw
WHERE source_type IN ('SEC_8K_ITEM_101', 'SEC_EXHIBIT_21')
ORDER BY source_type, length(clean_text) DESC;
```

### Scorecard
After labeling a gold set CSV:
```bash
python eval/score.py --gold eval/gold_set_20260423.csv --run-id run_20260423_120000
```
Writes `eval/scorecard_<run_id>.md` with per-field precision and dedup metrics.

---

## Conventions

### Prompt versioning
Prompt files use semantic versions (e.g., `0.1`, `0.2`, `1.0`). Every extraction output records the prompt version that produced it (`staging_extraction.prompt_version`), which lets old rows be rerun through new prompts and compared. See `prompts/prompt_conventions.md`.

### Schema versioning
Enumerations are enforced at the application layer, not via SQLite CHECK constraints. This keeps DDL stable across prompt iterations — changing an enum is a prompt-file change, not a migration. If the enum set needs to change formally, a new DDL version is created in `schema/` (e.g., `002_enum_update.sql`).

### Error posture
No pipeline stage halts on an individual row failure. Failures are logged to `extraction_failure_log` and `logs/`, and the pipeline continues. Only infrastructure-level failures (invalid API keys, database unreachable, persistent rate limits) halt a run.

---

## Next Steps

Current queue (see `docs/project_state.md` for the freshest state):

1. Currency + period anchoring — blocks the implied-valuation tier.
2. `total_debt` + `Cash_ST` as `target_financials` metrics — activates the dormant total-debt branch; lets `net_debt` derive.
3. Review export value-model surface — expose current value-model and funding fields without treating `_v2` shadow columns as reviewer-facing Grata enum fields.
4. `transaction_size` + its export column — the reviewer-facing deliverable now that the first §4.2 re-aggregation is discharged.
5. Legacy value-field inventory/reorganization — `enterprise_value` is now a compatibility mirror of canonical `implied_enterprise_value`; downstream cleanup remains deferred.

Owed operational work: the **second re-aggregation** after `total_debt` + `Cash_ST` lands — route through `run.py` so migrations apply first. See `docs/project_state.md`.

---

## License & Access

Private repository. Not licensed for redistribution.

---

## Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-08-11 | Sync to operational reality — purpose/positioning, status, directory structure, run modes, next steps |
