"""
Stage 12: export — NOT YET IMPLEMENTED.

Exports all transaction_record rows where is_current = 1, joined with their
current summary and rationale_tag rows, to a CSV file at
exports/transactions_<run_id>.csv. Rows are sorted by announced_date DESC.
The consideration_components and secondary_rationales JSON arrays are
serialized as strings in the CSV (not exploded). See specs/pipeline.md §2
(Stage 12) and the operator-approved column list for the full field order.

Spec references: specs/pipeline.md §2 (Stage 12)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Export current transactions to CSV. NOT YET IMPLEMENTED.

    Parameters
    ----------
    conn:
        Open database connection.
    cfg:
        Loaded pipeline configuration.
    run_id:
        Current run identifier used for logging.

    Returns
    -------
    dict
        Stage result counts keyed for the run summary, including
        ``export_path`` with the output file path.
    """
    log = get_logger("export", run_id, level=cfg.log_level)
    log.warning("Stage 12 (export) is not yet implemented — skipping")
    return {"export_path": ""}
