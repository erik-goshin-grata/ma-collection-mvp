# Session handoff — Transaction value model, 2026-08-10

For picking this up cold. Assumes repo access and no memory of the session.

---

## What this session did

Redesigned the transaction value model, reconciled it against the running code, and landed
three implementation pieces. Design work is complete; two runs are owed.

The trigger: an audit of `exports/ML_worksheet.csv` found reviewers picking the largest figure
from a single unlabelled value column, and funding check sizes labelled as company equity value.

---

## Read these first, in order

1. `docs/decisions.md` — **authoritative.** Entries dated 2026-08-10 (two batches).
2. `docs/spec_transaction_value_model.md` — elaborates the decisions and must conform to them.
   Any conflict resolves to `decisions.md`.
3. `docs/project_state.md` — living state, including the owed re-aggregations.
4. The handoffs in `docs/` — see status below.

---

## The model, in brief

**Two tiers. Every value field is in exactly one.**

- **Tier 1, as-transacted** — `equity_value` (stake-level), `transaction_value`,
  `transaction_size`. Records what happened. **Never a multiple numerator.**
- **Tier 2, 100% basis** — `implied_equity_value`, `implied_enterprise_value`. Whole-company
  valuation. **The only legal multiple numerators.**

**`transaction_value`** — as-reported where a source states one. Otherwise `equity_value` +
`total_debt` at `pct_acquired ≥ 50`, `equity_value` below it, null where debt is unknown
above the threshold. Cash never netted.

**`transaction_size`** — universal magnitude across all deal types, derived not extracted, with
a mandatory basis stamp. Must not be summed across bases. **Not yet built.**

**Funding rounds** populate `post_money_valuation` only. No implied equity, therefore no EV,
therefore no multiples. Enforced twice — by the model, and by an event-type gate in
`_compute_multiples`.

**Stake-level `enterprise_value` is removed** in the design; the code rewire is parked.

---

## Landed

Confirmed against `git log`, in order.

| Commit | What |
|---|---|
| `874a6d5` | Merged two-tier value model spec + first decisions batch |
| `d476e1b` | Named-value-fields decision, spec gap 9, §6 qualifier, §1 and §2.11 corrections |
| `fd8adfd` | TV rule amended to the `pct_acquired ≥ 50` threshold; §4.1 and §4.2 handoffs unblocked and landed |
| `006f817` | §4.1 — capital-raised precondition + `MINORITY_INVESTMENT` capture field |
| `19e6955` | §4.7 — `deal_value_currency` persisted, mismatch guard (null on conflict), tests |
| `e66c88c` | §4.1 complete — `round_size` added to `HC_FIELDS`, fixing the observation read path |
| `18720b7` | §4.2 Changes 1+2 — stake-level `equity_value`, TV threshold, `pct_acquired` resolution, `pct_acquired_source`, four columns |
| `fe1b55e` | Part-2 decisions appended; parity test replaced with `test_schema_convergence.py`; false `v2_event_type` drift note retracted |

`006f817` onward are the commits that changed code. `4f8797f` sits just before `874a6d5` in the
log and is **pre-session baseline** — the bug-8 derivation this work extended, not part of it.

Note that `18720b7` shipped a schema-parity test comparing `001_initial.sql` against the `db.py`
migration list. That premise was wrong — `_apply_migrations` executes `002`/`003` rather than
duplicating their columns — and `fe1b55e` replaced it with the convergence test. If you find a
reference to the parity test anywhere, it's stale.

---

## Owed — recorded in `project_state.md`

**Two re-aggregations**, both deferred to a convenient run:

1. After §4.2 — the joint run covering stake-level `equity_value` and the TV threshold together.
   Never run between the two; that produces a third semantics.
2. After `total_debt` + `cash` extraction lands.

**Route re-aggregation through `run.py`**, or call `init_db()` first. A bare `get_connection`
skips `_apply_migrations`.

