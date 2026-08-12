# Work order — Code, 2026-08-11

**Phase 1 of 3.** Phase 1 puts our own house in order. Phase 2 runs examples to confirm it.
Phase 3 is the Grata enum/schema exercise, which does not start until 1 and 2 are done.

**Gated on Erik approving `docs/draft_decisions_observation_coverage.md`.** Nothing below starts
until those two entries are approved; step 1 is landing them.

Context is in `docs/briefing_code_2026_08_11.md` (all four claims verified). This document
sequences the work — it does not restate the decisions.

---

## 1. Land the decisions

Append both entries from `docs/draft_decisions_observation_coverage.md` to `docs/decisions.md`,
changing Status from *proposed* to *accepted*. Delete the draft file once appended, so there is
one copy.

---

## 2. Allow-list entries — the two confirmed non-defects

Neither is a drop. Both need an explicit entry **with the reason**, per the parity spec.

- `consideration_type` — derived by `_derive_consideration_type` from `consideration_components`;
  the extracted value is intentionally ignored.
- `round_stage_category` — derived by `_derive_round_stage_category` from `round_label`;
  `003_funding_path.sql` states this in a comment.

---

## 3. Close the observation coverage gap

**The 19 tier-2 fields are not one job.** `write_staging_observations_for_extraction` is
**group-driven, not field-list-driven** — it knows only `include_stage3` / `include_hc` /
`include_lc`, and there is no funding group. `[verified: lib/observation_writer.py — 2026-08-11]`
So the seven fields from `002` ride existing groups and are tuple additions; the twelve from `003`
have no group to ride and require writer work first.

**3a. Funding writer group — new code, not configuration.** Add a funding field group and an
`include_funding` path to `write_staging_observations_for_extraction`. This is the prerequisite for
3c and for the funding half of step 5.

**3b. Stage 4b dual-write.** Add observation writing to `stages/funding_hc_extract.py` under a new
`observation_source_stage` value `FUNDING_HC_EXTRACT`, and add that stage to the observation
loader's accepted set. Depends on 3a.

**3c. Tier 2 — 7 fields from `002`, genuinely mechanical.** Tuple additions to the existing
Stage-3 and HC groups; no writer change.

`announced_date_precision`, `closed_date_precision`, `rumor_date`, `financials_disclosure_status`,
`acquirer_type_v2`, `target_revenue_period_type_v2`, `target_ebitda_period_type_v2`

**3d. Tier 2 — 12 fields from `003`, gated on 3a.** These populate the new funding group.

`round_label`, `pre_money_valuation`, `post_money_valuation`, `valuation_currency`,
`facility_size`, `total_raised_to_date`, `is_extension_round`, `is_down_round`, `is_bridge_round`,
`use_of_proceeds`, `has_board_seat`, `board_seat_notes`

**3e. Tier 3 — 3 fields, need both read paths.** Wire, do not delete.

- `target_type_v2`, `spin_split_type_v2` — add to `_FIELDS` using the legacy-fallback read that
  `002_v2_prompt_alignment.sql` specifies, and to the observation write path.
- `signing_date_precision` — add to `_FIELDS`, to the observation write path, and add the missing
  `transaction_record` column.

---

## 4. Round currency

Add the `round_size` currency column to `staging_extraction` and `transaction_record`, populated
from the `round.currency` the funding HC prompt already emits.

Generalize `deal_value_currency` resolution to **unanimity or null** across all contributing
currency sources. Extend `scripts/test_deal_value_currency.py` with a three-source case: all
agreeing tags, any two disagreeing nulls.

---

## 5. Backfill observations for existing rows

Steps 3 and 4 only change what *future* extractions write. Existing `staging_extraction` rows
already hold the data; their observations are missing. Without the backfill, step 6's parity would
pass vacuously for the newly-wired fields.

