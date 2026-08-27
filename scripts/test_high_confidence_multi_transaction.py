#!/usr/bin/env python3
"""Regression tests for high-confidence multi-transaction extraction.

Locks two narrow behaviors:
- additional HC transaction rows bind the same number of values as columns;
- prompts allow multi-transaction extraction only for a shared announcement/event
  context, not unrelated roundup/list stories.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
import stages.high_confidence_extract as hc


def _txn(target: str, acquirer: str) -> dict:
    return {
        "target": {
            "name": target,
            "domain": None,
            "ticker": None,
            "description": "Target company.",
        },
        "acquirer": {
            "name": acquirer,
            "domain": None,
            "ticker": None,
            "type": "strategic_corporate",
            "description": "Acquirer company.",
            "sponsor_name": None,
        },
        "parent_seller": {
            "name": None,
            "ticker": None,
            "description": None,
        },
        "deal": {
            "pct_acquired": None,
            "stake_transition_type": "FULL_ACQUISITION",
        },
        "dates": {
            "announced_date": "2026-08-12",
            "announced_date_precision": "exact",
            "closed_date": "2026-08-12",
            "closed_date_precision": "exact",
            "signing_date": None,
            "signing_date_precision": None,
            "rumor_date": None,
        },
        "value": {
            "amount": None,
            "currency": None,
            "type": "UNDISCLOSED",
            "type_confidence": "HIGH",
            "qualifier": None,
            "per_share_price": None,
        },
        "reported_multiples": [],
        "value_observations": [],
        "features": {
            "is_platform_investment": None,
            "is_secondary_buyout": None,
            "is_merger_of_equals": None,
        },
        "target_financials": {
            "revenue_amount": None,
            "revenue_period_type": None,
            "revenue_period_end": None,
            "ebitda_amount": None,
            "ebitda_period_type": None,
            "ebitda_period_end": None,
            "currency": None,
        },
        "financials_disclosure_status": "UNDISCLOSED",
        "consideration_type": None,
        "model_confidence": "HIGH",
        "notes": "Two acquisitions announced in the same release.",
    }


def _assert_insert_shape(failures: list[str]) -> None:
    source = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    match = re.search(
        r"INSERT INTO staging_extraction \(\n(.*?)\n\s*\) VALUES \(\n\s*([?,]+)\n\s*\)",
        source,
        re.S,
    )
    if not match:
        failures.append("Could not find HC multi-transaction INSERT")
        return
    cols = [c.strip() for c in match.group(1).replace("\n", " ").split(",") if c.strip()]
    placeholders = match.group(2).count("?")
    if len(cols) != placeholders:
        failures.append(
            f"HC multi-transaction INSERT shape mismatch: {len(cols)} columns, {placeholders} placeholders"
        )


def _assert_prompt_guardrails(failures: list[str]) -> None:
    checks = {
        "prompts/high_confidence_extraction.md": [
            "same announcement/event context",
            "Do not split merely because a summary, roundup, market brief",
            "mentions several unrelated deals",
        ],
        "prompts/funding_hc_extraction.md": [
            "same announcement/event context",
            "Do not split merely because a summary, roundup, market brief",
            "mentions several unrelated financings",
        ],
    }
    for rel, snippets in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                failures.append(f"{rel} missing prompt guardrail: {snippet!r}")


def _assert_multi_transaction_db_write(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "hc_multi.db")
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO source_raw
                    (source_type, source_tier, url, title, published_date,
                     clean_text, content_hash, source_status, fetched_at)
                VALUES ('WEB_URL', 'T2', 'https://example.com/two-deals',
                        'Buyer announces two acquisitions', '2026-08-12',
                        'Buyer acquired Alpha and Beta in the same announcement.',
                        'hc_multi_hash', 'FETCHED', '2026-08-12T00:00:00Z')
                """
            )
            source_raw_id = conn.execute("SELECT source_raw_id FROM source_raw").fetchone()[0]
            conn.execute(
                """
                INSERT INTO staging_extraction
                    (source_raw_id, status, deal_type, v2_event_type,
                     event_type, event_history_type, target_status,
                     dt_prompt_version)
                VALUES (?, 'CLASSIFIED', 'ACQUISITION', 'ACQUISITION',
                        'ANNOUNCEMENT', 'ANNOUNCEMENT', 'PRIVATE',
                        'deal_type_classifier:test')
                """,
                (source_raw_id,),
            )
            conn.commit()

            original_call_prompt = hc.call_prompt
            original_sleep = hc._SLEEP
            hc._SLEEP = 0
            hc.call_prompt = lambda **_kwargs: {
                "transactions": [
                    _txn("Alpha Target", "Buyer Inc."),
                    _txn("Beta Target", "Buyer Inc."),
                ]
            }
            try:
                result = hc.run(
                    conn=conn,
                    cfg=SimpleNamespace(log_level="ERROR"),
                    run_id="test_hc_multi_transaction",
                )
            finally:
                hc.call_prompt = original_call_prompt
                hc._SLEEP = original_sleep

            if result.get("hc_extracted") != 1 or result.get("failures") != 0:
                failures.append(f"unexpected HC run result: {result!r}")

            rows = conn.execute(
                """
                SELECT target_name, multi_transaction_index, multi_transaction_total,
                       stake_transition_type
                FROM staging_extraction
                ORDER BY multi_transaction_index
                """
            ).fetchall()
            got = [(r[0], r[1], r[2], r[3]) for r in rows]
            expected = [
                ("Alpha Target", 0, 2, "FULL_ACQUISITION"),
                ("Beta Target", 1, 2, "FULL_ACQUISITION"),
            ]
            if got != expected:
                failures.append(f"unexpected staged rows: expected {expected!r}, got {got!r}")
        finally:
            conn.close()


