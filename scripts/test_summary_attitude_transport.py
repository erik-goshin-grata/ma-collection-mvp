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
from prompts.base import load_prompt_file

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


def _capture_prompt(conn) -> str:
    """Same real-Stage-12 mechanism as _run_stage12, returning the whole user prompt.

    The funding assertions need more than one line of it, and reading the transported text
    is the point: a parallel summary implementation would prove nothing about what the
    production template actually emits.
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
        summarize.run(conn, SimpleNamespace(log_level="ERROR"), "test_summary_funding")
    finally:
        summarize.call_prompt = real
    return captured.get("user_prompt", "")


def _funding_block(prompt_text: str, txn_id: str) -> dict:
    m = re.search(r"^FUNDING: (.+)$", prompt_text, re.M)
    if m is None:
        raise AssertionError(f"no FUNDING line in the transported prompt for {txn_id}")
    return json.loads(m.group(1))


def _disclosure_line(prompt_text: str, txn_id: str) -> str:
    m = re.search(r"^FINANCIALS DISCLOSURE: (.+)$", prompt_text, re.M)
    if m is None:
        raise AssertionError(f"no FINANCIALS DISCLOSURE line in the transported "
                             f"prompt for {txn_id}")
    return m.group(1).strip()


def _seed(conn, txn_id: str, deal_attitude, approach_type, sponsor_role=None) -> None:
    conn.execute(
        """INSERT INTO transaction_record
               (transaction_id, is_current, deal_type, v2_event_type, target_name,
                acquirer_name, acquirer_type, announced_date, deal_attitude, approach_type,
                sponsor_transaction_role, hostile,
                competing_bid, regulatory_approvals_required, is_take_private, has_go_shop,
                -- Explicit NULLs: both carry DEFAULT 0 in schema/003_funding_path.sql, but
                -- Stage 9 writes them from field_values.get(...), so a real ACQUISITION row
                -- has NULL here, not 0. Omitting them would seed a value production never
                -- writes and make the control assert against SQLite's default.
                is_extension_round, is_bridge_round)
           VALUES (?, 1, 'ACQUISITION', 'ACQUISITION', 'Verity Biosciences',
                   'Halden Therapeutics', 'pe_portfolio', '2026-08-18', ?, ?, ?, 1, 0, 0, 0, 0,
                   NULL, NULL)""",
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
# 2b. Funding round facts reach the summary (deal_summary 0.16)
# ---------------------------------------------------------------------------
#
# The canonical funding fields were always fetched -- summarize.py runs SELECT tr.* -- and
# then dropped, because the user template had no funding placeholder. Funding events also
# derive no transaction value by design, so the VALUE block arrived null and the model read
# VALUE FRAMING's UNDISCLOSED line, asserting "Financial terms were not disclosed" on rounds
# whose size, valuation and total-raised were all correctly stored. Four of seven funding
# transactions in the PL integration run said exactly that.
#
# The anchors below are those live cases, with their real figures. They pin the three facts
# most easily conflated -- this round's size, the cumulative total, and a separate facility
# -- as distinct values in the transported block, plus the null-preservation rule that a
# fact which is not established arrives as JSON null rather than 0 or false.

_FUNDING_COLUMNS = (
    "round_label", "round", "vc_stage", "round_size", "round_currency",
    "pre_money_valuation", "post_money_valuation", "valuation_currency",
    "facility_size", "total_raised_to_date", "round_price_direction",
    "is_extension_round", "is_bridge_round", "use_of_proceeds",
)

# (label, v2_event_type, disclosure, {canonical funding values}, {expected in prompt})
_FUNDING_CASES = [
    (
        "castelion_round_plus_facility_plus_post_money", "VC_ROUND", "DISCLOSED",
        {"round_label": "Series C", "round": "SERIES_C", "vc_stage": "LATE_VC",
         "round_size": 800000000.0, "round_currency": "USD",
         "facility_size": 250000000.0, "post_money_valuation": 13000000000.0,
         "valuation_currency": "USD"},
        # round_size and facility_size must arrive as two separate figures. Summing them
        # ($1.05B) or reporting either alone is the failure this case exists to catch.
        {"round_size": 800000000.0, "facility_size": 250000000.0,
         "post_money_valuation": 13000000000.0, "round_label": "Series C"},
    ),
    (
        "rillet_round_plus_post_money_plus_total_raised", "VC_ROUND", "DISCLOSED",
        {"round_label": "Series C", "round": "SERIES_C", "round_size": 100000000.0,
         "round_currency": "USD", "post_money_valuation": 1000000000.0,
         "valuation_currency": "USD", "total_raised_to_date": 200000000.0},
        # total_raised_to_date is cumulative and must not stand in for round_size.
        {"round_size": 100000000.0, "total_raised_to_date": 200000000.0,
         "post_money_valuation": 1000000000.0},
    ),
    (
        "kynexis_extension_round_eur", "VC_ROUND", "DISCLOSED",
        {"round_label": "Series A Extension", "round": "SERIES_A",
         "round_size": 40000000.0, "round_currency": "EUR",
         "total_raised_to_date": 97000000.0, "is_extension_round": 1},
        # A true extension flag must survive as a positive fact, and the currency must be
        # the round's own, not defaulted.
        {"round_size": 40000000.0, "round_currency": "EUR",
         "total_raised_to_date": 97000000.0, "is_extension_round": 1},
    ),
    (
        "tiger_sparse_round", "VC_ROUND", "DISCLOSED",
        {"round_label": "Series A", "round": "SERIES_A", "round_size": 10000000.0,
         "round_currency": "USD"},
        # The sparse case. Everything unstated must arrive as null -- not 0, not false.
        {"round_size": 10000000.0, "post_money_valuation": None,
         "facility_size": None, "total_raised_to_date": None,
         "is_extension_round": None, "is_bridge_round": None,
         "round_price_direction": None, "use_of_proceeds": None},
    ),
]


def _seed_funding(conn, txn_id: str, event_type: str, disclosure, values: dict) -> None:
    # Every funding column is written EXPLICITLY, including the ones left unset. Stage 9
    # owns these columns and writes field_values.get(...) for each, so an unobserved fact
    # reaches canonical as NULL. staging_extraction and transaction_record both declare
    # is_extension_round / is_bridge_round with DEFAULT 0 (schema/003_funding_path.sql), so
    # omitting them here would seed a 0 the production writer never produces and would test
    # SQLite's default rather than this stage's transport.
    cols = ["transaction_id", "is_current", "deal_type", "v2_event_type", "target_name",
            "announced_date", "financials_disclosure_status"]
    vals = [txn_id, 1, event_type, event_type, "Castelion", "2026-08-18", disclosure]
    for col in _FUNDING_COLUMNS:
        cols.append(col)
        vals.append(values.get(col))
    conn.execute(
        f"INSERT INTO transaction_record ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})", vals)
    conn.commit()


def _test_funding_transport(failures: list[str]) -> None:
    for label, event_type, disclosure, values, expected in _FUNDING_CASES:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.db")
            init_db(path)
            conn = get_connection(path)
            txn = f"tc_fund_{label}"
            _seed_funding(conn, txn, event_type, disclosure, values)
            try:
                text = _capture_prompt(conn)
                funding = _funding_block(text, txn)
                got_disclosure = _disclosure_line(text, txn)
            except AssertionError as exc:
                failures.append(f"{label}: {exc}")
                conn.close()
                continue
            for key in _FUNDING_COLUMNS:
                if key not in funding:
                    failures.append(f"{label}: FUNDING block is missing {key!r}")
            for key, want in expected.items():
                _eq(failures, f"{label}.{key}", funding.get(key, "<absent>"), want)
            _eq(failures, f"{label}.financials_disclosure", got_disclosure, disclosure)
            conn.close()


def _test_stored_false_transports_as_false(failures: list[str]) -> None:
    """A stored 0 must arrive as 0 -- uncoerced in the other direction too.

    The funding extractor declares is_extension_round / is_bridge_round as plain booleans
    and its worked examples emit `false`, so 0 here is an AUTHORED negative rather than an
    absent one. The transport must not launder it into null any more than it may turn null
    into false. What stops a stored 0 becoming a false claim is the prompt rule -- false
    licenses silence, never an affirmative "this was not an extension" -- not the transport.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "z.db")
        init_db(path)
        conn = get_connection(path)
        _seed_funding(conn, "tc_fund_stored_false", "VC_ROUND", "DISCLOSED",
                      {"round_label": "Series B", "round_size": 25000000.0,
                       "is_extension_round": 0, "is_bridge_round": 0})
        try:
            funding = _funding_block(_capture_prompt(conn), "tc_fund_stored_false")
        except AssertionError as exc:
            failures.append(str(exc))
            conn.close()
            return
        _eq(failures, "stored_false.is_extension_round", funding.get("is_extension_round"), 0)
        _eq(failures, "stored_false.is_bridge_round", funding.get("is_bridge_round"), 0)
        conn.close()


