#!/usr/bin/env python3
"""Test §4.2 pct_acquired resolution (§2.6 default) and transaction_value threshold.

The load-bearing case is the pct_acquired NULL bug: a 100% acquisition with an
unstated percentage must resolve to 100 (assumed) and take the control branch, not
fall silently to the below-control (minority) branch.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stages.aggregate import _derive_transaction_value, _resolve_pct_acquired

# (name, fv, expected_pct, expected_source)
PCT_CASES = [
    ("stated_pct", {"v2_event_type": "ACQUISITION", "pct_acquired": 55}, 55.0, "stated"),
    ("null_pct_control_defaults_100", {"v2_event_type": "ACQUISITION", "pct_acquired": None}, 100.0, "assumed"),
    ("null_pct_merger_defaults_100", {"v2_event_type": "MERGER"}, 100.0, "assumed"),
    ("null_pct_minority_stays_unknown", {"v2_event_type": "MINORITY_INVESTMENT", "pct_acquired": None}, None, None),
    ("null_pct_growth_stays_unknown", {"v2_event_type": "GROWTH_EQUITY"}, None, None),
]

# (name, fv, equity_value, total_debt, pct, expected_value, expected_basis)
TV_CASES = [
    ("as_reported_wins", {"value_type": "TRANSACTION_VALUE", "value_amount": 80}, 6, None, 100.0, 80.0, "STATED"),
    ("minority_below_50_equity_no_debt", {}, 600, None, 27.0, 600, "EQUITY_BELOW_CONTROL"),
    ("control_debt_known_adds_total_debt", {}, 200, 50, 100.0, 250, "EQUITY_PLUS_TOTAL_DEBT"),
    ("control_debt_unknown_is_null", {}, 200, None, 100.0, None, None),   # do NOT assume debt=0
    ("no_equity_is_null", {}, None, None, 100.0, None, None),
    ("pct_unknown_is_null", {}, 200, None, None, None, None),
]


def main() -> None:
    failed = []
    for name, fv, exp_pct, exp_src in PCT_CASES:
        pct, src = _resolve_pct_acquired(fv)
        if (pct, src) != (exp_pct, exp_src):
            failed.append(f"{name}: expected ({exp_pct}, {exp_src}), got ({pct}, {src})")

    for name, fv, eq, td, pct, exp_val, exp_basis in TV_CASES:
        val, basis = _derive_transaction_value(fv, eq, td, pct)
        if (val, basis) != (exp_val, exp_basis):
            failed.append(f"{name}: expected ({exp_val}, {exp_basis}), got ({val}, {basis})")

    if failed:
        for f in failed:
            print(f"FAIL {f}")
        raise SystemExit(1)
    print(f"PASS transaction_value + pct resolution cases={len(PCT_CASES) + len(TV_CASES)}")


if __name__ == "__main__":
    main()
