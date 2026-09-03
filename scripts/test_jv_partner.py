#!/usr/bin/env python3
"""HC 0.37 — JV_PARTNER: the parties forming a joint venture.

WHAT WENT WRONG

A real historical extraction (Wärtsilä / RCT Solutions, HC 0.12) showed the gap: a
JOINT_VENTURE event has no buyer and no target, but the prompt had no party shape for
co-forming partners, so the model invented a synthetic acquirer name ("Wärtsilä / RCT
Solutions Joint Venture") and acquirer_type=consortium to force the event into the
ordinary acquirer/target shape.

WHAT CHANGED

A seventh party array, `jv_partners`, same preservation pattern as the six existing
roles (name only, always an array including `[]`). Populated ONLY when the classified
event is JOINT_VENTURE, enforced at write time in stages/high_confidence_extract.py
(the validator sees only the model response; the resolved event type comes from
Stage 3 on the row) -- the same enforcement pattern asset_type already uses for its
own subordination to target_type=assets.

WHAT THIS IS NOT

No entity resolution, no per-partner type or other attribute, no new Stage 9 canonical
behaviour -- preservation-only, exactly like the six existing party arrays, all of
which are deliberately absent from aggregate.py's _FIELDS.

Run from project root:
    python scripts/test_jv_partner.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
import lib.observation_writer as ow
from prompts.base import load_prompt_file
import stages.aggregate as agg
import stages.high_confidence_extract as hc

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []
log = logging.getLogger("t")
logging.basicConfig(level=logging.CRITICAL)


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t)


# ---------------------------------------------------------------------------
# 1. The contract
# ---------------------------------------------------------------------------

def test_prompt() -> None:
    print("\nThe instruction reaches the model:")
    flat = _norm(load_prompt_file("high_confidence_extraction")["system"])
    check("JOINT VENTURE PARTIES delivered", "JOINT VENTURE PARTIES" in flat, True)
    check("gated to JOINT_VENTURE",
          "Populate ONLY when DEAL TYPE is JOINT_VENTURE" in flat, True)
    check("unnamed partners are not invented",
          "DO NOT INFER AN UNNAMED PARTNER" in flat, True)
    check("an existing JV acting elsewhere is not this array's concern",
          "AN EXISTING JV IS NOT THIS ARRAY'S CONCERN" in flat, True)
    check("response slot present", '"jv_partners": []' in flat, True)
    check("prompt version is 0.37", hc._VERSION, "0.37")

    print("\nconsideration_type is retired from this contract:")
    check("no consideration_type instruction delivered",
          "consideration_type" in flat, False)


def _txn(name_or_none):
    """One transaction element, jv_partners set from the argument."""
    jv = [{"name": n} for n in name_or_none] if name_or_none else []
    return {
        "target": {"name": None, "domain": None, "ticker": None, "description": None,
                   "asset_type": None},
        "acquirer": {"name": "Wärtsilä", "domain": None, "ticker": None,
                     "type": "strategic_corporate", "description": None,
                     "sponsor_name": None},
        "parent_seller": {"name": None, "ticker": None, "description": None},
        "acquirers": [], "buy_side_sponsors": [], "parent_sellers": [],
        "parent_acquirers": [], "sell_side_sponsors": [], "sellers": [],
        "jv_partners": jv,
        "deal": {"pct_acquired": None, "stake_transition_type": None,
                  "offer_mechanism": None, "sponsor_transaction_role": None},
        "dates": {"announced_date": "2026-08-01", "announced_date_precision": "exact",
                   "closed_date": None, "closed_date_precision": None,
                   "signing_date": None, "signing_date_precision": None,
                   "rumor_date": None},
        "value": {"amount": None, "currency": None, "type": None,
                   "type_confidence": "HIGH", "qualifier": None, "per_share_price": None},
        "value_observations": [],
        "features": {"is_secondary_buyout": None, "is_merger_of_equals": None,
                      "is_going_private_outcome": None},
        "reported_multiples": [],
        "round_size": None,
        "financials_disclosure_status": "UNKNOWN",
        "transaction_terms_disclosure_status": "UNKNOWN",
        "target_financials": {
            "revenue_amount": None, "revenue_period_type": None, "revenue_period_end": None,
            "ebitda_amount": None, "ebitda_period_type": None, "ebitda_period_end": None,
            "currency": None, "total_debt": None, "total_debt_currency": None,
            "cash_st": None, "cash_st_currency": None, "balance_sheet_as_of_date": None,
        },
        "model_confidence": "HIGH",
        "notes": None,
    }


# ---------------------------------------------------------------------------
# 2. The parser -- generic over the array key, same as the other six
# ---------------------------------------------------------------------------

def test_parser() -> None:
    print("\nName only, no invented per-partner attribute:")
    out = json.loads(hc._parties_json(
        {"jv_partners": [{"name": "Wärtsilä"}, {"name": "RCT Solutions GmbH"}]},
        "jv_partners", log, 1))
    check("two partners", len(out), 2)
    check("named as stated", [i["name"] for i in out],
          ["Wärtsilä", "RCT Solutions GmbH"])
    check("no type key invented", any("type" in i for i in out), False)

    print("\nAlways an array, including empty:")
    check("empty stays []", hc._parties_json({"jv_partners": []}, "jv_partners", log, 1), "[]")
    check("a missing key is still []", hc._parties_json({}, "jv_partners", log, 1), "[]")


# ---------------------------------------------------------------------------
# 3. Write-path gating: JV_PARTNER only on a JOINT_VENTURE event
# ---------------------------------------------------------------------------

def _seed_source_and_staging(conn, event_type: str) -> int:
    conn.execute(
        "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
        " clean_text, source_status, fetched_at) VALUES"
        " ('PR_NEWSWIRE','T2','https://e.test/jv','t','2026-08-01','body','RELEVANT',"
        " '2026-08-01T00:00:00Z')"
    )
    source_raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO staging_extraction (source_raw_id, status, deal_type, v2_event_type,"
        " event_type, event_history_type, target_status, dt_prompt_version)"
        " VALUES (?, 'CLASSIFIED', ?, ?, 'ANNOUNCEMENT', 'ANNOUNCED', 'PRIVATE',"
        " 'deal_type_classifier:test')",
        (source_raw_id, event_type, event_type),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _run_hc_with_mocked_response(conn, jv_names) -> None:
    original_call_prompt = hc.call_prompt
    original_sleep = hc._SLEEP
    hc._SLEEP = 0
    hc.call_prompt = lambda **_kwargs: {"transactions": [_txn(jv_names)]}
    try:
        hc.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"), run_id="test_jv_partner")
    finally:
        hc.call_prompt = original_call_prompt
        hc._SLEEP = original_sleep


def test_write_path_gating() -> None:
    print("\nA JOINT_VENTURE event collects its named partners:")
    db_path = os.path.join(tempfile.mkdtemp(), "jv_yes.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        eid = _seed_source_and_staging(conn, "JOINT_VENTURE")
        _run_hc_with_mocked_response(conn, ["Wärtsilä", "RCT Solutions GmbH"])
        row = conn.execute(
            "SELECT jv_partners FROM staging_extraction WHERE extraction_id=?", (eid,)
        ).fetchone()
        check("staging_extraction.jv_partners preserved",
              [i["name"] for i in json.loads(row["jv_partners"] or "[]")],
              ["Wärtsilä", "RCT Solutions GmbH"])

        obs = conn.execute(
            "SELECT field_value, observation_fact_key FROM transaction_field_observation"
            " WHERE field_name='jv_partner_party' ORDER BY observation_id"
        ).fetchall()
        check("two jv_partner_party observations", len(obs), 2)
        check("named independently",
              [json.loads(r["field_value"])["name"] for r in obs],
              ["Wärtsilä", "RCT Solutions GmbH"])
        check("distinct fact keys", len({r["observation_fact_key"] for r in obs}), 2)
    finally:
        conn.close()

    print("\nAn unnamed partner is not invented -- empty stays empty:")
    db_path = os.path.join(tempfile.mkdtemp(), "jv_empty.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        eid = _seed_source_and_staging(conn, "JOINT_VENTURE")
        _run_hc_with_mocked_response(conn, None)
        row = conn.execute(
            "SELECT jv_partners FROM staging_extraction WHERE extraction_id=?", (eid,)
        ).fetchone()
        check("jv_partners stays []", row["jv_partners"], "[]")
    finally:
        conn.close()

    print("\nAn existing JV acting as an ordinary buyer does NOT populate jv_partners:")
    db_path = os.path.join(tempfile.mkdtemp(), "jv_wrong_type.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        eid = _seed_source_and_staging(conn, "ACQUISITION")
        # A model that (incorrectly) still returns jv_partners on a non-JOINT_VENTURE
        # row must be cleared at write time -- this array is not a general-purpose
        # "other parties" slot.
        _run_hc_with_mocked_response(conn, ["Wärtsilä-RCT Solutions JV LLC"])
        row = conn.execute(
            "SELECT jv_partners FROM staging_extraction WHERE extraction_id=?", (eid,)
        ).fetchone()
        check("jv_partners cleared for a non-JOINT_VENTURE event", row["jv_partners"], "[]")

        obs = conn.execute(
            "SELECT COUNT(*) AS n FROM transaction_field_observation"
            " WHERE field_name='jv_partner_party'"
        ).fetchone()
        check("no jv_partner_party observation written", obs["n"], 0)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Preservation only -- no new Stage 9 canonical behaviour
# ---------------------------------------------------------------------------

def test_preservation_only() -> None:
    print("\njv_partner_party is preservation only, exactly like its six siblings:")
    check("jv_partners absent from aggregate._FIELDS",
          "jv_partners" in dict(agg._FIELDS), False)
    check("jv_partner_party absent from aggregate._FIELDS",
          "jv_partner_party" in dict(agg._FIELDS), False)
    check("registered in the observation writer",
          any(col == "jv_partners" for col, _ in ow.PARTY_ARRAY_FIELDS), True)
    check("aggregate version unchanged at 0.13", agg._VERSION, "0.13")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_parser()
    test_write_path_gating()
    test_preservation_only()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — JV_PARTNER is collected only for a JOINT_VENTURE event, "
          f"named parties only, preservation only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
