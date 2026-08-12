#!/usr/bin/env python3
"""Test Stage 9 deal_value_currency derivation and its mismatch guard (§4.7).

Unanimity-or-null over three currency sources (valuation_currency, value_currency,
round_currency — the third added 2026-08-11):
  1. deal_value_currency is non-null wherever a currency is present and every source
     present agrees.
  2. Where any two present sources differ, deal_value_currency is null. The null is
     the queryable mismatch signal — assertion 2 proves the guard fires.
  3. The third source (round_currency) participates: it can supply the tag on its own,
     and it can break unanimity that the other two had.

Unit cases run with no DB. Pass a sqlite path to also check the DB invariant on a
re-aggregated database (only meaningful after Stage 9 has run with this code).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stages.aggregate import _derive_deal_value_currency

# (name, field_values, expected)
CASES = [
    # Assertion 1 — currency present, no conflict -> non-null
    ("valuation_currency_preferred", {"valuation_currency": "USD", "value_currency": None}, "USD"),
    ("value_currency_fallback", {"valuation_currency": None, "value_currency": "EUR"}, "EUR"),
    ("both_same_no_conflict", {"valuation_currency": "USD", "value_currency": "USD"}, "USD"),
    ("valuation_only_non_usd", {"valuation_currency": "KRW"}, "KRW"),
    ("neither_present_is_null", {}, None),  # no currency captured — legitimately null
    # Assertion 2 — genuine mismatch -> null (the guard fires)
    ("conflict_eur_usd_null", {"valuation_currency": "EUR", "value_currency": "USD"}, None),
    ("conflict_krw_usd_null", {"valuation_currency": "KRW", "value_currency": "USD"}, None),
    # Assertion 3 — third source (round_currency) participates in unanimity-or-null
    ("round_only_tags", {"round_currency": "GBP"}, "GBP"),
    ("three_agree_tags", {"valuation_currency": "USD", "value_currency": "USD", "round_currency": "USD"}, "USD"),
    ("round_agrees_valuation", {"valuation_currency": "EUR", "round_currency": "EUR"}, "EUR"),
    ("round_breaks_unanimity_null", {"valuation_currency": "USD", "value_currency": "USD", "round_currency": "EUR"}, None),
    ("round_vs_valuation_conflict_null", {"round_currency": "EUR", "valuation_currency": "USD"}, None),
]


def _unit() -> list[tuple[str, object, object]]:
    failed = []
    for name, fv, expected in CASES:
        actual = _derive_deal_value_currency(fv)  # log/cluster_id optional
        if actual != expected:
            failed.append((name, expected, actual))
    return failed


def check_db_invariant(conn: sqlite3.Connection) -> list[str]:
    """The two data-level assertions, for a re-aggregated DB."""
    problems = []
    derived = ("equity_value IS NOT NULL OR implied_equity_value IS NOT NULL "
               "OR enterprise_value IS NOT NULL OR investment_amount IS NOT NULL")
    conflict = ("valuation_currency IS NOT NULL AND value_currency IS NOT NULL "
                "AND valuation_currency != value_currency")
    has_currency = "(valuation_currency IS NOT NULL OR value_currency IS NOT NULL)"

    # 1. currency present, no conflict, derived value populated -> must be non-null
    n1 = conn.execute(
        f"SELECT COUNT(*) FROM transaction_record "
        f"WHERE ({derived}) AND {has_currency} AND NOT ({conflict}) "
        f"AND deal_value_currency IS NULL"
    ).fetchone()[0]
    if n1:
        problems.append(f"assertion 1 violated: {n1} rows have a derived value + currency + no conflict but null deal_value_currency")

    # 2. conflict -> must be null (proves the guard fires)
    n2 = conn.execute(
        f"SELECT COUNT(*) FROM transaction_record "
        f"WHERE {conflict} AND deal_value_currency IS NOT NULL"
    ).fetchone()[0]
    if n2:
        problems.append(f"assertion 2 violated: {n2} conflicting-currency rows have non-null deal_value_currency")
    return problems


def main() -> None:
    failed = _unit()
    if failed:
        for name, expected, actual in failed:
            print(f"FAIL {name}: expected {expected!r}, got {actual!r}")
        raise SystemExit(1)
    print(f"PASS deal_value_currency unit cases={len(CASES)}")

    if len(sys.argv) > 1:
        db_path = sys.argv[1]
        conn = sqlite3.connect(db_path)
        problems = check_db_invariant(conn)
        conn.close()
        if problems:
            for p in problems:
                print(f"FAIL (db {db_path}): {p}")
            raise SystemExit(1)
        print(f"PASS deal_value_currency DB invariant on {db_path}")


if __name__ == "__main__":
    main()
