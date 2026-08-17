#!/usr/bin/env python3
"""Regression guard: the remediation planner runs on the corpus it is meant to inspect.

No network and no model calls.

The planner's whole purpose is to plan a correction against the **pre-remediation,
pre-re-aggregation** database. That database has not been migrated, so it does not have
`transaction_size` / `transaction_size_basis` — those arrive with the transaction_size
work. A planner that names them unguarded dies on the only corpus it will ever be
pointed at:

    sqlite3.OperationalError: no such column: tr.transaction_size

The legacy fixture below is built **by hand**, not through `init_db`. `init_db` applies
the migrations and would add exactly the columns whose absence is the thing under test,
so a fixture built that way passes while the real corpus fails — which is how the defect
reached the live run in the first place.

The rule the planner follows: an absent column and an unpopulated one mean the same
thing to a planner — no recorded value — so substituting the NULL literal keeps one
query shape across schema generations. Planning **never migrates**: a tool whose job is
to inspect a database in a particular state must not change that state to run.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Exactly the shape of the live corpus before the transaction_size migration: it has
# round_size and the value fields, and NO transaction_size / transaction_size_basis.
_LEGACY_TR = (
    "transaction_id TEXT PRIMARY KEY",
    "v2_event_type TEXT",
    "target_name TEXT",
    "transaction_value REAL",
    "transaction_value_basis TEXT",
    "equity_value REAL",
    "round_size REAL",
    "investment_amount REAL",
)
_LEGACY_SE = (
    "extraction_id INTEGER PRIMARY KEY AUTOINCREMENT",
    "source_raw_id INTEGER",
    "status TEXT",
    "v2_event_type TEXT",
    "hc_prompt_version TEXT",
    "value_type TEXT",
    "value_amount REAL",
    "value_currency TEXT",
    "round_size REAL",
    "notes TEXT",
    "updated_at TEXT",
    "transaction_cluster_id TEXT",
)
_LEGACY_SR = (
    "source_raw_id INTEGER PRIMARY KEY AUTOINCREMENT",
    "source_type TEXT",
    "title TEXT",
    "clean_text TEXT",
)

# (target, event, staged amount) — two batch-1 approvals and the one row that remains
# unresolved. Chronograph is unresolved by REPRESENTATION: its source clearly supports a
# funding magnitude ("over $140 million"), but the model cannot carry a lower bound.
_ROWS = [
    ("Arcade.dev", "VC_ROUND", 60_000_000.0),
    ("Rejoni", "GROWTH_EQUITY", 25_000_000.0),
    ("Chronograph", "GROWTH_EQUITY", 140_000_000.0),
]

# Cellares is the divergent case: the staged figure is one investor's check, and the
# canonical round_size is the round it sat inside.
_DIVERGENT_ROWS = [("Cellares", "GROWTH_EQUITY", 50_000_000.0)]


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _build(path: str, *, migrated: bool, rows=None) -> None:
    """Hand-built schema. `migrated=True` adds only the transaction_size pair."""
    conn = sqlite3.connect(path)
    tr = list(_LEGACY_TR)
    if migrated:
        tr += ["transaction_size REAL", "transaction_size_basis TEXT"]
    conn.execute(f"CREATE TABLE transaction_record ({', '.join(tr)})")
    conn.execute(f"CREATE TABLE staging_extraction ({', '.join(_LEGACY_SE)})")
    conn.execute(f"CREATE TABLE source_raw ({', '.join(_LEGACY_SR)})")
    conn.execute(
        "CREATE TABLE transaction_field_observation ("
        "  observation_id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT,"
        "  field_name TEXT, field_value TEXT, field_value_numeric REAL,"
        "  staging_extraction_id INTEGER, source_raw_id INTEGER, source_type TEXT,"
        "  observation_source_stage TEXT, extracted_at TEXT)"
    )
    for target, event, amount in (rows if rows is not None else _ROWS):
        txn = f"tc_{target.lower().replace('.', '_')}"
        cur = conn.execute(
            "INSERT INTO source_raw (source_type, title, clean_text) VALUES (?,?,?)",
            ("WEB_URL", f"{target} announcement", f"{target} raised ${amount:,.0f}."),
        )
        srid = cur.lastrowid
        conn.execute(
            "INSERT INTO transaction_record (transaction_id, v2_event_type, target_name,"
            " transaction_value, transaction_value_basis, equity_value, round_size,"
            " investment_amount) VALUES (?,?,?,?,'STATED',NULL,NULL,?)",
            (txn, event, target, amount, amount),
        )
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, v2_event_type,"
            " hc_prompt_version, value_type, value_amount, value_currency, round_size,"
            " notes, transaction_cluster_id)"
            " VALUES (?, 'AGGREGATED', ?, '0.12', 'TRANSACTION_VALUE', ?, 'USD', NULL,"
            " '{\"hc\": \"n\"}', ?)",
            (srid, event, amount, txn),
        )
    conn.commit()
    conn.close()


def _plan(path: str, batch: str = "batch1_legacy_hc012"):
    """Run the planner's selection + classification for one batch. Read-only."""
    import scripts.plan_funding_round_size_remediation as planner
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return planner.select_rows(conn, batch)
    finally:
        conn.close()


