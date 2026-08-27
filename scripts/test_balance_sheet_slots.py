#!/usr/bin/env python3
"""HC 0.30 — the balance-sheet facts can finally be answered.

WHAT WENT WRONG

Five fields are instructed at length in the section 4 prose and had no key in the
`RESPONSE FORMAT` object that same prose names:

    target_financials.total_debt
    target_financials.total_debt_currency
    target_financials.cash_st
    target_financials.cash_st_currency
    target_financials.balance_sheet_as_of_date

The instruction is not vague. It carries the net-figure trap ("If the source
states only a net debt figure, leave total_debt null"), the combined-cash rule,
a per-figure currency rule, and a whole paragraph insisting the as-of date is
POINT_IN_TIME and never LTM/TTM/NTM/annual/quarterly. Everything downstream was
already built: Stage 4 reads all five paths, all five sit in the production HC
observation group, Stage 9 owns all five canonical columns.

They are null across all 47 M&A extractions in both live corpora, because the
model was never shown anywhere to put them.

WORSE THAN 0.29, IN ONE SPECIFIC WAY

0.29 restored two keys that section 6's output schema already declared. Section 6
did NOT declare these five. They existed only as prose in a fence whose response
structure contradicted it, so no document in the repository described a shape
that could carry them. Both sections are repaired, and section 6's prior silence
is pinned here so the account in the versioning table stays checkable.

WHAT IS DELIBERATELY NOT ADDED

`net_debt`, `net_debt_currency` and `balance_sheet_period_type`. The prompt
forbids the first outright -- "Do not compute net_debt. Do not compute enterprise
value" -- and the other two are reference-derived, never authored:
`balance_sheet_period_type` is the constant POINT_IN_TIME, written where the
model cannot mislabel it. Adding a slot for any of them would invite the model to
answer a question the contract tells it not to answer.

WHAT THIS MAKES REACHABLE, AND WHY THAT IS NOT A RULE CHANGE

Capturing the source facts wakes four dormant reference derivations: calculated
net debt, `implied_enterprise_value`, the `EQUITY_PLUS_TOTAL_DEBT` branch of
transaction value, and the first non-null multiples -- which are NOT_CALCULABLE
today only because their numerator is null. None of that logic is touched here.
The six Product control cases below record what each one produces, labelled
SOURCE FACT or REFERENCE-DERIVED, so a reviewer can tell a number the source
stated from a number this pipeline reconstructed.

INSPECTION FINDING, RECORDED NOT FIXED

The two consumers of balance-sheet facts apply different evidential standards.
`net_debt` -- and therefore implied EV and every multiple -- requires both
components, one shared currency and one shared KNOWN as-of date. Transaction
value's `EQUITY_PLUS_TOTAL_DEBT` branch reads raw `total_debt` and requires
neither a cash figure nor any date. So a source stating debt but not cash moves
transaction value while net debt refuses. Product ruled this an inspection
finding: capture the facts, let the reference layer show its existing behaviour.
Cases H and I pin it as current behaviour so it cannot change unnoticed while it
is under review.

LAYER

Contract assertions parse the response block out of
load_prompt_file(...)["system"], so a key that drifts outside the section 4 fence
fails. The canonical assertions use the production observation writer with the
production include_hc flag and the production aggregation on the configured read
source. The control cases call the real derivation functions directly.

Because the canonical and control blocks seed staging rather than call a model,
they pass on the pre-change tree too -- the plumbing was always sound. The
demonstrated pre-change failure is therefore at the contract layer, which is
exactly where the defect was: 13 checks, all of them a missing key or a version.

Run from project root:
    python scripts/test_balance_sheet_slots.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE  # noqa: E402
from db import get_connection, init_db  # noqa: E402
from prompts.base import load_prompt_file  # noqa: E402
import stages.aggregate as aggregate  # noqa: E402
import stages.high_confidence_extract as hc  # noqa: E402
from lib.observation_writer import (  # noqa: E402
    HC_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []

_FIVE = ("total_debt", "total_debt_currency", "cash_st", "cash_st_currency",
         "balance_sheet_as_of_date")
_NOT_ADDED = ("net_debt", "net_debt_currency", "balance_sheet_period_type")


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_version_floor(md: str, stage_version: str, introduced: str) -> None:
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check(f"versioning table still carries the {introduced} row",
          bool(re.search(rf"^\| {re.escape(introduced)} \|", md, re.M)), True)
    check("prompt declares a version", bool(declared), True)
    if not declared:
        return
    check(f"prompt version >= {introduced} (currently {declared.group(1)})",
          _version_tuple(declared.group(1)) >= _version_tuple(introduced), True)
    check("stage _VERSION agrees with the prompt", stage_version, declared.group(1))


def _response_object(system: str) -> dict | None:
    """Parse the RESPONSE FORMAT block, which also proves it is still valid JSON."""
    start = system.find('{\n  "transactions": [')
    if start == -1:
        return None
    depth, end = 0, None
    for i in range(start, len(system)):
        if system[i] == "{":
            depth += 1
        elif system[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(system[start:end])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. The delivered response structure
# ---------------------------------------------------------------------------

def test_response_slots() -> None:
    print("\nAll five balance-sheet keys are in target_financials, where the prose sends them:")
    system = load_prompt_file("high_confidence_extraction")["system"]
    obj = _response_object(system)
    check("RESPONSE FORMAT block parses as JSON", obj is not None, True)
    if obj is None:
        return
    tf = (obj.get("transactions") or [{}])[0].get("target_financials")
    check("target_financials object present", isinstance(tf, dict), True)
    if not isinstance(tf, dict):
        return
    for field in _FIVE:
        check(f"target_financials.{field}", field in tf, True)

    print("\nThe reference-derived fields are deliberately NOT offered:")
    flat = json.dumps(obj)
    for field in _NOT_ADDED:
        check(f"{field} has no slot anywhere in the response", f'"{field}"' in flat, False)
    check("and the prompt still forbids computing net debt",
          "Do not compute net_debt. Do not compute enterprise value." in system, True)

    print("\nThe revenue and EBITDA fields are untouched:")
    check("target_financials still carries exactly the expected keys",
          sorted(tf),
          ["balance_sheet_as_of_date", "cash_st", "cash_st_currency", "currency",
           "ebitda_amount", "ebitda_period_end", "ebitda_period_type",
           "revenue_amount", "revenue_period_end", "revenue_period_type",
           "total_debt", "total_debt_currency"])
    check("0.29's round_size slot survives",
          "round_size" in (obj.get("transactions") or [{}])[0], True)
    check("0.29's stake_transition_type slot survives",
          "stake_transition_type" in (obj.get("transactions") or [{}])[0].get("deal", {}), True)


# ---------------------------------------------------------------------------
# 2. Section 6 was silent too — the difference from 0.29
# ---------------------------------------------------------------------------

def test_schema_document() -> None:
    print("\nSection 6 now declares them as well (it did not before — unlike 0.29):")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    i, j = md.find("## 6. Output Schema"), md.find("## 7. Few-Shot Examples")
    sec6 = md[i:j] if i != -1 and j != -1 else ""
    check("section 6 present", bool(sec6), True)
    for field in _FIVE:
        check(f"section 6 declares {field}", f'"{field}":' in sec6, True)
    for field in _NOT_ADDED:
        check(f"section 6 does not declare {field}", f'"{field}":' in sec6, False)
    system = load_prompt_file("high_confidence_extraction")["system"]
    check("section 6 still reaches no model", "## 6. Output Schema" in system, False)


# ---------------------------------------------------------------------------
# 3. Everything downstream already existed
# ---------------------------------------------------------------------------

def test_readers_exist() -> None:
    print("\nStage 4, the observation group and Stage 9 were ready the whole time:")
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    owned = set(aggregate._STAGE9_OWNED_COLUMNS)
    for field in _FIVE:
        check(f"Stage 4 reads target_financials.{field}", f'tf.get("{field}")' in src, True)
        check(f"{field} in the production HC observation group", field in HC_FIELDS, True)
        check(f"Stage 9 owns the {field} column", field in owned, True)
    print("\nAnd the derived companions stay derived, never observed:")
    for field in ("net_debt_currency", "balance_sheet_period_type"):
        check(f"{field} is not in any observation group", field in HC_FIELDS, False)
        check(f"{field} is still a Stage 9 output", field in owned, True)
    check("balance_sheet_period_type is the constant, not an extraction",
          aggregate.BALANCE_SHEET_PERIOD_TYPE, "POINT_IN_TIME")


# ---------------------------------------------------------------------------
# 4. Canonical path — production writer, production include_* flag
# ---------------------------------------------------------------------------

def test_canonical_path() -> None:
    """Four hops for all five, with target_revenue as the neighbouring control.

    The control is an unchanged HC_FIELDS member on the identical path. If both
    fail, the harness is broken; if only the balance-sheet fields fail, they are.

    Currency and as-of companions are anchored by _companion_from_sources to the
    same source as their amount, so this seeds one source and asserts the anchoring
    resolves rather than assuming it.
    """
    print("\nAll five reach canonical, end to end:")
    db_path = os.path.join(tempfile.mkdtemp(), "bs.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-26','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "event_history_type",
                "target_status", "target_type", "target_type_v2", "target_name",
                "acquirer_name", "acquirer_type", "acquirer_type_v2",
                "announced_date", "announced_date_precision", "financials_disclosure_status",
                "value_amount", "value_currency", "value_type", "pct_acquired",
                "target_revenue", "target_revenue_period_type", "target_revenue_period_type_v2",
                "target_revenue_period_end", "financials_currency",
                "total_debt", "total_debt_currency", "cash_st", "cash_st_currency",
                "balance_sheet_as_of_date",
                "model_confidence", "dt_prompt_version", "hc_prompt_version",
                "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "ACQUISITION", "ACQUISITION", "ANNOUNCED", "PRIVATE",
                "standalone_company", "standalone_company", "Northwind Systems",
                "Cascade Industrial", "strategic_corporate", "strategic_corporate",
                "2026-08-26", "exact", "DISCLOSED",
                1_000_000_000.0, "USD", "EQUITY_VALUE", 100.0,
                400_000_000.0, "ANNUAL", "ANNUAL", "2025", "USD",
                300_000_000.0, "USD", 50_000_000.0, "USD", "2026-06-30",
                "HIGH", "0.15", hc._VERSION, "tc_bs_0001"]
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        for field in _FIVE + ("target_revenue",):
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id='tc_bs_0001' AND field_name=?", (field,)).fetchone()
            check(f"observation/{field} written", row is not None, True)

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "bs-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id='tc_bs_0001'").fetchone()
        check("canonical row written", canon is not None, True)
        if canon is None:
            return
        src_label = DEFAULT_AGGREGATION_READ_SOURCE
        print(f"\n  SOURCE FACTS, stored as stated (read_source={src_label}):")
        for field, expected in (("total_debt", 300_000_000.0), ("total_debt_currency", "USD"),
                                ("cash_st", 50_000_000.0), ("cash_st_currency", "USD"),
                                ("balance_sheet_as_of_date", "2026-06-30")):
            check(f"canonical/{field}", canon[field], expected)
        check("canonical/target_revenue CONTROL", canon["target_revenue"], 400_000_000.0)

        print("\n  REFERENCE-DERIVED, newly reachable — existing logic, untouched:")
        check("canonical/net_debt = 300M - 50M", canon["net_debt"], 250_000_000.0)
        # SECOND INSPECTION FINDING, recorded not fixed. The persisted `net_debt_currency`
        # is only ever the manual read-back (`existing["net_debt_currency"]`, aggregate.py
        # :1999, persisted at :2248). The currency the calculation actually used lives in
        # `net_debt_resolved_currency` and is passed to the implied-EV derivation in memory,
        # never stored. So a CALCULATED net debt lands unlabelled, while both of its own
        # components persist their currency. Harmless downstream -- the in-memory value does
        # the work -- but a reviewer reading transaction_record sees an unlabelled figure.
        # Pinned as current behaviour so it cannot change unnoticed while under review.
        check("canonical/net_debt_currency is NULL on a calculated net debt "
              "(inspection finding, unchanged here)", canon["net_debt_currency"], None)
        check("canonical/balance_sheet_period_type is the derived constant",
              canon["balance_sheet_period_type"], "POINT_IN_TIME")
        check("canonical/implied_enterprise_value = 1000M + 250M",
              canon["implied_enterprise_value"], 1_250_000_000.0)
        check("canonical/implied_enterprise_value_basis",
              canon["implied_enterprise_value_basis"], "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT")
        check("canonical/transaction_value = 1000M + 300M gross debt",
              canon["transaction_value"], 1_300_000_000.0)
        check("canonical/transaction_value_basis",
              canon["transaction_value_basis"], "EQUITY_PLUS_TOTAL_DEBT")
        check("canonical/ev_to_revenue_ltm = 1250M / 400M",
              canon["ev_to_revenue_ltm"], 3.12)
        check("canonical/multiple_quality is no longer NOT_CALCULABLE",
              canon["multiple_quality"], "CALCULATED")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. The six Product control cases
# ---------------------------------------------------------------------------

_BS = dict(total_debt=300_000_000.0, total_debt_currency="USD",
           cash_st=50_000_000.0, cash_st_currency="USD",
           balance_sheet_as_of_date="2026-06-30")
_BASE = dict(v2_event_type="ACQUISITION", value_currency="USD",
             value_type="EQUITY_VALUE", financials_currency="USD")


class _Silent:
    def debug(self, *a, **k):
        pass


def _run_chain(fv: dict) -> dict:
    """The production derivation chain, in the order Stage 9 calls it."""
    is_minority = aggregate._derive_is_minority(fv)
    pct, pct_source = aggregate._resolve_pct_acquired(fv, is_minority)
    equity, _ = aggregate._derive_equity_value(fv, fv.get("per_share_price"), None, pct)
    implied_equity = aggregate._derive_implied_equity(equity, pct)
    net_debt, nd_cur, _, nd_basis = aggregate._derive_net_debt(
        None, None, fv.get("total_debt"), fv.get("total_debt_currency"),
        fv.get("balance_sheet_as_of_date"), fv.get("cash_st"),
        fv.get("cash_st_currency"), fv.get("balance_sheet_as_of_date"))
    currency = fv.get("value_currency")
    iev, iev_basis = aggregate._derive_implied_enterprise_value(
        fv.get("value_amount"), fv.get("value_type"), implied_equity, net_debt,
        implied_equity_currency=currency, net_debt_currency=nd_cur, net_debt_basis=nd_basis)
    tv, tv_basis = aggregate._derive_transaction_value(
        fv, equity, fv.get("total_debt"), pct,
        equity_currency=currency, total_debt_currency=fv.get("total_debt_currency"))
    multiples = aggregate._compute_multiples(
        iev, currency, 400_000_000.0, "ANNUAL", None, None, fv.get("financials_currency"),
        _Silent(), "control", fv.get("v2_event_type"), "2026-08-26", "2025")
    return {"pct": pct, "pct_source": pct_source, "equity_value": equity,
            "implied_equity_value": implied_equity, "net_debt": net_debt,
            "implied_enterprise_value": iev, "implied_enterprise_value_basis": iev_basis,
            "transaction_value": tv, "transaction_value_basis": tv_basis,
            "ev_to_revenue_ltm": multiples["ev_to_revenue_ltm"],
            "multiple_quality": multiples["multiple_quality"]}


def test_controls() -> None:
    """Six Product cases. Every output is labelled SOURCE or DERIVED.

    The labels are the point of this block. A reviewer looking at 1,250,000,000 has
    no way to tell from the number itself whether a source said it or this pipeline
    reconstructed it, and after R1.2 the reconstructed ones are the majority.
    """
    print("\nSix control cases — what the source said vs what the reference layer built:")

    cases = [
        ("A · price $1B, explicit 100%, debt 300 / cash 50",
         dict(_BASE, value_amount=1e9, pct_acquired=100.0, **_BS),
         {"pct": 100.0, "pct_source": "stated",
          "equity_value": 1e9, "implied_equity_value": 1e9, "net_debt": 2.5e8,
          "implied_enterprise_value": 1.25e9,
          "implied_enterprise_value_basis": "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT",
          "transaction_value": 1.3e9, "transaction_value_basis": "EQUITY_PLUS_TOTAL_DEBT",
          "ev_to_revenue_ltm": 3.12, "multiple_quality": "CALCULATED"}),

        ("B · price $400M, explicit 40%, debt 300 / cash 50",
         dict(_BASE, value_amount=4e8, pct_acquired=40.0, **_BS),
         {"pct": 40.0, "pct_source": "stated",
          "equity_value": 4e8, "implied_equity_value": 1e9, "net_debt": 2.5e8,
          "implied_enterprise_value": 1.25e9,
          "implied_enterprise_value_basis": "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT",
          "transaction_value": 4e8, "transaction_value_basis": "EQUITY_BELOW_CONTROL",
          "ev_to_revenue_ltm": 3.12, "multiple_quality": "CALCULATED"}),

        ("C · price $1B, percentage UNSTATED, debt 300 / cash 50",
         dict(_BASE, value_amount=1e9, pct_acquired=None, **_BS),
         {"pct": 100.0, "pct_source": "assumed",
          "equity_value": 1e9, "implied_equity_value": 1e9, "net_debt": 2.5e8,
          "implied_enterprise_value": 1.25e9,
          "implied_enterprise_value_basis": "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT",
          "transaction_value": 1.3e9, "transaction_value_basis": "EQUITY_PLUS_TOTAL_DEBT",
          "ev_to_revenue_ltm": 3.12, "multiple_quality": "CALCULATED"}),

        ("D · price $1B, majority stated / exact % UNSTATED, debt 300 / cash 50",
         dict(_BASE, value_amount=1e9, pct_acquired=None,
              stake_transition_type="NEW_MAJORITY_STAKE", **_BS),
         {"pct": 100.0, "pct_source": "assumed",
          "equity_value": 1e9, "implied_equity_value": 1e9, "net_debt": 2.5e8,
          "implied_enterprise_value": 1.25e9,
          "implied_enterprise_value_basis": "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT",
          "transaction_value": 1.3e9, "transaction_value_basis": "EQUITY_PLUS_TOTAL_DEBT",
          "ev_to_revenue_ltm": 3.12, "multiple_quality": "CALCULATED"}),

        ("E · investment $200M, % UNSTATED, MINORITY_INVESTMENT, debt 300 / cash 50",
         dict(_BASE, v2_event_type="MINORITY_INVESTMENT", value_amount=2e8,
              pct_acquired=None, **_BS),
         {"pct": None, "pct_source": None,
          "equity_value": 2e8, "implied_equity_value": None, "net_debt": 2.5e8,
          "implied_enterprise_value": None, "implied_enterprise_value_basis": None,
          "transaction_value": None, "transaction_value_basis": None,
          "ev_to_revenue_ltm": None, "multiple_quality": "NOT_CALCULABLE"}),

        ("F · price $1B, % UNSTATED, NO balance sheet disclosed",
         dict(_BASE, value_amount=1e9, pct_acquired=None),
         {"pct": 100.0, "pct_source": "assumed",
          "equity_value": 1e9, "implied_equity_value": 1e9, "net_debt": None,
          "implied_enterprise_value": None, "implied_enterprise_value_basis": None,
          "transaction_value": 1e9, "transaction_value_basis": "EQUITY_VALUE_ONLY",
          "ev_to_revenue_ltm": None, "multiple_quality": "NOT_CALCULABLE"}),
    ]

    # SOURCE = the source stated it. DERIVED = this pipeline built it.
    labels = {"pct": "depends on pct_source", "pct_source": "-",
              "equity_value": "SOURCE", "implied_equity_value": "DERIVED",
              "net_debt": "DERIVED", "implied_enterprise_value": "DERIVED",
              "implied_enterprise_value_basis": "DERIVED",
              "transaction_value": "DERIVED", "transaction_value_basis": "DERIVED",
              "ev_to_revenue_ltm": "DERIVED", "multiple_quality": "DERIVED"}

    for label, fv, expected in cases:
        print(f"\n  {label}")
        got = _run_chain(fv)
        for key, want in expected.items():
            tag = labels[key]
            if key == "pct":
                tag = ("SOURCE" if got["pct_source"] == "stated"
                       else "DERIVED (assumed)" if got["pct_source"] == "assumed" else "-")
            check(f"    [{tag:18}] {key}", got[key], want)

    print("\nF1 inspection finding — the two consumers disagree, recorded not fixed:")
    print("    net_debt needs both components, one currency, one KNOWN as-of date.")
    print("    transaction_value needs total_debt and a currency match. Nothing else.")

    h = _run_chain(dict(_BASE, value_amount=1e9, pct_acquired=100.0,
                        total_debt=3e8, total_debt_currency="USD",
                        balance_sheet_as_of_date="2026-06-30"))
    check("    H · debt stated, cash NOT stated -> net_debt refuses", h["net_debt"], None)
    check("    H · ... and implied EV refuses with it", h["implied_enterprise_value"], None)
    check("    H · ... but transaction_value still takes the gross-debt branch",
          (h["transaction_value"], h["transaction_value_basis"]),
          (1.3e9, "EQUITY_PLUS_TOTAL_DEBT"))

    i = _run_chain(dict(_BASE, value_amount=1e9, pct_acquired=100.0,
                        total_debt=3e8, total_debt_currency="USD",
                        cash_st=5e7, cash_st_currency="USD"))
    check("    I · as-of date missing -> net_debt refuses", i["net_debt"], None)
    check("    I · ... but transaction_value still takes the gross-debt branch",
          (i["transaction_value"], i["transaction_value_basis"]),
          (1.3e9, "EQUITY_PLUS_TOTAL_DEBT"))

    g = _run_chain(dict(_BASE, value_amount=1e9, pct_acquired=100.0,
                        total_debt=3e8, total_debt_currency="EUR",
                        cash_st=5e7, cash_st_currency="USD",
                        balance_sheet_as_of_date="2026-06-30"))
    check("    G · cross-currency -> both refuse, no FX invented",
          (g["net_debt"], g["transaction_value_basis"]), (None, "EQUITY_VALUE_ONLY"))


# ---------------------------------------------------------------------------
# 6. No rule changed, and no derivation changed
# ---------------------------------------------------------------------------

def test_no_drift() -> None:
    print("\nThe substantive extraction rules are untouched:")
    prompt = load_prompt_file("high_confidence_extraction")
    system = prompt["system"]
    # Phrase pins run against a whitespace-normalized view: re-wrapping a paragraph is
    # formatting, not a contract change.
    flat = re.sub(r"\s+", " ", system)
    for phrase, label in (
        ("If the source states only a net debt figure, leave total_debt null",
         "net-figure trap"),
        ("as one combined figure. Do not split it into components", "combined-cash rule"),
        ("These are POINT_IN_TIME figures. Never label them LTM, TTM, or NTM",
         "POINT_IN_TIME discipline"),
        ("Never assume missing debt or cash/ST is zero", "no-zero-assumption rule"),
        ("A null is correct; a guess is not", "null-over-guess rule"),
        ("Never convert a currency yourself", "no-FX rule"),
        ("Do not compute net_debt. Do not compute enterprise value",
         "no-derivation-in-extraction rule"),
    ):
        check(f"{label} still delivered", phrase in flat, True)

    print("\nEarlier contracts all still delivered:")
    for marker, label in (
        ("WHAT IS NOT A DEAL-VALUE FACT", "0.28 value-scope boundary"),
        ("MULTIPLE BUYERS", "0.27 multiple buyers"),
        ("ONE ECONOMIC FACT, ONE OBSERVATION", "0.26 currency representation"),
        ("BUY-SIDE COHERENCE", "0.25 buy-side coherence"),
    ):
        check(f"{label} still delivered", marker in system, True)
    # This guard read "the pct_acquired rule text is untouched -- semantics stay parked"
    # and pinned the old prohibition verbatim. R1.2 did not touch that rule, which is
    # what it was there to prove. Product has since ruled the field evidence-only and
    # HC 0.32 rewrote it deliberately, so pinning the retired wording would now assert a
    # contract that no longer exists. What still matters here is the same thing it
    # always did: this slice is not the one that changed it.
    check("pct_acquired is instructed evidence-only, not by the retired prohibition",
          "Do not extract 100" in system.replace("\n  ", " "), False)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print("\nR1.2 changed no derivation — the reference layer only became observable:")
    check("Stage 9 still owns 120 canonical columns",
          len(aggregate._STAGE9_OWNED_COLUMNS), 120)
    check("_derive_transaction_value still documents the dormant branch as it was",
          "EQUITY_PLUS_TOTAL_DEBT — pct >= 50 and total_debt known"
          in (aggregate._derive_transaction_value.__doc__ or ""), True)
    check("_derive_net_debt still refuses a lone component",
          aggregate._derive_net_debt(None, None, 3e8, "USD", "2026-06-30", None, None, None),
          (None, None, None, None))

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, hc._VERSION, "0.30")
    check("the 0.29 row still records the earlier slot repair",
          bool(re.search(r"^\| 0\.29 \|", md, re.M)), True)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_response_slots()
    test_schema_document()
    test_readers_exist()
    test_canonical_path()
    test_controls()
    test_no_drift()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — balance-sheet facts are answerable; the reference chain is observable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
