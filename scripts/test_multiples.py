"""
scripts/test_multiples.py — Synthetic unit tests for _compute_multiples().

Run from project root:
    python scripts/test_multiples.py

Exercises the Drop 3.12 cases plus the date-aligned ANNUAL-as-trailing fallback.
The 455-day annual eligibility window is provisional pending broader corpus review.
"""

import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stages.aggregate import _compute_multiples

log = logging.getLogger("test_multiples")
logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


def check(name, got, expected_quality, expected_slots=None):
    ok = True
    notes = []
    if got["multiple_quality"] != expected_quality:
        ok = False
        notes.append(f"quality={got['multiple_quality']!r} want {expected_quality!r}")
    if expected_slots:
        for slot, val in expected_slots.items():
            actual = got[slot]
            if val is None and actual is not None:
                ok = False
                notes.append(f"{slot}={actual!r} want None")
            elif val is not None and actual is None:
                ok = False
                notes.append(f"{slot}=None want {val!r}")
            elif val is not None and actual is not None and abs(actual - val) > 0.01:
                ok = False
                notes.append(f"{slot}={actual!r} want {val!r}")
    status = PASS if ok else FAIL
    msg = f"  {status}  {name}"
    if notes:
        msg += f"  [{'; '.join(notes)}]"
    print(msg)
    results.append(ok)


# ─── Case 1: ENTERPRISE_VALUE + LTM revenue + LTM EBITDA, both in range ──────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="LTM",
    target_ebitda=50_000_000,
    target_ebitda_period_type="LTM",
    financials_currency="USD",
    log=log, cluster_id="tc_test1",
)
check("Case 1 — EV+LTM rev+ebitda in range → CALCULATED",
      r, "CALCULATED",
      {"ev_to_revenue_ltm": 5.0, "ev_to_ebitda_ltm": 10.0,
       "ev_to_revenue_ntm": None, "ev_to_ebitda_ntm": None})

# ─── Case 2: ENTERPRISE_VALUE + NTM revenue only ──────────────────────────────
r = _compute_multiples(
    implied_enterprise_value=300_000_000,
    value_currency="USD",
    target_revenue=60_000_000,
    target_revenue_period_type="NTM",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency=None,
    log=log, cluster_id="tc_test2",
)
check("Case 2 — NTM revenue only → CALCULATED, _ntm populated",
      r, "CALCULATED",
      {"ev_to_revenue_ntm": 5.0, "ev_to_revenue_ltm": None})

# ─── Case 3: ENTERPRISE_VALUE + LTM EBITDA at 250x → NM ─────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=None,
    target_revenue_period_type=None,
    target_ebitda=2_000_000,
    target_ebitda_period_type="LTM",
    financials_currency="USD",
    log=log, cluster_id="tc_test3",
)
check("Case 3 — EBITDA at 250x → NM, value preserved",
      r, "NM",
      {"ev_to_ebitda_ltm": 250.0})

# ─── Case 4: ENTERPRISE_VALUE + LTM EBITDA at 0.5x → NM (below 1x bound) ────
r = _compute_multiples(
    implied_enterprise_value=50_000_000,
    value_currency="USD",
    target_revenue=None,
    target_revenue_period_type=None,
    target_ebitda=120_000_000,
    target_ebitda_period_type="LTM",
    financials_currency="USD",
    log=log, cluster_id="tc_test4",
)
check("Case 4 — EBITDA at 0.42x (below 1x bound) → NM",
      r, "NM")

# ─── Case 5: no Tier-2 EV numerator → NOT_CALCULABLE ────────────────────────
r = _compute_multiples(
    implied_enterprise_value=None,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="LTM",
    target_ebitda=50_000_000,
    target_ebitda_period_type="LTM",
    financials_currency="USD",
    log=log, cluster_id="tc_test5",
)
check("Case 5 — Tier-1 transaction/stake values unavailable as EV numerator → NOT_CALCULABLE",
      r, "NOT_CALCULABLE",
      {"ev_to_revenue_ltm": None, "ev_to_ebitda_ltm": None})

# ─── Case 6: UNDISCLOSED + null financials → NOT_CALCULABLE ──────────────────
r = _compute_multiples(
    implied_enterprise_value=None,
    value_currency=None,
    target_revenue=None,
    target_revenue_period_type=None,
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency=None,
    log=log, cluster_id="tc_test6",
)
check("Case 6 — UNDISCLOSED + null financials → NOT_CALCULABLE",
      r, "NOT_CALCULABLE")

# ─── Case 7: Currency mismatch (USD value, EUR EBITDA) → NM ──────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=None,
    target_revenue_period_type=None,
    target_ebitda=50_000_000,
    target_ebitda_period_type="LTM",
    financials_currency="EUR",
    log=log, cluster_id="tc_test7",
)
check("Case 7 — currency mismatch USD/EUR → NM",
      r, "NM",
      {"ev_to_ebitda_ltm": None})  # value NOT stored when currency mismatch

