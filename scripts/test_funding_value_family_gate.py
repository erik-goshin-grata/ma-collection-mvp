#!/usr/bin/env python3
"""Regression guard: funding events never derive M&A-style canonical values.

No network and no model calls.

A funding round is primary capital going *into* the company. Its magnitude is
`round_size`, never a purchase price, so `transaction_value` and `equity_value` are
categorically inapplicable — not merely usually absent.

Stage 9 relied on that being true upstream rather than enforcing it.
`_derive_transaction_value` fires on any row carrying
`value_type = 'TRANSACTION_VALUE'`, and `_derive_equity_value` on any row carrying
`EQUITY_VALUE`, with no event-family check. `_compute_multiples` and
`_derive_investment_amount` do gate on family; these two did not.

That assumption held only while every funding row reached the funding extractor. Rows
extracted before 2026-08-07 went through the M&A path — which had no `round_size`
write and no capital-raised precondition until prompt 0.13 — so a Series A landed in
`value_amount` typed `TRANSACTION_VALUE`. Ten such rows exist in the live corpus, all
at prompt 0.12. On re-aggregation Stage 9 would faithfully regenerate a canonical M&A
`transaction_value` for each one, forever.

**This guard is independent of that backfill.** Correcting the ten rows fixes the data;
this stops any future stale or misclassified funding row from manufacturing a purchase
price out of a raise. The two are separate concerns and the guard is the durable one.

**Nothing is destroyed by refusing.** The amount stays in `staging_extraction` and in
the observation ledger — asserted below, because the remediation depends on those
amounts remaining findable. It is deliberately *not* parked in `investment_amount`:
that field means one named investor's check, and a generic funding amount sitting
there asserts a party-level fact no source stated.

**The gate is the funding family only.** `MINORITY_INVESTMENT` is deliberately outside
it: buying a non-controlling stake from an existing holder is an ordinary acquisition
whose consideration is a real `EQUITY_VALUE`, and the classifier routes genuine
secondaries to `ACQUISITION` precisely so this stays true.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stages.aggregate as aggregate
from db import get_connection, init_db
from lib.observation_writer import write_staging_observations_for_extraction

FUNDING = ("VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT")

LEGACY_TXN = "tc_gate_legacy_funding"
CLEAN_TXN = "tc_gate_clean_funding"


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


# --------------------------------------------------------------------------
# A. Unit — the two derivations refuse for funding events
# --------------------------------------------------------------------------
def _check_unit(failures: list[str]) -> None:
    for event in FUNDING:
        # A stated TRANSACTION_VALUE on a funding row is a mapping error, not a price.
        value, basis = aggregate._derive_transaction_value(
            {"v2_event_type": event, "value_type": "TRANSACTION_VALUE", "value_amount": 60_000_000},
            None, None,
            is_control=False, is_below_control=True, equity_currency="USD", total_debt_currency=None)
        _check(failures, f"{event} transaction_value", value, None)
        _check(failures, f"{event} transaction_value_basis", basis, None)

        # Even with an equity figure and a resolved pct, no purchase price exists.
        value, basis = aggregate._derive_transaction_value(
            {"v2_event_type": event}, 60_000_000, None,
            is_control=True,
            equity_currency="USD", total_debt_currency=None)
        _check(failures, f"{event} transaction_value via equity", value, None)
        _check(failures, f"{event} transaction_value basis via equity", basis, None)

        # A stated EQUITY_VALUE on a funding row is likewise inapplicable.
        value, basis = aggregate._derive_equity_value({"v2_event_type": event, "value_type": "EQUITY_VALUE", "value_amount": 60_000_000})
        _check(failures, f"{event} equity_value", value, None)
        _check(failures, f"{event} equity_value_basis", basis, None)

    # --- Boundary: the gate is the funding family, nothing wider ------------
    # MINORITY_INVESTMENT is non-control but NOT funding. A secondary purchase of a
    # stake is an ordinary acquisition and keeps a real equity consideration.
    value, basis = aggregate._derive_equity_value({"v2_event_type": "MINORITY_INVESTMENT", "value_type": "EQUITY_VALUE",
         "value_amount": 600_000_000})
    _check(failures, "MINORITY_INVESTMENT keeps equity_value", value, 600_000_000.0)
    _check(failures, "MINORITY_INVESTMENT keeps equity basis", basis, "STATED")

    # --- Regression: M&A behaviour is untouched -----------------------------
    value, basis = aggregate._derive_transaction_value(
        {"v2_event_type": "ACQUISITION", "value_type": "TRANSACTION_VALUE", "value_amount": 80},
        6, None,
            is_control=True, equity_currency="USD", total_debt_currency="USD")
    _check(failures, "ACQUISITION stated transaction_value", value, 80.0)
    _check(failures, "ACQUISITION stated basis", basis, "STATED")

    value, basis = aggregate._derive_equity_value({"v2_event_type": "ACQUISITION", "value_type": "EQUITY_VALUE", "value_amount": 600})
    _check(failures, "ACQUISITION equity_value", value, 600.0)

    # An unknown/absent event type must not be swept into the gate.
    value, basis = aggregate._derive_transaction_value(
        {"value_type": "TRANSACTION_VALUE", "value_amount": 80}, 6, None,
            is_control=True,
        equity_currency="USD", total_debt_currency="USD")
    _check(failures, "no event type still derives", value, 80.0)


# --------------------------------------------------------------------------
# B. End to end — a legacy-shaped row cannot regenerate M&A values
# --------------------------------------------------------------------------
def _insert(
    conn: sqlite3.Connection, txn_id: str, *, event: str,
    value_amount: float | None, value_type: str | None,
    round_size: float | None, hc_version: str,
) -> None:
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', ?, 'Company raises Series A', '2026-08-13', ?,
            ?, 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (f"https://example.test/{txn_id}",
         "Arcade.dev today announced $60 million in Series A funding.",
         f"gate-{txn_id}"),
    )
    source_raw_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_name, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            round_size, round_currency, financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', ?, ?, 'ANNOUNCED',
            'PRIVATE', ?, '2026-08-12', 'exact',
            ?, 'USD', ?, 'HIGH',
            ?, 'USD', 'DISCLOSED', 'HIGH', '0.7', ?, ?
        )
        """,
        (source_raw_id, event, event, f"Target {txn_id}",
         value_amount, value_type, round_size, hc_version, txn_id),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn, extraction_id, observation_source_stage="HC_EXTRACT",
        include_stage3=True, include_hc=True,
    )
    conn.commit()


