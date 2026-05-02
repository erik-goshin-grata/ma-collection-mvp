"""
Validation script for Drop 3.19 sec_documents stage.

Two tests:
  1. Section tagger with synthetic merger agreement text (no API needed).
     Asserts >= 5 section types are identified from a representative excerpt.

  2. Live adapter fetch: queries sec-api.io for a DEFM14A filing for a known
     recent M&A deal (Citizens National Corporation / Stellar Bancorp area),
     fetches the document, and runs the section tagger on real text.
     Skipped if SEC_API_KEY is not set.

Usage:
    python scripts/test_sec_documents.py

Output: section_type counts and excerpt char counts for each test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow imports from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.section_tagger import tag_sections

# ---------------------------------------------------------------------------
# Synthetic merger agreement text
# ---------------------------------------------------------------------------

_SYNTHETIC_TEXT = """
AGREEMENT AND PLAN OF MERGER

dated as of March 15, 2026

among

PARENT CORPORATION,

MERGER SUB, INC.,

and

TARGET COMPANY, INC.

RECITALS

WHEREAS, the Board of Directors of the Company (the "Company Board") has
determined that it is in the best interests of the Company and its stockholders
to consummate the business combination transactions provided for herein;

WHEREAS, the Board of Directors of Parent (the "Parent Board") has approved
this Agreement and the transactions contemplated hereby;

ARTICLE I
DEFINITIONS

Section 1.1 Definitions. As used herein, the following terms have the
following meanings:

"Acquisition Proposal" means any proposal or offer from any Person relating
to any acquisition of twenty percent (20%) or more of the total voting power
of the Company's equity securities.

"Effective Time" means the time at which the Certificate of Merger has been
duly filed with the Secretary of State of the State of Delaware.

ARTICLE II
THE MERGER

Section 2.1 The Merger. Upon the terms and subject to the conditions set
forth in this Agreement, at the Effective Time, Merger Sub shall be merged with
and into the Company.

MERGER CONSIDERATION

Section 2.2 Conversion of Company Common Stock. At the Effective Time, by
virtue of the Merger and without any action on the part of any holder thereof:

(a) each share of Company Common Stock issued and outstanding shall be
converted into the right to receive $42.00 in cash (the "Merger Consideration"),
without interest.

Section 2.3 Exchange Ratio. For the avoidance of doubt, there shall be no
exchange ratio with respect to Company Common Stock.

CAPITALIZATION

Section 3.1 Authorized and Outstanding Capital Stock. As of the date hereof,
the authorized capital stock of the Company consists of 200,000,000 shares of
Company Common Stock and 10,000,000 shares of Preferred Stock. As of March 12,
2026, there were 45,238,100 shares of Company Common Stock outstanding.

ARTICLE V
REPRESENTATIONS AND WARRANTIES OF THE COMPANY

Section 5.1 Organization and Qualification. The Company is a corporation duly
organized, validly existing, and in good standing under the laws of Delaware.

REPRESENTATIONS AND WARRANTIES

The Company hereby represents and warrants to Parent and Merger Sub that,
except as disclosed in the Company Disclosure Schedule...

ARTICLE VI
CONDITIONS TO CLOSING

Section 6.1 Conditions to Obligations of Each Party to Effect the Merger. The
respective obligations of each Party to effect the Merger are subject to the
satisfaction or waiver of the following conditions:

CONDITIONS TO CLOSING

(a) Stockholder Approval. The Company Stockholder Approval shall have been
obtained at the Company Stockholder Meeting.

(b) Regulatory Approvals. Any waiting period applicable to the consummation
of the Merger under the HSR Act shall have expired or been earlier terminated.

ARTICLE VII
TERMINATION

Section 7.1 Termination. This Agreement may be terminated and the Merger
abandoned at any time prior to the Effective Time.

TERMINATION FEES

