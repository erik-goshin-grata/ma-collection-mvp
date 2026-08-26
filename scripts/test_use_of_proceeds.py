#!/usr/bin/env python3
"""Funding HC 0.5 — what the company says the money is for, as a field.

WHAT WENT WRONG

`use_of_proceeds` had a staging column, a place in the funding observation group,
a canonical column Stage 9 owns, and a slot on the funding review sheet -- and no
author anywhere. Permanently null by construction.

It was not forgotten. All of it was drafted in full -- instruction, response slot,
worked examples -- in a **Funding LC** prompt, for a stage the funding design never
called for: `funding_path_design.md` §4 routes funding rows through the existing
deal-type-agnostic Stage 7. Commit 142674c archived that draft to `docs/` once the
repository stopped describing a stage that was never built. The columns outlived
the cancelled stage.

The evidence that it is collectable was sitting inside this very prompt the whole
time. Its own worked examples read "The proceeds will be used to expand the
company's sales team and accelerate product development" and "The company will use
the proceeds to build its founding team and launch its initial product" -- in a
contract that never asked for either.

A FIELD, NOT PROSE

The archived draft asked for "1-2 sentences" of source language. Product has since
ruled the opposite shape: explicitly stated uses only, each a one- or two-word noun
phrase with the verb, possessive and promotional wording stripped, every distinct
use preserved, and null when unstated.

THE DATATYPE IS PRESERVED, NOT REDESIGNED

V3 §7 types this `DATA POINT` -- a scalar, expressly not the repeating RELATIONSHIP
shape §2 uses for named people. Both columns are `TEXT`, and Stage 9's field-type
registry declares it `string`. So several uses share one field, comma-separated, in
the order stated -- the same compact convention this repository already uses for
co-sponsors on `acquirer.sponsor_name`. No schema change, no new table, no list.

BOTH WRITE PATHS, WHICH IS THE WHOLE RISK

Stage 4b writes staging twice: an UPDATE for a source's first transaction, and an
INSERT with its own separately-built parameter tuple for each additional one. That
duplication is deliberate -- reusing the UPDATE's tuple in the INSERT caused a
binding crash (bug #6) -- so a field added to one path reaches single-transaction
sources and silently vanishes from multi-transaction ones. Both are driven here,
against the real stage, so a mismatched tuple fails as a binding error rather than
as a quietly-null column.

WHAT MUST NOT BE ADDED

`has_board_seat` and `board_seat_notes` were drafted in the same archived prompt
and are deliberately left unauthored. V3 lists "a flat board-representation flag
and note" in its residue table: board representation is a participant relationship
with the named representative, and authoring these scalars would build the exact
shape the target removes. Their absence from this contract is asserted here so a
later slice cannot add them by momentum.

Run from project root:
    python scripts/test_use_of_proceeds.py
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
import stages.funding_hc_extract as fhc  # noqa: E402
from lib.observation_writer import (  # noqa: E402
    FUNDING_FIELDS,
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
    """Parse the RESPONSE FORMAT block, proving it is still valid JSON."""
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
# 1. Delivered contract
# ---------------------------------------------------------------------------

def test_contract() -> None:
    print("\nThe rule is delivered, and it asks for a field rather than prose:")
    prompt = load_prompt_file("funding_hc_extraction")
    system = prompt["system"]
    flat = re.sub(r"\s+", " ", system)

    check("USE OF PROCEEDS block present", "USE OF PROCEEDS" in system, True)
    check("stated as a field, not prose", "This is a field, not prose" in flat, True)
    check("explicitly stated uses only",
          "Capture ONLY uses the source explicitly states" in flat, True)
    check("one or two words per use", "one or two words" in flat, True)
    check("promotional and contextual wording stripped",
          "Strip the verb, the possessive, and any promotional or contextual wording"
          in flat, True)
    check("every distinct use preserved, comma-separated",
          "Preserve every distinct stated use, comma-separated, in the order stated"
          in flat, True)
    check("null when unstated",
          "Null when the source does not say what the capital is for" in flat, True)

    print("\nThe worked reductions are delivered, not left to inference:")
    check("two-use example", '"sales team, product development"' in flat, True)
    check("and it shows what NOT to emit",
          'not "expand the company\'s sales team"' in flat, True)
    check("second worked example", '"founding team, product launch"' in flat, True)

    print("\nAnti-inference, and the boundary Product drew inside it:")
    check("growth/strategy/momentum language yields null",
          "Do NOT infer a use from growth, strategy or momentum language" in flat, True)
    check("a growth round is not a statement of use",
          "described as a growth round is not a statement of what the money buys"
          in flat, True)
    check("but a stated-but-broad use is still captured",
          "A stated use that is itself broad is still a stated use" in flat, True)
    check("the test is stated-ness, not specificity",
          "whether the source states a use, not whether the use is specific"
          in flat, True)
    check("no sentences, no marketing adjectives",
          "Do not write a sentence here" in flat, True)

    print("\nThe response slot exists, top-level, and the block is still valid JSON:")
    obj = _response_object(system)
    check("response block parses", obj is not None, True)
    if obj is None:
        return
    txn = (obj.get("transactions") or [{}])[0]
    check("use_of_proceeds is top-level", "use_of_proceeds" in txn, True)
    check("not nested inside round", "use_of_proceeds" in (txn.get("round") or {}), False)
    # Guarded: on a pre-change tree the key is absent, and an unguarded .index() would
    # raise here and abort the run -- leaving every control below unproven, which reads
    # as "nothing else is broken" when in fact nothing else was tested.
    keys = list(txn)
    ordered = (all(k in keys for k in ("pct_acquired", "use_of_proceeds", "model_confidence"))
               and keys.index("use_of_proceeds") == keys.index("pct_acquired") + 1
               and keys.index("model_confidence") == keys.index("use_of_proceeds") + 1)
    check("it sits after pct_acquired, before the metadata pair", ordered, True)
    check("the example value is the reduced form, not the sentence",
          txn.get("use_of_proceeds"), "sales team, product development")

    print("\nThe board pair stays unauthored — V3 residue, not an oversight:")
    for field in ("has_board_seat", "board_seat_notes"):
        check(f"{field} absent from the delivered contract", field in system, False)
        check(f"{field} absent from the response object", field in json.dumps(obj), False)
    check("the word 'board' appears nowhere in the fence", "board" in system.lower(), False)

    print("\nSection 6 declares it too:")
    md = (ROOT / "prompts" / "funding_hc_extraction.md").read_text(encoding="utf-8")
    i, j = md.find("## 6. Output Schema"), md.find("## 7. Few-Shot Examples")
    sec6 = md[i:j] if i != -1 and j != -1 else ""
    check("section 6 declares use_of_proceeds",
          '"use_of_proceeds": "string | null"' in sec6, True)
    check("section 6 does not declare the board pair",
          "board_seat" in sec6, False)

    print("\n0.4's pct_acquired contract is untouched:")
    check("over-extraction warning intact",
          "This is the single most commonly over-extracted field" in flat, True)
    check("majority/control anti-inference intact",
          "Do NOT infer a percentage from" in flat, True)
    check("no rounding to 100", "Do NOT round an unstated percentage to 100" in flat, True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)


# ---------------------------------------------------------------------------
# 2. Datatype — preserved, not redesigned
# ---------------------------------------------------------------------------

def test_datatype_preserved() -> None:
    print("\nThe scalar datatype is preserved end to end:")
    db_path = os.path.join(tempfile.mkdtemp(), "uop_types.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        for table in ("staging_extraction", "transaction_record"):
            col = [r for r in conn.execute(f"PRAGMA table_info({table})")
                   if r[1] == "use_of_proceeds"]
            check(f"{table}.use_of_proceeds exists", bool(col), True)
            if col:
                check(f"{table}.use_of_proceeds is TEXT", col[0][2].upper(), "TEXT")
    finally:
        conn.close()
    check("Stage 9's field-type registry declares it a string",
          ("use_of_proceeds", "string") in aggregate._FIELDS, True)
    check("it is in the production funding observation group",
          "use_of_proceeds" in FUNDING_FIELDS, True)
    check("Stage 9 owns the canonical column",
          "use_of_proceeds" in set(aggregate._STAGE9_OWNED_COLUMNS), True)
    # No repeating structure was introduced: Stage 4b reads the scalar and stores it
    # verbatim. A list, a JSON dump or a split would each show up here.
    src = (ROOT / "stages" / "funding_hc_extract.py").read_text(encoding="utf-8")
    check("Stage 4b reads the field as a scalar",
          src.count('txn.get("use_of_proceeds")'), 2)   # one per write path
    check("no list, split or JSON handling was added for it",
          bool(re.search(r"use_of_proceeds.*(?:split|join|json\.dumps|\[\])", src)), False)


# ---------------------------------------------------------------------------
# 3. Production path — both Stage 4b write paths
# ---------------------------------------------------------------------------

def _txn(company: str, **overrides) -> dict:
    """One funding transaction in the shape funding_hc_extraction 0.5 emits."""
    txn = {
        "company": {"name": company, "domain": None, "ticker": None,
                    "description": f"{company} description"},
        "investors": [{"name": "Venture Partners", "domain": None,
                       "investor_type": "vc_firm", "is_lead": True,
                       "lead_investor_rank": 1, "investment_amount": None,
                       "investment_currency": None, "is_new_investor": True,
                       "is_existing_investor": False}],
        "round": {"label": "Series B", "size": 50000000, "currency": "USD",
                  "pre_money_valuation": None, "post_money_valuation": None,
                  "valuation_currency": None, "facility_size": None,
                  "total_raised_to_date": 68000000,
                  "is_extension_round": False, "round_price_direction": None,
                  "is_bridge_round": False},
        "dates": {"announced_date": "2026-06-01", "announced_date_precision": "exact",
                  "closed_date": None, "closed_date_precision": None},
        "financials_disclosure_status": "DISCLOSED",
        "consideration_type": "equity",
        "pct_acquired": None,
        "use_of_proceeds": None,
        "model_confidence": "HIGH",
        "notes": None,
    }
    txn.update(overrides)
    return txn


def _run_production(label: str, transactions: list[dict]) -> list[dict]:
    """Real Stage 4b -> production observation writer -> ledger -> Stage 9 -> canonical."""
    db_path = os.path.join(tempfile.mkdtemp(), f"uop_{label}.db")
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
            VALUES (?, 'CLASSIFIED', 'VC_ROUND', 'VC_ROUND', 'ANNOUNCEMENT', 'ANNOUNCED',
                    'deal_type_classifier:test')
            """, (srid,))
        conn.commit()

        real_call, real_sleep = fhc.call_prompt, fhc._SLEEP
        fhc.call_prompt = lambda **_k: {"transactions": transactions}
        fhc._SLEEP = 0
        try:
            fhc.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"),
                    run_id=f"uop_{label}")
        finally:
            fhc.call_prompt, fhc._SLEEP = real_call, real_sleep

        staged = conn.execute(
            "SELECT extraction_id, target_name, use_of_proceeds, round_label,"
            " total_raised_to_date FROM staging_extraction WHERE source_raw_id=?"
            " ORDER BY extraction_id", (srid,)).fetchall()

        # Stand in for Stage 8. Stage 4b, the observation writer and Stage 9 are real.
        for n, srow in enumerate(staged):
            conn.execute(
                "UPDATE staging_extraction SET status='CLUSTERED',"
                " transaction_cluster_id=? WHERE extraction_id=?",
                (f"tc_uop_{label}_{n}", srow["extraction_id"]))
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, f"uop_{label}")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        out = []
        for n, srow in enumerate(staged):
            ledger = conn.execute(
                "SELECT COUNT(*) AS c FROM transaction_field_observation"
                " WHERE staging_extraction_id=? AND field_name='use_of_proceeds'",
                (srow["extraction_id"],)).fetchone()["c"]
            canon = conn.execute(
                "SELECT use_of_proceeds, round_label, total_raised_to_date"
                " FROM transaction_record WHERE transaction_id=?",
                (f"tc_uop_{label}_{n}",)).fetchone()
            out.append({
                "target_name": srow["target_name"],
                "staging": srow["use_of_proceeds"],
                "ledger_rows": ledger,
                "canonical": None if canon is None else canon["use_of_proceeds"],
                # Controls on the identical chain, both unchanged FUNDING_FIELDS members.
                "ctl_round_label": None if canon is None else canon["round_label"],
                "ctl_total_raised": (None if canon is None
                                     else canon["total_raised_to_date"]),
            })
        return out
    finally:
        conn.close()


