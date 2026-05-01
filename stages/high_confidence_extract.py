"""
Stage 4: high_confidence_extract

Runs the Opus high-confidence extraction prompt on every staging_extraction
row with status = CLASSIFIED.  Updates the row in place with extracted fields
and transitions status to HC_EXTRACTED.

On PROMPT_FAILED, sets status = PROMPT_FAILED on the existing staging row.
Failed rows are not automatically retried; use --mode=rerun-prompt.

Notes field after this stage merges Stage 3's "dt" key with Stage 4's "hc":
    {"dt": "<Stage 3 notes>", "hc": "<Stage 4 notes>"}
The "hc" key is only added when the model returns non-null notes.

Schema validation enforced:
  - Required top-level keys must be present
  - value.type must be in: EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED

Spec references: prompts/high_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 4)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "high_confidence_extraction"
_VERSION = "0.8"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_REQUIRED_KEYS = frozenset({"target", "acquirer", "parent_seller", "dates", "value", "target_financials", "model_confidence", "deal"})
_VALID_VALUE_TYPES = frozenset({"EQUITY_VALUE", "TRANSACTION_VALUE", "ENTERPRISE_VALUE", "UNDISCLOSED"})
_SLEEP = 1.0  # conservative Opus throttle


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"
    vtype = (result.get("value") or {}).get("type")
    if vtype is not None and vtype not in _VALID_VALUE_TYPES:
        return f"invalid value.type: {vtype!r}"
    return None


def _fmt(v) -> str:
    """Render a nullable value as its string representation for prompt templates."""
    return str(v) if v is not None else "null"


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract high-confidence fields for classified staging rows.

    Returns
    -------
    dict
        Keys: classified_total, hc_extracted, extracted, hc_failed, failures
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.deal_type, se.spin_split_type, se.target_type,
               se.event_type, se.target_status, se.notes,
               sr.source_type, sr.source_tier, sr.title, sr.clean_text, sr.published_date
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'CLASSIFIED'
        """
    ).fetchall()

    total = len(rows)
    extracted = failed = 0
    log.info("Stage 4: %d rows to extract", total)

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            source_tier=row["source_tier"] or "",
            deal_type=_fmt(row["deal_type"]),
            spin_split_type=_fmt(row["spin_split_type"]),
            target_type=_fmt(row["target_type"]),
            event_type=_fmt(row["event_type"]),
            target_status=_fmt(row["target_status"]),
            published_date=_fmt(row["published_date"]),
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

        # Merge HC notes into existing notes dict from Stage 3
        nd: dict = {}
        if row["notes"]:
            try:
                nd = json.loads(row["notes"])
                if not isinstance(nd, dict):
                    nd = {}
            except (ValueError, TypeError):
                nd = {}
        hc_notes = result.get("notes")
        if hc_notes:
            nd["hc"] = hc_notes

        t = result.get("target") or {}
        a = result.get("acquirer") or {}
        ps = result.get("parent_seller") or {}
        d = result.get("dates") or {}
        v = result.get("value") or {}
        tf = result.get("target_financials") or {}

        conn.execute(
            """
            UPDATE staging_extraction SET
                status = 'HC_EXTRACTED',
                target_name = ?,  target_domain = ?,  target_ticker = ?,
                target_description = ?,
                acquirer_name = ?,  acquirer_domain = ?,  acquirer_ticker = ?,  acquirer_type = ?,
                acquirer_description = ?,
                acquirer_sponsor_name = ?,
                parent_seller_name = ?,  parent_seller_ticker = ?,
                parent_seller_description = ?,
                pct_acquired = ?,
                announced_date = ?,  closed_date = ?,  signing_date = ?,
                value_amount = ?,  value_currency = ?,  value_type = ?,
                value_type_confidence = ?,  value_qualifier = ?,  per_share_price = ?,
                target_revenue = ?,  target_revenue_period_type = ?,  target_revenue_period_end = ?,
                target_ebitda = ?,  target_ebitda_period_type = ?,  target_ebitda_period_end = ?,
                financials_currency = ?,
                model_confidence = ?,  hc_prompt_version = ?,  notes = ?,
                updated_at = ?
            WHERE extraction_id = ?
            """,
            (
                t.get("name"), t.get("domain"), t.get("ticker"),
                t.get("description"),
                a.get("name"), a.get("domain"), a.get("ticker"), a.get("type"),
                a.get("description"),
                a.get("sponsor_name"),
                ps.get("name"), ps.get("ticker"),
                ps.get("description"),
                (result.get("deal") or {}).get("pct_acquired"),
                d.get("announced_date"), d.get("closed_date"), d.get("signing_date"),
                v.get("amount"), v.get("currency"), v.get("type"),
                v.get("type_confidence"), v.get("qualifier"), v.get("per_share_price"),
                tf.get("revenue_amount"), tf.get("revenue_period_type"), tf.get("revenue_period_end"),
                tf.get("ebitda_amount"), tf.get("ebitda_period_type"), tf.get("ebitda_period_end"),
                tf.get("currency"),
                result.get("model_confidence"), _VERSION, json.dumps(nd),
                datetime.now(timezone.utc).isoformat(),
                eid,
            ),
        )
        conn.commit()
        extracted += 1
        log.info(
            "extraction_id=%d HC_EXTRACTED  target=%r  acquirer=%r  value=%s %s",
            eid, t.get("name"), a.get("name"), v.get("amount"), v.get("currency"),
        )
        time.sleep(_SLEEP)

    log.info("Stage 4 done  total=%d extracted=%d failed=%d", total, extracted, failed)
    return {
        "classified_total": total,
        "hc_extracted": extracted,
        "extracted": extracted,
        "hc_failed": failed,
        "failures": failed,
    }


def _mark_failed(conn: sqlite3.Connection, extraction_id: int) -> None:
    conn.execute(
        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
        (datetime.now(timezone.utc).isoformat(), extraction_id),
    )
    conn.commit()
