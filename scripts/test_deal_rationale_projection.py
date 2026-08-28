#!/usr/bin/env python3
"""Strategic rationale reaches the Collection review sheet.

WHAT WENT WRONG

Stage 13 classifies why a deal happened -- the one field on the sheet that answers
"why", where every other column answers "what" or "how much". The Collection
validation run stopped at Stage 12, so `rationale_tag` was never populated and the
question was never put in front of Product at all. Reviewers were reading the
fresh run's M&A comments and asking for exactly this.

WHAT CHANGED

Two things, and only two:

  1. Stage 13 joins the validation PIPELINE, immediately after Stage 12. It reads
     the current `summary` row -- `JOIN summary s ON ... is_current = 1` -- so that
     position is forced, not a preference. It is a model call per transaction, so
     the run is one stage longer and one prompt more expensive.

  2. The M&A sheet gains one column, `deal_rationale`: primary rationale first,
     then the secondary rationales in stored order, comma-delimited.

WHAT THIS IS NOT

Not a change to `rationale_tag`. The table keeps its own columns, its JSON
`secondary_rationales` array and its `model_confidence`/`notes`; Stage 14 still
exports them separately. The sheet cell is a projection over what is already
stored, built at read time, and this file pins that it writes nothing.

Confidence and notes are deliberately absent from the cell. The column answers
what the rationale was; a per-cell confidence invites reading it as a score.

The Funding sheet does not get the column. Stage 13 tags acquisitions.

Run from project root:
    python scripts/test_deal_rationale_projection.py
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _load_feeder():
    path = ROOT / "scripts" / "run_collection_validation.py"
    spec = importlib.util.spec_from_file_location("_rcv_rationale", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rcv_rationale"] = mod
    spec.loader.exec_module(mod)
    return mod


def _conn_with_rationales(rows) -> sqlite3.Connection:
    """An in-memory `rationale_tag` alone. The projection reads no other table."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE rationale_tag (
            rationale_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id       TEXT NOT NULL,
            primary_rationale    TEXT,
            secondary_rationales TEXT,
            model_confidence     TEXT,
            notes                TEXT,
            is_current           INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.executemany(
        "INSERT INTO rationale_tag (transaction_id, primary_rationale, "
        "secondary_rationales, model_confidence, notes, is_current) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 1. Stage 13 is in the pipeline, and in the only position it can occupy
# ---------------------------------------------------------------------------

def test_pipeline(fv) -> None:
    print("\nStage 13 runs, after Stage 12:")
    names = [n for n, _ in fv.PIPELINE]
    check("stage_13_rationale_tag is in the pipeline",
          "stage_13_rationale_tag" in names, True)
    check("it is the last stage", names[-1], "stage_13_rationale_tag")
    check("and it follows stage_12_summarize", names[-2], "stage_12_summarize")
    check("nine stages in all", len(names), 9)

    # Not a preference -- Stage 13's own query forces it. If that join ever goes
    # away this check should be revisited deliberately, not silently.
    stage_src = (ROOT / "stages" / "rationale_tag.py").read_text(encoding="utf-8")
    check("stage 13 reads the current summary row, which is why it must run after 12",
          bool(re.search(r"JOIN\s+summary", stage_src, re.I)), True)

    # The stages that stay out stay out. This run has no filings, no agreements
    # and no production export target.
    src = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")
    check("SEC / agreement / export stages still absent",
          any(s in src for s in ("sec_enrich", "sec_trigger", "agreement_extract",
                                 "stages.export")), False)


# ---------------------------------------------------------------------------
# 2. The projection
# ---------------------------------------------------------------------------

def test_projection(fv) -> None:
    fn = getattr(fv, "rationales_by_transaction", None)
    if fn is None:
        print(f"  {FAIL}  rationales_by_transaction is missing")
        _failures.append("rationales_by_transaction is missing")
        return

    print("\nPrimary first, then secondaries in stored order:")
    conn = _conn_with_rationales([
        ("tc_a", "SCALE_CONSOLIDATION",
         json.dumps(["GEOGRAPHIC_EXPANSION", "PRODUCT_OR_TECH_CAPABILITY"]),
         "HIGH", "some note", 1),
    ])
    out = fn(conn)
    check("both parts present, comma-delimited, primary leading", out["tc_a"],
          "SCALE_CONSOLIDATION, GEOGRAPHIC_EXPANSION, PRODUCT_OR_TECH_CAPABILITY")
    # Stored order, not sorted: the model ranks them and that ranking is the value.
    check("secondaries are not re-sorted",
          out["tc_a"].split(", ")[1:],
          ["GEOGRAPHIC_EXPANSION", "PRODUCT_OR_TECH_CAPABILITY"])
    check("confidence is not in the cell", "HIGH" in out["tc_a"], False)
    check("notes are not in the cell", "some note" in out["tc_a"], False)
    conn.close()

    print("\nA primary with no secondaries is just the primary:")
    for secondaries in (None, "", "[]"):
        conn = _conn_with_rationales([("tc_b", "TALENT_ACQUISITION", secondaries,
                                       None, None, 1)])
        check(f"secondary_rationales={secondaries!r} yields a bare primary",
              fn(conn).get("tc_b"), "TALENT_ACQUISITION")
        conn.close()

    print("\nNothing to say produces no cell, rather than an empty one:")
    conn = _conn_with_rationales([("tc_c", None, "[]", None, None, 1)])
    check("a row with no primary and no secondaries is absent", "tc_c" in fn(conn), False)
    conn.close()

    print("\nSuperseded rows are not read:")
    conn = _conn_with_rationales([
        ("tc_d", "FINANCIAL_OR_ARBITRAGE", "[]", None, None, 0),
        ("tc_d", "VERTICAL_INTEGRATION", "[]", None, None, 1),
    ])
    check("only the is_current row projects", fn(conn).get("tc_d"),
          "VERTICAL_INTEGRATION")
    conn.close()

    print("\nMalformed stored JSON degrades to the primary, it does not raise:")
    conn = _conn_with_rationales([("tc_e", "OTHER", "{not json", None, None, 1)])
    check("unparseable secondaries are dropped", fn(conn).get("tc_e"), "OTHER")
    conn.close()

    print("\nA secondary repeating the primary is not printed twice:")
    conn = _conn_with_rationales([
        ("tc_f", "SCALE_CONSOLIDATION",
         json.dumps(["SCALE_CONSOLIDATION", "MARKET_DIVERSIFICATION"]), None, None, 1),
    ])
    check("the duplicate is collapsed", fn(conn).get("tc_f"),
          "SCALE_CONSOLIDATION, MARKET_DIVERSIFICATION")
    conn.close()

    print("\nNon-string entries in the array are ignored:")
    conn = _conn_with_rationales([
        ("tc_g", "OTHER", json.dumps([None, 7, "", "GEOGRAPHIC_EXPANSION"]),
         None, None, 1),
    ])
    check("only the usable string survives", fn(conn).get("tc_g"),
          "OTHER, GEOGRAPHIC_EXPANSION")
    conn.close()

    print("\nThe projection writes nothing:")
    conn = _conn_with_rationales([("tc_h", "OTHER", "[]", None, None, 1)])
    before = conn.execute("SELECT COUNT(*) FROM rationale_tag").fetchone()[0]
    fn(conn)
    after = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(is_current), 0) FROM rationale_tag").fetchone()
    check("row count unchanged", after[0], before)
    check("is_current untouched", after[1], 1)
    conn.close()

    src = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")
    body = src.split("def rationales_by_transaction", 1)[-1].split("\ndef ", 1)[0]
    check("the helper contains no write statement",
          bool(re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", body, re.I)), False)


# ---------------------------------------------------------------------------
# 3. The column, on the M&A sheet only
# ---------------------------------------------------------------------------

def test_column(fv) -> None:
    print("\nThe sheet gains one column, on the M&A side:")
    check("deal_rationale is an M&A column", "deal_rationale" in fv._MA_COLS, True)
    check("it is not a Funding column -- Stage 13 tags acquisitions",
          "deal_rationale" in fv._FUNDING_COLS, False)
    check("M&A sheet is 85 columns", len(fv._MA_COLS), 85)
    check("Funding sheet is unchanged at 46", len(fv._FUNDING_COLS), 46)
    check("sheet version bumped to 1.3", fv._REVIEW_SHEET_VERSION, "1.3")

    # It reads beside the summary, because that is what a reviewer reads it with.
    # Guarded: on a pre-change tree the column is absent and .index() would raise
    # and abort every check below.
    if "deal_rationale" in fv._MA_COLS and "deal_summary" in fv._MA_COLS:
        check("it sits immediately before deal_summary",
              fv._MA_COLS.index("deal_summary") - fv._MA_COLS.index("deal_rationale"), 1)

    # No name collision: the sheet already carries other rationale-ish text.
    check("no duplicate column names on the sheet",
          len(set(fv._MA_COLS)), len(fv._MA_COLS))


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    fv = _load_feeder()
    test_pipeline(fv)
    test_projection(fv)
    test_column(fv)
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — all checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
