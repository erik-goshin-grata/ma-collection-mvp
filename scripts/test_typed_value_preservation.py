#!/usr/bin/env python3
"""Regression guard for typed deal-value preservation.

No live model calls. Two scenarios, both mirroring Samsonite / BÉIS:
- 85% acquired for $178.5M equity value
- separately stated $210M enterprise value
- separately stated 2025 net sales of $210M

Scenario 1 (single source): one article carries both typed value facts. The
legacy collapsed value_amount/value_type slot happens to hold the equity fact.

Scenario 2 (multi source): the cluster spans two articles, and the legacy
collapsed slot resolves to the *enterprise* fact. Every canonical value field
must still be populated from its own semantic type. This is the general
guardrail — independently typed facts must survive independently of whichever
type wins the single legacy slot — and it is the shape the single-source
scenario cannot exercise, because there the legacy winner is the equity fact
by construction.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stages.aggregate as aggregate
from db import get_connection, init_db
from lib.observation_writer import insert_observation, write_staging_observations_for_extraction
from stages.high_confidence_extract import _primary_value, _validate, _value_observations_json


TXN_ID = "tc_samsonite_beis_fixture"


def _assert_equal(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _insert_fixture(conn: sqlite3.Connection) -> int:
    body = (
        "Samsonite Group S.A. will acquire 85% of BÉIS, LLC for $178.5 million. "
        "The transaction represents a total enterprise value of approximately "
        "$210 million on a cash-free, debt-free basis. BÉIS generated net sales "
        "of approximately $210 million in 2025."
    )
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', 'https://example.test/samsonite-beis',
            'Samsonite Group S.A. to acquire BÉIS', '2026-08-13', ?,
            'typed-value-preservation-fixture', 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (body,),
    )
    source_raw_id = int(cur.lastrowid)

    hc_result = {
        "target": {"name": "BÉIS, LLC"},
        "acquirer": {"name": "Samsonite Group S.A.", "type": "strategic_corporate"},
        "parent_seller": {},
        "deal": {"pct_acquired": 85.0, "stake_transition_type": "NEW_MAJORITY_STAKE"},
        "dates": {"announced_date": "2026-08-12", "announced_date_precision": "exact"},
        "value": {
            "amount": 178_500_000,
            "currency": "USD",
            "type": "EQUITY_VALUE",
            "type_confidence": "HIGH",
            "qualifier": None,
            "per_share_price": None,
        },
        "reported_multiples": [],
        "acquirers": [],
        "buy_side_sponsors": [],
        "parent_sellers": [],
        "value_observations": [
            {
                "amount": 178_500_000,
                "currency": "USD",
                "type": "EQUITY_VALUE",
                "basis": "STATED",
                "qualifier": None,
                "evidence": "acquire 85% of BÉIS, LLC for $178.5 million",
            },
            {
                "amount": 210_000_000,
                "currency": "USD",
                "type": "ENTERPRISE_VALUE",
                "basis": "STATED",
                "qualifier": "approximately; cash-free, debt-free",
                "evidence": "total enterprise value of approximately $210 million",
            },
        ],
        "features": {
            "is_platform_investment": None,
            "is_secondary_buyout": None,
            "is_merger_of_equals": None,
        },
        "financials_disclosure_status": "DISCLOSED",
        "target_financials": {
            "revenue_amount": 210_000_000,
            "revenue_period_type": "ANNUAL",
            "revenue_period_end": "2025",
            "currency": "USD",
        },
        "model_confidence": "HIGH",
    }
    err = _validate(hc_result)
    if err:
        raise AssertionError(err)
    mismatched_legacy = dict(hc_result)
    mismatched_legacy["value"] = {
        "amount": 999,
        "currency": "USD",
        "type": "TRANSACTION_VALUE",
        "type_confidence": "HIGH",
        "qualifier": None,
        "per_share_price": None,
    }
    primary = _primary_value(mismatched_legacy)
    if (primary.get("amount"), primary.get("type")) != (178_500_000, "EQUITY_VALUE"):
        raise AssertionError("legacy primary value must be sourced from first typed value observation")
    missing_value_observations = dict(hc_result)
    missing_value_observations.pop("value_observations")
    if _validate(missing_value_observations) is None:
        raise AssertionError("value_observations must be a required output key")
    empty_value_observations = dict(hc_result)
    empty_value_observations["value_observations"] = []
    err = _validate(empty_value_observations)
    if err:
        raise AssertionError(f"empty value_observations array should validate: {err}")
    missing_features = dict(hc_result)
    missing_features.pop("features")
    if _validate(missing_features) is None:
        raise AssertionError("features must be a required output key")
    invalid_feature = dict(hc_result)
    invalid_feature["features"] = dict(hc_result["features"])
    # Keyed on is_secondary_buyout since V3 §T7 retired is_platform_investment from the
    # features contract. The assertion is unchanged: features values are boolean or null.
    invalid_feature["features"]["is_secondary_buyout"] = "yes"
    if _validate(invalid_feature) is None:
        raise AssertionError("features values must be boolean or null")

    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, stake_transition_type, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            value_observations,
            target_revenue, target_revenue_period_type, target_revenue_period_type_v2,
            target_revenue_period_end, financials_currency, financials_disclosure_status,
            model_confidence, dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'BÉIS, LLC', 'Samsonite Group S.A.', 'strategic_corporate', 'strategic_corporate',
            85.0, 'NEW_MAJORITY_STAKE', '2026-08-12', 'exact',
            178500000, 'USD', 'EQUITY_VALUE', 'HIGH',
            ?,
            210000000, 'ANNUAL', 'ANNUAL', '2025', 'USD', 'DISCLOSED',
            'HIGH', '0.7', '0.15', ?
        )
        """,
        (source_raw_id, _value_observations_json(hc_result), TXN_ID),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn,
        extraction_id,
        observation_source_stage="HC_EXTRACT",
        include_stage3=True,
        include_hc=True,
    )
    conn.commit()
    return extraction_id


