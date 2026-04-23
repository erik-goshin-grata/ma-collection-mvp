"""
Stage 11: rationale_tag — NOT YET IMPLEMENTED.

Classifies the primary strategic rationale for each transaction that has a
current summary. The orchestrator pre-selects up to 3 rationale-signaling
excerpts from source texts before calling the Opus strategic rationale prompt.
Inserts a row into rationale_tag with is_current = 1; prior tags for the same
transaction are flipped to is_current = 0.

Spec references: prompts/strategic_rationale.md, specs/pipeline.md §2 (Stage 11)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Tag strategic rationale for summarized transactions. NOT YET IMPLEMENTED.

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
    log = get_logger("rationale_tag", run_id, level=cfg.log_level)
    log.warning("Stage 11 (rationale_tag) is not yet implemented — skipping")
    return {"rationales_tagged": 0}
