"""
Stage 9: aggregate — NOT YET IMPLEMENTED.

For each transaction cluster, applies deterministic source-tier rules
(T1 > T2 > T3) to resolve field values across all staging_extraction rows
in the cluster. Calls the Opus aggregation prompt only for fields where
two sources of equal tier disagree. Derives consideration_type from the
aggregated consideration_components. Upserts a transaction_record row and
populates transaction_source links. Records all LLM-resolved conflicts in
aggregation_conflict_log. Sets staging_extraction.status = AGGREGATED.

Spec references: prompts/aggregation.md, specs/pipeline.md §2 (Stage 9)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Aggregate clustered extractions into canonical transaction records.
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
    log = get_logger("aggregate", run_id, level=cfg.log_level)
    log.warning("Stage 9 (aggregate) is not yet implemented — skipping")
    return {"transactions_created": 0}
