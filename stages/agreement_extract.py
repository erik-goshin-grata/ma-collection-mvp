"""
Stage 11: agreement_extract

Extracts structured fields from deal-document sections stored in
transaction_document_section (populated by Stage 10, sec_documents).

For each transaction with linked deal-document sections, runs 5 section-
specific prompts against HIGH/MEDIUM-confidence sections from deal documents
(8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A).  Results are written to
transaction_security (per-security-class rows) and transaction_record (scalar
fields).  All extractions are attributed to their source section and document.

Multi-source: when multiple source documents contain the same section type
(e.g., both 8K_EXHIBIT_21 and DEFM14A have a CAPITALIZATION section),
all are extracted.  For transaction_security, all rows are kept with source
attribution.  For scalar fields on transaction_record, most-recent-source-wins
(by filing_date); conflicts are logged to aggregation_conflict_log.

Stage placement: after Stage 10 (sec_documents), before Stage 12 (summarize)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from config import Config
from logger import get_logger
from prompts.base import (
    PromptFailure,
    call_prompt,
    load_prompt_file,
    log_prompt_failure,
    register_prompt_version,
)

_DEAL_DOC_TYPES = frozenset(["8K_EXHIBIT_21", "DEFM14A", "S4", "SC_TOT", "DEFA14A"])
_CONFIDENCE_OK = frozenset(["HIGH", "MEDIUM"])

_SECTION_PROMPT_MAP = {
    "RECITALS": "agreement_recitals",
    "CONSIDERATION": "agreement_consideration",
    "CAPITALIZATION": "agreement_capitalization",
    "TERMINATION_FEES": "agreement_termination",
    "CONDITIONS_TO_CLOSING": "agreement_conditions",
}

_VERSIONS = {
    "agreement_recitals": "0.1",
    "agreement_consideration": "0.1",
    "agreement_capitalization": "0.1",
    "agreement_termination": "0.1",
    "agreement_conditions": "0.1",
}

_SLEEP = 1.0  # between LLM calls


# ---------------------------------------------------------------------------
# Diluted shares computation
# ---------------------------------------------------------------------------

def _compute_diluted_shares(securities: list[dict]) -> tuple[int | None, str]:
    """Compute target_total_diluted_shares from a list of security dicts.

    Uses most-recent observation per (security_type, security_class).
    Returns (count, quality_flag) where quality is COMPLETE | PARTIAL | NOT_AVAILABLE.
    """
    if not securities:
        return None, "NOT_AVAILABLE"

    by_key: dict[tuple, dict] = {}
    for s in securities:
        key = (s.get("security_type"), s.get("security_class"))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = s
        else:
            s_date = s.get("shares_outstanding_as_of") or ""
            e_date = existing.get("shares_outstanding_as_of") or ""
            if s_date > e_date:
                by_key[key] = s

    total = 0
    has_common = has_options = has_preferred = False
    for s in by_key.values():
        shares = s.get("shares_outstanding") or 0
        stype = s.get("security_type", "")
        if stype == "COMMON_STOCK":
            total += shares
            has_common = True
        elif stype == "PREFERRED_STOCK":
            total += shares
            has_preferred = True
        elif stype in ("OPTIONS", "RSU", "PSU", "DSU", "SAR", "WARRANT"):
            total += shares
            has_options = True

    if has_common and has_options:
        quality = "COMPLETE"
    elif has_common or total > 0:
        quality = "PARTIAL"
    else:
        quality = "NOT_AVAILABLE"

    return total if total > 0 else None, quality


# ---------------------------------------------------------------------------
# Section-prompt dispatchers
# ---------------------------------------------------------------------------

def _call_section_prompt(
    prompt_name: str,
    section_text: str,
    conn: sqlite3.Connection,
    cfg: Config,
    run_id: str,
    log: Any,
) -> dict | None:
    prompt_version = f"{prompt_name}:{_VERSIONS[prompt_name]}"
    prompt = load_prompt_file(prompt_name)
    user = prompt["user_template"].format(
        section_text=section_text,
        prompt_version=prompt_version,
    )
    try:
        result = call_prompt(
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            user_prompt=user,
            system_prompt=prompt["system"],
            model="opus",
            temperature=0.0,
            max_tokens=2048,
            cfg=cfg,
            conn=conn,
            run_id=run_id,
            log=log,
        )
    except PromptFailure as exc:
        log.warning("%s prompt failed: %s", prompt_name, exc)
        log_prompt_failure(
            conn,
            source_raw_id=None,
            extraction_id=None,
            stage=prompt_name,
            failure_type=exc.failure_type,
            raw_response=exc.raw_response,
            error_message=str(exc),
            prompt_version=prompt_version,
            run_id=run_id,
        )
        return None
    return result


# ---------------------------------------------------------------------------
# Result appliers
# ---------------------------------------------------------------------------

def _apply_recitals(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    now: str,
    log: Any,
) -> None:
    """Update transaction_record with recitals extraction results."""
    updates: dict[str, Any] = {}
    if result.get("parent_acquirer_name"):
        updates["acquirer_merger_sub_name"] = result.get("merger_sub_name")
    if result.get("merger_structure"):
        updates["merger_structure"] = result["merger_structure"]
    if not updates:
        return
    _update_transaction_record(conn, txn_id, updates, now, "RECITALS", result, log)


def _apply_consideration(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    now: str,
    log: Any,
) -> None:
    """Update consideration_components on transaction_record when agreement provides richer data."""
    components = result.get("consideration_components")
    if not isinstance(components, list) or not components:
        return
    existing = conn.execute(
        "SELECT consideration_components FROM transaction_record WHERE transaction_id=?",
        (txn_id,),
    ).fetchone()
    existing_json = existing["consideration_components"] if existing else None

    agreement_json = json.dumps(components)
    if existing_json and existing_json != agreement_json:
        _log_conflict(conn, txn_id, "consideration_components", existing_json, agreement_json, log)

    conn.execute(
        "UPDATE transaction_record SET consideration_components=?, updated_at=? WHERE transaction_id=?",
        (agreement_json, now, txn_id),
    )


def _apply_capitalization(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    section_id: int,
    document_id: int,
    now: str,
    log: Any,
) -> int:
    """Insert transaction_security rows from capitalization result. Returns count inserted."""
    securities = result.get("securities")
    if not isinstance(securities, list):
        return 0
    inserted = 0
    for sec in securities:
        if not isinstance(sec, dict) or not sec.get("security_type"):
            continue
        conn.execute(
            """
            INSERT INTO transaction_security (
                transaction_id, security_type, security_type_as_reported, security_class,
                shares_outstanding, shares_outstanding_as_of, weighted_avg_strike_price,
                consideration_treatment, consideration_per_share, consideration_currency,
                notes, is_current,
                extraction_source_section_id, extraction_source_document_id, extracted_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)
            """,
            (
                txn_id,
                sec.get("security_type"),
                sec.get("security_type_as_reported"),
                sec.get("security_class"),
                sec.get("shares_outstanding"),
                sec.get("shares_outstanding_as_of"),
                sec.get("weighted_avg_strike_price"),
                sec.get("consideration_treatment"),
                sec.get("consideration_per_share"),
                sec.get("consideration_currency"),
                (sec.get("notes") or "")[:500] or None,
                section_id,
                document_id,
                now,
            ),
        )
        inserted += 1
    return inserted


def _apply_termination(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    now: str,
    log: Any,
) -> None:
    """Update transaction_record termination fee fields."""
    updates: dict[str, Any] = {}
    if result.get("target_termination_fee") is not None:
        updates["target_fee_amount"] = result["target_termination_fee"]
    if result.get("target_termination_fee_pct") is not None:
        updates["target_fee_percentage"] = result["target_termination_fee_pct"]
    if result.get("acquirer_termination_fee") is not None:
        updates["acquirer_fee_amount"] = result["acquirer_termination_fee"]
    if result.get("acquirer_termination_fee_pct") is not None:
        updates["acquirer_fee_percentage"] = result["acquirer_termination_fee_pct"]
    if result.get("has_go_shop") is not None:
        updates["has_go_shop"] = 1 if result["has_go_shop"] else 0
    if result.get("go_shop_period_days") is not None:
        updates["go_shop_period_days"] = result["go_shop_period_days"]
    if not updates:
        return
    _update_transaction_record(conn, txn_id, updates, now, "TERMINATION_FEES", result, log)


def _apply_conditions(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    now: str,
    log: Any,
) -> None:
    """Update transaction_record closing conditions fields."""
    updates: dict[str, Any] = {}
    if result.get("has_mac_clause") is not None:
        updates["has_mac_clause"] = 1 if result["has_mac_clause"] else 0
    if result.get("requires_target_shareholder_vote") is not None:
        updates["requires_target_shareholder_vote"] = (
            1 if result["requires_target_shareholder_vote"] else 0
        )
    if result.get("target_vote_threshold"):
        updates["target_vote_threshold"] = result["target_vote_threshold"]
    if result.get("closing_conditions_summary"):
        updates["closing_conditions_summary"] = result["closing_conditions_summary"][:2000]
    if not updates:
        return
    _update_transaction_record(conn, txn_id, updates, now, "CONDITIONS_TO_CLOSING", result, log)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _update_transaction_record(
    conn: sqlite3.Connection,
    txn_id: str,
    updates: dict[str, Any],
    now: str,
    section_type: str,
    result: dict,
    log: Any,
) -> None:
    if not updates:
        return
    existing = conn.execute(
        f"SELECT {', '.join(updates.keys())} FROM transaction_record WHERE transaction_id=?",
        (txn_id,),
    ).fetchone()

    set_parts = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [now, txn_id]
    conn.execute(
        f"UPDATE transaction_record SET {set_parts}, updated_at=? WHERE transaction_id=?",
        values,
    )

    if existing:
        for col, new_val in updates.items():
            old_val = existing[col] if col in existing.keys() else None
            if old_val is not None and str(old_val) != str(new_val):
                _log_conflict(conn, txn_id, col, str(old_val), str(new_val), log)


def _log_conflict(
    conn: sqlite3.Connection,
    txn_id: str,
    field_name: str,
    old_val: str,
    new_val: str,
    log: Any,
) -> None:
    """Log a field value change from agreement extraction to aggregation_conflict_log."""
    try:
        obs = [
            {"source": "PR_extraction", "value": old_val},
            {"source": "agreement_extraction", "value": new_val},
        ]
        conn.execute(
            """
            INSERT INTO aggregation_conflict_log
                (transaction_id, field_name, observations_json, chosen_value,
                 conflict_severity, flagged_for_review, reasoning, prompt_version)
            VALUES (?, ?, ?, ?, 'MINOR', 0, 'Agreement extraction superseded PR extraction', 'agreement_extract:auto')
            """,
            (txn_id, field_name, json.dumps(obs), str(new_val)),
        )
    except Exception as exc:
        log.debug("conflict log write failed for %s/%s: %s", txn_id, field_name, exc)


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract structured fields from stored deal-document sections.

    Returns
    -------
    dict
        Keys: transactions_total, transactions_processed, sections_extracted,
              securities_inserted, no_agreement_linked, agreement_errors
    """
    log = get_logger("agreement_extract", run_id, level=cfg.log_level)

    # Register all prompt versions
    for pname, ver in _VERSIONS.items():
        try:
            p = load_prompt_file(pname)
            register_prompt_version(conn, pname, ver, p["file_hash"])
        except Exception as exc:
            log.warning("Could not load/register prompt %s: %s", pname, exc)

    rows = conn.execute(
        """
        SELECT tr.transaction_id, tr.agreement_extraction_status
        FROM transaction_record tr
        WHERE tr.is_current = 1
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 11: %d transactions to process for agreement extraction", total)

    processed = sections_extracted = securities_inserted = no_agreement = error_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        txn_id = row["transaction_id"]

        if row["agreement_extraction_status"] == "EXTRACTED":
            log.debug("transaction_id=%s already EXTRACTED — skipping", txn_id)
            continue

        # Gather deal-document sections for this transaction
        section_rows = conn.execute(
            """
            SELECT tds.section_id, tds.section_type, tds.excerpt_text, tds.confidence,
                   td.document_id, td.filing_type, td.filing_date
            FROM transaction_document_section tds
            JOIN transaction_document td ON td.document_id = tds.document_id
            WHERE td.transaction_id = ?
              AND td.filing_type IN ('8K_EXHIBIT_21','DEFM14A','S4','SC_TOT','DEFA14A')
              AND tds.confidence IN ('HIGH','MEDIUM')
            ORDER BY td.filing_date DESC NULLS LAST, tds.section_id
            """,
            (txn_id,),
        ).fetchall()

        if not section_rows:
            conn.execute(
                "UPDATE transaction_record SET agreement_extraction_status='NO_AGREEMENT_LINKED', updated_at=? WHERE transaction_id=?",
                (now, txn_id),
            )
            conn.commit()
            no_agreement += 1
            log.debug("transaction_id=%s no deal-doc sections — NO_AGREEMENT_LINKED", txn_id)
            continue

        # Group by section_type
        by_type: dict[str, list] = {}
        for sr in section_rows:
            by_type.setdefault(sr["section_type"], []).append(sr)

        txn_sections = 0
        txn_securities = 0
        all_securities: list[dict] = []

        for section_type, prompt_name in _SECTION_PROMPT_MAP.items():
            sections = by_type.get(section_type, [])
            if not sections:
                continue

            for sec_row in sections:
                excerpt = sec_row["excerpt_text"]
                if not excerpt or not excerpt.strip():
                    continue

                log.debug(
                    "transaction_id=%s section=%s doc=%s confidence=%s",
                    txn_id, section_type, sec_row["document_id"], sec_row["confidence"],
                )

                result = _call_section_prompt(
                    prompt_name, excerpt, conn, cfg, run_id, log
                )
                time.sleep(_SLEEP)

                if result is None:
                    error_count += 1
                    continue

                txn_sections += 1

                try:
                    if section_type == "RECITALS":
                        _apply_recitals(conn, txn_id, result, now, log)
                    elif section_type == "CONSIDERATION":
                        _apply_consideration(conn, txn_id, result, now, log)
                    elif section_type == "CAPITALIZATION":
                        n = _apply_capitalization(
                            conn, txn_id, result,
                            sec_row["section_id"], sec_row["document_id"],
                            now, log,
                        )
                        txn_securities += n
                        # Accumulate for diluted share rollup
                        if isinstance(result.get("securities"), list):
                            all_securities.extend(result["securities"])
                    elif section_type == "TERMINATION_FEES":
                        _apply_termination(conn, txn_id, result, now, log)
                    elif section_type == "CONDITIONS_TO_CLOSING":
                        _apply_conditions(conn, txn_id, result, now, log)
                    conn.commit()
                except Exception as exc:
                    log.warning(
                        "transaction_id=%s section=%s apply error: %s — continuing",
                        txn_id, section_type, exc,
                    )
                    error_count += 1
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        # Compute diluted shares from all capitalization observations
        if all_securities:
            diluted, quality = _compute_diluted_shares(all_securities)
            conn.execute(
                "UPDATE transaction_record SET target_total_diluted_shares=?, fully_diluted_calc_quality=?, updated_at=? WHERE transaction_id=?",
                (diluted, quality, now, txn_id),
            )

        # Set final status
        status = "EXTRACTED" if txn_sections > 0 else "NO_AGREEMENT_LINKED"
        conn.execute(
            "UPDATE transaction_record SET agreement_extraction_status=?, updated_at=? WHERE transaction_id=?",
            (status, now, txn_id),
        )
        conn.commit()

        sections_extracted += txn_sections
        securities_inserted += txn_securities
        processed += 1

        log.info(
            "transaction_id=%s status=%s sections=%d securities=%d",
            txn_id, status, txn_sections, txn_securities,
        )

    log.info(
        "Stage 11 done  total=%d processed=%d sections=%d securities=%d "
        "no_agreement=%d errors=%d",
        total, processed, sections_extracted, securities_inserted,
        no_agreement, error_count,
    )
    return {
        "transactions_total": total,
        "transactions_processed": processed,
        "sections_extracted": sections_extracted,
        "securities_inserted": securities_inserted,
        "no_agreement_linked": no_agreement,
        "agreement_errors": error_count,
    }
