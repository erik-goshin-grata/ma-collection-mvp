"""
Validation script for Drop 3.22 _EX_21_RE and _find_exhibits() behaviour.

Tests:
  Pass cases (should match EX-2.1):
    1.  EX-2.1     — standard form
    2.  EX-2.01    — zero-padded minor version
    3.  EX 2.1     — space separator
    4.  EX2.1      — no prefix separator
    5.  ex-2.1     — all-lowercase (case-insensitive)
    6.  EXHIBIT 2.1  — long-form description
    7.  EXHIBIT 2.01 — long-form zero-padded
    8.  2.1         — bare form (appears in some description fields)

  Reject cases (must NOT match):
    9.  EX-2       — no decimal, broader Exhibit 2 class
    10. EX-21      — list of subsidiaries (was incorrectly matched by old regex)
    11. EX-21.1    — list of subsidiaries with sub-number
    12. EX-3.1     — unrelated exhibit class
    13. EX-99.1    — press release exhibit

  _find_exhibits() integration:
    14. Filing with EX-2.1 — returns (label, url)
    15. Filing with EX-21 and EX-2.1 — returns only EX-2.1, not EX-21
    16. Filing with no relevant exhibit — returns []
    17. Duplicate URL (same doc listed twice) — returned only once

No network calls.

Usage:
    python scripts/test_find_exhibits.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.sec_api import _EX_21_RE, _find_exhibits

# ---------------------------------------------------------------------------
# Regex pass/reject cases
# ---------------------------------------------------------------------------

REGEX_PASS = [
    ("EX-2.1",     "standard form"),
    ("EX-2.01",    "zero-padded minor version"),
    ("EX 2.1",     "space separator"),
    ("EX2.1",      "no prefix separator"),
    ("ex-2.1",     "all-lowercase"),
    ("EXHIBIT 2.1",  "long-form"),
    ("EXHIBIT 2.01", "long-form zero-padded"),
    ("2.1",        "bare form"),
]

REGEX_REJECT = [
    ("EX-2",    "no decimal — broader Exhibit 2 class"),
    ("EX-21",   "list of subsidiaries — must NOT match"),
    ("EX-21.1", "list of subsidiaries with sub-number — must NOT match"),
    ("EX-3.1",  "unrelated exhibit class"),
    ("EX-99.1", "press release exhibit"),
]


def _make_filing(docs: list[dict]) -> dict:
    return {"documentFormatFiles": docs}


def _doc(type_: str, url: str) -> dict:
    return {"type": type_, "description": type_, "documentUrl": url}


INTEGRATION_CASES = [
    (
        "Filing with EX-2.1 returns (label, url)",
        _make_filing([_doc("8-K", "https://sec.gov/8k.htm"), _doc("EX-2.1", "https://sec.gov/ex21.htm")]),
        [("EX-2.1", "https://sec.gov/ex21.htm")],
    ),
    (
        "Filing with EX-21 and EX-2.1 returns only EX-2.1",
        _make_filing([
            _doc("EX-21",  "https://sec.gov/subs.htm"),
            _doc("EX-2.1", "https://sec.gov/merger.htm"),
        ]),
        [("EX-2.1", "https://sec.gov/merger.htm")],
    ),
    (
        "Filing with no relevant exhibit returns []",
        _make_filing([_doc("8-K", "https://sec.gov/8k.htm"), _doc("EX-99.1", "https://sec.gov/pr.htm")]),
        [],
    ),
    (
        "Duplicate URL returned only once",
        _make_filing([
            _doc("EX-2.1",  "https://sec.gov/merger.htm"),
            _doc("EX-2.01", "https://sec.gov/merger.htm"),  # same URL, different label
        ]),
        [("EX-2.1", "https://sec.gov/merger.htm")],
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests() -> bool:
    all_pass = True

    print("--- Regex PASS cases ---")
    for label, description in REGEX_PASS:
        matched = bool(_EX_21_RE.match(label))
        ok = matched
        status = "PASS" if ok else "FAIL"
        print(f"  {status} [{label!r:20s}]  {description}")
        if not ok:
            all_pass = False

    print()
    print("--- Regex REJECT cases ---")
    for label, description in REGEX_REJECT:
        matched = bool(_EX_21_RE.match(label))
        ok = not matched
        status = "PASS" if ok else "FAIL"
        print(f"  {status} [{label!r:20s}]  {description}")
        if not ok:
            all_pass = False
            print(f"         !! incorrectly matched {label!r}")

    print()
    print("--- _find_exhibits() integration ---")
    for description, filing, expected in INTEGRATION_CASES:
        result = _find_exhibits(filing, _EX_21_RE)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {description}")
        if not ok:
            print(f"         expected: {expected}")
            print(f"         got:      {result}")
            all_pass = False

    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Drop 3.22: _EX_21_RE and _find_exhibits() tests")
    print("=" * 60)
    print()
    ok = run_tests()
    n_total = len(REGEX_PASS) + len(REGEX_REJECT) + len(INTEGRATION_CASES)
    print()
    results = (
        [bool(_EX_21_RE.match(l)) for l, _ in REGEX_PASS]
        + [not bool(_EX_21_RE.match(l)) for l, _ in REGEX_REJECT]
        + [_find_exhibits(f, _EX_21_RE) == e for _, f, e in INTEGRATION_CASES]
    )
    n_pass = sum(results)
    print(f"{n_pass}/{n_total} tests passed.")
    sys.exit(0 if ok else 1)
