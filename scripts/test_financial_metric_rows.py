#!/usr/bin/env python3
"""Source-stated revenue and EBITDA become normalized canonical rows.

WHAT WENT WRONG

Financial facts were held as flat columns -- target_revenue, its period type and end,
target_ebitda and the same three, and ONE financials_currency shared between both.
Two things that shape cannot express:

  1. A currency per figure. The metric-row policy's first rule is that currency
     attaches to the value it qualifies and a row never inherits one from another row.
     A single shared column cannot say revenue was USD and EBITDA was EUR; today that
     disagreement collapses to NULL and BOTH figures lose a currency each of them had.
  2. The precision of a period end. "2025" and "2025-12-31" sit in one TEXT column.

WHAT THIS TABLE IS

Canonical resolved facts, on the R3.3 rule: the value is the one reconciliation chose,
read from the resolved field values and never from a staging row, so an unresolved
conflict produces NO row. Not a second observation store -- observations stay in
transaction_field_observation and disagreements in aggregation_conflict_log.

WHAT IS DELIBERATELY NOT HERE

  * Calculated rows. is_calculated is 0 on every row: revenue and EBITDA are collected,
    never computed. Nothing is derived to fill the table, and no figure is ever
    recovered from an as-reported multiple -- dividing a price by a multiple would
    manufacture the third fact.
  * FX. fx_rate and fx_rate_date record a conversion that was performed; none was.
  * Balance-sheet metrics. TOTAL_DEBT, CASH_AND_EQUIVALENTS and NET_DEBT are canonical
    types and are not written yet, pending the two open balance-sheet findings. Product
    ruling recorded: when they are normalized, their reporting-period context is Q or A
    with the point-in-time date on the balance-sheet as-of date, never mapped into
    LTM / NTM / INTERIM_YTD.
  * Metric types this implementation does not author -- ARR, MRR, EBIT, NET_INCOME,
    FREE_CASH_FLOW, GROSS_PROFIT, SHAREHOLDERS_EQUITY. Coverage gaps, not empty rows.

TODAY'S CALCULATED OUTPUTS ARE UNCHANGED. _compute_multiples, the four flat ev_to_*
columns, multiple_quality and export are untouched, and this file asserts it.

Run from project root:
    python scripts/test_financial_metric_rows.py
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db as _db
from stages import aggregate as agg

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []
log = logging.getLogger("t")
logging.basicConfig(level=logging.CRITICAL)


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _fresh():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    _db.init_db(p)
    return _db.get_connection(p)


def _ready(conn) -> bool:
    """Guarded: seeding a table that does not exist yet raises and ABORTS the run,
    leaving every check below unproven and a pre-change run looking healthier than it
    is. One honest failure instead."""
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transaction_financial'"
    ).fetchone())


def _seed_txn(conn, txn="tc_1"):
    conn.execute("INSERT INTO source_raw (source_raw_id, source_type, source_tier, url,"
                 " raw_html, clean_text, source_status, fetched_at) VALUES"
                 " (1,'PR_NEWSWIRE','T2','https://e.test/1','<h/>','body','FETCHED','2026-08-27')")
    conn.execute("INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
                 " transaction_cluster_id) VALUES (1,1,'CLUSTERED',?)", (txn,))
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES (?,1)", (txn,))
    conn.commit()


BUNDLE = {"sources": [{"staging_extraction_id": 1, "source_raw_id": 1}]}


def _write(conn, fv, currencies=None, txn="tc_1"):
    fn = getattr(agg, "_write_financial_metrics", None)
    if fn is None:
        raise RuntimeError("_write_financial_metrics is missing")
    return fn(
        conn, txn, fv, currencies or {}, BUNDLE, log)


def _rows(conn, txn="tc_1"):
    return conn.execute("SELECT * FROM transaction_financial WHERE transaction_id=?"
                        " ORDER BY financial_id", (txn,)).fetchall()


# ---------------------------------------------------------------------------
# 1. Precision vocabulary — one, not two
# ---------------------------------------------------------------------------

def test_precision() -> None:
    print("\nOne precision vocabulary, the canonical one:")
    fn = agg._period_end_precision
    check("a bare year is 'year'", fn("2026"), "year")
    check("a full date is 'exact'", fn("2025-12-31"), "exact")
    check("a year-month is 'month'", fn("2025-06"), "month")
    check("nothing stated is None", fn(None), None)
    # 012 shipped DAY|MONTH|QUARTER|YEAR; the canonical metric row says
    # exact|month|quarter|year, and DAY has no canonical counterpart.
    check("the old uppercase vocabulary is gone",
          {fn("2026"), fn("2025-12-31")} & {"YEAR", "DAY", "MONTH"}, set())
    # Guarded: on a pre-change tree the migration does not exist, and an unguarded
    # read raises and ABORTS the run -- every check below it would go unproven and the
    # run would look healthier than it is.
    mig_path = ROOT / "schema" / "014_v3_transaction_financial.sql"
    mig = mig_path.read_text(encoding="utf-8") if mig_path.exists() else ""
    check("existing multiple rows are normalized by the migration",
          all(f"period_end_date_precision = '{v}'" in mig
              for v in ("exact", "month", "quarter", "year")), True)


# ---------------------------------------------------------------------------
# 2. The rows
# ---------------------------------------------------------------------------

def test_rows() -> None:
    conn = _fresh()
    if not _ready(conn) or not hasattr(agg, "_write_financial_metrics"):
        print(f"  {FAIL}  migration 014 / the metric writer are not present")
        _failures.append("migration 014 / the metric writer are not present")
        conn.close(); return
    _seed_txn(conn)

    print("\nStated LTM revenue:")
    _write(conn, {"target_revenue": 385_000_000.0,
                  "target_revenue_period_type_v2": "LTM",
                  "target_revenue_period_end": "2026-03-31"},
           {"target_revenue": "USD"})
    conn.commit()
    r = _rows(conn)[0]
    check("one row", len(_rows(conn)), 1)
    check("metric_type REVENUE", r["metric_type"], "REVENUE")
    check("value", r["value_captured"], 385_000_000.0)
    check("period_type LTM", r["period_type"], "LTM")
    check("period end", r["period_end_date"], "2026-03-31")
    check("precision exact", r["period_end_date_precision"], "exact")
    check("currency preserved", r["value_currency"], "USD")
    check("is_calculated 0 — source-stated", r["is_calculated"], 0)
    check("fx_rate null — no conversion was performed", r["fx_rate"], None)
    check("fx_rate_date null", r["fx_rate_date"], None)
    check("provenance carried", (r["staging_extraction_id"], r["source_raw_id"]), (1, 1))

    print("\nStated NTM EBITDA:")
    _write(conn, {"target_ebitda": 95_000_000.0,
                  "target_ebitda_period_type_v2": "NTM",
                  "target_ebitda_period_end": "2027-06-30"},
           {"target_ebitda": "USD"})
    conn.commit()
    r = _rows(conn)[0]
    check("metric_type EBITDA", r["metric_type"], "EBITDA")
    check("period_type NTM — not folded into LTM", r["period_type"], "NTM")

    print("\nA named year keeps year precision and is not expanded:")
    _write(conn, {"target_revenue": 50_000_000.0,
                  "target_revenue_period_type_v2": "ANNUAL",
                  "target_revenue_period_end": "2026"},
           {"target_revenue": "USD"})
    conn.commit()
    r = _rows(conn)[0]
    check("period_type ANNUAL — not collapsed to LTM or NTM", r["period_type"], "ANNUAL")
    check("period end is the bare year", r["period_end_date"], "2026")
    check("precision year", r["period_end_date_precision"], "year")

    print("\nINTERIM_YTD survives as itself:")
    _write(conn, {"target_revenue": 10.0, "target_revenue_period_type_v2": "INTERIM_YTD",
                  "target_revenue_period_end": "2026-06-30"}, {"target_revenue": "USD"})
    conn.commit()
    check("carried, not translated", _rows(conn)[0]["period_type"], "INTERIM_YTD")

    print("\nRevenue and EBITDA on one transaction — two rows:")
    _write(conn, {"target_revenue": 100.0, "target_revenue_period_type_v2": "LTM",
                  "target_ebitda": 20.0, "target_ebitda_period_type_v2": "LTM"},
           {"target_revenue": "USD", "target_ebitda": "USD"})
    conn.commit()
    rows = _rows(conn)
    check("two rows", len(rows), 2)
    check("one of each type", sorted(x["metric_type"] for x in rows), ["EBITDA", "REVENUE"])

    print("\nCurrency belongs to the row, not to the transaction:")
    # The case the shared column cannot hold: the two metrics disagree, so
    # financials_currency is null -- and each row still keeps its own.
    _write(conn, {"target_revenue": 100.0, "target_ebitda": 20.0,
                  "financials_currency": None},
           {"target_revenue": "USD", "target_ebitda": "EUR"})
    conn.commit()
    got = {x["metric_type"]: x["value_currency"] for x in _rows(conn)}
    check("revenue keeps USD", got["REVENUE"], "USD")
    check("EBITDA keeps EUR", got["EBITDA"], "EUR")
    check("neither inherited the other's", got["REVENUE"] != got["EBITDA"], True)

    print("\nAn unstated currency is null, never borrowed:")
    _write(conn, {"target_revenue": 100.0, "target_ebitda": 20.0},
           {"target_revenue": "USD"})          # EBITDA's source stated none
    conn.commit()
    got = {x["metric_type"]: x["value_currency"] for x in _rows(conn)}
    check("EBITDA currency is null", got["EBITDA"], None)
    check("and revenue's was not lent to it", got["REVENUE"], "USD")

    print("\nUnresolved conflict — no canonical row for that fact:")
    # _pick_value leaves an unresolvable field None; no value, no row. The
    # observations and the conflict record are elsewhere and untouched.
    _write(conn, {"target_revenue": None, "target_ebitda": 20.0},
           {"target_ebitda": "USD"})
    conn.commit()
    rows = _rows(conn)
    check("only the resolved fact is written", [x["metric_type"] for x in rows], ["EBITDA"])

    print("\nNothing stated at all — no rows:")
    _write(conn, {})
    conn.commit()
    check("no rows", len(_rows(conn)), 0)

    print("\nRe-aggregation replaces this transaction's rows:")
    fv = {"target_revenue": 100.0, "target_ebitda": 20.0}
    cur = {"target_revenue": "USD", "target_ebitda": "USD"}
    _write(conn, fv, cur); _write(conn, fv, cur); conn.commit()
    check("still two rows, not four", len(_rows(conn)), 2)

    print("\n  ...and only this transaction's:")
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES ('tc_2',1)")
    conn.execute("INSERT INTO transaction_financial (transaction_id, metric_type,"
                 " value_captured) VALUES ('tc_2','REVENUE',999.0)")
    conn.commit()
    _write(conn, fv, cur); conn.commit()
    check("the other transaction's row survives", len(_rows(conn, "tc_2")), 1)
    check("with its value intact", _rows(conn, "tc_2")[0]["value_captured"], 999.0)

    print("\nNo back-solving from an as-reported multiple:")
    src = (ROOT / "stages" / "aggregate.py").read_text(encoding="utf-8")
    body = (src.split("def _write_financial_metrics(", 1)[1].split("\ndef ", 1)[0]
            if "def _write_financial_metrics(" in src else "")
    code = body.split('"""', 2)[-1]
    check("the writer divides nothing", "/" in code, False)
    check("and never reads a multiple", "multiple" in code.lower(), False)
    conn.close()


