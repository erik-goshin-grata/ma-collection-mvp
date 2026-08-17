#!/usr/bin/env python3
"""Regression guard for total_debt / Cash_ST extraction and the debt-inclusive bases.

No network and no model calls.

Semantics fixed by decision (2026-08-17):

- `total_debt` and `Cash_ST` are point-in-time balance-sheet items, recorded as
  `POINT_IN_TIME` plus an exact `balance_sheet_as_of_date`. There is no LTM/TTM/NTM
  concept for them, and no annual/quarterly field — filing frequency is filing
  context, not the economic period of the amount.
- A *derived* `net_debt` requires both components to share one
  `balance_sheet_as_of_date`. Reported/manual `net_debt` is still preferred.
- Arithmetic that mixes consideration with debt or cash — the calculated implied-EV
  bases and `transaction_value`'s `EQUITY_PLUS_TOTAL_DEBT` — requires the relevant
  currencies to be **known and equal**. Unknown on either side does not calculate;
  known-and-differing does not calculate. No FX conversion anywhere.
- A source-stated enterprise value is a single figure rather than a sum, so the
  currency guard does not touch it.
- No announced-date tolerance, and no requirement that the balance-sheet date match
  the revenue/EBITDA denominator period. Both are deliberately unenforced for now;
  the as-of date is preserved so corpus behaviour can be evaluated later.

Qualifiers stay anchored to the source of their own amount, so debt and cash pulled
from different sources cannot silently share one currency or one as-of date.
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
from lib.observation_writer import HC_FIELDS, write_staging_observations_for_extraction

TXN_ID = "tc_debt_cash_fixture"
ANNOUNCED = "2026-08-12"
AS_OF = "2025-12-31"


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


# ---------------------------------------------------------------------------
# Unit level — net debt coherence
# ---------------------------------------------------------------------------

def _scenario_net_debt_coherence(failures: list[str]) -> None:
    p = "net-debt"
    d = aggregate._derive_net_debt

    # Reported/manual net debt stays preferred over the components.
    _check(
        failures, f"{p} reported preferred",
        d(900_000_000.0, "USD", 1_200_000_000.0, "USD", AS_OF, 200_000_000.0, "USD", AS_OF),
        (900_000_000.0, "USD", None, "REPORTED"),
    )

    # Components agreeing on currency and as-of date calculate.
    _check(
        failures, f"{p} components calculate",
        d(None, None, 1_200_000_000.0, "USD", AS_OF, 200_000_000.0, "USD", AS_OF),
        (1_000_000_000.0, "USD", AS_OF, "CALCULATED_TOTAL_DEBT_MINUS_CASH_ST"),
    )

    # Different balance-sheet dates are not a balance sheet.
    _check(
        failures, f"{p} as-of mismatch",
        d(None, None, 1_200_000_000.0, "USD", AS_OF, 200_000_000.0, "USD", "2025-06-30"),
        (None, None, None, None),
    )

    # Currencies must be known and equal.
    _check(
        failures, f"{p} currency mismatch",
        d(None, None, 1_200_000_000.0, "USD", AS_OF, 200_000_000.0, "EUR", AS_OF),
        (None, None, None, None),
    )
    _check(
        failures, f"{p} currency unknown on cash",
        d(None, None, 1_200_000_000.0, "USD", AS_OF, 200_000_000.0, None, AS_OF),
        (None, None, None, None),
    )
    _check(
        failures, f"{p} currency unknown on debt",
        d(None, None, 1_200_000_000.0, None, AS_OF, 200_000_000.0, "USD", AS_OF),
        (None, None, None, None),
    )

    # An unknown as-of date is insufficient evidence, not an implied match.
    _check(
        failures, f"{p} as-of unknown",
        d(None, None, 1_200_000_000.0, "USD", None, 200_000_000.0, "USD", None),
        (None, None, None, None),
    )

    # One component alone yields nothing; never treat the other as zero.
    _check(
        failures, f"{p} debt only",
        d(None, None, 1_200_000_000.0, "USD", AS_OF, None, None, None),
        (None, None, None, None),
    )


# ---------------------------------------------------------------------------
# Unit level — currency guards on the debt-inclusive bases
# ---------------------------------------------------------------------------

def _scenario_implied_ev_currency(failures: list[str]) -> None:
    p = "implied-ev"
    d = aggregate._derive_implied_enterprise_value

    _check(
        failures, f"{p} same currency calculates",
        d(None, None, 200_000_000.0, 50_000_000.0,
          implied_equity_currency="USD", net_debt_currency="USD"),
        (250_000_000.0, "IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT"),
    )
    _check(
        failures, f"{p} differing currency refuses",
        d(None, None, 200_000_000.0, 50_000_000.0,
          implied_equity_currency="USD", net_debt_currency="JPY"),
        (None, None),
    )
    _check(
        failures, f"{p} unknown net-debt currency refuses",
        d(None, None, 200_000_000.0, 50_000_000.0,
          implied_equity_currency="USD", net_debt_currency=None),
        (None, None),
    )
    _check(
        failures, f"{p} unknown deal currency refuses",
        d(None, None, 200_000_000.0, 50_000_000.0,
          implied_equity_currency=None, net_debt_currency="USD"),
        (None, None),
    )
    # A stated whole-company EV is one figure, not a sum.
    _check(
        failures, f"{p} stated EV unaffected",
        d(750_000_000.0, "ENTERPRISE_VALUE", None, None,
          implied_equity_currency=None, net_debt_currency="JPY"),
        (750_000_000.0, "STATED"),
    )


def _scenario_transaction_value_currency(failures: list[str]) -> None:
    p = "txn-value"
    d = aggregate._derive_transaction_value

    _check(
        failures, f"{p} same currency adds debt",
        d({}, 200.0, 50.0, 100.0, equity_currency="USD", total_debt_currency="USD"),
        (250.0, "EQUITY_PLUS_TOTAL_DEBT"),
    )
    # The debt basis is refused, but the known equity consideration is not thrown
    # away — it falls to EQUITY_VALUE_ONLY, which never implied debt was zero.
    _check(
        failures, f"{p} differing currency falls back",
        d({}, 200.0, 50.0, 100.0, equity_currency="USD", total_debt_currency="EUR"),
        (200.0, "EQUITY_VALUE_ONLY"),
    )
    _check(
        failures, f"{p} unknown debt currency falls back",
        d({}, 200.0, 50.0, 100.0, equity_currency="USD", total_debt_currency=None),
        (200.0, "EQUITY_VALUE_ONLY"),
    )
    # Below control, debt never entered the calculation to begin with.
    _check(
        failures, f"{p} below control unchanged",
        d({}, 200.0, 50.0, 25.0, equity_currency="USD", total_debt_currency="USD"),
        (200.0, "EQUITY_BELOW_CONTROL"),
    )


# ---------------------------------------------------------------------------
# Extraction path — schema, prompt, observation coverage
# ---------------------------------------------------------------------------

def _scenario_extraction_surface(failures: list[str]) -> None:
    p = "extraction-surface"
    new_fields = (
        "total_debt", "total_debt_currency",
        "cash_st", "cash_st_currency",
        "balance_sheet_as_of_date",
    )
    # Derived deterministically by aggregation rather than extracted, so it lives on
    # transaction_record only and is deliberately absent from _FIELDS/HC_FIELDS: a
    # constant the model never writes is a constant the model cannot mislabel.
    derived_only_fields = ("balance_sheet_period_type",)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "surface.db")
        init_db(db_path)
        conn = get_connection(db_path)
        for table in ("staging_extraction", "transaction_record"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for field in new_fields:
                if field not in cols:
                    failures.append(f"{p}: {table} missing column {field}")
        tr_cols = {row[1] for row in conn.execute("PRAGMA table_info(transaction_record)")}
        for field in derived_only_fields:
            if field not in tr_cols:
                failures.append(f"{p}: transaction_record missing column {field}")
            if field in aggregate._FIELD_TYPE:
                failures.append(f"{p}: {field} is derived, it must not be an extracted _FIELDS entry")
        conn.close()

    # Aggregation must read them, and the observation writer must cover every field
    # aggregation reads (decision "Observation Write Path Must Cover Every Field
    # Aggregation Reads") — otherwise the observation default silently loses them.
    for field in new_fields:
        if field not in aggregate._FIELD_TYPE:
            failures.append(f"{p}: aggregation _FIELDS missing {field}")
        if field not in HC_FIELDS:
            failures.append(f"{p}: observation HC_FIELDS missing {field}")

    # The prompt's blanket refusal to extract debt/cash has to be gone.
    prompt_text = Path("prompts/high_confidence_extraction.md").read_text(encoding="utf-8")
    if "this prompt does not extract total_debt" in prompt_text:
        failures.append(f"{p}: HC prompt still carries the blanket debt/cash extraction guard")
    for token in ("total_debt", "cash_st", "balance_sheet_as_of_date"):
        if token not in prompt_text:
            failures.append(f"{p}: HC prompt does not mention {token}")
    # Decision 3 — prefer a source-stated USD figure, never a self-made conversion.
    if "stated USD" not in prompt_text and "stated in USD" not in prompt_text:
        failures.append(f"{p}: HC prompt lacks the source-stated-USD preference")
    if "POINT_IN_TIME" not in prompt_text:
        failures.append(f"{p}: HC prompt does not name POINT_IN_TIME as the balance-sheet period type")


# ---------------------------------------------------------------------------
# End to end through aggregation
# ---------------------------------------------------------------------------

def _insert_source(
    conn: sqlite3.Connection,
    *,
    slug: str,
    tier: str,
    value_amount: float | None = None,
    value_type: str | None = None,
    value_currency: str | None = None,
    pct_acquired: float | None = None,
    total_debt: float | None = None,
    total_debt_currency: str | None = None,
    cash_st: float | None = None,
    cash_st_currency: str | None = None,
    balance_sheet_as_of_date: str | None = None,
) -> None:
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', ?, ?, 'Acquirer to acquire Target', '2026-08-13',
            'Acquirer Inc. agreed to acquire Target LLC.', ?, 'RELEVANT',
            '2026-08-14T00:00:00Z'
        )
        """,
        (tier, f"https://example.test/{slug}", f"debt-cash-fixture-{slug}"),
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
            total_debt, total_debt_currency, cash_st, cash_st_currency,
            balance_sheet_as_of_date,
            financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'Target LLC', 'Acquirer Inc.', 'strategic_corporate', 'strategic_corporate',
            ?, ?, 'exact',
            ?, ?, ?, 'HIGH',
            ?, ?, ?, ?,
            ?,
            'DISCLOSED', 'HIGH', '0.7', '0.15', ?
        )
        """,
        (
            source_raw_id, pct_acquired, ANNOUNCED,
            value_amount, value_currency, value_type,
            total_debt, total_debt_currency, cash_st, cash_st_currency,
            balance_sheet_as_of_date, TXN_ID,
        ),
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


def _aggregate(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    def _no_conflict(field_name, *_a, **_kw):
        raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")

    original = aggregate._call_agg_prompt
    aggregate._call_agg_prompt = _no_conflict
    try:
        cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
        aggregate.run(conn, cfg, label)
    finally:
        aggregate._call_agg_prompt = original
    return conn.execute(
        "SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)
    ).fetchone()


def _scenario_end_to_end_single_source(failures: list[str]) -> None:
    """One source states equity, debt, cash, currency and the balance-sheet date."""
    p = "e2e-single"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "e2e_single.db")
        init_db(db_path)
        conn = get_connection(db_path)
        _insert_source(
            conn, slug="filing", tier="T1",
            value_amount=200_000_000, value_type="EQUITY_VALUE", value_currency="USD",
            pct_acquired=100.0,
            total_debt=60_000_000, total_debt_currency="USD",
            cash_st=10_000_000, cash_st_currency="USD",
            balance_sheet_as_of_date=AS_OF,
        )
        row = _aggregate(conn, "e2e_single_test")
        if row is None:
            failures.append(f"{p}: no transaction_record produced")
            conn.close()
            return

        _check(failures, f"{p} total_debt", row["total_debt"], 60_000_000.0)
        _check(failures, f"{p} cash_st", row["cash_st"], 10_000_000.0)
        _check(failures, f"{p} total_debt_currency", row["total_debt_currency"], "USD")
        _check(failures, f"{p} balance_sheet_as_of_date", row["balance_sheet_as_of_date"], AS_OF)
        # The economic period type of a balance-sheet amount, recorded explicitly so
        # it can never be read as, or mistaken for, a trailing/forward period.
        _check(
            failures, f"{p} balance_sheet_period_type",
            row["balance_sheet_period_type"], "POINT_IN_TIME",
        )
        # net_debt = 60 - 10, both USD, one as-of date.
        _check(failures, f"{p} net_debt", row["net_debt"], 50_000_000.0)
        # transaction_value = equity + total debt at control.
        _check(failures, f"{p} transaction_value", row["transaction_value"], 260_000_000.0)
        _check(failures, f"{p} transaction_value_basis", row["transaction_value_basis"], "EQUITY_PLUS_TOTAL_DEBT")
        # implied EV = implied equity (200M at 100%) + net debt.
        _check(failures, f"{p} implied_enterprise_value", row["implied_enterprise_value"], 250_000_000.0)
        _check(
            failures, f"{p} implied_enterprise_value_basis",
            row["implied_enterprise_value_basis"], "IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT",
        )
        conn.close()


def _scenario_end_to_end_cross_source(failures: list[str]) -> None:
    """Debt and cash from different sources with different balance-sheet dates.

    Neither source states a coherent pair, so no net debt may be derived — and the
    as-of dates must not be interchanged to manufacture one.
    """
    p = "e2e-cross"
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "e2e_cross.db")
        init_db(db_path)
        conn = get_connection(db_path)
        _insert_source(
            conn, slug="filing", tier="T1",
            value_amount=200_000_000, value_type="EQUITY_VALUE", value_currency="USD",
            pct_acquired=100.0,
            total_debt=60_000_000, total_debt_currency="USD",
            balance_sheet_as_of_date=AS_OF,
        )
        _insert_source(
            conn, slug="press", tier="T2",
            cash_st=10_000_000, cash_st_currency="USD",
            balance_sheet_as_of_date="2025-06-30",
        )
        row = _aggregate(conn, "e2e_cross_test")
        if row is None:
            failures.append(f"{p}: no transaction_record produced")
            conn.close()
            return

        _check(failures, f"{p} net_debt", row["net_debt"], None)
        _check(failures, f"{p} implied_enterprise_value", row["implied_enterprise_value"], None)
        # A balance-sheet amount is present, so its period type is still recorded;
        # only the shared as-of date is null, because the two dates disagree.
        _check(
            failures, f"{p} balance_sheet_period_type",
            row["balance_sheet_period_type"], "POINT_IN_TIME",
        )
        _check(failures, f"{p} balance_sheet_as_of_date", row["balance_sheet_as_of_date"], None)
        # total_debt is coherent on its own, so the debt-inclusive TV still holds.
        _check(failures, f"{p} transaction_value", row["transaction_value"], 260_000_000.0)
        _check(
            failures, f"{p} transaction_value_basis",
            row["transaction_value_basis"], "EQUITY_PLUS_TOTAL_DEBT",
        )
        conn.close()


def main() -> None:
    failures: list[str] = []
    _scenario_net_debt_coherence(failures)
    _scenario_implied_ev_currency(failures)
    _scenario_transaction_value_currency(failures)
    _scenario_extraction_surface(failures)
    _scenario_end_to_end_single_source(failures)
    _scenario_end_to_end_cross_source(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS debt/cash extraction: coherent net debt, currency-guarded debt bases")


if __name__ == "__main__":
    main()
