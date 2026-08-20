#!/usr/bin/env python3
"""Guard tests for combination_structure (inventory §T2, slice S-B).

V2 carried MERGER and REVERSE_MERGER as top-level event types, competing with
ACQUISITION for the same slot: a merger and a purchase are both acquisitions, and typing
them separately meant "was this an acquisition?" had to enumerate three values and
"is this a merger?" could not be asked at all without conflating a de-SPAC with a
merger of equals.

V3 makes ACQUISITION the broad event and records the structure separately, hierarchically:

    DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER,  or null for an ordinary acquisition

The tests below pin four things:

  1. THE CANONICAL PATH HOLDS.  The S-A defect was a field written to staging and never
     observed, so its canonical column stayed NULL forever. Stage 3 writes no
     observations of its own -- they are written when Stage 4 runs with
     include_stage3=True -- so this walks the whole production chain and asserts the
     value survives it, with `recap_type` carried through as an unchanged control.
  2. THE HIERARCHY IS QUERYABLE.  The most specific value is stored, and broader
     questions are answered by implication rather than equality.
  3. NEW OUTPUT CANNOT NAME THE OLD VALUES.  Legacy rows stay readable, but a 0.9
     response saying MERGER is a schema violation -- readability of old data must never
     widen what the model may emit.
  4. NOTHING ELSE MOVED.  Spin/Split, JV, Recap, Funding, PIPE and UNKNOWN classify
     unchanged and cannot carry a combination structure.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
import stages.deal_type_classify as dtc
from lib.observation_writer import (
    STAGE3_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _result(**over) -> dict:
    base = {
        "v2_event_type": "ACQUISITION", "deal_type": "ACQUISITION",
        "combination_structure": None, "spin_split_type": None,
        "distribution_mechanism": None, "recap_type": None,
        "target_type": "standalone_company", "event_history_type": "ANNOUNCED",
        "target_status": "PRIVATE", "model_confidence": "HIGH",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Canonical-field gate: staging -> observation -> aggregation -> canonical
# ---------------------------------------------------------------------------

def _test_canonical_path(failures: list[str]) -> None:
    """The four-hop path, using the PRODUCTION field group and include_* flag.

    `recap_type` rides along as the control: it is an unchanged member of the same
    Stage 3 group, so if it fails too the test is reporting a broken harness rather
    than a broken field.
    """
    if "combination_structure" not in STAGE3_FIELDS:
        failures.append(
            "observation/STAGE3_FIELDS: combination_structure is absent, so Stage 4's "
            "include_stage3 write will not observe it and the canonical column stays NULL")
    if "recap_type" not in STAGE3_FIELDS:
        failures.append("observation/control: recap_type missing from STAGE3_FIELDS")

    db_path = os.path.join(tempfile.mkdtemp(), "combo.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # Report a missing column as a named failure rather than raising, and keep going:
        # the control must still be exercised so the pre-change run shows recap_type
        # passing while combination_structure fails. A control that is skipped whenever
        # the field under test is broken proves nothing about isolation.
        have_combo = True
        for table in ("staging_extraction", "transaction_record"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "combination_structure" not in cols:
                have_combo = False
                failures.append(f"schema/{table}: combination_structure column is missing — "
                                f"migration 005 did not run")

        now = datetime.now(timezone.utc).isoformat()
        # Two rows: the de-SPAC under test, and a recap carrying the control value.
        cases = [
            ("tc_combo_despac", "ACQUISITION", "DE_SPAC", None),
            ("tc_combo_recap", "RECAPITALIZATION", None, "DIVIDEND"),
        ]
        for i, (cluster, event, combo, recap) in enumerate(cases, start=1):
            conn.execute(
                "INSERT INTO source_raw (source_type, source_tier, url, title,"
                " published_date, clean_text, source_status, fetched_at)"
                " VALUES ('PR_NEWSWIRE','T1',?,?, '2026-08-18','body','RELEVANT',?)",
                (f"u{i}", f"t{i}", now),
            )
            srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "recap_type",
                    "event_history_type", "target_status", "target_type", "target_type_v2",
                    "target_name", "acquirer_name", "acquirer_type", "acquirer_type_v2",
                    "announced_date", "announced_date_precision",
                    "financials_disclosure_status", "model_confidence",
                    "dt_prompt_version", "hc_prompt_version", "transaction_cluster_id"]
            vals = [srid, "CLUSTERED", event, event, recap, "ANNOUNCED", "PRIVATE",
                    "standalone_company", "standalone_company", f"Target {i}",
                    f"Acquirer {i}", "unknown", "unknown", "2026-08-18", "exact",
                    "UNKNOWN", "HIGH", "0.9", "0.18", cluster]
            if have_combo:
                cols.append("combination_structure")
                vals.append(combo)
            conn.execute(
                f"INSERT INTO staging_extraction ({', '.join(cols)})"
                f" VALUES ({', '.join('?' * len(cols))})", vals)
            eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            # The production write, with the production flag. Not a local reimplementation.
            write_staging_observations_for_extraction(
                conn, eid, observation_source_stage="DT_CLASSIFY", include_stage3=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        # Hop 2: an observation must exist for each field.
        checks = [("recap_type", "tc_combo_recap", "DIVIDEND")]          # control, always
        if have_combo:
            checks.insert(0, ("combination_structure", "tc_combo_despac", "DE_SPAC"))
        for field, cluster, expected in checks:
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id=? AND field_name=?", (cluster, field)).fetchone()
            if row is None:
                failures.append(f"observation/{field}: no observation row was written")
            else:
                _eq(failures, f"observation/{field}", row["field_value"], expected)

        # Hops 3-4: aggregate under the CONFIGURED default, then read the canonical row.
        # Hardcoding "observation" here would test a path the pipeline may not take.
        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "combo-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        if have_combo:
            canon = conn.execute(
                "SELECT combination_structure FROM transaction_record"
                " WHERE transaction_id='tc_combo_despac'").fetchone()
            if canon is None:
                failures.append("canonical: no transaction_record row for the de-SPAC cluster")
            else:
                _eq(failures,
                    f"canonical/combination_structure "
                    f"(read_source={DEFAULT_AGGREGATION_READ_SOURCE})",
                    canon["combination_structure"], "DE_SPAC")

        ctl = conn.execute(
            "SELECT recap_type FROM transaction_record"
            " WHERE transaction_id='tc_combo_recap'").fetchone()
        if ctl is None:
            failures.append("canonical/control: no transaction_record row for the recap cluster")
        else:
            _eq(failures,
                f"canonical/recap_type CONTROL (read_source={DEFAULT_AGGREGATION_READ_SOURCE})",
                ctl["recap_type"], "DIVIDEND")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Hierarchy
# ---------------------------------------------------------------------------

def _test_hierarchy(failures: list[str]) -> None:
    vocab = getattr(dtc, "_VALID_COMBINATION_STRUCTURE", None)
    if vocab is None:
        failures.append("vocab: stages.deal_type_classify defines no "
                        "_VALID_COMBINATION_STRUCTURE — the field is not implemented")
        return
    _eq(failures, "vocab", set(vocab), {"MERGER", "REVERSE_MERGER", "DE_SPAC"})

    db_path = os.path.join(tempfile.mkdtemp(), "hier.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
        if "combination_structure" not in cols:
            failures.append("schema/hierarchy: combination_structure column is missing")
            return
        now = datetime.now(timezone.utc).isoformat()
        for i, combo in enumerate(("MERGER", "REVERSE_MERGER", "DE_SPAC", None), start=1):
            conn.execute(
                "INSERT INTO source_raw (source_type, source_tier, url, title,"
                " published_date, clean_text, source_status, fetched_at)"
                " VALUES ('PR_NEWSWIRE','T1',?,?, '2026-08-18','b','RELEVANT',?)",
                (f"h{i}", f"h{i}", now))
            srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO staging_extraction (source_raw_id, status, v2_event_type,"
                " combination_structure) VALUES (?, 'CLUSTERED', 'ACQUISITION', ?)",
                (srid, combo))

        # "Is this a merger?" is an implication query. Equality would miss two of three.
        by_implication = conn.execute(
            "SELECT COUNT(*) FROM staging_extraction WHERE combination_structure"
            " IN ('MERGER','REVERSE_MERGER','DE_SPAC')").fetchone()[0]
        by_equality = conn.execute(
            "SELECT COUNT(*) FROM staging_extraction WHERE combination_structure"
            " = 'MERGER'").fetchone()[0]
        _eq(failures, "hierarchy/is_merger by implication", by_implication, 3)
        _eq(failures, "hierarchy/equality alone is insufficient", by_equality, 1)

        despac = conn.execute(
            "SELECT COUNT(*) FROM staging_extraction WHERE combination_structure"
            " = 'DE_SPAC'").fetchone()[0]
        _eq(failures, "hierarchy/most specific stored", despac, 1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. New output cannot name the removed values, by any route
# ---------------------------------------------------------------------------

def _test_legacy_tolerance_does_not_leak(failures: list[str]) -> None:
    for value in ("MERGER", "REVERSE_MERGER"):
        if value in dtc._VALID_V2_EVENT_TYPES:
            failures.append(f"enum: {value} is still an accepted v2_event_type")
        if _validate_ok(_result(v2_event_type=value, deal_type=value)):
            failures.append(f"validator: 0.9 output naming {value} as v2_event_type was accepted")
        # The `deal_type` alias is the obvious back door: v2_event_type omitted, legacy
        # field carrying the removed value, resolver falling back to it.
        if _validate_ok(_result(v2_event_type=None, deal_type=value)):
            failures.append(f"validator: {value} slipped through the deal_type alias path")
        if dtc._resolve_v2_event_type({"v2_event_type": None, "deal_type": value}) == value:
            failures.append(f"resolver: {value} resolved from the legacy deal_type field")

    # Legacy READ tolerance is deliberately retained where stored rows are consumed.
    for value in ("MERGER", "REVERSE_MERGER"):
        if value not in aggregate._MA_EVENT_TYPES:
            failures.append(f"aggregate: {value} dropped from _MA_EVENT_TYPES; stored rows "
                            f"would lose their transaction-size derivation")
    if "MERGER" not in aggregate._CONTROL_DEFAULT_TYPES:
        failures.append("aggregate: MERGER dropped from _CONTROL_DEFAULT_TYPES; stored rows "
                        "would lose the 100% pct default")


def _validate_ok(result: dict) -> bool:
    return dtc._validate(result) is None


# ---------------------------------------------------------------------------
# 4. Nothing else moved
# ---------------------------------------------------------------------------

def _test_other_event_types_untouched(failures: list[str]) -> None:
    untouched = ("SPIN_OFF", "SPLIT_OFF", "JOINT_VENTURE", "RECAPITALIZATION",
                 "VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT", "PIPE", "UNKNOWN")
    for event in untouched:
        if event not in dtc._VALID_V2_EVENT_TYPES:
            failures.append(f"enum: {event} was removed — S-B must not touch it")

        extra = {}
        if event in ("SPIN_OFF", "SPLIT_OFF"):
            extra = {"spin_split_type": event}
        elif event == "RECAPITALIZATION":
            extra = {"recap_type": "DIVIDEND"}
        if not _validate_ok(_result(v2_event_type=event, deal_type=event, **extra)):
            failures.append(f"validator: {event} no longer validates: "
                            f"{dtc._validate(_result(v2_event_type=event, deal_type=event, **extra))}")

        # No non-acquisition event may carry a combination structure.
        if _validate_ok(_result(v2_event_type=event, deal_type=event,
                                combination_structure="MERGER", **extra)):
            failures.append(f"validator: {event} accepted a combination_structure — the "
                            f"merger collapse has leaked into another event family")

    # PIPE protection is an allowlist, so a de-SPAC arriving as ACQUISITION stays protected.
    from lib.pipe_recognition import PIPE_OVERRIDABLE_EVENT_TYPES
    if "ACQUISITION" in PIPE_OVERRIDABLE_EVENT_TYPES:
        failures.append("pipe: ACQUISITION became PIPE-overridable — de-SPACs now arrive as "
                        "ACQUISITION and would be displaced by concurrent PIPE language")

    # is_merger_of_equals stays an ordinary-source characteristic on the HC path.
    if "is_merger_of_equals" not in {f for f, _ in aggregate._FIELDS}:
        failures.append("moe: is_merger_of_equals left the aggregation field set")
    hc = open(os.path.join(ROOT, "prompts", "high_confidence_extraction.md"),
              encoding="utf-8").read()
    if "is_merger_of_equals" not in hc:
        failures.append("moe: is_merger_of_equals left the HC prompt — S-B must preserve it, "
                        "not move it to the SEC/agreement path")


# ---------------------------------------------------------------------------
# 5. Contract and consumer
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    clf = open(os.path.join(ROOT, "prompts", "deal_type_classifier.md"), encoding="utf-8").read()
    _eq(failures, "classifier/stage version parity", dtc._VERSION, "0.9")
    if "**Version:** 0.9" not in clf:
        failures.append("classifier prompt: version line is not 0.9")
    if "combination_structure" not in clf:
        failures.append("classifier prompt: combination_structure absent")
    if '"combination_structure": "DE_SPAC"' not in clf:
        failures.append("classifier prompt: no worked DE_SPAC example")
    if '"combination_structure": "MERGER"' not in clf:
        failures.append("classifier prompt: no worked MERGER example")
    for phrase in ("share or asset purchase", "no longer valid values"):
        if phrase not in clf:
            failures.append(f"classifier prompt: missing boundary rule — {phrase!r}")

    # The consumer S-B is obliged to keep alive.
    summ = open(os.path.join(ROOT, "prompts", "deal_summary.md"), encoding="utf-8").read()
    if "combination_structure = DE_SPAC" not in summ:
        failures.append("deal_summary: de-SPAC framing not re-keyed onto combination_structure")
    if "\n- MERGER: stock combination" in summ:
        failures.append("deal_summary: the old MERGER framing rule survives and is now dead — "
                        "nothing emits that event type any more")
    if "{combination_structure}" not in summ:
        failures.append("deal_summary: user template does not receive combination_structure")
    import stages.summarize as summarize
    _eq(failures, "deal_summary/stage version parity", summarize._VERSION, "0.10")


def main() -> int:
    failures: list[str] = []
    _test_canonical_path(failures)
    _test_hierarchy(failures)
    _test_legacy_tolerance_does_not_leak(failures)
    _test_other_event_types_untouched(failures)
    _test_prompt_contract(failures)

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS combination_structure: canonical path holds, hierarchy queryable by "
          "implication, removed values rejected on output, other event families untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
