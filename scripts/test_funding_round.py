#!/usr/bin/env python3
"""Guard tests for canonical round, vc_stage and round_price_direction (§T14, §A6.3; S-E).

Three fields, two architectures, and the difference is the point:

  round_price_direction   EXTRACTED by Stage 4b, observed via FUNDING_FIELDS, aggregated.
                          Gets the full four-hop canonical-field gate.
  round                   DERIVED in Stage 9 from the aggregated round_label.
  vc_stage                DERIVED from `round`.

The derived pair is deliberately NOT in FUNDING_FIELDS. Creating observations for them
just to satisfy the generic extracted-field gate would assert a path that does not exist
by design -- the same shape V2's round_stage_category already used. They are proved
instead along the path they actually take: observed round_label -> Stage 9 -> canonical.

The V2 derivation carried four established defects, each pinned below:

    Series H and beyond -> null      the branch enumerated "series d".."series g" literally
    Series AA           -> EARLY_STAGE   "series a" is a substring of "series aa"
    Bridge Round        -> null      correct outcome, but by accident rather than by rule
    input was round_label            free text, never a canonical value

No test exercised that function at all before this one, which is how it stayed wrong.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
import stages.funding_hc_extract as fhc
from lib.observation_writer import (
    FUNDING_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNDING_PROMPT = os.path.join(ROOT, "prompts", "funding_hc_extraction.md")


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_text: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Prompt and stage agree, and neither predates `minimum`.

    Compared numerically, not as strings: these are dotted decimals, so 0.10 > 0.9 and a
    string comparison inverts it. Not pinned to an exact literal, which would assert the
    prompt is frozen and break on the next slice's legitimate bump.
    """
    m = re.search(r"^\*\*Version:\*\* (\d+)\.(\d+)", prompt_text, re.M)
    if m is None:
        failures.append(f"{label} prompt: no parseable version line")
        return
    _eq(failures, f"{label}/prompt-stage version parity",
        f"{m.group(1)}.{m.group(2)}", stage_version)
    if (int(m.group(1)), int(m.group(2))) < minimum:
        failures.append(f"{label} prompt: version {m.group(0)!r} predates the release that "
                        f"{what} ({minimum[0]}.{minimum[1]})")


def _fund(**over) -> dict:
    """A schema-valid single funding transaction; the round block is overridden per-test."""
    rnd = {"label": "Series B", "size": 4e7, "currency": "USD",
           "is_extension_round": False, "is_bridge_round": False,
           "round_price_direction": None}
    rnd.update(over)
    return {
        "company": {"name": "Northwind Robotics", "domain": None, "description": None},
        "investors": [],
        "round": rnd,
        "dates": {"announced_date": "2026-05-04", "announced_date_precision": "exact"},
        "financials_disclosure_status": "DISCLOSED",
        "model_confidence": "HIGH",
    }


# ---------------------------------------------------------------------------
# 1. Normalization — the bounded generative shape, and the four V2 defects
# ---------------------------------------------------------------------------

def _test_normalization(failures: list[str]) -> None:
    nr = getattr(aggregate, "_normalize_round", None)
    vs = getattr(aggregate, "_derive_vc_stage", None)
    if nr is None or vs is None:
        failures.append("aggregate: _normalize_round / _derive_vc_stage are not defined — "
                        "canonical round and vc_stage are not implemented")
        return

    cases = [
        # label,               round,        vc_stage
        ("Pre-Seed",           "PRE_SEED",   "PRE_SEED"),
        ("Seed",               "SEED",       "SEED"),
        ("Angel",              "ANGEL",      "SEED"),      # distinct round, SEED stage
        ("Series A",           "SERIES_A",   "EARLY_STAGE"),
        ("Series A-1",         "SERIES_A1",  "EARLY_STAGE"),
        ("Series A1",          "SERIES_A1",  "EARLY_STAGE"),
        ("Series A2",          "SERIES_A2",  "EARLY_STAGE"),   # NOT collapsed to SERIES_A
        ("Series B",           "SERIES_B",   "GROWTH"),
        ("Series C",           "SERIES_C",   "GROWTH"),
        ("Series D",           "SERIES_D",   "LATE_STAGE"),
        ("Series G",           "SERIES_G",   "LATE_STAGE"),
        ("Series H",           "SERIES_H",   "LATE_STAGE"),   # V2 defect: returned null
        ("Series I",           "SERIES_I",   "LATE_STAGE"),
        ("Series J",           "SERIES_J",   "LATE_STAGE"),
        ("Series AA",          None,         None),           # V2 defect: EARLY_STAGE
        ("Bridge Round",       None,         None),           # orthogonal, not a stage
        ("Convertible Note",   None,         None),           # instrument, not a round
        ("Venture Debt",       None,         None),           # event structure, not a round
        ("Seed Extension",     "SEED",       "SEED"),         # extension is orthogonal
        ("Series B extension", "SERIES_B",   "GROWTH"),
        ("Series A0",          None,         None),           # zero index is not a variant
        (None,                 None,         None),
    ]
    for label, want_round, want_stage in cases:
        _eq(failures, f"normalize/{label!r} -> round", nr(label), want_round)
        _eq(failures, f"derive/{label!r} -> vc_stage", vs(nr(label)), want_stage)

    # The derivation must read the canonical round, never the raw label. Passing a label
    # straight in must not produce a stage -- that is precisely the V2 mistake.
    _eq(failures, "derive/rejects raw label", vs("Series B"), None)


