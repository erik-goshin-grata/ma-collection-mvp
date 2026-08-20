"""
Stage 7: low_confidence_extract

Runs the Opus low-confidence extraction prompt on every staging_extraction
row with status IN ('HC_EXTRACTED', 'SEC_NOT_TRIGGERED', 'SEC_ENRICHED').
The broad status set covers rows that finished HC extraction but may not have
gone through Stage 5/6 (e.g., resume after partial run).

Populates on success (status → LC_EXTRACTED):
  - consideration_components (JSON array on staging_extraction)
  - flags: includes_earnout, deal_attitude, approach_type, competing_bid,
    regulatory_approvals_required
  - go_shop: has_go_shop, go_shop_period_days
  - termination_fees: target_fee_amount/percentage, acquirer_fee_amount/percentage
  - lc_prompt_version
  - advisor rows (one INSERT per advisor in the response array)
  - notes["lc"] merged into existing notes dict

On PROMPT_FAILED: sets status = PROMPT_FAILED on the staging_extraction row.
Failed rows are not retried automatically; use --mode=rerun-prompt.

Schema validation:
  - Required top-level keys must be present
  - advisors and consideration_components must be lists
  - go_shop.has_go_shop=false with go_shop_period_days non-null → SCHEMA_VIOLATION
  - Per-advisor: invalid type or advised_party values are skipped with a warning
    rather than failing the entire row
  - deal_attitude / approach_type: null is valid and meaningful (fact not established);
    an out-of-vocabulary non-null value is a SCHEMA_VIOLATION

Spec references: prompts/low_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 7)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from lib.observation_writer import write_staging_observations_for_extraction
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "low_confidence_extraction"
_VERSION = "0.6"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_REQUIRED_KEYS = frozenset({
    "advisors", "consideration_components", "flags",
    "go_shop", "termination_fees", "model_confidence",
})
_VALID_ADVISOR_TYPES = frozenset({"FINANCIAL", "LEGAL", "OTHER"})
_VALID_ADVISED_PARTIES = frozenset({"TARGET", "ACQUIRER", "PARENT_SELLER", "BOTH", "UNKNOWN"})
# V3 §T11 — two independent nullable dimensions replacing the fused `hostile` boolean.
# null is a valid, meaningful value for both: the source did not establish the fact.
_VALID_DEAL_ATTITUDE = frozenset({"FRIENDLY", "HOSTILE"})
_VALID_APPROACH_TYPE = frozenset({"SOLICITED", "UNSOLICITED"})
_SLEEP = 1.0  # conservative Opus throttle


def _fmt(v) -> str:
    return str(v) if v is not None else "null"


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"
    if not isinstance(result.get("advisors"), list):
        return "advisors must be a list"
    if not isinstance(result.get("consideration_components"), list):
        return "consideration_components must be a list"
    go_shop = result.get("go_shop") or {}
    if not go_shop.get("has_go_shop", False) and go_shop.get("go_shop_period_days") is not None:
        return "go_shop_period_days must be null when has_go_shop is false"
    flags = result.get("flags") or {}
    for key, vocab in (("deal_attitude", _VALID_DEAL_ATTITUDE),
                       ("approach_type", _VALID_APPROACH_TYPE)):
        value = flags.get(key)
        if value is not None and value not in vocab:
            return f"invalid {key}: {value!r}"
    return None


def _clean_advisors(advisors: list, log, eid: int) -> list[dict]:
    """Filter advisor list to entries with valid type and advised_party.

    Bad entries are logged and skipped rather than failing the whole row.
    """
    valid = []
    for a in advisors:
        if not isinstance(a, dict):
            log.warning("extraction_id=%d skipping non-dict advisor: %r", eid, a)
            continue
        name = (a.get("name") or "").strip()
        if not name:
            log.warning("extraction_id=%d skipping advisor with empty name", eid)
            continue
        atype = a.get("type")
        if atype not in _VALID_ADVISOR_TYPES:
            log.warning("extraction_id=%d invalid advisor type %r for %r — skipping", eid, atype, name)
            continue
        party = a.get("advised_party")
        if party not in _VALID_ADVISED_PARTIES:
            log.warning("extraction_id=%d invalid advised_party %r for %r — skipping", eid, party, name)
            continue
        valid.append({"name": name, "type": atype, "advised_party": party})
    return valid


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract advisors, consideration components, and deal flags.

    Returns
    -------
    dict
        Keys: eligible_total, lc_extracted, failed, advisors_inserted
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               se.deal_type, se.target_type, se.event_type, se.v2_event_type,
               se.event_history_type,
               se.value_amount, se.value_currency, se.value_type,
               se.notes,
               sr.source_type, sr.title, sr.clean_text
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status IN ('HC_EXTRACTED', 'SEC_NOT_TRIGGERED', 'SEC_ENRICHED')
        """
    ).fetchall()

    total = len(rows)
    lc_extracted = failed = advisors_inserted = 0
    log.info("Stage 7: %d rows to extract", total)

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            deal_type=_fmt(row["deal_type"]),
            target_type=_fmt(row["target_type"]),
            event_type=_fmt(row["event_type"]),
            v2_event_type=_fmt(row["v2_event_type"]),
            event_history_type=_fmt(row["event_history_type"]),
            value_amount=_fmt(row["value_amount"]),
            value_currency=_fmt(row["value_currency"]),
            value_type=_fmt(row["value_type"]),
            title=title,
            clean_text=body,
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="opus",
                temperature=0.0,
                max_tokens=2048,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                extraction_id=eid,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("extraction_id=%d prompt failed: %s", eid, exc)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        err = _validate(result)
        if err:
            log.warning("extraction_id=%d schema violation: %s — PROMPT_FAILED", eid, err)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        flags = result.get("flags") or {}
        go_shop = result.get("go_shop") or {}
        fees = result.get("termination_fees") or {}

        # Merge LC notes into existing notes dict under "lc" key
        nd: dict = {}
        if row["notes"]:
            try:
                nd = json.loads(row["notes"])
                if not isinstance(nd, dict):
                    nd = {}
            except (ValueError, TypeError):
                nd = {}
        lc_notes = result.get("notes")
        if lc_notes:
            nd["lc"] = lc_notes

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE staging_extraction SET
                status = 'LC_EXTRACTED',
                consideration_components = ?,
                includes_earnout = ?,
                deal_attitude = ?,
                approach_type = ?,
                competing_bid = ?,
                regulatory_approvals_required = ?,
                has_go_shop = ?,
                go_shop_period_days = ?,
                target_fee_amount = ?,
                target_fee_percentage = ?,
                acquirer_fee_amount = ?,
                acquirer_fee_percentage = ?,
                model_confidence = ?,
                lc_prompt_version = ?,
                notes = ?,
                updated_at = ?
            WHERE extraction_id = ?
            """,
            (
                json.dumps(result.get("consideration_components") or []),
                1 if flags.get("includes_earnout") else 0,
                # V3 §T11: three-state. None must stay None — "not established" is not
                # "friendly". Do NOT coerce these to a default the way the flags below are.
                flags.get("deal_attitude"),
                flags.get("approach_type"),
                # competing_bid stays a coerced boolean by decision: it names a single fact
                # whose prompt contract is "false otherwise", unlike the two fields above.
                1 if flags.get("competing_bid") else 0,
                1 if flags.get("regulatory_approvals_required") else 0,
                1 if go_shop.get("has_go_shop") else 0,
                go_shop.get("go_shop_period_days"),
                fees.get("target_fee_amount"),
                fees.get("target_fee_percentage"),
                fees.get("acquirer_fee_amount"),
                fees.get("acquirer_fee_percentage"),
                result.get("model_confidence"),
                _VERSION,
                json.dumps(nd),
                now,
                eid,
            ),
        )
        write_staging_observations_for_extraction(
            conn,
            eid,
            observation_source_stage="LC_EXTRACT",
            include_lc=True,
        )
        conn.commit()

        # Insert advisor rows; bad entries already filtered by _clean_advisors
        clean_advisors = _clean_advisors(result.get("advisors") or [], log, eid)
        for adv in clean_advisors:
            conn.execute(
                "INSERT INTO advisor (extraction_id, name, type, advised_party) VALUES (?, ?, ?, ?)",
                (eid, adv["name"], adv["type"], adv["advised_party"]),
            )
        conn.commit()
        advisors_inserted += len(clean_advisors)

        lc_extracted += 1
        log.info(
            "extraction_id=%d LC_EXTRACTED  advisors=%d  components=%d  earnout=%s  regulatory=%s",
            eid,
            len(clean_advisors),
            len(result.get("consideration_components") or []),
            flags.get("includes_earnout"),
            flags.get("regulatory_approvals_required"),
        )
        time.sleep(_SLEEP)

    log.info(
        "Stage 7 done  total=%d lc_extracted=%d failed=%d advisors=%d",
        total, lc_extracted, failed, advisors_inserted,
    )
    return {
        "eligible_total": total,
        "lc_extracted": lc_extracted,
        "failed": failed,
        "advisors_inserted": advisors_inserted,
    }


def _mark_failed(conn: sqlite3.Connection, extraction_id: int) -> None:
    conn.execute(
        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
        (datetime.now(timezone.utc).isoformat(), extraction_id),
    )
    conn.commit()
