"""
Stage 4b: funding_hc_extract

Runs the funding high-confidence extraction prompt on every staging_extraction
row with status = CLASSIFIED and v2_event_type IN (VC_ROUND, GROWTH_EQUITY,
VENTURE_DEBT).

Creates or updates staging_extraction with round fields and writes investor
rows to staging_investor. Mirrors the structure of high_confidence_extract.py.

On success: status → HC_EXTRACTED, round/investor fields populated.
On failure: status → PROMPT_FAILED.

Spec references: prompts/funding_hc_extraction.md, docs/funding_path_design.md
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "funding_hc_extraction"
_VERSION = "0.1"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_FUNDING_EVENT_TYPES = frozenset({"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"})

_REQUIRED_KEYS = frozenset({
    "company", "investors", "round", "dates",
    "financials_disclosure_status", "model_confidence",
})

_VALID_FINANCIALS_DISCLOSURE = frozenset({"DISCLOSED", "UNDISCLOSED", "UNKNOWN"})
_VALID_INVESTOR_TYPES = frozenset({
    "vc_firm", "growth_equity", "corporate_vc", "family_office",
    "hedge_fund", "sovereign_wealth_fund", "angel", "accelerator",
    "lender", "unknown",
})

_SLEEP = 1.0


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"

    fds = result.get("financials_disclosure_status")
    if fds not in _VALID_FINANCIALS_DISCLOSURE:
        return f"invalid financials_disclosure_status: {fds!r}"

    investors = result.get("investors")
    if not isinstance(investors, list):
        return "investors must be an array"

    for inv in investors:
        itype = inv.get("investor_type")
        if itype is not None and itype not in _VALID_INVESTOR_TYPES:
            return f"invalid investor_type: {itype!r}"

    return None


def _fmt(val) -> str:
    return str(val) if val is not None else ""


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Run funding HC extraction on all CLASSIFIED funding rows.

    Returns
    -------
    dict
        Keys: funding_total, funding_extracted, funding_failed
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               se.v2_event_type, se.deal_type,
               se.event_history_type, se.event_type,
               se.notes, se.dt_prompt_version,
               sr.source_type, sr.source_tier,
               sr.title, sr.clean_text, sr.published_date
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'CLASSIFIED'
          AND COALESCE(se.v2_event_type, se.deal_type) IN ('VC_ROUND', 'GROWTH_EQUITY', 'VENTURE_DEBT')
        """
    ).fetchall()

    total = len(rows)
    extracted = failed = 0
    log.info("Stage 4b: %d funding rows to extract", total)

    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        base_nd: dict = {}
        if row["notes"]:
            try:
                base_nd = json.loads(row["notes"])
            except (ValueError, TypeError):
                pass

        v2_event_type = row["v2_event_type"] or row["deal_type"]
        event_history_type = row["event_history_type"] or row["event_type"] or "ANNOUNCED"

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            source_tier=row["source_tier"] or "",
            v2_event_type=_fmt(v2_event_type),
            event_history_type=_fmt(event_history_type),
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
                model="sonnet",
                temperature=0.0,
                max_tokens=2048,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                source_raw_id=row["source_raw_id"],
                log=log,
            )
        except PromptFailure as exc:
            log.warning("source_raw_id=%d prompt failed: %s", row["source_raw_id"], exc)
            conn.execute(
                "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                (now, eid),
            )
            conn.commit()
            failed += 1
            time.sleep(_SLEEP)
            continue

        transactions = result.get("transactions")
        if not transactions or not isinstance(transactions, list):
            log.warning("source_raw_id=%d no transactions in result — PROMPT_FAILED", row["source_raw_id"])
            conn.execute(
                "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                (now, eid),
            )
            conn.commit()
            failed += 1
            time.sleep(_SLEEP)
            continue

        multi_total = len(transactions)

        for i, txn in enumerate(transactions):
            err = _validate(txn)
            if err:
                log.warning("source_raw_id=%d txn[%d] schema violation: %s", row["source_raw_id"], i, err)
                if i == 0:
                    conn.execute(
                        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                        (now, eid),
                    )
                    conn.commit()
                    failed += 1
                break

            co = txn.get("company") or {}
            rd = txn.get("round") or {}
            dt = txn.get("dates") or {}
            nd = dict(base_nd)
            hc_notes = txn.get("notes")
            if hc_notes:
                nd["hc"] = hc_notes

            round_params = (
                co.get("name"), co.get("domain"), co.get("ticker"),
                co.get("description"),
                v2_event_type,
                rd.get("label"),
                rd.get("size"),
                rd.get("currency"),
                rd.get("pre_money_valuation"),
                rd.get("post_money_valuation"),
                rd.get("valuation_currency"),
                rd.get("facility_size"),
                rd.get("total_raised_to_date"),
                1 if rd.get("is_extension_round") else 0,
                1 if rd.get("is_down_round") else 0,
                1 if rd.get("is_bridge_round") else 0,
                dt.get("announced_date"),
                dt.get("announced_date_precision"),
                dt.get("closed_date"),
                dt.get("closed_date_precision"),
                txn.get("financials_disclosure_status"),
                txn.get("consideration_type"),
                txn.get("model_confidence"),
                _VERSION,
                json.dumps(nd) if nd else None,
                i,
                multi_total,
            )

            if i == 0:
                # Update the existing staging_extraction row
                conn.execute(
                    """
                    UPDATE staging_extraction SET
                        status = 'HC_EXTRACTED',
                        target_name = ?,
                        target_domain = ?,
                        target_ticker = ?,
                        target_description = ?,
                        v2_event_type = ?,
                        round_label = ?,
                        round_size = ?,
                        valuation_currency = ?,
                        pre_money_valuation = ?,
                        post_money_valuation = ?,
                        valuation_currency = ?,
                        facility_size = ?,
                        total_raised_to_date = ?,
                        is_extension_round = ?,
                        is_down_round = ?,
                        is_bridge_round = ?,
                        announced_date = ?,
                        announced_date_precision = ?,
                        closed_date = ?,
                        closed_date_precision = ?,
                        financials_disclosure_status = ?,
                        consideration_type = COALESCE(consideration_type, ?),
                        model_confidence = ?,
                        hc_prompt_version = ?,
                        notes = ?,
                        multi_transaction_index = ?,
                        multi_transaction_total = ?,
                        updated_at = ?
                    WHERE extraction_id = ?
                    """,
                    round_params + (now, eid),
                )
            else:
                # Insert a new row for additional transactions from same source
                cur = conn.execute(
                    """
                    INSERT INTO staging_extraction (
                        source_raw_id, status,
                        deal_type, v2_event_type,
                        event_type, event_history_type,
                        target_name, target_domain, target_ticker, target_description,
                        round_label, round_size, valuation_currency,
                        pre_money_valuation, post_money_valuation,
                        facility_size, total_raised_to_date,
                        is_extension_round, is_down_round, is_bridge_round,
                        announced_date, announced_date_precision,
                        closed_date, closed_date_precision,
                        financials_disclosure_status, consideration_type,
                        model_confidence, hc_prompt_version, notes,
                        dt_prompt_version,
                        multi_transaction_index, multi_transaction_total,
                        created_at, updated_at
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        row["source_raw_id"], "HC_EXTRACTED",
                        v2_event_type, v2_event_type,
                        event_history_type, event_history_type,
                    ) + round_params + (row["dt_prompt_version"], now, now),
                )
                new_eid = cur.lastrowid

            # Write investors to staging_investor
            target_eid = eid if i == 0 else (cur.lastrowid if i > 0 else eid)
            investors = txn.get("investors") or []
            for inv in investors:
                conn.execute(
                    """
                    INSERT INTO staging_investor (
                        extraction_id, name, domain, investor_type,
                        is_lead, lead_investor_rank,
                        investment_amount, investment_currency,
                        is_new_investor, is_existing_investor,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_eid,
                        inv.get("name"),
                        inv.get("domain"),
                        inv.get("investor_type"),
                        1 if inv.get("is_lead") else 0,
                        inv.get("lead_investor_rank"),
                        inv.get("investment_amount"),
                        inv.get("investment_currency"),
                        1 if inv.get("is_new_investor") else 0,
                        1 if inv.get("is_existing_investor") else 0,
                        now,
                    ),
                )

        conn.commit()
        extracted += 1
        log.info(
            "source_raw_id=%d FUNDING_HC_EXTRACTED  v2_event_type=%s  company=%r  investors=%d",
            row["source_raw_id"], v2_event_type,
            (transactions[0].get("company") or {}).get("name"),
            len(transactions[0].get("investors") or []),
        )
        time.sleep(_SLEEP)

    log.info(
        "Stage 4b done  total=%d extracted=%d failed=%d",
        total, extracted, failed,
    )
    return {
        "funding_total": total,
        "funding_extracted": extracted,
        "funding_failed": failed,
        "failures": failed,
    }
