#!/usr/bin/env python3
"""Guard tests for minority-as-derived-flag foundation.

These tests lock in the aggregation foundation: minority status is canonical as
a derived flag, and that flag can prevent silent 100% pct defaulting without
itself becoming a percentage for valuation gross-up. Legacy
MINORITY_INVESTMENT rows may still exist before reprocessing, so aggregation
continues to treat that old taxonomy value as explicit minority evidence.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection, init_db
from stages.aggregate import (
    _derive_enterprise_value,
    _derive_flags,
    _derive_implied_equity,
    _derive_implied_enterprise_value,
    _derive_transaction_value,
    _resolve_pct_acquired,
)
from stages.high_confidence_extract import _validate as _validate_hc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HC_PROMPT_PATH = os.path.join(ROOT, "prompts", "high_confidence_extraction.md")


def _assert_equal(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _test_flag_and_pct_resolution(failures: list[str]) -> None:
    cases = [
        (
            "explicit_minority_type_no_pct",
            {"v2_event_type": "MINORITY_INVESTMENT", "pct_acquired": None},
            1,
            (None, None),
        ),
        (
            "minority_acquisition",
            {"v2_event_type": "ACQUISITION", "pct_acquired": 35},
            1,
            (35.0, "stated"),
        ),
        (
            "minority_growth_equity",
            {"v2_event_type": "GROWTH_EQUITY", "pct_acquired": 20},
            1,
            (20.0, "stated"),
        ),
        (
            "minority_vc_round",
            {"v2_event_type": "VC_ROUND", "pct_acquired": 12.5},
            1,
            (12.5, "stated"),
        ),
        (
            # Was (100.0, "assumed") until aggregation 0.11. A silent control deal now
            # states no percentage, because the source did not.
            "control_acquisition_silent_pct_is_null",
            {"v2_event_type": "ACQUISITION", "pct_acquired": None},
            0,
            (None, None),
        ),
    ]

    for name, fields, expected_flag, expected_pct in cases:
        flag = _derive_flags(fields)["is_minority"]
        _assert_equal(failures, f"{name} is_minority", flag, expected_flag)
        _assert_equal(
            failures,
            f"{name} pct",
            _resolve_pct_acquired(fields),
            expected_pct,
        )


def _test_valuation_guards(failures: list[str]) -> None:
    # Minority status alone is not a percentage and must not authorize gross-up.
    minority_fields = {"v2_event_type": "MINORITY_INVESTMENT", "pct_acquired": None}
    minority_flag = _derive_flags(minority_fields)["is_minority"]
    pct, _source = _resolve_pct_acquired(minority_fields)
    _assert_equal(
        failures,
        "minority_without_pct_no_implied_equity",
        _derive_implied_equity(75_000_000.0, pct),
        None,
    )

    # Existing below-control transaction-value behavior is preserved when pct is
    # genuinely stated: equity value is the transaction value, with no debt added.
    _assert_equal(
        failures,
        "below_control_transaction_value_preserved",
        _derive_transaction_value({}, 600.0, None, is_control=False,
                                  is_below_control=True),
        (600.0, "EQUITY_BELOW_CONTROL"),
    )

    # Whole-company EV may be source stated, but not calculated from stake-level
    # equity_value + debt/cash without implied_equity_value.
    _assert_equal(
        failures,
        "implied_enterprise_value_stated_preserved",
        _derive_implied_enterprise_value(125.0, "ENTERPRISE_VALUE", None, None),
        (125.0, "STATED"),
    )
    _assert_equal(
        failures,
        "legacy_enterprise_value_no_stake_level_calc",
        _derive_enterprise_value(None, None, 100.0, 20.0),
        (None, None),
    )


def _test_lumina_remaining_stake_regression(failures: list[str]) -> None:
    fields = {
        "v2_event_type": "ACQUISITION",
        "pct_acquired": 20,
        "stake_transition_type": "MAJORITY_ACQUIRE_REMAINING",
    }
    flag = _derive_flags(fields)["is_minority"]
    _assert_equal(failures, "lumina_remaining_20_is_minority_feature", flag, 1)
    _assert_equal(
        failures,
        "lumina_remaining_20_pct_resolves_as_stated",
        _resolve_pct_acquired(fields),
        (20.0, "stated"),
    )

    text = open(HC_PROMPT_PATH, encoding="utf-8").read()
    required = [
        "Current transaction only: distinguish prior ownership",
        'buyer acquires the "remaining X%," extract pct_acquired = X',
        "Do not substitute\n  resulting ownership for the percentage acquired in the current transaction",
        "previously acquired/owned an 80% controlling stake",
        "acquired the remaining 20%, pct_acquired = 20",
        "target becomes a 100% wholly owned subsidiary",
        "stake_transition_type: Nullable explicit ownership-transition classification",
        "MAJORITY_ACQUIRE_REMAINING",
        "Do not infer from pct_acquired alone",
    ]
    for snippet in required:
        if snippet not in text:
            failures.append(f"HC prompt missing remaining-stake pct guidance: {snippet!r}")


def _test_stake_transition_regressions(failures: list[str]) -> None:
    cases = [
        ("0_to_20", "ACQUISITION", 20, "NEW_MINORITY_STAKE", 1),
        ("0_to_50_1_fort_logia", "ACQUISITION", 50.1, "NEW_MAJORITY_STAKE", 0),
        ("0_to_61_17_tpg_lotte", "ACQUISITION", 61.17, "NEW_MAJORITY_STAKE", 0),
        ("30_to_60", "ACQUISITION", 30, "MINORITY_ACQUIRING_MAJORITY", 1),
        ("20_to_100", "ACQUISITION", 80, "MINORITY_ACQUIRING_REMAINING", 0),
        ("60_to_80", "ACQUISITION", 20, "MAJORITY_INCREASING_STAKE", 1),
        ("20_to_35", "ACQUISITION", 15, "MINORITY_INCREASING_STAKE", 1),
        ("80_to_100_lumina", "ACQUISITION", 20, "MAJORITY_ACQUIRE_REMAINING", 1),
        ("0_to_100", "ACQUISITION", 100, "FULL_ACQUISITION", 0),
    ]
    for name, event_type, pct, transition, expected_flag in cases:
        fields = {
            "v2_event_type": event_type,
            "deal_type": event_type,
            "pct_acquired": pct,
            "stake_transition_type": transition,
        }
        flag = _derive_flags(fields)["is_minority"]
        _assert_equal(failures, f"{name} is_minority", flag, expected_flag)
        _assert_equal(
            failures,
            f"{name} pct resolution",
            _resolve_pct_acquired(fields),
            (float(pct), "stated"),
        )

    fallback = _derive_flags({
        "v2_event_type": "ACQUISITION",
        "deal_type": "ACQUISITION",
        "pct_acquired": 20,
        "stake_transition_type": None,
    })["is_minority"]
    _assert_equal(failures, "pct_fallback_without_transition", fallback, 1)

    pct_precedence = _derive_flags({
        "v2_event_type": "ACQUISITION",
        "deal_type": "ACQUISITION",
        "pct_acquired": 80,
        "stake_transition_type": "MAJORITY_ACQUIRE_REMAINING",
    })["is_minority"]
    _assert_equal(failures, "current_pct_overrides_transition_label", pct_precedence, 0)

    transition_without_pct = _derive_flags({
        "v2_event_type": "ACQUISITION",
        "deal_type": "ACQUISITION",
        "pct_acquired": None,
        "stake_transition_type": "MAJORITY_ACQUIRE_REMAINING",
    })["is_minority"]
    _assert_equal(failures, "minority_transition_used_when_pct_missing", transition_without_pct, 1)

    new_majority_without_pct = _derive_flags({
        "v2_event_type": "ACQUISITION",
        "deal_type": "ACQUISITION",
        "pct_acquired": None,
        "stake_transition_type": "NEW_MAJORITY_STAKE",
    })["is_minority"]
    _assert_equal(failures, "new_majority_transition_not_minority_without_pct", new_majority_without_pct, 0)


def _hc_result(stake_transition_type):
    return {
        "target": {},
        "acquirer": {},
        "parent_seller": {},
        "deal": {
            "pct_acquired": 20,
            "stake_transition_type": stake_transition_type,
        },
        "dates": {},
        "value": {"type": None},
        "reported_multiples": [],
        "acquirers": [],
        "buy_side_sponsors": [],
        "parent_sellers": [],
        "value_observations": [],
        "features": {
            "is_platform_investment": None,
            "is_secondary_buyout": None,
            "is_merger_of_equals": None,
        },
        "target_financials": {},
        "financials_disclosure_status": "UNKNOWN",
        "model_confidence": "HIGH",
    }


def _test_hc_stake_transition_validation(failures: list[str]) -> None:
    _assert_equal(
        failures,
        "HC accepts null stake transition",
        _validate_hc(_hc_result(None)),
        None,
    )
    _assert_equal(
        failures,
        "HC accepts Lumina transition",
        _validate_hc(_hc_result("MAJORITY_ACQUIRE_REMAINING")),
        None,
    )
    _assert_equal(
        failures,
        "HC accepts new majority transition",
        _validate_hc(_hc_result("NEW_MAJORITY_STAKE")),
        None,
    )
    _assert_equal(
        failures,
        "HC rejects bad transition",
        _validate_hc(_hc_result("MINORITY_INVESTMENT")),
        "invalid deal.stake_transition_type: 'MINORITY_INVESTMENT'",
    )
    _assert_equal(
        failures,
        "HC rejects UNKNOWN transition",
        _validate_hc(_hc_result("UNKNOWN")),
        "invalid deal.stake_transition_type: 'UNKNOWN'",
    )


def _test_schema_column(failures: list[str]) -> None:
    tmp = tempfile.mkdtemp(prefix="minority_flag_schema_")
    db_path = os.path.join(tmp, "t.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(transaction_record)")}
        if "is_minority" not in cols:
            failures.append("schema: transaction_record.is_minority missing")
        if "stake_transition_type" not in cols:
            failures.append("schema: transaction_record.stake_transition_type missing")
        se_cols = {row["name"] for row in conn.execute("PRAGMA table_info(staging_extraction)")}
        if "stake_transition_type" not in se_cols:
            failures.append("schema: staging_extraction.stake_transition_type missing")
    finally:
        conn.close()


def main() -> int:
    failures: list[str] = []
    _test_flag_and_pct_resolution(failures)
    _test_valuation_guards(failures)
    _test_lumina_remaining_stake_regression(failures)
    _test_stake_transition_regressions(failures)
    _test_hc_stake_transition_validation(failures)
    _test_schema_column(failures)

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS minority flag foundation: derived flag, pct default guard, valuation preservation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
