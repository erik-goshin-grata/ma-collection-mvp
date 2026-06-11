# OpenAI Validation Runbook

## Objective

Validate the existing M&A collection pipeline with OpenAI as the LLM provider while preserving Anthropic as the rollback provider.

This validation must not change prompts, schemas, extraction semantics, aggregation logic, or the data model.

## Baseline

Current baseline commit:

```text
a24f1dc docs: record 3.32a completion and validation

Company repo:
Add to chat
https://github.com/erik-goshin-grata/ma-collection-mvp

Setup From Fresh Clonebash



git clone https://github.com/erik-goshin-grata/ma-collection-mvp.git
cd ma-collection-mvp
git checkout a24f1dc

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Use Python 3.11 or newer. Do not use macOS system Python 3.9.
Environment VariablesCreate a local .env file from the template:
bash



cp .env.example .env

.env is local-only and must not be committed.
OpenAI Pathbash



LLM_PROVIDER=openai
OPENAI_API_KEY=...
SEC_API_KEY=...
OPERATOR_CONTACT_EMAIL=...
MAX_FETCHES=10
DB_PATH=data/openai_validation_10.db
RUN_ID_PREFIX=openai_validation_10
AGGREGATION_READ_SOURCE=staging

Optional OpenAI model overrides:
bash



OPENAI_RELEVANCY_MODEL=gpt-5-nano
OPENAI_CLASSIFICATION_MODEL=gpt-5-mini
OPENAI_EXTRACT_MODEL=gpt-5-mini
OPENAI_LEGAL_EXTRACT_MODEL=gpt-5.2
OPENAI_REASONING_MODEL=gpt-5.2

Anthropic Rollback Pathbash



LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
SEC_API_KEY=...
OPERATOR_CONTACT_EMAIL=...
MAX_FETCHES=10
DB_PATH=data/anthropic_validation_10.db
RUN_ID_PREFIX=anthropic_validation_10
AGGREGATION_READ_SOURCE=staging

Offline Smoke TestsRun these before live validation:
bash



python -m compileall .
python scripts/validate_llm_provider.py

The provider smoke test does not call Anthropic or OpenAI APIs. It validates imports, provider selection, OpenAI client instantiation, model routing, and JSON parsing compatibility.
Small OpenAI Live ValidationStart with 10 PRs:
bash



python run.py --mode=full

If successful, repeat with 25 PRs by updating:
bash



MAX_FETCHES=25
DB_PATH=data/openai_validation_25.db
RUN_ID_PREFIX=openai_validation_25

Then run:
bash



python run.py --mode=full

Use copied/local DBs only. Do not use or mutate a production database.
Anthropic ComparisonIf an Anthropic key is available, run the same validation size with:
bash



LLM_PROVIDER=anthropic
DB_PATH=data/anthropic_validation_10.db
RUN_ID_PREFIX=anthropic_validation_10

Then:
bash



python run.py --mode=full

Where possible, compare OpenAI and Anthropic using the same seeded PR set or the closest available baseline. Do not tune prompts during this comparison.
Comparison ChecklistCapture and compare:
Relevancy decisions
Deal type classification
Extraction completeness
JSON validity
Failure and retry counts
Prompt failure types
LLM provider and model used
Input tokens
Output tokens
Latency
Estimated cost
Final transaction count
Any canonical transaction differences
The pipeline logs provider, model, token counts, and latency for LLM calls. Estimated cost should be calculated from token counts using the provider/model prices current on the validation date.
Acceptance CriteriaOpenAI validation can be accepted when:
OpenAI run completes end-to-end on copied/local data.
No production DB is touched.
No prompt changes are made.
No schema changes are made.
No extraction semantics or aggregation behavior changes are made.
Cost and failure rate are captured.
Any output differences are reported rather than tuned away.
Anthropic remains available as rollback.
Non-GoalsDo not do any of the following during initial validation:
Change prompts
Change schemas
Change extraction semantics
Change aggregation logic
Change data model
Retune model mappings
Replace Anthropic support
Touch production DBs
Add to chat