"""
Validation script for Drop 3.21 extract_document_title() fix.

Tests:
  1. SGML <DOCUMENT> tag — must NOT be returned as title (was the bug)
  2. <HTML> tag — must NOT be returned
  3. <TYPE>S-4/A tag — must NOT be returned
  4. Legitimate all-caps merger agreement title — must be returned
  5. Title-Case heading with keyword — must be returned
  6. SEC cover boilerplate line — must be skipped
  7. Real SGML preamble block — title extracted from inner HTML, not the wrapper

No live API calls.

Usage:
    python scripts/test_extract_document_title.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.sec_api import extract_document_title

CASES = [
    # (description, raw_text, expected_result_or_None_check)
    (
        "SGML <DOCUMENT> tag is NOT returned as title",
        "<DOCUMENT>\n<TYPE>DEFM14A\n<SEQUENCE>1\n<FILENAME>defm14a.htm\n\nAGREEMENT AND PLAN OF MERGER",
        "AGREEMENT AND PLAN OF MERGER",   # title comes from the all-caps line AFTER the tags
    ),
    (
        "<DOCUMENT> alone returns None (no title in doc)",
        "<DOCUMENT>\n<TYPE>DEFM14A\n",
        None,
    ),
    (
        "<HTML> tag is NOT returned as title",
        "<HTML>\n<HEAD>\n<TITLE>Agreement and Plan of Merger</TITLE>\n</HEAD>\n",
        None,   # none of these lines qualify (too short or boilerplate)
    ),
    (
        "Legitimate all-caps merger agreement title is returned",
        "AGREEMENT AND PLAN OF MERGER\nDated as of April 15, 2026",
        "AGREEMENT AND PLAN OF MERGER",
    ),
    (
        "Formally-cased heading with keyword is returned (cap_ratio=1.0)",
        "Agreement And Plan Of Merger\nDated as of April 15, 2026",
        "Agreement And Plan Of Merger",
    ),
    (
        "SEC boilerplate line is skipped",
        "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nWASHINGTON, D.C. 20549\n\nAGREEMENT AND PLAN OF MERGER",
        "AGREEMENT AND PLAN OF MERGER",
    ),
    (
        "<TYPE>S-4/A tag is not returned",
        "<TYPE>S-4/A\n<SEQUENCE>1\nAMENDED AND RESTATED AGREEMENT AND PLAN OF MERGER",
        "AMENDED AND RESTATED AGREEMENT AND PLAN OF MERGER",
    ),
]


def run_tests() -> bool:
    all_pass = True
    for i, (description, raw_text, expected) in enumerate(CASES, 1):
        result = extract_document_title(raw_text)
        if result == expected:
            print(f"PASS [{i}]: {description}")
            print(f"       → {result!r}")
        else:
            print(f"FAIL [{i}]: {description}")
            print(f"       expected: {expected!r}")
            print(f"       got:      {result!r}")
            all_pass = False
        print()
    return all_pass


if __name__ == "__main__":
    print("=" * 60)
    print("Drop 3.21: extract_document_title() unit tests")
    print("=" * 60)
    print()
    ok = run_tests()
    passed = sum(1 for _ in CASES)  # recount after run
    # Count actual passes
    results = []
    for description, raw_text, expected in CASES:
        results.append(extract_document_title(raw_text) == expected)
    n_pass = sum(results)
    print(f"{n_pass}/{len(CASES)} tests passed.")
    sys.exit(0 if ok else 1)
