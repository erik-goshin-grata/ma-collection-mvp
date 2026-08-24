#!/usr/bin/env python3
"""Funding-path `pct_acquired` — delivered contract, both write paths, canonical chain.

THE GAP THIS CLOSES

`pct_acquired` had no author on the funding path. Stage 4 excludes funding event types
outright (`v2_event_type NOT IN (VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT)`), and Stage 4b
never asked for it. So an explicitly stated stake -- routine in growth equity, where
"acquired approximately 65% for $180 million" is ordinary press-release language -- was
lost, even though `staging_extraction.pct_acquired` has existed since `001_initial.sql`,
`HC_FIELDS` already carried it into the observation ledger, and Stage 9 already owned the
canonical column. Complete transport, no producer. Nothing here adds a column.

The prompt's own Example 2 was the tell: it described a stated 65% in the free-text `notes`
field, because the response contract had nowhere else to put it.

STATED OR NULL

The field is only ever a number the source states. Majority/control framing, acquisition
framing, and any computation from round size and valuation are all forbidden, and an
unstated percentage is never rounded to 100. The last of those matters most, because Stage 9
DOES default a silent `pct_acquired` to 100 -- but only for `_CONTROL_DEFAULT_TYPES`
(`ACQUISITION`, `MERGER`). The funding-vs-M&A boundary is therefore load-bearing, and it is
pinned in both directions below: if a funding type ever entered that set, an unstated VC
round would silently become a whole-company buy.

WHY THE PARSER CLEARS RATHER THAN REJECTS

`_validate` failure marks the entire extraction `PROMPT_FAILED`. Discarding a whole funding
row over one malformed optional percentage is out of proportion to the fact lost, so
`_clean_pct` clears the field and the rest of the extraction stands -- the advisor
precedent.

`"65%"` -> `65.0` is deterministic parser normalization, not inference, and so is
`"approximately 65%"` -> `65.0`: an approximation word in front of ONE number is hedging,
not a second value. A range or a bound is different in kind -- "30-40%" and "at least 65%"
each leave the actual percentage unknown, so taking an endpoint would invent precision the
source never gave. Those clear, along with zero, negatives, anything above 100, and any
other non-numeric text.

BOTH WRITE PATHS

Stage 4b writes staging twice: an UPDATE for the first transaction of a source and an
INSERT for each additional one. The INSERT builds its own parameter tuple by design --
reusing `round_params` there caused a binding crash (bug #6) -- so a field added to only one
path reaches single-transaction sources and silently vanishes from multi-transaction ones.
Both are driven here.

LAYERS

  1. delivered contract  -- assertions read `load_prompt_file(...)["system"]`, never the
                            Markdown, so a rule that drifts outside the §4 fence fails
  2. parser              -- `_clean_pct` normalization and clearing
  3. production path     -- real Stage 4b -> production observation writer -> ledger ->
                            Stage 9 at the CONFIGURED read source -> canonical
  4. boundary            -- the assumed-100 default, funding vs M&A
  5. controls            -- `total_raised_to_date` (funding-specific) and
                            `target_description` (the same HC_FIELDS group `pct_acquired`
                            travels in) must be unchanged in every case

Run from project root:
    python scripts/test_funding_pct_acquired.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
from prompts.base import load_prompt_file
import stages.aggregate as aggregate
import stages.funding_hc_extract as fhc
from stages.aggregate import _resolve_pct_acquired
from lib.observation_writer import (
    HC_FIELDS,
    backfill_observation_transaction_ids,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def check_true(label: str, cond: bool) -> None:
    check(label, bool(cond), True)


# ---------------------------------------------------------------------------
# 1. Delivered contract
# ---------------------------------------------------------------------------
#
# Read what load_prompt_file() actually sends. A rule written into §6, §7 or a header is
# documentation the model never sees, which is exactly how a 24-code vocabulary once passed
# a parity test while the model saw none of it.

def test_delivered_contract() -> None:
    print("\nDelivered contract (load_prompt_file, not the Markdown):")
    system = load_prompt_file("funding_hc_extraction")["system"]

    check_true("PCT ACQUIRED block is delivered", "PCT ACQUIRED" in system)
    check_true("pct_acquired is in the delivered response format",
               '"pct_acquired"' in system)
    check_true("only-when-explicitly-stated rule delivered",
               "ONLY when the source explicitly states it" in system)

    # The four anti-inference rules, each pinned separately: a single "forbids inference"
    # assertion would pass while three of the four were deleted.
    for phrase, label in (
        ("majority investment", "majority-framing anti-inference delivered"),
        ("control investment", "control-framing anti-inference delivered"),
        ("round size and valuation", "computed-percentage anti-inference delivered"),
        ("round an unstated percentage to 100", "no-rounding-to-100 rule delivered"),
    ):
        check_true(label, phrase in system)

    check_true("stated approximation is admitted", "approximately 65%" in system)
    check_true("stated range is excluded", "between 30% and 40%" in system)


# ---------------------------------------------------------------------------
# 2. Parser: _clean_pct
# ---------------------------------------------------------------------------

class _Log:
    """Captures warnings so 'cleared' can be distinguished from 'never looked at'."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, fmt: str, *args) -> None:
        self.warnings.append(fmt % args)


