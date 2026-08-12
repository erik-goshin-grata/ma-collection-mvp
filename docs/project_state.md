# Project State
**Updated:** 2026-08-12
**Last commit:** this commit
**Branch:** `main`

---

## Current Drop: Transaction Value Model + Minority Cleanup

V2 prompt vocabulary and the funding event path are landed and operational (prior
drop). The active workstream is the **two-tier transaction value model** — Tier 1
as-transacted (`equity_value` stake-level, `transaction_value`, `transaction_size`)
vs Tier 2 100%-basis (`implied_equity_value`, `implied_enterprise_value`, the only
legal multiple numerators). Design is landed in `docs/decisions.md` (2026-08-10
entries) and `docs/spec_transaction_value_model.md`; code landings §4.1/4.2/4.7 are
in. The first §4.2 re-aggregation is discharged on the two live DBs; the second
re-aggregation remains owed after `total_debt` + `Cash_ST` extraction. All 14
stages + the funding branch remain wired and running.

Minority cleanup is now accepted and implemented in this validation harness:
`MINORITY_INVESTMENT` is no longer a validated core classifier output, minority
status is derived as `is_minority`, and explicit ownership step-ups are captured
with nullable `stake_transition_type` (`NULL`, not `UNKNOWN`, means insufficient
explicit evidence). This does not change Grata production schema/enums.

---

## Schema

Schema of record is `schema/*.sql` (001+002+003) **plus** the additive ALTERs in
`db.py _apply_migrations`, which run on every `init_db`. `test_schema_convergence.py`
asserts all paths reach one canonical column set (verified across 10 historical DBs).

| Source | Applied | Contents |
|---|---|---|
| `001_initial.sql` | Yes | Base schema — 16 tables incl. `staging_extraction`, `transaction_record`, `transaction_field_observation`, `staging_investor` |
| `002_v2_prompt_alignment.sql` | Yes | 12 new nullable V2 columns on `staging_extraction` and `transaction_record` |
| `003_funding_path.sql` | Yes | `staging_investor` table + funding scalar columns |
| `db.py _apply_migrations` ALTERs | Yes | Value-model / harness columns not in historical DBs: `investment_amount`, `deal_value_currency`, `total_debt`, `transaction_value`, `transaction_value_basis`, `pct_acquired_source`, `is_minority`, `stake_transition_type`, precision/round observation columns |

---

## Prompt Versions

See `docs/prompt_versions.md` for full cross-prompt version table.

Current versions:
- `relevancy_filter` 0.5
- `deal_type_classifier` 0.7 (minority-as-flag routing; no core `MINORITY_INVESTMENT`)
- `high_confidence_extraction` 0.14 (0.14: nullable explicit `stake_transition_type`)
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
| 3 | `deal_type_classify` | v0.7 — V2 event types, funding types classifiable; `MINORITY_INVESTMENT` rejected as core output |
| 4a | `high_confidence_extract` | v0.14 — V2 vocabulary; §4.1 capital-raised precondition + `round_size`; nullable `stake_transition_type` when explicit |
| 4b | `funding_hc_extract` | v0.1 — NEW; routes VC_ROUND/GROWTH_EQUITY/VENTURE_DEBT |
| 5 | `sec_trigger_detect` | Running |
| 6 | `sec_enrich` | Extended lookback/lookahead window |
| 7 | `low_confidence_extract` | v0.5 |
| 8 | `entity_cluster` | Running |
| 9 | `aggregate` | V2 + funding fields; multiples gated off funding; value-model §4.1/4.2 landed; derives `is_minority` from `stake_transition_type` before pct fallback |
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

**Internal consistency guardrails:**
- Field inventory + parity test — method decided (generate by origin);
  `docs/spec_field_parity_test.md` is landed and its ON HOLD gate is lifted.
  Checks 2–4 test this repo's internal consistency and do not depend on Grata
  schema/enum locations. See value-model handoff §6 and decisions.md
  "Field Inventory Method and Naming Conventions".
- _(The former `docs/enum_schema_gaps.md` reference was removed — that file does
  not exist in the repo. Funding-valuation scope since resolved:
  `post_money_valuation` only, no implied EV/multiples — decisions.md
  "Funding Valuation Scope".)_

**Validation:**
- Minority cleanup validated on a four-story live set:
  Lumina/TNQTech now yields `ACQUISITION`, `pct_acquired=20`,
  `stake_transition_type=MAJORITY_ACQUIRE_REMAINING`, `is_minority=0`;
  LMPG/Platinum remains evidence-limited and stable; Lydian remains `VC_ROUND`;
  Paradium/InfoSentience remains full `ACQUISITION`.
- Funding path test corpus **built** — `data/pl_funding.db` (68 stranded VC/venture-debt
  rounds + the KG / 10x `MINORITY_INVESTMENT` cases); funding run surfaced bugs #5–#9,
  all fixed and committed.
- Observation coverage and round-currency work is landed: the observation write
  path covers every field Stage 9 reads, Stage 4b dual-writes funding observations,
  and `round_currency` feeds the three-source unanimity rule for `deal_value_currency`.
- The implied-equity violation found during §4.2 verification is fixed:
  `implied_equity_value` derives from `equity_value` only, never from an
  unqualified `transaction_value` or `post_money_valuation`.
