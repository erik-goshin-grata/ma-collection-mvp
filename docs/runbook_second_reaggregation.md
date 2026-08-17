# Runbook — Second Owed Re-aggregation (§4.2)

**Status:** EXECUTED AND ACCEPTED (2026-08-17). Retained as the procedure of record
for future re-aggregations; the outcome of the run it planned is in §8.
**Prepared:** 2026-08-17
**Target:** `data/ma_mvp.db` (92 `transaction_record` rows)

---

## 0. The finding that changes the shape of this

**Re-aggregation alone will not activate the debt branch.**

The owed §4.2 re-aggregation was scoped as "after `total_debt` + `Cash_ST`
extraction lands." Extraction has landed *in code*, but the 92 existing
`staging_extraction` rows were produced by HC prompt 0.16 and earlier, which did not
capture debt or cash. The new `staging_extraction.total_debt` / `cash_st` columns
were added by migration and are **NULL on every existing row**.

So a re-aggregation reads NULL debt and NULL cash, and:

- `EQUITY_PLUS_TOTAL_DEBT` cannot fire — still zero rows,
- `IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT` cannot fire,
- `balance_sheet_period_type` stays NULL (no amounts to qualify).

This is corroborated by the live quantifier run: 0 rows with `net_debt`, 0 calculated
EV rows, 0 `EQUITY_PLUS_TOTAL_DEBT`. The debt paths have never fired on this corpus
and **will not fire from re-aggregation alone**.

That splits the work into two decisions, which should not be run together:

| | Path A — re-aggregate | Path B — re-extract, then re-aggregate |
|---|---|---|
| Cost | Deterministic, no LLM calls | Full Stage 4a LLM cost on 92+ sources |
| Activates debt branch | No | Yes, where sources state debt/cash |
| Changes | read path, typed equity, anchoring | all of Path A, plus every HC field re-derived |
| Reversible | Yes, from a file copy | Yes, from a file copy |
| Risk | Low, bounded | Higher — all HC fields move, not just debt/cash |

**Path A was run first and accepted; Path B is deferred** until a naturally occurring
or manually collected debt/cash case exists. Path A is what §4.2 actually owed. Path B
is a new extraction run wearing a re-aggregation label, and bundling them would have
made an unbounded diff impossible to attribute.

The rest of this runbook is Path A. Its outcome is recorded in §8.

---

## 1. What Path A will actually change

Three landed changes bear on re-derivation. None of them touch debt/cash on this
corpus.

1. **Read source `staging` → `observation`** (`abd8464`). All 92 rows were derived
   under the staging read. This is the largest source of diff and the hardest to
   predict, because the observation read keys per fact rather than per extraction.
2. **`equity_value` from its own typed observation** (`9300b67`). Affects only
   clusters carrying more than one typed value fact. Expect few rows, each gaining
   `equity_value` / `transaction_value` / `implied_equity_value` that were previously
   null.
3. **Currency and period anchoring** (`68876cf`). Expect *losses*: a
   `target_revenue_period_end` or `financials_currency` that came from a different
   source than its amount now resolves to null, and a multiple that depended on a
   borrowed `period_end` disappears. These are corrections, not regressions — the
   values were never supported by the source of their own amount.

Expect **unattributable diffs** as well. Aggregation has always been incremental, so
the DB holds several historical derivation semantics, not one. A diff that does not
trace to the three changes above is not automatically a defect.

---

## 2. Precondition — the reset is a required, deliberate step

Aggregation derives `WHERE se.status = 'CLUSTERED'` and moves members to
`AGGREGATED`. A database whose rows are all `AGGREGATED` has nothing to re-derive:
Stage 9 would run clean against zero rows and report success. **A silent no-op is
the default failure mode here**, so row counts are asserted before, between, and
after.

Rows to reset — cluster members only:

```sql
-- Inspect first. Nothing is modified by this block.
SELECT status, COUNT(*) FROM staging_extraction GROUP BY status;

-- The rows that will be reset: AGGREGATED and actually part of a cluster.
SELECT COUNT(*) FROM staging_extraction
WHERE status = 'AGGREGATED' AND transaction_cluster_id IS NOT NULL;

-- Guard: AGGREGATED but with no cluster id. These cannot re-derive; if this is
-- non-zero, stop and investigate before resetting anything.
SELECT COUNT(*) FROM staging_extraction
WHERE status = 'AGGREGATED' AND transaction_cluster_id IS NULL;
```

The reset itself (do **not** run yet):

```sql
UPDATE staging_extraction
SET status = 'CLUSTERED', updated_at = datetime('now')
WHERE status = 'AGGREGATED' AND transaction_cluster_id IS NOT NULL;
```