_CLEAN_CASES = [
    # (label,                       raw,          expected, expect_warning)
    ("plain_float",                 65.0,          65.0,  False),
    ("plain_int",                   65,            65.0,  False),
    ("percent_string",              "65%",         65.0,  False),
    ("percent_string_spaced",       "65.0 %",      65.0,  False),
    ("percent_string_untrimmed",    "  65 % ",     65.0,  False),
    ("boundary_100",                100,          100.0,  False),
    # Approximation wording in front of ONE number is hedging, not a second value.
    # The delivered prompt tells the model these are stated, so the parser accepts them.
    ("approximately_word",          "approximately 65%", 65.0, False),
    ("approx_abbrev_dot",           "approx. 65%", 65.0,  False),
    ("approx_abbrev",               "approx 65%",  65.0,  False),
    ("about_word",                  "about 65%",   65.0,  False),
    ("roughly_word",                "roughly 65%", 65.0,  False),
    ("circa_word",                  "circa 65%",   65.0,  False),
    ("tilde_form",                  "~65%",        65.0,  False),
    ("approx_mixed_case",           "Approximately 65%", 65.0, False),
    ("approx_without_percent_sign", "approximately 65",  65.0, False),
    ("approx_decimal",              "about 65.5%", 65.5,  False),
    ("small_stake",                 0.5,           0.5,   False),
    ("none_stays_none",             None,          None,  False),
    # Cleared -- each with a warning, none failing the extraction.
    ("zero_is_not_a_stake",         0,             None,  True),
    ("negative",                    -5,            None,  True),
    ("above_100",                   150,           None,  True),
    ("above_100_string",            "150%",        None,  True),
    ("range_form",                  "30-40%",      None,  True),
    ("range_en_dash",               "30\u201340%",     None,  True),
    ("range_words",                 "between 30 and 40", None, True),
    ("range_to",                    "30 to 40%",   None,  True),
    ("lower_bound_at_least",        "at least 65%", None, True),
    ("lower_bound_more_than",       "more than 65%", None, True),
    ("lower_bound_over",            "over 65%",    None,  True),
    ("lower_bound_plus",            "65+%",        None,  True),
    ("upper_bound_up_to",           "up to 65%",   None,  True),
    ("upper_bound_less_than",       "less than 65%", None, True),
    ("upper_bound_below",           "below 65%",   None,  True),
    ("bound_minimum",               "minimum 65%", None,  True),
    ("approx_hides_a_range",        "about 30-40%", None, True),
    ("empty_string",                "",            None,  True),
    ("nonnumeric_text",             "majority",    None,  True),
    ("control_language_a_majority", "a majority stake", None, True),
    ("approx_out_of_range",         "approximately 150%", None, True),
    ("bool_true_is_not_a_number",   True,          None,  True),
    ("list_is_not_a_number",        [65],          None,  True),
]


def test_clean_pct() -> None:
    print("\nParser normalization and clearing (_clean_pct):")
    for label, raw, expected, expect_warning in _CLEAN_CASES:
        log = _Log()
        got = fhc._clean_pct(raw, log, 1)
        check(f"{label} -> {expected!r}", got, expected)
        if expect_warning:
            check_true(f"{label} logs a warning", len(log.warnings) == 1)
        else:
            check_true(f"{label} logs nothing", not log.warnings)


# ---------------------------------------------------------------------------
# 4. The assumed-100 boundary (unit layer)
# ---------------------------------------------------------------------------
#
# Stage 9 defaults a silent pct_acquired to 100 for control event types. The funding types
# must never be in that set. Both directions are asserted from the same fixture shape, so a
# pass cannot come from the fixture happening to be unusual.