**Expect diffs that don't trace to §4.2.** Aggregation is incremental — `WHERE status =
'CLUSTERED'`, then rows move to `AGGREGATED` — so past derivation changes only ever touched new
rows. The DB holds several historical semantics. Unattributable diffs are expected, not
regressions.

---

## Next, in order

1. **Currency and period anchoring** (§2.10 items 1–2). Now the head of the queue. These block
   the implied tier, and extracting `total_debt` + `cash` requires period anchoring anyway — so
   they are one piece of work, not two.
2. **`total_debt` + `cash` as `target_financials` metrics** — with period type and
   `period_end_date`, alongside `target_revenue` and `target_ebitda`. Activates the dormant
   total-debt branch and lets `net_debt` derive from total debt − cash. Touches prompt, parser,
   staging, `_FIELDS`, `HC_FIELDS`, schema, migration list.
3. **`transaction_size` + the export** — `handoff_transaction_size.md`. The only item in the
   batch a reviewer ever sees, and the original point of the exercise. Depends on §4.2's
   re-aggregation.
4. **EV rewire** (§4.3) — parked until step 1 clears.

---

## Open items

**Blocking the implied tier:**

1. **Currency** — `implied_enterprise_value` adds consideration in deal currency to net debt in
   the target's reporting currency. Needs a conversion point and FX date *before* the addition.
   `deal_value_currency` is a partial answer: single tag, null on mismatch, per-field deferred.
2. **Period coherence** — net debt anchors to announced date; the multiple denominator carries
   its own period basis. Nothing requires them to agree.

**Parked, blocked on the above:**

3. **EV rewire** — feed `_derive_enterprise_value` the implied equity, rename the output, delete
   the stake-level path. **Guard: do not export the derived EV and do not repoint multiples at
   it.** Either turns a latent defect live before its fix exists.
4. **EV as a `transaction_size` rung** — control deals only. Parked with the rewire.

**Decided, no handoff:**

5. **Named `*_as_reported` value fields** — replacing the single `value` slot, which drops a
   figure whenever a source states more than one. Spec gap 9.

**Waiting on input:**

6. **Field inventory + parity test.** Method decided: generate by origin — prompts for
   extracted, aggregation code for derived, a short list for manual. Definition precedes
   parity. Erik is supplying eng-team information on schema and enum locations first; the draft
   spec is landed as `docs/spec_field_parity_test.md`, marked ON HOLD pending it (check 1
   is superseded by `test_schema_convergence.py`; checks 2–4 stand).

**Deferred:**

7. **Convertible rounds** — a stated valuation cap has no field. Convertibles *are* identifiable
   via `consideration_type` (`safe`, `convertible_note`). Population too small to justify work.
8. **M&A control flags** (`is_minority`, `pre_existing_control`, `acquires_remaining`) — absent
   from the repo, and no longer needed by the value model since TV uses `pct_acquired`. Worth
   building for comps segmentation on their own merits.
9. **Taxonomy restructure** — discussed, not decided. Decision #9 (VC vs Growth) remains open in
   `handoff_decision9_vc_vs_growth.md`.

---

## Known limitations — deliberate, do not "fix"

- **`pct_acquired ≥ 50` misreads a step-up into control.** 30% → 60% gives `pct_acquired` = 30,
  reads as below control, adds no debt when it should. Uncommon, fails conservatively. The
  alternative required extracting pre-transaction ownership, which sources rarely state.
- **The 50–99% band adds full company debt to a partial equity stake.** Market convention (CIQ
  Total Transaction Value). `pct_acquired` is stamped alongside, and TV is never a multiple
  numerator, so it does not propagate.
- **`transaction_value` is often null.** A when-stated field, closer to `per_share_price` than a
  computed column. `transaction_size` carries the magnitude regardless.
- **`total_debt` and `net_debt` both manual, and a row may have only one.** Only `net_debt`
  yields an EV and no calculated TV. Expected.

---

## Working agreement

Established after several errors caused by reasoning from a stale documentation snapshot.

**Provenance tags on every factual claim:** `[verified: path, date]` /
`[unverified — Code confirm]`.

Seven claims this session were asserted confidently and later found false — including a citation
to a document not in the repo, a `consideration_component` table that doesn't exist, and a
"deeper schema drift" finding that was an artefact of reading `001_initial.sql` alone. Several
were endorsed downstream before being caught. The tags exist so errors don't propagate *between*
agents.

**Source-of-truth order:** `decisions.md` > spec > handoffs. Nothing lands until they agree.

**Boundary:** Claude authors design documents; Code lands them, runs operations, maintains
`project_state.md`, and verifies against the repo. Two agents don't edit the same file.

**Don't cite line numbers in durable documents.** They drift. Quote the text.

**Project knowledge is stale and should not be attached.** It predates
`funding_hc_extraction.md` and contains prompt copies whose structure no longer matches the
repo. The repo is authoritative for everything.

**Scope conversations to one workstream.** This session covered the value model, taxonomy,
prompt rules, four handoffs and the working agreement — and `transaction_value` was redefined
four times because early decisions were revisited as later context arrived.

---

## Verified facts worth not re-deriving

- **The schema is three files** — `001_initial.sql`, `002_v2_prompt_alignment.sql`,
  `003_funding_path.sql` — plus the `db.py` `_apply_migrations` list, which *executescripts*
  002 and 003 rather than duplicating their columns. `test_schema_convergence.py` asserts all
  paths reach one canonical column set; passes across six historical DBs.
- **`mvp_goal_and_schema.md` is superseded** (v0.1, 100-deal proof loop, specifies tables that
  were built as columns). The V2 documents are design intent. Neither describes what runs.
- **There is no `financial_metric` table and no `consideration_component` table.** Financial
  metrics are columns on `staging_extraction` and `transaction_record`. Target securities live
  in `transaction_security`, whose `security_type` carries the instrument and `security_class`
  the class label — not the reverse.
- **Funding types route to Stage 4b** and carry the raise in `round.size`.
  `MINORITY_INVESTMENT` stays on the M&A path — that was the entire §4.1 gap.
- **`low_confidence_extraction` consumes HC's value** and does not produce one.
- **`transaction_security` is populated only on control/M&A types.** Zero funding rows across
  three DBs, so it cannot identify convertible rounds in practice.
- **`_compute_multiples` reads the *stated* enterprise value**, not the derived one, and gates on
  event type before the value-type check. This is why the stake-level EV defect is latent.
- **`pct_acquired` is NULL when 100% is implicit.** Any rule keyed on it must resolve first —
  `NULL >= 50` silently routes ordinary 100% acquisitions to the minority branch.
- **Sampling ~20 `MINORITY_INVESTMENT` deals:** roughly half primary, a fifth secondary, ~30%
  undeterminable from stored fields. The stored data doesn't preserve the distinction, so past
  rows can't be audited without re-extraction.
