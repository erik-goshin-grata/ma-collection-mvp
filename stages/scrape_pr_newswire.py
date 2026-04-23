"""
Stage 1: scrape_pr_newswire — NOT YET IMPLEMENTED.

Fetches the PR Newswire M&A / Acquisitions category listing and the body of
each press release. Inserts rows into source_raw with source_status = FETCHED.
Respects robots.txt, applies rate limiting, and deduplicates by URL and
content hash.

Spec references: specs/adapter_pr_newswire.md, specs/pipeline.md §2 (Stage 1)
"""

from __future__ import annotations

import sqlite3

from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Scrape PR Newswire and populate source_raw. NOT YET IMPLEMENTED.

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
    log = get_logger("scrape_pr_newswire", run_id, level=cfg.log_level)
    log.warning("Stage 1 (scrape_pr_newswire) is not yet implemented — skipping")
    return {"sources_fetched": 0}
