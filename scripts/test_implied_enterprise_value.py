#!/usr/bin/env python3
"""Guard tests for canonical implied_enterprise_value.

The accepted value architecture permits whole-company EV only as a source-stated
ENTERPRISE_VALUE or as implied_equity_value + net_debt. net_debt may be reported
directly, or calculated from total_debt - cash_st. It must never add debt/cash
directly to stake-level equity_value.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stages.aggregate import (
    _derive_net_debt,
    _compute_multiples,
    _derive_equity_value,
    _derive_implied_enterprise_value,
    _derive_implied_equity,
    _derive_transaction_value,
    _pick_value_amount_for_type,
)



_AS_OF = "2025-12-31"


def _implied_ev_from_components(implied_equity, total_debt, cash_st):
    """Component path: coherent net debt first, then the implied EV sum.

    Mirrors how aggregation now composes these — _derive_net_debt owns the
    component-coherence rules (one currency, one balance-sheet date) and
    _derive_implied_enterprise_value consumes the resolved figure.
    """
    net_debt, net_debt_currency, _as_of, basis = _derive_net_debt(
        None, None, total_debt, "USD", _AS_OF, cash_st, "USD", _AS_OF
    )
    return _derive_implied_enterprise_value(
        None, None, implied_equity, net_debt,
        implied_equity_currency="USD", net_debt_currency=net_debt_currency,
        net_debt_basis=basis,
    )

def _assert_equal(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def main() -> None:
    failures: list[str] = []
    log = logging.getLogger("test_implied_enterprise_value")

    equity_value = 2_500_000_000.0
    pct = 25.0
    implied_equity = _derive_implied_equity(equity_value, pct)
    _assert_equal(failures, "25pct_implied_equity", implied_equity, 10_000_000_000.0)
    _assert_equal(
        failures,
        "25pct_no_debt_cash_st_no_implied_ev",
        _derive_implied_enterprise_value(None, None, implied_equity, None),
        (None, None),
    )
    _assert_equal(
        failures,
        "25pct_total_debt_without_cash_st_no_zero_assumption",
        _derive_net_debt(None, None, 1_200_000_000.0, "USD", _AS_OF, None, None, None),
        (None, None, None, None),
    )
    _assert_equal(
        failures,
        "25pct_reported_net_debt_calculates_whole_company_ev",
        _derive_implied_enterprise_value(
            None, None, implied_equity, 900_000_000.0,
            implied_equity_currency="USD", net_debt_currency="USD", net_debt_basis="REPORTED",
        ),
        (10_900_000_000.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT"),
    )
    _assert_equal(
        failures,
        "25pct_with_total_debt_and_cash_st_calculates_net_debt_then_ev",
        _implied_ev_from_components(implied_equity, 1_200_000_000.0, 200_000_000.0),
        (11_000_000_000.0, "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT"),
    )
    _assert_equal(
        failures,
        "reported_net_debt_preferred_over_calculated_net_debt",
        _derive_net_debt(
            900_000_000.0, "USD", 1_200_000_000.0, "USD", _AS_OF, 200_000_000.0, "USD", _AS_OF
        )[3],
        "REPORTED",
    )
    _assert_equal(
        failures,
        "source_stated_ev_populates_directly",
        _derive_implied_enterprise_value(8_500_000_000.0, "ENTERPRISE_VALUE", None, None),
        (8_500_000_000.0, "STATED"),
    )
    mediaworks_observations = {
        "value_amount": [
            {
                "observation_id": 1,
                "source_key": ("bt", 11),
                "tier": "T2",
                "value": 130_000_000.0,
                "model_confidence": "HIGH",
            },
            {
                "observation_id": 2,
                "source_key": ("smallcaps", 12),
                "tier": "T2",
                "value": 130_000_000.0,
                "model_confidence": "HIGH",
            },
        ],
        "value_type": [
            {
                "observation_id": 1,
                "source_key": ("bt", 11),
                "tier": "T2",
                "value": "TRANSACTION_VALUE",
                "model_confidence": "HIGH",
            },
            {
                "observation_id": 2,
                "source_key": ("smallcaps", 12),
                "tier": "T2",
                "value": "ENTERPRISE_VALUE",
                "model_confidence": "HIGH",
            },
        ],
    }
    mediaworks_transaction_amount = _pick_value_amount_for_type(
        mediaworks_observations, "TRANSACTION_VALUE"
    )
    mediaworks_ev_amount = _pick_value_amount_for_type(
        mediaworks_observations, "ENTERPRISE_VALUE"
    )
    _assert_equal(failures, "mediaworks_transaction_value_observation_survives", mediaworks_transaction_amount, 130_000_000.0)
    _assert_equal(failures, "mediaworks_ev_observation_survives", mediaworks_ev_amount, 130_000_000.0)
    _assert_equal(
        failures,
        "mediaworks_transaction_value_and_stated_ev_coexist",
        (
            _derive_transaction_value(
                {"value_amount": mediaworks_transaction_amount, "value_type": "TRANSACTION_VALUE"},
                None,
                None,
                100.0,
            ),
            _derive_implied_enterprise_value(
                mediaworks_ev_amount,
                "ENTERPRISE_VALUE",
                None,
                None,
            ),
        ),
        ((130_000_000.0, "STATED"), (130_000_000.0, "STATED")),
    )
    _assert_equal(
        failures,
        "stake_equity_not_used_as_ev_base",
        _implied_ev_from_components(None, 1_200_000_000.0, 200_000_000.0),
        (None, None),
    )
    _assert_equal(
        failures,
        "full_acquisition_stable_implied_ev",
        _implied_ev_from_components(
            _derive_implied_equity(900_000_000.0, 100.0), 150_000_000.0, 50_000_000.0
        ),
        (1_000_000_000.0, "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT"),
    )
    _assert_equal(
        failures,
        "full_acquisition_transaction_value_stable",
        _derive_transaction_value(
            {}, 900_000_000.0, 150_000_000.0, 100.0,
            equity_currency="USD", total_debt_currency="USD",
        ),
        (1_050_000_000.0, "EQUITY_PLUS_TOTAL_DEBT"),
    )
    equity_only_tv, equity_only_basis = _derive_transaction_value(
        {}, 178_500_000.0, None, 85.0
    )
    _assert_equal(
        failures,
        "control_debt_unknown_transaction_value_equity_only",
        (equity_only_tv, equity_only_basis),
        (178_500_000.0, "EQUITY_VALUE_ONLY"),
    )
    _assert_equal(
        failures,
        "equity_only_transaction_value_does_not_imply_ev",
        _derive_implied_enterprise_value(None, None, 210_000_000.0, None),
        (None, None),
    )

    no_ev_multiples = _compute_multiples(
        implied_enterprise_value=None,
        value_currency="USD",
        target_revenue=100_000_000.0,
        target_revenue_period_type="LTM",
        target_ebitda=None,
        target_ebitda_period_type=None,
        financials_currency="USD",
        log=log,
        cluster_id="no_ev",
    )
    _assert_equal(failures, "multiples_require_tier2_ev", no_ev_multiples["multiple_quality"], "NOT_CALCULABLE")

    ev_multiples = _compute_multiples(
        implied_enterprise_value=1_000_000_000.0,
        value_currency="USD",
        target_revenue=100_000_000.0,
        target_revenue_period_type="LTM",
        target_ebitda=None,
        target_ebitda_period_type=None,
        financials_currency="USD",
        log=log,
        cluster_id="with_ev",
    )
    _assert_equal(failures, "multiples_use_implied_ev", ev_multiples["ev_to_revenue_ltm"], 10.0)
    _assert_equal(failures, "multiples_quality_with_implied_ev", ev_multiples["multiple_quality"], "CALCULATED")

    # Funding/primary-capital rows still vacate equity_value, so no implied EV is
    # manufactured from a funding amount.
    funding_equity, _basis = _derive_equity_value(
        {"v2_event_type": "GROWTH_EQUITY", "round_size": 100_000_000.0},
        None,
        None,
    )
    _assert_equal(failures, "funding_equity_value_null", funding_equity, None)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS implied_enterprise_value guards: 18 cases")


if __name__ == "__main__":
    main()
