# Project State
**Updated:** 2026-08-11
**Last commit:** `02db22e`
**Branch:** `main`

---

## Current Drop: Transaction Value Model

V2 prompt vocabulary and the funding event path are landed and operational (prior
drop). The active workstream is the **two-tier transaction value model** — Tier 1
as-transacted (`equity_value` stake-level, `transaction_value`, `transaction_size`)
vs Tier 2 100%-basis (`implied_equity_value`, `implied_enterprise_value`, the only
legal multiple numerators). Design is landed in `docs/decisions.md` (2026-08-10
entries) and `docs/spec_transaction_value_model.md`; code landings §4.1/4.2/4.7 are
in; two re-aggregations are owed (see bottom of this doc). All 14 stages + the
funding branch remain wired and running.

---

## Schema

Schema of record is `schema/*.sql` (001+002+003) **plus** the additive ALTERs in
`db.py _apply_migrations`, which run on every `init_db`. `test_schema_convergence.py`
asserts all paths reach one canonical column set (verified across 6 historical DBs).

| Source | Applied | Contents |
|---|---|---|
| `001_initial.sql` | Yes | Base schema — 16 tables incl. `staging_extraction`, `transaction_record`, `transaction_field_observation`, `staging_investor` |
| `002_v2_prompt_alignment.sql` | Yes | 12 new nullable V2 columns on `staging_extraction` and `transaction_record` |
| `003_funding_path.sql` | Yes | `staging_investor` table + funding scalar columns |
| `db.py _apply_migrations` ALTERs | Yes | Value-model columns not in any .sql file: `investment_amount`, `deal_value_currency`, `total_debt`, `transaction_value`, `transaction_value_basis`, `pct_acquired_source` (db.py §4.1/§4.2 blocks) |

---

## Prompt Versions

See `docs/prompt_versions.md` for full cross-prompt version table.

Current versions:
- `relevancy_filter` 0.5
- `deal_type_classifier` 0.6
- `high_confidence_extraction` 0.13 (0.13: §4.1 capital-raised precondition + `round_size` capture)
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
| 4a | `high_confidence_extract` | v0.13 — V2 vocabulary; §4.1 capital-raised precondition + `round_size` |
| 4b | `funding_hc_extract` | v0.1 — NEW; routes VC_ROUND/GROWTH_EQUITY/VENTURE_DEBT |
| 5 | `sec_trigger_detect` | Running |
| 6 | `sec_enrich` | Extended lookback/lookahead window |
| 7 | `low_confidence_extract` | v0.5 |
| 8 | `entity_cluster` | Running |
| 9 | `aggregate` | V2 + funding fields; multiples gated off funding; value-model §4.1/4.2 landed (stake-level `equity_value`, `transaction_value` at `pct_acquired ≥ 50`) |
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
- `sonnet_model=claude-sonnet-4-6` — Sonnet tier (classify/HC/funding-HC/summary), wired 2026-08-03
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

**Awaiting eng input:**
- Field inventory + parity test — method decided (generate by origin);
  `docs/spec_field_parity_test.md` is landed but **ON HOLD** pending eng-team
  info on schema/enum locations (Erik supplying). See value-model handoff §6 and
  decisions.md "Field Inventory Method and Naming Conventions".
- _(The former `docs/enum_schema_gaps.md` reference was removed — that file does
  not exist in the repo. Funding-valuation scope since resolved:
  `post_money_valuation` only, no implied EV/multiples — decisions.md
  "Funding Valuation Scope".)_

**Validation:**
- Funding path test corpus **built** — `data/pl_funding.db` (68 stranded VC/venture-debt
  rounds + the KG / 10x `MINORITY_INVESTMENT` cases); funding run surfaced bugs #5–#9,
  all fixed and committed.
- Currency + period anchoring not yet validated (blocks the implied tier).
- Two owed re-aggregations not yet run (see bottom of this doc).

---

## Next Steps

Current queue (source: `docs/session_handoff_2026_08_10_value_model.md`):

1. **Currency + period anchoring** (§2.10 items 1–2) — blocks the implied tier;
   extracting `total_debt`/`cash` needs period anchoring anyway, so one piece of work.
2. **`total_debt` + `cash` (Cash_ST) as `target_financials` metrics** — with period type
   and `period_end_date`; activates the dormant `transaction_value` total-debt branch and
   lets `net_debt` derive from `total_debt − Cash_ST`.
3. **`transaction_size` + export column** — the reviewer-facing deliverable; depends on
   the §4.2 re-aggregation (`docs/handoff_transaction_size.md`).
4. **EV rewire** (§4.3) — parked until step 1 clears. Guard: do not export the derived EV
   and do not repoint multiples at it before its fix exists.

Owed operational: the **two re-aggregations** below — route through `run.py` so
`_apply_migrations` adds the columns first.

Still open, lower priority: write `stages/funding_lc_extract.py`; `deal_summary` v0.10
funding framing; apply `AGGREGATION_READ_SOURCE=observation` after a validation run.

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
2. **After total_debt + Cash_ST extraction (the next piece):** re-aggregate again to populate
   the transaction_value total-debt branch (dormant until then) and derive net_debt from
   `total_debt − Cash_ST` (cash is defined as `Cash_ST` — see decisions.md "Debt and Cash
   Inputs").

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
