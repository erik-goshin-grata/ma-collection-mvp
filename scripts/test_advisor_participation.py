#!/usr/bin/env python3
"""Advisor participation — specialty and advised participant survive to persistence.

An advisor participation is four separate facts: who advised, in what specialty, which
specific participant they advised, and on which side. The pre-0.11 shape could express two
and compressed both.

`OTHER` was lossy BY WRITTEN INSTRUCTION: the prompt's own definition read "OTHER covers
fairness opinion providers, proxy solicitors, info agents, and accounting/tax advisors", so
four named specialties collapsed into one bucket while the evidence sat in the source text.
`BOTH` was worse -- one advisor serving two participants is two participations, and a single
row cannot say which two.

These run the REAL Stage 7 with only `call_prompt` intercepted, so the assertions are about
what the production stage persists, not about a reimplementation of it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
import stages.low_confidence_extract as lc
import stages.summarize as summarize
from prompts.base import load_prompt_file


def _lc_response(advisors: list[dict]) -> dict:
    return {
        "advisors": advisors,
        "consideration_components": [],
        "flags": {},
        "deal_attitude": None,
        "approach_type": None,
        "competing_bid": False,
        "regulatory_approvals_required": False,
        "go_shop": {"has_go_shop": False, "go_shop_period_days": None},
        "termination_fees": {
            "target_fee_amount": None,
            "target_fee_percentage": None,
            "acquirer_fee_amount": None,
            "acquirer_fee_percentage": None,
        },
        "model_confidence": "HIGH",
        "notes": None,
    }


def _run_stage7(advisors: list[dict]) -> list[sqlite3.Row]:
    """Seed one HC_EXTRACTED row, run real Stage 7, return the advisor rows written."""
    db_path = os.path.join(tempfile.mkdtemp(), "adv.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u-adv','t-adv','2026-08-18','body','RELEVANT',?)",
            (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO staging_extraction
                   (source_raw_id, status, deal_type, v2_event_type, event_type,
                    event_history_type, target_status, target_name, acquirer_name,
                    dt_prompt_version, transaction_cluster_id)
               VALUES (?, 'HC_EXTRACTED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCEMENT',
                       'ANNOUNCED', 'PRIVATE', 'Beta Industries', 'Acme Corp',
                       'deal_type_classifier:test', 'tc_adv')""",
            (srid,))
        conn.commit()

        real_call, real_sleep = lc.call_prompt, lc._SLEEP
        lc.call_prompt, lc._SLEEP = (lambda **_k: _lc_response(advisors)), 0
        try:
            lc.run(conn, SimpleNamespace(log_level="ERROR"), "test_advisor_participation")
        finally:
            lc.call_prompt, lc._SLEEP = real_call, real_sleep

        return conn.execute(
            "SELECT name, type, advised_party, specialty, advised_party_name, advised_side"
            " FROM advisor ORDER BY name").fetchall()
    finally:
        conn.close()


def _eq(failures: list[str], name: str, got, want) -> None:
    if got != want:
        failures.append(f"{name}: expected {want!r}, got {got!r}")


# ---------------------------------------------------------------------------
# 1. Specialty survives at its own granularity
# ---------------------------------------------------------------------------

_SPECIALTY_CASES = [
    # (label, emitted specialty, expected stored specialty, expected legacy type)
    ("financial_advisory", "financial_advisory", "financial_advisory", "FINANCIAL"),
    ("legal",              "legal",              "legal",              "LEGAL"),
    # The four the old contract named in its own OTHER definition. Each must now keep its
    # identity in `specialty` while the legacy column still says OTHER.
    ("accounting",         "accounting",         "accounting",         "OTHER"),
    ("tax",                "tax",                "tax",                "OTHER"),
    ("fairness_opinion",   "fairness_opinion",   "fairness_opinion",   "OTHER"),
    ("proxy_solicitation", "proxy_solicitation", "proxy_solicitation", "OTHER"),
    ("information_agent",  "information_agent",  "information_agent",  "OTHER"),
    ("regulatory",         "regulatory",         "regulatory",         "OTHER"),
    # Product addition at 0.11.
    ("communications",     "communications",     "communications",     "OTHER"),
]


