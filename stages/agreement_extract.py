"""
Stage 11: agreement_extract

Extracts structured fields from deal-document sections stored in
transaction_document_section (populated by Stage 10, sec_documents).

For each transaction with linked deal-document sections, runs 5 section-
specific prompts against HIGH/MEDIUM-confidence sections from deal documents
(8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A).  Results are written to:
  - transaction_security (per-security-class rows)
  - transaction_record (scalar fields, via priority rules — Drop 3.20b)
  - transaction_field_observation (every extracted value with source attribution — Drop 3.20b)

Multi-source: when multiple source documents contain the same section type,
all are extracted.  For transaction_security, all rows are kept.  For scalar
fields on transaction_record, FIELD_PRIORITY_RULES determine which source type
populates each canonical column; fields without explicit rules use most-recent-
filing-date wins.  Conflicts are logged to aggregation_conflict_log.

Diff surfacing (Drop 3.20b): after all sections are processed for a transaction,
observation_changes_summary on transaction_record is populated with a structured
description of any field that has multiple distinct values across sources.

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

# Per-field source-type priority rules.  First entry in each list is the most
# preferred source type.  Fields not listed here default to most-recent-filing-
# date wins (controlled by section query ORDER BY filing_date ASC, so last write
# = most recent = winner).
_FIELD_PRIORITY_RULES: dict[str, list[str]] = {
    # Termination / go-shop: legal source-of-truth is original agreement
    "target_fee_amount":                ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "target_fee_percentage":            ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "acquirer_fee_amount":              ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "acquirer_fee_percentage":          ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "has_go_shop":                      ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A"],
    "go_shop_period_days":              ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A"],
    # Closing conditions: agreement is legal source
    "has_mac_clause":                   ["8K_EXHIBIT_21", "DEFM14A", "S4"],
    "requires_target_shareholder_vote": ["8K_EXHIBIT_21", "DEFM14A"],
    "target_vote_threshold":            ["8K_EXHIBIT_21", "DEFM14A"],
    # Merger structure: agreement establishes
    "merger_structure":                 ["8K_EXHIBIT_21", "DEFM14A", "S4"],
    # per_share_price / consideration_components: most-recent wins (DEFA14A captures bumps)
    # no explicit rule = default behaviour
}

# Fields in extraction results that are not written as observations
_OBSERVATION_SKIP = frozenset({
    "model_confidence", "notes", "prompt_version",
    "consideration_components", "securities",
})


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
    has_common = has_options = False
    for s in by_key.values():
        shares = s.get("shares_outstanding") or 0
        stype = s.get("security_type", "")
        if stype == "COMMON_STOCK":
            total += shares
            has_common = True
        elif stype == "PREFERRED_STOCK":
            total += shares
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
# Observation writing (Drop 3.20b)
# ---------------------------------------------------------------------------

def _write_observations(
    conn: sqlite3.Connection,
    transaction_id: str,
    extraction_results: dict,
    source_document_id: int,
    source_section_id: int | None,
    filing_date: str | None,
    extraction_prompt_version: str,
) -> None:
    """Write transaction_field_observation rows for all scalar fields in extraction_results.

    Skips metadata fields and array fields (securities, consideration_components).
    Array fields are written as compound field names instead.
    Null values are skipped (we don't observe absence).
    """
    for field_name, field_value in extraction_results.items():
        if field_name in _OBSERVATION_SKIP:
            continue
        if field_value is None:
            continue

        numeric_value: float | None = None
        if isinstance(field_value, bool):
            numeric_value = 1.0 if field_value else 0.0
        elif isinstance(field_value, (int, float)):
            numeric_value = float(field_value)

        conn.execute(
            """
            INSERT INTO transaction_field_observation (
                transaction_id, field_name, field_value, field_value_numeric,
                source_document_id, source_section_id, observed_as_of_date,
                filing_date, extraction_prompt_version
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                transaction_id, field_name, str(field_value), numeric_value,
                source_document_id, source_section_id,
                filing_date, extraction_prompt_version,
            ),
        )

    # Compound: securities → shares_outstanding.{type}[.{class}]
    for sec in extraction_results.get("securities") or []:
        stype = sec.get("security_type", "UNKNOWN")
        sclass = sec.get("security_class")
        shares = sec.get("shares_outstanding")
        if shares is None:
            continue
        fname = f"shares_outstanding.{stype}" + (f".{sclass}" if sclass else "")
        conn.execute(
            """
            INSERT INTO transaction_field_observation (
                transaction_id, field_name, field_value, field_value_numeric,
                source_document_id, source_section_id, observed_as_of_date,
                filing_date, extraction_prompt_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id, fname, str(shares), float(shares),
                source_document_id, source_section_id,
                sec.get("shares_outstanding_as_of"),
                filing_date, extraction_prompt_version,
            ),
        )

    # Compound: consideration_components → consideration.{form}.{attr}
    for comp in extraction_results.get("consideration_components") or []:
        form = comp.get("form", "UNKNOWN")
        for attr in ("per_share_amount", "amount", "exchange_ratio"):
            val = comp.get(attr)
            if val is None:
                continue
            fname = f"consideration.{form}.{attr}"
            numeric = float(val) if isinstance(val, (int, float)) else None
            conn.execute(
                """
                INSERT INTO transaction_field_observation (
                    transaction_id, field_name, field_value, field_value_numeric,
                    source_document_id, source_section_id, observed_as_of_date,
                    filing_date, extraction_prompt_version
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    transaction_id, fname, str(val), numeric,
                    source_document_id, source_section_id,
                    filing_date, extraction_prompt_version,
                ),
            )


