#!/usr/bin/env python3
"""Regression guard for the net-debt currency-gap quantifier.

No network and no model calls.

The quantifier reads live corpora, and `transaction_record` is not one shape. A
corpus that predates the implied-EV rewire carries `enterprise_value` /
`enterprise_value_basis` and has no `implied_enterprise_value`, no `cash_st`, and no
`*_currency` columns at all. The first version of the script assumed the migrated
shape and died on the real one, so the legacy fixture below is built by hand rather
than through `init_db` — `init_db` applies the migrations and would add exactly the
columns whose absence is the thing under test.

An absent column and an unpopulated one mean the same thing to this tool: no
recorded value. A database that never had `net_debt_currency` has no known net-debt
currencies, so every calculated-basis row with a net debt is at risk. That is the
headline the legacy case must produce.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.quantify_net_debt_currency_gap import (  # noqa: E402
    analyze,
    affected_rows,
    col_or_null,
    resolve_ev_columns,
)

# Exactly the columns reported present on the live corpus, and nothing else.
_LEGACY_COLUMNS = (
    "transaction_id TEXT PRIMARY KEY",
    "enterprise_value REAL",
    "enterprise_value_basis TEXT",
    "implied_equity_value REAL",
    "net_debt REAL",
    "equity_value REAL",
    "deal_value_currency TEXT",
    "total_debt REAL",
    "transaction_value REAL",
    "transaction_value_basis TEXT",
)


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _legacy_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE transaction_record ({', '.join(_LEGACY_COLUMNS)})")
    rows = [
        # calculated EV, net debt present → at risk (no currency column exists at all)
        ("t1", 500.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 400.0, 100.0, 300.0, "USD", None, 300.0, "EQUITY_VALUE_ONLY"),
        # legacy basis vocabulary — must still count as calculated, not be missed by
        # a name pattern that only knows the newer spelling
        ("t2", 800.0, "EQUITY_PLUS_NET_DEBT", 700.0, 100.0, 600.0, "USD", None, 600.0, "EQUITY_VALUE_ONLY"),
        # STATED → unaffected, it is a single source figure rather than a sum
        ("t3", 900.0, "STATED", None, None, None, "USD", None, None, None),
        # calculated basis but no net debt recorded → reported separately
        ("t4", 250.0, "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT", 200.0, None, 200.0, "USD", 50.0, 250.0, "EQUITY_PLUS_TOTAL_DEBT"),
        # no EV at all
        ("t5", None, None, None, 20.0, 100.0, None, None, 100.0, "EQUITY_VALUE_ONLY"),
    ]
    conn.executemany(
        "INSERT INTO transaction_record VALUES (?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def _modern_db(path: str) -> None:
    """The migrated shape, to prove the resolver prefers the canonical pair."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE transaction_record (
            transaction_id TEXT PRIMARY KEY,
            enterprise_value REAL, enterprise_value_basis TEXT,
            implied_enterprise_value REAL, implied_enterprise_value_basis TEXT,
            implied_equity_value REAL, net_debt REAL, net_debt_currency TEXT,
            equity_value REAL, deal_value_currency TEXT,
            total_debt REAL, total_debt_currency TEXT,
            cash_st REAL, cash_st_currency TEXT,
            transaction_value REAL, transaction_value_basis TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO transaction_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # currency recorded and matching → safe
            ("m1", 500.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 500.0,
             "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 400.0, 100.0, "USD",
             300.0, "USD", None, None, None, None, 300.0, "EQUITY_VALUE_ONLY"),
            # net-debt currency missing → at risk
            ("m2", 600.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 600.0,
             "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 500.0, 100.0, None,
             400.0, "USD", None, None, None, None, 400.0, "EQUITY_VALUE_ONLY"),
            # deal currency missing → at risk
            ("m3", 700.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 700.0,
             "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT", 600.0, 100.0, "USD",
             500.0, None, None, None, None, None, 500.0, "EQUITY_VALUE_ONLY"),
            # STATED → unaffected
            ("m4", 900.0, "STATED", 900.0, "STATED", None, None, None,
             None, "USD", None, None, None, None, None, None),
        ],
    )
    conn.commit()
    conn.close()