# ---------------------------------------------------------------------------
# 3. Nothing else moved
# ---------------------------------------------------------------------------

def test_unchanged() -> None:
    print("\nToday's calculated outputs are unchanged:")
    r = agg._compute_multiples(
        implied_enterprise_value=1_200_000_000.0, value_currency="USD",
        target_revenue=None, target_revenue_period_type=None,
        target_ebitda=100_000_000.0, target_ebitda_period_type="LTM",
        financials_currency="USD", log=log, cluster_id="c1")
    check("EV/EBITDA LTM still 12.0", r["ev_to_ebitda_ltm"], 12.0)
    check("quality still CALCULATED", r["multiple_quality"], "CALCULATED")
    r = agg._compute_multiples(
        implied_enterprise_value=None, value_currency="USD",
        target_revenue=None, target_revenue_period_type=None,
        target_ebitda=None, target_ebitda_period_type=None,
        financials_currency="USD", log=log, cluster_id="c2")
    check("no EV still NOT_CALCULABLE", r["multiple_quality"], "NOT_CALCULABLE")

    check("Stage 9 still owns 120 canonical columns",
          len(getattr(agg, "_STAGE9_OWNED_COLUMNS", ())), 120)
    check("the flat financial columns are still Stage 9's",
          all(c in agg._STAGE9_OWNED_COLUMNS
              for c in ("target_revenue", "target_ebitda", "financials_currency")), True)
    check("no metric row field leaked into the column list",
          any(c in agg._STAGE9_OWNED_COLUMNS
              for c in ("value_captured", "metric_type", "is_calculated")), False)
    check("aggregate version is 0.12", agg._VERSION, "0.12")

    print("\nBalance-sheet metrics are deliberately not written:")
    src = (ROOT / "stages" / "aggregate.py").read_text(encoding="utf-8")
    body = (src.split("def _write_financial_metrics(", 1)[1].split("\ndef ", 1)[0]
            if "def _write_financial_metrics(" in src else "")
    types = getattr(agg, "_FINANCIAL_METRIC_TYPES", ())
    check("only REVENUE and EBITDA are written",
          sorted(t[0] for t in types), ["EBITDA", "REVENUE"])
    check("no balance-sheet type appears",
          any(t in body for t in ("TOTAL_DEBT", "CASH_AND_EQUIVALENTS", "NET_DEBT")), False)
    check("nor POINT_IN_TIME", "POINT_IN_TIME" in body, False)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_precision()
    test_rows()
    test_unchanged()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — stated financials are rows; nothing is calculated, borrowed or converted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
