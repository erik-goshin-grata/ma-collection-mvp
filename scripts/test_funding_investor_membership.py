#!/usr/bin/env python3
"""Funding HC 0.8 — investors[] is membership in this financing, not appearance
in the source.

WHAT WENT WRONG

A fresh acceptance case (NovaGo Therapeutics: a Series B "complemented by"
non-dilutive funding from two named foundations) showed the prompt correctly
explaining in `notes` that the two foundations were not equity investors and
were separate from the round -- and then placing them in structured
`investors[]` anyway. The explanation and the structured answer disagreed,
and only the structured answer reaches `staging_investor` and canonical.

WHAT CHANGED

§4 INVESTORS gained a MEMBERSHIP block (prompts/funding_hc_extraction.md 0.7
-> 0.8, documentation/instruction only -- no response-schema change, no
per-field extraction rule touched): grants, non-dilutive funding, awards,
subsidies, philanthropic support and research funding are never investment
regardless of naming; language separating a support source from the round
("complemented by", "in addition to", "separately", "alongside") states that
it is NOT part of the round; unnamed groups ("existing and new investors")
do not license a synthetic investors[] entry. A named VENTURE_DEBT lender
remains a valid member -- the rule narrows who counts as a participant, it
does not remove lenders from that group.

This is a prompt-contract fix, not a code fix: stages/funding_hc_extract.py
already writes exactly what investors[] contains, with no code-level
filtering of any kind. There is therefore no live-model behavioral test here
(no API access) -- what this file verifies is (1) the delivered §4 text
actually carries the fix, so a future edit cannot silently weaken it back to
ambiguous, and (2) the write path faithfully persists a correctly-shaped
NovaGo-style response with no addition, loss, or synthesis of its own.

Run from project root:
    python scripts/test_funding_investor_membership.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
from prompts.base import load_prompt_file
import stages.funding_hc_extract as fhc

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t)


# ---------------------------------------------------------------------------
# 1. The delivered contract
# ---------------------------------------------------------------------------

def test_prompt() -> None:
    print("\nThe instruction reaches the model:")
    flat = _norm(load_prompt_file("funding_hc_extraction")["system"])
    check("membership is scoped to the profiled financing",
          "PARTICIPATING IN THIS FINANCING, NOT MERELY NAMED NEARBY" in flat, True)
    check("grants/non-dilutive/awards/subsidies/philanthropic/research excluded",
          "Grants, non-dilutive funding, awards, subsidies, philanthropic support, or"
          in flat, True)
    check("separation language (\"complemented by\" etc.) excludes the named party",
          '"complemented by," "in addition to," "separately,"' in flat, True)
    check("unnamed groups do not license a synthetic record",
          "Unnamed groups" in flat and "do not license a synthetic investor" in flat, True)
    check("a named VENTURE_DEBT lender remains valid",
          "still a valid investors[] member" in flat, True)
    check("prompt version is 0.8", fhc._VERSION, "0.8")

    print("\nExisting per-field extraction discipline is untouched:")
    check("pct_acquired anti-inference rules still delivered",
          "Do NOT infer a percentage from" in flat, True)
    check("lender investor_type still in the vocabulary",
          "lender — debt provider (for VENTURE_DEBT)" in flat, True)


# ---------------------------------------------------------------------------
# 2. Write path: a correctly-shaped NovaGo-style response persists faithfully
# ---------------------------------------------------------------------------

def _novago_txn():
    return {
        "company": {"name": "NovaGo Therapeutics", "domain": None, "ticker": None,
                     "description": None},
        # The two named foundations are deliberately absent -- this is the
        # model behavior the corrected prompt asks for. The unnamed
        # "existing and new investors" group is also absent, on purpose.
        "investors": [
            {"name": "Neurimmune", "domain": None, "investor_type": "vc_firm",
             "is_lead": True, "lead_investor_rank": 1,
             "investment_amount": None, "investment_currency": None,
             "is_new_investor": None, "is_existing_investor": None},
            {"name": "Pureos Bioventures", "domain": None, "investor_type": "vc_firm",
             "is_lead": True, "lead_investor_rank": 1,
             "investment_amount": None, "investment_currency": None,
             "is_new_investor": None, "is_existing_investor": None},
        ],
        "round": {"label": "Series B", "size": 30000000, "currency": "USD",
                   "pre_money_valuation": None, "post_money_valuation": None,
                   "valuation_currency": None, "facility_size": None,
                   "total_raised_to_date": None, "is_extension_round": False,
                   "round_price_direction": None, "is_bridge_round": False},
        "dates": {"announced_date": "2026-08-30", "announced_date_precision": "exact",
                   "closed_date": "2026-08-30", "closed_date_precision": "exact"},
        "financials_disclosure_status": "UNKNOWN",
        "transaction_terms_disclosure_status": "DISCLOSED",
        "consideration_type": "equity",
        "pct_acquired": None,
        "model_confidence": "HIGH",
        "notes": "Swiss Paraplegic Foundation and Wings for Life provide non-dilutive "
                 "funding explicitly described as complementing, not part of, the "
                 "Series B -- not investors[] members.",
    }


def _venture_debt_txn():
    return {
        "company": {"name": "TechStartup", "domain": None, "ticker": None,
                     "description": None},
        "investors": [
            {"name": "Silicon Valley Bank", "domain": None, "investor_type": "lender",
             "is_lead": True, "lead_investor_rank": 1,
             "investment_amount": 30000000, "investment_currency": "USD",
             "is_new_investor": None, "is_existing_investor": None},
        ],
        "round": {"label": None, "size": None, "currency": None,
                   "pre_money_valuation": None, "post_money_valuation": None,
                   "valuation_currency": None, "facility_size": 30000000,
                   "total_raised_to_date": None, "is_extension_round": False,
                   "round_price_direction": None, "is_bridge_round": False},
        "dates": {"announced_date": "2026-05-10", "announced_date_precision": "exact",
                   "closed_date": "2026-05-10", "closed_date_precision": "exact"},
        "financials_disclosure_status": "DISCLOSED",
        "transaction_terms_disclosure_status": "DISCLOSED",
        "consideration_type": "debt",
        "pct_acquired": None,
        "model_confidence": "HIGH",
        "notes": "facility_size populated; round.size null (debt facility).",
    }


def _seed_classified_row(conn, *, v2_event_type: str) -> int:
    conn.execute(
        "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
        " clean_text, source_status, fetched_at) VALUES"
        " ('PR_NEWSWIRE','T2','https://e.test/funding-membership','t','2026-08-30',"
        " 'body','RELEVANT','2026-08-30T00:00:00Z')"
    )
    source_raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO staging_extraction (source_raw_id, status, deal_type, v2_event_type,"
        " event_history_type, event_type, dt_prompt_version)"
        " VALUES (?, 'CLASSIFIED', ?, ?, 'ANNOUNCED', 'ANNOUNCEMENT',"
        " 'deal_type_classifier:test')",
        (source_raw_id, v2_event_type, v2_event_type),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _run_funding_hc_with_mocked_response(conn, txn) -> None:
    original_call_prompt = fhc.call_prompt
    original_sleep = fhc._SLEEP
    fhc._SLEEP = 0
    fhc.call_prompt = lambda **_kwargs: {"transactions": [txn]}
    try:
        fhc.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"),
                run_id="test_funding_investor_membership")
    finally:
        fhc.call_prompt = original_call_prompt
        fhc._SLEEP = original_sleep


def test_novago_write_path() -> None:
    print("\nNovaGo: two named lead investors persist; no foundations, no synthetic entry:")
    db_path = os.path.join(tempfile.mkdtemp(), "novago.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        eid = _seed_classified_row(conn, v2_event_type="VC_ROUND")
        _run_funding_hc_with_mocked_response(conn, _novago_txn())

        rows = conn.execute(
            "SELECT name, investor_type, is_lead FROM staging_investor"
            " WHERE extraction_id=? ORDER BY name", (eid,)
        ).fetchall()
        names = [r["name"] for r in rows]

        check("exactly two investor rows written", len(rows), 2)
        check("Neurimmune present", "Neurimmune" in names, True)
        check("Pureos Bioventures present", "Pureos Bioventures" in names, True)
        check("Swiss Paraplegic Foundation absent", "Swiss Paraplegic Foundation" in names, False)
        check("Wings for Life absent", "Wings for Life" in names, False)
        check("no synthetic entry for the unnamed group",
              all(n in ("Neurimmune", "Pureos Bioventures") for n in names), True)
        check("both leads recorded as lead (co-leads, no invented ranking)",
              all(r["is_lead"] == 1 for r in rows), True)

        status = conn.execute(
            "SELECT status FROM staging_extraction WHERE extraction_id=?", (eid,)
        ).fetchone()["status"]
        check("extraction status HC_EXTRACTED", status, "HC_EXTRACTED")
    finally:
        conn.close()


def test_venture_debt_unaffected() -> None:
    print("\nVENTURE_DEBT: a named lender in the profiled facility is still a valid member:")
    db_path = os.path.join(tempfile.mkdtemp(), "vdebt.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        eid = _seed_classified_row(conn, v2_event_type="VENTURE_DEBT")
        _run_funding_hc_with_mocked_response(conn, _venture_debt_txn())

        rows = conn.execute(
            "SELECT name, investor_type FROM staging_investor WHERE extraction_id=?",
            (eid,),
        ).fetchall()
        check("one lender row written", len(rows), 1)
        check("Silicon Valley Bank present as lender",
              (rows[0]["name"], rows[0]["investor_type"]) if rows else None,
              ("Silicon Valley Bank", "lender"))
    finally:
        conn.close()


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_novago_write_path()
    test_venture_debt_unaffected()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — investors[] is membership in the profiled financing, not appearance "
          f"in the source; a named VENTURE_DEBT lender is unaffected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
