#!/usr/bin/env python3
"""Quantify the rows a debt-inclusive currency guard will change. READ ONLY.

Run this BEFORE the owed AGGREGATED→CLUSTERED re-aggregation.

Debt-inclusive arithmetic now requires the deal currency and the balance-sheet
currency to be known and equal (decision "total_debt / Cash_ST Extraction and
Debt-Inclusive Arithmetic"). Manual `net_debt` was collected before the currency
columns existed, so rows carrying a manual net debt with no recorded currency will
stop producing a calculated `implied_enterprise_value` on re-derivation. This script
measures that population instead of guessing at it.

It writes nothing and re-derives nothing. It opens the database read-only, so it
cannot alter a live file even by accident.

Usage:
    python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.db
    python scripts/quantify_net_debt_currency_gap.py --db data/ma_mvp.db --list
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _count(conn: sqlite3.Connection, where: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM transaction_record WHERE {where}").fetchone()[0])


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

    cols = _columns(conn, "transaction_record")
    has_currency_cols = "net_debt_currency" in cols
    has_bs_cols = "total_debt_currency" in cols

    total_rows = _count(conn, "1=1")
    with_net_debt = _count(conn, "net_debt IS NOT NULL")
    with_implied_ev = _count(conn, "implied_enterprise_value IS NOT NULL")
    calculated_ev = _count(
        conn,
        "implied_enterprise_value IS NOT NULL "
        "AND implied_enterprise_value_basis LIKE 'IMPLIED_EQUITY_PLUS_%'",
    )
    stated_ev = _count(
        conn, "implied_enterprise_value IS NOT NULL AND implied_enterprise_value_basis = 'STATED'"
    )

    print(f"Database: {args.db}")
    print(f"  transaction_record rows:                  {total_rows}")
    print(f"  rows with net_debt:                       {with_net_debt}")
    print(f"  rows with implied_enterprise_value:       {with_implied_ev}")
    print(f"    of which basis = STATED:                {stated_ev}   (unaffected — not a sum)")
    print(f"    of which basis = IMPLIED_EQUITY_PLUS_*: {calculated_ev}   (subject to the guard)")

    if not has_currency_cols:
        print(
            "\n  net_debt_currency column absent — this DB predates the anchor columns.\n"
            "  Every one of the "
            f"{calculated_ev} calculated-basis rows above will lose its\n"
            "  implied_enterprise_value on re-derivation until a currency is recorded."
        )
        conn.close()
        return 0

    missing_currency = _count(conn, "net_debt IS NOT NULL AND net_debt_currency IS NULL")
    at_risk = _count(
        conn,
        "net_debt IS NOT NULL AND net_debt_currency IS NULL "
        "AND implied_enterprise_value IS NOT NULL "
        "AND implied_enterprise_value_basis LIKE 'IMPLIED_EQUITY_PLUS_%'",
    )
    mismatched = _count(
        conn,
        "net_debt_currency IS NOT NULL AND deal_value_currency IS NOT NULL "
        "AND net_debt_currency <> deal_value_currency",
    )
    deal_ccy_missing = _count(
        conn,
        "net_debt IS NOT NULL AND deal_value_currency IS NULL "
        "AND implied_enterprise_value IS NOT NULL "
        "AND implied_enterprise_value_basis LIKE 'IMPLIED_EQUITY_PLUS_%'",
    )

    print(f"\n  net_debt present but net_debt_currency NULL: {missing_currency}")
    print(f"  net_debt_currency <> deal_value_currency:    {mismatched}")
    print("\n  WILL LOSE implied_enterprise_value on re-aggregation:")
    print(f"    missing net-debt currency:                 {at_risk}")
    print(f"    missing deal currency:                     {deal_ccy_missing}")

    if has_bs_cols:
        td_no_ccy = _count(conn, "total_debt IS NOT NULL AND total_debt_currency IS NULL")
        cash_no_ccy = _count(conn, "cash_st IS NOT NULL AND cash_st_currency IS NULL")
        tv_debt_basis = _count(conn, "transaction_value_basis = 'EQUITY_PLUS_TOTAL_DEBT'")
        print(f"\n  total_debt without currency:                 {td_no_ccy}")
        print(f"  cash_st without currency:                    {cash_no_ccy}")
        print(
            f"  transaction_value at EQUITY_PLUS_TOTAL_DEBT: {tv_debt_basis}"
            "   (these fall back to EQUITY_VALUE_ONLY, they do not go null)"
        )

    if args.list and (at_risk or deal_ccy_missing):
        print("\n  Affected transaction_ids:")
        rows = conn.execute(
            """
            SELECT transaction_id, deal_value_currency, net_debt, net_debt_currency,
                   implied_enterprise_value, implied_enterprise_value_basis
            FROM transaction_record
            WHERE net_debt IS NOT NULL
              AND implied_enterprise_value IS NOT NULL
              AND implied_enterprise_value_basis LIKE 'IMPLIED_EQUITY_PLUS_%'
              AND (net_debt_currency IS NULL OR deal_value_currency IS NULL)
            ORDER BY implied_enterprise_value DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
        for row in rows:
            print(
                f"    {row['transaction_id']}  deal_ccy={row['deal_value_currency']!r} "
                f"net_debt={row['net_debt']} ccy={row['net_debt_currency']!r} "
                f"EV={row['implied_enterprise_value']} ({row['implied_enterprise_value_basis']})"
            )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