# ---------------------------------------------------------------------------
# Observation diff computation (Drop 3.20b)
# ---------------------------------------------------------------------------

def _compute_observation_changes(
    conn: sqlite3.Connection,
    transaction_id: str,
) -> tuple[int, int, str | None]:
    """Compute has_observation_changes, field_count, and summary JSON for a transaction.

    Returns (has_changes_flag, field_count, summary_json or None).
    """
    rows = conn.execute(
        """
        SELECT
            tfo.field_name, tfo.field_value, tfo.field_value_numeric, tfo.filing_date,
            td.filing_type, td.document_title
        FROM transaction_field_observation tfo
        LEFT JOIN transaction_document td ON td.document_id = tfo.source_document_id
        WHERE tfo.transaction_id = ? AND tfo.is_current = 1
        ORDER BY tfo.field_name, tfo.filing_date
        """,
        (transaction_id,),
    ).fetchall()

    by_field: dict[str, list] = {}
    for r in rows:
        by_field.setdefault(r["field_name"], []).append(r)

    fields_with_changes: list[dict] = []
    for field_name, observations in by_field.items():
        distinct_values = {o["field_value"] for o in observations}
        if len(distinct_values) <= 1:
            continue

        ordered = sorted(observations, key=lambda o: o["filing_date"] or "")
        earliest = ordered[0]
        latest = ordered[-1]

        change: dict[str, Any] = {
            "field": field_name,
            "values": [
                {
                    "value": o["field_value"],
                    "filing_date": o["filing_date"],
                    "filing_type": o["filing_type"],
                    "document_title": o["document_title"],
                }
                for o in ordered
            ],
        }

        e_num = earliest["field_value_numeric"]
        l_num = latest["field_value_numeric"]
        if e_num is not None and l_num is not None:
            delta = l_num - e_num
            change["delta"] = delta
            change["delta_pct"] = round((delta / e_num) * 100, 2) if e_num != 0 else None
            if delta > 0:
                change["change_type"] = "INCREASE"
            elif delta < 0:
                change["change_type"] = "DECREASE"
            else:
                change["change_type"] = "IDENTICAL"
        else:
            change["change_type"] = "DIFFERENT"

        fields_with_changes.append(change)

    if not fields_with_changes:
        return 0, 0, None

    return 1, len(fields_with_changes), json.dumps(fields_with_changes)


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
    filing_type: str | None,
    field_priority_state: dict,
    log: Any,
) -> None:
    updates: dict[str, Any] = {}
    if result.get("parent_acquirer_name"):
        updates["acquirer_merger_sub_name"] = result.get("merger_sub_name")
    if result.get("merger_structure"):
        updates["merger_structure"] = result["merger_structure"]
    if not updates:
        return
    _update_transaction_record(
        conn, txn_id, updates, now, "RECITALS", result, log, filing_type, field_priority_state
    )


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
    filing_type: str | None,
    field_priority_state: dict,
    log: Any,
) -> None:
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
    _update_transaction_record(
        conn, txn_id, updates, now, "TERMINATION_FEES", result, log, filing_type, field_priority_state
    )