def _test_funding_nulls_are_not_falsey(failures: list[str]) -> None:
    """An unestablished funding fact must arrive as null, never coerced to 0/false.

    This is the has_go_shop mistake: bool(None) is False, and a summary told `false` will
    state the negative as fact. json.dumps writes None as null only if nothing coerces it
    on the way, so the assertion is on the transported JSON, not on the database row.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "n.db")
        init_db(path)
        conn = get_connection(path)
        _seed_funding(conn, "tc_fund_all_null", "VC_ROUND", "UNKNOWN", {})
        try:
            funding = _funding_block(_capture_prompt(conn), "tc_fund_all_null")
        except AssertionError as exc:
            failures.append(str(exc))
            conn.close()
            return
        for key in _FUNDING_COLUMNS:
            if funding.get(key, "<absent>") is not None:
                failures.append(f"null-preservation: FUNDING.{key} arrived as "
                                f"{funding.get(key, '<absent>')!r}, expected null — a fact "
                                "that is not established must not become 0 or false")
        conn.close()


def _test_non_funding_control(failures: list[str]) -> None:
    """A control transaction must be untouched by the funding change.

    Its FUNDING block must be present and entirely null, and the three neighbouring blocks
    must keep the exact shapes they had before 0.16. Without this, a change that populated
    FUNDING from the wrong columns, or disturbed FLAGS/GO-SHOP/FEES, would still pass.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.db")
        init_db(path)
        conn = get_connection(path)
        _seed(conn, "tc_sum_fund_control", "HOSTILE", None)
        try:
            text = _capture_prompt(conn)
            funding = _funding_block(text, "tc_sum_fund_control")
        except AssertionError as exc:
            failures.append(str(exc))
            conn.close()
            return
        for key in _FUNDING_COLUMNS:
            if funding.get(key, "<absent>") is not None:
                failures.append(f"control: FUNDING.{key} is populated on an ACQUISITION "
                                f"({funding.get(key)!r}) — funding fields must stay null")
        for block in ("FLAGS: ", "GO-SHOP: ", "TERMINATION FEES: "):
            if block not in text:
                failures.append(f"control: the {block.strip()} block vanished from the "
                                "summary input")
        flags = json.loads(re.search(r"^FLAGS: (.+)$", text, re.M).group(1))
        for key in ("is_take_private", "competing_bid", "regulatory_approvals_required"):
            if not isinstance(flags.get(key), bool):
                failures.append(f"control: flags.{key} lost its bool shape at 0.16")
        conn.close()