Statuses that must **not** be reset: `LC_EXTRACTED`, `PROMPT_FAILED`, `CLASSIFIED`,
`REJECTED`. They were never aggregated, and moving them to `CLUSTERED` would push
rows into Stage 9 that the pipeline deliberately held back.

---

## 3. ~~Hazard — `INSERT OR REPLACE` nulls columns Stage 9 does not write~~ RESOLVED

**Resolved 2026-08-17.** Stage 9 now writes with an upsert
(`INSERT ... ON CONFLICT(transaction_id) DO UPDATE SET ...`) scoped to the 115
columns it owns. The 15 columns it does not own — Stage 10/11 output plus `notes`
and `created_at` — are left untouched on re-aggregation.

**The snapshot-and-restore step this section used to require is no longer needed.**
Re-running Stage 9 alone no longer discards Stage 10/11 output, so the three handling
options (snapshot / re-run 10–13 / accept the loss) are all moot. Nothing needs to be
saved before the reset.

For the record, the columns Stage 9 preserves:

```
linked_filings_count              agreement_extraction_status
acquirer_merger_sub_name          has_observation_changes
merger_structure                  observation_changes_field_count
has_mac_clause                    observation_changes_summary
requires_target_shareholder_vote  notes
target_vote_threshold             created_at
closing_conditions_summary
target_total_diluted_shares
fully_diluted_calc_quality
```

Guarded by `scripts/test_stage9_field_ownership.py`, which asserts both halves of the
rule: these survive re-aggregation, *and* a Stage-9-owned field is still cleared to
NULL when the evidence no longer supports it.

The census below is no longer a decision gate. It remains useful as a
before/after check that the preservation actually held:

```sql
SELECT
  SUM(agreement_extraction_status IS NOT NULL) AS agreement_rows,
  SUM(linked_filings_count > 0)                AS sec_linked_rows,
  SUM(target_total_diluted_shares IS NOT NULL) AS diluted_share_rows,
  SUM(notes IS NOT NULL)                       AS notes_rows
FROM transaction_record;
```

---

## 4. Backup procedure

The database is WAL-mode, so `cp` alone can capture a file whose committed state
lives in the `-wal` sidecar. Use SQLite's own backup, which is consistent under
concurrent access:

```bash
# Stop anything writing to the DB first.
sqlite3 data/ma_mvp.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 data/ma_mvp.db ".backup 'data/ma_mvp.prereagg_20260817.db'"

# Verify the copy is intact and has the expected shape before touching the original.
sqlite3 data/ma_mvp.prereagg_20260817.db "PRAGMA integrity_check;"
sqlite3 data/ma_mvp.prereagg_20260817.db "SELECT COUNT(*) FROM transaction_record;"   -- expect 92
```

Keep the backup outside `data/` if `data/*` is gitignored and you want it to survive
a clean. Rollback is a file restore — never an attempted reverse-migration.

---

## 5. Validation outputs to capture before *and* after

Capture each into a file, then diff. Structural checks first, then value diffs.

**A. Row-count invariants** — these must be identical before and after:

```sql
SELECT COUNT(*) FROM transaction_record;                      -- expect 92, unchanged
SELECT status, COUNT(*) FROM staging_extraction GROUP BY status;
SELECT COUNT(DISTINCT transaction_cluster_id) FROM staging_extraction
 WHERE transaction_cluster_id IS NOT NULL;
```

A changed `transaction_record` count means clusters merged or split — investigate
before looking at anything else.

**B. Full canonical snapshot**, for a row-level diff:

```sql
.mode csv
.headers on
.output prereagg_transactions.csv
SELECT * FROM transaction_record ORDER BY transaction_id;
.output stdout
```

Diff the before/after CSVs and classify every changed cell into: read-path,
typed-equity, anchoring, INSERT-OR-REPLACE loss, or unattributable.

**C. Value-model field census** — the fields this stack touches:

```sql
SELECT
  SUM(equity_value IS NOT NULL)              AS equity_value,
  SUM(implied_equity_value IS NOT NULL)      AS implied_equity,
  SUM(enterprise_value IS NOT NULL)          AS ev,
  SUM(transaction_value IS NOT NULL)         AS txn_value,
  SUM(target_revenue IS NOT NULL)            AS revenue,
  SUM(target_revenue_period_end IS NOT NULL) AS revenue_period_end,
  SUM(financials_currency IS NOT NULL)       AS fin_ccy,
  SUM(ev_to_revenue_ltm IS NOT NULL)         AS ev_rev,
  SUM(ev_to_ebitda_ltm IS NOT NULL)          AS ev_ebitda
FROM transaction_record;
```

Expected direction: `revenue_period_end`, `fin_ccy`, and the multiples may **fall**
(anchoring removing borrowed qualifiers). `equity_value` / `txn_value` /
`implied_equity` may **rise** slightly (typed-equity fix). A large unexplained move
in any single field is the signal to stop.