def _check_end_to_end(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "funding_gate.db")
        init_db(db_path)
        conn = get_connection(db_path)

        # The live corpus shape: prompt 0.12, raise typed TRANSACTION_VALUE, no round_size.
        _insert(conn, LEGACY_TXN, event="VC_ROUND", value_amount=60_000_000,
                value_type="TRANSACTION_VALUE", round_size=None, hc_version="0.12")
        # A correctly extracted round, for contrast.
        _insert(conn, CLEAN_TXN, event="VC_ROUND", value_amount=None,
                value_type=None, round_size=80_000_000, hc_version="0.15")

        original = aggregate._call_agg_prompt

        def _no_conflict(field_name, *_a, **_kw):
            raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")

        aggregate._call_agg_prompt = _no_conflict
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
            aggregate.run(conn, cfg, "funding_gate")
        finally:
            aggregate._call_agg_prompt = original

        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (LEGACY_TXN,)
        ).fetchone()
        if row is None:
            failures.append("legacy funding fixture produced no transaction_record")
        else:
            # No M&A canonical value may be regenerated from the stale field.
            _check(failures, "legacy transaction_value", row["transaction_value"], None)
            _check(failures, "legacy transaction_value_basis", row["transaction_value_basis"], None)
            _check(failures, "legacy equity_value", row["equity_value"], None)
            _check(failures, "legacy equity_value_basis", row["equity_value_basis"], None)
            _check(failures, "legacy implied_equity_value", row["implied_equity_value"], None)
            _check(failures, "legacy enterprise_value", row["enterprise_value"], None)
            # round_size stays null — the guard refuses, it does not reclassify. Moving
            # the amount is source-supported remediation, not a derivation.
            _check(failures, "legacy round_size not invented", row["round_size"], None)
            _check(failures, "legacy transaction_size", row["transaction_size"], None)
            _check(failures, "legacy transaction_size_basis", row["transaction_size_basis"], None)
            # investment_amount means ONE investor's check. A generic funding amount
            # is not that, so it must not land here either — the field is expected to
            # be null for most deals, and a populated value asserts a party-level fact
            # no source stated.
            _check(failures, "legacy investment_amount cleared", row["investment_amount"], None)

        # A correctly extracted round does NOT put its round size in investment_amount
        # either: the round total is not any single investor's check.
        clean = conn.execute(
            "SELECT investment_amount FROM transaction_record WHERE transaction_id = ?",
            (CLEAN_TXN,),
        ).fetchone()
        if clean is not None:
            _check(failures, "round_size must not populate investment_amount",
                   clean["investment_amount"], None)

        # The raw fact survives in the ledger regardless of what Stage 9 refuses.
        kept = conn.execute(
            "SELECT COUNT(*) FROM transaction_field_observation "
            "WHERE transaction_id = ? AND field_name = 'value_amount'", (LEGACY_TXN,)
        ).fetchone()[0]
        if kept < 1:
            failures.append("legacy value_amount observation was not retained")

        # A correctly extracted round is unaffected by the gate.
        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (CLEAN_TXN,)
        ).fetchone()
        if row is None:
            failures.append("clean funding fixture produced no transaction_record")
        else:
            _check(failures, "clean round_size", row["round_size"], 80_000_000.0)
            _check(failures, "clean transaction_size", row["transaction_size"], 80_000_000.0)
            _check(failures, "clean transaction_size_basis",
                   row["transaction_size_basis"], "ROUND_SIZE")
            _check(failures, "clean transaction_value stays null", row["transaction_value"], None)

        conn.close()


def main() -> None:
    failures: list[str] = []
    _check_unit(failures)
    _check_end_to_end(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS funding family gate: funding events derive no transaction_value or equity_value")


if __name__ == "__main__":
    main()
