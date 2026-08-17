#!/usr/bin/env python3
"""Regression guard for the `transaction_size` waterfall.

No network and no model calls.

`transaction_size` is the one magnitude that spans transaction families, so a reviewer
can rank deals without picking whichever number looks largest. It is **derived**, never
extracted, and every populated value carries a `transaction_size_basis` naming the rung
that produced it.

The waterfall is keyed on **event family**, and the branches are disjoint — a funding
round never falls through to a purchase price, and an M&A deal never falls through to a
round size. Ordering only has meaning *within* a family.

| Family     | Rungs                                            |
|------------|--------------------------------------------------|
| M&A        | `transaction_value` -> `TRANSACTION_VALUE`       |
| Funding    | `round_size` -> `ROUND_SIZE`                     |
| Spin/Split | reserved, no live rung                           |
| Other      | null                                             |

**Three rungs are deliberately absent**, and this file pins each of them:

- **No equity rung.** Every case where a stake-level equity figure can safely stand for
  the magnitude already produces `transaction_value`. The only states where
  `transaction_value` is null while `equity_value` is known are those where
  `pct_acquired` is unknown — i.e. the transaction scope is unknown — so an equity
  figure there could be the whole company.
- **No EV rung.** Below control an enterprise value is the grossed-up whole-company
  figure; it would report a 27%-for-$600M deal as $2.22B.
- **No live `SOLE_INVESTOR_AMOUNT` rung.** The vocabulary reserves it, but
  `transaction_participant` has no per-investor amount column, so there is nothing to
  read. Deriving it from `transaction_record.investment_amount` would be actively
  wrong: that field is transaction-level and falls back to the legacy value slot, so it
  is not a single investor's check.
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
from scripts.export_review_xlsx import COLUMNS, _review_transaction_size


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


# --------------------------------------------------------------------------
# A. The waterfall itself
# --------------------------------------------------------------------------
def _check_waterfall(failures: list[str]) -> None:
    d = aggregate._derive_transaction_size

    # --- M&A: transaction_value, whatever basis produced it -----------------
    for event in ("ACQUISITION", "MERGER", "REVERSE_MERGER"):
        size, basis = d({"v2_event_type": event}, 450_000_000.0)
        _check(failures, f"{event} size", size, 450_000_000.0)
        _check(failures, f"{event} basis", basis, "TRANSACTION_VALUE")

    # A below-control M&A still stamps TRANSACTION_VALUE. EQUITY_BELOW_CONTROL is a
    # transaction_value_basis value and does not propagate here: this enum names the
    # source field that supplied the magnitude, not the derivation that filled it.
    size, basis = d({"v2_event_type": "ACQUISITION"}, 600_000_000.0)
    _check(failures, "below-control M&A size", size, 600_000_000.0)
    _check(failures, "below-control M&A basis", basis, "TRANSACTION_VALUE")

    # No transaction value -> null. There is no equity fallback.
    size, basis = d({"v2_event_type": "ACQUISITION"}, None)
    _check(failures, "M&A without transaction_value size", size, None)
    _check(failures, "M&A without transaction_value basis", basis, None)

    # --- Funding: round_size only -------------------------------------------
    for event in ("VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"):
        size, basis = d({"v2_event_type": event, "round_size": 200_000_000.0}, None)
        _check(failures, f"{event} size", size, 200_000_000.0)
        _check(failures, f"{event} basis", basis, "ROUND_SIZE")

    # A funding round never falls through to a purchase price, even if one is present.
    size, basis = d(
        {"v2_event_type": "VC_ROUND", "round_size": 200_000_000.0}, 999_000_000.0
    )
    _check(failures, "funding does not consume transaction_value", size, 200_000_000.0)

    # No round size -> null. SOLE_INVESTOR_AMOUNT is reserved, not live, and a
    # multi-investor round is never summed.
    size, basis = d({"v2_event_type": "VC_ROUND"}, None)
    _check(failures, "funding without round_size size", size, None)
    _check(failures, "funding without round_size basis", basis, None)

    # The post-money trap: a valuation must never become an as-transacted magnitude.
    size, basis = d(
        {"v2_event_type": "VC_ROUND", "post_money_valuation": 1_000_000_000.0}, None
    )
    _check(failures, "post-money must not become transaction_size", size, None)

    # --- Spin/Split: reserved ------------------------------------------------
    for event in ("SPIN_OFF", "SPLIT_OFF"):
        size, basis = d({"v2_event_type": event}, 300_000_000.0)
        _check(failures, f"{event} size (reserved, not live)", size, None)
        _check(failures, f"{event} basis (reserved, not live)", basis, None)

    # --- Other families: null ------------------------------------------------
    for event in ("JOINT_VENTURE", "RECAPITALIZATION", "UNKNOWN"):
        size, basis = d({"v2_event_type": event}, 300_000_000.0)
        _check(failures, f"{event} size", size, None)
        _check(failures, f"{event} basis", basis, None)

    # --- The reserved vocabulary is declared but carries no live rung --------
    reserved = getattr(aggregate, "TRANSACTION_SIZE_BASES", None)
    if reserved is None:
        failures.append("TRANSACTION_SIZE_BASES vocabulary is not declared")
    else:
        for expected in (
            "TRANSACTION_VALUE", "ROUND_SIZE",
            "SOLE_INVESTOR_AMOUNT", "SPIN_SPLIT_CONSIDERATION_VALUE",
        ):
            if expected not in reserved:
                failures.append(f"TRANSACTION_SIZE_BASES missing {expected!r}")
        for forbidden in ("EQUITY_VALUE", "EQUITY_CONSIDERATION",
                          "ENTERPRISE_VALUE", "IMPLIED_ENTERPRISE_VALUE",
                          "EQUITY_BELOW_CONTROL"):
            if forbidden in reserved:
                failures.append(
                    f"TRANSACTION_SIZE_BASES must not contain {forbidden!r} — "
                    "no equity rung, no EV rung, and EQUITY_BELOW_CONTROL belongs "
                    "only to transaction_value_basis"
                )


# --------------------------------------------------------------------------
# B. The export carries one waterfall, not two
# --------------------------------------------------------------------------
def _check_export(failures: list[str]) -> None:
    _check(failures, "review XLSX column count", len(COLUMNS), 67)
    for column in ("transaction_size", "transaction_size_basis"):
        if column not in COLUMNS:
            failures.append(f"review XLSX lost the {column} column")

    cols = {"transaction_size", "transaction_size_basis"}

    # Canonical value present -> surfaced with its basis.
    value, basis = _review_transaction_size(
        {"transaction_size": 450_000_000.0, "transaction_size_basis": "TRANSACTION_VALUE"},
        cols,
    )
    _check(failures, "export surfaces canonical size", value, 450_000_000.0)
    _check(failures, "export surfaces canonical basis", basis, "TRANSACTION_VALUE")

    # Canonical null -> blank, even though the retired shadow waterfall would have
    # produced a figure from transaction_value or round_size. Accepting the canonical
    # null is the point: two independent waterfalls would drift.
    value, basis = _review_transaction_size(
        {
            "transaction_size": None, "transaction_size_basis": None,
            "transaction_value": 999_000_000.0, "round_size": 888_000_000.0,
            "v2_event_type": "VC_ROUND",
        },
        cols,
    )
    _check(failures, "export does not re-derive size", value, "")
    _check(failures, "export does not re-derive basis", basis, "")


# --------------------------------------------------------------------------
# C. End to end, including the basis invariant
# --------------------------------------------------------------------------
def _insert(
    conn: sqlite3.Connection, txn_id: str, *, event: str,
    value_amount: float | None, value_type: str | None,
    pct: float | None, round_size: float | None = None,
) -> None:
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', ?, 'Deal announcement', '2026-08-13', ?,
            ?, 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (f"https://example.test/{txn_id}", f"Announcement for {txn_id}.", f"size-{txn_id}"),
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
            round_size, financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', ?, ?, 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            ?, 'Meridian Holdings', 'strategic_corporate', 'strategic_corporate',
            ?, '2026-08-12', 'exact',
            ?, 'USD', ?, 'HIGH',
            ?, 'DISCLOSED', 'HIGH', '0.7', '0.18', ?
        )
        """,
        (source_raw_id, event, event, f"Target for {txn_id}", pct,
         value_amount, value_type, round_size, txn_id),
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
    cases = [
        # (txn_id, event, amount, type, pct, round_size, expected size, expected basis)
        ("tc_size_ma_stated", "ACQUISITION", 450_000_000, "TRANSACTION_VALUE", 100.0,
         None, 450_000_000.0, "TRANSACTION_VALUE"),
        # Below control: TV derives to the equity figure and the size follows it.
        ("tc_size_ma_minority", "ACQUISITION", 600_000_000, "EQUITY_VALUE", 27.0,
         None, 600_000_000.0, "TRANSACTION_VALUE"),
        # Stated EV only, no TV. No EV rung, so null despite a large known figure.
        ("tc_size_ma_ev_only", "ACQUISITION", 2_220_000_000, "ENTERPRISE_VALUE", 27.0,
         None, None, None),
        # Undisclosed M&A.
        ("tc_size_ma_none", "ACQUISITION", None, None, 100.0, None, None, None),
        # Funding with a round size.
        ("tc_size_funding_round", "VC_ROUND", None, None, None,
         200_000_000, 200_000_000.0, "ROUND_SIZE"),
        # Funding without one — the sole-investor rung is reserved, not live.
        ("tc_size_funding_noround", "VC_ROUND", None, None, None, None, None, None),
        # Spin-off: reserved family, no live rung.
        ("tc_size_spin", "SPIN_OFF", 300_000_000, "TRANSACTION_VALUE", None,
         None, None, None),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "transaction_size.db")
        init_db(db_path)
        conn = get_connection(db_path)

        for txn_id, event, amount, vtype, pct, round_size, _, _ in cases:
            _insert(conn, txn_id, event=event, value_amount=amount,
                    value_type=vtype, pct=pct, round_size=round_size)

        original = aggregate._call_agg_prompt

        def _no_conflict(field_name, *_a, **_kw):
            raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")

        aggregate._call_agg_prompt = _no_conflict
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
            aggregate.run(conn, cfg, "transaction_size")
        finally:
            aggregate._call_agg_prompt = original

        for txn_id, _e, _a, _t, _p, _r, expected_size, expected_basis in cases:
            row = conn.execute(
                "SELECT transaction_size, transaction_size_basis "
                "FROM transaction_record WHERE transaction_id = ?", (txn_id,)
            ).fetchone()
            if row is None:
                failures.append(f"{txn_id}: no transaction_record")
                continue
            _check(failures, f"{txn_id} size", row["transaction_size"], expected_size)
            _check(failures, f"{txn_id} basis", row["transaction_size_basis"], expected_basis)

        # The invariant, checked as a set rather than case by case: basis is NOT NULL
        # wherever size is populated, and never populated without one.
        orphan_size = conn.execute(
            "SELECT COUNT(*) FROM transaction_record "
            "WHERE transaction_size IS NOT NULL AND transaction_size_basis IS NULL"
        ).fetchone()[0]
        _check(failures, "sizes without a basis", orphan_size, 0)
        orphan_basis = conn.execute(
            "SELECT COUNT(*) FROM transaction_record "
            "WHERE transaction_size_basis IS NOT NULL AND transaction_size IS NULL"
        ).fetchone()[0]
        _check(failures, "bases without a size", orphan_basis, 0)

        conn.close()


def main() -> None:
    failures: list[str] = []
    _check_waterfall(failures)
    _check_export(failures)
    _check_end_to_end(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS transaction_size: family-keyed waterfall, basis always stamped, one waterfall only")


if __name__ == "__main__":
    main()
