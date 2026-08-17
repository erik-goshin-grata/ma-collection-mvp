# Runbook — Path B: Debt/Cash Re-extraction

**Status:** PLAN ONLY. Nothing here has been executed.
**Prepared:** 2026-08-17
**Target:** `data/ma_mvp.db` (92 transactions, 99 staging rows)
**Prerequisite:** Path A discharged and accepted (2026-08-17).

---

## 0. Why Path B exists

Path A re-derived; it could not re-extract. The corpus still has **zero** `net_debt`,
zero calculated enterprise values, and zero `EQUITY_PLUS_TOTAL_DEBT`. The §4.2
debt branch has never run against real data, because no existing staging row carries
debt or cash — those rows were extracted by HC prompt 0.16 and earlier, which did not
capture them.

Path B re-runs Stage 4a with prompt **0.17** to populate them.

**The cost that matters is not dollars.** Re-extraction re-derives *every* HC field,
not just debt and cash — so the diff is unbounded in a way Path A's was not. Budget
the review, not the API bill.

---

## 1. Blocking prerequisite (fixed 2026-08-17, verify before running)

Prompt 0.17 asks the model for `total_debt`, `cash_st`, their currencies, and
`balance_sheet_as_of_date`, and `staging_extraction` has columns for all five — but
until commit *"Persist extracted balance-sheet fields in Stage 4a"* the stage neither
parsed them off `target_financials` nor wrote them in its INSERT **or** its UPDATE.
A Path B run before that fix would have paid full model cost and stored nothing, with
no error — every row would simply have come back null.

`scripts/test_debt_cash_extraction.py` now asserts both write paths. **Run it before
scoping** — the UPDATE path is the one re-extraction actually takes, and an
INSERT-only fix would fail silently in exactly this scenario.

---

## 2. Scope and cost

Measure, don't estimate:

```bash
python scripts/plan_debt_cash_reextraction.py --db data/ma_mvp.db --list-candidates
```

Read-only. It reports the `hc_prompt_version` distribution (what is already 0.17),
how many sources survive a balance-sheet keyword pre-scan, and an order-of-magnitude
cost for the bounded and unbounded sets.

**Bounding.** The keyword pre-scan over `clean_text` is free and typically removes a
large share of the corpus — an article that never mentions debt, cash, leverage, or a
cash-free/debt-free basis cannot yield a balance sheet. It is deliberately
over-inclusive: a cost bound, not a classifier.

**Cost shape.** At roughly 14.5k prompt tokens per source, the *prompt* term dominates
— it is paid once per source regardless of article length. For a corpus this size the
total is small in absolute terms at claude-sonnet-4-6 list price ($3/MTok in,
$15/MTok out); the scoping script prints both the bounded and unbounded figures. It
does not model prompt caching, which would cut the dominant term substantially on a
sequential run.

**Recommendation: run the bounded set first.** It exercises the whole path end to end
on the rows that can actually produce a result, and its diff is small enough to review
row by row.

---

## 3. The structural risk Path A did not have

Stage 4a selects `WHERE se.status = 'CLASSIFIED'`. Stage 8 (`entity_cluster`) selects
`WHERE status = 'LC_EXTRACTED'`. So a reset to `CLASSIFIED` sends rows back through
**HC → LC → clustering → aggregation**, and re-clustering can assign
`transaction_cluster_id` differently than it did before.

**Path A held clusters fixed. Path B does not.** The 92 → 92 invariant is not
guaranteed: clusters can merge or split, which changes `transaction_record`
composition and identity. Treat any change in transaction count as a finding to
investigate, not a tolerance to absorb.

Two further consequences:

- **Every HC field is re-derived**, not just debt and cash. Names, dates, values,
  flags, financials — all of it moves if the model answers differently. This is why
  the diff must be reviewed field-by-field rather than spot-checked.
- **The observation ledger is append-oriented.** Its unique index is
  `(staging_extraction_id, field_name, field_value, observation_fact_key)`, so a
  changed value *adds* a row rather than replacing one. Verify how the ledger should
  represent supersession before running at corpus scale — this is an open question,
  not a settled behaviour, and it is the single item most likely to need a decision.

