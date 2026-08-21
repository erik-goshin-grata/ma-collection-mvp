#!/usr/bin/env python3
"""Prompt provenance is caller-owned: the model neither authors nor echoes it.

Every stage already knows the authoritative prompt name and version -- it passes
`_FULL_VERSION` into `call_prompt`, and `register_prompt_version` records the prompt file's
hash. The model adds nothing to that, and asking it to supply the value creates a way for
provenance to be wrong.

Two places persisted the model's answer instead of the caller's:

  aggregation_conflict_log.prompt_version   the audit row for every LLM-resolved field
                                            conflict, written from result["prompt_version"]
  source_raw.notes -> relevancy.prompt_version   written the same way

Neither prompt supplies the version in its user template, and nothing validates the value
that comes back, so the model's only available source was the worked example in the prompt
-- which is how `aggregation_conflict_log` came to record `aggregation:0.4` while the
prompt was at 0.5. That is not a cosmetic drift: it is the provenance column of an audit
table naming a prompt version that did not run.

Two other stages required the key merely to be present, so an otherwise valid response was
rejected for omitting a field the caller already knew.

What this pins:

  1. THE AGGREGATION AUDIT ROW takes the caller's version even when the model returns a
     different one. Driven through the real conflict path: two same-tier, equal-confidence
     sources disagreeing on one field, which is the only way `_call_agg_prompt` is reached.
  2. THE RELEVANCY NOTE does the same, through the production writer.
  3. SUMMARY AND RATIONALE accept a response with no `prompt_version` key at all.
  4. NO PRODUCTION STAGE reads `result["prompt_version"]` for anything.

Run from project root:
    python scripts/test_prompt_provenance_caller_owned.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
import stages.rationale_tag as rationale_tag
import stages.relevancy_filter as relevancy_filter
import stages.summarize as summarize
from lib.observation_writer import (
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

TXN = "tc_prov_0001"
# What a model might return: plausible, wrong, and exactly what a stale worked example
# would have taught it to say.
_MODEL_INVENTED = "aggregation:0.1-invented-by-the-model"


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


# ---------------------------------------------------------------------------
# 1. The aggregation audit row — through a real same-tier conflict
# ---------------------------------------------------------------------------

def _seed_conflicting_sources(conn) -> None:
    """Two T2 sources, equal confidence, disagreeing on value_amount.

    This is the shape that reaches the aggregation prompt at all: `_pick_value` drops
    nulls, resolves single-tier agreement deterministically, and breaks ties on confidence
    first. Only a same-tier, same-confidence disagreement escalates.
    """
    now = datetime.now(timezone.utc).isoformat()
    for i, amount in enumerate((500_000_000.0, 485_000_000.0), start=1):
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T2',?,?, '2026-08-18','body','RELEVANT',?)",
            (f"u{i}", f"t{i}", now))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO staging_extraction
                   (source_raw_id, status, deal_type, v2_event_type, event_history_type,
                    target_status, target_type, target_type_v2, target_name, acquirer_name,
                    value_amount, value_currency, value_type, announced_date,
                    announced_date_precision, financials_disclosure_status, model_confidence,
                    dt_prompt_version, hc_prompt_version, transaction_cluster_id)
               VALUES (?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED', 'PRIVATE',
                       'standalone_company', 'standalone_company', 'Verity Biosciences',
                       'Halden Therapeutics', ?, 'USD', 'TRANSACTION_VALUE', '2026-08-18',
                       'exact', 'DISCLOSED', 'HIGH', '0.12', '0.22', ?)""",
            (srid, amount, TXN))
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
    backfill_observation_transaction_ids(conn)
    conn.commit()


