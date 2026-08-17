#!/usr/bin/env python3
"""Plan the legacy funding round_size remediation. DRY RUN BY DEFAULT — mutates nothing.

Nine rows have source-supported round sizes sitting in `transaction_value`. This plans
their correction and prints the exact before/after canonical state for each. Cellares is
carried as UNRESOLVED and is never written.

DESIGN — how history is preserved
---------------------------------
The correction is **additive at every layer**. Nothing is edited or deleted, so the
remediation is reversible and the record of what prompt 0.12 actually extracted survives
intact.

1. **Observation ledger — append only.** A new `round_size` observation is inserted per
   row, stamped `observation_source_stage = 'MANUAL_REMEDIATION'` and carrying the
   approving human and the source sentence. The original `value_amount` / `value_type`
   observations are **left untouched**: they are the true record of what the 0.12
   extraction produced, and deleting them would rewrite history to make the pipeline look
   like it never erred.

2. **`staging_extraction` — one additive write.** `round_size` is set on a column that
   was NULL. `value_amount` and `value_type` are **not cleared**. They remain the
   extraction record; the Stage 9 family gate already makes them inert for funding rows,
   so leaving them costs nothing canonical and preserves the audit trail.

3. **`transaction_record` — not written at all.** Every affected field is Stage-9-owned,
   so re-aggregation derives the corrected state. Writing canonical values by hand would
   put the DB in a state Stage 9 cannot reproduce, which is the failure mode the
   ownership work exists to prevent.

4. **A `notes` entry** records the remediation on the staging row under a `remediation`
   key, alongside the existing `dt`/`hc` keys.

Supersession is therefore *by construction*: only one `round_size` observation will ever
exist per row, so there is no conflict for Stage 9 to resolve, and the superseded
`TRANSACTION_VALUE` fact is retired by the family gate rather than by deletion. This is
the first concrete case of the open ledger-supersession question, and it is deliberately
the narrowest possible answer — it does not generalise to re-extraction, where the same
field genuinely gets two competing values.

Usage:
    python plan_funding_round_size_remediation.py --db data/ma_mvp.db          # plan only
    python plan_funding_round_size_remediation.py --db data/ma_mvp.db --sql    # emit SQL
    python plan_funding_round_size_remediation.py --db data/ma_mvp.db \
        --apply --approved-by "<name>"                                         # execute
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.quantify_net_debt_currency_gap import col_or_null, table_columns  # noqa: E402

# Approved batches, in the order they were authorized. Amounts are in whole units and
# must match the row's staged amount exactly, or the row is skipped as changed-under-us.
#
# Batches are kept separate rather than merged into one list so the record of what was
# approved, when, and on what evidence survives. Re-running an applied batch is a no-op:
# its rows now carry a round_size and no longer match the selection.
BATCHES: dict[str, dict[str, float]] = {
    # Applied 2026-08-17. Stage 9 re-run confirmed: 9 rows at basis ROUND_SIZE.
    "batch1_legacy_hc012": {
        "Ent": 100_000_000,
        "Hydra Host": 100_000_000,
        "Respond.io": 62_500_000,
        "Arcade.dev": 60_000_000,
        "Interchecks": 50_000_000,
        "Radical Numerics": 50_000_000,
        "Gray Swan": 40_000_000,
        "Rejoni": 25_000_000,
        "Kimba": 6_500_000,
    },
    # Approved from the coverage review. Both carry exact primary-capital amounts bound
    # to the target's own financing event.
    "batch2_coverage_review": {
        "Aston Power": 20_000_000,
        "AttoTude": 52_000_000,
    },
}
DEFAULT_BATCH = "batch2_coverage_review"

# Carried through the plan, never written. Each needs a source sentence binding the
# amount to the financing event before it can be approved.
UNRESOLVED = {
    "Cellares": "the $50M is not tied to the financing event by any source sentence",
    "Chronograph": "source says 'over $140 million' — a lower bound. The model has no "
                   "qualifier field for round_size at any layer, so writing 140M would "
                   "assert an exactness the source withheld. Representation gap.",
}

IN_SCOPE_EVENTS = ("VC_ROUND", "GROWTH_EQUITY")


def _match(target: str | None, key: str) -> bool:
    if not target:
        return False
    t, k = target.strip().lower(), key.strip().lower()
    return t == k or t.startswith(k + " ") or t.startswith(k + ",") or k in t


def select_rows(conn: sqlite3.Connection, batch: str = DEFAULT_BATCH):
    """Select and classify the in-scope rows for one approved batch.

    Pure read — mutates nothing.

    Returns (planned, unresolved, skipped), each a list of tuples:
      planned    -> (row, approved_key, amount)
      unresolved -> (row, reason)
      skipped    -> (row, reason)

    Exposed separately from `main` so the regression can assert it against a
    hand-built un-migrated database without going through argument parsing or
    printing.
    """
    # --- schema awareness -------------------------------------------------
    #
    # This runs against the PRE-remediation, PRE-re-aggregation corpus by design, so it
    # must not assume the migrated shape. `transaction_size` / `transaction_size_basis`
    # arrive with the transaction_size work; a corpus that has not been migrated has
    # neither, and naming them unguarded kills the query with
    # "no such column: tr.transaction_size". Substituting the NULL literal keeps one
    # query shape across schema generations, and reads identically on a migrated DB
    # where the columns exist but are unpopulated — absent and empty mean the same
    # thing to a planner: no recorded value.
    #
    # Planning never migrates. A tool whose job is to inspect a database in a
    # particular state must not change that state to make itself run.
    tr_cols = table_columns(conn, "transaction_record")
    se_cols = table_columns(conn, "staging_extraction")
    if not tr_cols:
        raise SystemExit("FATAL: no transaction_record table in this database")
    for required in ("value_amount", "transaction_cluster_id"):
        if required not in se_cols:
            raise SystemExit(f"FATAL: staging_extraction lacks {required!r}; "
                             "this database is too old for the planner to read")

    def tr(name: str) -> str:
        """`tr.<col>` when present, else the NULL literal, always aliased to <col>."""
        return (f"tr.{name} AS {name}" if name in tr_cols else f"NULL AS {name}")

    select_cols = ",\n               ".join([
        "tr.transaction_id",
        tr("v2_event_type"), tr("target_name"),
        tr("transaction_value"), tr("transaction_value_basis"), tr("equity_value"),
        tr("round_size"), tr("investment_amount"),
        tr("transaction_size"), tr("transaction_size_basis"),
        "se.value_amount AS staged_amount",
    ])
    # The event filter has to survive a missing column too; with no v2_event_type there
    # is no way to identify funding rows, so say so rather than silently plan nothing.
    if "v2_event_type" not in tr_cols:
        raise SystemExit("FATAL: transaction_record lacks v2_event_type; cannot "
                         "identify funding rows")
    # round_size may legitimately be absent on a very old corpus — then no row can
    # already carry one, so the predicate is vacuously true.
    tr_round_guard = ("AND tr.round_size IS NULL" if "round_size" in tr_cols else "")
    se_round_guard = ("AND se.round_size IS NULL" if "round_size" in se_cols else "")

    # Select on the STAGING amount, not the derived transaction_value.
    #
    # Order-of-operations hazard, caught on a fixture: once the Stage 9 family gate is
    # in place and a re-aggregation has run, `transaction_record.transaction_value` is
    # already NULL for every funding row — so a selector keyed on it silently matches
    # nothing and the script reports "0 rows planned" as if the work were done.
    # `staging_extraction.value_amount` is the extraction record and is never cleared,
    # so it identifies the affected rows whether or not re-aggregation has run yet.
    ph = ",".join("?" * len(IN_SCOPE_EVENTS))
    rows = conn.execute(
        f"""
        SELECT {select_cols}
        FROM transaction_record tr
        JOIN staging_extraction se ON se.transaction_cluster_id = tr.transaction_id
        WHERE tr.v2_event_type IN ({ph})
          AND se.value_amount IS NOT NULL
          {se_round_guard}
          {tr_round_guard}
        GROUP BY tr.transaction_id
        ORDER BY se.value_amount DESC
        """,
        IN_SCOPE_EVENTS,
    ).fetchall()

    approved = BATCHES[batch]
    planned, skipped, unresolved = [], [], []
    for r in rows:
        name = r["target_name"] or ""
        approved_key = next((k for k in approved if _match(name, k)), None)
        unresolved_key = next((k for k in UNRESOLVED if _match(name, k)), None)
        if unresolved_key:
            unresolved.append((r, UNRESOLVED[unresolved_key]))
            continue
        if not approved_key:
            skipped.append((r, "not on the approved list"))
            continue
        expected = approved[approved_key]
        if abs(float(r["staged_amount"]) - expected) > 0.5:
            skipped.append((r, f"amount changed: approved {expected:,.0f}, "
                               f"found {r['staged_amount']:,.0f}"))
            continue
        planned.append((r, approved_key, expected))
    return planned, unresolved, skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    choices=sorted(BATCHES),
                    help=f"approved batch to plan (default {DEFAULT_BATCH})")
    ap.add_argument("--sql", action="store_true", help="print the statements, do not run")
    ap.add_argument("--apply", action="store_true", help="EXECUTE the writes")
    ap.add_argument("--approved-by", default=None, help="required with --apply")
    args = ap.parse_args()

    if args.apply and not args.approved_by:
        raise SystemExit("--apply requires --approved-by (it is written into provenance)")

    mode = "ro" if not args.apply else "rw"
    conn = sqlite3.connect(f"file:{args.db}?mode={mode}", uri=True)
    conn.row_factory = sqlite3.Row

    tr_cols = table_columns(conn, "transaction_record")
    se_cols = table_columns(conn, "staging_extraction")
    planned, unresolved, skipped = select_rows(conn, args.batch)

    print(f"\n{'=' * 74}\nFUNDING round_size REMEDIATION [{args.batch}] — "
          f"{'APPLY' if args.apply else 'DRY RUN (nothing written)'}\n{'=' * 74}")
    print(f"  in-scope rows found : "
          f"{len(planned) + len(unresolved) + len(skipped)}")
    print(f"  planned             : {len(planned)}")
    print(f"  unresolved (skipped): {len(unresolved)}")
    print(f"  other skips         : {len(skipped)}")

    print(f"\n{'-' * 74}\nBEFORE / AFTER canonical state\n{'-' * 74}")
    print("  'after' is what Stage 9 will DERIVE on the next re-aggregation.")
    print("  This script writes no canonical field directly.")
    if "transaction_size" not in tr_cols:
        print("\n  NOTE: this database has no transaction_size/_basis columns yet, so")
        print("  their 'before' reads NULL because the column is absent, not because it")
        print("  is empty. Routing the re-aggregation through run.py adds them via")
        print("  _apply_migrations before Stage 9 derives. Planning does not migrate.")
    print()
    for r, key, amount in planned:
        print(f"  {key} ({r['transaction_id']}) — {r['v2_event_type']}")
        print(f"      {'field':<24} {'before':>18}   {'after':>18}")
        print(f"      {'-' * 24} {'-' * 18}   {'-' * 18}")
        for field, before, after in (
            ("round_size", r["round_size"], amount),
            ("transaction_size", r["transaction_size"], amount),
            ("transaction_size_basis", r["transaction_size_basis"], "ROUND_SIZE"),
            ("transaction_value", r["transaction_value"], None),
            ("transaction_value_basis", r["transaction_value_basis"], None),
            ("equity_value", r["equity_value"], None),
            ("investment_amount", r["investment_amount"], None),
        ):
            fmt = lambda v: (f"{v:,.0f}" if isinstance(v, (int, float)) else
                             ("NULL" if v is None else str(v)))
            flag = "" if fmt(before) == fmt(after) else "  <-"
            print(f"      {field:<24} {fmt(before):>18} → {fmt(after):>18}{flag}")
        print()

    if unresolved:
        print(f"{'-' * 74}\nUNRESOLVED — carried, never written\n{'-' * 74}")
        for r, why in unresolved:
            print(f"  {r['target_name']} ({r['transaction_id']}): "
                  f"{r['staged_amount']:,.0f} — {why}")
            print("      after re-aggregation this row is: round_size NULL, "
                  "transaction_size NULL,")
            print("      transaction_value NULL (family gate), investment_amount NULL.")
            print("      The amount stays in staging_extraction.value_amount and the ledger.\n")

    if skipped:
        print(f"{'-' * 74}\nSKIPPED\n{'-' * 74}")
        for r, why in skipped:
            print(f"  {r['target_name']} ({r['transaction_id']}): {why}")

    if not (args.sql or args.apply):
        print(f"\n{'=' * 74}")
        print("DRY RUN. Nothing was written. Re-run with --sql to see the statements,")
        print("or --apply --approved-by '<name>' to execute. After applying, a Stage 9")
        print("re-aggregation is required to derive the 'after' state above.")
        conn.close()
        return

    # --- write-path preconditions ----------------------------------------
    # Planning tolerates a missing column by reading NULL. Writing cannot: a column
    # that is not there cannot receive a value. Check before touching anything and say
    # exactly what is missing, rather than failing halfway through a partial apply.
    missing_writes = [c for c in ("round_size", "notes", "updated_at") if c not in se_cols]
    if missing_writes:
        raise SystemExit(
            "FATAL: staging_extraction lacks " + ", ".join(repr(c) for c in missing_writes)
            + ". Applying requires those columns. Run the pipeline through run.py once so "
            "_apply_migrations adds them, then re-run --apply. Planning does not need them."
        )
    obs_cols = table_columns(conn, "transaction_field_observation")
    if not obs_cols:
        raise SystemExit("FATAL: no transaction_field_observation table; the remediation "
                         "records provenance there and will not write without it.")
    has_round_currency = "round_currency" in se_cols

    now = datetime.now(timezone.utc).isoformat()
    statements: list[tuple[str, tuple]] = []
    for r, key, amount in planned:
        se = conn.execute(
            "SELECT se.extraction_id, se.source_raw_id, sr.source_type, se.notes "
            "FROM staging_extraction se "
            "JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id "
            "WHERE se.transaction_cluster_id = ? "
            "ORDER BY se.extraction_id LIMIT 1",
            (r["transaction_id"],),
        ).fetchone()
        if se is None:
            continue
        try:
            notes = json.loads(se["notes"] or "{}")
        except (json.JSONDecodeError, TypeError):
            notes = {"raw": se["notes"]}
        notes["remediation"] = (
            f"{now}: round_size {amount:,.0f} canonicalized from a legacy "
            f"transaction_value extracted under HC 0.12; approved by "
            f"{args.approved_by or '<pending>'}. Original value_amount/value_type "
            f"left intact as the extraction record."
        )
        currency_clause = (
            ", round_currency = COALESCE(round_currency, value_currency)"
            if has_round_currency else ""
        )
        statements.append((
            f"UPDATE staging_extraction SET round_size = ?{currency_clause}, "
            "notes = ?, updated_at = ? WHERE extraction_id = ?",
            (amount, json.dumps(notes, ensure_ascii=False), now, se["extraction_id"]),
        ))
        statements.append((
            "INSERT INTO transaction_field_observation ("
            "  transaction_id, field_name, field_value, field_value_numeric,"
            "  staging_extraction_id, source_raw_id, source_type,"
            "  observation_source_stage, extracted_at"
            ") VALUES (?, 'round_size', ?, ?, ?, ?, ?, 'MANUAL_REMEDIATION', ?)",
            (r["transaction_id"], str(amount), amount, se["extraction_id"],
             se["source_raw_id"], se["source_type"], now),
        ))

    if args.sql:
        print(f"\n{'-' * 74}\nSQL ({len(statements)} statements) — NOT executed\n{'-' * 74}")
        for sql, params in statements:
            print(f"\n{sql}\n  -- params: {params}")
        conn.close()
        return

    for sql, params in statements:
        conn.execute(sql, params)
    conn.commit()
    print(f"\nAPPLIED {len(statements)} statements for {len(planned)} rows.")
    print("Now reset those rows to CLUSTERED and run Stage 9 to derive the after-state.")
    conn.close()


if __name__ == "__main__":
    main()
