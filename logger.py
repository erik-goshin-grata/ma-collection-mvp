"""
Shared logging infrastructure for the M&A Collection MVP pipeline.

get_logger() returns a Python logger scoped to a (stage, run_id) pair that
writes to both stdout and logs/<stage>_<run_id>.log. Loggers are cached so
repeated calls with the same arguments don't stack up duplicate handlers.

Also provides helpers for the run_log table so run.py can record run state
without additional imports.

Spec references: specs/pipeline.md §6 (run summary, error posture),
                 schema/001_initial.sql (run_log table)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path("logs")

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)

# Cache keyed by (stage, run_id) to avoid duplicate handlers on re-entry.
_logger_cache: dict[tuple[str, str], logging.Logger] = {}


def get_logger(stage: str, run_id: str, level: str = "INFO") -> logging.Logger:
    """Return a logger that writes to stdout and logs/<stage>_<run_id>.log.

    Parameters
    ----------
    stage:
        Pipeline stage name used in the log file name and logger name,
        e.g. ``"pr_newswire"`` or ``"entity_cluster"``.
    run_id:
        Current run identifier, e.g. ``"run_20260422_120000"``.
    level:
        Log level string (DEBUG / INFO / WARNING / ERROR). Defaults to INFO.

    Returns
    -------
    logging.Logger
    """
    cache_key = (stage, run_id)
    if cache_key in _logger_cache:
        return _logger_cache[cache_key]

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"{run_id}.{stage}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False  # Don't double-log via the root logger.

    if not logger.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(_FORMATTER)
        logger.addHandler(stream_handler)

        file_handler = logging.FileHandler(
            _LOG_DIR / f"{stage}_{run_id}.log", encoding="utf-8"
        )
        file_handler.setFormatter(_FORMATTER)
        logger.addHandler(file_handler)

    _logger_cache[cache_key] = logger
    return logger


def insert_run_log(
    conn: sqlite3.Connection,
    run_id: str,
    mode: str,
) -> None:
    """Insert a RUNNING row into run_log at the start of a pipeline run.

    Uses INSERT OR IGNORE so re-running with the same run_id is safe.

    Parameters
    ----------
    conn:
        Open database connection.
    run_id:
        Unique run identifier.
    mode:
        Pipeline mode string (full / resume / scrape / etc.).
    """
    now = _utcnow()
    conn.execute(
        "INSERT OR IGNORE INTO run_log (run_id, mode, started_at, status) "
        "VALUES (?, ?, ?, 'RUNNING')",
        (run_id, mode, now),
    )
    conn.commit()


def update_run_log(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    summary_json: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Update run_log at the conclusion of a pipeline run.

    Parameters
    ----------
    conn:
        Open database connection.
    run_id:
        Unique run identifier.
    status:
        Final status: ``"COMPLETED"`` or ``"FAILED"``.
    summary_json:
        Dict of per-stage counts to store as JSON. None if not available.
    error_message:
        Error string if status is FAILED. None otherwise.
    """
    now = _utcnow()
    conn.execute(
        "UPDATE run_log "
        "SET status = ?, ended_at = ?, summary_json = ?, error_message = ? "
        "WHERE run_id = ?",
        (
            status,
            now,
            json.dumps(summary_json) if summary_json is not None else None,
            error_message,
            run_id,
        ),
    )
    conn.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
