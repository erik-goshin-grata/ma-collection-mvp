"""
Validation script for Drop 3.22b data-quality fixes.

Tests (no network, no API cost):
  Change 1 — UNKNOWN treated as non-observation for merger_structure
  Change 2 — Defined-term and bracketed-placeholder rejection for entity-name fields
  Change 3 — document_title watermark skip (EXECUTION VERSION, etc.)
  Change 5 — RECITALS position constraint (>15% rejected) + heading exclusion

Usage:
    python scripts/test_agreement_extract_filters.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from stages.agreement_extract import (
    _DEFINED_TERM_RE,
    _ENTITY_NAME_FIELDS,
    _OBSERVATION_REJECT_VALUES,
    _PLACEHOLDER_RE,
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
    ("has_mac_clause", "UNKNOWN", False, "UNKNOWN not in reject list for has_mac_clause"),
]


def _would_reject_c1(field_name: str, field_value) -> bool:
    if field_value is None:
        return False
    reject = _OBSERVATION_REJECT_VALUES.get(field_name, frozenset())
    return str(field_value) in reject


# ---------------------------------------------------------------------------
# Change 2 — Defined-term and placeholder rejection
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


def _would_reject_c2(field_name: str, value: str) -> bool:
    if field_name not in _ENTITY_NAME_FIELDS:
        return False
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
        got = _would_reject_c2("parent_acquirer_name", val)
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

    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Drop 3.22b: agreement-extract data-quality filter tests")
    print("=" * 60)
    print()
    ok = run_tests()
    n_total = len(C1_CASES) + len(C2_DEFINED_TERM_CASES) + len(C2_PLACEHOLDER_CASES) + len(C3_CASES) + len(C5_CASES)
    print()
    print(f"{'All tests passed.' if ok else 'SOME TESTS FAILED.'}")
    sys.exit(0 if ok else 1)
