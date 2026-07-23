#!/usr/bin/env python3
"""Smoke-test Stage 9 take-private flag derivation."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stages.aggregate import _derive_flags


def _case(**overrides: object) -> dict:
    base = {
        "deal_type": "ACQUISITION",
        "target_status": "PUBLIC",
        "target_type": "STANDALONE_COMPANY",
        "acquirer_type": "PRIVATE_EQUITY",
        "acquirer_ticker": None,
    }
    base.update(overrides)
    return base


CASES = [
    ("pe_take_private", _case(acquirer_type="PRIVATE_EQUITY"), 1),
    ("pe_platform_take_private", _case(acquirer_type="PE_PORTFOLIO"), 1),
    ("private_strategic_take_private", _case(acquirer_type="STRATEGIC_CORPORATE"), 1),
    ("private_consortium_take_private", _case(acquirer_type="CONSORTIUM"), 1),
    ("management_take_private", _case(acquirer_type="MANAGEMENT"), 1),
    ("public_acquirer_blocks_flag", _case(acquirer_type="STRATEGIC_CORPORATE", acquirer_ticker="NYSE:ABC"), 0),
    ("public_public_merger_not_take_private", _case(deal_type="MERGER", acquirer_type="STRATEGIC_CORPORATE"), 0),
    ("public_target_asset_sale_not_take_private", _case(target_type="ASSETS", acquirer_type="PRIVATE_EQUITY"), 0),
    ("public_target_subsidiary_sale_not_take_private", _case(target_type="SUBSIDIARY", acquirer_type="PRIVATE_EQUITY"), 0),
    ("minority_investment_not_take_private", _case(deal_type="MINORITY_INVESTMENT", acquirer_type="PRIVATE_EQUITY"), 0),
    ("unknown_acquirer_not_enough", _case(acquirer_type="UNKNOWN"), 0),
]


def main() -> None:
    failed = []
    for name, fields, expected in CASES:
        actual = _derive_flags(fields)["is_take_private"]
        if actual != expected:
            failed.append((name, expected, actual))

    if failed:
        for name, expected, actual in failed:
            print(f"FAIL {name}: expected {expected}, got {actual}")
        raise SystemExit(1)

    print(f"PASS take-private derivation cases={len(CASES)}")


if __name__ == "__main__":
    main()
