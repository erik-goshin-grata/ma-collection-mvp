"""
Validation script for Drop 3.20a agreement extraction prompts.

Five tests, one per section-specific prompt:
  1. agreement_recitals   — party identification + Merger Sub demotion
  2. agreement_consideration — cash per share and CVR components
  3. agreement_capitalization — multi-class share counts
  4. agreement_termination — termination fees + go-shop
  5. agreement_conditions — MAC clause, shareholder vote, conditions summary

Each test constructs a synthetic section excerpt, calls the prompt directly
via the configured LLM provider, and validates the response shape and key field values.

Key assertions:
  - Recitals: Merger Sub is identified separately from parent acquirer
  - Capitalization: two common stock classes + options produce three rows

Skipped if the selected provider API key is not set. Estimated API cost depends
on the configured provider and model mapping.

Usage:
    python scripts/test_agreement_extraction.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from config import get_config
    from prompts.base import call_prompt, load_prompt_file, parse_json_response
    import sqlite3
    _IMPORTS_OK = True
except Exception as _e:
    _IMPORTS_OK = False
    _IMPORT_ERROR = str(_e)


# ---------------------------------------------------------------------------
# Synthetic section excerpts
# ---------------------------------------------------------------------------

_RECITALS_TEXT = """
AGREEMENT AND PLAN OF MERGER

dated as of March 15, 2026

among

HORIZON CAPITAL PARTNERS LP, a Delaware limited partnership ("Parent"),

HCP MERGER SUBSIDIARY INC., a Delaware corporation and a wholly-owned subsidiary
of Parent formed solely for the purpose of effecting the Merger ("Merger Sub"),

and

PINNACLE FINANCIAL SERVICES CORPORATION, a Tennessee corporation (the "Company").

WHEREAS, the Board of Directors of the Company has determined it is in the best
interests of the Company and its stockholders to consummate the business combination
provided herein;

WHEREAS, it is intended that Merger Sub shall merge with and into the Company (the
"Merger"), with the Company surviving the Merger as a wholly-owned subsidiary of Parent.
"""

_CONSIDERATION_TEXT = """
ARTICLE III
MERGER CONSIDERATION

Section 3.1 Conversion of Company Common Stock. At the Effective Time, by virtue
of the Merger and without any action on the part of any holder thereof, each share
of Company Common Stock issued and outstanding immediately prior to the Effective Time
(other than Excluded Shares and Dissenting Shares) shall be converted into the right
to receive (i) $38.00 in cash, without interest (the "Cash Consideration"), plus
(ii) one (1) Contingent Value Right (a "CVR") representing the right to receive up
to $3.50 per share in cash upon the achievement of the Post-Closing Revenue Milestone
(as defined in the CVR Agreement) within 24 months following the Closing Date.

The aggregate per share consideration, assuming full CVR payment, would be $41.50.
"""

_CAPITALIZATION_TEXT = """
Section 4.2 Capitalization.

(a) As of March 12, 2026, the authorized capital stock of the Company consists of:
(i) 400,000,000 shares of Class A Common Stock, par value $0.01 per share, of which
189,450,000 shares were issued and outstanding; (ii) 100,000,000 shares of Class B
Common Stock, par value $0.01 per share (ten votes per share), of which 42,100,000
shares were issued and outstanding; and (iii) 50,000,000 shares of Preferred Stock,
par value $0.001 per share, none of which are issued and outstanding.

(b) As of March 12, 2026, (i) options to purchase 8,320,000 shares of Class A Common
Stock were outstanding, with a weighted average exercise price of $22.45 per share;
(ii) 14,600,000 restricted stock units representing the right to receive shares of
Class A Common Stock upon vesting were outstanding.
"""

_TERMINATION_TEXT = """
Section 8.3 Termination Fee.

(a) Go-Shop Period. During the period beginning on the date of this Agreement and
ending at 11:59 p.m. Eastern time on the date that is 30 days after the date of this
Agreement (the "Go-Shop Period"), the Company and its subsidiaries may initiate and
solicit Acquisition Proposals.

(b) Company Termination Fee. In connection with a termination by Parent pursuant to
Section 8.1(d)(i) in the context of a Superior Proposal identified during the Go-Shop
Period, the Company shall pay Parent a fee of $18,000,000 (the "Go-Shop Fee"). For
all other terminations under Section 8.1(d)(i) or Section 8.1(d)(ii), the Company
shall pay Parent a fee equal to $52,000,000 (the "Company Termination Fee"), which
represents approximately 2.8% of the implied equity value.

(c) Parent Termination Fee. In the event this Agreement is terminated by the Company
pursuant to Section 8.1(e) (Failure to Close Due to Financing), Parent shall pay
the Company $104,000,000 (the "Parent Termination Fee") within three Business Days.
"""

_CONDITIONS_TEXT = """
ARTICLE VI
CONDITIONS TO CLOSING