# ---------------------------------------------------------------------------
# 2. Four-hop canonical gate — round_price_direction (extracted)
# ---------------------------------------------------------------------------

def _test_price_direction_canonical_path(failures: list[str]) -> None:
    """Production FUNDING_FIELDS and production include_funding, configured read source.

    `round_size` is the control: an unchanged FUNDING_FIELDS member carried through the
    identical path. It is seeded and asserted whether or not round_price_direction
    resolves, so it cannot be skipped by the failure it exists to isolate.
    """
    if "round_price_direction" not in FUNDING_FIELDS:
        failures.append("observation/FUNDING_FIELDS: round_price_direction is absent, so "
                        "Stage 4b's include_funding write will not observe it and the "
                        "canonical column stays NULL")
    if "round_size" not in FUNDING_FIELDS:
        failures.append("observation/control: round_size missing from FUNDING_FIELDS")
    if "is_down_round" in FUNDING_FIELDS:
        failures.append("observation/FUNDING_FIELDS: is_down_round is still observed, but "
                        "Stage 4b no longer writes that column — it would be a permanent "
                        "NULL observation")

    db_path = os.path.join(tempfile.mkdtemp(), "fund.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        staging = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
        have = "round_price_direction" in staging
        if not have:
            failures.append("schema/staging_extraction: round_price_direction is missing — "
                            "migration 008 did not run")
        tr = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
        for col in ("round", "vc_stage", "round_price_direction"):
            if col not in tr:
                failures.append(f"schema/transaction_record: {col} is missing")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-05-04','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "event_history_type",
                "target_name", "round_label", "round_size", "announced_date",
                "announced_date_precision", "financials_disclosure_status",
                "model_confidence", "dt_prompt_version", "funding_prompt_version"
                if "funding_prompt_version" in staging else "hc_prompt_version",
                "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "VC_ROUND", "VC_ROUND", "ANNOUNCED",
                "Northwind Robotics", "Series H", 4e7, "2026-05-04", "exact",
                "DISCLOSED", "HIGH", "0.10", "0.2", "tc_fund_0001"]
        if have:
            cols.append("round_price_direction")
            vals.append("DOWN")
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Production writer, production flag — not a local reimplementation.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="FUNDING_HC_EXTRACT",
            include_stage3=True, include_funding=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        checks = [("round_size", 4e7), ("round_label", "Series H")]      # controls
        if have:
            checks.insert(0, ("round_price_direction", "DOWN"))
        for field, expected in checks:
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id='tc_fund_0001' AND field_name=?", (field,)).fetchone()
            if row is None:
                failures.append(f"observation/{field}: no observation row was written")
            else:
                got = row["field_value"]
                _eq(failures, f"observation/{field}",
                    float(got) if isinstance(expected, float) else got, expected)

        # The derived pair must NOT be observed. Asserting their absence is as load-bearing
        # as asserting the extracted field's presence: an observation here would mean the
        # derived architecture had silently become an extracted one.
        for derived in ("round", "vc_stage"):
            row = conn.execute(
                "SELECT 1 FROM transaction_field_observation"
                " WHERE transaction_id='tc_fund_0001' AND field_name=?", (derived,)).fetchone()
            if row is not None:
                failures.append(f"observation/{derived}: an observation exists, but this "
                                f"field is derived in Stage 9 and must not be observed")

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "fund-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id='tc_fund_0001'").fetchone()
        if canon is None:
            failures.append("canonical: no transaction_record row")
            return
        src = DEFAULT_AGGREGATION_READ_SOURCE
        _eq(failures, f"canonical/round_size CONTROL (read_source={src})",
            canon["round_size"], 4e7)
        _eq(failures, f"canonical/round_label CONTROL (read_source={src})",
            canon["round_label"], "Series H")
        if have:
            _eq(failures, f"canonical/round_price_direction (read_source={src})",
                canon["round_price_direction"], "DOWN")
        # Derived pair, end to end: observed label -> Stage 9 -> canonical. Series H is the
        # V2 ceiling defect, so this row is null on pre-S-E code.
        if "round" in tr:
            _eq(failures, "canonical/round derived from observed label",
                canon["round"], "SERIES_H")
        if "vc_stage" in tr:
            _eq(failures, "canonical/vc_stage derived from canonical round",
                canon["vc_stage"], "LATE_STAGE")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Vocabulary and the three-state write
