"""
Stage 1: scrape_pr_newswire

Fetches the PR Newswire M&A / Acquisitions category listing and the body of
each press release. Delegates entirely to adapters/pr_newswire.py.

Spec references: specs/adapter_pr_newswire.md, specs/pipeline.md §2 (Stage 1)
"""

from __future__ import annotations

import sqlite3

import adapters.pr_newswire as _adapter
from config import Config


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Stage 1: scrape PR Newswire. Delegates to the adapter."""
    return _adapter.run(conn=conn, cfg=cfg, run_id=run_id)
