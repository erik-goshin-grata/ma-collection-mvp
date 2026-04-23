"""
Stage 8: entity_cluster — NOT YET IMPLEMENTED.

Clusters all staging_extraction rows with status = LC_EXTRACTED into groups
that represent the same real-world transaction. Uses name normalization
(legal suffix stripping, parenthetical removal, whitespace collapse) followed
by rapidfuzz token_set_ratio >= 90 on both target and acquirer names, combined
with an announced_date proximity window of ± 3 days. Transitive closure via
union-find. Assigns a deterministic transaction_cluster_id (tc_ + first 12
hex chars of SHA-256) to each row in the cluster.

Spec references: specs/entity_resolution.md, specs/pipeline.md §2 (Stage 8)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Cluster LC-extracted rows into transaction groups. NOT YET IMPLEMENTED.

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
    log = get_logger("entity_cluster", run_id, level=cfg.log_level)
    log.warning("Stage 8 (entity_cluster) is not yet implemented — skipping")
    return {"clusters": 0, "merged_duplicates": 0}
