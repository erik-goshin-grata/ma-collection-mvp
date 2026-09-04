#!/usr/bin/env python3
"""Canonical participant closure — funding investors.

WHAT WENT WRONG

Funding HC 0.8 emits structured investors[], and stages/funding_hc_extract.py
persists them correctly into staging_investor. Nothing bridged those rows into
the existing canonical entity / transaction_participant model, so a completed
funding transaction had no investors in transaction_participant and Summary
could not see them -- even though the source-level fact was captured
correctly the whole time.

WHAT CHANGED

lib/investor_participant.py::materialize_investor_participants(conn,
transaction_id, log) reads every staging_investor row for every source
sharing a transaction_cluster_id, reconciles duplicate observations of the
same investor across sources (dedup by normalized name), and upserts one
canonical entity + transaction_participant row per resolved investor.
New vocabulary: participant_role="INVESTOR", side="INVESTOR" -- reusing
BUYER/SELLER/TARGET would misrepresent the relationship. is_lead,
is_new_investor and is_existing_investor are preserved on
transaction_participant, which already had columns for exactly these three.

lead_investor_rank, investment_amount and investment_currency have NO
canonical destination and none was added -- this file documents that gap
rather than silently dropping it or adding schema.

Wired into stages/aggregate.py, called once per cluster immediately after
the transaction_record upsert (same "must follow the transaction_record
INSERT above for the FK" point as the existing financial-metrics and
as-reported-multiples writers). No-op for a cluster with no staging_investor
rows (every M&A cluster, and any funding cluster naming no investors).

stages/summarize.py's funding_json gained an "investors" key, read from the
canonical transaction_participant/entity rows (not staging_investor
directly) -- plumbing only; the prompt is not yet updated to narrate it.

Run from project root:
    python scripts/test_investor_participant.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
from lib.investor_participant import materialize_investor_participants
import stages.aggregate as agg
import stages.summarize as summarize

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _seed_source_raw(conn, tag: str) -> int:
    conn.execute(
        "INSERT INTO source_raw (source_type, source_tier, source_character, url, title,"
        " clean_text, source_status, fetched_at) VALUES"
        " ('PR_NEWSWIRE','T2','FIRST_PARTY_ANNOUNCEMENT',?,'t','body','RELEVANT',"
        " '2026-09-04T00:00:00Z')",
        (f"https://e.test/investor-participant/{tag}",),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_extraction(conn, *, source_raw_id: int, cluster_id: str, target_name: str) -> int:
    conn.execute(
        "INSERT INTO staging_extraction (source_raw_id, status, deal_type, v2_event_type,"
        " event_history_type, target_status, target_name, model_confidence,"
        " dt_prompt_version, hc_prompt_version, transaction_cluster_id)"
        " VALUES (?, 'CLUSTERED', 'VC_ROUND', 'VC_ROUND', 'ANNOUNCED', 'PRIVATE', ?,"
        " 'HIGH', 'deal_type_classifier:test', 'funding_hc_extraction:0.8', ?)",
        (source_raw_id, target_name, cluster_id),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _seed_investor(conn, *, extraction_id: int, name: str, is_lead: bool = False,
                    is_new: int | None = None, is_existing: int | None = None) -> None:
    conn.execute(
        "INSERT INTO staging_investor (extraction_id, name, is_lead, is_new_investor,"
        " is_existing_investor) VALUES (?, ?, ?, ?, ?)",
        (extraction_id, name, 1 if is_lead else 0, is_new, is_existing),
    )


def _seed_transaction_record(conn, transaction_id: str) -> None:
    # materialize_investor_participants requires transaction_record to already
    # exist (transaction_participant.transaction_id is a real FK) -- exactly
    # the precondition Stage 9 satisfies by calling it right after its own
    # transaction_record upsert. Minimal row: only transaction_id is required.
    conn.execute(
        "INSERT INTO transaction_record (transaction_id, is_current) VALUES (?, 1)",
        (transaction_id,),
    )


def _fresh_db(name: str):
    db_path = os.path.join(tempfile.mkdtemp(), name)
    init_db(db_path)
    return get_connection(db_path)


def _participants(conn, transaction_id: str):
    return conn.execute(
        """
        SELECT e.canonical_name AS name, tp.is_lead, tp.is_new_investor,
               tp.is_existing_investor, tp.participant_role, tp.side
        FROM transaction_participant tp
        JOIN entity e ON e.entity_id = tp.entity_id
        WHERE tp.transaction_id = ? AND tp.is_current = 1
        ORDER BY e.canonical_name
        """,
        (transaction_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# 1. NovaGo -- direct call, both leads preserved
# ---------------------------------------------------------------------------

def test_novago() -> None:
    print("\nNovaGo: Neurimmune + Pureos Bioventures become canonical lead investors:")
    conn = _fresh_db("novago.db")
    try:
        sid = _seed_source_raw(conn, "novago")
        eid = _seed_extraction(conn, source_raw_id=sid, cluster_id="tc_novago",
                                target_name="NovaGo Therapeutics")
        _seed_investor(conn, extraction_id=eid, name="Neurimmune", is_lead=True)
        _seed_investor(conn, extraction_id=eid, name="Pureos Bioventures", is_lead=True)
        _seed_transaction_record(conn, "tc_novago")
        conn.commit()

        result = materialize_investor_participants(conn, "tc_novago")
        conn.commit()
        check("two source-level rows seen", result["investors_seen"], 2)
        check("two canonical participants written", result["participants_written"], 2)

        rows = _participants(conn, "tc_novago")
        names = [r["name"] for r in rows]
        check("exactly two canonical investors", len(rows), 2)
        check("Neurimmune present", "Neurimmune" in names, True)
        check("Pureos Bioventures present", "Pureos Bioventures" in names, True)
        check("both recorded as lead", all(r["is_lead"] == 1 for r in rows), True)
        check("role is INVESTOR", {r["participant_role"] for r in rows}, {"INVESTOR"})
        check("side is INVESTOR", {r["side"] for r in rows}, {"INVESTOR"})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Catch -- duplicate source-level rows collapse to one canonical row each
# ---------------------------------------------------------------------------

def test_catch_dedup() -> None:
    print("\nCatch: duplicate source-level investor rows collapse to exactly four:")
    conn = _fresh_db("catch.db")
    try:
        # Source 1 names three investors.
        sid1 = _seed_source_raw(conn, "catch-1")
        eid1 = _seed_extraction(conn, source_raw_id=sid1, cluster_id="tc_catch",
                                 target_name="Catch")
        _seed_investor(conn, extraction_id=eid1, name="Entrée Capital", is_lead=True)
        _seed_investor(conn, extraction_id=eid1, name="Pitango")
        _seed_investor(conn, extraction_id=eid1, name="Seedcamp")
        # Source 2 independently restates two of the same investors and names one new one.
        sid2 = _seed_source_raw(conn, "catch-2")
        eid2 = _seed_extraction(conn, source_raw_id=sid2, cluster_id="tc_catch",
                                 target_name="Catch")
        _seed_investor(conn, extraction_id=eid2, name="Pitango")
        _seed_investor(conn, extraction_id=eid2, name="Seedcamp")
        _seed_investor(conn, extraction_id=eid2, name="Factorial Capital")
        _seed_transaction_record(conn, "tc_catch")
        conn.commit()

        result = materialize_investor_participants(conn, "tc_catch")
        conn.commit()
        check("six source-level rows seen (three per source)", result["investors_seen"], 6)
        check("four canonical participants written", result["participants_written"], 4)

        rows = _participants(conn, "tc_catch")
        names = sorted(r["name"] for r in rows)
        check("exactly four canonical investors, each once",
              names, ["Entrée Capital", "Factorial Capital", "Pitango", "Seedcamp"])

        print("\nRe-running materialization is idempotent (no duplicate rows):")
        result2 = materialize_investor_participants(conn, "tc_catch")
        conn.commit()
        check("second run writes zero new participants", result2["participants_written"], 0)
        check("still exactly four canonical investors", len(_participants(conn, "tc_catch")), 4)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. DocPharma
# ---------------------------------------------------------------------------

def test_docpharma() -> None:
    print("\nDocPharma: Equentis, 100Unicorns and Vinners become canonical investors:")
    conn = _fresh_db("docpharma.db")
    try:
        sid = _seed_source_raw(conn, "docpharma")
        eid = _seed_extraction(conn, source_raw_id=sid, cluster_id="tc_docpharma",
                                target_name="DocPharma")
        _seed_investor(conn, extraction_id=eid, name="Equentis", is_lead=True)
        _seed_investor(conn, extraction_id=eid, name="100Unicorns")
        _seed_investor(conn, extraction_id=eid, name="Vinners")
        _seed_transaction_record(conn, "tc_docpharma")
        conn.commit()

        materialize_investor_participants(conn, "tc_docpharma")
        conn.commit()
        names = sorted(r["name"] for r in _participants(conn, "tc_docpharma"))
        check("all three named investors present",
              names, ["100Unicorns", "Equentis", "Vinners"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. No synthesis for a cluster with no named investors (e.g. an M&A cluster)
# ---------------------------------------------------------------------------

def test_no_synthesis() -> None:
    print("\nA cluster with no staging_investor rows materializes nothing:")
    conn = _fresh_db("nosynth.db")
    try:
        sid = _seed_source_raw(conn, "nosynth")
        _seed_extraction(conn, source_raw_id=sid, cluster_id="tc_nosynth",
                          target_name="Beta Industries")
        conn.commit()

        result = materialize_investor_participants(conn, "tc_nosynth")
        check("zero investors seen", result["investors_seen"], 0)
        check("zero participants written", result["participants_written"], 0)
        check("no participant rows exist", len(_participants(conn, "tc_nosynth")), 0)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Wired into the real Stage 9 pipeline, not just callable standalone
# ---------------------------------------------------------------------------

def test_wired_into_aggregate() -> None:
    print("\nStage 9's own run() materializes investors without a direct call:")
    conn = _fresh_db("wired.db")
    try:
        sid = _seed_source_raw(conn, "wired")
        eid = _seed_extraction(conn, source_raw_id=sid, cluster_id="tc_wired",
                                target_name="WiredCo")
        _seed_investor(conn, extraction_id=eid, name="Wired Ventures", is_lead=True)
        conn.commit()

        from lib.observation_writer import (
            backfill_observation_transaction_ids,
            write_staging_observations_for_extraction,
        )
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="FUNDING_HC_EXTRACT",
            include_stage3=True, include_hc=True, include_funding=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                               aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = agg._call_agg_prompt
        agg._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            agg.run(conn, cfg, "investor-participant-wiring-test")
        finally:
            agg._call_agg_prompt = original
        conn.commit()

        rows = _participants(conn, "tc_wired")
        check("Stage 9 itself materialized the investor, no direct call needed",
              [r["name"] for r in rows], ["Wired Ventures"])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Summary plumbing: canonical investors reach the rendered prompt
# ---------------------------------------------------------------------------

def test_summary_plumbing() -> None:
    print("\nSummarize's funding_json carries the materialized canonical investors:")
    conn = _fresh_db("summary.db")
    try:
        sid = _seed_source_raw(conn, "summary")
        eid = _seed_extraction(conn, source_raw_id=sid, cluster_id="tc_summary_test",
                                target_name="SummaryCo")
        _seed_investor(conn, extraction_id=eid, name="Northwind Ventures", is_lead=True)
        conn.commit()

        from lib.observation_writer import (
            backfill_observation_transaction_ids,
            write_staging_observations_for_extraction,
        )
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="FUNDING_HC_EXTRACT",
            include_stage3=True, include_hc=True, include_funding=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                               aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = agg._call_agg_prompt
        agg._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError("unexpected aggregation conflict"))
        try:
            agg.run(conn, cfg, "summary-plumbing-test")
        finally:
            agg._call_agg_prompt = original
        conn.commit()

        captured = {}

        def _fake_call_prompt(**kwargs):
            captured["user_prompt"] = kwargs["user_prompt"]
            return {"summary_text": "SummaryCo raised a round.", "word_count": 5,
                    "model_confidence": "HIGH", "notes": None}

        original_call_prompt = summarize.call_prompt
        original_sleep = summarize._SLEEP
        summarize.call_prompt = _fake_call_prompt
        summarize._SLEEP = 0
        try:
            summarize.run(conn, cfg, "summary-plumbing-test")
        finally:
            summarize.call_prompt = original_call_prompt
            summarize._SLEEP = original_sleep

        check("summarize ran and captured a prompt", "user_prompt" in captured, True)
        check("Northwind Ventures reaches the rendered Summary prompt",
              "Northwind Ventures" in captured.get("user_prompt", ""), True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Unsupported canonical attributes: no schema was added for them
# ---------------------------------------------------------------------------

def test_unsupported_attributes_documented_not_added() -> None:
    print("\nlead_investor_rank / investment_amount / investment_currency: no schema added:")
    conn = _fresh_db("schema.db")
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_participant)")}
        check("lead_investor_rank has no canonical column",
              "lead_investor_rank" in cols, False)
        check("investment_amount has no canonical column",
              "investment_amount" in cols, False)
        check("investment_currency has no canonical column",
              "investment_currency" in cols, False)
        check("is_lead column exists (preserved)", "is_lead" in cols, True)
        check("is_new_investor column exists (preserved)", "is_new_investor" in cols, True)
        check("is_existing_investor column exists (preserved)",
              "is_existing_investor" in cols, True)
    finally:
        conn.close()


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_novago()
    test_catch_dedup()
    test_docpharma()
    test_no_synthesis()
    test_wired_into_aggregate()
    test_summary_plumbing()
    test_unsupported_attributes_documented_not_added()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — funding investors materialize into the canonical participant model, "
          f"deduplicated across sources, with no unnamed synthesis.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
