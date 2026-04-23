"""
Stage 3: deal_type_classify

Runs the Opus deal type classifier on every source_raw row with
source_status = RELEVANT that does not yet have a staging_extraction row.

Creates a staging_extraction row for each processed source:
  - status = CLASSIFIED on success
  - status = PROMPT_FAILED on prompt failure or schema violation

On CLASSIFIED, populates: deal_type, spin_split_type, distribution_mechanism,
target_type, event_type, target_status, model_confidence, dt_prompt_version.

Notes field shape for newly created staging_extraction rows:
    {"dt": "<model notes string or null>"}
    If overrides_relevancy_hint is True, adds "dt_overrides_relevancy": true.

Schema validation enforced:
  - deal_type must be in: ACQUISITION, MERGER, SPIN_SPLIT, REVERSE_MERGER,
    JOINT_VENTURE, MINORITY_INVESTMENT, UNKNOWN
  - event_type must be in: ANNOUNCEMENT, CLOSE, AMENDMENT, TERMINATION
  - target_status must be in: PUBLIC, PRIVATE, SUBSIDIARY_OF_PUBLIC,
    SUBSIDIARY_OF_PRIVATE, UNKNOWN
  - spin_split_type and distribution_mechanism must be null for non-SPIN_SPLIT

PROMPT_FAILED rows are not retried automatically; use --mode=rerun-prompt.

Spec references: prompts/deal_type_classifier.md, specs/pipeline.md §2 (Stage 3)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "deal_type_classifier"
_VERSION = "0.3"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_VALID_DEAL_TYPES = frozenset({
    "ACQUISITION", "MERGER", "SPIN_SPLIT", "REVERSE_MERGER",
    "JOINT_VENTURE", "MINORITY_INVESTMENT", "UNKNOWN",
})
_VALID_EVENT_TYPES = frozenset({"ANNOUNCEMENT", "CLOSE", "AMENDMENT", "TERMINATION"})
_VALID_TARGET_STATUSES = frozenset({
    "PUBLIC", "PRIVATE", "SUBSIDIARY_OF_PUBLIC", "SUBSIDIARY_OF_PRIVATE", "UNKNOWN",
})
_SLEEP = 1.0  # conservative Opus throttle


def _validate(result: dict) -> str | None:
    if result.get("deal_type") not in _VALID_DEAL_TYPES:
        return f"invalid deal_type: {result.get('deal_type')!r}"
    if result.get("event_type") not in _VALID_EVENT_TYPES:
        return f"invalid event_type: {result.get('event_type')!r}"
    if result.get("target_status") not in _VALID_TARGET_STATUSES:
        return f"invalid target_status: {result.get('target_status')!r}"
    if result.get("deal_type") != "SPIN_SPLIT":
        if result.get("spin_split_type") is not None or result.get("distribution_mechanism") is not None:
            return "spin_split discriminators must be null for non-SPIN_SPLIT"
    return None


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Classify deal types for relevant rows and create staging_extraction records.

    Returns
    -------
    dict
        Keys: relevant_total, classified, dt_failed, failures
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT sr.source_raw_id, sr.title, sr.clean_text, sr.notes
        FROM source_raw sr
        WHERE sr.source_status = 'RELEVANT'
          AND NOT EXISTS (
              SELECT 1 FROM staging_extraction se
              WHERE se.source_raw_id = sr.source_raw_id
          )
        """
    ).fetchall()

    total = len(rows)
    classified = failed = 0
    log.info("Stage 3: %d rows to classify", total)

    for row in rows:
        sid = row["source_raw_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        # Read reason_code written by Stage 2
        reason_code = "UNKNOWN"
        if row["notes"]:
            try:
                nd = json.loads(row["notes"])
                reason_code = nd.get("relevancy", {}).get("reason_code") or "UNKNOWN"
            except (ValueError, TypeError):
                pass

        user_prompt = prompt["user_template"].format(
            title=title,
            clean_text=body,
            relevancy_reason_code=reason_code,
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="opus",
                temperature=0.0,
                max_tokens=512,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                source_raw_id=sid,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("source_raw_id=%d prompt failed: %s", sid, exc)
            _insert(conn, sid, "PROMPT_FAILED", None, _VERSION, None)
            failed += 1
            time.sleep(_SLEEP)
            continue

        err = _validate(result)
        if err:
            log.warning("source_raw_id=%d schema violation: %s — PROMPT_FAILED", sid, err)
            _insert(conn, sid, "PROMPT_FAILED", None, _VERSION, None)
            failed += 1
            time.sleep(_SLEEP)
            continue

        notes_dict: dict = {"dt": result.get("notes")}
        if result.get("overrides_relevancy_hint"):
            notes_dict["dt_overrides_relevancy"] = True

        _insert(conn, sid, "CLASSIFIED", result, _VERSION, json.dumps(notes_dict))
        classified += 1
        log.info(
            "source_raw_id=%d CLASSIFIED  deal_type=%s  event_type=%s  confidence=%s",
            sid, result.get("deal_type"), result.get("event_type"), result.get("model_confidence"),
        )
        time.sleep(_SLEEP)

    log.info("Stage 3 done  total=%d classified=%d failed=%d", total, classified, failed)
    return {
        "relevant_total": total,
        "classified": classified,
        "dt_failed": failed,
        "failures": failed,
    }


def _insert(
    conn: sqlite3.Connection,
    source_raw_id: int,
    status: str,
    result: dict | None,
    dt_version: str,
    notes: str | None,
) -> None:
    r = result or {}
    conn.execute(
        """
        INSERT INTO staging_extraction
            (source_raw_id, status,
             deal_type, spin_split_type, distribution_mechanism,
             target_type, event_type, target_status,
             model_confidence, dt_prompt_version, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_raw_id, status,
            r.get("deal_type"), r.get("spin_split_type"), r.get("distribution_mechanism"),
            r.get("target_type"), r.get("event_type"), r.get("target_status"),
            r.get("model_confidence"), dt_version, notes,
        ),
    )
    conn.commit()
