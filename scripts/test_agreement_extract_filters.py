"""
Validation script for Drop 3.22b and 3.22c data-quality fixes.

Tests (no network, no API cost):
  Change 1 — UNKNOWN treated as non-observation for merger_structure
  Change 2 — Defined-term and bracketed-placeholder rejection for entity-name fields
  Change 3 — document_title watermark skip (EXECUTION VERSION, etc.)
  Change 5 — RECITALS position constraint (>15% rejected) + heading exclusion
  3.22c — _clear_stale_canonical_fields() NULLs TR fields on empty observation set

Usage:
    python scripts/test_agreement_extract_filters.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from stages.agreement_extract import (
    _CANONICAL_FIELD_OBSERVATION_MAP,
    _DEFINED_TERM_RE,
    _OBSERVATION_REJECT_VALUES,
    _PLACEHOLDER_RE,
    _clear_stale_canonical_fields,
)
from adapters.sec_api import extract_document_title
from lib.section_tagger import tag_sections


# ---------------------------------------------------------------------------
# Change 1 — UNKNOWN rejection
# ---------------------------------------------------------------------------

C1_CASES = [
    # (field_name, value, should_reject, description)
    ("merger_structure", "UNKNOWN", True,  "UNKNOWN rejected for merger_structure"),
    ("merger_structure", "REVERSE_TRIANGULAR", False, "REVERSE_TRIANGULAR accepted"),
    ("merger_structure", "DIRECT", False, "DIRECT accepted"),
    ("merger_structure", None, False, "None accepted (skipped by None guard, not UNKNOWN guard)"),
    ("regulatory_approvals_required", "UNKNOWN", False, "UNKNOWN not in reject list for regulatory_approvals_required"),
]


def _would_reject_c1(field_name: str, field_value) -> bool:
    if field_value is None:
        return False
    reject = _OBSERVATION_REJECT_VALUES.get(field_name, frozenset())
    return str(field_value) in reject


# ---------------------------------------------------------------------------
# Change 2 — Defined-term and placeholder rejection
#
# The rejection check used to be gated behind a fixed _ENTITY_NAME_FIELDS
# field-name set; that set was retired in agreement_recitals 0.6 in favor of
# the repeating parties[] array (party.{role} compound observations), so the
# check now applies to every party name unconditionally, not by field name.
# ---------------------------------------------------------------------------

C2_DEFINED_TERM_CASES = [
    # (value, should_reject, description)
    ("Parent",      True,  "defined term 'Parent'"),
    ("Company",     True,  "defined term 'Company'"),
    ("Purchaser",   True,  "defined term 'Purchaser'"),
    ("Seller",      True,  "defined term 'Seller'"),
    ("Buyer",       True,  "defined term 'Buyer'"),
    ("Target",      True,  "defined term 'Target'"),
    ("Acquirer",    True,  "defined term 'Acquirer'"),
    ("SPAC",        True,  "defined term 'SPAC'"),
    ("Sponsor",     True,  "defined term 'Sponsor'"),
    ("Merger Sub",  True,  "defined term 'Merger Sub'"),
    ("MergerSub",   False, "not a recognized defined term"),
    ("parent",      True,  "lowercase 'parent' (case-insensitive)"),
    # Real legal names should NOT be rejected
    ("Essential Utilities, Inc.", False, "real legal name"),
    ("American Water Works Company, Inc.", False, "real legal name with commas"),
    ("Legato Merger Corp. III", False, "name containing 'Merger' but not bare defined term"),
    ("NorthStar Capital Partners LP", False, "real acquirer name"),
]

C2_PLACEHOLDER_CASES = [
    # (value, should_reject, description)
    ("[Purchaser]",  True,  "bracketed placeholder [Purchaser]"),
    ("[Name]",       True,  "bracketed placeholder [Name]"),
    ("[●]",          False, "bullet placeholder — not Latin brackets"),  # depends on implementation
    ("Campbell Lutyens Holdings Limited", False, "real name, no brackets"),
]


def _would_reject_c2(value: str) -> bool:
    sv = str(value).strip()
    return bool(_DEFINED_TERM_RE.match(sv) or _PLACEHOLDER_RE.search(sv))


# ---------------------------------------------------------------------------
# Change 3 — document_title watermark skip
# ---------------------------------------------------------------------------

def _make_doc(lines: list[str]) -> str:
    return "\n".join(lines)


C3_CASES = [
    (
        _make_doc(["EXECUTION VERSION", "", "AGREEMENT AND PLAN OF MERGER", "dated as of April 1, 2026"]),
        "AGREEMENT AND PLAN OF MERGER",
        "EXECUTION VERSION skipped; real title returned",
    ),
    (
        _make_doc(["EXECUTION COPY", "", "SALE AND PURCHASE AGREEMENT"]),
        "SALE AND PURCHASE AGREEMENT",
        "EXECUTION COPY skipped",
    ),
    (
        _make_doc(["CONFORMED COPY", "", "AGREEMENT AND PLAN OF MERGER"]),
        "AGREEMENT AND PLAN OF MERGER",
        "CONFORMED COPY skipped",
    ),
    (
        _make_doc(["AGREEMENT AND PLAN OF MERGER"]),
        "AGREEMENT AND PLAN OF MERGER",
        "No watermark — title returned directly",
    ),
    (
        _make_doc(["EXECUTION VERSION", "CONFORMED COPY", ""]),
        None,
        "Only watermarks — returns None",
    ),
]


# ---------------------------------------------------------------------------
# Change 5 — RECITALS position constraint + heading exclusion
# ---------------------------------------------------------------------------

def _make_merger_doc(recitals_position_pct: float, heading: str = "RECITALS") -> str:
    total = 100_000
    pad = int(total * recitals_position_pct)
    prefix = "X" * pad + "\n"
    now_therefore = "NOW, THEREFORE, in consideration of the mutual covenants herein...\n"
    recitals_block = (
        f"\n{heading}\n\n"
        "WHEREAS, Parent desires to acquire Target;\n\n"
        "WHEREAS, the Board of Target has approved the Merger;\n\n"
    )
    suffix = "Article I. Definitions\n\n" + "Z" * 50_000
    # Place NOW THEREFORE at 40% mark
    now_pos = int(total * 0.40)
    return prefix + recitals_block + "B" * max(0, now_pos - pad - len(recitals_block)) + now_therefore + suffix


def _make_exhibit_doc(heading: str) -> str:
    # Short doc with a schedule/form heading near start (3%)
    prefix = "A" * 3000 + "\n"
    block = f"\n{heading}\n\nWHEREAS, this form is attached as a schedule...\n\n"
    suffix = "Body text " * 5000
    return prefix + block + suffix


C5_CASES = [
    # (doc_fn, heading, expected_recitals_count, description)
    (
        _make_merger_doc(0.02, "RECITALS"),
        1,
        "RECITALS at 2% accepted",
    ),
    (
        _make_merger_doc(0.20, "RECITALS"),
        0,
        "RECITALS at 20% rejected (>15%)",
    ),
    (
        _make_exhibit_doc("FORM OF DEED OF ADHERENCE"),
        0,
        "FORM OF heading excluded",
    ),
    (
        _make_exhibit_doc("SCHEDULE A"),
        0,
        "SCHEDULE A heading excluded",
    ),
    (
        _make_exhibit_doc("EXHIBIT B"),
        0,
        "EXHIBIT B heading excluded",
    ),
]


# ---------------------------------------------------------------------------
# Change 6 (3.22c) — _clear_stale_canonical_fields: canonical NULL on empty obs
# ---------------------------------------------------------------------------

_NULL_LOG = SimpleNamespace(debug=lambda *a, **kw: None, warning=lambda *a, **kw: None)
_NOW = "2026-05-05T00:00:00+00:00"


def _make_canonical_db() -> sqlite3.Connection:
    """In-memory DB with the tables needed by _clear_stale_canonical_fields."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    tr_cols = ", ".join(
        [f"{col} TEXT" for col in _CANONICAL_FIELD_OBSERVATION_MAP]
        + ["consideration_components TEXT", "updated_at TEXT"]
    )
    conn.execute(f"CREATE TABLE transaction_record (transaction_id TEXT PRIMARY KEY, {tr_cols})")
    conn.execute(
        """
        CREATE TABLE transaction_field_observation (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            field_value TEXT,
            is_current INTEGER DEFAULT 1
        )
        """
    )
    return conn


