"""
Guard test for implied_equity_value derivation (2026-08-12).

Decision "implied_equity_value Derives From equity_value Only": implied equity is
grossed up from equity_value by a §2.6-RESOLVED pct, or source-stated. Never from
transaction_value (value_amount) or post_money_valuation. A NULL/non-positive pct
yields None — never a silent 100% gross-up (that would reopen the manufactured-
numerator defect through a pct-null door).

The function takes (equity_value, pct) — pct already resolved by the caller via
_resolve_pct_acquired, exactly as _derive_transaction_value receives it. There is no
fv param, so there is no surface to read a raw pct off.

No DB, no API. Usage: python scripts/test_implied_equity_source.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stages.aggregate import _derive_implied_equity

# (name, equity_value, resolved_pct, expected)
CASES = [
    # grossed up from equity_value
    ("minority_stake_grossed",      75_000_000.0,   10.0,  750_000_000.0),   # KG tc_9731
    ("control_partial_grossed",     2_550_000_000.0, 26.0, round(2_550_000_000.0 / 0.26, 2)),  # G City
    # pct resolved to 100 (control silent -> assumed, or stated 100) -> equity_value as-is
    ("control_full_returns_equity", 100_000_000.0,  100.0, 100_000_000.0),
    # no equity to gross up -> None (the violation rows: TC Skyward, KG tc_616c)
    ("no_equity_is_null",           None,           50.0,  None),
    ("zero_equity_is_null",         0.0,            50.0,  None),
    # THE HAZARD: partial type, source silent -> resolver returns None -> must be None,
    # NOT a fall-through 100% gross-up that asserts the whole company = the stake.
    ("partial_silent_pct_is_null",  50_000_000.0,   None,  None),
    # bad parse: non-positive pct -> None (never the stake figure as a whole-company value)
    ("zero_pct_is_null",            50_000_000.0,   0.0,   None),
    ("negative_pct_is_null",        50_000_000.0,   -5.0,  None),
]


def _run() -> int:
    failures = []
    for name, eq, pct, expected in CASES:
        got = _derive_implied_equity(eq, pct)
        if got != expected:
            failures.append(f"{name}: _derive_implied_equity({eq}, {pct}) = {got}, expected {expected}")
    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print(f"PASS implied_equity_value source guard: {len(CASES)} cases "
          "(equity-only gross-up; NULL/non-positive resolved pct -> None; no whole-company manufacture)")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
