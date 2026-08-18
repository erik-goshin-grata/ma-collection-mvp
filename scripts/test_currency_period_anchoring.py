#!/usr/bin/env python3
"""Regression guard for currency and period anchoring (spec §2.10 items 1-2).

No network and no model calls.

A financial amount is only interpretable together with the currency it is stated
in and the period it covers. Aggregation resolves each canonical field
independently, so `target_revenue` can be selected from one source while
`target_revenue_period_end` and `financials_currency` are selected from another.
The result is an amount silently re-labelled with a qualifier its own source never
stated — and because the annual-as-trailing rule keys off `period_end`, a borrowed
period can decide whether a multiple is computed at all.

This is the same defect class as the typed-value collapse: a per-fact qualifier
resolved independently of the fact it qualifies. The rule asserted here is that a
qualifier travels with its amount, and that an unstated qualifier resolves to null
rather than borrowing a neighbour's. Null is the honest answer and matches the
established "null is itself the queryable signal" posture used by
`deal_value_currency`.

Scenarios are built so every contested field resolves deterministically by source
tier, with no LLM conflict path involved; the stub below turns an unexpected
conflict into a loud failure rather than a network call.

The cross-currency guard on implied enterprise value was originally asserted here.
It moved to `scripts/test_debt_cash_extraction.py`, which owns the debt/cash
arithmetic semantics, so the rule has exactly one home.
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

TXN_ID = "tc_anchoring_fixture"
ANNOUNCED = "2026-08-12"


def _no_conflict_expected(field_name, *_args, **_kwargs):
    raise AssertionError(
        f"unexpected aggregation conflict for {field_name!r} — the fixture is meant to "
        "resolve every field deterministically by tier"
    )


def _insert_source(
    conn: sqlite3.Connection,
    *,
    slug: str,
    tier: str,
    revenue: float | None = None,
    revenue_period_type: str | None = None,
    revenue_period_end: str | None = None,
    financials_currency: str | None = None,
    value_amount: float | None = None,
    value_type: str | None = None,
    value_currency: str | None = None,
) -> int:
    """Insert one source + its CLUSTERED extraction into the shared cluster."""
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', ?, ?, 'Acquirer to acquire Target', '2026-08-13',
            'Acquirer Inc. agreed to acquire Target LLC.',
            ?, 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (tier, f"https://example.test/{slug}", f"anchoring-fixture-{slug}"),
    )
    source_raw_id = int(cur.lastrowid)

    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            target_revenue, target_revenue_period_type, target_revenue_period_type_v2,
            target_revenue_period_end, financials_currency, financials_disclosure_status,
            model_confidence, dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'Target LLC', 'Acquirer Inc.', 'strategic_corporate', 'strategic_corporate',
            100.0, ?, 'exact',
            ?, ?, ?, 'HIGH',
            ?, ?, ?,
            ?, ?, 'DISCLOSED',
            'HIGH', '0.7', '0.15', ?
        )
        """,
        (
            source_raw_id, ANNOUNCED,
            value_amount, value_currency, value_type,
            revenue, revenue_period_type, revenue_period_type,
            revenue_period_end, financials_currency,
            TXN_ID,
        ),
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


def _run_aggregation(
    conn: sqlite3.Connection, label: str, read_source: str = "observation"
) -> sqlite3.Row | None:
    original = aggregate._call_agg_prompt
    aggregate._call_agg_prompt = _no_conflict_expected
    try:
        cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source=read_source)
        aggregate.run(conn, cfg, label)
    finally:
        aggregate._call_agg_prompt = original
    return conn.execute(
        "SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)
    ).fetchone()


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _scenario_period_anchor(failures: list[str]) -> None:
    """The higher-tier source states the amount but no period end.

    T1 supplies revenue with no period_end; T2 supplies a different revenue and a
    recent period_end. Each field wins on its own, so the T1 amount inherits the
    T2 period end — and that borrowed date is recent enough to make the
    annual-as-trailing rule fire, producing a multiple for an amount whose own
    source never dated it.
    """
    prefix = "period-anchor"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "anchor_period.db")
        init_db(db_path)
        conn = get_connection(db_path)

        _insert_source(
            conn, slug="sec-filing", tier="T1",
            revenue=100_000_000, revenue_period_type="ANNUAL", revenue_period_end=None,
            financials_currency="USD",
            value_amount=500_000_000, value_type="ENTERPRISE_VALUE", value_currency="USD",
        )
        _insert_source(
            conn, slug="press", tier="T2",
            revenue=120_000_000, revenue_period_type="ANNUAL",
            revenue_period_end="2025-12-31",
            financials_currency="USD",
        )

        row = _run_aggregation(conn, "anchor_period_test")
        if row is None:
            failures.append(f"{prefix}: no transaction_record produced")
            conn.close()
            return

        # The T1 amount wins, as it should.
        _check(failures, f"{prefix} target_revenue", row["target_revenue"], 100_000_000.0)
        # Its period end must NOT be borrowed from the T2 source.
        _check(failures, f"{prefix} target_revenue_period_end", row["target_revenue_period_end"], None)
        # With no period end of its own, an ANNUAL actual is not trailing-eligible,
        # so no multiple may be struck.
        _check(failures, f"{prefix} ev_to_revenue_ltm", row["ev_to_revenue_ltm"], None)
        conn.close()


