"""
Stage 7: low_confidence_extract — NOT YET IMPLEMENTED.

Runs the Opus low-confidence extraction prompt on every staging_extraction
row with status = HC_EXTRACTED or SEC_ENRICHED. Extracts advisors
(written to the advisor table), consideration components, deal flags,
go-shop terms, and termination fees. Derives consideration_type from the
components array. Updates status = LC_EXTRACTED.

Spec references: prompts/low_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 7)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract advisors, consideration, and deal flags. NOT YET IMPLEMENTED.

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
    log = get_logger("low_confidence_extract", run_id, level=cfg.log_level)
    log.warning("Stage 7 (low_confidence_extract) is not yet implemented — skipping")
    return {}
