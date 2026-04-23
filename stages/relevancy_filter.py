"""
Stage 2: relevancy_filter

Runs the Haiku relevancy classifier on every source_raw row with
source_status = FETCHED, source_type = PR_NEWSWIRE, and non-null clean_text.

Updates source_raw.source_status:
  - RELEVANT         — classification == RELEVANT
  - NOT_RELEVANT     — classification == NOT_RELEVANT
  - RELEVANCY_FAILED — prompt failure or unexpected enum value

Stores classifier output in source_raw.notes as a merged JSON blob.  Existing
non-JSON adapter notes are preserved under the "adapter_notes" key.  The
relevancy result is nested under "relevancy":

    {
      "relevancy": {
        "reason_code": "ACQUISITION_ANNOUNCEMENT",
        "model_confidence": "HIGH",
        "notes": null,
        "prompt_version": "relevancy_filter:0.2"
      }
    }

Stage 3 reads reason_code from notes["relevancy"]["reason_code"].
RELEVANCY_FAILED rows are not retried automatically; use --mode=rerun-prompt.

Spec references: prompts/relevancy_filter.md, specs/pipeline.md §2 (Stage 2)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import (
    PromptFailure, call_prompt, load_prompt_file, log_prompt_failure,
    register_prompt_version,
)

_PROMPT_NAME = "relevancy_filter"
_VERSION = "0.4"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"
_VALID_CLASSIFICATIONS = frozenset({"RELEVANT", "NOT_RELEVANT"})
_VALID_REASON_CODES = frozenset({
    # RELEVANT side
    "ACQUISITION_ANNOUNCEMENT", "MERGER_ANNOUNCEMENT", "CARVE_OUT_OR_DIVESTITURE",
    "SPIN_OFF_OR_SPLIT", "TAKE_PRIVATE", "REVERSE_MERGER", "JOINT_VENTURE",
    "MINORITY_INVESTMENT", "DEAL_CLOSE_OR_COMPLETION", "DEAL_AMENDMENT_OR_TERMINATION",
    "AMBIGUOUS_BUT_LIKELY_DEAL",
    # NOT_RELEVANT side
    "PRODUCT_OR_COMMERCIAL", "PERSONNEL", "EARNINGS_OR_FINANCIAL_REPORTING",
    "BUYBACK_OR_DIVIDEND", "DEBT_OR_NON_DEAL_FINANCING", "REGULATORY_OR_COMPLIANCE",
    "MARKETING_OR_COMMENTARY", "RUMOR_OR_SPECULATION", "IPO_OR_DIRECT_LISTING",
    "OTHER_NOT_RELEVANT",
})
_SLEEP = 0.3  # conservative Haiku throttle


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Apply the Haiku relevancy gate to fetched PR Newswire rows.

    Returns
    -------
    dict
        Keys: fetched_total, relevant, not_relevant, relevancy_failed, failures
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT source_raw_id, title, clean_text, notes
        FROM source_raw
        WHERE source_status = 'FETCHED'
          AND source_type = 'PR_NEWSWIRE'
          AND clean_text IS NOT NULL
          AND clean_text != ''
        """
    ).fetchall()

    total = len(rows)
    relevant = not_relevant = failed = 0
    log.info("Stage 2: %d rows to classify", total)

    for row in rows:
        sid = row["source_raw_id"]
        # Escape braces in user content to avoid str.format() substitution errors
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "")[:4000].replace("{", "{{").replace("}", "}}")

        user_prompt = prompt["user_template"].format(title=title, clean_text=body)

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="haiku",
                temperature=0.0,
                max_tokens=256,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                source_raw_id=sid,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("source_raw_id=%d prompt failed: %s", sid, exc)
            _write(conn, sid, "RELEVANCY_FAILED", row["notes"], None)
            failed += 1
            time.sleep(_SLEEP)
            continue

        classification = result.get("classification")
        if classification not in _VALID_CLASSIFICATIONS:
            log.warning(
                "source_raw_id=%d unexpected classification %r — RELEVANCY_FAILED",
                sid, classification,
            )
            log_prompt_failure(
                conn, source_raw_id=sid, extraction_id=None, stage=_PROMPT_NAME,
                failure_type="SCHEMA_VIOLATION", raw_response=json.dumps(result),
                error_message=f"unexpected classification: {classification!r}",
                prompt_version=_FULL_VERSION, run_id=run_id,
            )
            _write(conn, sid, "RELEVANCY_FAILED", row["notes"], result)
            failed += 1
            time.sleep(_SLEEP)
            continue

        reason_code = result.get("reason_code")
        if reason_code not in _VALID_REASON_CODES:
            log.warning(
                "source_raw_id=%d invalid reason_code %r — RELEVANCY_FAILED",
                sid, reason_code,
            )
            log_prompt_failure(
                conn, source_raw_id=sid, extraction_id=None, stage=_PROMPT_NAME,
                failure_type="SCHEMA_VIOLATION", raw_response=json.dumps(result),
                error_message=f"invalid reason_code: {reason_code!r}",
                prompt_version=_FULL_VERSION, run_id=run_id,
            )
            _write(conn, sid, "RELEVANCY_FAILED", row["notes"], result)
            failed += 1
            time.sleep(_SLEEP)
            continue

        new_status = "RELEVANT" if classification == "RELEVANT" else "NOT_RELEVANT"
        _write(conn, sid, new_status, row["notes"], result)

        if new_status == "RELEVANT":
            relevant += 1
            log.info(
                "source_raw_id=%d RELEVANT  reason=%s  confidence=%s",
                sid, result.get("reason_code"), result.get("model_confidence"),
            )
        else:
            not_relevant += 1
            log.debug("source_raw_id=%d NOT_RELEVANT  reason=%s", sid, result.get("reason_code"))

        time.sleep(_SLEEP)

    log.info(
        "Stage 2 done  total=%d relevant=%d not_relevant=%d failed=%d",
        total, relevant, not_relevant, failed,
    )
    return {
        "fetched_total": total,
        "relevant": relevant,
        "not_relevant": not_relevant,
        "relevancy_failed": failed,
        "failures": failed,
    }


def _write(
    conn: sqlite3.Connection,
    source_raw_id: int,
    status: str,
    existing_notes: str | None,
    result: dict | None,
) -> None:
    """Update source_status and merge relevancy data into source_raw.notes."""
    if existing_notes:
        try:
            nd = json.loads(existing_notes)
            if not isinstance(nd, dict):
                nd = {"adapter_notes": existing_notes}
        except (ValueError, TypeError):
            nd = {"adapter_notes": existing_notes}
    else:
        nd = {}

    if result is not None:
        nd["relevancy"] = {
            "reason_code": result.get("reason_code"),
            "model_confidence": result.get("model_confidence"),
            "notes": result.get("notes"),
            "prompt_version": result.get("prompt_version"),
        }

    conn.execute(
        "UPDATE source_raw SET source_status=?, notes=?, updated_at=? WHERE source_raw_id=?",
        (status, json.dumps(nd), datetime.now(timezone.utc).isoformat(), source_raw_id),
    )
    conn.commit()