def test_assumed_100_boundary() -> None:
    print("\nAssumed-100 boundary (funding never defaults, M&A still does):")
    for etype in ("VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"):
        pct, source = _resolve_pct_acquired({"v2_event_type": etype, "pct_acquired": None})
        check(f"{etype} silent -> null", (pct, source), (None, None))

    for etype in ("ACQUISITION", "MERGER"):
        pct, source = _resolve_pct_acquired({"v2_event_type": etype, "pct_acquired": None})
        check(f"{etype} silent -> 100 assumed", (pct, source), (100.0, "assumed"))

    # A stated value wins on every event type, and is never restamped as assumed.
    for etype in ("GROWTH_EQUITY", "ACQUISITION"):
        pct, source = _resolve_pct_acquired({"v2_event_type": etype, "pct_acquired": 65.0})
        check(f"{etype} stated 65 -> stated", (pct, source), (65.0, "stated"))

    check_true("funding types absent from _CONTROL_DEFAULT_TYPES",
               not ({"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"}
                    & aggregate._CONTROL_DEFAULT_TYPES))
    check_true("pct_acquired rides the production HC observation group",
               "pct_acquired" in HC_FIELDS)


# ---------------------------------------------------------------------------
# 3. Production path
# ---------------------------------------------------------------------------

def _response(transactions: list[dict]) -> dict:
    return {"transactions": transactions}


def _txn(company: str, **overrides) -> dict:
    """One funding transaction in the shape funding_hc_extraction 0.4 emits."""
    txn = {
        "company": {"name": company, "domain": None, "ticker": None,
                    "description": f"{company} description"},
        "investors": [{"name": "TA Associates", "domain": None,
                       "investor_type": "growth_equity", "is_lead": True,
                       "lead_investor_rank": 1, "investment_amount": None,
                       "investment_currency": None, "is_new_investor": True,
                       "is_existing_investor": False}],
        "round": {"label": None, "size": 180000000, "currency": "USD",
                  "pre_money_valuation": 275000000, "post_money_valuation": None,
                  "valuation_currency": "USD", "facility_size": None,
                  "total_raised_to_date": 240000000,
                  "is_extension_round": False, "round_price_direction": None,
                  "is_bridge_round": False},
        "dates": {"announced_date": "2026-06-01", "announced_date_precision": "exact",
                  "closed_date": "2026-06-01", "closed_date_precision": "exact"},
        "financials_disclosure_status": "DISCLOSED",
        "consideration_type": "equity",
        "pct_acquired": None,
        "model_confidence": "HIGH",
        "notes": None,
    }
    txn.update(overrides)
    return txn


