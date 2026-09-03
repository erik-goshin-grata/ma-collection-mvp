#!/usr/bin/env python3
"""HC 0.37 / LC 0.14 closure — consideration_type retired from HC; percentage is
source-stated only in LC; Funding HC's consideration_type is no longer stranded.

WHAT WENT WRONG (three related findings from the HC/LC alignment review)

1. HC authored `consideration_type` directly ("classify the consideration structure
   if determinable"), but the field was absent from HC_FIELDS, so it never reached
   transaction_field_observation. Canonical `transaction_record.consideration_type`
   is (and always was) populated exclusively by `_derive_consideration_type` from
   LC's `consideration_components` -- HC's value was pure sunk cost with zero
   downstream effect, on the M&A path.

2. LC's own worked examples computed `consideration_components[].percentage` by
   dividing a stated component amount by the deal's stated total value (Example 2:
   50.0/25.0/25.0 from $400M/$200M/$200M against $800M). Nothing the source said was
   a percentage; this was the same forbidden arithmetic HC applies to deal value
   itself, applied to a field nothing had said so about.

3. Funding HC extracts its own `consideration_type` (equity/safe/convertible_note/
   debt/warrant) -- a real, distinct instrument vocabulary that has no equivalent in
   LC's consideration_components forms. It shares the physical column and canonical
   field with the M&A path but was ALSO absent from FUNDING_FIELDS, so a correctly
   extracted funding instrument was discarded the same way HC's M&A-path value was.

WHAT CHANGED

- HC no longer authors `consideration_type` at all (removed from §4, RESPONSE
  FORMAT, and stage validation/write plumbing).
- LC's `percentage` is populated only from a percentage the source states directly;
  never computed from amount / deal value.
- `consideration_type` is added to FUNDING_FIELDS and to aggregate.py's `_FIELDS`,
  and Stage 9's derivation falls back to the collected value only when the
  component-based derivation returns None -- which happens for every funding row
  (no SAFE/convertible_note/warrant component form exists) and never for an M&A row
  where components exist, so the M&A path's own derived value keeps precedence.

Run from project root:
    python scripts/test_consideration_type_closure.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import lib.observation_writer as ow
from lib.observation_writer import (
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)
from prompts.base import load_prompt_file
import stages.aggregate as agg
import stages.high_confidence_extract as hc

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
# 1. HC no longer authors consideration_type
# ---------------------------------------------------------------------------

def test_hc_retirement() -> None:
    print("\nHC's delivered contract no longer mentions consideration_type:")
    flat = _norm(load_prompt_file("high_confidence_extraction")["system"])
    check("no CONSIDERATION TYPE section", "CONSIDERATION TYPE" in flat, False)
    check("no consideration_type key anywhere in §4", "consideration_type" in flat, False)

    print("\nHC's parser no longer validates or writes it:")
    check("_VALID_CONSIDERATION_TYPES removed", hasattr(hc, "_VALID_CONSIDERATION_TYPES"), False)
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    check("no consideration_type column in the SQL plumbing",
          "consideration_type" in src, False)

    print("\nStage 4 does not require it:")
    check("consideration_type absent from _REQUIRED_KEYS",
          "consideration_type" in hc._REQUIRED_KEYS, False)


# ---------------------------------------------------------------------------
# 2. LC: percentage is source-stated only
# ---------------------------------------------------------------------------

def test_lc_percentage() -> None:
    print("\nLC's delivered contract forbids computing a percentage:")
    flat = _norm(load_prompt_file("low_confidence_extraction")["system"])
    check("NEVER COMPUTE A PERCENTAGE delivered", "NEVER COMPUTE A PERCENTAGE" in flat, True)
    check("the old compute-percentages framing is gone",
          "compute percentages against total deal value" in flat, False)

    print("\nEvery worked example's percentage is null unless the source states one:")
    md = (ROOT / "prompts" / "low_confidence_extraction.md").read_text(encoding="utf-8")
    # Every remaining numeric percentage in the examples section must be one this
    # test can account for -- the new source-stated example (60/40) is the only one.
    examples_start = md.index("## 7. Few-Shot Examples")
    examples_end = md.index("## 8. Failure Modes")
    examples_text = md[examples_start:examples_end]
    numeric_percentages = re.findall(r'"percentage":\s*([0-9.]+)', examples_text)
    check("only the source-stated example carries a numeric percentage",
          sorted(numeric_percentages, key=float), ["40.0", "60.0"])
    check("the source-stated example is present",
          "60% cash and 40% Acme common stock" in examples_text, True)

    print("\nLC version bumped, parity holds:")
    check("prompt declares 0.14",
          bool(re.search(r"\*\*Version:\*\*\s*0\.14", md)), True)
    from stages import low_confidence_extract as lc
    check("stage _VERSION agrees", lc._VERSION, "0.14")


# ---------------------------------------------------------------------------
# 3. Funding HC's consideration_type is no longer stranded
# ---------------------------------------------------------------------------

def test_funding_consideration_type_reaches_canonical() -> None:
    print("\nconsideration_type is registered for the funding write/read path:")
    check("in FUNDING_FIELDS", "consideration_type" in ow.FUNDING_FIELDS, True)
    check("in aggregate._FIELDS", "consideration_type" in dict(agg._FIELDS), True)

    print("\nEnd-to-end: a funding row's collected instrument reaches canonical:")
    db_path = os.path.join(tempfile.mkdtemp(), "funding_ctype.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at) VALUES"
            " ('PR_NEWSWIRE','T2','https://e.test/funding','t','2026-08-01','body',"
            " 'RELEVANT','2026-08-01T00:00:00Z')"
        )
        source_raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        txn = "tc_funding_ctype"
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, deal_type, v2_event_type,"
            " event_type, event_history_type, target_status, target_name,"
            " consideration_type, model_confidence, dt_prompt_version,"
            " hc_prompt_version, transaction_cluster_id)"
            " VALUES (?, 'CLUSTERED', 'VC_ROUND', 'VC_ROUND', 'ANNOUNCEMENT', 'ANNOUNCED',"
            " 'PRIVATE', 'EarlyStage', 'safe', 'HIGH', 'deal_type_classifier:test',"
            " 'funding_hc_extraction:0.7', ?)",
            (source_raw_id, txn),
        )
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="FUNDING_HC_EXTRACT",
            include_funding=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        obs = conn.execute(
            "SELECT field_value FROM transaction_field_observation"
            " WHERE transaction_id=? AND field_name='consideration_type'", (txn,)
        ).fetchone()
        check("observation written", obs["field_value"] if obs else None, "safe")

        cfg = SimpleNamespace(log_level="ERROR",
                               aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = agg._call_agg_prompt
        agg._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            agg.run(conn, cfg, "funding-ctype-test")
        finally:
            agg._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT consideration_type FROM transaction_record WHERE transaction_id=?",
            (txn,)).fetchone()
        check("canonical consideration_type on the funding path",
              canon["consideration_type"] if canon else None, "safe")
    finally:
        conn.close()

    print("\nThe M&A path's own derivation keeps precedence when components exist:")
    db_path = os.path.join(tempfile.mkdtemp(), "ma_ctype.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at) VALUES"
            " ('PR_NEWSWIRE','T2','https://e.test/ma','t','2026-08-01','body',"
            " 'RELEVANT','2026-08-01T00:00:00Z')"
        )
        source_raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        txn = "tc_ma_ctype"
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, deal_type, v2_event_type,"
            " event_type, event_history_type, target_status, target_name, acquirer_name,"
            " consideration_components, model_confidence, dt_prompt_version,"
            " hc_prompt_version, lc_prompt_version, transaction_cluster_id)"
            " VALUES (?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCEMENT',"
            " 'ANNOUNCED', 'PRIVATE', 'Beta Industries', 'Acme Corp', ?, 'HIGH',"
            " 'deal_type_classifier:test', 'high_confidence_extraction:0.37',"
            " 'low_confidence_extraction:0.14', ?)",
            (source_raw_id,
             json.dumps([{"form": "CASH", "amount": None, "percentage": None,
                          "description": "all cash, terms undisclosed"}]),
             txn),
        )
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        # No HC consideration_type observation is ever written on the M&A path
        # (0.37 no longer authors it) -- only the LC component field group.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="LC_EXTRACT", include_lc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                               aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = agg._call_agg_prompt
        agg._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            agg.run(conn, cfg, "ma-ctype-test")
        finally:
            agg._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT consideration_type FROM transaction_record WHERE transaction_id=?",
            (txn,)).fetchone()
        check("canonical consideration_type still derives from components on the M&A path",
              canon["consideration_type"] if canon else None, "CASH")
    finally:
        conn.close()


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_hc_retirement()
    test_lc_percentage()
    test_funding_consideration_type_reaches_canonical()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — HC's stranded value is gone; LC's percentage is source-stated "
          f"only; Funding HC's instrument reaches canonical without disturbing the "
          f"M&A path's own derivation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
