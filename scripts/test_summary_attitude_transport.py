#!/usr/bin/env python3
"""Guard tests for the Stage 12 summary input contract (V3 §T11, deal_summary 0.12).

S-A split the fused `hostile` boolean into `deal_attitude` and `approach_type`, and Stage 7
stopped writing `hostile`. Stage 12 kept sending `flags.hostile` anyway, reading a column
nothing populates: every new transaction reached the summary prompt asserting "not hostile",
including genuinely hostile ones, while the two canonical fields never arrived at all.

This is a TRANSPORT test, and it is deliberately not a prompt-content test. What can regress
is the dictionary Stage 12 builds and hands to the prompt template, so that dictionary is what
gets asserted -- by running the real `summarize.run()` against a temp database with
`call_prompt` intercepted, and reading the FLAGS block out of the user prompt that the
production template actually produced. Asserting on the prompt file's schema block instead
would pass even if `summarize.py` sent something else entirely, which is exactly the defect
this test exists to catch.

Four things are pinned:

  1. TRANSPORT.        HOSTILE arrives as HOSTILE, FRIENDLY as FRIENDLY, and each
                       approach_type value arrives as itself.
  2. NULL IS NULL.     A null attitude or approach stays null in the JSON. Coercing it to
                       false would recreate the original defect in a new field: absence of
                       hostile evidence is NOT FRIENDLY (§T11), and `approach_type` null is a
                       first-class outcome, not a denial.
  3. INDEPENDENCE.     The two fields are carried separately. Nothing derives, defaults or
                       infers one from the other -- including the combination Gate 2 found
                       on Kontron, HOSTILE with a null approach.
  4. RETIRED KEY GONE. `flags.hostile` is absent from both the transported dictionary and
                       the prompt's input contract. Its column and its history are untouched;
                       what ends is this prompt being told about it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection, init_db
import stages.summarize as summarize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_PROMPT = os.path.join(ROOT, "prompts", "deal_summary.md")


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_text: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Prompt and stage agree, and neither predates `minimum`.

    Not an equality check against a literal: pinning an exact version asserts the prompt is
    frozen and breaks on the next legitimate bump. Compared numerically because these are
    dotted decimals -- 0.12 > 0.9, which a string comparison inverts.
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


_SUMMARY_RESULT = {
    "summary_text": "x " * 90,
    "word_count": 90,
    "model_confidence": "HIGH",
    "notes": None,
    "prompt_version": None,          # filled per call from the stage's own version
}


def _run_stage12(conn, txn_id: str) -> dict:
    """Run the real Stage 12 and return the FLAGS dict it transported.

    `call_prompt` is intercepted rather than mocked away: the stage builds the user prompt
    from the production template exactly as it does in a real run, and the captured text is
    the same string the model would have seen.
    """
    captured: dict = {}
    real = summarize.call_prompt

    def _capture(**kw):
        captured["user_prompt"] = kw["user_prompt"]
        out = dict(_SUMMARY_RESULT)
        out["prompt_version"] = kw["prompt_version"]
        return out

    summarize.call_prompt = _capture
    summarize._SLEEP = 0.0
    try:
        summarize.run(conn, SimpleNamespace(log_level="ERROR"), "test_summary_transport")
    finally:
        summarize.call_prompt = real

    m = re.search(r"^FLAGS: (.+)$", captured.get("user_prompt", ""), re.M)
    if m is None:
        raise AssertionError(f"no FLAGS line in the transported prompt for {txn_id}")
    return json.loads(m.group(1))


def _seed(conn, txn_id: str, deal_attitude, approach_type, sponsor_role=None) -> None:
    conn.execute(
        """INSERT INTO transaction_record
               (transaction_id, is_current, deal_type, v2_event_type, target_name,
                acquirer_name, acquirer_type, announced_date, deal_attitude, approach_type,
                sponsor_transaction_role, hostile,
                competing_bid, regulatory_approvals_required, is_take_private, has_go_shop)
           VALUES (?, 1, 'ACQUISITION', 'ACQUISITION', 'Verity Biosciences',
                   'Halden Therapeutics', 'pe_portfolio', '2026-08-18', ?, ?, ?, 1, 0, 0, 0, 0)""",
        (txn_id, deal_attitude, approach_type, sponsor_role),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. Transport, null preservation and independence
# ---------------------------------------------------------------------------

# `hostile` is seeded as 1 in every row on purpose. It is the retired column, and a row
# where it disagrees with deal_attitude is the only way to prove the summary is reading the
# canonical field rather than the legacy one.
CASES = [
    ("hostile_reaches_prompt",        "HOSTILE",  "UNSOLICITED"),
    ("friendly_reaches_prompt",       "FRIENDLY", "SOLICITED"),
    ("null_attitude_stays_null",      None,       "UNSOLICITED"),
    ("null_approach_stays_null",      "HOSTILE",  None),
    ("both_null_stay_null",           None,       None),
]


# `acquirer_type` is seeded as 'pe_portfolio' on every row above, deliberately. Under the
# V2 rule that value alone meant "add-on"; V3 §T7 removes that derivation, so a row where the
# acquirer type would have implied ADD_ON while sponsor_transaction_role says otherwise is the
# only way to prove the summary reads the canonical field and not the proxy.
_SPONSOR_CASES = [
    ("sponsor_add_on_reaches_prompt", "ADD_ON"),
    ("sponsor_platform_reaches_prompt", "PLATFORM"),
    ("sponsor_null_stays_null", None),
]


def _test_sponsor_role_transport(failures: list[str]) -> None:
    for name, role in _SPONSOR_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            init_db(path)
            conn = get_connection(path)
            txn = f"tc_sum_{name}"
            _seed(conn, txn, "FRIENDLY", None, sponsor_role=role)
            try:
                flags = _run_stage12(conn, txn)
            except AssertionError as exc:
                failures.append(str(exc))
                conn.close()
                continue

            _eq(failures, f"{name}/sponsor_transaction_role",
                flags.get("sponsor_transaction_role", "<missing>"), role)
            if role is None and flags.get("sponsor_transaction_role") is False:
                failures.append(f"{name}: a null sponsor role was coerced to false — null means "
                                "no role is established, not that one is denied")
            if role is None and flags.get("sponsor_transaction_role") not in (None, "<missing>"):
                failures.append(f"{name}: a sponsor role appeared where the canonical value is "
                                "null — §T7 forbids deriving it from acquirer_type")
            conn.close()


def _test_transport(failures: list[str]) -> None:
    for name, attitude, approach in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            init_db(path)
            conn = get_connection(path)
            txn = f"tc_sum_{name}"
            _seed(conn, txn, attitude, approach)
            try:
                flags = _run_stage12(conn, txn)
            except AssertionError as exc:
                failures.append(str(exc))
                conn.close()
                continue

            _eq(failures, f"{name}/deal_attitude", flags.get("deal_attitude", "<missing>"),
                attitude)
            _eq(failures, f"{name}/approach_type", flags.get("approach_type", "<missing>"),
                approach)

            if attitude is None and flags.get("deal_attitude") is False:
                failures.append(f"{name}: null deal_attitude was coerced to false — "
                                "absence of hostile evidence is NOT FRIENDLY (§T11)")
            if approach is None and flags.get("approach_type") is False:
                failures.append(f"{name}: null approach_type was coerced to false — "
                                "null is a first-class outcome, not a denial")

            if "hostile" in flags:
                failures.append(f"{name}: retired flags.hostile is still transported "
                                f"({flags['hostile']!r}) — Stage 7 stopped writing it at §T11")

            # Independence: neither field may be filled in from the other.
            if attitude == "HOSTILE" and approach is None and flags.get("approach_type") is not None:
                failures.append(f"{name}: approach_type was inferred from HOSTILE — the two "
                                "dimensions are independent by decision")
            conn.close()


# ---------------------------------------------------------------------------
# 2. Neighbouring flags are untouched (control)
# ---------------------------------------------------------------------------

def _test_neighbours_unchanged(failures: list[str]) -> None:
    """The other three flags must survive the swap with their shapes intact.

    Without a control, a change that emptied or restructured the FLAGS block entirely would
    still satisfy every assertion above.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.db")
        init_db(path)
        conn = get_connection(path)
        _seed(conn, "tc_sum_control", "HOSTILE", None)
        try:
            flags = _run_stage12(conn, "tc_sum_control")
        except AssertionError as exc:
            failures.append(str(exc))
            conn.close()
            return
        for key in ("is_take_private", "competing_bid", "regulatory_approvals_required"):
            if key not in flags:
                failures.append(f"control: flags.{key} disappeared from the summary input")
            elif not isinstance(flags[key], bool):
                failures.append(f"control: flags.{key} is {type(flags[key]).__name__}, "
                                "expected bool — neighbouring flags keep their coercion")
        conn.close()


