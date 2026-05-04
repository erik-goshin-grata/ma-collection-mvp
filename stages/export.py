"""
Stage 14: export

Exports all transaction_record rows where is_current = 1, joined with their
current summary and rationale_tag rows, to a CSV file at
exports/transactions_<run_id>.csv. Rows are sorted by announced_date DESC.

consideration_components and secondary_rationales are serialized as JSON
strings in the CSV (not exploded). Columns follow the operator-approved order.

Spec references: specs/pipeline.md §2 (Stage 14)
"""

from __future__ import annotations

import csv
import os
import sqlite3

from config import Config
from logger import get_logger

_COLUMNS = [
    "transaction_id",
    "deal_type", "event_type", "transaction_status", "spin_split_type", "distribution_mechanism",
    "target_name", "target_domain", "target_ticker", "target_type", "pct_acquired", "target_status", "target_description",
    "acquirer_name", "acquirer_domain", "acquirer_ticker", "acquirer_type", "acquirer_description", "acquirer_sponsor_name",
    "parent_seller_name", "parent_seller_ticker", "parent_seller_description",
    "announced_date", "signing_date", "closed_date",
    "value_amount", "value_currency", "value_type", "per_share_price",
    "target_revenue", "target_revenue_period_type", "target_revenue_period_end",
    "target_ebitda", "target_ebitda_period_type", "target_ebitda_period_end",
    "ev_to_revenue_ltm", "ev_to_revenue_ntm", "ev_to_ebitda_ltm", "ev_to_ebitda_ntm", "multiple_quality",
    "consideration_type", "consideration_components_json",
    "includes_earnout", "hostile", "competing_bid", "regulatory_approvals_required",
    "has_go_shop", "go_shop_period_days",
    "target_fee_amount", "target_fee_percentage", "acquirer_fee_amount", "acquirer_fee_percentage",
    "is_take_private", "is_add_on", "is_divestiture", "is_de_spac", "has_earnout", "has_cvr",
    "linked_filings_count",
    "acquirer_merger_sub_name", "merger_structure",
    "has_mac_clause", "requires_target_shareholder_vote", "target_vote_threshold",
    "target_total_diluted_shares", "fully_diluted_calc_quality",
    "agreement_extraction_status", "linked_securities_count",
    "has_observation_changes", "observation_changes_field_count",
    "summary_text", "primary_rationale", "secondary_rationales_json",
    "created_at", "updated_at",
]


_MULTIPLE_COLS = ("ev_to_revenue_ltm", "ev_to_revenue_ntm", "ev_to_ebitda_ltm", "ev_to_ebitda_ntm")


def _format_row(row: dict) -> dict:
    """Apply NM display rule to valuation multiple columns."""
    out = dict(row)
    quality = out.get("multiple_quality")
    for col in _MULTIPLE_COLS:
        val = out.get(col)
        if val is None:
            out[col] = ""
        elif quality == "NM":
            out[col] = "NM"
        else:
            out[col] = f"{val:.2f}"
    return out


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Export current transactions to CSV.

    Returns
    -------
    dict
        Keys: transactions_total, rows_exported, export_path
    """
    log = get_logger("export", run_id, level=cfg.log_level)

    rows = conn.execute(
        """
        SELECT
            tr.transaction_id,
            tr.deal_type, tr.event_type, tr.transaction_status, tr.spin_split_type, tr.distribution_mechanism,
            tr.target_name, tr.target_domain, tr.target_ticker, tr.target_type, tr.pct_acquired, tr.target_status, tr.target_description,
            tr.acquirer_name, tr.acquirer_domain, tr.acquirer_ticker, tr.acquirer_type, tr.acquirer_description,
            tr.acquirer_sponsor_name,
            tr.parent_seller_name, tr.parent_seller_ticker, tr.parent_seller_description,
            tr.announced_date, tr.signing_date, tr.closed_date,
            tr.value_amount, tr.value_currency, tr.value_type, tr.per_share_price,
            tr.target_revenue, tr.target_revenue_period_type, tr.target_revenue_period_end,
            tr.target_ebitda, tr.target_ebitda_period_type, tr.target_ebitda_period_end,
            tr.ev_to_revenue_ltm, tr.ev_to_revenue_ntm, tr.ev_to_ebitda_ltm, tr.ev_to_ebitda_ntm,
            tr.multiple_quality,
            tr.consideration_type,
            tr.consideration_components       AS consideration_components_json,
            tr.includes_earnout, tr.hostile, tr.competing_bid,
            tr.regulatory_approvals_required,
            tr.has_go_shop, tr.go_shop_period_days,
            tr.target_fee_amount, tr.target_fee_percentage,
            tr.acquirer_fee_amount, tr.acquirer_fee_percentage,
            tr.is_take_private, tr.is_add_on, tr.is_divestiture, tr.is_de_spac, tr.has_earnout, tr.has_cvr,
            tr.linked_filings_count,
            tr.acquirer_merger_sub_name, tr.merger_structure,
            tr.has_mac_clause, tr.requires_target_shareholder_vote, tr.target_vote_threshold,
            tr.target_total_diluted_shares, tr.fully_diluted_calc_quality,
            tr.agreement_extraction_status,
            (SELECT COUNT(*) FROM transaction_security ts WHERE ts.transaction_id = tr.transaction_id) AS linked_securities_count,
            tr.has_observation_changes, tr.observation_changes_field_count,
            s.summary_text,
            rt.primary_rationale,
            rt.secondary_rationales           AS secondary_rationales_json,
            tr.created_at, tr.updated_at
        FROM transaction_record tr
        LEFT JOIN summary s
            ON s.transaction_id = tr.transaction_id AND s.is_current = 1
        LEFT JOIN rationale_tag rt
            ON rt.transaction_id = tr.transaction_id AND rt.is_current = 1
        WHERE tr.is_current = 1
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 13: %d transactions to export", total)

    exports_dir = os.path.join(os.path.dirname(cfg.db_path), "..", "exports")
    os.makedirs(exports_dir, exist_ok=True)
    csv_path = os.path.join(exports_dir, f"transactions_{run_id}.csv")
    csv_path = os.path.normpath(csv_path)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(_format_row(dict(row)))

    log.info("Stage 14 done  rows=%d  path=%s", total, csv_path)
    return {"transactions_total": total, "rows_exported": total, "export_path": csv_path}
