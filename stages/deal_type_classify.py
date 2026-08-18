"""
Stage 3: deal_type_classify

Runs the Opus deal type classifier on every source_raw row with
source_status = RELEVANT that does not yet have a staging_extraction row.

Creates a staging_extraction row for each processed source:
  - status = CLASSIFIED on success
  - status = RECOGNIZED_NOT_PROFILED when the source explicitly identifies a PIPE
  - status = PROMPT_FAILED on prompt failure or schema violation

RECOGNIZED_NOT_PROFILED is a terminal state for structures this pipeline recognizes but
does not profile. Both extraction gates select `status = 'CLASSIFIED'`, so such a row is
skipped by Stage 4 and Stage 4a without either gate naming the structure, and never
reaches clustering or aggregation. See lib/pipe_recognition.py for why a PIPE needs one:
declining to call it a funding round previously routed it into M&A extraction instead,
because Stage 4's gate is a NOT IN on the funding family and UNKNOWN satisfies it.

On CLASSIFIED, populates: deal_type, v2_event_type, spin_split_type,
spin_split_type_v2, distribution_mechanism, recap_type, target_type,
target_type_v2, event_type, event_history_type, target_status,
model_confidence, dt_prompt_version.

Notes field shape for newly created staging_extraction rows:
    {"dt": "<model notes string or null>"}
    If overrides_relevancy_hint is True, adds "dt_overrides_relevancy": true.
    If the row is excluded as a recognized PIPE, adds "pipe_exclusion": {...} carrying
    the classifier's own verdict and the quoted source sentence, so the exclusion is
    reviewable and reversible.

Schema validation enforced:
  - v2_event_type must be in V2 EventType enum when present
  - deal_type accepted as transitional alias (same valid set)
  - event_history_type must be in: ANNOUNCED, CLOSED, AMENDED, TERMINATED
  - event_type accepted as legacy alias during rollout
  - target_status must be in valid set
  - recap_type must be null for non-RECAPITALIZATION
  - spin_split discriminators must be null for non-spin types

PROMPT_FAILED rows are not retried automatically; use --mode=rerun-prompt.

Spec references: prompts/deal_type_classifier.md, specs/pipeline.md §2 (Stage 3)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from lib.pipe_recognition import (
    PIPE_EVENT_TYPE, PIPE_EXCLUDED_STATUS, resolve_classification,
)
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "deal_type_classifier"
_VERSION = "0.7"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

# V2 EventType enum values
_VALID_V2_EVENT_TYPES = frozenset({
    "ACQUISITION", "MERGER", "SPIN_OFF", "SPLIT_OFF", "REVERSE_MERGER",
    "JOINT_VENTURE", "RECAPITALIZATION",
    "VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT",
    # Recognized, not profiled. Accepted here so the value validates whether it comes
    # from the deterministic recognizer or, later, from the classifier itself — the
    # prompt does not yet offer it, and adding it to the enum first is what makes that
    # a prompt-only change when it happens.
    PIPE_EVENT_TYPE,
    "UNKNOWN",
})
# Legacy deal_type values accepted during migration
_VALID_DEAL_TYPES = _VALID_V2_EVENT_TYPES | frozenset({"SPIN_SPLIT"})

# V2 event_history_type values
_VALID_EVENT_HISTORY_TYPES = frozenset({"ANNOUNCED", "CLOSED", "AMENDED", "TERMINATED"})
# Legacy event_type values accepted during rollout
_VALID_LEGACY_EVENT_TYPES = frozenset({"ANNOUNCEMENT", "CLOSE", "AMENDMENT", "TERMINATION"})

_VALID_TARGET_STATUSES = frozenset({
    "PUBLIC", "PRIVATE", "SUBSIDIARY_OF_PUBLIC", "SUBSIDIARY_OF_PRIVATE", "UNKNOWN",
})
_VALID_RECAP_TYPES = frozenset({"DIVIDEND", "EQUITY", "LEVERAGED", "SPONSOR_RECAP"})
_VALID_SPIN_SPLIT_TYPES = frozenset({"SPIN_OFF", "SPLIT_OFF", "SPLIT"})  # SPLIT accepted during rollout

# V2 lowercase target_type values
_VALID_TARGET_TYPES_V2 = frozenset({
    "standalone_company", "business_unit", "subsidiary", "assets", "spinco",
})
# Legacy uppercase accepted during rollout
_VALID_LEGACY_TARGET_TYPES = frozenset({
    "STANDALONE_COMPANY", "BUSINESS_UNIT", "SUBSIDIARY", "ASSETS",
})

_SLEEP = 1.0  # conservative Opus throttle

# ---------------------------------------------------------------------------
# Normalization helpers — handle legacy values from pre-v0.6 prompt output
# ---------------------------------------------------------------------------

_LEGACY_EVENT_TYPE_MAP = {
    "ANNOUNCEMENT": "ANNOUNCED",
    "CLOSE": "CLOSED",
    "AMENDMENT": "AMENDED",
    "TERMINATION": "TERMINATED",
}

_LEGACY_TARGET_TYPE_MAP = {
    "STANDALONE_COMPANY": "standalone_company",
    "BUSINESS_UNIT": "business_unit",
    "SUBSIDIARY": "subsidiary",
    "ASSETS": "assets",
}

_LEGACY_SPIN_SPLIT_TYPE_MAP = {
    "SPLIT": "SPLIT_OFF",  # v0.5 used SPLIT; v0.6 uses SPLIT_OFF
}


def _normalize_event_history_type(result: dict) -> str | None:
    """
    Resolve event_history_type from a classifier result.
    Prefers event_history_type (v0.6+); falls back to event_type (v0.5).
    Normalizes legacy ANNOUNCEMENT/CLOSE/AMENDMENT/TERMINATION values.
    """
    eht = result.get("event_history_type")
    if eht in _VALID_EVENT_HISTORY_TYPES:
        return eht
    # Normalize if it's a V2 value in the old field
    legacy = result.get("event_type")
    if legacy in _VALID_EVENT_HISTORY_TYPES:
        return legacy
    if legacy in _LEGACY_EVENT_TYPE_MAP:
        return _LEGACY_EVENT_TYPE_MAP[legacy]
    return eht  # return as-is; validation will catch invalid values


def _normalize_target_type_v2(raw: str | None) -> str | None:
    """Normalize target_type to V2 lowercase vocabulary."""
    if raw is None:
        return None
    if raw in _VALID_TARGET_TYPES_V2:
        return raw
    return _LEGACY_TARGET_TYPE_MAP.get(raw, raw)


def _normalize_spin_split_type_v2(raw: str | None) -> str | None:
    """Normalize spin_split_type to V2 values."""
    if raw is None:
        return None
    return _LEGACY_SPIN_SPLIT_TYPE_MAP.get(raw, raw)


def _resolve_v2_event_type(result: dict) -> str | None:
    """Get canonical v2_event_type — prefer v2_event_type field, fall back to deal_type."""
    v2et = result.get("v2_event_type")
    if v2et in _VALID_V2_EVENT_TYPES:
        return v2et
    # Fall back to deal_type, normalizing SPIN_SPLIT → SPIN_OFF
    dt = result.get("deal_type")
    if dt == "SPIN_SPLIT":
        # Determine SPIN_OFF vs SPLIT_OFF from distribution_mechanism
        mech = result.get("distribution_mechanism")
        return "SPLIT_OFF" if mech == "EXCHANGE_OFFER" else "SPIN_OFF"
    if dt in _VALID_V2_EVENT_TYPES:
        return dt
    return v2et


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate(result: dict) -> str | None:
    # Must have at least one of v2_event_type or deal_type
    raw_v2et = result.get("v2_event_type")
    if raw_v2et is not None and raw_v2et not in _VALID_V2_EVENT_TYPES:
        return f"invalid v2_event_type: {raw_v2et!r}"

    v2et = _resolve_v2_event_type(result)

    dt = result.get("deal_type")
    if dt is not None and dt not in _VALID_DEAL_TYPES:
        return f"invalid deal_type: {dt!r}"

    if v2et is None and dt is None:
        return "missing both v2_event_type and deal_type"

    # event_history_type — accept new field or legacy event_type
    eht = _normalize_event_history_type(result)
    if eht not in _VALID_EVENT_HISTORY_TYPES:
        return f"invalid event_history_type: {eht!r}"

    # target_status
    if result.get("target_status") not in _VALID_TARGET_STATUSES:
        return f"invalid target_status: {result.get('target_status')!r}"

    # recap_type — required null for non-RECAPITALIZATION
    recap = result.get("recap_type")
    if v2et == "RECAPITALIZATION":
        if recap is not None and recap not in _VALID_RECAP_TYPES:
            return f"invalid recap_type: {recap!r}"
    elif recap is not None:
        return f"recap_type must be null for v2_event_type={v2et!r}"

    # spin_split discriminators — must be null for non-spin types
    is_spin = v2et in ("SPIN_OFF", "SPLIT_OFF") or dt == "SPIN_SPLIT"
    if not is_spin:
        if result.get("spin_split_type") is not None:
            return "spin_split_type must be null for non-spin types"
        if result.get("distribution_mechanism") is not None:
            return "distribution_mechanism must be null for non-spin types"

    # target_type — accept both legacy uppercase and V2 lowercase
    tt = result.get("target_type")
    if tt is not None and tt not in (_VALID_TARGET_TYPES_V2 | _VALID_LEGACY_TARGET_TYPES):
        return f"invalid target_type: {tt!r}"

    return None


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

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
    classified = failed = excluded = 0
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
                model="sonnet",
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

        v2_event_type = _resolve_v2_event_type(result)
        event_history_type = _normalize_event_history_type(result)

        # Recognized-but-not-profiled exclusion. Consulted only for the seats a PIPE
        # can mis-occupy; a structural verdict is never re-examined. The full classifier
        # result is still written, so nothing about the row is lost.
        routing = resolve_classification(v2_event_type, row["title"] or "",
                                         row["clean_text"] or "")
        if routing["excluded"]:
            notes_dict["pipe_exclusion"] = routing["provenance"]

        _insert(conn, sid, routing["status"], result, _VERSION, json.dumps(notes_dict),
                v2_event_type=routing["v2_event_type"],
                event_history_type=event_history_type)
        if routing["excluded"]:
            excluded += 1
            log.info(
                "source_raw_id=%d %s  v2_event_type=%s  (classifier said %s)  evidence=%r",
                sid, PIPE_EXCLUDED_STATUS, routing["v2_event_type"],
                routing["provenance"]["classifier_v2_event_type"],
                routing["provenance"]["evidence"][:120],
            )
        else:
            classified += 1
            log.info(
                "source_raw_id=%d CLASSIFIED  v2_event_type=%s  event_history_type=%s  confidence=%s",
                sid, v2_event_type, event_history_type, result.get("model_confidence"),
            )
        time.sleep(_SLEEP)

    log.info("Stage 3 done  total=%d classified=%d excluded=%d failed=%d",
             total, classified, excluded, failed)
    return {
        "relevant_total": total,
        "classified": classified,
        "recognized_not_profiled": excluded,
        "dt_failed": failed,
        "failures": failed,
    }


# ---------------------------------------------------------------------------
# Insert helper
# ---------------------------------------------------------------------------

def _insert(
    conn: sqlite3.Connection,
    source_raw_id: int,
    status: str,
    result: dict | None,
    dt_version: str,
    notes: str | None,
    v2_event_type: str | None = None,
    event_history_type: str | None = None,
) -> None:
    r = result or {}

    # Normalize V2 fields
    target_type_v2 = _normalize_target_type_v2(r.get("target_type"))
    spin_split_type_v2 = _normalize_spin_split_type_v2(r.get("spin_split_type"))

    conn.execute(
        """
        INSERT INTO staging_extraction
            (source_raw_id, status,
             deal_type, v2_event_type,
             spin_split_type, spin_split_type_v2,
             distribution_mechanism,
             recap_type,
             target_type, target_type_v2,
             event_type, event_history_type,
             target_status,
             model_confidence, dt_prompt_version, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_raw_id, status,
            # deal_type — legacy field; keep writing for backward compat
            r.get("deal_type") or v2_event_type,
            # v2_event_type — new canonical field
            v2_event_type,
            # spin_split_type legacy + V2
            r.get("spin_split_type"), spin_split_type_v2,
            # distribution_mechanism (unchanged)
            r.get("distribution_mechanism"),
            # recap_type — new field
            r.get("recap_type"),
            # target_type legacy + V2 lowercase
            r.get("target_type"), target_type_v2,
            # event_type legacy + event_history_type new
            r.get("event_type") or event_history_type,
            event_history_type,
            # target_status (unchanged)
            r.get("target_status"),
            r.get("model_confidence"), dt_version, notes,
        ),
    )
    conn.commit()