# ---------------------------------------------------------------------------
# 3. The prompt's own input contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(SUMMARY_PROMPT, encoding="utf-8").read()
    _check_version(failures, "deal_summary", text, summarize._VERSION, (0, 13),
                   "carried sponsor_transaction_role into the summary input")
    if '"hostile"' in text:
        failures.append("deal_summary prompt: the input contract still declares flags.hostile")
    for field in ("deal_attitude", "approach_type", "sponsor_transaction_role"):
        if f'"{field}"' not in text:
            failures.append(f"deal_summary prompt: {field} is missing from the input contract")
    # V3 §T7: sponsor role is carried by the canonical field, never inferred from buyer type.
    if "acquirer_type = pe_portfolio: add-on" in text:
        failures.append("deal_summary prompt: the retired pe_portfolio -> add-on inference is "
                        "still an active framing rule — §T7 replaced it with "
                        "sponsor_transaction_role")


def main() -> None:
    failures: list[str] = []
    _test_transport(failures)
    _test_sponsor_role_transport(failures)
    _test_neighbours_unchanged(failures)
    _test_prompt_contract(failures)

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"PASS  summary flags transport  ({len(CASES)} attitude/approach + "
          f"{len(_SPONSOR_CASES)} sponsor-role cases + control + prompt contract)")


if __name__ == "__main__":
    main()