Section 6.1 Conditions to Each Party's Obligations. The obligations of each party
to effect the Merger are subject to satisfaction or waiver of:

(a) Stockholder Approval. The Company Stockholder Approval shall have been obtained
(the affirmative vote of the holders of a majority of the outstanding shares of
Company Common Stock and Company Class B Common Stock, voting together as a single class).

(b) Regulatory Approval. The waiting period applicable under the HSR Act shall have
expired or been terminated, and any required approvals from CFIUS shall have been
obtained.

(c) No Injunction. No governmental entity shall have issued any final, non-appealable
order restraining, enjoining, or otherwise prohibiting the consummation of the Merger.

Section 6.2 Conditions to Parent's and Merger Sub's Obligations.

(d) Accuracy of Representations. The representations and warranties of the Company
shall be true and correct, except where a failure to be true and correct would not
reasonably be expected to have a Material Adverse Effect on the Company.

(e) No Material Adverse Effect. No Material Adverse Effect shall have occurred with
respect to the Company since the date of this Agreement and be continuing.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_conn() -> sqlite3.Connection:
    """Minimal in-memory DB with extraction_failure_log for call_prompt logging."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE extraction_failure_log (
            failure_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_raw_id INTEGER, extraction_id INTEGER,
            stage TEXT, failure_type TEXT, raw_response TEXT,
            error_message TEXT, prompt_version TEXT, run_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE prompt_version (
            prompt_name TEXT, version TEXT,
            prompt_text_hash TEXT, first_used_at TEXT,
            PRIMARY KEY (prompt_name, version)
        )
    """)
    return conn