def _test_specialty_survives(failures: list[str]) -> None:
    for label, emitted, want_spec, want_type in _SPECIALTY_CASES:
        rows = _run_stage7([{"name": "Firm LLP", "advisor_specialty": emitted,
                             "advised_party_name": "Acme Corp", "advised_side": "BUY_SIDE"}])
        if len(rows) != 1:
            failures.append(f"specialty/{label}: expected 1 advisor row, got {len(rows)}")
            continue
        _eq(failures, f"specialty/{label}.specialty", rows[0]["specialty"], want_spec)
        _eq(failures, f"specialty/{label}.legacy_type", rows[0]["type"], want_type)


def _test_specialties_not_collapsed(failures: list[str]) -> None:
    """accounting, tax and fairness_opinion must stay three facts, not one bucket."""
    rows = _run_stage7([
        {"name": "A Accountants", "advisor_specialty": "accounting",
         "advised_party_name": "Acme Corp", "advised_side": None},
        {"name": "B Tax", "advisor_specialty": "tax",
         "advised_party_name": "Acme Corp", "advised_side": None},
        {"name": "C Fairness", "advisor_specialty": "fairness_opinion",
         "advised_party_name": "Beta Industries", "advised_side": None},
    ])
    if len(rows) != 3:
        failures.append(f"not_collapsed: expected 3 advisor rows, got {len(rows)}")
        return
    got = {r["name"]: r["specialty"] for r in rows}
    _eq(failures, "not_collapsed", got,
        {"A Accountants": "accounting", "B Tax": "tax", "C Fairness": "fairness_opinion"})
    # All three project to the same legacy value -- which is exactly why the new column exists.
    if {r["type"] for r in rows} != {"OTHER"}:
        failures.append("not_collapsed: legacy type projection changed unexpectedly")


# ---------------------------------------------------------------------------
# 2. Advised participant identity, side, and the absence of BOTH
# ---------------------------------------------------------------------------

def _test_two_participants_two_entries(failures: list[str]) -> None:
    """One advisor serving two identified participants is two rows, never one 'BOTH'."""
    rows = _run_stage7([
        {"name": "Dual Advisors", "advisor_specialty": "financial_advisory",
         "advised_party_name": "Acme Corp", "advised_side": "BUY_SIDE"},
        {"name": "Dual Advisors", "advisor_specialty": "financial_advisory",
         "advised_party_name": "Beta Industries", "advised_side": "SELL_SIDE"},
    ])
    _eq(failures, "two_entries.count", len(rows), 2)
    _eq(failures, "two_entries.parties",
        sorted(r["advised_party_name"] for r in rows), ["Acme Corp", "Beta Industries"])
    _eq(failures, "two_entries.sides",
        sorted(r["advised_side"] for r in rows), ["BUY_SIDE", "SELL_SIDE"])
    if any(r["advised_party"] == "BOTH" for r in rows):
        failures.append("two_entries: a row was persisted with advised_party = BOTH")


def _test_side_only_no_fabricated_participant(failures: list[str]) -> None:
    """Side established, participant not: keep the side, invent no name."""
    rows = _run_stage7([{"name": "Sell Side Co", "advisor_specialty": "financial_advisory",
                         "advised_party_name": None, "advised_side": "SELL_SIDE"}])
    if not rows:
        failures.append("side_only: the advisor participation was dropped entirely")
        return
    _eq(failures, "side_only.side", rows[0]["advised_side"], "SELL_SIDE")
    _eq(failures, "side_only.party_name", rows[0]["advised_party_name"], None)
    # The row seeds target_name = Beta Industries. A SELL_SIDE advisor is not evidence that
    # Beta is the client, and the stage must not reach for it.
    if rows[0]["advised_party_name"] is not None:
        failures.append("side_only: a participant name was manufactured from side evidence")