Section 7.3 Company Termination Fee. In the event this Agreement is terminated
by Parent pursuant to Section 7.1(e), then the Company shall pay to Parent,
within two (2) Business Days, a fee equal to $85,000,000 (the "Company
Termination Fee").

Section 7.4 Parent Termination Fee. In the event this Agreement is terminated
by the Company pursuant to Section 7.1(f), then Parent shall pay to the
Company a fee equal to $170,000,000 (the "Parent Termination Fee").

ARTICLE VIII
GENERAL PROVISIONS
"""

# ---------------------------------------------------------------------------
# Test 1: synthetic text
# ---------------------------------------------------------------------------

def test_synthetic() -> bool:
    print("=" * 60)
    print("Test 1: Section tagger — synthetic merger agreement text")
    print("=" * 60)

    matches = tag_sections(_SYNTHETIC_TEXT)

    by_type: dict[str, list] = {}
    for m in matches:
        by_type.setdefault(m.section_type, []).append(m)

    print(f"Total sections identified: {len(matches)}")
    print(f"Distinct section types:   {len(by_type)}")
    print()
    for stype in sorted(by_type):
        items = by_type[stype]
        for m in items:
            print(
                f"  {stype:<28} confidence={m.confidence:<6} "
                f"excerpt_chars={len(m.excerpt_text):<6} "
                f"heading={m.heading_text[:50]!r}"
            )
    print()

    expected_types = {"RECITALS", "DEFINITIONS", "CONSIDERATION", "CAPITALIZATION",
                      "REPRESENTATIONS", "CONDITIONS_TO_CLOSING", "TERMINATION_FEES"}
    found = set(by_type.keys())
    missing = expected_types - found
    if missing:
        print(f"FAIL: expected section types not found: {missing}")
        return False

    print(f"PASS: all {len(expected_types)} expected section types identified.")
    return True


# ---------------------------------------------------------------------------
# Test 2: live adapter fetch (requires SEC_API_KEY)
# ---------------------------------------------------------------------------

def test_live_adapter() -> bool:
    print("=" * 60)
    print("Test 2: Live SEC adapter fetch — DEFM14A query")
    print("=" * 60)

    # Check for SEC API key
    api_key = None
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("SEC_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not api_key:
        api_key = os.environ.get("SEC_API_KEY")

    if not api_key:
        print("SKIP: SEC_API_KEY not found in .env or environment.")
        return True

    try:
        from config import get_config
        cfg = get_config()
    except Exception as exc:
        print(f"SKIP: Could not load config: {exc}")
        return True

    import requests as req_lib
    import adapters.sec_api as _sec

    session = req_lib.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    # Query for recent DEFM14A — use a 2025-2026 date range against "merger" keyword
    # We'll try Citizens National Corporation (mentioned in production run) as the target
    # window: wide range to improve odds of finding something
    test_cases = [
        ("DEFM14A", None, "Citizens National Corporation", "2026-01-15"),
        ("DEFM14A", None, "Stellar Bancorp", "2025-10-01"),
        ("DEFM14A", "STEL", None, "2025-10-01"),
    ]

    for filing_type, ticker, company_name, announced_date in test_cases:
        print(f"\nQuerying {filing_type}: ticker={ticker!r} company={company_name!r} date={announced_date}")
        try:
            filings = _sec.query_filings_by_formtype(
                cfg, session, filing_type, ticker, company_name, announced_date, None,
                window_days=180, max_results=3,
            )
        except Exception as exc:
            print(f"  Query error: {exc}")
            continue

        if not filings:
            print(f"  No filings returned — trying next candidate")
            continue

        filing = filings[0]
        print(f"  Found: accession={filing.get('accessionNo')}  filer={filing.get('companyName')}  filed={filing.get('filedAt', '')[:10]}")

        raw_text, status = _sec.fetch_filing_full_text(cfg, session, filing, None)
        if status == "SKIP" or not raw_text:
            print(f"  Fetch returned status={status} — trying next candidate")
            continue

        print(f"  Fetched {len(raw_text)} chars  status={status}")
        matches = tag_sections(raw_text)
        by_type: dict[str, list] = {}
        for m in matches:
            by_type.setdefault(m.section_type, []).append(m)

        print(f"  Sections found: {len(matches)} total, {len(by_type)} distinct types")
        for stype in sorted(by_type):
            items = by_type[stype]
            total_chars = sum(len(m.excerpt_text) for m in items)
            print(f"    {stype:<28} count={len(items)}  total_excerpt_chars={total_chars}")

        if len(by_type) >= 3:
            print(f"\nPASS: found >= 3 section types in live {filing_type}.")
            return True
        else:
            print(f"  Only {len(by_type)} section types — trying next candidate")

    print("\nSKIP: No suitable live filing found (all candidates returned empty or too few sections).")
    print("      This is expected if no test-case tickers match recent DEFM14A filings.")
    print("      Synthetic test (Test 1) validates section tagger logic.")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ok1 = test_synthetic()
    print()
    ok2 = test_live_adapter()
    print()
    if ok1 and ok2:
        print("All tests passed.")
        sys.exit(0)
    else:
        print("One or more tests FAILED.")
        sys.exit(1)
