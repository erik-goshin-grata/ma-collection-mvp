"""
Stage 2: relevancy_filter — NOT YET IMPLEMENTED.

Runs the Haiku relevancy classifier on every source_raw row with
source_status = FETCHED and source_type = PR_NEWSWIRE. Updates
source_status to RELEVANT or NOT_RELEVANT based on the classifier output.
SEC-sourced rows are not filtered (assumed relevant by construction).

Spec references: prompts/relevancy_filter.md, specs/pipeline.md §2 (Stage 2)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Apply the Haiku relevancy gate to fetched PR Newswire rows. NOT YET IMPLEMENTED.

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
    log = get_logger("relevancy_filter", run_id, level=cfg.log_level)
    log.warning("Stage 2 (relevancy_filter) is not yet implemented — skipping")
    return {"relevant": 0}