def test_production_paths() -> None:
    print("\nSingle-transaction source — the UPDATE path:")
    rows = _run_production("single", [
        _txn("TechCo", use_of_proceeds="sales team, product development")])
    check("one transaction written", len(rows), 1)
    check("staging", rows[0]["staging"], "sales team, product development")
    check("observation row written", rows[0]["ledger_rows"], 1)
    check(f"canonical (read_source={DEFAULT_AGGREGATION_READ_SOURCE})",
          rows[0]["canonical"], "sales team, product development")
    check("CONTROL round_label unchanged", rows[0]["ctl_round_label"], "Series B")
    check("CONTROL total_raised_to_date unchanged",
          rows[0]["ctl_total_raised"], 68000000.0)

    print("\nMulti-transaction source — the INSERT path (bug #6 territory):")
    rows = _run_production("multi", [
        _txn("AlphaCo", use_of_proceeds="hiring"),
        _txn("BetaCo", use_of_proceeds="founding team, product launch"),
        _txn("GammaCo"),
    ])
    check("three transactions written", len(rows), 3)
    check("first (UPDATE path) staging", rows[0]["staging"], "hiring")
    check("first (UPDATE path) canonical", rows[0]["canonical"], "hiring")
    check("second (INSERT path) staging",
          rows[1]["staging"], "founding team, product launch")
    check("second (INSERT path) canonical",
          rows[1]["canonical"], "founding team, product launch")
    check("second (INSERT path) observation written", rows[1]["ledger_rows"], 1)
    check("third (INSERT path) null stays null", rows[2]["staging"], None)
    check("third writes no observation for an absent value", rows[2]["ledger_rows"], 0)
    check("third canonical stays null", rows[2]["canonical"], None)
    check("CONTROL round_label survives the INSERT path",
          rows[1]["ctl_round_label"], "Series B")
    check("CONTROL total_raised survives the INSERT path",
          rows[1]["ctl_total_raised"], 68000000.0)

    print("\nAn unstated use stays null through the whole chain:")
    rows = _run_production("unstated", [_txn("QuietCo")])
    check("staging null", rows[0]["staging"], None)
    check("no observation row", rows[0]["ledger_rows"], 0)
    check("canonical null", rows[0]["canonical"], None)
    check("CONTROL round_label still written", rows[0]["ctl_round_label"], "Series B")


# ---------------------------------------------------------------------------
# 4. Version integrity
# ---------------------------------------------------------------------------

def test_versions() -> None:
    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "funding_hc_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, fhc._VERSION, "0.5")
    check("the 0.4 row still records pct_acquired's origin",
          bool(re.search(r"^\| 0\.4 \|", md, re.M)), True)
    check("the archived Funding LC draft is still marked never-executable",
          "HISTORICAL — never executable" in
          (ROOT / "docs" / "historical_funding_lc_extraction_prompt.md")
          .read_text(encoding="utf-8"), True)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_contract()
    test_datatype_preserved()
    test_production_paths()
    test_versions()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — stated uses are captured as a field, on both write paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
