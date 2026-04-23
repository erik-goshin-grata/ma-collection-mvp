"""
Stage 5: sec_trigger_detect

Pure Python stage — no LLM calls. Detects public-party signals in each
HC-extracted press release by calling detect_public_party() from the SEC
adapter, which looks for exchange:ticker patterns, SEC filing language, and
public-company boilerplate.

Updates staging_extraction per row:
  - signal found → sec_lookup_status = TRIGGERED, status = SEC_TRIGGERED
  - no signal    → sec_lookup_status = NOT_TRIGGERED, status = SEC_NOT_TRIGGERED

Also writes the trigger dict into source_raw.notes["sec_trigger"] so Stage 6
can recover it without re-running detection.

Spec references: specs/adapter_sec_api.md §3, specs/pipeline.md §2 (Stage 5)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from adapters.sec_api import detect_public_party
from config import Config
from logger import get_logger


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Detect public-party signals in HC-extracted rows.

    Returns
    -------
    dict
        Keys: hc_extracted_total, triggered, not_triggered
    """
    log = get_logger("sec_trigger_detect", run_id, level=cfg.log_level)

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.target_name, se.acquirer_name,
               se.source_raw_id,
               sr.clean_text, sr.notes
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'HC_EXTRACTED'
        """
    ).fetchall()

    total = len(rows)
    triggered = not_triggered = 0
    log.info("Stage 5: %d rows to check for public-party signals", total)

    for row in rows:
        eid = row["extraction_id"]
        sid = row["source_raw_id"]
        now = datetime.now(timezone.utc).isoformat()

        try:
            signal = detect_public_party(
                row["clean_text"] or "",
                row["target_name"],
                row["acquirer_name"],
            )
        except Exception as exc:
            log.warning("extraction_id=%d detection error: %s — treating as NOT_TRIGGERED", eid, exc)
            signal = None

        if signal:
            # Persist trigger details into source_raw.notes["sec_trigger"]
            try:
                nd = json.loads(row["notes"] or "{}")
                if not isinstance(nd, dict):
                    nd = {"original_notes": row["notes"]}
            except (ValueError, TypeError):
                nd = {"original_notes": row["notes"]}
            nd["sec_trigger"] = signal

            conn.execute(
                "UPDATE source_raw SET notes=?, updated_at=? WHERE source_raw_id=?",
                (json.dumps(nd), now, sid),
            )
            conn.execute(
                """UPDATE staging_extraction
                   SET status='SEC_TRIGGERED', sec_lookup_status='TRIGGERED', updated_at=?
                   WHERE extraction_id=?""",
                (now, eid),
            )
            conn.commit()
            triggered += 1
            log.info(
                "extraction_id=%d TRIGGERED  ticker=%s  side=%s  signal=%r",
                eid, signal.get("ticker"), signal.get("side"), signal.get("trigger_signal"),
            )
        else:
            conn.execute(
                """UPDATE staging_extraction
                   SET status='SEC_NOT_TRIGGERED', sec_lookup_status='NOT_TRIGGERED', updated_at=?
                   WHERE extraction_id=?""",
                (now, eid),
            )
            conn.commit()
            not_triggered += 1
            log.debug("extraction_id=%d NOT_TRIGGERED", eid)

    log.info(
        "Stage 5 done  total=%d triggered=%d not_triggered=%d",
        total, triggered, not_triggered,
    )
    return {"hc_extracted_total": total, "triggered": triggered, "not_triggered": not_triggered}
