#!/usr/bin/env python3
"""Regression guard for Stage 9's transaction_record write ownership.

No network and no model calls.

Stage 9 wrote `transaction_record` with `INSERT OR REPLACE`, which deletes the row
and inserts a fresh one. Any column absent from its INSERT list was therefore reset
to NULL on every re-aggregation — silently, and including columns owned by later
stages. Re-running Stage 9 alone destroyed Stage 10/11 output.

The rule this asserts has two halves, and a fix that satisfies only one is wrong:

1. **Preserve what Stage 9 does not own.** Columns written by Stage 10 (SEC
   documents) and Stage 11 (agreement extraction), plus `notes` and `created_at`,
   must survive a re-aggregation unchanged.
2. **Still clear what Stage 9 does own.** A Stage-9 field whose newly aggregated
   evidence says NULL must actually become NULL. The obvious way to protect the
   first half — COALESCE, or "only write non-null values" — breaks this one, turning
   every canonical field into a high-water mark that can never be retracted. That is
   worse than the bug being fixed: a value the evidence no longer supports would
   persist forever.

Also asserts that transaction identity and source relationships are untouched, and
that the insert path for genuinely new records still works.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stages.aggregate as aggregate
from db import get_connection, init_db
from lib.observation_writer import write_staging_observations_for_extraction

TXN_ID = "tc_ownership_fixture"
ANNOUNCED = "2026-08-12"

# Written by Stage 10 (sec_documents) and Stage 11 (agreement_extract), plus the two
# row-level columns nothing else owns. Seeded with recognisable values so a wipe is
# unmistakable in the failure message.
DOWNSTREAM_SEEDS = {
    "linked_filings_count": 4,
    "acquirer_merger_sub_name": "Project Falcon Merger Sub, Inc.",
    "merger_structure": "REVERSE_TRIANGULAR",
    "has_mac_clause": 1,
    "requires_target_shareholder_vote": 1,
    "target_vote_threshold": "MAJORITY_OUTSTANDING",
    "closing_conditions_summary": "HSR clearance; target shareholder approval.",
    "target_total_diluted_shares": 48_250_000,
    "fully_diluted_calc_quality": "HIGH",
    "agreement_extraction_status": "EXTRACTED",
    "has_observation_changes": 1,
    "observation_changes_field_count": 3,
    "observation_changes_summary": "per_share_price revised in DEFA14A",
    "notes": "manual reviewer note — do not discard",
}


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _insert_source(conn: sqlite3.Connection, *, slug: str) -> None:
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T1', ?, 'Acquirer to acquire Target', '2026-08-13',
            'Acquirer Inc. agreed to acquire Target LLC for $200 million.',
            ?, 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (f"https://example.test/{slug}", f"ownership-{slug}"),
    )
    source_raw_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'Target LLC', 'Acquirer Inc.', 'strategic_corporate', 'strategic_corporate',
            100.0, ?, 'exact',
            200000000, 'USD', 'EQUITY_VALUE', 'HIGH',
            'DISCLOSED', 'HIGH', '0.7', '0.17', ?
        )
        """,
        (source_raw_id, ANNOUNCED, TXN_ID),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn, extraction_id, observation_source_stage="HC_EXTRACT",
        include_stage3=True, include_hc=True,
    )
    conn.commit()


def _aggregate(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    def _no_conflict(field_name, *_a, **_kw):
        raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")

    original = aggregate._call_agg_prompt
    aggregate._call_agg_prompt = _no_conflict
    try:
        cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
        aggregate.run(conn, cfg, label)
    finally:
        aggregate._call_agg_prompt = original
    return conn.execute(
        "SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)
    ).fetchone()


def _reset_to_clustered(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE staging_extraction SET status='CLUSTERED' WHERE status='AGGREGATED'")
    conn.commit()


def main() -> None:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "ownership.db")
        init_db(db_path)
        conn = get_connection(db_path)

        # --- Pass 1: create the record (the insert path) --------------------
        _insert_source(conn, slug="filing")
        first = _aggregate(conn, "ownership_pass_1")
        if first is None:
            print("FAIL insert path produced no transaction_record")
            raise SystemExit(1)
        _check(failures, "insert path equity_value", first["equity_value"], 200_000_000.0)
        _check(failures, "insert path aggregation_version", first["aggregation_version"], 1)
        created_at = first["created_at"]

        # --- Seed downstream-owned fields, as Stages 10/11 would ------------
        assignments = ", ".join(f"{col} = ?" for col in DOWNSTREAM_SEEDS)
        conn.execute(
            f"UPDATE transaction_record SET {assignments} WHERE transaction_id = ?",
            (*DOWNSTREAM_SEEDS.values(), TXN_ID),
        )
        # Seed a Stage-9-OWNED field with a value the evidence does not support.
        # The fixture states no EBITDA, so re-aggregation must clear this to NULL.
        conn.execute(
            "UPDATE transaction_record SET target_ebitda = ? WHERE transaction_id = ?",
            (999_000_000.0, TXN_ID),
        )
        conn.commit()

        sources_before = conn.execute(
            "SELECT transaction_id, source_raw_id, role FROM transaction_source "
            "WHERE transaction_id = ? ORDER BY source_raw_id",
            (TXN_ID,),
        ).fetchall()

        # --- Pass 2: re-aggregate -------------------------------------------
        _reset_to_clustered(conn)
        second = _aggregate(conn, "ownership_pass_2")
        if second is None:
            failures.append("re-aggregation produced no transaction_record")
            conn.close()
            _report(failures)
            return

        # 1. Stage 9 must not own these — every one must survive verbatim.
        for column, expected in DOWNSTREAM_SEEDS.items():
            _check(failures, f"preserved {column}", second[column], expected)
        _check(failures, "preserved created_at", second["created_at"], created_at)

        # 2. Stage 9 DOES own this — the unsupported value must be cleared, not
        #    preserved. A COALESCE-style fix passes check 1 and fails here.
        _check(failures, "stage-9-owned target_ebitda cleared", second["target_ebitda"], None)

        # Stage 9 fields it does support are still written.
        _check(failures, "stage-9-owned equity_value", second["equity_value"], 200_000_000.0)
        _check(failures, "aggregation_version incremented", second["aggregation_version"], 2)

        # 3. Identity and source relationships unchanged.
        _check(failures, "transaction_id stable", second["transaction_id"], TXN_ID)
        _check(
            failures, "transaction_record row count",
            conn.execute("SELECT COUNT(*) FROM transaction_record").fetchone()[0], 1,
        )
        sources_after = conn.execute(
            "SELECT transaction_id, source_raw_id, role FROM transaction_source "
            "WHERE transaction_id = ? ORDER BY source_raw_id",
            (TXN_ID,),
        ).fetchall()
        _check(
            failures, "transaction_source rows",
            [tuple(r) for r in sources_after], [tuple(r) for r in sources_before],
        )

        # 4. A genuinely new cluster still inserts normally alongside the existing one.
        conn.execute(
            "UPDATE staging_extraction SET transaction_cluster_id = ? WHERE 1=0", (TXN_ID,)
        )
        conn.close()

    _report(failures)


def _report(failures: list[str]) -> None:
    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS stage 9 ownership: downstream fields preserved, owned fields still clearable")


if __name__ == "__main__":
    main()