**D. Basis distributions** — vocabulary shifts are easy to miss in a cell diff:

```sql
SELECT transaction_value_basis, COUNT(*) FROM transaction_record GROUP BY 1;
SELECT enterprise_value_basis,  COUNT(*) FROM transaction_record GROUP BY 1;
SELECT equity_value_basis,      COUNT(*) FROM transaction_record GROUP BY 1;
```

**E. The currency-gap quantifier**, before and after — it should stay all-zero:

```bash
python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.db
```

**F. Stage 10/11 survival** — the §3 census, re-run after. These must match exactly;
Stage 9 no longer touches those columns, so any change is a defect, not a tradeoff.

**G. Review XLSX** — regenerate and confirm the **67-column shape is unchanged**.
The export was deliberately not modified by this stack.

**H. Deterministic suite** — re-run before starting; it is independent of the DB but
establishes the code is the reviewed code:

```bash
for t in scripts/test_*.py; do python "$t" >/dev/null 2>&1 || echo "FAIL $t"; done
```

---

## 6. Ordered procedure (for execution once approved)

1. Run the full deterministic suite; confirm green.
2. Confirm `AGGREGATION_READ_SOURCE` is unset or `observation`; record which.
3. Capture validation outputs A–G into `prereagg_*` files.
4. Back up per §4; verify `integrity_check` and row count on the copy.
5. Assert `AGGREGATED` count; run the §2 reset; assert `CLUSTERED` count equals it.
6. Run Stage 9 **only**, via `run.py`, so `_apply_migrations` adds the new columns
   before the derivation reads them.
7. Assert every member moved back to `AGGREGATED`; assert `transaction_record`
   count is still 92.
8. Capture validation outputs A–G into `postreagg_*` files.
9. Diff, classify every change, and write the findings into `docs/decisions.md`
   before anyone reads the corpus as current.

**Stop conditions** — halt and restore from backup if any of these occur:
`transaction_record` count changes; a value-model field census moves by more than a
handful of rows without an attributable cause; the quantifier reports non-zero
at-risk rows; the reset touches a status other than `AGGREGATED`.

---

## 7. Open question for the owner — ANSWERED

Path B (re-extraction) is what actually populates debt and cash and discharges the
spirit of §4.2 — the branch has still never run against real data. It is a separate
decision with real LLM cost, and it should follow Path A's review rather than
accompany it. `hc_prompt_version` distinguishes rows extracted with balance-sheet
capability (`0.17`) from those without, so a partial or staged re-extraction remains
attributable.

**Answered 2026-08-17: Path B is deferred.** It will not be run against the current
corpus merely to seek a debt/cash case; it waits for a naturally occurring or manually
collected one. The plan is written up and ready in
`docs/runbook_path_b_reextraction.md`.

---

## 8. Path A outcome (executed and accepted, 2026-08-17)

**Ran:** the §6 procedure, Stage 9 only, under `AGGREGATION_READ_SOURCE=observation`.

**Structural invariants — all held.**

| Check | Before | After |
|---|---|---|
| `transaction_record` rows | 92 | **92** |
| Cluster members re-derived | — | 98 `AGGREGATED` |
| Held back (correctly untouched) | — | 1 `PROMPT_FAILED` |

No cluster merged or split; no transaction identity changed.

**Value-model results.**

- The typed-value fix is visible on real rows: Anysphere at $60.0B and Payoneer at
  $2.75B now carry `equity_value` with `transaction_value_basis = EQUITY_VALUE_ONLY`
  — previously collapsed. `EQUITY_VALUE_ONLY` records *debt unknown*, not debt = 0.
- Multiples survived where they were genuinely supported: Dahl holds 0.76x.
- Anchoring produced **no** losses on this corpus. That is not evidence the guard is
  inert — it means no live row depended on a borrowed qualifier. The fixture in
  `scripts/test_currency_period_anchoring.py` still reproduces the defect the guard
  prevents.
- Stage 10/11 census: 0 / 0 / 0 / 0 before and after, i.e. nothing to preserve on this
  corpus and nothing lost. The ownership guarantee is carried by
  `scripts/test_stage9_field_ownership.py`, not by this run.

**Debt branch — still dormant, as §0 predicted.** 0 rows with `net_debt`, 4
enterprise values all `STATED`, 0 calculated, 0 `EQUITY_PLUS_TOTAL_DEBT`, 0 at-risk
rows from the currency-gap quantifier. Path A could not change this; only Path B can.

**Caveat that survives this run.** Aggregation remains incremental. Path A re-derived
the corpus as it stood on 2026-08-17, but Stage 9 still processes `CLUSTERED` rows as
they arrive, so rows added later derive under whatever semantics are current then. The
DB is not guaranteed to represent a single derivation vintage, and a future reader
should not assume it does.