---

## 4. Shadow-database method (required)

Do **not** re-extract in place. Re-extraction overwrites the current extraction, and
`hc_prompt_version` — the only marker distinguishing a 0.17 row from a 0.16 one — is
overwritten with it, destroying the comparison baseline.

```bash
sqlite3 data/ma_mvp.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 data/ma_mvp.db ".backup 'data/ma_mvp.pathb_shadow.db'"
sqlite3 data/ma_mvp.pathb_shadow.db "PRAGMA integrity_check;"
```

Re-extract on the **shadow**, diff shadow against live, and only then decide whether
to promote. Promotion is a separate, explicit step — never a side effect of the run.

---

## 5. Comparing new debt/cash against the existing corpus

Three questions, in order. The first is the go/no-go.

**A. Did extraction produce anything?**

```sql
SELECT COUNT(*) FROM staging_extraction WHERE total_debt IS NOT NULL;
SELECT COUNT(*) FROM staging_extraction WHERE cash_st IS NOT NULL;
SELECT COUNT(*) FROM staging_extraction
 WHERE total_debt IS NOT NULL AND cash_st IS NOT NULL
   AND total_debt_currency IS NOT NULL AND cash_st_currency IS NOT NULL
   AND balance_sheet_as_of_date IS NOT NULL;   -- the coherent-pair count
```

The third number is the one that matters: only coherent pairs can derive `net_debt`.
A large gap between it and the first two means the model is finding figures but not
their qualifiers — a prompt problem, not a pipeline problem.

**B. Is each extracted figure right?** Every extracted `total_debt` and `cash_st` must
be **read back against its source text by a human** before promotion. There is no
automated check for "is this the right number", and the specific failure to hunt for
is a **net** debt figure landing in `total_debt` — the prompt warns against it because
it is the error that silently corrupts every downstream derivation. Sample the largest
absolute values first; they carry the most downstream weight.

**C. What changed that wasn't debt/cash?** Diff the shadow's `staging_extraction`
against live across all HC fields, keyed by `extraction_id`, and classify every changed
cell as: intended (debt/cash), model drift on an unrelated field, or a clustering
change. Drift on descriptive fields is expected at some rate; drift on values, dates,
or flags needs explanation before promotion.

Then re-run the Path A validation set (`docs/runbook_second_reaggregation.md` §5)
against the shadow after aggregation, plus:

```bash
python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.pathb_shadow.db
```

which should now report **non-zero** calculated-EV rows for the first time.

---

## 6. Ordered procedure (for execution once approved)

1. Run the deterministic suite; confirm green (verifies the §1 prerequisite).
2. Run the §2 scoping script; record candidate count and cost.
3. Create the shadow DB per §4; verify integrity and row counts.
4. Capture the baseline: `hc_prompt_version` distribution, the §5A counts (expected
   all zero), and a full `staging_extraction` snapshot.
5. Reset **only the bounded candidate set** on the shadow to `CLASSIFIED`. Assert the
   count matches the scoping script's candidate count exactly.
6. Run the pipeline on the shadow. Assert `hc_prompt_version = '0.17'` on exactly
   those rows.
7. Run §5A. **If the coherent-pair count is zero, stop** — the prompt is not landing
   the qualifiers, and no amount of re-running fixes that.
8. Human review per §5B on every extracted figure in the bounded set.
9. Diff per §5C; classify every change.
10. Re-run the Path A validation set and the quantifier on the shadow.
11. Decide: promote, widen to the unbounded set, or revise the prompt and repeat.

**Stop conditions** — halt and keep the shadow for inspection if: the coherent-pair
count is zero; `transaction_record` count changes without an explainable clustering
cause; a `total_debt` value on review turns out to be a net figure; or unrelated HC
fields drift on more than a handful of rows without cause.

---

## 7. Open question for the owner

**Observation ledger supersession (§3).** Re-extraction appends observations rather
than replacing them, and there is no `is_current` handling in the write path today.
Before running at corpus scale, decide whether a re-extracted value should supersede
its predecessor in the ledger, and how. Running the bounded set first keeps this
bounded to a reviewable number of rows either way.