# ─── Case 8: TTM treated as LTM → CALCULATED, populated in _ltm slot ─────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="TTM",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency=None,
    log=log, cluster_id="tc_test8",
)
check("Case 8 — TTM treated as LTM → CALCULATED, ev_to_revenue_ltm populated",
      r, "CALCULATED",
      {"ev_to_revenue_ltm": 5.0, "ev_to_revenue_ntm": None})

# ─── Case 9: FY period_type → NOT_CALCULABLE (legacy FY is not relabeled) ────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="FY",
    target_ebitda=50_000_000,
    target_ebitda_period_type="FY",
    financials_currency="USD",
    log=log, cluster_id="tc_test9",
)
check("Case 9 — FY period_type → NOT_CALCULABLE (no LTM/NTM mapping)",
      r, "NOT_CALCULABLE",
      {"ev_to_revenue_ltm": None, "ev_to_ebitda_ltm": None})

# ─── Case 10: recent ANNUAL actual → LTM analytical slot fallback ────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2025",
    target_ebitda=50_000_000,
    target_ebitda_period_type="ANNUAL",
    target_ebitda_period_end="2025",
    financials_currency="USD",
    log=log, cluster_id="tc_test10",
    announced_date="2026-03-15",
)
check("Case 10 — recent ANNUAL actual eligible as trailing denominator",
      r, "CALCULATED",
      {"ev_to_revenue_ltm": 5.0, "ev_to_ebitda_ltm": 10.0,
       "ev_to_revenue_ntm": None, "ev_to_ebitda_ntm": None})

# ─── Case 11: stale ANNUAL actual → NOT_CALCULABLE ───────────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2024",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_test11",
    announced_date="2026-08-14",
)
check("Case 11 — stale ANNUAL actual rejected",
      r, "NOT_CALCULABLE",
      {"ev_to_revenue_ltm": None})

# ─── Case 12: future ANNUAL period end → NOT_CALCULABLE ──────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2026-12-31",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_test12",
    announced_date="2026-08-14",
)
check("Case 12 — future ANNUAL period end rejected",
      r, "NOT_CALCULABLE",
      {"ev_to_revenue_ltm": None})

# ─── Case 13: explicit LTM remains directly eligible ─────────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=125_000_000,
    target_revenue_period_type="LTM",
    target_revenue_period_end="2026-06-30",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_test13",
    announced_date="2026-08-14",
)
check("Case 13 — explicit LTM remains preferred/direct",
      r, "CALCULATED",
      {"ev_to_revenue_ltm": 4.0})

# ─── Case 14: NTM unchanged and separate ─────────────────────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="NTM",
    target_revenue_period_end="2027-08-14",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_test14",
    announced_date="2026-08-14",
)
check("Case 14 — NTM unchanged and separate",
      r, "CALCULATED",
      {"ev_to_revenue_ntm": 5.0, "ev_to_revenue_ltm": None})

# ─── Case 15: eligible ANNUAL with currency mismatch → NM ────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2025",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="EUR",
    log=log, cluster_id="tc_test15",
    announced_date="2026-03-15",
)
check("Case 15 — eligible ANNUAL with currency mismatch stays NM",
      r, "NM",
      {"ev_to_revenue_ltm": None})

# ─── Case 16: funding gate unchanged ─────────────────────────────────────────
r = _compute_multiples(
    implied_enterprise_value=500_000_000,
    value_currency="USD",
    target_revenue=100_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2025",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_test16",
    v2_event_type="GROWTH_EQUITY",
    announced_date="2026-03-15",
)
check("Case 16 — funding gate unchanged",
      r, "NOT_CALCULABLE",
      {"ev_to_revenue_ltm": None})

# ─── Case 17: Samsonite-style annual revenue within 455 days ─────────────────
r = _compute_multiples(
    implied_enterprise_value=210_000_000,
    value_currency="USD",
    target_revenue=210_000_000,
    target_revenue_period_type="ANNUAL",
    target_revenue_period_end="2025",
    target_ebitda=None,
    target_ebitda_period_type=None,
    financials_currency="USD",
    log=log, cluster_id="tc_samsonite",
    announced_date="2026-08-12",
)
check("Case 17 — Samsonite ANNUAL 2025 revenue eligible for trailing EV/Revenue",
      r, "CALCULATED",
      {"ev_to_revenue_ltm": 1.0, "ev_to_revenue_ntm": None})

# ─── Summary ──────────────────────────────────────────────────────────────────
passed = sum(results)
total = len(results)
print(f"\n{passed}/{total} tests passed")
sys.exit(0 if passed == total else 1)
