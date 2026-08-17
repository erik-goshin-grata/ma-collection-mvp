#!/usr/bin/env python3
"""Quantify the rows a debt-inclusive currency guard will change. READ ONLY.

Run this BEFORE the owed AGGREGATED→CLUSTERED re-aggregation.

Debt-inclusive arithmetic now requires the deal currency and the balance-sheet
currency to be known and equal (decision "total_debt / Cash_ST Extraction and
Debt-Inclusive Arithmetic"). Manual `net_debt` was collected before the currency
columns existed, so rows carrying a manual net debt with no recorded currency will
stop producing a calculated enterprise value on re-derivation. This script measures
that population instead of guessing at it.

**Schema-aware by necessity.** `transaction_record` differs across live databases:
the canonical EV column is `enterprise_value` / `enterprise_value_basis` on corpora
that predate the implied-EV rewire, and `implied_enterprise_value` /
`implied_enterprise_value_basis` on migrated ones. The currency columns
(`net_debt_currency`, `total_debt_currency`, `cash_st_currency`) and `cash_st` are
absent entirely on older corpora. Every column is resolved from PRAGMA table_info
before use, and a missing column is treated as an unrecorded value rather than an
error — a database that never had a currency column has, by definition, no known
currencies, which is exactly the population this script exists to count.

It writes nothing and re-derives nothing. The database is opened read-only, so it
cannot alter a live file even by accident. It never migrates.

Usage:
    python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.db
    python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.db --list
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# EV column pairs in preference order: the canonical two-tier field when the database
# has been migrated, otherwise the legacy compatibility column.
_EV_COLUMN_PAIRS = (
    ("implied_enterprise_value", "implied_enterprise_value_basis"),
    ("enterprise_value", "enterprise_value_basis"),
)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def resolve_ev_columns(cols: set[str]) -> tuple[str | None, str | None]:
    """Pick the EV value/basis pair this database actually has."""
    for value_col, basis_col in _EV_COLUMN_PAIRS:
        if value_col in cols and basis_col in cols:
            return value_col, basis_col
    for value_col, _basis in _EV_COLUMN_PAIRS:
        if value_col in cols:
            return value_col, None
    return None, None


def col_or_null(cols: set[str], name: str) -> str:
    """SQL for a column, or the NULL literal when the database lacks it.

    An absent column and an unpopulated one mean the same thing here — no recorded
    value — so substituting NULL keeps one query shape across schema generations
    instead of branching every predicate.
    """
    return name if name in cols else "NULL"


def analyze(conn: sqlite3.Connection) -> dict:
    """Collect the counts. Pure read; returns a dict for printing or asserting."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transaction_record'"
    ).fetchone():
        raise LookupError("transaction_record table not found in this database")

    cols = table_columns(conn, "transaction_record")
    ev_value, ev_basis = resolve_ev_columns(cols)

    def count(where: str) -> int:
        return int(
            conn.execute(f"SELECT COUNT(*) FROM transaction_record WHERE {where}").fetchone()[0]
        )

    result: dict = {
        "ev_value_column": ev_value,
        "ev_basis_column": ev_basis,
        "has_net_debt_currency": "net_debt_currency" in cols,
        "has_cash_st": "cash_st" in cols,
        "total_rows": count("1=1"),
        "notes": [],
    }

    net_debt_sql = col_or_null(cols, "net_debt")
    deal_ccy_sql = col_or_null(cols, "deal_value_currency")
    net_debt_ccy_sql = col_or_null(cols, "net_debt_currency")

    result["with_net_debt"] = count(f"{net_debt_sql} IS NOT NULL")

    if ev_value is None:
        result["notes"].append("no enterprise-value column found — nothing to measure")
        return result

    result["with_ev"] = count(f"{ev_value} IS NOT NULL")

    if ev_basis is None:
        result["notes"].append(
            f"{ev_value} present but no basis column — STATED and calculated rows "
            "cannot be separated, so the at-risk count is not computable"
        )
        return result

    # STATED is a single source-stated figure, not a sum, so the currency guard does
    # not touch it. Anything else non-null is a calculated basis. Matching by
    # "not STATED" rather than by a name pattern keeps this correct across the
    # older and newer basis vocabularies.
    calculated = f"{ev_value} IS NOT NULL AND {ev_basis} IS NOT NULL AND {ev_basis} <> 'STATED'"
    result["stated_ev"] = count(f"{ev_value} IS NOT NULL AND {ev_basis} = 'STATED'")
    result["calculated_ev"] = count(calculated)
    result["ev_no_basis"] = count(f"{ev_value} IS NOT NULL AND {ev_basis} IS NULL")

    result["basis_breakdown"] = [
        (row[0], row[1])
        for row in conn.execute(
            f"SELECT {ev_basis}, COUNT(*) FROM transaction_record "
            f"WHERE {ev_value} IS NOT NULL GROUP BY {ev_basis} ORDER BY COUNT(*) DESC"
        )
    ]

    result["missing_net_debt_currency"] = count(
        f"{net_debt_sql} IS NOT NULL AND {net_debt_ccy_sql} IS NULL"
    )
    result["currency_mismatch"] = count(
        f"{net_debt_ccy_sql} IS NOT NULL AND {deal_ccy_sql} IS NOT NULL "
        f"AND {net_debt_ccy_sql} <> {deal_ccy_sql}"
    )
    result["at_risk_missing_net_debt_currency"] = count(
        f"{calculated} AND {net_debt_sql} IS NOT NULL AND {net_debt_ccy_sql} IS NULL"
    )
    result["at_risk_missing_deal_currency"] = count(
        f"{calculated} AND {net_debt_sql} IS NOT NULL AND {deal_ccy_sql} IS NULL"
    )
    result["at_risk_total"] = count(
        f"{calculated} AND {net_debt_sql} IS NOT NULL "
        f"AND ({net_debt_ccy_sql} IS NULL OR {deal_ccy_sql} IS NULL)"
    )
    result["calculated_without_net_debt"] = count(f"{calculated} AND {net_debt_sql} IS NULL")

    total_debt_sql = col_or_null(cols, "total_debt")
    result["total_debt_without_currency"] = count(
        f"{total_debt_sql} IS NOT NULL AND {col_or_null(cols, 'total_debt_currency')} IS NULL"
    )
    if "cash_st" in cols:
        result["cash_st_without_currency"] = count(
            f"cash_st IS NOT NULL AND {col_or_null(cols, 'cash_st_currency')} IS NULL"
        )
    if "transaction_value_basis" in cols:
        result["tv_debt_basis"] = count("transaction_value_basis = 'EQUITY_PLUS_TOTAL_DEBT'")

    if not result["has_net_debt_currency"]:
        result["notes"].append(
            "net_debt_currency column absent — every calculated-basis row with a "
            "net_debt is at risk, because no currency can be known"
        )
    if not result["has_cash_st"]:
        result["notes"].append("cash_st column absent — component-path counts skipped")
    return result


