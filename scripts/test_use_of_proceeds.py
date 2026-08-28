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

A BOUNDED VOCABULARY (0.6)

The archived draft asked for "1-2 sentences" of source language. 0.5 replaced that
with one- or two-word noun phrases in the source's own words -- normalized, but
unbounded. A review of seven fresh funding rows showed the predictable result: one
release produced ten items including five separate "...capabilities" and both
"customer deliveries" and "customer deployments", while others ran to four words.
Free text cannot be aggregated, compared across rows, or conflict-resolved.

0.6 is not a length rule. Eleven categories plus OTHER, enumerated from the
phrasing the funding corpus actually uses -- the method `advisor_specialty`
established -- with ACQUISITIONS, DEBT_REPAYMENT and WORKING_CAPITAL added on
Product ruling as common, materially distinct uses that should not route through
OTHER. Designed on the `strategic_rationale` pattern: bounded taxonomy,
source-stated evidence only, OTHER as the honest fallback -- but flat rather than
primary-plus-secondary, because a round's proceeds genuinely split several ways
with no ranking stated.

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

# The taxonomy Product approved, written out here rather than imported, so this file
# asserts the agreed vocabulary rather than echoing whatever the stage happens to define.
_APPROVED_USES = {
    "HIRING", "PRODUCT_AND_TECHNOLOGY", "MANUFACTURING_AND_SUPPLY_CHAIN",
    "GO_TO_MARKET", "MARKET_EXPANSION", "FACILITIES_AND_EQUIPMENT",
    "REGULATORY_AND_COMPLIANCE", "ACQUISITIONS", "DEBT_REPAYMENT",
    "WORKING_CAPITAL", "GENERAL_CORPORATE", "OTHER",
}

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
    check("classified into a vocabulary, not the source's words",
          "classified into the vocabulary below" in flat
          and "not the source's own words" in flat, True)
    check("comma-separated, in the order stated",
          "comma-separated, in the order the source states them" in flat, True)
    check("every independently supported category",
          "Return every category the source independently supports" in flat, True)
    check("null when unstated",
          "null when the source does not say what the capital is for" in flat, True)
    check("only vocabulary values may be returned",
          "Return only values from the list above" in flat, True)
    check("no source phrasing, no invented category, no repeats",
          "Do not write the source's phrasing, do not invent a category, and do not "
          "repeat one" in flat, True)

    print("\nAll twelve values are delivered with a gloss:")
    # Read defensively. On a pre-change tree the constant does not exist, and an
    # AttributeError here would abort the run and leave every control below unproven --
    # which reads as "nothing else is broken" when nothing else was tested. The expected
    # vocabulary is written out rather than read from the stage, so this file states the
    # taxonomy Product approved instead of echoing whatever the code happens to hold.
    vocab = getattr(fhc, "_VALID_PROCEEDS_USES", frozenset())
    check("the approved vocabulary, exactly", sorted(vocab), sorted(_APPROVED_USES))
    for value in sorted(_APPROVED_USES):
        check(f"{value} glossed in the delivered contract", f"{value} —" in flat, True)
    check("exactly twelve values", len(vocab), 12)
    check("the three Product-added categories are present",
          {"ACQUISITIONS", "DEBT_REPAYMENT", "WORKING_CAPITAL"} <= set(vocab), True)

    print("\nThe two boundaries the corpus turns on are written out:")
    check("headcount is HIRING whatever the department",
          "Headcount is HIRING whatever the department" in flat, True)
    check('"expand the sales team" is named explicitly',
          '"Expand the sales team" is HIRING, not GO_TO_MARKET' in flat, True)
    check("building vs selling",
          '"launch its initial product" is GO_TO_MARKET' in flat, True)
    check("a source spanning both returns both",
          "supports both categories, and both are returned" in flat, True)

    print("\nThe worked examples are delivered, including the over-splitting case:")
    check("fixture one", '"HIRING, PRODUCT_AND_TECHNOLOGY"' in flat, True)
    check("fixture two", '"HIRING, GO_TO_MARKET"' in flat, True)
    check("the ten-item row is shown collapsing to four",
          "four categories, not one per phrase" in flat, True)
    check("and why", "Several phrases describing the same kind of use are one category"
          in flat, True)

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
    check("the example value is vocabulary, not the source's phrasing",
          txn.get("use_of_proceeds"), "HIRING, PRODUCT_AND_TECHNOLOGY")

    print("\nThe board pair stays unauthored — V3 residue, not an oversight:")
    for field in ("has_board_seat", "board_seat_notes"):
        check(f"{field} absent from the delivered contract", field in system, False)
        check(f"{field} absent from the response object", field in json.dumps(obj), False)
    check("the word 'board' appears nowhere in the fence", "board" in system.lower(), False)

    print("\nSection 6 declares it too:")
    md = (ROOT / "prompts" / "funding_hc_extraction.md").read_text(encoding="utf-8")
    i, j = md.find("## 6. Output Schema"), md.find("## 7. Few-Shot Examples")
    sec6 = md[i:j] if i != -1 and j != -1 else ""
    check("section 6 declares the vocabulary, not a bare string",
          '"use_of_proceeds": "comma-separated subset of HIRING' in sec6, True)
    check("section 6 lists all twelve", all(v in sec6 for v in _APPROVED_USES), True)
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
    # No repeating structure was introduced. The value is cleaned once and the SAME
    # cleaned scalar reaches both write paths -- reading txn.get() twice is how a field
    # comes to differ between a single- and a multi-transaction row (bug #6).
    src = (ROOT / "stages" / "funding_hc_extract.py").read_text(encoding="utf-8")
    check("the raw value is read exactly once",
          src.count('txn.get("use_of_proceeds")'), 1)
    check("cleaned once, beside the other cleaned field",
          '_clean_proceeds(txn.get("use_of_proceeds"), log, eid)' in src, True)
    # Three indented occurrences, which is what a correctly wired field looks like:
    # the round_params tuple (UPDATE path), the INSERT column list, and the INSERT's
    # own param tuple. Plus `use_of_proceeds = ?` in the SET clause, checked next.
    check("the cleaned scalar reaches both write paths",
          src.count("\n                use_of_proceeds,")
          + src.count("\n                        use_of_proceeds,"), 3)
    check("and the UPDATE path has its SET assignment",
          "use_of_proceeds = ?," in src, True)
    check("no JSON or list structure was introduced",
          bool(re.search(r"use_of_proceeds.*(?:json\.dumps|\[\])", src)), False)


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
        "transaction_terms_disclosure_status": "DISCLOSED",
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
        _txn("TechCo", use_of_proceeds="HIRING, PRODUCT_AND_TECHNOLOGY")])
    check("one transaction written", len(rows), 1)
    check("staging", rows[0]["staging"], "HIRING, PRODUCT_AND_TECHNOLOGY")
    check("observation row written", rows[0]["ledger_rows"], 1)
    check(f"canonical (read_source={DEFAULT_AGGREGATION_READ_SOURCE})",
          rows[0]["canonical"], "HIRING, PRODUCT_AND_TECHNOLOGY")
    check("CONTROL round_label unchanged", rows[0]["ctl_round_label"], "Series B")
    check("CONTROL total_raised_to_date unchanged",
          rows[0]["ctl_total_raised"], 68000000.0)

    print("\nMulti-transaction source — the INSERT path (bug #6 territory):")
    rows = _run_production("multi", [
        _txn("AlphaCo", use_of_proceeds="hiring"),   # lowercase: case is folded
        _txn("BetaCo", use_of_proceeds="HIRING, GO_TO_MARKET"),
        _txn("GammaCo"),
    ])
    check("three transactions written", len(rows), 3)
    check("first (UPDATE path) staging, case folded", rows[0]["staging"], "HIRING")
    check("first (UPDATE path) canonical", rows[0]["canonical"], "HIRING")
    check("second (INSERT path) staging", rows[1]["staging"], "HIRING, GO_TO_MARKET")
    check("second (INSERT path) canonical",
          rows[1]["canonical"], "HIRING, GO_TO_MARKET")
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

