"""
Stage 5: sec_trigger_detect — NOT YET IMPLEMENTED.

Pure Python stage (no LLM calls). Inspects the clean_text of each
PR Newswire source for public-company signals: exchange:ticker patterns,
SEC filing language, and public-company boilerplate. Sets
staging_extraction.sec_lookup_status = TRIGGERED or NOT_TRIGGERED and
updates status = SEC_TRIGGERED or SEC_NOT_TRIGGERED.

Spec references: specs/adapter_sec_api.md §3 (trigger logic),
                 specs/pipeline.md §2 (Stage 5)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Detect public-party signals in HC-extracted rows. NOT YET IMPLEMENTED.

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
    log = get_logger("sec_trigger_detect", run_id, level=cfg.log_level)
    log.warning("Stage 5 (sec_trigger_detect) is not yet implemented — skipping")
    return {}