def affected_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    cols = table_columns(conn, "transaction_record")
    ev_value, ev_basis = resolve_ev_columns(cols)
    if ev_value is None or ev_basis is None:
        return []
    net_debt_sql = col_or_null(cols, "net_debt")
    deal_ccy_sql = col_or_null(cols, "deal_value_currency")
    net_debt_ccy_sql = col_or_null(cols, "net_debt_currency")
    return conn.execute(
        f"""
        SELECT transaction_id,
               {deal_ccy_sql}   AS deal_value_currency,
               {net_debt_sql}   AS net_debt,
               {net_debt_ccy_sql} AS net_debt_currency,
               {ev_value}       AS ev_value,
               {ev_basis}       AS ev_basis
        FROM transaction_record
        WHERE {ev_value} IS NOT NULL
          AND {ev_basis} IS NOT NULL AND {ev_basis} <> 'STATED'
          AND {net_debt_sql} IS NOT NULL
          AND ({net_debt_ccy_sql} IS NULL OR {deal_ccy_sql} IS NULL)
        ORDER BY {ev_value} DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the SQLite database.")
    parser.add_argument("--list", action="store_true", help="List affected transaction_ids.")
    parser.add_argument("--limit", type=int, default=50, help="Max ids to list (default 50).")
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 2

    # Read-only URI: this must never be able to modify a live database.
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            data = analyze(conn)
        except LookupError as exc:
            print(f"{exc}", file=sys.stderr)
            return 2

        print(f"Database: {args.db}")
        print(f"  EV columns in use:                        {data['ev_value_column']} / {data['ev_basis_column']}")
        print(f"  transaction_record rows:                  {data['total_rows']}")
        print(f"  rows with net_debt:                       {data['with_net_debt']}")

        if data.get("ev_value_column") is None or "calculated_ev" not in data:
            for note in data["notes"]:
                print(f"\n  NOTE: {note}")
            return 0

        print(f"  rows with an enterprise value:            {data['with_ev']}")
        print(f"    basis = STATED:                         {data['stated_ev']}   (unaffected — not a sum)")
        print(f"    basis = calculated:                     {data['calculated_ev']}   (subject to the guard)")
        if data["ev_no_basis"]:
            print(f"    basis NULL:                             {data['ev_no_basis']}   (indeterminate)")

        print("\n  Basis values present:")
        for basis, n in data["basis_breakdown"]:
            print(f"    {basis!r}: {n}")

        print(f"\n  net_debt present but currency unrecorded:  {data['missing_net_debt_currency']}")
        print(f"  net_debt currency <> deal currency:        {data['currency_mismatch']}")

        print("\n  WILL LOSE the calculated enterprise value on re-aggregation:")
        print(f"    net-debt currency unrecorded:            {data['at_risk_missing_net_debt_currency']}")
        print(f"    deal currency unrecorded:                {data['at_risk_missing_deal_currency']}")
        print(f"    TOTAL (deduplicated):                    {data['at_risk_total']}")
        if data["calculated_without_net_debt"]:
            print(
                f"    calculated basis but net_debt NULL:      {data['calculated_without_net_debt']}"
                "   (inspect separately — basis and inputs disagree)"
            )

        print(f"\n  total_debt without currency:              {data['total_debt_without_currency']}")
        if "cash_st_without_currency" in data:
            print(f"  cash_st without currency:                 {data['cash_st_without_currency']}")
        if "tv_debt_basis" in data:
            print(
                f"  transaction_value EQUITY_PLUS_TOTAL_DEBT: {data['tv_debt_basis']}"
                "   (falls back to EQUITY_VALUE_ONLY, does not go null)"
            )

        for note in data["notes"]:
            print(f"\n  NOTE: {note}")

        if args.list:
            rows = affected_rows(conn, args.limit)
            if rows:
                print("\n  Affected transaction_ids (largest EV first):")
                for row in rows:
                    print(
                        f"    {row['transaction_id']}  deal_ccy={row['deal_value_currency']!r} "
                        f"net_debt={row['net_debt']} ccy={row['net_debt_currency']!r} "
                        f"EV={row['ev_value']} ({row['ev_basis']})"
                    )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
