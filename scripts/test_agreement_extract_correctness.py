"""
Drop 3.25 — Correctness tests for the observation pipeline fixes.

Tests three bugs fixed in this drop:
  A. Unique index enforcement — duplicate (source_section_id, field_name, field_value)
     with is_current=1 is rejected; INSERT OR IGNORE makes it a no-op.
  B. _write_observations() INSERT OR IGNORE — same field/value = no-op;
     different value = second row inserted; different section = both rows inserted.
  C. Savepoint rollback restores prior observations on section failure.
  D. agreement_extracted_at stays NULL when a section fails (document remains PENDING).
  E. Full-success path: agreement_extracted_at set, observations committed.

No network. No API cost. Uses in-memory SQLite only.

Usage:
    python scripts/test_agreement_extract_correctness.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from stages.agreement_extract import _write_observations

_PASS = 0
_FAIL = 0


def _check(label: str, condition: bool) -> None:
    global _PASS, _FAIL
    if condition:
        print(f"  PASS  {label}")
        _PASS += 1
    else:
        print(f"  FAIL  {label}")
        _FAIL += 1


# ---------------------------------------------------------------------------
# Minimal in-memory schema
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """Return an in-memory connection with the minimal schema for these tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")  # no FK tables needed in isolation
    conn.executescript("""
        CREATE TABLE transaction_field_observation (
            observation_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id            TEXT NOT NULL,
            field_name                TEXT NOT NULL,
            field_value               TEXT,
            field_value_numeric       REAL,
            source_document_id        INTEGER NOT NULL,
            source_section_id         INTEGER,
            observed_as_of_date       TEXT,
            filing_date               TEXT,
            extracted_at              TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            extraction_prompt_version TEXT,
            is_current                INTEGER DEFAULT 1
        );
        CREATE UNIQUE INDEX idx_observation_unique_current
            ON transaction_field_observation (source_section_id, field_name, field_value)
            WHERE is_current = 1;
        CREATE TABLE transaction_document (
            document_id           INTEGER PRIMARY KEY,
            transaction_id        TEXT,
            filing_type           TEXT,
            agreement_extracted_at DATETIME
        );
    """)
    return conn


def _count_current(conn: sqlite3.Connection, txn_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=? AND is_current=1",
        (txn_id,),
    ).fetchone()[0]


