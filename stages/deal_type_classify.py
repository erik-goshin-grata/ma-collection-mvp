"""
Stage 3: deal_type_classify — NOT YET IMPLEMENTED.

Runs the Opus deal type classifier on every source_raw row with
source_status = RELEVANT. Creates a staging_extraction row for each,
populated with deal_type, spin_split_type, distribution_mechanism,
target_type, event_type, target_status, and sets status = CLASSIFIED.

Spec references: prompts/deal_type_classifier.md, specs/pipeline.md §2 (Stage 3)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Classify deal types for relevant rows and create staging_extraction records.
    NOT YET IMPLEMENTED.

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
    log = get_logger("deal_type_classify", run_id, level=cfg.log_level)
    log.warning("Stage 3 (deal_type_classify) is not yet implemented — skipping")
    return {}
