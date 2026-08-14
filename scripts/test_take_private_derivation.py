#!/usr/bin/env python3
"""Smoke-test Stage 9 take-private flag derivation."""

from __future__ import annotations

import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from stages.aggregate import _derive_flags, _load_sponsor_participant_context


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

    feature_cases = [
        (
            "platform_explicit_true",
            _case(is_platform_investment=1, acquirer_type="PRIVATE_EQUITY"),
            "is_platform_investment",
            1,
        ),
        (
            "platform_pe_buyer_alone_false",
            _case(acquirer_type="PRIVATE_EQUITY"),
            "is_platform_investment",
            0,
        ),
        (
            "secondary_explicit_true",
            _case(is_secondary_buyout=1, acquirer_type="PRIVATE_EQUITY"),
            "is_secondary_buyout",
            1,
        ),
        (
            "secondary_side_qualified_sponsors_true",
            _case(
                acquirer_type="PRIVATE_EQUITY",
                _has_buyer_sponsor_party=1,
                _has_seller_sponsor_party=1,
            ),
            "is_secondary_buyout",
            1,
        ),
        (
            "secondary_pe_buyer_alone_false",
            _case(acquirer_type="PRIVATE_EQUITY"),
            "is_secondary_buyout",
            0,
        ),
        (
            "secondary_one_sponsor_side_false",
            _case(acquirer_type="PRIVATE_EQUITY", _has_buyer_sponsor_party=1),
            "is_secondary_buyout",
            0,
        ),
        (
            "secondary_non_ma_even_with_sponsors_false",
            _case(
                deal_type="GROWTH_EQUITY",
                acquirer_type="PRIVATE_EQUITY",
                _has_buyer_sponsor_party=1,
                _has_seller_sponsor_party=1,
            ),
            "is_secondary_buyout",
            0,
        ),
        (
            "moe_explicit_true",
            _case(deal_type="MERGER", is_merger_of_equals=1),
            "is_merger_of_equals",
            1,
        ),
        (
            "moe_merger_structure_alone_false",
            _case(deal_type="MERGER"),
            "is_merger_of_equals",
            0,
        ),
    ]
    for name, fields, flag_name, expected in feature_cases:
        actual = _derive_flags(fields)[flag_name]
        if actual != expected:
            failed.append((name, expected, actual))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE transaction_participant (
            transaction_id TEXT,
            party_name TEXT,
            side TEXT,
            participant_role TEXT,
            is_current INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO transaction_participant (transaction_id, party_name, side, participant_role, is_current) VALUES (?,?,?,?,?)",
        [
            ("side_qualified", "Buyer Sponsor", "BUYER", "BUYER_SPONSOR", 1),
            ("side_qualified", "Seller Sponsor", "SELLER", "SELLER_SPONSOR", 1),
            ("generic_mentions", "Buyer Sponsor", None, "BUYER_SPONSOR", 1),
            ("generic_mentions", "Seller Sponsor", None, "SELLER_SPONSOR", 1),
        ],
    )
    context = _load_sponsor_participant_context(conn)
    if context.get("side_qualified") != {"_has_buyer_sponsor_party": 1, "_has_seller_sponsor_party": 1}:
        failed.append(("secondary_loader_side_qualified", {"buyer": 1, "seller": 1}, context.get("side_qualified")))
    generic_fields = _case(acquirer_type="PRIVATE_EQUITY")
    generic_fields.update(context.get("generic_mentions", {}))
    if _derive_flags(generic_fields)["is_secondary_buyout"] != 0:
        failed.append(("secondary_loader_generic_mentions", 0, context.get("generic_mentions")))

    if failed:
        for name, expected, actual in failed:
            print(f"FAIL {name}: expected {expected}, got {actual}")
        raise SystemExit(1)

    print(f"PASS transaction feature derivation cases={len(CASES) + len(feature_cases)}")


if __name__ == "__main__":
    main()