def _insert_second_source_fixture(conn: sqlite3.Connection) -> int:
    """A second independent article in the same cluster, stating only the $210M EV.

    Non-value fields deliberately mirror the first source so the only contested
    field group is the value pair — the scenario under test. The legacy
    value_amount/value_type slot on this row carries ENTERPRISE_VALUE, so the
    cluster's collapsed legacy winner is no longer the equity fact.
    """
    body = (
        "Samsonite Group S.A.'s acquisition of BÉIS, LLC values the business at "
        "a total enterprise value of approximately $210 million."
    )
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', 'https://example.test/samsonite-beis-second',
            'Samsonite values BÉIS at $210 million', '2026-08-13', ?,
            'typed-value-preservation-fixture-2', 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (body,),
    )
    source_raw_id = int(cur.lastrowid)

    hc_result = {
        "target": {"name": "BÉIS, LLC"},
        "acquirer": {"name": "Samsonite Group S.A.", "type": "strategic_corporate"},
        "parent_seller": {},
        "deal": {"pct_acquired": 85.0, "stake_transition_type": "NEW_MAJORITY_STAKE"},
        "dates": {"announced_date": "2026-08-12", "announced_date_precision": "exact"},
        "value": {
            "amount": 210_000_000,
            "currency": "USD",
            "type": "ENTERPRISE_VALUE",
            "type_confidence": "HIGH",
            "qualifier": None,
            "per_share_price": None,
        },
        "reported_multiples": [],
        "acquirers": [],
        "buy_side_sponsors": [],
        "parent_sellers": [],
        "value_observations": [
            {
                "amount": 210_000_000,
                "currency": "USD",
                "type": "ENTERPRISE_VALUE",
                "basis": "STATED",
                "qualifier": "approximately",
                "evidence": "total enterprise value of approximately $210 million",
            },
        ],
        "features": {
            "is_platform_investment": None,
            "is_secondary_buyout": None,
            "is_merger_of_equals": None,
        },
        "financials_disclosure_status": "DISCLOSED",
        "target_financials": {},
        "model_confidence": "HIGH",
    }
    err = _validate(hc_result)
    if err:
        raise AssertionError(err)

    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, stake_transition_type, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            value_observations,
            financials_disclosure_status,
            model_confidence, dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'BÉIS, LLC', 'Samsonite Group S.A.', 'strategic_corporate', 'strategic_corporate',
            85.0, 'NEW_MAJORITY_STAKE', '2026-08-12', 'exact',
            210000000, 'USD', 'ENTERPRISE_VALUE', 'HIGH',
            ?,
            'DISCLOSED',
            'HIGH', '0.7', '0.15', ?
        )
        """,
        (source_raw_id, _value_observations_json(hc_result), TXN_ID),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn,
        extraction_id,
        observation_source_stage="HC_EXTRACT",
        include_stage3=True,
        include_hc=True,
    )
    conn.commit()
    return extraction_id


def _enterprise_winner_conflict_stub(field_name, _field_type, _context, observations, *_args, **_kwargs):
    """Legacy-slot conflict resolution that lands on the enterprise fact.

    With two of the three typed value observations in the cluster stating
    ENTERPRISE_VALUE, the collapsed legacy pair resolving to 210M/ENTERPRISE_VALUE
    is a legitimate outcome. The canonical typed fields must not depend on it.
    """
    chosen = {"value_amount": 210_000_000.0, "value_type": "ENTERPRISE_VALUE"}
    if field_name not in chosen:
        raise AssertionError(f"unexpected aggregation conflict for {field_name}")
    return {
        "chosen_observation_id": observations[0]["observation_id"],
        "chosen_value": chosen[field_name],
        "aggregation_confidence": "HIGH",
        "conflict_type": "SEMANTIC",
        "flagged_for_review": False,
        "reasoning": "two of three typed observations state enterprise value",
        "notes": None,
        "prompt_version": "test",
    }


def _legacy_conflict_stub(field_name, _field_type, _context, observations, *_args, **_kwargs):
    if field_name == "value_amount":
        return {
            "chosen_observation_id": observations[0]["observation_id"],
            "chosen_value": 178_500_000.0,
            "aggregation_confidence": "HIGH",
            "conflict_type": "SEMANTIC",
            "flagged_for_review": False,
            "reasoning": "legacy compatibility value remains primary equity value",
            "notes": None,
            "prompt_version": "test",
        }
    if field_name == "value_type":
        return {
            "chosen_observation_id": observations[0]["observation_id"],
            "chosen_value": "EQUITY_VALUE",
            "aggregation_confidence": "HIGH",
            "conflict_type": "SEMANTIC",
            "flagged_for_review": False,
            "reasoning": "legacy compatibility value_type remains primary equity value",
            "notes": None,
            "prompt_version": "test",
        }
    raise AssertionError(f"unexpected aggregation conflict for {field_name}")


def _scenario_single_source(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "typed_value_preservation.db")
        init_db(db_path)
        conn = get_connection(db_path)
        extraction_id = _insert_fixture(conn)

        typed_rows = conn.execute(
            """
            SELECT observation_fact_key, field_name, field_value, field_value_numeric
            FROM transaction_field_observation
            WHERE staging_extraction_id = ?
              AND observation_fact_key IS NOT NULL
              AND field_name IN ('value_amount', 'value_currency', 'value_type', 'value_observation')
            ORDER BY observation_fact_key, field_name
            """,
            (extraction_id,),
        ).fetchall()
        fact_keys = {row["observation_fact_key"] for row in typed_rows}
        _assert_equal(failures, "typed fact keys", fact_keys, {"value_observations[0]", "value_observations[1]"})
        ev_amounts = [
            row["field_value_numeric"]
            for row in typed_rows
            if row["observation_fact_key"] == "value_observations[1]" and row["field_name"] == "value_amount"
        ]
        _assert_equal(failures, "enterprise value amount observation", ev_amounts, [210_000_000.0])
        audit_rows = [row for row in typed_rows if row["field_name"] == "value_observation"]
        _assert_equal(failures, "auditable typed value payload count", len(audit_rows), 2)
        json.loads(audit_rows[1]["field_value"])

        insert_observation(
            conn,
            transaction_id=None,
            field_name="value_amount",
            field_value=210_000_000,
            staging_extraction_id=extraction_id,
            source_raw_id=1,
            observation_fact_key="same_numeric_a",
        )
        insert_observation(
            conn,
            transaction_id=None,
            field_name="value_amount",
            field_value=210_000_000,
            staging_extraction_id=extraction_id,
            source_raw_id=1,
            observation_fact_key="same_numeric_b",
        )
        same_numeric_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM transaction_field_observation
            WHERE transaction_id IS NULL
              AND staging_extraction_id = ?
              AND field_name = 'value_amount'
              AND field_value_numeric = 210000000
            """,
            (extraction_id,),
        ).fetchone()[0]
        _assert_equal(failures, "same numeric amount with different fact keys", same_numeric_count, 2)

        original_call_agg_prompt = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = _legacy_conflict_stub
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
            aggregate.run(conn, cfg, "typed_value_preservation_test")
        finally:
            aggregate._call_agg_prompt = original_call_agg_prompt

        row = conn.execute("SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)).fetchone()
        _assert_equal(failures, "legacy value_amount", row["value_amount"], 178_500_000.0)
        _assert_equal(failures, "legacy value_type", row["value_type"], "EQUITY_VALUE")
        _assert_equal(failures, "equity_value", row["equity_value"], 178_500_000.0)
        _assert_equal(failures, "transaction_value", row["transaction_value"], 178_500_000.0)
        _assert_equal(failures, "transaction_value_basis", row["transaction_value_basis"], "EQUITY_VALUE_ONLY")
        _assert_equal(failures, "implied_equity_value", row["implied_equity_value"], 210_000_000.0)
        _assert_equal(failures, "implied_enterprise_value", row["implied_enterprise_value"], 210_000_000.0)
        _assert_equal(failures, "implied_enterprise_value_basis", row["implied_enterprise_value_basis"], "STATED")
        _assert_equal(failures, "target_revenue", row["target_revenue"], 210_000_000.0)
        _assert_equal(failures, "target_revenue_period_type", row["target_revenue_period_type"], "ANNUAL")
        _assert_equal(failures, "target_revenue_period_end", row["target_revenue_period_end"], "2025")
        _assert_equal(failures, "stake_transition_type", row["stake_transition_type"], "NEW_MAJORITY_STAKE")
        _assert_equal(failures, "is_minority", row["is_minority"], 0)
        conn.close()


def _scenario_multi_source(failures: list[str]) -> None:
    """Two articles, one cluster, legacy slot resolving to the enterprise fact.

    Asserts the canonical value model is driven by the typed observations rather
    than by whichever semantic type wins the single legacy value_amount/value_type
    pair.
    """
    prefix = "multi-source"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "typed_value_preservation_multi.db")
        init_db(db_path)
        conn = get_connection(db_path)
        _insert_fixture(conn)
        _insert_second_source_fixture(conn)

        original_call_agg_prompt = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = _enterprise_winner_conflict_stub
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
            aggregate.run(conn, cfg, "typed_value_preservation_multi_test")
        finally:
            aggregate._call_agg_prompt = original_call_agg_prompt

        row = conn.execute("SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)).fetchone()
        if row is None:
            failures.append(f"{prefix}: no transaction_record produced for {TXN_ID}")
            conn.close()
            return

        # The legacy compatibility slot is expected to hold the enterprise fact here.
        _assert_equal(failures, f"{prefix} legacy value_amount", row["value_amount"], 210_000_000.0)
        _assert_equal(failures, f"{prefix} legacy value_type", row["value_type"], "ENTERPRISE_VALUE")

        # Canonical typed fields must be unaffected by that collapse.
        _assert_equal(failures, f"{prefix} equity_value", row["equity_value"], 178_500_000.0)
        _assert_equal(failures, f"{prefix} equity_value_basis", row["equity_value_basis"], "STATED")
        _assert_equal(failures, f"{prefix} transaction_value", row["transaction_value"], 178_500_000.0)
        _assert_equal(
            failures, f"{prefix} transaction_value_basis", row["transaction_value_basis"], "EQUITY_VALUE_ONLY"
        )
        _assert_equal(failures, f"{prefix} implied_equity_value", row["implied_equity_value"], 210_000_000.0)
        _assert_equal(
            failures, f"{prefix} implied_enterprise_value", row["implied_enterprise_value"], 210_000_000.0
        )
        _assert_equal(
            failures, f"{prefix} implied_enterprise_value_basis", row["implied_enterprise_value_basis"], "STATED"
        )
        _assert_equal(failures, f"{prefix} target_revenue", row["target_revenue"], 210_000_000.0)
        _assert_equal(failures, f"{prefix} ev_to_revenue_ltm", row["ev_to_revenue_ltm"], 1.0)
        _assert_equal(failures, f"{prefix} pct_acquired", row["pct_acquired"], 85.0)
        conn.close()


def main() -> None:
    failures: list[str] = []
    _scenario_single_source(failures)
    _scenario_multi_source(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS typed value preservation: single-source and multi-source typed facts survive")


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    main()
