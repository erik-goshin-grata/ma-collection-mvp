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
fields on transaction_record, FIELD_FILING_TYPE_PRIORITY determines which
source type populates each canonical column; fields without explicit rules use
most-recent-filing-date wins.  Conflicts are logged to aggregation_conflict_log.

Diff surfacing (Drop 3.20b): after all sections are processed for a transaction,
observation_changes_summary on transaction_record is populated with a structured
description of any field that has multiple distinct values across sources.

Stage placement: after Stage 10 (sec_documents), before Stage 12 (summarize)
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from config import Config
from lib.field_priority import FIELD_FILING_TYPE_PRIORITY, filing_type_rank
from lib.observation_writer import extract_agreement_dated_as_of, insert_observation
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
    "agreement_recitals": "0.3",
    "agreement_consideration": "0.2",
    "agreement_capitalization": "0.2",
    "agreement_termination": "0.2",
    "agreement_conditions": "0.2",
}

_SLEEP = 1.0  # between LLM calls

# Fields in extraction results that are not written as observations
_OBSERVATION_SKIP = frozenset({
    "model_confidence", "notes", "prompt_version",
    "consideration_components", "securities",
})

# Closed-vocab field values treated as non-observations (skip insert + canonical)
_OBSERVATION_REJECT_VALUES: dict[str, frozenset[str]] = {
    "merger_structure": frozenset({"UNKNOWN"}),
}

