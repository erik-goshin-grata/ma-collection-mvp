#!/usr/bin/env python3
"""Stage 9 take-private flag derivation — unit cases plus the production path.

`is_take_private` means: a PUBLIC, STANDALONE_COMPANY target acquired by a buyer with no
public listing of its own, into private ownership. Four conditions, unchanged here.

TWO LAYERS, AND THE SECOND ONE IS THE POINT.

The CASES table below tests `_derive_flags()` directly. That layer is kept because it is a
cheap guard on the condition logic, but on its own it certified a field that was broken in
production for every transaction: it hands the derivation hand-authored UPPERCASE values,
while Stage 3 and Stage 4 store what the prompts emit, which is LOWERCASE. The comparison in
`aggregate.py` was written against the uppercase form, so `target_type != "STANDALONE_COMPANY"`
returned early on every real row and the flag was 0 everywhere. The unit layer could not see
that, because it never touched storage.

The production-path section therefore drives the real chain:

    staging_extraction -> production observation writer (include_stage3 / include_hc)
                       -> observation ledger -> Stage 9 at the CONFIGURED read source
                       -> canonical transaction_record.is_take_private

with the values the shipped prompts actually produce. One qualifying transaction must reach
1; four controls must stay 0 through the identical path, so the proof is isolation rather
than "the flag can be made truthy". A separate legacy-uppercase case pins read tolerance:
stored rows may still carry the uppercase form, and repairing new-production casing must not
strand them.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
from stages.aggregate import _derive_flags, _load_sponsor_participant_context
from lib.observation_writer import (
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)


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



# ---------------------------------------------------------------------------
# Production path: staging -> observations -> configured Stage 9 -> canonical
# ---------------------------------------------------------------------------
#
# Values below are the ones the SHIPPED prompts emit. deal_type and target_status are
# genuinely UPPERCASE in production (Stage 3 emits ACQUISITION / PUBLIC); target_type and
# acquirer_type are LOWERCASE (deal_type_classifier and high_confidence_extraction both
# emit the V2 lowercase vocabulary). Mixing the two casings here is not sloppiness -- it is
# what a real row looks like, and the mismatch between the two lowercase fields and the
# uppercase comparisons in aggregate.py is the whole defect.
#
# Note which column carries which value. Stage 3 writes the RAW model output to
# `target_type` and the normalized value to `target_type_v2`; Stage 4 does the same with
# `acquirer_type` / `acquirer_type_v2`. The derivation reads the legacy-named column, so
# that column is what must be seeded to reproduce production.

_PROD_CASES = [
    # (label, expected, overrides)
    ("positive_pe_lowercase", 1, {}),
    ("control_public_acquirer_has_ticker", 0, {"acquirer_ticker": "NYSE:ABC"}),
    ("control_assets_target", 0, {"target_type": "assets", "target_type_v2": "assets"}),
    ("control_private_target_status", 0, {"target_status": "PRIVATE"}),
    ("control_minority_investment", 0, {"deal_type": "MINORITY_INVESTMENT",
                                        "v2_event_type": "MINORITY_INVESTMENT"}),
    # Kept deliberately separate from the four controls: this is not a V3-production row.
    # It proves that repairing new-production casing does not strand rows already stored in
    # the legacy uppercase form, which Stage 3 and Stage 4 still accept.
    ("legacy_uppercase_read_tolerance", 1, {"target_type": "STANDALONE_COMPANY",
                                            "acquirer_type": "PRIVATE_EQUITY"}),
]


def _run_production_case(label: str, overrides: dict) -> int | None:
    """Seed one transaction, run the real chain, return canonical is_take_private."""
    txn = f"tc_tp_{label}"
    db_path = os.path.join(tempfile.mkdtemp(), "tp.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        row = {
            "source_raw_id": srid,
            "status": "CLUSTERED",
            "deal_type": "ACQUISITION",           # uppercase in production
            "v2_event_type": "ACQUISITION",       # uppercase in production
            "event_history_type": "ANNOUNCED",
            "target_status": "PUBLIC",            # uppercase in production
            "target_type": "standalone_company",      # lowercase in production
            "target_type_v2": "standalone_company",
            "target_name": "Verity Biosciences",
            "target_ticker": "NASDAQ: VRTY",
            "acquirer_name": "Halden Capital Partners",
            "acquirer_ticker": None,              # no public listing -> stays private
            "acquirer_type": "private_equity",        # lowercase in production
            "acquirer_type_v2": "private_equity",
            "announced_date": "2026-08-18",
            "announced_date_precision": "exact",
            "financials_disclosure_status": "UNKNOWN",
            "model_confidence": "HIGH",
            "dt_prompt_version": "0.11",
            "hc_prompt_version": "0.20",
            "transaction_cluster_id": txn,
        }
        row.update(overrides)
        cols = [c for c in row if row[c] is not None]
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Production writer with the production flags -- not a local field list.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "take-private-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT is_take_private FROM transaction_record WHERE transaction_id=?",
            (txn,)).fetchone()
        return None if canon is None else canon["is_take_private"]
    finally:
        conn.close()


def _test_production_path(failed: list) -> None:
    src = DEFAULT_AGGREGATION_READ_SOURCE
    for label, expected, overrides in _PROD_CASES:
        got = _run_production_case(label, overrides)
        if got is None:
            failed.append((f"production/{label}", expected, "no transaction_record row"))
        elif got != expected:
            failed.append((f"production/{label} (read_source={src})", expected, got))


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

    _test_production_path(failed)

    if failed:
        for name, expected, actual in failed:
            print(f"FAIL {name}: expected {expected}, got {actual}")
        raise SystemExit(1)

    print(f"PASS transaction feature derivation  unit={len(CASES) + len(feature_cases)}  production-path={len(_PROD_CASES)}")


if __name__ == "__main__":
    main()