def test_filter() -> None:
    """The vocabulary is enforced, not merely published.

    A bounded taxonomy that nothing checks is not bounded in the data. This is the
    same filter `rationale_tag` applies to `secondary_rationales`, and the same
    clear-don't-reject posture `_clean_pct` uses: an unusable optional value loses
    its own field and the funding row still stands.
    """
    print("\nThe vocabulary is enforced at the parser:")

    class _Log:
        def __init__(self): self.msgs = []
        def warning(self, fmt, *a): self.msgs.append(fmt % a)

    def clean(raw):
        log = _Log()
        fn = getattr(fhc, "_clean_proceeds", None)
        if fn is None:
            return "<no _clean_proceeds>", log.msgs
        return fn(raw, log, 1), log.msgs

    check("a single value", clean("HIRING")[0], "HIRING")
    check("several values keep source order",
          clean("GO_TO_MARKET, HIRING, MARKET_EXPANSION")[0],
          "GO_TO_MARKET, HIRING, MARKET_EXPANSION")
    check("case is folded — the vocabulary is the contract, not the capitalization",
          clean("hiring, Go_To_Market")[0], "HIRING, GO_TO_MARKET")
    check("whitespace is tolerated", clean("  HIRING ,  OTHER  ")[0], "HIRING, OTHER")

    print("\n  Repeats and non-vocabulary values are dropped, and logged:")
    check("a repeated category appears once",
          clean("HIRING, GO_TO_MARKET, HIRING")[0], "HIRING, GO_TO_MARKET")
    value, msgs = clean("HIRING, sales team, PRODUCT_AND_TECHNOLOGY")
    check("0.5-style free text is dropped", value, "HIRING, PRODUCT_AND_TECHNOLOGY")
    check("and the drop is logged with the value",
          any("sales team" in m for m in msgs), True)
    value, msgs = clean("customer deliveries, customer deployments")
    check("a row of pure free text clears the field", value, None)
    check("and says so", any("dropping non-vocabulary" in m for m in msgs), True)
    check("an invented category is dropped", clean("HIRING, TALENT")[0], "HIRING")

    print("\n  A validator, never a classifier:")
    # The parser may case-normalize, split, dedupe and drop. It must never MAP a phrase
    # onto a category -- "sales team" -> HIRING is a reading of the source, and reading
    # the source is the model's job under the prompt contract. A translation table here
    # would silently become a second, untested classifier with no evidence gate.
    check("'sales team' is dropped, not translated to HIRING", clean("sales team")[0], None)
    check("'repay debt' is dropped, not translated to DEBT_REPAYMENT",
          clean("repay debt")[0], None)
    check("'hiring engineers' is dropped — it is not the value HIRING",
          clean("hiring engineers")[0], None)
    src_fn = (ROOT / "stages" / "funding_hc_extract.py").read_text(encoding="utf-8")
    i = src_fn.index("def _clean_proceeds")
    body = src_fn[i:src_fn.index("\ndef ", i + 10)]
    check("the cleaner holds no mapping table",
          any(tok in body for tok in ("_MAP", "startswith(", "in raw", "replace(")), False)
    check("it only folds case and splits on commas",
          ".upper()" in body and '.split(",")' in body, True)

    print("\n  Absent and empty inputs stay absent:")
    check("null in, null out", clean(None)[0], None)
    check("empty string", clean("")[0], None)
    check("whitespace only", clean("   ")[0], None)
    check("commas only", clean(" , , ")[0], None)

    print("\n  Clearing, never rejecting — the funding row survives:")
    check("_validate does not police this field",
          "use_of_proceeds" in (fhc._validate.__doc__ or ""), False)
    src = (ROOT / "stages" / "funding_hc_extract.py").read_text(encoding="utf-8")
    check("no PromptFailure is raised for a bad value",
          bool(re.search(r"use_of_proceeds.*PromptFailure", src)), False)


def test_versions() -> None:
    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "funding_hc_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, fhc._VERSION, "0.6")
    check("the 0.5 row still records the field's origin",
          bool(re.search(r"^\| 0\.5 \|", md, re.M)), True)
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
    test_filter()
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