- Currency + period anchoring not yet validated (blocks the implied tier).
- First §4.2 re-aggregation discharged on `pl_funding.db` and `ma_mvp.db`; the
  second re-aggregation remains owed after `total_debt` + `Cash_ST`.

---

## Next Steps

Current queue (source: `docs/session_handoff_2026_08_10_value_model.md`):

1. **Currency + period anchoring** (§2.10 items 1–2) — blocks the implied tier;
   extracting `total_debt`/`Cash_ST` needs period anchoring anyway, so one piece of work.
2. **`total_debt` + `Cash_ST` as `target_financials` metrics** — with period type
   and `period_end_date`; activates the dormant `transaction_value` total-debt branch and
   lets `net_debt` derive from `total_debt − Cash_ST`.
3. **Review export value-model surface** — expose the current value-model fields
   (`equity_value`, `implied_equity_value`, `transaction_value`, `investment_amount`,
   `deal_value_currency`, and funding round fields) without treating `_v2` shadow
   columns as reviewer-facing Grata enum fields.
4. **`transaction_size` + export column** — the reviewer-facing deliverable now that
   the first §4.2 re-aggregation is discharged (`docs/handoff_transaction_size.md`).
5. **EV rewire** (§4.3) — parked until step 1 clears. Guard: do not export the derived EV
   and do not repoint multiples at it before its fix exists.

Owed operational: the **second re-aggregation** below — route through `run.py` so
`_apply_migrations` adds the columns first.

Still open, lower priority: write `stages/funding_lc_extract.py`; `deal_summary` v0.10
funding framing; apply `AGGREGATION_READ_SOURCE=observation` after a validation run.

## Pending re-aggregation — §4.2 (2026-08-10)

### DISCHARGE (2026-08-12): first §4.2 re-aggregation done on the two live DBs

The first owed re-aggregation (item 1 below) is **discharged on `pl_funding.db` and `ma_mvp.db`** —
the two live targets. Both routed through `init_db` (columns asserted), full AGGREGATED→CLUSTERED
reset with row-count assertions, real Stage 9, diff classified by decision lineage. `ma_valu8.db`
/ `ma_grata.db` remain deferred (control-heavy fixtures, not read from). **The second re-aggregation
(item 2, after `total_debt` + `Cash_ST`) is still owed** — and per the dormancy finding above it is
substantive: §4.2's `EQUITY_PLUS_TOTAL_DEBT` branch has produced zero values on real data to date.

A mid-run verification surfaced a decision violation in `_derive_implied_equity` (below), which was
**fixed before `ma_mvp` ran** (`065a87d`). A corrective re-aggregation of `pl_funding.db` nulled its
manufactured implied values; `ma_mvp` re-aggregated with the fix in place, so it corrected by never
creating. Net implied-equity outcome:
- `pl_funding.db`: 9 violation values nulled (2 tv-fallback + **7 live `post_money`-branch** funding
  violations, incl. Base Power 13B, DeepX 3.14T); 3 legitimate equity-grossed values preserved.
- `ma_mvp.db`: 1 tv-fallback (Genesis) prevented; 3 equity-grossed preserved; 0 post-money.

**Run-note worth keeping:** the "exactly two value-model fields changed" assertion on the corrective
`pl_funding` pass *earned its place by failing* — it tripped at 9 rows, forcing the investigation
that found the 7 live `post_money` violations. A looser check would have passed and nobody would have
looked. And: any claim about how many rows hold a value must be measured against **state**, never
inferred from a NULL→val **diff** (the error that put a false "latent" claim into `decisions.md`,
since corrected).

§4.2 landed as CODE first (equity_value now stake-level; transaction_value threshold;
+ total_debt / transaction_value / transaction_value_basis / pct_acquired_source columns),
then was applied to the two live DBs by deliberate re-aggregation. Aggregation remains
**incremental** (only CLUSTERED rows are derived; existing AGGREGATED rows keep old
semantics), so any future semantic change still needs an explicit reset-and-run, not a normal
pipeline pass. **One deliberate re-aggregation remains owed:**

1. **After total_debt + Cash_ST extraction (the next piece):** re-aggregate again to populate
   the transaction_value total-debt branch (dormant until then) and derive net_debt from
   `total_debt − Cash_ST` (cash is defined as `Cash_ST` — see decisions.md "Debt and Cash
   Inputs").

Expect **unattributable diffs** from re-aggregation: the DB holds several historical
derivation semantics (aggregation has always been incremental), not just a single expected
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

### FINDING (2026-08-12): implied_equity_value grossed up from an unqualified transaction_value — DECISION VIOLATION [RESOLVED `065a87d`, decision "implied_equity_value Derives From equity_value Only"]

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

Resolved in `065a87d`: the derivation no longer grosses an unqualified/`STATED`
`transaction_value` into `implied_equity_value`. Corrective re-aggregation nulled the affected
`pl_funding.db` values, and `ma_mvp.db` re-aggregated with the fix in place.

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
across all 10 historical DBs.

_Retracted:_ an earlier note here flagged `v2_event_type` as undocumented CREATE/reality drift.
That was a false positive from reading `001_initial.sql` alone — it is defined in
`schema/002_v2_prompt_alignment.sql:18,69`. The schema of record is `schema/*.sql` collectively
(001 + 002 + 003) plus the db.py migration list; see decisions.md "Schema Sources of Record".