def _scenario_currency_anchor(failures: list[str]) -> None:
    """The higher-tier source states the amount but no financials currency.

    A borrowed currency is worse than no currency: it silently re-denominates the
    amount and defeats the cross-currency guard in _compute_multiples, which
    compares value_currency against financials_currency.
    """
    prefix = "currency-anchor"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "anchor_currency.db")
        init_db(db_path)
        conn = get_connection(db_path)

        _insert_source(
            conn, slug="sec-filing", tier="T1",
            revenue=100_000_000, revenue_period_type="ANNUAL",
            revenue_period_end="2025-12-31", financials_currency=None,
            value_amount=500_000_000, value_type="ENTERPRISE_VALUE", value_currency="USD",
        )
        _insert_source(
            conn, slug="press", tier="T2",
            revenue=120_000_000, revenue_period_type="ANNUAL",
            revenue_period_end="2025-12-31", financials_currency="JPY",
        )

        row = _run_aggregation(conn, "anchor_currency_test")
        if row is None:
            failures.append(f"{prefix}: no transaction_record produced")
            conn.close()
            return

        _check(failures, f"{prefix} target_revenue", row["target_revenue"], 100_000_000.0)
        _check(failures, f"{prefix} financials_currency", row["financials_currency"], None)
        conn.close()


def _scenario_qualifiers_travel_together(failures: list[str]) -> None:
    """Control case: when one source supplies everything, nothing changes.

    Anchoring must not disturb the ordinary single-source path, including the
    annual-as-trailing multiple that a correctly dated ANNUAL actual earns.
    """
    prefix = "single-source"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "anchor_single.db")
        init_db(db_path)
        conn = get_connection(db_path)

        _insert_source(
            conn, slug="sec-filing", tier="T1",
            revenue=100_000_000, revenue_period_type="ANNUAL",
            revenue_period_end="2025-12-31", financials_currency="USD",
            value_amount=500_000_000, value_type="ENTERPRISE_VALUE", value_currency="USD",
        )

        row = _run_aggregation(conn, "anchor_single_test")
        if row is None:
            failures.append(f"{prefix}: no transaction_record produced")
            conn.close()
            return

        _check(failures, f"{prefix} target_revenue", row["target_revenue"], 100_000_000.0)
        _check(failures, f"{prefix} target_revenue_period_end", row["target_revenue_period_end"], "2025-12-31")
        _check(failures, f"{prefix} financials_currency", row["financials_currency"], "USD")
        _check(failures, f"{prefix} implied_enterprise_value", row["implied_enterprise_value"], 500_000_000.0)
        _check(failures, f"{prefix} ev_to_revenue_ltm", row["ev_to_revenue_ltm"], 5.0)
        conn.close()


def _scenario_staging_compatibility(failures: list[str]) -> None:
    """The staging read path stays usable with anchoring in place.

    Staging keys observations per extraction, so a metric's qualifiers already come
    from the same row as its amount and anchoring is a no-op there. Asserted rather
    than assumed, because staging is the documented rollback/debug path and must not
    be collateral damage of a fix aimed at the observation read.
    """
    prefix = "staging-compat"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "anchor_staging.db")
        init_db(db_path)
        conn = get_connection(db_path)

        _insert_source(
            conn, slug="sec-filing", tier="T1",
            revenue=100_000_000, revenue_period_type="ANNUAL",
            revenue_period_end="2025-12-31", financials_currency="USD",
            value_amount=500_000_000, value_type="ENTERPRISE_VALUE", value_currency="USD",
        )

        row = _run_aggregation(conn, "anchor_staging_test", read_source="staging")
        if row is None:
            failures.append(f"{prefix}: no transaction_record produced")
            conn.close()
            return

        _check(failures, f"{prefix} target_revenue", row["target_revenue"], 100_000_000.0)
        _check(failures, f"{prefix} target_revenue_period_end", row["target_revenue_period_end"], "2025-12-31")
        _check(failures, f"{prefix} financials_currency", row["financials_currency"], "USD")
        _check(failures, f"{prefix} ev_to_revenue_ltm", row["ev_to_revenue_ltm"], 5.0)
        conn.close()


def main() -> None:
    failures: list[str] = []
    _scenario_period_anchor(failures)
    _scenario_currency_anchor(failures)
    _scenario_qualifiers_travel_together(failures)
    _scenario_staging_compatibility(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS currency/period anchoring: qualifiers travel with their amount")


if __name__ == "__main__":
    main()