**This is mostly automatic, and that is the trap.** `backfill_staging_observations` is idempotent —
partial unique index plus `INSERT OR IGNORE` — and `db.py` calls it on every `init_db`, right after
`_apply_migrations`. `[verified: lib/observation_writer.py, db.py — 2026-08-11]` So it re-fires on
any routed run, including step 7's, and writes nothing when data is unchanged. Safe to run
repeatedly.

**But it inherits the same group limitation as the writer.** It requests
`include_stage3 / include_hc / include_lc` only. **Extend it to request funding once 3a lands**, or
existing funding rows never receive funding observations and step 6's funding parity is exactly the
vacuous case this step exists to prevent.

---

## 6. Parity run — isolated, before the re-aggregation

**This is a run, not a build.** `scripts/validate_331c_observation_read.py` is the 3.31c harness
and does exactly this activity: copies a real DB into two temporary files, runs Stage 9 once under
`AGGREGATION_READ_SOURCE=staging` and once under `observation`, compares side effects, no live API
calls. It compares Stage 9 output generically, so newly-wired fields are picked up without
modification. `[verified: scripts/validate_331c_observation_read.py — 2026-08-11]`

The original acceptance criterion for the observation path was **zero canonical transaction
diffs**, and that guarantee has been false since `002`.

**Run this before step 7, not after.** The re-aggregation changes stored meaning; the parity run
does not. Doing parity first means a failure attributes cleanly to the coverage work instead of
being tangled with re-aggregation diffs that are expected anyway.

`AGGREGATION_READ_SOURCE` **stays `staging`** until this passes.

**Canonical parity is necessary but not sufficient.** The harness also compares LLM conflict
counts, and a delta there fails the run even when `transaction_record_diffs` is empty. That check
is not pedantry: identical outputs reached through more LLM adjudications is *latent* divergence,
not equivalence. Those extra resolutions agree today; a model version bump, a temperature change,
or one marginal case and they stop agreeing, at which point the canonical parity silently ceases to
hold with nothing indicating why. There is also a plain cost signal — extra Opus conflict calls on
every run.

Pin which fields drive any delta before signing this step off. Two hypotheses to separate:

- **Benign** — the observation path now sees newly-covered fields, so more same-tier disagreements
  surface and happen to resolve identically.
- **Artifact** — the backfill wrote competing observations for one field. The dedup index is on
  `(staging_extraction_id, field_name, field_value)`, so **field value is part of the key**: the
  same field written twice with different string representations yields two live observations and
  a spurious conflict. Check whether any field reachable from two groups is normalised
  differently, given `_string_value` takes a `bool_as_int_text` flag.

### Baseline measurement — record, but do not over-read

`pl_funding.db` predates the value model: `deal_value_currency` does not exist on it, nor
`transaction_value`, `total_debt`, or `pct_acquired_source`. **There is no two-source baseline to
take here.** The third-source null-rate question moves to phase 2 and needs a corpus containing
genuine cross-currency funding — a euro round at a dollar post-money. `pl_funding.db` is thin on
that (`valuation_currency`: 52 null, 6 USD, 1 KRW, 1 EUR), so corpus design has to supply it
deliberately. A run producing zero mismatches proves nothing either way.

---

## 7. §4.2 joint re-aggregation

The re-aggregation owed since 2026-08-10. **Both changes together — stake-level `equity_value` and
the transaction-value threshold — never one without the other**, or a third semantics is created.

That constraint is about the **code** being in place, not about any one DB exercising both halves.
Both landed together in `18720b7`.

### 7a. Target DBs — two runs

§4.2 has two halves and no single fixture exercises both.

- **`pl_funding.db`** — the `equity_value` half. The decision's own framing is that the control
  path already stored stake-level equity while the funding path stored the 100% figure, and this
  DB is 55 `VC_ROUND` + 20 `MINORITY_INVESTMENT` — 75 of 91 rows on exactly the path whose
  semantics changed. Pristine snapshot already exists at `data/pl_funding_pre_step5.db`.
