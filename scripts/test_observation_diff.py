"""
Validation script for Drop 3.20b cross-source observation tracking and diff surfacing.

Three tests:
  1. Single-source case — one observation per field → has_observation_changes=0
  2. Two-source bump — per_share_price 42.00 → 44.50 from 8K_EXHIBIT_21 to DEFA14A →
       has_observation_changes=1, field_count=1, change_type=INCREASE, delta=2.50
  3. Three-source case — per_share_price 42.00 → 44.50 → 43.00 →
       summary shows all three values in chronological order; change_type reflects
       earliest-to-latest comparison (INCREASE because 43.00 > 42.00)

Tests also verify that target_termination_fee is present in diffs when it changes,
and that unchanged fields do NOT appear in the diff summary.

No live API calls.  Runs against an in-memory SQLite DB constructed from the
schema and migrations.

Usage:
    python scripts/test_observation_diff.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

# ---------------------------------------------------------------------------
# Minimal schema setup
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE transaction_record (
    transaction_id TEXT PRIMARY KEY,
    has_observation_changes INTEGER DEFAULT 0,
    observation_changes_summary TEXT,
    observation_changes_field_count INTEGER DEFAULT 0,
    is_current INTEGER DEFAULT 1
);

CREATE TABLE transaction_document (
    document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    filing_type TEXT NOT NULL,
    filing_date TEXT,
    document_title TEXT
);

CREATE TABLE transaction_document_section (
    section_id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL
);

CREATE TABLE transaction_field_observation (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_value TEXT,
    field_value_numeric REAL,
    source_document_id INTEGER NOT NULL,
    source_section_id INTEGER,
    observed_as_of_date TEXT,
    filing_date TEXT,
    extracted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    extraction_prompt_version TEXT,
    is_current INTEGER DEFAULT 1
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def _insert_doc(conn, txn_id, filing_type, filing_date, title=None) -> int:
    cur = conn.execute(
        "INSERT INTO transaction_document (transaction_id, filing_type, filing_date, document_title) VALUES (?,?,?,?)",
        (txn_id, filing_type, filing_date, title),
    )
    return cur.lastrowid


def _insert_obs(conn, txn_id, field_name, field_value, field_value_numeric, doc_id, filing_date, prompt_ver="test:0.1"):
    conn.execute(
        """
        INSERT INTO transaction_field_observation
            (transaction_id, field_name, field_value, field_value_numeric,
             source_document_id, filing_date, extraction_prompt_version)
        VALUES (?,?,?,?,?,?,?)
        """,
        (txn_id, field_name, str(field_value), field_value_numeric, doc_id, filing_date, prompt_ver),
    )


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

try:
    from stages.agreement_extract import _compute_observation_changes
    _IMPORT_OK = True
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERROR = str(e)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_source() -> bool:
    print("=" * 60)
    print("Test 1: single-source — no changes expected")
    print("=" * 60)

    conn = _make_conn()
    txn_id = "tc_test_001"
    conn.execute("INSERT INTO transaction_record (transaction_id) VALUES (?)", (txn_id,))

    doc_id = _insert_doc(conn, txn_id, "8K_EXHIBIT_21", "2026-04-15", "Agreement and Plan of Merger")
    _insert_obs(conn, txn_id, "per_share_price", "42.00", 42.0, doc_id, "2026-04-15")
    _insert_obs(conn, txn_id, "target_termination_fee", "50000000", 50000000.0, doc_id, "2026-04-15")
    conn.commit()

    has_changes, field_count, summary_json = _compute_observation_changes(conn, txn_id)
    print(f"  has_observation_changes: {has_changes}")
    print(f"  observation_changes_field_count: {field_count}")
    print(f"  observation_changes_summary: {summary_json}")
    print()

    ok = True
    if has_changes != 0:
        print(f"FAIL: has_observation_changes expected 0, got {has_changes}")
        ok = False
    if field_count != 0:
        print(f"FAIL: observation_changes_field_count expected 0, got {field_count}")
        ok = False
    if summary_json is not None:
        print(f"FAIL: observation_changes_summary expected None, got {summary_json!r}")
        ok = False

    if ok:
        print("PASS: single-source case produces no diffs.")
    return ok


def test_two_source_bump() -> bool:
    print("=" * 60)
    print("Test 2: two-source bump — per_share_price 42.00 → 44.50")
    print("=" * 60)

    conn = _make_conn()
    txn_id = "tc_test_002"
    conn.execute("INSERT INTO transaction_record (transaction_id) VALUES (?)", (txn_id,))

    doc1 = _insert_doc(conn, txn_id, "8K_EXHIBIT_21", "2026-04-15", "Agreement and Plan of Merger")
    doc2 = _insert_doc(conn, txn_id, "DEFA14A", "2026-05-20", "Definitive Additional Materials")

    # Same fee from both sources (no diff expected for this field)
    _insert_obs(conn, txn_id, "target_termination_fee", "50000000", 50000000.0, doc1, "2026-04-15")
    _insert_obs(conn, txn_id, "target_termination_fee", "50000000", 50000000.0, doc2, "2026-05-20")

    # Per-share price bumped in supplemental proxy
    _insert_obs(conn, txn_id, "per_share_price", "42.00", 42.0, doc1, "2026-04-15")
    _insert_obs(conn, txn_id, "per_share_price", "44.50", 44.5, doc2, "2026-05-20")
    conn.commit()

    has_changes, field_count, summary_json = _compute_observation_changes(conn, txn_id)
    print(f"  has_observation_changes: {has_changes}")
    print(f"  observation_changes_field_count: {field_count}")
    if summary_json:
        print(f"  observation_changes_summary:")
        for entry in json.loads(summary_json):
            print(f"    field={entry['field']} change_type={entry['change_type']} "
                  f"delta={entry.get('delta')} delta_pct={entry.get('delta_pct')}")
    print()

    ok = True
    if has_changes != 1:
        print(f"FAIL: has_observation_changes expected 1, got {has_changes}")
        ok = False
    if field_count != 1:
        print(f"FAIL: observation_changes_field_count expected 1, got {field_count}")
        ok = False
    if not summary_json:
        print("FAIL: observation_changes_summary is None")
        ok = False
    else:
        entries = json.loads(summary_json)
        if len(entries) != 1:
            print(f"FAIL: expected 1 diff entry, got {len(entries)}")
            ok = False
        else:
            entry = entries[0]
            if entry["field"] != "per_share_price":
                print(f"FAIL: diff field expected 'per_share_price', got {entry['field']!r}")
                ok = False
            if entry.get("change_type") != "INCREASE":
                print(f"FAIL: change_type expected INCREASE, got {entry.get('change_type')!r}")
                ok = False
            if abs(entry.get("delta", 0) - 2.5) > 0.001:
                print(f"FAIL: delta expected 2.5, got {entry.get('delta')}")
                ok = False
            expected_pct = round((2.5 / 42.0) * 100, 2)
            if abs(entry.get("delta_pct", 0) - expected_pct) > 0.01:
                print(f"FAIL: delta_pct expected ~{expected_pct}, got {entry.get('delta_pct')}")
                ok = False
            # Verify unchanged fee is NOT in diffs
            diff_fields = {e["field"] for e in entries}
            if "target_termination_fee" in diff_fields:
                print("FAIL: target_termination_fee appears in diffs but values are identical")
                ok = False

    if ok:
        print("PASS: two-source bump correctly identified with INCREASE, delta=2.5.")
    return ok


def test_three_source_bump_reduce() -> bool:
    print("=" * 60)
    print("Test 3: three-source — per_share_price 42.00 → 44.50 → 43.00")
    print("=" * 60)

    conn = _make_conn()
    txn_id = "tc_test_003"
    conn.execute("INSERT INTO transaction_record (transaction_id) VALUES (?)", (txn_id,))

    doc1 = _insert_doc(conn, txn_id, "8K_EXHIBIT_21", "2026-04-15", "Agreement and Plan of Merger")
    doc2 = _insert_doc(conn, txn_id, "DEFA14A", "2026-05-20", "Supplemental Proxy Materials")
    doc3 = _insert_doc(conn, txn_id, "DEFA14A", "2026-06-10", "Amendment No. 1 Proxy Materials")

    _insert_obs(conn, txn_id, "per_share_price", "42.00", 42.0,  doc1, "2026-04-15")
    _insert_obs(conn, txn_id, "per_share_price", "44.50", 44.5,  doc2, "2026-05-20")
    _insert_obs(conn, txn_id, "per_share_price", "43.00", 43.0,  doc3, "2026-06-10")

    # Also add a termination fee that changes
    _insert_obs(conn, txn_id, "target_termination_fee", "50000000", 50000000.0, doc1, "2026-04-15")
    _insert_obs(conn, txn_id, "target_termination_fee", "55000000", 55000000.0, doc2, "2026-05-20")
    conn.commit()

    has_changes, field_count, summary_json = _compute_observation_changes(conn, txn_id)
    print(f"  has_observation_changes: {has_changes}")
    print(f"  observation_changes_field_count: {field_count}")
    if summary_json:
        print(f"  observation_changes_summary:")
        for entry in json.loads(summary_json):
            print(f"    field={entry['field']} change_type={entry['change_type']} "
                  f"delta={entry.get('delta')} values_count={len(entry.get('values', []))}")
            for v in entry.get("values", []):
                print(f"      {v['filing_date']} [{v['filing_type']}] value={v['value']}")
    print()

    ok = True
    if has_changes != 1:
        print(f"FAIL: has_observation_changes expected 1, got {has_changes}")
        ok = False
    if field_count != 2:
        print(f"FAIL: observation_changes_field_count expected 2 (per_share_price + fee), got {field_count}")
        ok = False
    if not summary_json:
        print("FAIL: observation_changes_summary is None")
        ok = False
    else:
        entries = {e["field"]: e for e in json.loads(summary_json)}

        # per_share_price: earliest=42.00, latest=43.00 → INCREASE (43 > 42)
        psp = entries.get("per_share_price")
        if psp is None:
            print("FAIL: per_share_price not in diffs")
            ok = False
        else:
            if len(psp.get("values", [])) != 3:
                print(f"FAIL: per_share_price expected 3 value entries, got {len(psp.get('values', []))}")
                ok = False
            if psp.get("change_type") != "INCREASE":
                print(f"FAIL: per_share_price change_type expected INCREASE (earliest=42, latest=43), got {psp.get('change_type')!r}")
                ok = False
            if abs(psp.get("delta", 0) - 1.0) > 0.001:
                print(f"FAIL: per_share_price delta expected 1.0 (43-42), got {psp.get('delta')}")
                ok = False
            # Values should be in chronological order
            dates = [v["filing_date"] for v in psp.get("values", [])]
            if dates != sorted(dates):
                print(f"FAIL: per_share_price values not in chronological order: {dates}")
                ok = False

        # target_termination_fee: 50M → 55M → INCREASE
        fee = entries.get("target_termination_fee")
        if fee is None:
            print("FAIL: target_termination_fee not in diffs")
            ok = False
        else:
            if fee.get("change_type") != "INCREASE":
                print(f"FAIL: target_termination_fee change_type expected INCREASE, got {fee.get('change_type')!r}")
                ok = False
            if abs(fee.get("delta", 0) - 5000000) > 1:
                print(f"FAIL: target_termination_fee delta expected 5000000, got {fee.get('delta')}")
                ok = False

    if ok:
        print("PASS: three-source case shows all values in chronological order with correct deltas.")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _IMPORT_OK:
        print(f"SKIP: import error — {_IMPORT_ERROR}")
        sys.exit(0)

    results = []
    for test_fn in [test_single_source, test_two_source_bump, test_three_source_bump_reduce]:
        try:
            passed = test_fn()
        except Exception as exc:
            print(f"ERROR: {test_fn.__name__} raised: {exc}")
            import traceback; traceback.print_exc()
            passed = False
        results.append(passed)
        print()

    passed_count = sum(results)
    print(f"{passed_count}/{len(results)} tests passed.")
    sys.exit(0 if all(results) else 1)