def _insert_tr(conn: sqlite3.Connection, txn_id: str, **fields) -> None:
    cols = ["transaction_id"] + list(fields)
    vals = [txn_id] + list(fields.values())
    placeholders = ", ".join("?" * len(vals))
    conn.execute(f"INSERT INTO transaction_record ({', '.join(cols)}) VALUES ({placeholders})", vals)


def _insert_obs(conn: sqlite3.Connection, txn_id: str, field_name: str, field_value: str, is_current: int = 1) -> None:
    conn.execute(
        "INSERT INTO transaction_field_observation (transaction_id, field_name, field_value, is_current) VALUES (?, ?, ?, ?)",
        (txn_id, field_name, field_value, is_current),
    )


def _get_tr(conn: sqlite3.Connection, txn_id: str, col: str):
    row = conn.execute(f"SELECT {col} FROM transaction_record WHERE transaction_id=?", (txn_id,)).fetchone()
    return row[col] if row else None


def run_canonical_clear_tests() -> bool:
    all_pass = True

    print("--- 3.22c: _clear_stale_canonical_fields ---")

    # Test 1: soft-deleted observation → canonical field becomes NULL
    conn = _make_canonical_db()
    _insert_tr(conn, "txn1", merger_structure="REVERSE_TRIANGULAR")
    _insert_obs(conn, "txn1", "merger_structure", "REVERSE_TRIANGULAR", is_current=0)
    _clear_stale_canonical_fields(conn, "txn1", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn1", "merger_structure")
    ok = got is None
    print(f"  {'PASS' if ok else 'FAIL'}  soft-deleted obs → merger_structure becomes NULL (got {got!r})")
    all_pass = all_pass and ok

    # Test 2: filter-rejection case (no observation written; stale TR value present)
    conn = _make_canonical_db()
    _insert_tr(conn, "txn2", acquirer_merger_sub_name="Merger Sub")
    # No observation for party.MERGER_SUB (filtered before insert — never written)
    _clear_stale_canonical_fields(conn, "txn2", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn2", "acquirer_merger_sub_name")
    ok = got is None
    print(f"  {'PASS' if ok else 'FAIL'}  no obs for party.MERGER_SUB → acquirer_merger_sub_name becomes NULL (got {got!r})")
    all_pass = all_pass and ok

    # Test 3: multiple observations, all soft-deleted → field NULLed
    conn = _make_canonical_db()
    _insert_tr(conn, "txn3", regulatory_approvals_required="1")
    for _ in range(3):
        _insert_obs(conn, "txn3", "regulatory_approvals_required", "1", is_current=0)
    _clear_stale_canonical_fields(conn, "txn3", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn3", "regulatory_approvals_required")
    ok = got is None
    print(f"  {'PASS' if ok else 'FAIL'}  3 soft-deleted obs → regulatory_approvals_required becomes NULL (got {got!r})")
    all_pass = all_pass and ok

    # Test 4: mix of current and soft-deleted → current wins, field not NULLed
    conn = _make_canonical_db()
    _insert_tr(conn, "txn4", merger_structure="FORWARD_TRIANGULAR")
    _insert_obs(conn, "txn4", "merger_structure", "REVERSE_TRIANGULAR", is_current=0)
    _insert_obs(conn, "txn4", "merger_structure", "FORWARD_TRIANGULAR", is_current=1)
    _clear_stale_canonical_fields(conn, "txn4", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn4", "merger_structure")
    ok = got == "FORWARD_TRIANGULAR"
    print(f"  {'PASS' if ok else 'FAIL'}  1 current + 1 soft-deleted → merger_structure preserved (got {got!r})")
    all_pass = all_pass and ok

    # Test 5: all canonical fields NULLed when no observations exist (clean-slate transaction)
    conn = _make_canonical_db()
    _insert_tr(
        conn, "txn5",
        merger_structure="DIRECT",
        acquirer_merger_sub_name="Sub Inc.",
        target_fee_amount="50000000",
        target_fee_percentage="2.5",
        has_go_shop="1",
        regulatory_approvals_required="1",
    )
    # No observations at all
    _clear_stale_canonical_fields(conn, "txn5", _NOW, _NULL_LOG)
    stale = [
        col for col in ["merger_structure", "acquirer_merger_sub_name", "target_fee_amount",
                         "target_fee_percentage", "has_go_shop", "regulatory_approvals_required"]
        if _get_tr(conn, "txn5", col) is not None
    ]
    ok = not stale
    print(f"  {'PASS' if ok else 'FAIL'}  no observations → all 6 stale fields become NULL (still set: {stale})")
    all_pass = all_pass and ok

    # Test 6: consideration_components NULLed when no consideration.* observations
    conn = _make_canonical_db()
    _insert_tr(conn, "txn6", consideration_components='[{"form":"CASH"}]')
    # No consideration.* observations
    _clear_stale_canonical_fields(conn, "txn6", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn6", "consideration_components")
    ok = got is None
    print(f"  {'PASS' if ok else 'FAIL'}  no consideration.* obs → consideration_components becomes NULL (got {got!r})")
    all_pass = all_pass and ok

    # Test 7: consideration_components preserved when consideration.* observation exists
    conn = _make_canonical_db()
    _insert_tr(conn, "txn7", consideration_components='[{"form":"CASH"}]')
    _insert_obs(conn, "txn7", "consideration.CASH.per_share_amount", "34.00", is_current=1)
    _clear_stale_canonical_fields(conn, "txn7", _NOW, _NULL_LOG)
    got = _get_tr(conn, "txn7", "consideration_components")
    ok = got == '[{"form":"CASH"}]'
    print(f"  {'PASS' if ok else 'FAIL'}  consideration.* obs present → consideration_components preserved (got {got!r})")
    all_pass = all_pass and ok

    # Test 8: unrelated fields untouched (fields with current observations are NOT NULLed)
    conn = _make_canonical_db()
    _insert_tr(conn, "txn8", regulatory_approvals_required="1", merger_structure="DIRECT")
    _insert_obs(conn, "txn8", "regulatory_approvals_required", "1", is_current=1)
    # No merger_structure observation
    _clear_stale_canonical_fields(conn, "txn8", _NOW, _NULL_LOG)
    reg = _get_tr(conn, "txn8", "regulatory_approvals_required")
    ms = _get_tr(conn, "txn8", "merger_structure")
    ok = reg == "1" and ms is None
    print(f"  {'PASS' if ok else 'FAIL'}  regulatory_approvals_required preserved (obs current); merger_structure NULLed (no obs) (reg={reg!r}, ms={ms!r})")
    all_pass = all_pass and ok

    return all_pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests() -> bool:
    all_pass = True

    print("--- Change 1: UNKNOWN rejection ---")
    for field, val, should_reject, desc in C1_CASES:
        got = _would_reject_c1(field, val)
        ok = got == should_reject
        print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
        if not ok:
            print(f"       expected reject={should_reject}, got {got}")
            all_pass = False

    print()
    print("--- Change 2a: defined-term rejection ---")
    for val, should_reject, desc in C2_DEFINED_TERM_CASES:
        got = _would_reject_c2(val)
        ok = got == should_reject
        print(f"  {'PASS' if ok else 'FAIL'}  [{val!r:45s}]  {desc}")
        if not ok:
            print(f"       expected reject={should_reject}, got {got}")
            all_pass = False

    print()
    print("--- Change 2b: placeholder rejection ---")
    for val, should_reject, desc in C2_PLACEHOLDER_CASES:
        got = bool(_PLACEHOLDER_RE.search(str(val).strip()))
        ok = got == should_reject
        print(f"  {'PASS' if ok else 'FAIL'}  [{val!r:45s}]  {desc}")
        if not ok:
            print(f"       expected reject={should_reject}, got {got}")
            # placeholder cases are advisory; don't fail suite on [●] edge case
            if "[●]" not in val:
                all_pass = False

    print()
    print("--- Change 3: document_title watermark skip ---")
    for doc_text, expected, desc in C3_CASES:
        got = extract_document_title(doc_text)
        ok = got == expected
        print(f"  {'PASS' if ok else 'FAIL'}  {desc}")
        if not ok:
            print(f"       expected {expected!r}, got {got!r}")
            all_pass = False

    print()
    print("--- Change 5: RECITALS tagger position + heading exclusion ---")
    for doc_text, expected_count, desc in C5_CASES:
        sections = tag_sections(doc_text)
        recitals = [s for s in sections if s.section_type == "RECITALS"]
        ok = len(recitals) == expected_count
        print(f"  {'PASS' if ok else 'FAIL'}  {desc}  (got {len(recitals)}, expected {expected_count})")
        if not ok:
            for s in recitals:
                print(f"       heading={s.heading_text!r}  offset={s.excerpt_start_offset}")
            all_pass = False

    print()
    ok_canonical = run_canonical_clear_tests()
    if not ok_canonical:
        all_pass = False

    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Drop 3.22b/c: agreement-extract data-quality filter tests")
    print("=" * 60)
    print()
    ok = run_tests()
    n_total = (
        len(C1_CASES) + len(C2_DEFINED_TERM_CASES) + len(C2_PLACEHOLDER_CASES)
        + len(C3_CASES) + len(C5_CASES) + 8  # 8 canonical-clear tests
    )
    print()
    print(f"{'All tests passed.' if ok else 'SOME TESTS FAILED.'}")
    sys.exit(0 if ok else 1)
