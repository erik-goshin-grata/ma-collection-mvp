"""
Drop 3.24 — Reprocess section tags for existing Exhibit 2.1 documents.

Deletes existing transaction_document_section rows for all is_current
8K_EXHIBIT_21 documents, re-runs section extraction via
lib/exhibit_21_navigator.navigate_exhibit_21() (with fallback to
lib/section_tagger.tag_sections() when the navigator returns empty),
inserts new section rows, and resets agreement_extracted_at=NULL so
Stage 11 re-runs on the next agreement-rerun pass.

Expected outcome (10-doc corpus):
  6 docs process via navigator (docs 8, 9, 10, 11, 12, 13)
  4 docs fall back to section_tagger (docs 5, 6, 7, 14)

Usage:
    python scripts/reprocess_exhibit_21_sections.py [--db-path PATH] [--dry-run]

Options:
    --db-path   Path to the SQLite DB (defaults to DB_PATH env var or data/ma_mvp.db)
    --dry-run   Report which docs would be reprocessed without making any changes
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import get_connection
from lib.exhibit_21_navigator import navigate_exhibit_21
from lib.section_tagger import tag_sections


def _retag_document(conn, document_id: int, raw_text: str, log) -> tuple[int, str]:
    """Delete existing sections and re-tag via navigator or fallback tagger.

    Returns (section_count, method) where method is 'navigator' or 'tagger_fallback'.
    """
    conn.execute(
        "DELETE FROM transaction_document_section WHERE document_id=?",
        (document_id,),
    )
    matches = navigate_exhibit_21(raw_text)
    method = "navigator"
    if not matches:
        log.info("document_id=%d: navigator returned empty — falling back to section_tagger", document_id)
        matches = tag_sections(raw_text)
        method = "tagger_fallback"

    for m in matches:
        conn.execute(
            """
            INSERT INTO transaction_document_section
                (document_id, section_type, heading_text, excerpt_text,
                 excerpt_start_offset, excerpt_end_offset, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                m.section_type,
                m.heading_text,
                m.excerpt_text,
                m.excerpt_start_offset,
                m.excerpt_end_offset,
                m.confidence,
            ),
        )
    return len(matches), method


def run(db_path: str, dry_run: bool) -> None:
    log = logging.getLogger("reprocess_exhibit_21_sections")
    conn = get_connection(db_path)

    rows = conn.execute(
        """
        SELECT document_id, transaction_id, raw_text_length
        FROM transaction_document
        WHERE filing_type = '8K_EXHIBIT_21'
          AND is_current = 1
          AND raw_text IS NOT NULL
        ORDER BY transaction_id, document_id
        """
    ).fetchall()

    log.info("Found %d 8K_EXHIBIT_21 document(s) to reprocess", len(rows))
    if not rows:
        log.info("Nothing to reprocess.")
        conn.close()
        return

    if dry_run:
        for row in rows:
            log.info(
                "DRY-RUN  document_id=%d  transaction_id=%s  chars=%s",
                row["document_id"], row["transaction_id"], row["raw_text_length"],
            )
        conn.close()
        return

    now = datetime.now(timezone.utc).isoformat()
    navigator_count = fallback_count = 0

    for row in rows:
        doc_id = row["document_id"]
        txn_id = row["transaction_id"]

        raw_text = conn.execute(
            "SELECT raw_text FROM transaction_document WHERE document_id=?",
            (doc_id,),
        ).fetchone()["raw_text"]

        sections, method = _retag_document(conn, doc_id, raw_text, log)

        conn.execute(
            "UPDATE transaction_document SET agreement_extracted_at=NULL WHERE document_id=?",
            (doc_id,),
        )
        conn.commit()

        if method == "navigator":
            navigator_count += 1
        else:
            fallback_count += 1

        log.info(
            "document_id=%d  transaction_id=%s  sections=%d  method=%s",
            doc_id, txn_id, sections, method,
        )

    conn.close()
    log.info(
        "Done.  navigator=%d  fallback=%d  total=%d",
        navigator_count, fallback_count, len(rows),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s  %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=os.environ.get("DB_PATH", "data/ma_mvp.db"),
        help="Path to the SQLite database (default: data/ma_mvp.db or DB_PATH env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report affected rows without making changes",
    )
    args = parser.parse_args()
    run(args.db_path, args.dry_run)


if __name__ == "__main__":
    main()