def _test_participant_only_no_fabricated_side(failures: list[str]) -> None:
    """Participant named, side not established: keep the name, invent no side."""
    rows = _run_stage7([{"name": "Named Client Co", "advisor_specialty": "legal",
                         "advised_party_name": "Acme Corp", "advised_side": None}])
    if not rows:
        failures.append("party_only: the advisor participation was dropped entirely")
        return
    _eq(failures, "party_only.party_name", rows[0]["advised_party_name"], "Acme Corp")
    _eq(failures, "party_only.side", rows[0]["advised_side"], None)
    # Acme Corp is the seeded acquirer, so BUY_SIDE is inferable -- and must not be inferred.
    if rows[0]["advised_side"] is not None:
        failures.append("party_only: a side was manufactured from the participant name")


# ---------------------------------------------------------------------------
# 3. Nothing is silently dropped, and legacy rows stay readable
# ---------------------------------------------------------------------------

def _test_unsupported_specialty_keeps_the_advisor(failures: list[str]) -> None:
    """An unsupported specialty drops the specialty, never the participation.

    Before 0.11 an unrecognized `type` skipped the whole entry with a log line, so a newly
    supported specialty arriving from a newer prompt would have deleted the advisor -- name
    included. The name is the irreducible fact.
    """
    rows = _run_stage7([{"name": "Restructuring Advisors", "advisor_specialty": "restructuring",
                         "advised_party_name": "Acme Corp", "advised_side": "BUY_SIDE"}])
    _eq(failures, "unsupported.count", len(rows), 1)
    if rows:
        _eq(failures, "unsupported.name", rows[0]["name"], "Restructuring Advisors")
        _eq(failures, "unsupported.specialty", rows[0]["specialty"], None)
        _eq(failures, "unsupported.party_name", rows[0]["advised_party_name"], "Acme Corp")