- **One M&A DB** — the transaction-value / debt half, which `pl_funding.db` cannot exercise
  (`total_debt` does not exist on it, so there are zero control-plus-debt-known cases).
  **Confirm which of `ma_mvp.db` / `ma_valu8.db` / `ma_grata.db` is actually read from before
  choosing** — a permanently mixed column only matters where the data is live, and regenerable
  fixtures do not need it. Snapshot before running, as with `pl_funding.db`.

### 7b. Precondition — the AGGREGATED→CLUSTERED reset

Aggregation is incremental: it derives `WHERE status = 'CLUSTERED'`, then moves rows to
`AGGREGATED`. **A DB whose rows are all `AGGREGATED` has nothing to re-derive.**
`pl_funding.db` is in exactly that state — zero `CLUSTERED` rows.

So the reset is a required, deliberate step, not an implementation detail. `validate_331c` performs
it internally on its own copy; a real step-7 run does not inherit that.

**Assert row counts before and after the reset, and after the run.** Without the assertion, step 7
executes cleanly against zero rows and reports success — a silent no-op wearing a green tick,
which is worse than a failure because nothing prompts a second look.

### 7c. Running

- Route through `run.py`, or call `init_db()` first. A bare `get_connection` skips
  `_apply_migrations`.
- **Assert the `round_currency` column from step 4 exists before running.** If it does not, the
  re-aggregation writes without it and step 4 is undone.
- Cheap re-confirmation of step 6 afterwards is worth it, since the loaders are unchanged but the
  underlying rows are not.

### 7d. Expected diffs — three categories, only one unattributable

1. **§4.2 semantics** — stake-level `equity_value`, the transaction-value threshold. The point of
   the run.
2. **Tier-3 columns NULL → populated** — `target_type_v2` and `spin_split_type_v2` have written
   perpetual NULL to `transaction_record` since `002`; step 3e wired them. On `pl_funding.db` that
   is ~83 of 91 rows. Large, visually alarming, entirely ours, entirely predictable. **Name it in
   advance so it is not swept into category 3.**
3. **Historical derivation semantics** — genuinely unattributable. Aggregation has always been
   incremental, so the DB holds several generations of semantics. Expected, not regressions.

`round_currency` and `deal_value_currency` should not move at all on historical rows —
`round_currency` is NULL everywhere, having never been captured. **If `deal_value_currency` moves,
stop.** Nothing in this work order should have changed it on data where `round_currency` is absent.

---

## 8. Verify

- `scripts/test_schema_convergence.py` passes across all historical DBs.
- `scripts/test_deal_value_currency.py` passes, including the new three-source case.
- Update `project_state.md`: the §4.2 re-aggregation is discharged **on the DBs named in 7a, and
  which ones** — a DB not listed still carries mixed semantics. One re-aggregation remains owed
  (after `total_debt` + `Cash_ST` extraction).

---

## What phase 2 needs from this

Phase 2 runs examples to confirm phase 1, and **the same runs produce the worked examples the
Grata memo will need in phase 3.** Designing for both now avoids reconstructing evidence later.

Two things to demonstrate:

1. **The wiring holds** — observation path reaches parity, funding fields populate rather than
   nulling.
2. **The value model produces the right numbers** — the four cases from
   `docs/handoff_transaction_size.md`: minority stake, control with debt known, control with debt
   unknown, and a funding round. The minority case is the one that carries the argument in phase 3
   — a partial stake must not gross up into a multiple.

Capture inputs and outputs per case as they run. That record is the evidence base, and it is much
cheaper to keep than to rebuild.

---

## Not in this work order

- **The Grata enum/schema comparison.** Phase 3. Separate repo, ChatGPT drafting.
- **`transaction_size`** — unblocked once step 7 lands, but deliberately not in phase 1.
- **EV rewire (§4.3)** — still parked on currency and period anchoring.
- **The field parity test** (`docs/spec_field_parity_test.md`, checks 2–4). This order fixes the
  current instances by hand; the test is what stops them recurring. **Its ON HOLD gate is lifted**
  — checks 2–4 test this repo's internal consistency and never depended on Grata. Natural
  follow-on to phase 1.
