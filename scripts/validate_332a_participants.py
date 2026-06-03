"""
Validate Drop 3.32a organization participant backfill on a copied real DB.

The script copies the source DB, runs the 3.32a schema migration and backfill
on the copy, checks idempotency and participant coverage, and emits JSON. It
does not make live API calls and does not modify the source DB.

Usage:
    python3 scripts/validate_332a_participants.py --source-db /path/to/copied_real.db
    python3 scripts/validate_332a_participants.py --source-db /path/to/copied_real.db \
        --validation-db /private/tmp/ma_332a_validation.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection, init_db
from lib.participant_backfill import backfill_participants

_GENERIC_GROUP_RE = re.compile(
    r"^(buyer group|investor group|seller group|consortium|acquirer group)$"
    r"|(\bgroup\s+(led|formed|organized|comprised)\s+by\b)"
    r"|(\b(consortium|investor group|buyer group)\s+led\s+by\b)"
    r"|(\bconsortium\b)",
    re.IGNORECASE,
)
_SYNTHETIC_GROUP_ENTITY_RE = re.compile(
    r"^(buyer group|investor group|seller group|consortium|acquirer group)$"
    r"|(\bbuyer group\b|\binvestor group\b|\bseller group\b|\bconsortium\b)",
    re.IGNORECASE,
)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _digest_table(conn: sqlite3.Connection, table_name: str, order_col: str) -> dict[str, Any]:
    cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")]
    rows = [
        {col: row[col] for col in cols}
        for row in conn.execute(
            f"SELECT {', '.join(cols)} FROM {table_name} ORDER BY {order_col}"
        )
    ]
    payload = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    return {
        "row_count": len(rows),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def _cleanup_db_files(path: Path) -> None:
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()


def _copy_source(source: Path, validation_db: Path | None) -> tuple[str, tempfile.TemporaryDirectory | None]:
    if validation_db:
        if source.resolve() == validation_db.resolve():
            raise ValueError("--validation-db must differ from --source-db")
        validation_db.parent.mkdir(parents=True, exist_ok=True)
        _cleanup_db_files(validation_db)
        shutil.copy2(source, validation_db)
        return str(validation_db), None

    tmpdir = tempfile.TemporaryDirectory(prefix="ma_332a_validate_")
    copied = Path(tmpdir.name) / "participants.db"
    shutil.copy2(source, copied)
    return str(copied), tmpdir


def _run_backfill_twice(db_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        first = backfill_participants(conn, dry_run=False)
        conn.commit()
        second = backfill_participants(conn, dry_run=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return first, second


def _counts_by_role(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["participant_role"]: row["n"]
        for row in conn.execute(
            """
            SELECT participant_role, COUNT(*) AS n
            FROM transaction_participant
            WHERE is_current=1
            GROUP BY participant_role
            ORDER BY participant_role
            """
        )
    }


def _counts_by_side(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["side"]: row["n"]
        for row in conn.execute(
            """
            SELECT side, COUNT(*) AS n
            FROM transaction_participant
            WHERE is_current=1
            GROUP BY side
            ORDER BY side
            """
        )
    }


def _counts_by_group_type(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row["group_type"]: row["n"]
        for row in conn.execute(
            """
            SELECT group_type, COUNT(*) AS n
            FROM transaction_participant_group
            GROUP BY group_type
            ORDER BY group_type
            """
        )
    }


def _coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    target_missing = _rows(
        conn,
        """
        SELECT tr.transaction_id, tr.target_name
        FROM transaction_record tr
        WHERE tr.is_current=1
          AND tr.target_name IS NOT NULL
          AND TRIM(tr.target_name)!=''
          AND NOT EXISTS (
              SELECT 1
              FROM transaction_participant tp
              WHERE tp.transaction_id=tr.transaction_id
                AND tp.participant_role='TARGET'
                AND tp.side='TARGET'
                AND tp.is_current=1
          )
        ORDER BY tr.transaction_id
        """,
    )

    acquirer_missing_all = _rows(
        conn,
        """
        SELECT tr.transaction_id, tr.acquirer_name, tr.acquirer_type
        FROM transaction_record tr
        WHERE tr.is_current=1
          AND tr.acquirer_name IS NOT NULL
          AND TRIM(tr.acquirer_name)!=''
          AND NOT EXISTS (
              SELECT 1
              FROM transaction_participant tp
              WHERE tp.transaction_id=tr.transaction_id
                AND tp.participant_role IN ('ACQUIRER', 'BUYER_PLATFORM')
                AND tp.side='BUYER'
                AND tp.is_current=1
          )
        ORDER BY tr.transaction_id
        """,
    )
    acquirer_group_label_exceptions = [
        row for row in acquirer_missing_all if _GENERIC_GROUP_RE.search(row["acquirer_name"] or "")
    ]
    acquirer_missing_actionable = [
        row
        for row in acquirer_missing_all
        if row not in acquirer_group_label_exceptions
    ]

    parent_seller_missing = _rows(
        conn,
        """
        SELECT tr.transaction_id, tr.parent_seller_name
        FROM transaction_record tr
        WHERE tr.is_current=1
          AND tr.parent_seller_name IS NOT NULL
          AND TRIM(tr.parent_seller_name)!=''
          AND NOT EXISTS (
              SELECT 1
              FROM transaction_participant tp
              WHERE tp.transaction_id=tr.transaction_id
                AND tp.participant_role='PARENT_SELLER'
                AND tp.side='SELLER'
                AND tp.is_current=1
          )
        ORDER BY tr.transaction_id
        """,
    )

    sponsor_missing = _rows(
        conn,
        """
        SELECT tr.transaction_id, tr.acquirer_sponsor_name
        FROM transaction_record tr
        WHERE tr.is_current=1
          AND tr.acquirer_sponsor_name IS NOT NULL
          AND TRIM(tr.acquirer_sponsor_name)!=''
          AND NOT EXISTS (
              SELECT 1
              FROM transaction_participant tp
              WHERE tp.transaction_id=tr.transaction_id
                AND tp.participant_role='BUYER_SPONSOR'
                AND tp.side='BUYER'
                AND tp.is_current=1
          )
        ORDER BY tr.transaction_id
        """,
    )

    sponsor_review_flags = _count(
        conn,
        """
        SELECT COUNT(*)
        FROM transaction_participant
        WHERE participant_role='BUYER_SPONSOR'
          AND side='BUYER'
          AND is_current=1
          AND review_status='NEEDS_REVIEW'
        """,
    )

    return {
        "target_missing_count": len(target_missing),
        "target_missing_samples": target_missing[:10],
        "acquirer_missing_strict_count": len(acquirer_missing_all),
        "acquirer_missing_strict_samples": acquirer_missing_all[:10],
        "acquirer_group_label_exception_count": len(acquirer_group_label_exceptions),
        "acquirer_group_label_exception_samples": acquirer_group_label_exceptions[:10],
        "acquirer_missing_actionable_count": len(acquirer_missing_actionable),
        "acquirer_missing_actionable_samples": acquirer_missing_actionable[:10],
        "parent_seller_missing_count": len(parent_seller_missing),
        "parent_seller_missing_samples": parent_seller_missing[:10],
        "buyer_sponsor_missing_count": len(sponsor_missing),
        "buyer_sponsor_missing_samples": sponsor_missing[:10],
        "buyer_sponsor_needs_review_count": sponsor_review_flags,
    }


def _duplicates(conn: sqlite3.Connection) -> dict[str, Any]:
    duplicate_current_participants = _rows(
        conn,
        """
        SELECT transaction_id, entity_id, participant_role, side,
               COALESCE(group_id, '') AS group_key,
               COUNT(*) AS n
        FROM transaction_participant
        WHERE is_current=1
        GROUP BY transaction_id, entity_id, participant_role, side, COALESCE(group_id, '')
        HAVING COUNT(*) > 1
        ORDER BY n DESC, transaction_id
        """,
    )
    duplicate_groups = _rows(
        conn,
        """
        SELECT transaction_id, side, group_type,
               COALESCE(group_label, '') AS group_label_key,
               COALESCE(disclosed_group_name, '') AS disclosed_group_key,
               COUNT(*) AS n
        FROM transaction_participant_group
        GROUP BY transaction_id, side, group_type,
                 COALESCE(group_label, ''), COALESCE(disclosed_group_name, '')
        HAVING COUNT(*) > 1
        ORDER BY n DESC, transaction_id
        """,
    )
    return {
        "duplicate_current_participant_count": len(duplicate_current_participants),
        "duplicate_current_participant_samples": duplicate_current_participants[:10],
        "duplicate_group_count": len(duplicate_groups),
        "duplicate_group_samples": duplicate_groups[:10],
    }


def _synthetic_group_entities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    candidates = _rows(
        conn,
        """
        SELECT entity_id, canonical_name, normalized_name, legal_name, domain,
               ticker, cik, lei, review_status
        FROM entity
        ORDER BY canonical_name
        """,
    )
    flagged = []
    for row in candidates:
        name = row["canonical_name"] or ""
        has_legal_evidence = any(row.get(col) for col in ("legal_name", "domain", "ticker", "cik", "lei"))
        if _SYNTHETIC_GROUP_ENTITY_RE.search(name) and not has_legal_evidence:
            flagged.append(row)
    return flagged


def _samples(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    consortium_cases = _rows(
        conn,
        """
        SELECT g.transaction_id, tr.acquirer_name, g.disclosed_group_name,
               g.review_status, COUNT(tp.participant_id) AS participant_count,
               GROUP_CONCAT(tp.participant_role || ':' || e.canonical_name, ' | ') AS participants
        FROM transaction_participant_group g
        LEFT JOIN transaction_record tr
            ON tr.transaction_id=g.transaction_id
        LEFT JOIN transaction_participant tp
            ON tp.group_id=g.group_id
           AND tp.is_current=1
        LEFT JOIN entity e
            ON e.entity_id=tp.entity_id
        WHERE g.group_type='CONSORTIUM'
        GROUP BY g.group_id
        ORDER BY g.transaction_id
        LIMIT ?
        """,
        (limit,),
    )
    sponsor_cases = _rows(
        conn,
        """
        SELECT tr.transaction_id, tr.acquirer_name, tr.acquirer_type,
               tr.acquirer_sponsor_name,
               GROUP_CONCAT(e.canonical_name, ' | ') AS buyer_sponsors,
               MAX(CASE WHEN tp.review_status='NEEDS_REVIEW' THEN 1 ELSE 0 END)
                   AS has_review_flag
        FROM transaction_record tr
        JOIN transaction_participant tp
            ON tp.transaction_id=tr.transaction_id
           AND tp.participant_role='BUYER_SPONSOR'
           AND tp.side='BUYER'
           AND tp.is_current=1
        JOIN entity e
            ON e.entity_id=tp.entity_id
        WHERE tr.is_current=1
        GROUP BY tr.transaction_id
        ORDER BY tr.transaction_id
        LIMIT ?
        """,
        (limit,),
    )
    return {
        "consortium_cases": consortium_cases,
        "sponsor_cases": sponsor_cases,
    }


def _validate(db_path: str, sample_limit: int) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        baseline = {
            "transaction_record": _digest_table(conn, "transaction_record", "transaction_id"),
            "advisor": _digest_table(conn, "advisor", "advisor_id"),
        }
    finally:
        conn.close()

    first, second = _run_backfill_twice(db_path)

    conn = _connect(db_path)
    try:
        final = {
            "transaction_record": _digest_table(conn, "transaction_record", "transaction_id"),
            "advisor": _digest_table(conn, "advisor", "advisor_id"),
        }
        fk_issues = _rows(conn, "PRAGMA foreign_key_check")
        coverage = _coverage(conn)
        duplicates = _duplicates(conn)
        synthetic_entities = _synthetic_group_entities(conn)
        counts = {
            "participant_counts_by_role": _counts_by_role(conn),
            "participant_counts_by_side": _counts_by_side(conn),
            "group_counts_by_type": _counts_by_group_type(conn),
        }
        samples = _samples(conn, sample_limit)
        entity_relationship_exists = _table_exists(conn, "entity_relationship")
    finally:
        conn.close()

    checks = {
        "transaction_record_unchanged": baseline["transaction_record"] == final["transaction_record"],
        "advisor_unchanged": baseline["advisor"] == final["advisor"],
        "foreign_key_check_passed": len(fk_issues) == 0,
        "backfill_idempotent": all(value == 0 for value in second["inserted_counts"].values()),
        "no_duplicate_current_participants": duplicates["duplicate_current_participant_count"] == 0,
        "no_duplicate_groups": duplicates["duplicate_group_count"] == 0,
        "target_coverage_passed": coverage["target_missing_count"] == 0,
        "acquirer_organization_coverage_passed": coverage["acquirer_missing_actionable_count"] == 0,
        "parent_seller_coverage_passed": coverage["parent_seller_missing_count"] == 0,
        "buyer_sponsor_coverage_passed": coverage["buyer_sponsor_missing_count"] == 0,
        "no_synthetic_group_entities": len(synthetic_entities) == 0,
        "entity_relationship_absent": not entity_relationship_exists,
    }

    return {
        "checks": checks,
        "ok": all(checks.values()),
        "baseline_digests": baseline,
        "final_digests": final,
        "first_run_summary": first,
        "second_run_summary": second,
        "foreign_key_issue_count": len(fk_issues),
        "foreign_key_issue_samples": fk_issues[:10],
        "coverage": coverage,
        "duplicates": duplicates,
        "synthetic_group_entity_count": len(synthetic_entities),
        "synthetic_group_entity_samples": synthetic_entities[:10],
        "counts": counts,
        "samples": samples,
        "entity_relationship_exists": entity_relationship_exists,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Drop 3.32a participant backfill.")
    parser.add_argument("--source-db", required=True, help="Copied real DB source path.")
    parser.add_argument(
        "--validation-db",
        help="Optional copied DB output path to keep after validation.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=10,
        help="Maximum consortium and sponsor examples to include.",
    )
    args = parser.parse_args()

    source = Path(args.source_db)
    if not source.exists():
        parser.error(f"--source-db does not exist: {source}")

    validation_arg = Path(args.validation_db) if args.validation_db else None
    tmpdir: tempfile.TemporaryDirectory | None = None
    try:
        validation_db, tmpdir = _copy_source(source, validation_arg)
        result = _validate(validation_db, args.sample_limit)
        result["source_db"] = str(source)
        result["validation_db"] = validation_db
        result["validation_db_retained"] = validation_arg is not None
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    finally:
        if tmpdir is not None:
            tmpdir.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