def _assert_multi_transaction_requires_value_observations_per_item(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "hc_multi_missing_value_observations.db")
        init_db(db_path)
        conn = get_connection(db_path)
        try:
            conn.execute(
                """
                INSERT INTO source_raw
                    (source_type, source_tier, url, title, published_date,
                     clean_text, content_hash, source_status, fetched_at)
                VALUES ('WEB_URL', 'T2', 'https://example.com/two-deals-missing-field',
                        'Buyer announces two acquisitions', '2026-08-12',
                        'Buyer acquired Alpha and Beta in the same announcement.',
                        'hc_multi_missing_hash', 'FETCHED', '2026-08-12T00:00:00Z')
                """
            )
            source_raw_id = conn.execute("SELECT source_raw_id FROM source_raw").fetchone()[0]
            conn.execute(
                """
                INSERT INTO staging_extraction
                    (source_raw_id, status, deal_type, v2_event_type,
                     event_type, event_history_type, target_status,
                     dt_prompt_version)
                VALUES (?, 'CLASSIFIED', 'ACQUISITION', 'ACQUISITION',
                        'ANNOUNCEMENT', 'ANNOUNCEMENT', 'PRIVATE',
                        'deal_type_classifier:test')
                """,
                (source_raw_id,),
            )
            conn.commit()

            missing = _txn("Beta Target", "Buyer Inc.")
            missing.pop("value_observations")

            original_call_prompt = hc.call_prompt
            original_sleep = hc._SLEEP
            hc._SLEEP = 0
            hc.call_prompt = lambda **_kwargs: {
                "transactions": [
                    _txn("Alpha Target", "Buyer Inc."),
                    missing,
                ]
            }
            try:
                result = hc.run(
                    conn=conn,
                    cfg=SimpleNamespace(log_level="ERROR"),
                    run_id="test_hc_multi_transaction_missing_value_observations",
                )
            finally:
                hc.call_prompt = original_call_prompt
                hc._SLEEP = original_sleep

            if result.get("hc_extracted") != 0 or result.get("failures") != 1:
                failures.append(f"missing per-item value_observations did not fail HC run: {result!r}")
        finally:
            conn.close()


def main() -> None:
    failures: list[str] = []
    _assert_insert_shape(failures)
    _assert_prompt_guardrails(failures)
    _assert_multi_transaction_db_write(failures)
    _assert_multi_transaction_requires_value_observations_per_item(failures)
    if failures:
        for failure in failures:
            print("FAIL:", failure)
        raise SystemExit(1)
    print("PASS high-confidence multi-transaction guardrails")


if __name__ == "__main__":
    main()
