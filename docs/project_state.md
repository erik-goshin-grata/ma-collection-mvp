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

**Open-deal monitoring (placeholder — later):**
- Capability we do not have: keep watching a deal in `ANNOUNCED` status for
  follow-on SEC filings until it closes or dies, and feed them back in. Today
  the pipeline is one-shot per discovery.
- Stop conditions are the lifecycle filings we already fetch: **2.01 (close)**
  and **1.02 (termination)** — keep these grouped with 1.01/8.01 when designing.
- Half-built already: the observation ledger's diff-surfacing (a value moving
  across filings over time) and the `event_history` ANNOUNCED→AMENDED→CLOSED/
  TERMINATED concept. Missing piece is the scheduler that re-checks EDGAR for
  new filings on still-open deals. Not designed; do not start without a decision.

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

### Finding (2026-08-12): §4.2's transaction-value/debt branch has never run on real data

`total_debt` exists on **no** database — not `pl_funding.db`, `ma_mvp.db`, `ma_valu8.db`, or
`ma_grata.db`. It is a manual column, never populated. So §4.2's control-path branch
(`transaction_value = equity_value + total_debt` at `pct_acquired ≥ 50`) **cannot fire against
any existing data** — every control deal takes the debt-unknown fallback (`transaction_value =
equity_value`, or null). The **second owed re-aggregation is therefore substantive, not a
formality**: the total-debt branch will run against real data for the *first time* only after
`total_debt` + `Cash_ST` extraction lands. The first re-aggregation (below) can only exercise the
stake-level `equity_value` change and the TV=equity fallback.

Target choice for re-aggregation follows *live data*, not interesting data: re-aggregation is
remediation of a mixed column someone reads, not a test of a code path (that's a unit test).
Live targets: `pl_funding.db` (75/91 rows on the changed funding/minority path) and `ma_mvp.db`
(config-live per `.env`; all 92 rows non-control = the changed `equity_value` path).
`ma_valu8.db` / `ma_grata.db` (control-heavy) are deferred unless someone reads them.

### Precondition (was missing from the work order): AGGREGATED→CLUSTERED reset

Aggregation derives `WHERE status='CLUSTERED'` then moves rows to `AGGREGATED`. A DB whose rows
are all `AGGREGATED` (which `pl_funding.db` and `ma_mvp.db` both are — 0 CLUSTERED) has nothing to
re-derive; step 7 would run clean against zero rows and report success — a silent no-op. **The
reset is a required, deliberate step; assert row counts before the reset, after it, and after the
run.**

### Phase-1 progress (2026-08-11/12): observation coverage + round currency landed

Steps 1–6 of `docs/workorder_code_2026_08_11.md` are complete and committed. The observation
write path now covers every field aggregation reads (funding group + tier-2/tier-3 wiring);
`round_currency` is captured and `deal_value_currency` is unanimity-or-null over three sources.
Step-6 parity on `pl_funding.db`: **zero canonical transaction diffs**. A residual +10 LLM-conflict
delta on six HC *descriptive* fields (target/acquirer name+description, ticker, per_share_price) is
**pre-existing** — verified identical on the pristine pre-backfill snapshot — and reflects the
observation model's finer per-source granularity, not this work. It is a latent-divergence item to
resolve before any switch to `AGGREGATION_READ_SOURCE=observation`, tracked separately from §4.2.

### FINDING (2026-08-12): implied_equity_value grossed up from an unqualified transaction_value — DECISION VIOLATION

Step-7 verification on `pl_funding.db` surfaced a live bug that violates a recorded decision.
`decisions.md` — **"Transaction Size as Universal Magnitude"** — states an unqualified source
figure "must never populate equity value or either implied value, because grossing up an
unqualified number and striking a multiple off it manufactures a figure no source ever qualified."
Aggregation does exactly that: on rows where the source states only an unqualified value (lands as
`transaction_value` with `transaction_value_basis='STATED'`) and no separate equity figure,
`_derive_*` grosses that value up into `implied_equity_value` via `transaction_value / pct_acquired`.

Confirmed instances (all `equity_value` NULL, `implied` = `transaction_value / pct` exactly):
- `pl_funding.db`: **TC Skyward** (pct 50, tv 1.946B → implied **3.892B**); **KG Mobility tc_616c**
  (pct 10, tv 75M → implied **750M**).
- `ma_mvp.db` (dry-run on copy, not yet live): **1 row** — Genesis Digital Assets (pct 38.3, tv
  500M → implied **1.305B**).

Severity: `implied_equity_value` is one of the two *legal multiple numerators*. These are
manufactured numerators one step from a manufactured multiple — the multiples gate held only
because no revenue/EBITDA denominator computed, not because the input was sound. **This is the
phase-3 evidence** (a manufactured numerator from our own data), in a different shape than the
predicted "multiple struck off a stake-level value."

Fix is owed and is a **design decision (Claude authors, Code lands)**: the derivation must not
gross an unqualified/`STATED` `transaction_value` into `implied_equity_value`. Correcting it needs
a small re-aggregation of the affected rows (2 on pl_funding, 1 on ma_mvp) afterward.

### FINDING (2026-08-12): KG Mobility double-counted — Stage 8 clustering miss

Two `transaction_record`s exist for the same KG Mobility ← Chery 10% deal (`tc_616c` from source 9,
`tc_9731` from source 151) — two source articles that did not cluster together. Separate from the
value model; a Stage 8 (`entity_cluster`) dedup gap affecting counts/aggregates. It also acted as an
accidental controlled experiment: the same figure routed as `equity_value` in one record (coherent
two-tier: 75M/750M, ratio exactly 100/pct) and as `transaction_value` in the other (the violation
above) — the first real-data instance of the classify-or-lose problem the **"Named Value Fields
Replace the Single Value Slot"** decision (accepted, unmigrated) was written to fix.

### Phase-3 framing note

Grata's silver header carries **named scalars** (`deal_value`, `reported_ev`, `amount_raised`,
`post_evaluation`) pivoting into `financial_metric` rows in gold — no single classify-or-lose slot,
so it does not have this defect. This is a place to **adopt from eng rather than push back** — worth
stating plainly in the phase-3 memo, since conceding where their model is better buys credibility for
where ours does.

Schema drift is now guarded by `scripts/test_schema_convergence.py`: `init_db` brings any DB
(a fresh one, a 001-base one, or a historical `data/*.db`) to one canonical schema — verified
across all 6 historical DBs.

_Retracted:_ an earlier note here flagged `v2_event_type` as undocumented CREATE/reality drift.
That was a false positive from reading `001_initial.sql` alone — it is defined in
`schema/002_v2_prompt_alignment.sql:18,69`. The schema of record is `schema/*.sql` collectively
(001 + 002 + 003) plus the db.py migration list; see decisions.md "Schema Sources of Record".