def _test_legacy_response_still_accepted(failures: list[str]) -> None:
    """A pre-0.11 response shape is still stored, including BOTH and OTHER."""
    rows = _run_stage7([
        {"name": "Legacy Fin", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
        {"name": "Legacy Other", "type": "OTHER", "advised_party": "BOTH"},
    ])
    _eq(failures, "legacy.count", len(rows), 2)
    by = {r["name"]: r for r in rows}
    if len(rows) != 2:
        return
    if "Legacy Fin" in by:
        _eq(failures, "legacy.fin.type", by["Legacy Fin"]["type"], "FINANCIAL")
        _eq(failures, "legacy.fin.party", by["Legacy Fin"]["advised_party"], "ACQUIRER")
    if "Legacy Other" in by:
        # OTHER is evidence that a non-financial, non-legal specialty was observed. It must
        # survive as itself -- not be rewritten to NULL because the new column is empty.
        _eq(failures, "legacy.other.type", by["Legacy Other"]["type"], "OTHER")
        _eq(failures, "legacy.other.specialty", by["Legacy Other"]["specialty"], None)
        _eq(failures, "legacy.other.party", by["Legacy Other"]["advised_party"], "BOTH")


def _test_historical_rows_read_unchanged(failures: list[str]) -> None:
    """A row written before 011 -- new columns absent -- still reads and still summarizes."""
    db_path = os.path.join(tempfile.mkdtemp(), "hist.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u-h','t-h','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, transaction_cluster_id)"
            " VALUES (?, 'LC_EXTRACTED', 'tc_hist')", (srid,))
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Legacy shape only: the three 011 columns are left entirely unset.
        conn.execute("INSERT INTO advisor (extraction_id, name, type, advised_party)"
                     " VALUES (?, 'Old Bank', 'FINANCIAL', 'TARGET')", (eid,))
        conn.commit()
        row = conn.execute("SELECT specialty, advised_party_name, advised_side, type,"
                           " advised_party FROM advisor").fetchone()
        _eq(failures, "historical.specialty_is_null", row["specialty"], None)
        _eq(failures, "historical.type_preserved", row["type"], "FINANCIAL")
        _eq(failures, "historical.party_preserved", row["advised_party"], "TARGET")
        summary = summarize._build_advisors_summary(conn, "tc_hist")
        if not summary or "Old Bank" not in summary or "Target" not in summary:
            failures.append(f"historical: legacy row no longer summarizes — got {summary!r}")
    finally:
        conn.close()


def _test_summary_prefers_stated_client(failures: list[str]) -> None:
    """The summary names the actual client when the row carries one."""
    db_path = os.path.join(tempfile.mkdtemp(), "sum.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u-s','t-s','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, transaction_cluster_id)"
            " VALUES (?, 'LC_EXTRACTED', 'tc_sum')", (srid,))
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO advisor (extraction_id, name, type, advised_party, specialty,"
            " advised_party_name, advised_side)"
            " VALUES (?, 'Goldman Sachs', 'FINANCIAL', 'UNKNOWN', 'financial_advisory',"
            " 'Acme Corp', 'BUY_SIDE')", (eid,))
        conn.commit()
        summary = summarize._build_advisors_summary(conn, "tc_sum")
        if not summary or "Acme Corp" not in summary:
            failures.append(f"summary: stated client not used — got {summary!r}")
        if summary and "Unknown" in summary:
            failures.append(f"summary: rendered the legacy UNKNOWN role — got {summary!r}")
    finally:
        conn.close()


def _test_delivered_contract(failures: list[str]) -> None:
    """The vocabulary must reach the model, not merely the parser.

    The stage tests below would all pass if the prompt silently stopped offering a
    specialty: the parser would still accept it, and no source would ever produce it. Only
    an assertion on load_prompt_file() output can see that, because everything outside the
    §4/§5 fences is documentation the model never receives.
    """
    system = load_prompt_file("low_confidence_extraction")["system"]
    for value in sorted(lc._VALID_ADVISOR_SPECIALTIES):
        if value not in system:
            failures.append(f"delivered contract: specialty {value!r} is accepted by the "
                            "stage but never offered to the model")
    for key in ("advisor_specialty", "advised_party_name", "advised_side"):
        if key not in system:
            failures.append(f"delivered contract: {key} is missing from the system prompt")
    # BOTH must not return as an advised-party value; one advisor serving two participants
    # is two entries. (The token appears elsewhere in unrelated consideration prose.)
    if '"advised_party"' in system:
        failures.append("delivered contract: the legacy advised_party role is being asked "
                        "for again — 0.11 replaced it with advised_party_name + advised_side")
    # The role was renamed LENDER -> FINANCING_PROVIDER; the rule this asserts is
    # unchanged, so it follows the wording rather than pinning the old name.
    if "FINANCING PROVIDER is NOT an advisor specialty" not in system:
        failures.append("delivered contract: the provider-is-not-a-specialty rule is missing")


def main() -> None:
    failures: list[str] = []
    _test_delivered_contract(failures)
    _test_specialty_survives(failures)
    _test_specialties_not_collapsed(failures)
    _test_two_participants_two_entries(failures)
    _test_side_only_no_fabricated_participant(failures)
    _test_participant_only_no_fabricated_side(failures)
    _test_unsupported_specialty_keeps_the_advisor(failures)
    _test_legacy_response_still_accepted(failures)
    _test_historical_rows_read_unchanged(failures)
    _test_summary_prefers_stated_client(failures)

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"PASS advisor participation  ({len(_SPECIALTY_CASES)} specialties + "
          "granularity + two-entries + side-only + party-only + unsupported + legacy + "
          "historical-read + summary)")


if __name__ == "__main__":
    main()
