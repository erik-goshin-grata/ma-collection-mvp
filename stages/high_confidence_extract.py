"""
Stage 4: high_confidence_extract — NOT YET IMPLEMENTED.

Runs the Opus high-confidence extraction prompt on every staging_extraction
row with status = CLASSIFIED. Extracts parties (target, acquirer, parent
seller), dates (announced, closed, signing), deal value, and target financials.
Updates the staging_extraction row in place and sets status = HC_EXTRACTED.

Spec references: prompts/high_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 4)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract high-confidence fields for classified staging rows. NOT YET IMPLEMENTED.

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
    log = get_logger("high_confidence_extract", run_id, level=cfg.log_level)
    log.warning("Stage 4 (high_confidence_extract) is not yet implemented — skipping")
    return {"extracted": 0}