def _open_ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _scenario_legacy(failures: list[str]) -> None:
    p = "legacy-schema"
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "legacy.db")
        _legacy_db(path)
        conn = _open_ro(path)
        data = analyze(conn)

        _check(failures, f"{p} ev_value_column", data["ev_value_column"], "enterprise_value")
        _check(failures, f"{p} ev_basis_column", data["ev_basis_column"], "enterprise_value_basis")
        _check(failures, f"{p} has_net_debt_currency", data["has_net_debt_currency"], False)
        _check(failures, f"{p} has_cash_st", data["has_cash_st"], False)
        _check(failures, f"{p} total_rows", data["total_rows"], 5)
        _check(failures, f"{p} with_net_debt", data["with_net_debt"], 3)
        _check(failures, f"{p} with_ev", data["with_ev"], 4)
        _check(failures, f"{p} stated_ev", data["stated_ev"], 1)
        # t1, t2 (legacy basis name), t4 — all calculated
        _check(failures, f"{p} calculated_ev", data["calculated_ev"], 3)
        # t1 and t2 have a net debt; t4's basis says calculated but net_debt is NULL
        _check(failures, f"{p} at_risk_total", data["at_risk_total"], 2)
        _check(failures, f"{p} at_risk_missing_net_debt_currency",
               data["at_risk_missing_net_debt_currency"], 2)
        _check(failures, f"{p} calculated_without_net_debt",
               data["calculated_without_net_debt"], 1)
        _check(failures, f"{p} total_debt_without_currency",
               data["total_debt_without_currency"], 1)
        # cash_st absent → that count is skipped rather than guessed
        if "cash_st_without_currency" in data:
            failures.append(f"{p}: cash_st count must be skipped when the column is absent")
        if not any("net_debt_currency column absent" in n for n in data["notes"]):
            failures.append(f"{p}: missing the note explaining the absent currency column")

        ids = [row["transaction_id"] for row in affected_rows(conn, 50)]
        _check(failures, f"{p} affected ids", ids, ["t2", "t1"])  # ordered by EV desc
        conn.close()


def _scenario_modern(failures: list[str]) -> None:
    p = "modern-schema"
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "modern.db")
        _modern_db(path)
        conn = _open_ro(path)
        data = analyze(conn)

        # Both pairs exist; the canonical one wins.
        _check(failures, f"{p} ev_value_column", data["ev_value_column"], "implied_enterprise_value")
        _check(failures, f"{p} has_net_debt_currency", data["has_net_debt_currency"], True)
        _check(failures, f"{p} stated_ev", data["stated_ev"], 1)
        _check(failures, f"{p} calculated_ev", data["calculated_ev"], 3)
        _check(failures, f"{p} at_risk_missing_net_debt_currency",
               data["at_risk_missing_net_debt_currency"], 1)
        _check(failures, f"{p} at_risk_missing_deal_currency",
               data["at_risk_missing_deal_currency"], 1)
        _check(failures, f"{p} at_risk_total", data["at_risk_total"], 2)
        conn.close()


def _scenario_helpers(failures: list[str]) -> None:
    p = "helpers"
    _check(failures, f"{p} prefers canonical pair",
           resolve_ev_columns({"enterprise_value", "enterprise_value_basis",
                               "implied_enterprise_value", "implied_enterprise_value_basis"}),
           ("implied_enterprise_value", "implied_enterprise_value_basis"))
    _check(failures, f"{p} falls back to legacy pair",
           resolve_ev_columns({"enterprise_value", "enterprise_value_basis"}),
           ("enterprise_value", "enterprise_value_basis"))
    _check(failures, f"{p} value without basis",
           resolve_ev_columns({"enterprise_value"}), ("enterprise_value", None))
    _check(failures, f"{p} no ev columns", resolve_ev_columns({"equity_value"}), (None, None))
    _check(failures, f"{p} col present", col_or_null({"net_debt"}, "net_debt"), "net_debt")
    _check(failures, f"{p} col absent", col_or_null(set(), "net_debt"), "NULL")


def _scenario_no_table(failures: list[str]) -> None:
    """A database without transaction_record reports clearly instead of crashing."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "empty.db")
        sqlite3.connect(path).close()
        conn = _open_ro(path)
        try:
            analyze(conn)
        except LookupError:
            pass
        except Exception as exc:  # noqa: BLE001
            failures.append(f"no-table: expected LookupError, got {type(exc).__name__}: {exc}")
        else:
            failures.append("no-table: expected LookupError, got no exception")
        conn.close()


def _scenario_read_only(failures: list[str]) -> None:
    """The tool must not be able to modify a live database."""
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "ro.db")
        _legacy_db(path)
        conn = _open_ro(path)
        try:
            conn.execute("DELETE FROM transaction_record")
        except sqlite3.OperationalError:
            pass
        else:
            failures.append("read-only: a write succeeded against the read-only connection")
        conn.close()


def main() -> None:
    failures: list[str] = []
    _scenario_helpers(failures)
    _scenario_legacy(failures)
    _scenario_modern(failures)
    _scenario_no_table(failures)
    _scenario_read_only(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS quantifier: legacy and migrated transaction_record shapes both measured")


if __name__ == "__main__":
    main()