def _apply_conditions(
    conn: sqlite3.Connection,
    txn_id: str,
    result: dict,
    now: str,
    filing_type: str | None,
    field_priority_state: dict,
    log: Any,
) -> None:
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
    _update_transaction_record(
        conn, txn_id, updates, now, "CONDITIONS_TO_CLOSING", result, log, filing_type, field_priority_state
    )


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
    filing_type: str | None = None,
    field_priority_state: dict | None = None,
) -> None:
    """Apply updates to transaction_record, respecting FIELD_PRIORITY_RULES.

    For fields listed in _FIELD_PRIORITY_RULES, only applies the update if the
    current source's filing_type has equal or better priority than any previously
    applied source for that field.  Fields not in the rules dict always apply
    (last-write wins; caller controls ordering via ASC date query).
    """
    if not updates:
        return

    # Filter updates by priority rules
    allowed: dict[str, Any] = {}
    for col, new_val in updates.items():
        if filing_type and field_priority_state is not None and col in _FIELD_PRIORITY_RULES:
            rule = _FIELD_PRIORITY_RULES[col]
            new_pri = rule.index(filing_type) if filing_type in rule else len(rule)
            current_pri, _ = field_priority_state.get(col, (float("inf"), None))
            if new_pri > current_pri:
                continue  # current source has lower priority — skip
            field_priority_state[col] = (new_pri, filing_type)
        allowed[col] = new_val

    if not allowed:
        return

    existing = conn.execute(
        f"SELECT {', '.join(allowed.keys())} FROM transaction_record WHERE transaction_id=?",
        (txn_id,),
    ).fetchone()

    set_parts = ", ".join(f"{k}=?" for k in allowed)
    values = list(allowed.values()) + [now, txn_id]
    conn.execute(
        f"UPDATE transaction_record SET {set_parts}, updated_at=? WHERE transaction_id=?",
        values,
    )

    if existing:
        for col, new_val in allowed.items():
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
              securities_inserted, no_agreement_linked, agreement_errors,
              observations_written
    """
    log = get_logger("agreement_extract", run_id, level=cfg.log_level)

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
    observations_written = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        txn_id = row["transaction_id"]

        if row["agreement_extraction_status"] == "EXTRACTED":
            log.debug("transaction_id=%s already EXTRACTED — skipping", txn_id)
            continue

        # Gather deal-document sections ordered ASC so most-recent is applied last
        # (last-write wins for non-priority fields)
        section_rows = conn.execute(
            """
            SELECT tds.section_id, tds.section_type, tds.excerpt_text, tds.confidence,
                   td.document_id, td.filing_type, td.filing_date, td.document_title
            FROM transaction_document_section tds
            JOIN transaction_document td ON td.document_id = tds.document_id
            WHERE td.transaction_id = ?
              AND td.filing_type IN ('8K_EXHIBIT_21','DEFM14A','S4','SC_TOT','DEFA14A')
              AND tds.confidence IN ('HIGH','MEDIUM')
            ORDER BY td.filing_date ASC NULLS FIRST, tds.section_id
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

        by_type: dict[str, list] = {}
        for sr in section_rows:
            by_type.setdefault(sr["section_type"], []).append(sr)

        txn_sections = 0
        txn_securities = 0
        txn_observations = 0
        all_securities: list[dict] = []
        field_priority_state: dict[str, tuple[float, str | None]] = {}

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
                prompt_version = f"{prompt_name}:{_VERSIONS[prompt_name]}"

                try:
                    # Write observations before applying to transaction_record
                    obs_before = conn.execute(
                        "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=?",
                        (txn_id,),
                    ).fetchone()[0]

                    _write_observations(
                        conn,
                        transaction_id=txn_id,
                        extraction_results=result,
                        source_document_id=sec_row["document_id"],
                        source_section_id=sec_row["section_id"],
                        filing_date=sec_row["filing_date"],
                        extraction_prompt_version=prompt_version,
                    )

                    obs_after = conn.execute(
                        "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=?",
                        (txn_id,),
                    ).fetchone()[0]
                    txn_observations += obs_after - obs_before

                    # Apply to transaction_record with priority rules
                    if section_type == "RECITALS":
                        _apply_recitals(
                            conn, txn_id, result, now,
                            sec_row["filing_type"], field_priority_state, log,
                        )
                    elif section_type == "CONSIDERATION":
                        _apply_consideration(conn, txn_id, result, now, log)
                    elif section_type == "CAPITALIZATION":
                        n = _apply_capitalization(
                            conn, txn_id, result,
                            sec_row["section_id"], sec_row["document_id"],
                            now, log,
                        )
                        txn_securities += n
                        if isinstance(result.get("securities"), list):
                            all_securities.extend(result["securities"])
                    elif section_type == "TERMINATION_FEES":
                        _apply_termination(
                            conn, txn_id, result, now,
                            sec_row["filing_type"], field_priority_state, log,
                        )
                    elif section_type == "CONDITIONS_TO_CLOSING":
                        _apply_conditions(
                            conn, txn_id, result, now,
                            sec_row["filing_type"], field_priority_state, log,
                        )
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

        # Diluted shares rollup from capitalization observations
        if all_securities:
            diluted, quality = _compute_diluted_shares(all_securities)
            conn.execute(
                "UPDATE transaction_record SET target_total_diluted_shares=?, fully_diluted_calc_quality=?, updated_at=? WHERE transaction_id=?",
                (diluted, quality, now, txn_id),
            )

        # Observation diff computation (Drop 3.20b)
        try:
            has_changes, field_count, summary_json = _compute_observation_changes(conn, txn_id)
            conn.execute(
                """
                UPDATE transaction_record
                SET has_observation_changes=?, observation_changes_field_count=?,
                    observation_changes_summary=?, updated_at=?
                WHERE transaction_id=? AND is_current=1
                """,
                (has_changes, field_count, summary_json, now, txn_id),
            )
        except Exception as exc:
            log.warning("transaction_id=%s observation diff computation failed: %s", txn_id, exc)

        # Final status
        status = "EXTRACTED" if txn_sections > 0 else "NO_AGREEMENT_LINKED"
        conn.execute(
            "UPDATE transaction_record SET agreement_extraction_status=?, updated_at=? WHERE transaction_id=?",
            (status, now, txn_id),
        )
        conn.commit()

        sections_extracted += txn_sections
        securities_inserted += txn_securities
        observations_written += txn_observations
        processed += 1

        log.info(
            "transaction_id=%s status=%s sections=%d securities=%d observations=%d",
            txn_id, status, txn_sections, txn_securities, txn_observations,
        )

    log.info(
        "Stage 11 done  total=%d processed=%d sections=%d securities=%d "
        "observations=%d no_agreement=%d errors=%d",
        total, processed, sections_extracted, securities_inserted,
        observations_written, no_agreement, error_count,
    )
    return {
        "transactions_total": total,
        "transactions_processed": processed,
        "sections_extracted": sections_extracted,
        "securities_inserted": securities_inserted,
        "observations_written": observations_written,
        "no_agreement_linked": no_agreement,
        "agreement_errors": error_count,
    }