def _run_production(label: str, v2_event_type: str, transactions: list[dict]) -> list[dict]:
    """Real Stage 4b -> production observation writer -> ledger -> Stage 9 -> canonical.

    Returns one dict per transaction, in source order.
    """
    db_path = os.path.join(tempfile.mkdtemp(), "fpct.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            f" VALUES ('PR_NEWSWIRE','T2','u-{label}','t-{label}','2026-06-01','body',"
            "'RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO staging_extraction
                (source_raw_id, status, deal_type, v2_event_type, event_type,
                 event_history_type, dt_prompt_version)
            VALUES (?, 'CLASSIFIED', ?, ?, 'ANNOUNCEMENT', 'ANNOUNCED',
                    'deal_type_classifier:test')
            """,
            (srid, v2_event_type, v2_event_type),
        )
        conn.commit()

        real_call, real_sleep = fhc.call_prompt, fhc._SLEEP
        fhc.call_prompt, fhc._SLEEP = (lambda **_k: _response(transactions)), 0
        try:
            fhc.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"),
                    run_id=f"fpct_{label}")
        finally:
            fhc.call_prompt, fhc._SLEEP = real_call, real_sleep

        staged = conn.execute(
            "SELECT extraction_id, target_name, pct_acquired, total_raised_to_date,"
            " target_description FROM staging_extraction WHERE source_raw_id=?"
            " ORDER BY extraction_id", (srid,)).fetchall()

        # Stand in for Stage 8: promote to CLUSTERED and assign one cluster id per
        # transaction. Stage 4b, the observation writer and Stage 9 are all real.
        for n, srow in enumerate(staged):
            conn.execute(
                "UPDATE staging_extraction SET status='CLUSTERED',"
                " transaction_cluster_id=? WHERE extraction_id=?",
                (f"tc_fpct_{label}_{n}", srow["extraction_id"]))
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, f"fpct_{label}")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        out = []
        for n, srow in enumerate(staged):
            ledger = conn.execute(
                "SELECT COUNT(*) AS c FROM transaction_field_observation"
                " WHERE staging_extraction_id=? AND field_name='pct_acquired'",
                (srow["extraction_id"],)).fetchone()["c"]
            canon = conn.execute(
                "SELECT pct_acquired, pct_acquired_source, total_raised_to_date,"
                " target_description FROM transaction_record WHERE transaction_id=?",
                (f"tc_fpct_{label}_{n}",)).fetchone()
            out.append({
                "target_name": srow["target_name"],
                "staging": srow["pct_acquired"],
                "ledger_rows": ledger,
                "canonical": None if canon is None else canon["pct_acquired"],
                "source": None if canon is None else canon["pct_acquired_source"],
                # Controls, carried through the identical chain.
                "ctl_total_raised": (None if canon is None
                                     else canon["total_raised_to_date"]),
                "ctl_description": (None if canon is None
                                    else canon["target_description"]),
            })
        return out
    finally:
        conn.close()


def test_production_path() -> None:
    print("\nProduction path (Stage 4b -> ledger -> Stage 9 -> canonical):")

    # (a) Growth equity with an explicitly stated stake -- the case that was being lost.
    got = _run_production("ge_stated", "GROWTH_EQUITY",
                          [_txn("PortfolioCo", pct_acquired=65.0)])[0]
    check("growth_equity stated: staging", got["staging"], 65.0)
    check("growth_equity stated: one ledger row", got["ledger_rows"], 1)
    check("growth_equity stated: canonical", got["canonical"], 65.0)
    check("growth_equity stated: source is 'stated'", got["source"], "stated")

    # (b) VC round with no stated ownership -- stays null, writes no observation.
    got = _run_production("vc_silent", "VC_ROUND", [_txn("TechCo")])[0]
    check("vc silent: staging null", got["staging"], None)
    check("vc silent: no ledger row", got["ledger_rows"], 0)
    check("vc silent: canonical null", got["canonical"], None)
    check("vc silent: no assumed-100", got["source"], None)

    # (c) Control framing without a number. This is the model-side half of the rule: the
    #     source says "majority investment" and the response still carries null.
    got = _run_production("ge_majority_no_number", "GROWTH_EQUITY",
                          [_txn("MajorityCo")])[0]
    check("majority framing without a number: canonical null", got["canonical"], None)
    check("majority framing without a number: not defaulted to 100", got["source"], None)

    # (d) Venture debt -- the third funding type, silent.
    got = _run_production("vd_silent", "VENTURE_DEBT", [_txn("DebtCo")])[0]
    check("venture_debt silent: canonical null", got["canonical"], None)

    # (e) Malformed value clears its own field; the extraction survives intact.
    got = _run_production("ge_malformed", "GROWTH_EQUITY",
                          [_txn("MalformedCo", pct_acquired="majority")])[0]
    check("malformed: canonical null", got["canonical"], None)
    check("malformed: extraction survived (row exists)",
          got["target_name"], "MalformedCo")
    check("malformed: control total_raised_to_date intact",
          got["ctl_total_raised"], 240000000)

    # (f) Percent-string normalization survives the full chain, not just the parser.
    got = _run_production("ge_percent_string", "GROWTH_EQUITY",
                          [_txn("StringCo", pct_acquired="65%")])[0]
    check("percent string: canonical 65.0", got["canonical"], 65.0)

    # (g) MULTI-TRANSACTION INSERT PATH. The stake is on the SECOND transaction, which is
    #     written by the INSERT branch. A field added only to the UPDATE passes (a) and
    #     fails here.
    rows = _run_production("multi", "GROWTH_EQUITY", [
        _txn("FirstCo"),
        _txn("SecondCo", pct_acquired=42.0),
    ])
    check("multi: two transactions written", len(rows), 2)
    check("multi: first transaction null", rows[0]["canonical"], None)
    check("multi (INSERT path): second transaction staging", rows[1]["staging"], 42.0)
    check("multi (INSERT path): second transaction canonical",
          rows[1]["canonical"], 42.0)
    check("multi (INSERT path): source is 'stated'", rows[1]["source"], "stated")

    # (h) Controls, on the case that changed. Both must be untouched: one funding-specific
    #     field and one from the same HC_FIELDS group pct_acquired rides in.
    got = _run_production("ctl", "GROWTH_EQUITY",
                          [_txn("ControlCo", pct_acquired=65.0)])[0]
    check("control total_raised_to_date unchanged", got["ctl_total_raised"], 240000000)
    check("control target_description unchanged",
          got["ctl_description"], "ControlCo description")


def main() -> None:
    print(__doc__.strip().split("\n")[0])
    test_delivered_contract()
    test_clean_pct()
    test_assumed_100_boundary()
    test_production_path()

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — funding-path pct_acquired: contract, parser, boundary and "
          f"production chain all hold")


if __name__ == "__main__":
    main()