def _run_prompt(prompt_name: str, section_text: str, cfg, conn) -> dict:
    version = "0.1"
    full_version = f"{prompt_name}:{version}"
    prompt = load_prompt_file(prompt_name)
    user = prompt["user_template"].format(
        section_text=section_text,
        prompt_version=full_version,
    )
    return call_prompt(
        prompt_name=prompt_name,
        prompt_version=full_version,
        user_prompt=user,
        system_prompt=prompt["system"],
        model="opus",
        temperature=0.0,
        max_tokens=2048,
        cfg=cfg,
        conn=conn,
        run_id="test_agreement_extraction",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_recitals(cfg, conn) -> bool:
    print("=" * 60)
    print("Test 1: agreement_recitals — Merger Sub demotion")
    print("=" * 60)
    result = _run_prompt("agreement_recitals", _RECITALS_TEXT, cfg, conn)
    print(json.dumps(result, indent=2))
    print()

    ok = True
    parent = result.get("parent_acquirer_name", "")
    sub = result.get("merger_sub_name") or ""
    target = result.get("target_name", "")
    structure = result.get("merger_structure", "")

    if "Merger Sub" in parent or "Merger Subsidiary" in parent:
        print(f"FAIL: parent_acquirer_name contains Merger Sub name: {parent!r}")
        ok = False
    if "Horizon" not in parent and "Horizon Capital" not in parent:
        print(f"FAIL: parent_acquirer_name should contain 'Horizon Capital': {parent!r}")
        ok = False
    if not sub or "Merger" not in sub and "Subsidiary" not in sub:
        print(f"FAIL: merger_sub_name should contain 'Merger Sub' / 'Subsidiary': {sub!r}")
        ok = False
    if "Pinnacle" not in target:
        print(f"FAIL: target_name should contain 'Pinnacle': {target!r}")
        ok = False
    if structure not in ("REVERSE_TRIANGULAR", "TENDER_OFFER"):
        print(f"FAIL: merger_structure expected REVERSE_TRIANGULAR, got {structure!r}")
        ok = False

    if ok:
        print("PASS: Merger Sub correctly separated from parent acquirer.")
    return ok


def test_consideration(cfg, conn) -> bool:
    print("=" * 60)
    print("Test 2: agreement_consideration — cash + CVR")
    print("=" * 60)
    result = _run_prompt("agreement_consideration", _CONSIDERATION_TEXT, cfg, conn)
    print(json.dumps(result, indent=2))
    print()

    components = result.get("consideration_components", [])
    forms = [c.get("form") for c in components]
    ok = True

    if "CASH" not in forms:
        print(f"FAIL: CASH component not found in {forms}")
        ok = False
    if "CVR" not in forms:
        print(f"FAIL: CVR component not found in {forms}")
        ok = False

    cash_comps = [c for c in components if c.get("form") == "CASH"]
    if cash_comps and cash_comps[0].get("per_share_amount") != 38.0:
        print(f"FAIL: CASH per_share_amount expected 38.0, got {cash_comps[0].get('per_share_amount')}")
        ok = False

    if ok:
        print("PASS: CASH and CVR components identified with correct amounts.")
    return ok


def test_capitalization(cfg, conn) -> bool:
    print("=" * 60)
    print("Test 3: agreement_capitalization — dual-class common + options")
    print("=" * 60)
    result = _run_prompt("agreement_capitalization", _CAPITALIZATION_TEXT, cfg, conn)
    print(json.dumps(result, indent=2))
    print()

    securities = result.get("securities", [])
    types = [s.get("security_type") for s in securities]
    classes = [s.get("security_class") for s in securities]
    ok = True

    common_rows = [s for s in securities if s.get("security_type") == "COMMON_STOCK"]
    if len(common_rows) < 2:
        print(f"FAIL: expected >= 2 COMMON_STOCK rows (Class A + Class B), got {len(common_rows)}")
        ok = False
    else:
        class_labels = {s.get("security_class") for s in common_rows}
        if "Class A" not in class_labels or "Class B" not in class_labels:
            print(f"FAIL: expected Class A and Class B in common rows, got {class_labels}")
            ok = False

    opt_rows = [s for s in securities if s.get("security_type") == "OPTIONS"]
    if not opt_rows:
        print("FAIL: no OPTIONS row found")
        ok = False
    elif opt_rows[0].get("shares_outstanding") != 8320000:
        print(f"WARN: OPTIONS shares_outstanding expected 8320000, got {opt_rows[0].get('shares_outstanding')}")

    rsu_rows = [s for s in securities if s.get("security_type") == "RSU"]
    if not rsu_rows:
        print("FAIL: no RSU row found")
        ok = False

    if ok:
        print(f"PASS: {len(securities)} securities found with correct type/class breakdown.")
    return ok


def test_termination(cfg, conn) -> bool:
    print("=" * 60)
    print("Test 4: agreement_termination — fees + go-shop")
    print("=" * 60)
    result = _run_prompt("agreement_termination", _TERMINATION_TEXT, cfg, conn)
    print(json.dumps(result, indent=2))
    print()

    ok = True
    co_fee = result.get("target_termination_fee")
    pa_fee = result.get("acquirer_termination_fee")
    go_shop = result.get("has_go_shop")
    go_days = result.get("go_shop_period_days")

    if co_fee != 52000000:
        print(f"FAIL: target_termination_fee expected 52000000, got {co_fee}")
        ok = False
    if pa_fee != 104000000:
        print(f"FAIL: acquirer_termination_fee expected 104000000, got {pa_fee}")
        ok = False
    if not go_shop:
        print(f"FAIL: has_go_shop expected True, got {go_shop}")
        ok = False
    if go_days != 30:
        print(f"FAIL: go_shop_period_days expected 30, got {go_days}")
        ok = False

    if ok:
        print("PASS: termination fees and go-shop correctly extracted.")
    return ok


def test_conditions(cfg, conn) -> bool:
    print("=" * 60)
    print("Test 5: agreement_conditions — MAC clause + shareholder vote")
    print("=" * 60)
    result = _run_prompt("agreement_conditions", _CONDITIONS_TEXT, cfg, conn)
    print(json.dumps(result, indent=2))
    print()

    ok = True
    has_mac = result.get("has_mac_clause")
    req_vote = result.get("requires_target_shareholder_vote")
    threshold = result.get("target_vote_threshold")
    summary = result.get("closing_conditions_summary") or ""

    if not has_mac:
        print(f"FAIL: has_mac_clause expected True, got {has_mac}")
        ok = False
    if not req_vote:
        print(f"FAIL: requires_target_shareholder_vote expected True, got {req_vote}")
        ok = False
    if threshold not in ("MAJORITY_OUTSTANDING", "OTHER"):
        print(f"WARN: target_vote_threshold expected MAJORITY_OUTSTANDING, got {threshold!r}")
    if len(summary) < 20:
        print(f"FAIL: closing_conditions_summary too short: {summary!r}")
        ok = False

    if ok:
        print("PASS: MAC clause and shareholder vote correctly identified.")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _IMPORTS_OK:
        print(f"SKIP: import error — {_IMPORT_ERROR}")
        sys.exit(0)

    try:
        cfg = get_config()
    except Exception as exc:
        print(f"SKIP: could not load config: {exc}")
        sys.exit(0)

    if cfg.llm_provider == "anthropic" and not cfg.anthropic_api_key:
        print("SKIP: ANTHROPIC_API_KEY not set.")
        sys.exit(0)
    if cfg.llm_provider == "openai" and not cfg.openai_api_key:
        print("SKIP: OPENAI_API_KEY not set.")
        sys.exit(0)

    conn = _fake_conn()
    results = []

    for test_fn in [
        test_recitals,
        test_consideration,
        test_capitalization,
        test_termination,
        test_conditions,
    ]:
        try:
            passed = test_fn(cfg, conn)
        except Exception as exc:
            print(f"ERROR: {test_fn.__name__} raised: {exc}")
            import traceback
            traceback.print_exc()
            passed = False
        results.append(passed)
        print()

    passed_count = sum(results)
    print(f"{passed_count}/{len(results)} tests passed.")
    sys.exit(0 if all(results) else 1)
