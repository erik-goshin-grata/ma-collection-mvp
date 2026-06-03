# M&A Collection MVP

An independent MVP pipeline for collecting and structuring M&A transactions from public sources.

Discovery from PR Newswire's M&A category; enrichment from SEC 8-K Item 1.01 filings and Exhibit 2.1 merger agreements via sec-api.io; extraction and classification via the configured LLM provider.

Target scope for first production run: **100 press releases**, end-to-end in under 2 hours, with operator-graded acceptance criteria.

---

## Status

MVP. Pre-implementation — specs and prompt files are committed; pipeline code is to be implemented by Claude Code against these specs.

| Component | Status |
| :--- | :--- |
| Goal doc & schema | Committed |
| Adapter specs (PR Newswire, sec-api.io) | Committed |
| Prompt specs (8 files) | Committed |
| Pipeline orchestration spec | Committed |
| Entity resolution spec | Committed |
| Evaluation spec + gold set template | Committed |
| SQLite DDL (v0.2) | Committed |
| Python implementation | Pending |
| First production run | Pending |

---

## Directory Structure

```
ma-collection-mvp/
├── README.md                          # This file
├── mvp_goal_and_schema.md             # MVP scope, acceptance criteria, schema reference
├── specs/
│   ├── adapter_pr_newswire.md         # PR Newswire scraper spec
│   ├── adapter_sec_api.md             # sec-api.io enrichment spec
│   ├── pipeline.md                    # Orchestration state machine, run modes, error posture
│   ├── entity_resolution.md           # Name normalization, clustering, dedup
│   └── evaluation.md                  # Gold set methodology, scoring
├── prompts/
│   ├── prompt_conventions.md          # Shared conventions (JSON I/O, temperature, versioning)
│   ├── relevancy_filter.md            # Binary relevancy gate
│   ├── deal_type_classifier.md        # 7-type taxonomy classifier
│   ├── high_confidence_extraction.md  # Parties, dates, value, target financials
│   ├── low_confidence_extraction.md   # Advisors, consideration, flags, termination fees
│   ├── aggregation.md                 # Conflict resolution (tier tie-breaking)
│   ├── deal_summary.md                # 80-150 word natural-language summary
│   └── strategic_rationale.md         # 8-category rationale classifier
├── schema/
│   └── 001_initial.sql                # SQLite DDL, 12 tables, v0.2 enums
├── eval/
│   └── gold_set_template.csv          # Empty CSV template for operator labeling
├── .env.example                       # Required environment variables
└── .gitignore                         # Excludes .env, DB, logs, generated CSVs

Generated at runtime (not committed):
  data/ma_mvp.db                       # SQLite database
  exports/transactions_<run_id>.csv    # CSV exports of canonical transactions
  logs/                                # Per-stage, per-run log files
  notes/                               # Operator notes, post-run reviews
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

### 5. Initialize the database
```bash
mkdir -p data
python -c "import sqlite3; conn=sqlite3.connect('data/ma_mvp.db'); conn.executescript(open('schema/001_initial.sql').read()); conn.close()"
```

(The implementation will wrap this in a CLI command, but the one-liner above is sufficient for initial setup.)

---

## Running the Pipeline

The primary entry point is `run.py` with a mode flag. See `specs/pipeline.md` for the full list.

### First production run (100 PRs, end-to-end)
```bash
python run.py --mode=full
```

### Resume after a failure
```bash
python run.py --mode=resume
```

### Scrape only (no LLM calls)
```bash
python run.py --mode=scrape
```

### Re-run a specific prompt after revision
```bash
python run.py --mode=rerun-prompt --prompt=deal_type_classifier --version=0.3
```

See `specs/pipeline.md` §4 for the full mode reference.

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

1. Implementation — Claude Code builds the Python pipeline against these specs.
2. Sandbox validation — first test run on 5–10 PRs to debug adapter and prompt integration.
3. First production run — 100 PRs, end-to-end.
4. Gold set labeling — operator grades the sample.
5. Scorecard review — identify prompts needing revision.
6. Post-first-run T1 source review — examine raw 8-K and Exhibit 2.1 texts to inform v2 securities extraction scoping (per goal doc §8).

---

## License & Access

Private repository. Not licensed for redistribution.

---

## Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