# ---------------------------------------------------------------------------

def _test_price_direction_vocabulary(failures: list[str]) -> None:
    vocab = getattr(fhc, "_VALID_ROUND_PRICE_DIRECTION", None)
    if vocab is None:
        failures.append("vocab: stages.funding_hc_extract defines no "
                        "_VALID_ROUND_PRICE_DIRECTION — the field is not implemented")
        return
    _eq(failures, "vocab/exact", set(vocab), {"UP", "DOWN", "FLAT"})

    for good in ("UP", "DOWN", "FLAT", None):
        _eq(failures, f"validate/{good}", fhc._validate(_fund(round_price_direction=good)), None)
    for bad in ("DOWN_ROUND", "FLAT_ROUND", "UNKNOWN", "true"):
        if fhc._validate(_fund(round_price_direction=bad)) is None:
            failures.append(f"validate/{bad}: accepted, but it is not in the vocabulary")

    # null must survive as null. Coercing it would rebuild the defect this field replaces:
    # "not stated" would become indistinguishable from an asserted value.
    src = open(os.path.join(ROOT, "stages", "funding_hc_extract.py"), encoding="utf-8").read()
    if '1 if rd.get("round_price_direction")' in src:
        failures.append("stage: round_price_direction is coerced with `1 if ... else 0` — "
                        "that is the is_down_round defect being recreated")
    if 'rd.get("round_price_direction")' not in src:
        failures.append("stage: round_price_direction is not read from the round block")
    if "is_down_round = ?" in src:
        failures.append("stage: still writes the is_down_round column")


# ---------------------------------------------------------------------------
# 4. Bridge / extension stay orthogonal
# ---------------------------------------------------------------------------

def _test_orthogonal_characteristics(failures: list[str]) -> None:
    """Bridge and extension describe a round; they are not stages.

    'Bridge Round' yielding a null stage is the correct V3 answer -- but for a stated
    reason, not because no substring matched. A regression asserting a non-null stage here
    would pin the wrong behaviour, so the assertion is that the round is null while the
    orthogonal flag survives independently.
    """
    nr = getattr(aggregate, "_normalize_round", None)
    if nr is None:
        return
    _eq(failures, "orthogonal/bridge is not a round", nr("Bridge Round"), None)
    _eq(failures, "orthogonal/extension does not change the round", nr("Series B extension"),
        "SERIES_B")
    for f in ("is_bridge_round", "is_extension_round"):
        if f not in FUNDING_FIELDS:
            failures.append(f"orthogonal/{f}: dropped from FUNDING_FIELDS — S-E preserves "
                            f"these characteristics unchanged")


# ---------------------------------------------------------------------------
# 5. Prompt contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(FUNDING_PROMPT, encoding="utf-8").read()
    _check_version(failures, "funding_hc_extraction", text, fhc._VERSION,
                   (0, 2), "introduced round_price_direction")

    if "round_price_direction" not in text:
        failures.append("prompt: round_price_direction absent from the contract")
    if '"is_down_round"' in text:
        failures.append("prompt: an is_down_round key survives in the contract or an example")

    # The rule that keeps null meaningful, and the anti-inference rule.
    if "NOT the same as FLAT" not in text:
        failures.append("prompt: missing the rule distinguishing null from FLAT")
    if "Do NOT infer any value from valuation figures" not in text:
        failures.append("prompt: missing the valuation anti-inference rule")
    if '"round_price_direction": "DOWN"' not in text:
        failures.append("prompt: no worked example with a non-null direction")

    # Canonical round is derived, so it must not appear as a prompt field.
    if re.search(r'^\s*-\s*(canonical_)?round:\s*enum', text, re.M):
        failures.append("prompt: canonical round appears as an extracted field — it is a "
                        "deterministic normalization of round_label (§T14)")


def main() -> int:
    failures: list[str] = []
    _test_normalization(failures)
    _test_price_direction_canonical_path(failures)
    _test_price_direction_vocabulary(failures)
    _test_orthogonal_characteristics(failures)
    _test_prompt_contract(failures)

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("PASS funding round: bounded generative normalization, no Series-G ceiling, "
          "no AA collision, derived pair unobserved, price direction three-state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
