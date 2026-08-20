#!/usr/bin/env python3
"""Guard tests for the V3 attitude/approach split (inventory §T11, slice S-A).

V2 carried one boolean, `hostile`, defined as "hostile, unsolicited, OR subject to a
proxy contest" — three facts in one bit — and Stage 7 wrote `1 if flags.get("hostile")
else 0`, so a source that said nothing about posture stored exactly what an explicitly
friendly source stored: 0.

V3 replaces it with two independent nullable dimensions. The tests below pin the three
properties that make the split worth doing:

  1. NULL SURVIVES.        A source silent on posture must store NULL, never FRIENDLY.
                           This is the defect case: in V2 it was indistinguishable
                           from friendly, and no test caught it.
  2. THE AXES ARE FREE.    `UNSOLICITED` + `FRIENDLY` must be storable together. That
                           combination is unrepresentable in a single enum and is the
                           whole reason attitude and approach are separate fields.
  3. THE DIVERGENCE HOLDS. `competing_bid` deliberately keeps V2's coerce-to-boolean
                           behaviour. It names one fact whose prompt contract says
                           "false otherwise", so it is not a three-state field. Adjacent
                           lines in the stage now behave differently on purpose; this
                           test is what stops that from being "tidied up" later.

These are structural regressions over the validator, the write path and the Stage 9
carry-through. They do NOT validate extraction quality — that requires the prompt and
model against real source text, which is a separate gate.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stages.low_confidence_extract import (
    _VALID_APPROACH_TYPE,
    _VALID_DEAL_ATTITUDE,
    _VERSION,
    _validate,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LC_PROMPT_PATH = os.path.join(ROOT, "prompts", "low_confidence_extraction.md")


def _assert_equal(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _result(**flags) -> dict:
    """A schema-valid LC result whose flags block is overridden per-test."""
    base = {
        "includes_earnout": False,
        "deal_attitude": None,
        "approach_type": None,
        "competing_bid": False,
        "regulatory_approvals_required": False,
    }
    base.update(flags)
    return {
        "advisors": [],
        "consideration_components": [],
        "flags": base,
        "go_shop": {"has_go_shop": False, "go_shop_period_days": None},
        "termination_fees": {},
        "model_confidence": "HIGH",
    }


# ---------------------------------------------------------------------------
# 1. Validator: null is valid; out-of-vocabulary is not
# ---------------------------------------------------------------------------

def _test_validator(failures: list[str]) -> None:
    _assert_equal(failures, "validator/both_null", _validate(_result()), None)
    _assert_equal(failures, "validator/friendly",
                  _validate(_result(deal_attitude="FRIENDLY")), None)
    _assert_equal(failures, "validator/hostile_unsolicited",
                  _validate(_result(deal_attitude="HOSTILE", approach_type="UNSOLICITED")), None)

    # The removed v0.4.1 value must not slip back in through the attitude field.
    if _validate(_result(deal_attitude="UNSOLICITED")) is None:
        failures.append("validator/attitude_unsolicited: UNSOLICITED must be rejected as an "
                        "attitude — it is an approach (§T11)")
    if _validate(_result(deal_attitude="NEUTRAL")) is None:
        failures.append("validator/attitude_neutral: NEUTRAL is not a V3 value (§T11)")
    if _validate(_result(approach_type="FRIENDLY")) is None:
        failures.append("validator/approach_friendly: FRIENDLY must be rejected as an approach")

    _assert_equal(failures, "vocab/attitude", set(_VALID_DEAL_ATTITUDE), {"FRIENDLY", "HOSTILE"})
    _assert_equal(failures, "vocab/approach", set(_VALID_APPROACH_TYPE),
                  {"SOLICITED", "UNSOLICITED"})


# ---------------------------------------------------------------------------
# 2. Write path: the three-state property, against a real table
# ---------------------------------------------------------------------------

_WRITE_SQL = """
    UPDATE staging_extraction SET
        deal_attitude = ?, approach_type = ?, competing_bid = ?
    WHERE extraction_id = ?