# ---------------------------------------------------------------------------
# 3. The prompt's own input contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(SUMMARY_PROMPT, encoding="utf-8").read()
    _check_version(failures, "deal_summary", text, summarize._VERSION, (0, 16),
                   "carried the canonical funding fields into the summary input")
    if '"hostile"' in text:
        failures.append("deal_summary prompt: the input contract still declares flags.hostile")
    for field in ("deal_attitude", "approach_type", "sponsor_transaction_role"):
        if f'"{field}"' not in text:
            failures.append(f"deal_summary prompt: {field} is missing from the input contract")
    # V3 §T7: sponsor role is carried by the canonical field, never inferred from buyer type.
    # Stale active example / framing. Changelog rows may name retired values; worked
    # examples and framing rules may not present them as current.
    body = text.split("## 9. Versioning")[0] if "## 9. Versioning" in text else text
    if "TARGET TYPE: spinco" in body:
        failures.append("deal_summary prompt: a worked example still supplies "
                        "TARGET TYPE: spinco — V3 §T3 removed the value, so the summary can "
                        "never receive it")
    # Assert on the framing-rule line itself. A byte window around the token is fragile:
    # the label may sit before or after it, and the changelog mentions the value too.
    for line in body.splitlines():
        if line.lstrip().startswith("- MINORITY_INVESTMENT") and "legacy" not in line.lower():
            failures.append("deal_summary prompt: the MINORITY_INVESTMENT framing rule carries "
                            "no legacy-row label. The classifier stopped emitting the type at "
                            "0.7, so the rule is compatibility for stored rows, not current "
                            "output")

    if "acquirer_type = pe_portfolio: add-on" in text:
        failures.append("deal_summary prompt: the retired pe_portfolio -> add-on inference is "
                        "still an active framing rule — §T7 replaced it with "
                        "sponsor_transaction_role")

    # deal_summary 0.16. Assert on the DELIVERED system prompt and user template, not on the
    # file: the loader extracts only the §4 and §5 fences, so a rule written into §3, §7 or
    # the changelog is documentation the model never receives. That distinction is what made
    # the funding gap invisible in the first place.
    delivered = load_prompt_file("deal_summary")
    system, user = delivered["system"], delivered["user_template"]
    for placeholder in ("{funding_json}", "{financials_disclosure_status}"):
        if placeholder not in user:
            failures.append(f"deal_summary user template: {placeholder} is missing — the "
                            "canonical value is fetched and then dropped without it")
    for rule, why in (
        ("FUNDING FRAMING", "the funding fields arrive with no rule for reading them"),
        ("CATEGORICALLY INAPPLICABLE",
         "a null value_type on a funding event is still readable as UNDISCLOSED"),
        ("VC_ROUND / GROWTH_EQUITY / VENTURE_DEBT",
         "funding events have no deal-type framing entry"),
        ("total_raised_to_date is CUMULATIVE",
         "the cumulative total may be reported as this round's size"),
        ("round_price_direction", "up/down framing has no canonical field to read"),
    ):
        if rule not in system:
            failures.append(f"deal_summary system prompt: {rule!r} is not delivered to the "
                            f"model — {why}")
    # The disclosure gate is the rule that stops absent input becoming a false claim.
    # The wording became per-axis when the second disclosure axis was added; the
    # guarantee is unchanged and is asserted on the meaning rather than one phrasing.
    low = system.lower()
    if "undisclosed" not in low or "at least one relevant fact" not in low:
        failures.append("deal_summary system prompt: the narrow disclosure semantics are "
                        "not delivered — DISCLOSED must not read as complete disclosure")
    # And the licence must sit on the axis that actually makes the claim: the deal's
    # terms, not the target's own financials.
    # The delivered prompt names the axis by the label the model is shown, which is
    # what the template supplies; the snake_case field name lives in the input-shape
    # documentation and reaches no model.
    if "TRANSACTION TERMS DISCLOSURE" not in system:
        failures.append("deal_summary system prompt: the transaction-terms disclosure axis "
                        "is not delivered")
    if "does NOT license it" not in system:
        failures.append("deal_summary system prompt: financials_disclosure_status must not "
                        "license a claim about the deal's terms")
    if "is_down_round" not in system:
        failures.append("deal_summary system prompt: the prohibition on inventing an "
                        "is_down_round field is missing")


def main() -> None:
    failures: list[str] = []
    _test_transport(failures)
    _test_sponsor_role_transport(failures)
    _test_neighbours_unchanged(failures)
    _test_funding_transport(failures)
    _test_funding_nulls_are_not_falsey(failures)
    _test_stored_false_transports_as_false(failures)
    _test_non_funding_control(failures)
    _test_prompt_contract(failures)

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"PASS  summary transport  ({len(CASES)} attitude/approach + "
          f"{len(_SPONSOR_CASES)} sponsor-role + {len(_FUNDING_CASES)} funding anchors "
          f"+ null-preservation + 2 controls + prompt contract)")


if __name__ == "__main__":
    main()
