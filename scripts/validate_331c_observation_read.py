"""
Validate Drop 3.31c observation-backed Stage 9 parity.

This script copies a real DB into two temporary files, runs Stage 9 once with
AGGREGATION_READ_SOURCE=staging and once with observation, stubs aggregation LLM
conflict resolution, and compares side effects. It does not make live API calls
and does not modify the source DB.

Usage:
    python scripts/validate_331c_observation_read.py --source-db /path/to/copied_real.db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from stages import aggregate


def _prepare_db(db_path: str) -> None:
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            DELETE FROM aggregation_conflict_log;
            DELETE FROM transaction_source;
            DELETE FROM transaction_record;
            UPDATE staging_extraction
            SET status = 'CLUSTERED'
            WHERE status = 'AGGREGATED';
            """
        )
        conn.commit()
    finally:
        conn.close()


def _coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "clustered_without_observations": conn.execute(
            """
            SELECT COUNT(*)
            FROM staging_extraction se
            WHERE se.status = 'CLUSTERED'
              AND se.transaction_cluster_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM transaction_field_observation tfo
                  WHERE tfo.staging_extraction_id = se.extraction_id
                    AND tfo.transaction_id = se.transaction_cluster_id
                    AND tfo.is_current = 1
              )
            """
        ).fetchone()[0],
        "missing_required_provenance": conn.execute(
            """
            SELECT COUNT(*)
            FROM transaction_field_observation
            WHERE staging_extraction_id IS NOT NULL
              AND is_current = 1
              AND (
                  transaction_id IS NULL
                  OR source_raw_id IS NULL
                  OR source_tier IS NULL
                  OR model_confidence IS NULL
              )
            """
        ).fetchone()[0],
        "missing_consideration_json": conn.execute(
            """
            SELECT COUNT(*)
            FROM staging_extraction se
            WHERE se.status IN ('CLUSTERED', 'AGGREGATED')
              AND se.consideration_components IS NOT NULL
              AND se.consideration_components != '[]'
              AND NOT EXISTS (
                  SELECT 1
                  FROM transaction_field_observation tfo
                  WHERE tfo.staging_extraction_id = se.extraction_id
                    AND tfo.field_name = 'consideration_components'
                    AND tfo.is_current = 1
              )
            """
        ).fetchone()[0],
    }


def _run_stage9(db_path: str, read_source: str) -> tuple[dict, int]:
    call_count = 0

    def _stub_conflict(
        field_name: str,
        field_type: str,
        deal_context: dict,
        observations: list[dict],
        prompt: dict,
        cfg: Any,
        conn: sqlite3.Connection,
        run_id: str,
        transaction_id: str,
        log: Any,
    ) -> dict:
        nonlocal call_count
        call_count += 1
        chosen = observations[0]
        return {
            "chosen_observation_id": chosen["observation_id"],
            "chosen_value": chosen["value"],
            "aggregation_confidence": "LOW",
            "conflict_severity": "LOW",
            "flagged_for_review": False,
            "reasoning": "local 3.31c validation stub chose first observation",
            "prompt_version": "local-331c-validation-stub",
            "notes": "No live API call made.",
        }

    original = aggregate._call_agg_prompt
    aggregate._call_agg_prompt = _stub_conflict
    try:
        conn = get_connection(db_path)
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source=read_source)
            summary = aggregate.run(conn=conn, cfg=cfg, run_id=f"validate_331c_{read_source}")
        finally:
            conn.close()
    finally:
        aggregate._call_agg_prompt = original
    return summary, call_count


def _dump_outputs(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tr_cols = [row["name"] for row in conn.execute("PRAGMA table_info(transaction_record)")]
        tr_compare_cols = [col for col in tr_cols if col not in {"created_at", "updated_at"}]
        records = [
            {col: row[col] for col in tr_compare_cols}
            for row in conn.execute(
                f"SELECT {', '.join(tr_compare_cols)} FROM transaction_record ORDER BY transaction_id"
            )
        ]
        sources = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT transaction_id, source_raw_id, role
                FROM transaction_source
                ORDER BY transaction_id, source_raw_id, role
                """
            )
        ]
        return {
            "records": records,
            "sources": sources,
            "stats": {
                "transaction_record_count": len(records),
                "aggregation_conflict_log_count": conn.execute(
                    "SELECT COUNT(*) FROM aggregation_conflict_log"
                ).fetchone()[0],
                "transaction_source_count": len(sources),
                "observation_count": conn.execute(
                    "SELECT COUNT(*) FROM transaction_field_observation"
                ).fetchone()[0],
                "status_counts": [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT status, COUNT(*) FROM staging_extraction GROUP BY status ORDER BY status"
                    )
                ],
                "coverage": _coverage(conn),
            },
        }
    finally:
        conn.close()


def _record_diffs(left: list[dict], right: list[dict]) -> list[Any]:
    diffs: list[Any] = []
    if len(left) != len(right):
        return [("row_count", len(left), len(right))]
    for a, b in zip(left, right):
        if a != b:
            tid = a.get("transaction_id") or b.get("transaction_id")
            changed = {
                key: (a.get(key), b.get(key))
                for key in sorted(set(a) | set(b))
                if a.get(key) != b.get(key)
            }
            diffs.append((tid, changed))
            if len(diffs) >= 5:
                break
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate 3.31c observation-read parity.")
    parser.add_argument("--source-db", required=True, help="Path to copied real DB to validate.")
    args = parser.parse_args()

    source = Path(args.source_db)
    if not source.exists():
        parser.error(f"--source-db does not exist: {source}")

    with tempfile.TemporaryDirectory(prefix="ma_331c_validate_") as tmpdir:
        staging_db = str(Path(tmpdir) / "staging.db")
        observation_db = str(Path(tmpdir) / "observation.db")
        shutil.copy2(source, staging_db)
        shutil.copy2(source, observation_db)

        _prepare_db(staging_db)
        _prepare_db(observation_db)

        staging_summary, staging_calls = _run_stage9(staging_db, "staging")
        observation_summary, observation_calls = _run_stage9(observation_db, "observation")

        staging = _dump_outputs(staging_db)
        observation = _dump_outputs(observation_db)

        record_diffs = _record_diffs(staging["records"], observation["records"])
        source_diffs: Any = []
        if staging["sources"] != observation["sources"]:
            left = set(staging["sources"])
            right = set(observation["sources"])
            source_diffs = {
                "staging_only": sorted(left - right)[:5],
                "observation_only": sorted(right - left)[:5],
            }

        result = {
            "summaries": {
                "staging": staging_summary,
                "observation": observation_summary,
            },
            "llm_conflict_calls": {
                "staging": staging_calls,
                "observation": observation_calls,
            },
            "stats": {
                "staging": staging["stats"],
                "observation": observation["stats"],
            },
            "transaction_record_diffs": record_diffs,
            "transaction_source_diffs": source_diffs,
        }
        print(json.dumps(result, indent=2, sort_keys=True))

        ok = (
            not record_diffs
            and not source_diffs
            and staging_summary == observation_summary
            and staging_calls == observation_calls
            and staging["stats"]["aggregation_conflict_log_count"]
            == observation["stats"]["aggregation_conflict_log_count"]
            and staging["stats"]["transaction_source_count"]
            == observation["stats"]["transaction_source_count"]
            and observation["stats"]["coverage"]["clustered_without_observations"] == 0
            and observation["stats"]["coverage"]["missing_required_provenance"] == 0
            and observation["stats"]["coverage"]["missing_consideration_json"] == 0
        )
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