# Entity-name fields — filtered for defined-term references and draft placeholders
_ENTITY_NAME_FIELDS = frozenset({"parent_acquirer_name", "target_name", "merger_sub_name", "acquirer_name"})
_DEFINED_TERM_RE = re.compile(
    r"^(Parent|Company|Purchaser|Seller|Buyer|Target|Acquirer|SPAC|Sponsor|"
    r"Issuer|Holdco|Topco|Bidco|Merger\s+Sub|Sub|Acquireco|Acquisitionco|AcquireCo)$",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(r"\[|\]|…|\[[A-Z][^\]]*\]", re.IGNORECASE)

# Maps transaction_record column → observation field_name that populates it.
# Used by _clear_stale_canonical_fields() to NULL canonical fields whose current
# observation set is empty (e.g. after soft-delete on re-run or filter rejection).
# consideration_components is excluded: it has no direct observation field_name
# (individual components are stored as consideration.{form}.{attr}).
_CANONICAL_FIELD_OBSERVATION_MAP: dict[str, str] = {
    "merger_structure":                  "merger_structure",
    "acquirer_merger_sub_name":          "merger_sub_name",
    "target_fee_amount":                 "target_termination_fee",
    "target_fee_percentage":             "target_termination_fee_pct",
    "acquirer_fee_amount":               "acquirer_termination_fee",
    "acquirer_fee_percentage":           "acquirer_termination_fee_pct",
    "has_go_shop":                       "has_go_shop",
    "go_shop_period_days":               "go_shop_period_days",
    "has_mac_clause":                    "has_mac_clause",
    "requires_target_shareholder_vote":  "requires_target_shareholder_vote",
    "target_vote_threshold":             "target_vote_threshold",
    "closing_conditions_summary":        "closing_conditions_summary",
}


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
# Canonical field cleanup (Drop 3.22c)
# ---------------------------------------------------------------------------

def _clear_stale_canonical_fields(
    conn: sqlite3.Connection,
    txn_id: str,
    now: str,
    log: Any,
) -> None:
    """NULL canonical transaction_record fields whose observation set is empty.

    When observations are soft-deleted (e.g. on re-run after a filter change)
    and no new observations replace them, the corresponding TR column retains its
    stale value. This pass makes canonical state consistent with the current
    observation set. Runs once per transaction after all documents are processed.
    """
    current_obs_fields: set[str] = {
        row[0] for row in conn.execute(
            "SELECT DISTINCT field_name FROM transaction_field_observation "
            "WHERE transaction_id=? AND is_current=1",
            (txn_id,),
        ).fetchall()
    }

    to_null = [
        tr_col
        for tr_col, obs_field in _CANONICAL_FIELD_OBSERVATION_MAP.items()
        if obs_field not in current_obs_fields
    ]

    # consideration_components: NULL when no consideration.* observations exist
    has_consideration = any(f.startswith("consideration.") for f in current_obs_fields)
    if not has_consideration:
        to_null.append("consideration_components")

    if not to_null:
        return

    set_parts = ", ".join(f"{col}=NULL" for col in to_null)
    conn.execute(
        f"UPDATE transaction_record SET {set_parts}, updated_at=? WHERE transaction_id=?",
        (now, txn_id),
    )
    log.debug(
        "transaction_id=%s: nulled %d stale canonical fields: %s",
        txn_id, len(to_null), to_null,
    )


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
    filing_type: str | None = None,
    model_confidence: str | None = None,
    agreement_dated_as_of: str | None = None,
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
        # Reject closed-vocab non-observations (UNKNOWN treated as absent)
        if field_name in _OBSERVATION_REJECT_VALUES and str(field_value) in _OBSERVATION_REJECT_VALUES[field_name]:
            continue
        # Reject defined-term and placeholder values for entity-name fields
        if field_name in _ENTITY_NAME_FIELDS:
            sv = str(field_value).strip()
            if _DEFINED_TERM_RE.match(sv) or _PLACEHOLDER_RE.search(sv):
                continue

        insert_observation(
            conn,
            transaction_id=transaction_id,
            field_name=field_name,
            field_value=field_value,
            source_document_id=source_document_id,
            source_section_id=source_section_id,
            source_type=filing_type,
            source_tier="T1",
            model_confidence=model_confidence,
            filing_type=filing_type,
            agreement_dated_as_of=agreement_dated_as_of,
            filing_date=filing_date,
            extraction_prompt_version=extraction_prompt_version,
            observation_source_stage="AGREEMENT_EXTRACT",
        )

    # Compound: securities → shares_outstanding.{type}[.{class}]
    for sec in extraction_results.get("securities") or []:
        stype = sec.get("security_type", "UNKNOWN")
        sclass = sec.get("security_class")
        shares = sec.get("shares_outstanding")
        if shares is None:
            continue
        fname = f"shares_outstanding.{stype}" + (f".{sclass}" if sclass else "")
        insert_observation(
            conn,
            transaction_id=transaction_id,
            field_name=fname,
            field_value=shares,
            source_document_id=source_document_id,
            source_section_id=source_section_id,
            source_type=filing_type,
            source_tier="T1",
            model_confidence=model_confidence,
            filing_type=filing_type,
            agreement_dated_as_of=agreement_dated_as_of,
            observed_as_of_date=sec.get("shares_outstanding_as_of"),
            filing_date=filing_date,
            extraction_prompt_version=extraction_prompt_version,
            observation_source_stage="AGREEMENT_EXTRACT",
        )

    # Compound: consideration_components → consideration.{form}.{attr}
    for comp in extraction_results.get("consideration_components") or []:
        form = comp.get("form", "UNKNOWN")
        for attr in ("per_share_amount", "amount", "exchange_ratio"):
            val = comp.get(attr)
            if val is None:
                continue
            fname = f"consideration.{form}.{attr}"
            insert_observation(
                conn,
                transaction_id=transaction_id,
                field_name=fname,
                field_value=val,
                source_document_id=source_document_id,
                source_section_id=source_section_id,
                source_type=filing_type,
                source_tier="T1",
                model_confidence=model_confidence,
                filing_type=filing_type,
                agreement_dated_as_of=agreement_dated_as_of,
                filing_date=filing_date,
                extraction_prompt_version=extraction_prompt_version,
                observation_source_stage="AGREEMENT_EXTRACT",
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
        reject = _OBSERVATION_REJECT_VALUES.get(field_name, frozenset())
        # Exclude rejected values (e.g. UNKNOWN) from divergence — treat as non-observations
        live_obs = [o for o in observations if o["field_value"] not in reject]
        distinct_values = {o["field_value"] for o in live_obs}
        if len(distinct_values) <= 1:
            continue

        ordered = sorted(live_obs, key=lambda o: o["filing_date"] or "")
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
    # prompt_version is deliberately NOT formatted in. Provenance is caller-owned: it is
    # passed to call_prompt below and stamped on the row, and the model is never asked to
    # carry it. This family was the only one that genuinely supplied the value, and even
    # here nothing compared what came back.
    user = prompt["user_template"].format(section_text=section_text)
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
    # Only write merger_sub_name to TR if it's a real legal entity name (not defined-term)
    msub = (result.get("merger_sub_name") or "").strip()
    if msub and not _DEFINED_TERM_RE.match(msub) and not _PLACEHOLDER_RE.search(msub):
        updates["acquirer_merger_sub_name"] = msub
    # Only write merger_structure if it's a real value (UNKNOWN is a non-observation)
    merger_struct = result.get("merger_structure")
    if merger_struct and merger_struct != "UNKNOWN":
        updates["merger_structure"] = merger_struct
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

    # Soft-delete prior rows for this document before reinserting. Inside the
    # per-document SAVEPOINT doc_extract (Drop 3.25) — rollback on any section
    # failure atomically restores these rows to is_current=1.
    conn.execute(
        "UPDATE transaction_security SET is_current=0 "
        "WHERE extraction_source_document_id=? AND is_current=1",
        (document_id,),
    )

    inserted = 0
    for sec in securities:
        if not isinstance(sec, dict) or not sec.get("security_type"):
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO transaction_security (
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
    """Apply updates to transaction_record, respecting FIELD_FILING_TYPE_PRIORITY.

    For fields listed in FIELD_FILING_TYPE_PRIORITY, only applies the update if the
    current source's filing_type has equal or better priority than any previously
    applied source for that field.  Fields not in the rules dict always apply
    (last-write wins; caller controls ordering via ASC date query).
    """
    if not updates:
        return

    # Filter updates by priority rules
    allowed: dict[str, Any] = {}
    for col, new_val in updates.items():
        if filing_type and field_priority_state is not None and col in FIELD_FILING_TYPE_PRIORITY:
            new_pri = filing_type_rank(col, filing_type)
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

    # Select transactions that have at least one unextracted agreement-class document.
    # This per-document gate (vs. the old transaction-level EXTRACTED skip) allows
    # re-processing when new documents are added to already-EXTRACTED transactions.
    rows = conn.execute(
        """
        SELECT DISTINCT tr.transaction_id
        FROM transaction_record tr
        JOIN transaction_document td ON td.transaction_id = tr.transaction_id
        WHERE tr.is_current = 1
          AND td.is_current = 1
          AND td.filing_type IN ('8K_EXHIBIT_21','DEFM14A','S4','SC_TOT','DEFA14A')
          AND td.agreement_extracted_at IS NULL
        ORDER BY tr.announced_date DESC NULLS LAST
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 11: %d transactions with unextracted agreement docs", total)

    processed = sections_extracted = securities_inserted = no_agreement = error_count = 0
    observations_written = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        txn_id = row["transaction_id"]

        # Gather unextracted docs for this transaction, ASC by filing date so most-recent
        # is applied last (last-write wins for non-priority fields)
        doc_rows = conn.execute(
            """
            SELECT td.document_id, td.filing_type, td.filing_date, td.document_title, td.raw_text
            FROM transaction_document td
            WHERE td.transaction_id = ?
              AND td.is_current = 1
              AND td.filing_type IN ('8K_EXHIBIT_21','DEFM14A','S4','SC_TOT','DEFA14A')
              AND td.agreement_extracted_at IS NULL
            ORDER BY td.filing_date ASC NULLS FIRST, td.document_id
            """,
            (txn_id,),
        ).fetchall()

        txn_sections = 0
        txn_securities = 0
        txn_observations = 0
        all_securities: list[dict] = []
        field_priority_state: dict[str, tuple[float, str | None]] = {}

        for doc_row in doc_rows:
            doc_id = doc_row["document_id"]
            agreement_dated_as_of = extract_agreement_dated_as_of(doc_row["raw_text"])
            doc_all_succeeded = True
            doc_sections = 0
            doc_observations = 0
            doc_securities = 0
            doc_securities_list: list[dict] = []

            try:
                conn.execute("SAVEPOINT doc_extract")

                # Soft-delete existing observations inside savepoint so rollback
                # on section failure atomically restores prior is_current=1 rows.
                conn.execute(
                    "UPDATE transaction_field_observation SET is_current=0 WHERE source_document_id=?",
                    (doc_id,),
                )

                section_rows = conn.execute(
                    """
                    SELECT tds.section_id, tds.section_type, tds.excerpt_text, tds.confidence,
                           td.filing_type, td.filing_date, td.document_title
                    FROM transaction_document_section tds
                    JOIN transaction_document td ON td.document_id = tds.document_id
                    WHERE tds.document_id = ?
                      AND tds.confidence IN ('HIGH','MEDIUM')
                    ORDER BY tds.section_id
                    """,
                    (doc_id,),
                ).fetchall()

                by_type: dict[str, list] = {}
                for sr in section_rows:
                    by_type.setdefault(sr["section_type"], []).append(sr)

                for section_type, prompt_name in _SECTION_PROMPT_MAP.items():
                    if not doc_all_succeeded:
                        break
                    sections = by_type.get(section_type, [])
                    if not sections:
                        continue

                    for sec_row in sections:
                        if not doc_all_succeeded:
                            break
                        excerpt = sec_row["excerpt_text"]
                        if not excerpt or not excerpt.strip():
                            continue

                        log.debug(
                            "transaction_id=%s section=%s doc=%s confidence=%s",
                            txn_id, section_type, doc_id, sec_row["confidence"],
                        )

                        result = _call_section_prompt(
                            prompt_name, excerpt, conn, cfg, run_id, log
                        )
                        time.sleep(_SLEEP)

                        if result is None:
                            error_count += 1
                            doc_all_succeeded = False
                            break

                        doc_sections += 1
                        prompt_version = f"{prompt_name}:{_VERSIONS[prompt_name]}"

                        # V3 §T12 corroboration. `merger_structure = TENDER_OFFER` and
                        # canonical `offer_mechanism` are the same fact seen from the
                        # agreement, so the agreement contributes an observation rather
                        # than having its evidence discarded. It does NOT become the
                        # canonical owner: high_confidence_extraction populates the field
                        # independently, and where both establish it, ordinary tier
                        # resolution merges two agreeing observations.
                        #
                        # Only TENDER_OFFER maps. DIRECT / FORWARD_TRIANGULAR /
                        # REVERSE_TRIANGULAR stay merger-mechanics observations with no V3
                        # destination (§T2 defers them); mapping them here would invent one.
                        # `merger_structure` itself is untouched and keeps all four values.
                        if result.get("merger_structure") == "TENDER_OFFER":
                            result = {**result, "offer_mechanism": "TENDER_OFFER"}

                        try:
                            obs_before = conn.execute(
                                "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=? AND is_current=1",
                                (txn_id,),
                            ).fetchone()[0]

                            _write_observations(
                                conn,
                                transaction_id=txn_id,
                                extraction_results=result,
                                source_document_id=doc_id,
                                source_section_id=sec_row["section_id"],
                                filing_date=sec_row["filing_date"],
                                extraction_prompt_version=prompt_version,
                                filing_type=sec_row["filing_type"],
                                model_confidence=result.get("model_confidence"),
                                agreement_dated_as_of=agreement_dated_as_of,
                            )

                            obs_after = conn.execute(
                                "SELECT COUNT(*) FROM transaction_field_observation WHERE transaction_id=? AND is_current=1",
                                (txn_id,),
                            ).fetchone()[0]
                            doc_observations += obs_after - obs_before

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
                                    sec_row["section_id"], doc_id,
                                    now, log,
                                )
                                doc_securities += n
                                if isinstance(result.get("securities"), list):
                                    doc_securities_list.extend(result["securities"])
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
                        except Exception as exc:
                            log.warning(
                                "transaction_id=%s section=%s apply error: %s — rolling back doc",
                                txn_id, section_type, exc,
                            )
                            error_count += 1
                            doc_all_succeeded = False
                            break

                if doc_all_succeeded:
                    # agreement_extracted_at only set on full per-document success
                    conn.execute(
                        "UPDATE transaction_document SET agreement_extracted_at=? WHERE document_id=?",
                        (now, doc_id),
                    )
                    conn.execute("RELEASE SAVEPOINT doc_extract")
                    conn.commit()
                    txn_sections += doc_sections
                    txn_observations += doc_observations
                    txn_securities += doc_securities
                    all_securities.extend(doc_securities_list)
                else:
                    # Rollback restores soft-deleted observations and undoes any
                    # partial inserts; document stays PENDING for next rerun.
                    conn.execute("ROLLBACK TO SAVEPOINT doc_extract")
                    conn.execute("RELEASE SAVEPOINT doc_extract")
                    log.warning(
                        "transaction_id=%s doc_id=%d rolled back due to section failure; "
                        "prior observations restored, document remains PENDING",
                        txn_id, doc_id,
                    )

            except Exception as exc:
                log.exception(
                    "transaction_id=%s doc_id=%d rolled back due to exception: %s",
                    txn_id, doc_id, exc,
                )
                error_count += 1
                try:
                    conn.execute("ROLLBACK TO SAVEPOINT doc_extract")
                    conn.execute("RELEASE SAVEPOINT doc_extract")
                except Exception:
                    pass

        # Diluted shares rollup from capitalization observations
        if all_securities:
            diluted, quality = _compute_diluted_shares(all_securities)
            conn.execute(
                "UPDATE transaction_record SET target_total_diluted_shares=?, fully_diluted_calc_quality=?, updated_at=? WHERE transaction_id=?",
                (diluted, quality, now, txn_id),
            )

        # Clear canonical fields whose observation set has become empty (Drop 3.22c)
        try:
            _clear_stale_canonical_fields(conn, txn_id, now, log)
        except Exception as exc:
            log.warning("transaction_id=%s canonical-clear failed: %s", txn_id, exc)

        # Observation diff (Drop 3.20b) — re-compute after all docs processed
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

        # Roll up transaction extraction status
        # Check if any agreement doc has now been extracted (has sections)
        any_extracted = conn.execute(
            """
            SELECT COUNT(*) FROM transaction_document
            WHERE transaction_id=? AND is_current=1
              AND filing_type IN ('8K_EXHIBIT_21','DEFM14A','S4','SC_TOT','DEFA14A')
              AND agreement_extracted_at IS NOT NULL
            """,
            (txn_id,),
        ).fetchone()[0]
        status = "EXTRACTED" if (txn_sections > 0 or any_extracted > 0) else "NO_AGREEMENT_LINKED"
        conn.execute(
            "UPDATE transaction_record SET agreement_extraction_status=?, updated_at=? WHERE transaction_id=?",
            (status, now, txn_id),
        )
        conn.commit()

        sections_extracted += txn_sections
        securities_inserted += txn_securities
        observations_written += txn_observations
        processed += 1
        if txn_sections == 0:
            no_agreement += 1

        log.info(
            "transaction_id=%s status=%s sections=%d securities=%d observations=%d",
            txn_id, status, txn_sections, txn_securities, txn_observations,
        )

    log.info(
        "Stage 11 done  total=%d processed=%d sections=%d securities=%d "
        "observations=%d no_sections=%d errors=%d",
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