def _test_aggregation_conflict_row(failures: list[str]) -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "prov.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        _seed_conflicting_sources(conn)

        called = {"n": 0}
        real = aggregate.call_prompt

        def _fake(**kw):
            called["n"] += 1
            return {
                "chosen_observation_id": None,   # filled below from the real observations
                "chosen_value": 485_000_000.0,
                "aggregation_confidence": "HIGH",
                "reasoning": "test",
                "flagged_for_review": False,
                "conflict_severity": "MINOR",
                "notes": None,
                "prompt_version": _MODEL_INVENTED,
            }

        # `_call_agg_prompt` rejects a chosen_observation_id outside the supplied set, so the
        # stub has to pick a real one. Wrap the caller to learn the ids it passed.
        real_call_agg = aggregate._call_agg_prompt

        def _wrapped(field_name, field_type, deal_context, observations, *a, **k):
            def _stub(**kw):
                r = _fake(**kw)
                r["chosen_observation_id"] = observations[0]["observation_id"]
                return r
            aggregate.call_prompt = _stub
            try:
                return real_call_agg(field_name, field_type, deal_context, observations, *a, **k)
            finally:
                aggregate.call_prompt = real

        aggregate._call_agg_prompt = _wrapped
        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        try:
            aggregate.run(conn, cfg, "provenance-test")
        finally:
            aggregate._call_agg_prompt = real_call_agg
            aggregate.call_prompt = real
        conn.commit()

        rows = conn.execute(
            "SELECT field_name, prompt_version FROM aggregation_conflict_log"
            " WHERE transaction_id=?", (TXN,)).fetchall()
        if not rows:
            failures.append("fixture: no aggregation conflict was logged — the two seeded "
                            "sources did not produce a same-tier escalation, so this test "
                            "proves nothing")
            return
        for row in rows:
            if row["prompt_version"] == _MODEL_INVENTED:
                failures.append(
                    f"aggregation_conflict_log/{row['field_name']}: the audit row records the "
                    f"MODEL's {row['prompt_version']!r}. Provenance must be the caller's "
                    f"{aggregate._FULL_VERSION!r} — the model is never told which version ran")
            else:
                _eq(failures, f"aggregation_conflict_log/{row['field_name']}",
                    row["prompt_version"], aggregate._FULL_VERSION)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. The relevancy note — through the production writer
# ---------------------------------------------------------------------------

def _test_relevancy_note(failures: list[str]) -> None:
    db_path = os.path.join(tempfile.mkdtemp(), "rel.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T2','u','t','2026-08-18','body','PENDING',?)", (now,))
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        relevancy_filter._write(conn, sid, "RELEVANT", None, {
            "classification": "RELEVANT",
            "reason_code": "ACQUISITION_ANNOUNCEMENT",
            "model_confidence": "HIGH",
            "notes": None,
            "prompt_version": "relevancy_filter:0.1-invented-by-the-model",
        })
        notes = conn.execute("SELECT notes FROM source_raw WHERE source_raw_id=?",
                             (sid,)).fetchone()["notes"]
        got = (json.loads(notes) or {}).get("relevancy", {}).get("prompt_version")
        if got and got.endswith("invented-by-the-model"):
            failures.append(f"relevancy note: records the MODEL's {got!r}. Provenance must be "
                            f"the caller's {relevancy_filter._FULL_VERSION!r}")
        else:
            _eq(failures, "relevancy note/prompt_version", got, relevancy_filter._FULL_VERSION)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Summary and rationale must not require the key
# ---------------------------------------------------------------------------

def _test_required_keys(failures: list[str]) -> None:
    if "prompt_version" in summarize._REQUIRED_KEYS:
        failures.append("summarize: prompt_version is still a required response key — the "
                        "caller already knows the version, so demanding it rejects otherwise "
                        "valid responses for omitting a field the model should not author")
    if "prompt_version" in rationale_tag._REQUIRED_KEYS:
        failures.append("rationale_tag: prompt_version is still a required response key")

    err = summarize._validate({"summary_text": "x " * 90, "word_count": 90,
                               "model_confidence": "HIGH", "notes": None})
    if err is not None:
        failures.append(f"summarize._validate rejected a valid response with no "
                        f"prompt_version: {err}")
    err = rationale_tag._validate({"rationale": "GEOGRAPHIC_EXPANSION", "secondary_rationales": [],
                                   "supporting_excerpt_index": 0, "model_confidence": "HIGH",
                                   "notes": None})
    if err is not None:
        failures.append(f"rationale_tag._validate rejected a valid response with no "
                        f"prompt_version: {err}")


# ---------------------------------------------------------------------------
# 4. No stage reads the model's value for anything
# ---------------------------------------------------------------------------

_READ_RE = re.compile(r"""result(?:\.get\(|\[)["']prompt_version["']""")


def _test_no_stage_reads_model_value(failures: list[str]) -> None:
    stages_dir = os.path.join(ROOT, "stages")
    for name in sorted(os.listdir(stages_dir)):
        if not name.endswith(".py"):
            continue
        text = open(os.path.join(stages_dir, name), encoding="utf-8").read()
        for m in _READ_RE.finditer(text):
            lineno = text[:m.start()].count("\n") + 1
            failures.append(f"stages/{name} L{lineno}: reads the model's prompt_version. "
                            "Provenance is caller-owned — use _FULL_VERSION")


def main() -> None:
    failures: list[str] = []
    _test_aggregation_conflict_row(failures)
    _test_relevancy_note(failures)
    _test_required_keys(failures)
    _test_no_stage_reads_model_value(failures)

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS  prompt provenance is caller-owned  (aggregation audit row via a real "
          "same-tier conflict, relevancy note, required-key removal, no stage reads)")


if __name__ == "__main__":
    main()
