"""
Stage 10: summarize — NOT YET IMPLEMENTED.

Generates an 80–150 word natural-language summary for each transaction_record
row where is_current = 1 and no current summary exists. Calls the Opus deal
summary prompt with the full aggregated transaction context. Inserts a row
into the summary table with is_current = 1; any previous summaries for the
same transaction are flipped to is_current = 0.

Spec references: prompts/deal_summary.md, specs/pipeline.md §2 (Stage 10)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Generate deal summaries for current transaction records. NOT YET IMPLEMENTED.

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
        Stage result counts keyed for the run summary.
    """
    log = get_logger("summarize", run_id, level=cfg.log_level)
    log.warning("Stage 10 (summarize) is not yet implemented — skipping")
    return {"summaries_generated": 0}
