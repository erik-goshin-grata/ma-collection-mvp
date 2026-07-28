"""
Stage 6: sec_enrich

Calls adapters.sec_api.run_per_transaction() for each SEC_TRIGGERED row.
Recovers the stored trigger_info from source_raw.notes["sec_trigger"] written
by Stage 5 so detection is not repeated.

Status transitions:
  - adapter returned data or NO_MATCH → status = SEC_ENRICHED
    (sec_lookup_status already set by the adapter: TRIGGERED or NO_MATCH)
  - adapter returned errors (non-empty result["errors"]) →
    status = PROMPT_FAILED, sec_lookup_status = ERROR, logged to failure table
  - RuntimeError (auth failure) → re-raised, halts the pipeline

Spec references: specs/adapter_sec_api.md §5–6, specs/pipeline.md §2 (Stage 6)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import adapters.sec_api as _sec
from config import Config
from logger import get_logger
from prompts.base import log_prompt_failure


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Enrich SEC-triggered rows with 8-K item text and exhibits.

    Returns
    -------
    dict
        Keys: triggered_total, enriched_with_rows, no_match, error
    """
    log = get_logger("sec_enrich", run_id, level=cfg.log_level)

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               sr.notes AS source_notes
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'SEC_TRIGGERED'
        """
    ).fetchall()

    total = len(rows)
    enriched_with_rows = no_match = error_count = attached_sources_queued = 0
    log.info("Stage 6: %d rows to enrich via SEC", total)

    for row in rows:
        eid = row["extraction_id"]
        now = datetime.now(timezone.utc).isoformat()

        # Recover trigger_info written by Stage 5 to avoid re-running detection
        trigger_info = None
        if row["source_notes"]:
            try:
                nd = json.loads(row["source_notes"])
                if isinstance(nd, dict):
                    trigger_info = nd.get("sec_trigger")
            except (ValueError, TypeError):
                pass

        try:
            result = _sec.run_per_transaction(
                conn=conn,
                cfg=cfg,
                run_id=run_id,
                extraction_id=eid,
                trigger_info=trigger_info,
            )
        except RuntimeError as exc:
            # Auth failure — fatal, re-raise to halt the pipeline
            log.error("SEC auth failure for extraction_id=%d: %s — halting", eid, exc)
            raise

        if result["errors"]:
            err_msg = "; ".join(result["errors"])
            log.warning("extraction_id=%d SEC errors: %s", eid, err_msg)
            log_prompt_failure(
                conn,
                source_raw_id=row["source_raw_id"],
                extraction_id=eid,
                stage="sec_enrich",
                failure_type="API_ERROR",
                raw_response="",
                error_message=err_msg,
                prompt_version="sec_enrich",
                run_id=run_id,
            )
            conn.execute(
                """UPDATE staging_extraction
                   SET status='PROMPT_FAILED', sec_lookup_status='ERROR', updated_at=?
                   WHERE extraction_id=?""",
                (now, eid),
            )
            conn.commit()
            error_count += 1
            continue

        # Transition to SEC_ENRICHED; sec_lookup_status already written by adapter
        conn.execute(
            "UPDATE staging_extraction SET status='SEC_ENRICHED', updated_at=? WHERE extraction_id=?",
            (now, eid),
        )
        conn.commit()

        if result["filings_found"] == 0:
            no_match += 1
            log.info("extraction_id=%d SEC_ENRICHED (no filings found)", eid)
        else:
            enriched_with_rows += 1
            attached_sources_queued += int(result.get("attached_sources_queued") or 0)
            log.info(
                "extraction_id=%d SEC_ENRICHED  accession=%s  rows_inserted=%d  item=%s  attached_sources_queued=%d",
                eid,
                result.get("filing_accession"),
                result["rows_inserted"],
                result.get("item_extracted"),
                result.get("attached_sources_queued") or 0,
            )

    log.info(
        "Stage 6 done  total=%d enriched_with_rows=%d no_match=%d errors=%d",
        total, enriched_with_rows, no_match, error_count,
    )
    return {
        "triggered_total": total,
        "enriched_with_rows": enriched_with_rows,
        "attached_sources_queued": attached_sources_queued,
        "no_match": no_match,
        "error": error_count,
    }
