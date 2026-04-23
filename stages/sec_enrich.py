"""
Stage 6: sec_enrich — NOT YET IMPLEMENTED.

Calls the sec-api.io Filing Query API, Item Extractor API, and Exhibit
Download API for each staging_extraction row with status = SEC_TRIGGERED.
Inserts up to two new rows in source_raw (SEC_8K_ITEM_101 and SEC_EXHIBIT_21).
Updates staging_extraction.sec_lookup_status and status = SEC_ENRICHED (or
leaves TRIGGERED with sec_lookup_status = NO_MATCH / ERROR on failure).

Spec references: specs/adapter_sec_api.md, specs/pipeline.md §2 (Stage 6)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Enrich triggered rows with SEC 8-K Item 1.01 and Exhibit 2.1 text.
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
    log = get_logger("sec_enrich", run_id, level=cfg.log_level)
    log.warning("Stage 6 (sec_enrich) is not yet implemented — skipping")
    return {}