"""


def _write(conn: sqlite3.Connection, eid: int, flags: dict) -> tuple:
    """Mirror of the Stage 7 write for these three fields, exactly as the stage does it."""
    conn.execute(_WRITE_SQL, (
        flags.get("deal_attitude"),                 # three-state: None stays None
        flags.get("approach_type"),                 # three-state: None stays None
        1 if flags.get("competing_bid") else 0,     # coerced boolean, by decision
        eid,
    ))
    return conn.execute(
        "SELECT deal_attitude, approach_type, competing_bid FROM staging_extraction "
        "WHERE extraction_id = ?", (eid,)
    ).fetchone()


def _test_write_path(failures: list[str]) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("""
            CREATE TABLE staging_extraction (
                extraction_id INTEGER PRIMARY KEY,
                deal_attitude TEXT,
                approach_type TEXT,
                competing_bid INTEGER
            )
        """)
        for eid in (1, 2, 3, 4):
            conn.execute("INSERT INTO staging_extraction (extraction_id) VALUES (?)", (eid,))

        # THE DEFECT CASE: source establishes nothing.
        row = _write(conn, 1, {})
        _assert_equal(failures, "write/silent_attitude_is_null", row[0], None)
        _assert_equal(failures, "write/silent_approach_is_null", row[1], None)
        if row[0] == "FRIENDLY":
            failures.append("write/silent: silence was recorded as FRIENDLY — this is the "
                            "V2 defect the slice exists to remove")

        # THE INDEPENDENCE CASE: unsolicited *and* friendly. One enum cannot say this.
        row = _write(conn, 2, {"deal_attitude": "FRIENDLY", "approach_type": "UNSOLICITED"})
        _assert_equal(failures, "write/unsolicited_and_friendly", (row[0], row[1]),
                      ("FRIENDLY", "UNSOLICITED"))

        row = _write(conn, 3, {"deal_attitude": "HOSTILE"})
        _assert_equal(failures, "write/hostile", (row[0], row[1]), ("HOSTILE", None))

        # THE DELIBERATE DIVERGENCE: competing_bid still coerces.
        row = _write(conn, 4, {})
        _assert_equal(failures, "write/competing_bid_coerces_to_zero", row[2], 0)
        row = _write(conn, 4, {"competing_bid": True})
        _assert_equal(failures, "write/competing_bid_true", row[2], 1)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Stage 9 carry-through: declared, selected, written, aligned
# ---------------------------------------------------------------------------

def _test_aggregation_wiring(failures: list[str]) -> None:
    from stages.aggregate import (
        _FIELDS,
        _STAGE9_OWNED_COLUMNS,
        _TRANSACTION_RECORD_UPSERT_SQL,
    )
    declared = {name for name, _ in _FIELDS}
    for field in ("deal_attitude", "approach_type"):
        if field not in declared:
            failures.append(f"aggregate/_FIELDS: {field} not declared — it would be dropped "
                            f"silently between staging and transaction_record")
        if field not in _STAGE9_OWNED_COLUMNS:
            failures.append(f"aggregate/_STAGE9_OWNED_COLUMNS: {field} missing")

    # The upsert derives its placeholders from the column tuple; a mismatch here means
    # every canonical field after the insertion point is written to the wrong column.
    _assert_equal(failures, "aggregate/placeholder_alignment",
                  _TRANSACTION_RECORD_UPSERT_SQL.count("?"), len(_STAGE9_OWNED_COLUMNS))

    # `hostile` is retained on purpose: existing rows keep their values and V2 `hostile = 0`
    # must never be read as FRIENDLY. Retention is the decision, so it is asserted.
    if "hostile" not in _STAGE9_OWNED_COLUMNS:
        failures.append("aggregate: legacy `hostile` column was removed — S-A retains it "
                        "unwritten; removal is migration work that is out of scope")


# ---------------------------------------------------------------------------
# 4. Prompt/stage contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(LC_PROMPT_PATH, encoding="utf-8").read()

    _assert_equal(failures, "prompt/stage_version_parity", _VERSION, "0.6")
    if "**Version:** 0.6" not in text:
        failures.append("prompt: version line is not 0.6")

    # The fused field must be gone from the contract, not merely supplemented.
    if '"hostile"' in text:
        failures.append("prompt: a `hostile` key survives in the contract or an example")

    for field in ("deal_attitude", "approach_type"):
        if field not in text:
            failures.append(f"prompt: {field} absent from the contract")

    # The rule that distinguishes V3 from V2: silence is not friendliness.
    if "NOT FRIENDLY" not in text:
        failures.append("prompt: missing the explicit rule that absence of hostile evidence "
                        "is not FRIENDLY — without it the model reproduces the V2 default")
    if "Do NOT infer FRIENDLY merely because discussions or negotiations occurred" not in text:
        failures.append("prompt: missing the negotiations-are-not-agreement rule")

    # approach_type describes ORIGIN and must not be inferred from the absence of a
    # disclosed process, nor from the deal having become friendly. Both negative rules are
    # load-bearing: without them the model defaults an unstated origin to UNSOLICITED.
    if "Do not infer either value from the absence of the other" not in text:
        failures.append("prompt: missing the approach_type non-inference rule — without it the "
                        "model defaults an unstated approach to UNSOLICITED")
    if "approach_type is independent of deal_attitude" not in text:
        failures.append("prompt: missing the explicit independence statement for approach_type")

    # The Seer pair is what teaches independence by counterexample: same approach, different
    # posture. If either half is dropped the lesson silently becomes "unsolicited = hostile".
    if text.count('"approach_type": "UNSOLICITED"') < 2:
        failures.append("prompt: fewer than two worked UNSOLICITED examples — the pair that "
                        "separates approach from attitude is incomplete")
    if '"deal_attitude": "HOSTILE"' not in text:
        failures.append("prompt: no worked HOSTILE example")
    if '"deal_attitude": null,\n    "approach_type": "UNSOLICITED"' not in text:
        failures.append("prompt: no worked UNSOLICITED-with-null-attitude example — this is "
                        "the case V2's fused boolean got wrong")

    # §T11.1: proxy contest is not promoted, so it must not reappear as a captured fact.
    if "proxy contest" in text.lower() and "not carried forward" not in text.lower():
        failures.append("prompt: proxy contest appears as a captured fact — §T11.1 does not "
                        "promote it to V3")


# ---------------------------------------------------------------------------
# 5. Stage source guard
#
# _test_write_path above mirrors the stage's write rather than calling it, because
# invoking Stage 7 requires a live model call. A mirror proves the pattern is sound but
# would happily keep passing if the real stage regressed. This reads the stage source and
# pins the one line that matters: the two new fields must be passed through, and must NOT
# be wrapped in the `1 if ... else 0` coercion that the V2 `hostile` write used.
# ---------------------------------------------------------------------------

LC_STAGE_PATH = os.path.join(ROOT, "stages", "low_confidence_extract.py")


def _test_stage_source_guard(failures: list[str]) -> None:
    src = open(LC_STAGE_PATH, encoding="utf-8").read()

    for field in ("deal_attitude", "approach_type"):
        if f'flags.get("{field}")' not in src:
            failures.append(f"stage: {field} is not read from the flags block")
        if f'1 if flags.get("{field}")' in src:
            failures.append(f"stage: {field} is coerced with `1 if ... else 0` — that is the "
                            f"V2 defect; None must stay None")

    # The fused field must no longer be written.
    if 'hostile = ?' in src:
        failures.append("stage: still writes the `hostile` column; S-A stops writing it")

    # competing_bid must keep its coercion — the deliberate divergence.
    if '1 if flags.get("competing_bid") else 0' not in src:
        failures.append("stage: competing_bid lost its boolean coercion — it is a boolean by "
                        "decision, not a three-state field")


def main() -> int:
    failures: list[str] = []
    _test_validator(failures)
    _test_write_path(failures)
    _test_aggregation_wiring(failures)
    _test_prompt_contract(failures)
    _test_stage_source_guard(failures)

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS attitude/approach: null survives, axes independent, competing_bid unchanged, "
          "Stage 9 aligned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