def _snapshot(path: str) -> tuple:
    conn = sqlite3.connect(path)
    try:
        return (
            conn.execute("SELECT COUNT(*) FROM staging_extraction "
                         "WHERE round_size IS NOT NULL").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM transaction_field_observation").fetchone()[0],
            {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")},
        )
    finally:
        conn.close()


def main() -> None:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        # --- 1. Legacy DB: the exact live-corpus shape ---------------------
        legacy = str(Path(tmp) / "legacy.db")
        _build(legacy, migrated=False)
        before = _snapshot(legacy)
        if "transaction_size" in before[2]:
            failures.append("fixture is not legacy — it already has transaction_size")

        try:
            planned, unresolved, skipped = _plan(legacy)
        except sqlite3.OperationalError as exc:
            failures.append(
                f"planner died on the un-migrated corpus it exists to inspect: {exc}"
            )
            planned = unresolved = skipped = []

        _check(failures, "legacy planned rows", len(planned), 2)
        _check(failures, "legacy unresolved rows", len(unresolved), 1)
        _check(failures, "legacy skipped rows", len(skipped), 0)
        for row, _key, _amount in planned:
            # Absent columns must read as NULL, not raise and not fabricate.
            _check(failures, f"{row['target_name']} transaction_size reads NULL",
                   row["transaction_size"], None)
            _check(failures, f"{row['target_name']} basis reads NULL",
                   row["transaction_size_basis"], None)

        # --- 2. Planning mutated nothing, and migrated nothing -------------
        after = _snapshot(legacy)
        _check(failures, "planning wrote no round_size", after[0], before[0])
        _check(failures, "planning wrote no observations", after[1], before[1])
        _check(failures, "planning did not alter the schema", after[2], before[2])
        if "transaction_size" in after[2]:
            failures.append("planning migrated the database — it must never do that")

        # --- 3. Migrated DB: identical behaviour ---------------------------
        migrated = str(Path(tmp) / "migrated.db")
        _build(migrated, migrated=True)
        m_planned, m_unresolved, m_skipped = _plan(migrated)
        _check(failures, "migrated planned rows", len(m_planned), len(planned))
        _check(failures, "migrated unresolved rows", len(m_unresolved), len(unresolved))
        _check(failures, "migrated skipped rows", len(m_skipped), len(skipped))
        _check(
            failures, "same transactions planned on both shapes",
            sorted(r["transaction_id"] for r, _k, _a in m_planned),
            sorted(r["transaction_id"] for r, _k, _a in planned),
        )
        _check(
            failures, "same amounts planned on both shapes",
            sorted(a for _r, _k, a in m_planned),
            sorted(a for _r, _k, a in planned),
        )

        # --- 4. Batches are isolated ---------------------------------------
        # A batch plans only its own approvals. Rows approved in another batch are
        # skipped, not silently swept in — that is what keeps a bounded remediation
        # bounded when a later batch is added to the same file.
        import scripts.plan_funding_round_size_remediation as planner
        _check(failures, "batch2 exists", "batch2_coverage_review" in planner.BATCHES, True)
        _check(failures, "batch2 is exactly Aston Power + AttoTude + Cellares",
               sorted(planner.BATCHES["batch2_coverage_review"]),
               ["Aston Power", "AttoTude", "Cellares"])
        _check(failures, "batch1 unchanged at nine rows",
               len(planner.BATCHES["batch1_legacy_hc012"]), 9)
        # The fixture holds only batch1 targets plus the unresolved one, so batch2 must
        # plan nothing from it and must report the batch1 rows as skipped, not invisible.
        b2_planned, b2_unres, b2_skipped = _plan(legacy, "batch2_coverage_review")
        _check(failures, "batch2 plans nothing from a batch1 fixture", len(b2_planned), 0)
        _check(failures, "batch2 skips the batch1 rows", len(b2_skipped), 2)
        _check(failures, "batch2 still carries the unresolved row", len(b2_unres), 1)
        # Chronograph is carried as unresolved and can never be planned by any batch.
        _check(failures, "Chronograph is unresolved, not approved",
               any("Chronograph" in k for k in planner.UNRESOLVED), True)
        _check(failures, "Cellares is no longer unresolved",
               any("Cellares" in k for k in planner.UNRESOLVED), False)

        # --- 5. Divergent approval: staged amount is NOT the canonical one ---
        # Cellares' staged $50M is Prime Radiant's check; the round is $327M. The
        # changed-under-us guard must still compare against the STAGED figure, or a
        # divergent correction could never be planned at all — while the amount actually
        # written is the canonical round size.
        divergent = str(Path(tmp) / "divergent.db")
        _build(divergent, migrated=True, rows=_DIVERGENT_ROWS)
        d_planned, _d_unres, d_skipped = _plan(divergent, "batch2_coverage_review")
        _check(failures, "divergent row is planned", len(d_planned), 1)
        _check(failures, "divergent row not skipped", len(d_skipped), 0)
        if d_planned:
            row, key, amount = d_planned[0]
            _check(failures, "divergent key", key, "Cellares")
            _check(failures, "canonical amount is the ROUND, not the check",
                   amount, 327_000_000.0)
            _check(failures, "staged amount is still the check",
                   row["staged_amount"], 50_000_000.0)
        staged, canonical, note = planner.resolve_approval(
            planner.BATCHES["batch2_coverage_review"]["Cellares"]
        )
        _check(failures, "resolve_approval staged", staged, 50_000_000.0)
        _check(failures, "resolve_approval canonical", canonical, 327_000_000.0)
        if not note or "check" not in note.lower():
            failures.append("divergent approval must carry a note explaining the divergence")
        # A bare number still means staged == canonical.
        s2, c2, n2 = planner.resolve_approval(20_000_000)
        _check(failures, "bare approval staged", s2, 20_000_000.0)
        _check(failures, "bare approval canonical", c2, 20_000_000.0)
        _check(failures, "bare approval has no note", n2, None)

        # The guard still fires when the staged figure moved underneath us.
        moved = str(Path(tmp) / "moved.db")
        _build(moved, migrated=True, rows=[("Cellares", "GROWTH_EQUITY", 99_000_000.0)])
        m2_planned, _m2u, m2_skipped = _plan(moved, "batch2_coverage_review")
        _check(failures, "changed staged amount is skipped", len(m2_planned), 0)
        _check(failures, "changed staged amount reported", len(m2_skipped), 1)
        for batch_name, approvals in planner.BATCHES.items():
            for name in approvals:
                if name in planner.UNRESOLVED:
                    failures.append(
                        f"{name!r} is both approved in {batch_name} and unresolved"
                    )

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS remediation planner: reads the un-migrated corpus, migrates nothing, "
          "identical on both schema generations")


if __name__ == "__main__":
    main()
