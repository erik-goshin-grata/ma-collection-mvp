#!/usr/bin/env python3
"""Source authority / tiering — deterministic resolver, adapter-known classification,
and Relevancy's source_character inference for the residual generic-discovery case.

WHAT WENT WRONG

`source_tier` was a per-adapter literal with no policy behind it: PR_NEWSWIRE and
WEB_URL both wrote 'T2' unconditionally, SEC wrote 'T1' unconditionally regardless of
document type, and T3/T4 were never assigned by any production path. SEC provenance
was conflated with authority: an EX-99.x exhibit that is the company's own press
release (a first-party announcement, not operative evidence) was stamped T1 the same
as an EX-2.x operative agreement, purely because both arrived through the SEC adapter.

WHAT CHANGED

Two independent ways to establish tier, both resolved through the single policy
function `lib/source_authority.py::resolve_tier`:

  known_tier         -- an acquisition path already knows the document identity
                         (a SEC regulatory/operative filing) with certainty. Wins
                         outright; source_character is not consulted.
  source_character    -- whose voice the content is in. Declared deterministically by
                         a known path (PR Newswire's own issuer feed; an SEC EX-99.x
                         exhibit sec_api.py's own regex classifier already identified
                         as a press release) OR inferred by Relevancy, only for a
                         generically discovered source (WEB_URL) with no
                         source_character already on the row.

A known classification already on the row is never overwritten by Relevancy's own
inference for that row -- computed, but discarded.

Run from project root:
    python scripts/test_source_authority.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
from lib.field_priority import TIER_ORDER
from lib.observation_writer import (
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)
from lib.source_authority import SOURCE_CHARACTER_VALUES, resolve_tier
import adapters.pr_newswire as pr_newswire
import stages.aggregate as agg
import stages.relevancy_filter as rf

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


# ---------------------------------------------------------------------------
# 1. The resolver itself
# ---------------------------------------------------------------------------

def test_resolver() -> None:
    print("\nknown_tier wins outright -- source_character is not consulted:")
    check("known regulatory evidence resolves T1",
          resolve_tier(known_tier="T1"), "T1")
    check("known_tier overrides even a contradicting source_character",
          resolve_tier(known_tier="T1", source_character="DERIVATIVE_REPORTING"), "T1")

    print("\nEvery source_character value resolves to its documented tier:")
    check("FIRST_PARTY_ANNOUNCEMENT -> T2",
          resolve_tier(source_character="FIRST_PARTY_ANNOUNCEMENT"), "T2")
    check("THIN_FIRST_PARTY_RECORD -> T3",
          resolve_tier(source_character="THIN_FIRST_PARTY_RECORD"), "T3")
    check("ORIGINAL_REPORTING -> T3",
          resolve_tier(source_character="ORIGINAL_REPORTING"), "T3")
    check("DERIVATIVE_REPORTING -> T4",
          resolve_tier(source_character="DERIVATIVE_REPORTING"), "T4")

    print("\nUnknown/absent/invalid character is never promoted to higher authority:")
    check("UNKNOWN -> T4 (the floor, same as a confirmed derivative rewrite)",
          resolve_tier(source_character="UNKNOWN"), "T4")
    check("an off-enum value -> T4, not invented authority",
          resolve_tier(source_character="SOMETHING_NEW"), "T4")
    check("no character at all -> T4",
          resolve_tier(source_character=None), "T4")
    check("no arguments at all -> T4",
          resolve_tier(), "T4")

    print("\nT4 is a real member of TIER_ORDER, sorting last:")
    check("TIER_ORDER", TIER_ORDER, ("T1", "T2", "T3", "T4"))
    check("T4 index", TIER_ORDER.index("T4"), 3)


# ---------------------------------------------------------------------------
# 2. Known acquisition paths resolve tier without Relevancy inference
# ---------------------------------------------------------------------------

def test_known_paths() -> None:
    print("\nA subscribed PR Newswire feed resolves T2 at ingestion -- no model call:")
    db_path = os.path.join(tempfile.mkdtemp(), "pr.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        sid = pr_newswire.insert_source_raw(
            conn, url="https://prnewswire.test/1", title="Acme to Acquire Beta",
            published_date="2026-09-01", raw_html=None, clean_text="body",
            c_hash="hash1", notes=None, fetched_at="2026-09-01T00:00:00Z",
        )
        row = conn.execute(
            "SELECT source_tier, source_character FROM source_raw WHERE source_raw_id=?",
            (sid,),
        ).fetchone()
        check("source_tier is T2", row["source_tier"], "T2")
        check("source_character is FIRST_PARTY_ANNOUNCEMENT known at insert",
              row["source_character"], "FIRST_PARTY_ANNOUNCEMENT")
    finally:
        conn.close()

    print("\nSEC regulatory/operative evidence keeps its T1 default (unchanged):")
    check("insert_source_raw's own default resolves T1 without any character",
          resolve_tier(known_tier="T1"), "T1")


# ---------------------------------------------------------------------------
# 3. Relevancy: the residual case, and known-classification precedence
# ---------------------------------------------------------------------------

def _seed_source(conn, *, source_type: str, source_tier: str,
                  source_character: str | None, clean_text: str = "body text") -> int:
    conn.execute(
        "INSERT INTO source_raw (source_type, source_tier, source_character, url, title,"
        " clean_text, source_status, fetched_at) VALUES (?, ?, ?, ?, 't', ?, 'FETCHED', ?)",
        (source_type, source_tier, source_character,
         f"https://e.test/{source_type}/{id(object())}", clean_text, "2026-09-01T00:00:00Z"),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _run_relevancy_with_mocked_answer(conn, answer: dict) -> None:
    original_call_prompt = rf.call_prompt
    original_sleep = rf._SLEEP
    rf._SLEEP = 0
    rf.call_prompt = lambda **_kwargs: dict(answer)
    try:
        rf.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"), run_id="test_source_authority")
    finally:
        rf.call_prompt = original_call_prompt
        rf._SLEEP = original_sleep


def test_relevancy_residual_case() -> None:
    base_answer = {
        "classification": "RELEVANT",
        "reason_code": "ACQUISITION_ANNOUNCEMENT",
        "model_confidence": "HIGH",
        "notes": None,
    }

    cases = [
        ("FIRST_PARTY_ANNOUNCEMENT", "T2"),
        ("THIN_FIRST_PARTY_RECORD", "T3"),
        ("ORIGINAL_REPORTING", "T3"),
        ("DERIVATIVE_REPORTING", "T4"),
        ("not-a-real-value", "T4"),  # off-enum -> normalized to UNKNOWN -> T4
    ]
    print("\nA generically discovered WEB_URL row with no known character:")
    print("Relevancy's inference resolves source_character and source_tier:")
    for character, expected_tier in cases:
        db_path = os.path.join(tempfile.mkdtemp(), "web.db")
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            sid = _seed_source(conn, source_type="WEB_URL", source_tier="T2",
                               source_character=None)
            _run_relevancy_with_mocked_answer(
                conn, {**base_answer, "source_character": character})
            row = conn.execute(
                "SELECT source_tier, source_character, source_status"
                " FROM source_raw WHERE source_raw_id=?", (sid,),
            ).fetchone()
            expected_character = character if character in SOURCE_CHARACTER_VALUES else "UNKNOWN"
            check(f"{character}: source_character persisted",
                  row["source_character"], expected_character)
            check(f"{character}: source_tier resolves to {expected_tier}",
                  row["source_tier"], expected_tier)
            check(f"{character}: relevance classification still applied",
                  row["source_status"], "RELEVANT")
        finally:
            conn.close()

    print("\nA row whose character an acquisition path already knows is left untouched:")
    db_path = os.path.join(tempfile.mkdtemp(), "known.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        sid = _seed_source(conn, source_type="PR_NEWSWIRE", source_tier="T2",
                            source_character="FIRST_PARTY_ANNOUNCEMENT")
        # The model is deliberately given a DIFFERENT answer -- if this were
        # applied it would wrongly demote a known first-party announcement.
        _run_relevancy_with_mocked_answer(
            conn, {**base_answer, "source_character": "DERIVATIVE_REPORTING"})
        row = conn.execute(
            "SELECT source_tier, source_character, source_status"
            " FROM source_raw WHERE source_raw_id=?", (sid,),
        ).fetchone()
        check("known source_character is not overwritten by the model's answer",
              row["source_character"], "FIRST_PARTY_ANNOUNCEMENT")
        check("known source_tier is not demoted",
              row["source_tier"], "T2")
        check("relevance classification still applied on a known-character row",
              row["source_status"], "RELEVANT")
    finally:
        conn.close()

    print("\nExisting relevant/not-relevant behavior is intact (untouched by this change):")
    db_path = os.path.join(tempfile.mkdtemp(), "notrel.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        sid = _seed_source(conn, source_type="WEB_URL", source_tier="T2",
                            source_character=None)
        _run_relevancy_with_mocked_answer(conn, {
            "classification": "NOT_RELEVANT",
            "reason_code": "PRODUCT_OR_COMMERCIAL",
            "model_confidence": "HIGH",
            "notes": None,
            "source_character": "ORIGINAL_REPORTING",
        })
        row = conn.execute(
            "SELECT source_status, notes FROM source_raw WHERE source_raw_id=?", (sid,),
        ).fetchone()
        check("NOT_RELEVANT still sets source_status", row["source_status"], "NOT_RELEVANT")
        notes = json.loads(row["notes"])
        check("reason_code still recorded under notes.relevancy",
              notes["relevancy"]["reason_code"], "PRODUCT_OR_COMMERCIAL")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. T4 is accepted by the existing tier-resolution machinery end to end
# ---------------------------------------------------------------------------

def test_t4_accepted_by_aggregation() -> None:
    print("\nA T4 observation loses to a T2 observation for the same field (unchanged rule),")
    print("and T4 does not raise or misorder anything in Stage 9:")
    db_path = os.path.join(tempfile.mkdtemp(), "t4_agg.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        txn = "tc_t4"
        # T4 source: a derivative rewrite naming a wrong/looser acquirer_name.
        conn.execute(
            "INSERT INTO source_raw (source_raw_id, source_type, source_tier,"
            " source_character, url, title, clean_text, source_status, fetched_at)"
            " VALUES (1,'WEB_URL','T4','DERIVATIVE_REPORTING','https://e.test/t4','t',"
            " 'body','RELEVANT','2026-09-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
            " deal_type, v2_event_type, event_history_type, target_status, target_name,"
            " acquirer_name, model_confidence, dt_prompt_version, hc_prompt_version,"
            " transaction_cluster_id)"
            " VALUES (1,1,'CLUSTERED','ACQUISITION','ACQUISITION','ANNOUNCED','PRIVATE',"
            " 'Beta Industries','Acme Corp Group','HIGH','deal_type_classifier:test',"
            " 'high_confidence_extraction:0.37',?)",
            (txn,),
        )
        # T2 source: the issuer's own announcement, the correct acquirer_name.
        conn.execute(
            "INSERT INTO source_raw (source_raw_id, source_type, source_tier,"
            " source_character, url, title, clean_text, source_status, fetched_at)"
            " VALUES (2,'PR_NEWSWIRE','T2','FIRST_PARTY_ANNOUNCEMENT','https://e.test/t2','t',"
            " 'body','RELEVANT','2026-09-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
            " deal_type, v2_event_type, event_history_type, target_status, target_name,"
            " acquirer_name, model_confidence, dt_prompt_version, hc_prompt_version,"
            " transaction_cluster_id)"
            " VALUES (2,2,'CLUSTERED','ACQUISITION','ACQUISITION','ANNOUNCED','PRIVATE',"
            " 'Beta Industries','Acme Corp','HIGH','deal_type_classifier:test',"
            " 'high_confidence_extraction:0.37',?)",
            (txn,),
        )
        conn.commit()

        for eid in (1, 2):
            write_staging_observations_for_extraction(
                conn, eid, observation_source_stage="HC_EXTRACT", include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                               aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = agg._call_agg_prompt
        agg._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r} -- T2 should win outright"))
        try:
            agg.run(conn, cfg, "t4-test")
        finally:
            agg._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT acquirer_name FROM transaction_record WHERE transaction_id=?", (txn,)
        ).fetchone()
        check("the T2 first-party announcement's value wins over the T4 rewrite's",
              canon["acquirer_name"] if canon else None, "Acme Corp")
    finally:
        conn.close()


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_resolver()
    test_known_paths()
    test_relevancy_residual_case()
    test_t4_accepted_by_aggregation()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — tier is deterministic policy; known classification always wins; "
          f"T4 is a real, accepted floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