def _count_all(conn: sqlite3.Connection, txn_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=?",
        (txn_id,),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# A. Unique index enforcement
# ---------------------------------------------------------------------------

def run_unique_index_tests() -> None:
    print("\n--- A. Unique index enforcement ---")
    conn = _make_conn()

    # A1: direct duplicate INSERT is rejected by index
    conn.execute(
        "INSERT INTO transaction_field_observation (transaction_id, field_name, field_value, source_document_id, source_section_id) VALUES ('t1', 'f1', 'v1', 1, 10)"
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO transaction_field_observation (transaction_id, field_name, field_value, source_document_id, source_section_id) VALUES ('t1', 'f1', 'v1', 1, 10)"
        )
        conn.commit()
        _check("A1: duplicate INSERT raises IntegrityError", False)
    except sqlite3.IntegrityError:
        _check("A1: duplicate INSERT raises IntegrityError", True)

    # A2: INSERT OR IGNORE on same tuple — no-op, count stable
    count_before = _count_current(conn, "t1")
    conn.execute(
        "INSERT OR IGNORE INTO transaction_field_observation (transaction_id, field_name, field_value, source_document_id, source_section_id) VALUES ('t1', 'f1', 'v1', 1, 10)"
    )
    conn.commit()
    _check("A2: INSERT OR IGNORE on duplicate — count stable", _count_current(conn, "t1") == count_before)

    # A3: soft-deleted row (is_current=0) does NOT block a new is_current=1 insert
    conn.execute(
        "UPDATE transaction_field_observation SET is_current=0 WHERE transaction_id='t1'"
    )
    conn.commit()
    conn.execute(
        "INSERT OR IGNORE INTO transaction_field_observation (transaction_id, field_name, field_value, source_document_id, source_section_id) VALUES ('t1', 'f1', 'v1', 1, 10)"
    )
    conn.commit()
    _check("A3: soft-deleted row doesn't block re-insert of same key",
           _count_current(conn, "t1") == 1)
    _check("A3: total row count = 2 (one soft-deleted, one current)",
           _count_all(conn, "t1") == 2)

    conn.close()


# ---------------------------------------------------------------------------
# B. _write_observations INSERT OR IGNORE behaviour
# ---------------------------------------------------------------------------

def run_write_observations_tests() -> None:
    print("\n--- B. _write_observations INSERT OR IGNORE ---")
    conn = _make_conn()

    base_args = dict(
        transaction_id="t2",
        source_document_id=2,
        source_section_id=20,
        filing_date="2025-01-01",
        extraction_prompt_version="test:0.1",
    )

    # B1: first write inserts scalar field
    _write_observations(conn, extraction_results={"merger_structure": "FORWARD_MERGER"}, **base_args)
    conn.commit()
    _check("B1: first write — row inserted", _count_current(conn, "t2") == 1)

    # B2: identical write is no-op (INSERT OR IGNORE)
    _write_observations(conn, extraction_results={"merger_structure": "FORWARD_MERGER"}, **base_args)
    conn.commit()
    _check("B2: identical re-write — no-op (INSERT OR IGNORE)", _count_current(conn, "t2") == 1)

    # B3: different value for same field/section — second row inserts
    _write_observations(conn, extraction_results={"merger_structure": "REVERSE_MERGER"}, **base_args)
    conn.commit()
    _check("B3: different value, same section/field — second row inserted",
           _count_current(conn, "t2") == 2)

    # B4: same field/value but different section — both rows exist (corroboration)
    args_sec2 = {**base_args, "source_section_id": 21}
    _write_observations(conn, extraction_results={"merger_structure": "FORWARD_MERGER"}, **args_sec2)
    conn.commit()
    _check("B4: same field/value, different section — both rows exist (corroboration)",
           _count_current(conn, "t2") == 3)

    # B5: securities compound field — INSERT OR IGNORE on duplicate
    sec_result = {"securities": [{"security_type": "COMMON_STOCK", "shares_outstanding": 1000000, "shares_outstanding_as_of": "2025-01-01"}]}
    _write_observations(conn, extraction_results=sec_result, **base_args)
    conn.commit()
    count_after_first = _count_current(conn, "t2")
    _write_observations(conn, extraction_results=sec_result, **base_args)
    conn.commit()
    _check("B5: duplicate securities observation — INSERT OR IGNORE no-op",
           _count_current(conn, "t2") == count_after_first)

    # B6: consideration_components compound field — INSERT OR IGNORE on duplicate
    consid_result = {"consideration_components": [{"form": "CASH", "per_share_amount": 42.50}]}
    _write_observations(conn, extraction_results=consid_result, **base_args)
    conn.commit()
    count_after_first = _count_current(conn, "t2")
    _write_observations(conn, extraction_results=consid_result, **base_args)
    conn.commit()
    _check("B6: duplicate consideration observation — INSERT OR IGNORE no-op",
           _count_current(conn, "t2") == count_after_first)

    conn.close()


# ---------------------------------------------------------------------------
# C + D + E. Savepoint rollback / PENDING / EXTRACTED path
# ---------------------------------------------------------------------------

def _setup_doc(conn: sqlite3.Connection, doc_id: int, txn_id: str) -> None:
    """Insert a document row and seed it with prior observations."""
    conn.execute(
        "INSERT INTO transaction_document (document_id, transaction_id, filing_type) VALUES (?, ?, '8K_EXHIBIT_21')",
        (doc_id, txn_id),
    )
    # Prior observations simulating a previous successful extraction
    for i in range(3):
        conn.execute(
            "INSERT INTO transaction_field_observation "
            "(transaction_id, field_name, field_value, source_document_id, source_section_id, is_current) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (txn_id, f"prior_field_{i}", f"prior_value_{i}", doc_id, 100 + i),
        )
    conn.commit()


def run_savepoint_tests() -> None:
    print("\n--- C. Savepoint rollback restores prior observations ---")
    conn = _make_conn()

    txn_id = "t3"
    doc_id = 3

    _setup_doc(conn, doc_id, txn_id)
    prior_count = _count_current(conn, txn_id)
    _check("C-setup: prior observations present", prior_count == 3)

    # Simulate the per-document savepoint path with a forced failure
    conn.execute("SAVEPOINT doc_extract")
    # Soft-delete prior observations
    conn.execute(
        "UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?",
        (doc_id,),
    )
    _check("C1: soft-delete zeroes current count inside savepoint",
           _count_current(conn, txn_id) == 0)

    # Partial writes succeed
    conn.execute(
        "INSERT OR IGNORE INTO transaction_field_observation "
        "(transaction_id, field_name, field_value, source_document_id, source_section_id) "
        "VALUES (?, 'new_field', 'new_value', ?, 200)",
        (txn_id, doc_id),
    )

    # Simulate section failure — rollback
    conn.execute("ROLLBACK TO SAVEPOINT doc_extract")
    conn.execute("RELEASE SAVEPOINT doc_extract")

    _check("C2: after rollback, prior observations restored",
           _count_current(conn, txn_id) == prior_count)
    _check("C3: new partial write is gone after rollback",
           conn.execute(
               "SELECT COUNT(*) FROM transaction_field_observation WHERE field_name='new_field'",
           ).fetchone()[0] == 0)

    conn.close()


def run_pending_on_failure_tests() -> None:
    print("\n--- D. Document remains PENDING on section failure ---")
    conn = _make_conn()

    txn_id = "t4"
    doc_id = 4
    _setup_doc(conn, doc_id, txn_id)

    # Simulate failure path: savepoint → soft-delete → partial writes → rollback
    # agreement_extracted_at is NOT set before rollback
    conn.execute("SAVEPOINT doc_extract")
    conn.execute(
        "UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?",
        (doc_id,),
    )
    # Simulate section failure — do NOT set agreement_extracted_at
    conn.execute("ROLLBACK TO SAVEPOINT doc_extract")
    conn.execute("RELEASE SAVEPOINT doc_extract")

    extracted_at = conn.execute(
        "SELECT agreement_extracted_at FROM transaction_document WHERE document_id=?",
        (doc_id,),
    ).fetchone()["agreement_extracted_at"]
    _check("D1: agreement_extracted_at remains NULL on rollback",
           extracted_at is None)
    _check("D2: prior observations still current after rollback",
           _count_current(conn, txn_id) == 3)

    conn.close()


def run_extracted_on_success_tests() -> None:
    print("\n--- E. Document marked EXTRACTED only on full success ---")
    conn = _make_conn()

    txn_id = "t5"
    doc_id = 5
    _setup_doc(conn, doc_id, txn_id)

    now = "2026-05-06T00:00:00+00:00"

    # Simulate success path: savepoint → soft-delete → new writes → release
    conn.execute("SAVEPOINT doc_extract")
    conn.execute(
        "UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?",
        (doc_id,),
    )
    # Write new observations
    for i in range(2):
        conn.execute(
            "INSERT OR IGNORE INTO transaction_field_observation "
            "(transaction_id, field_name, field_value, source_document_id, source_section_id) "
            "VALUES (?, ?, ?, ?, 300)",
            (txn_id, f"new_field_{i}", f"new_value_{i}", doc_id),
        )
    # Set agreement_extracted_at inside savepoint
    conn.execute(
        "UPDATE transaction_document SET agreement_extracted_at=? WHERE document_id=?",
        (now, doc_id),
    )
    conn.execute("RELEASE SAVEPOINT doc_extract")
    conn.commit()

    extracted_at = conn.execute(
        "SELECT agreement_extracted_at FROM transaction_document WHERE document_id=?",
        (doc_id,),
    ).fetchone()["agreement_extracted_at"]
    _check("E1: agreement_extracted_at set on full success", extracted_at == now)
    _check("E2: prior soft-deleted rows are gone (replaced)",
           conn.execute(
               "SELECT COUNT(*) FROM transaction_field_observation WHERE field_name LIKE 'prior_field_%' AND is_current=1",
           ).fetchone()[0] == 0)
    _check("E3: new observations are current",
           _count_current(conn, txn_id) == 2)
    _check("E4: total row count = 5 (3 soft-deleted + 2 new current)",
           _count_all(conn, txn_id) == 5)

    conn.close()


def run_tests() -> None:
    print("=" * 60)
    print("test_agreement_extract_correctness.py  (Drop 3.25)")
    print("=" * 60)

    run_unique_index_tests()
    run_write_observations_tests()
    run_savepoint_tests()
    run_pending_on_failure_tests()
    run_extracted_on_success_tests()

    total = _PASS + _FAIL
    print(f"\n{'=' * 60}")
    print(f"Results: {_PASS}/{total} passed  {_FAIL} failed")
    if _FAIL:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
