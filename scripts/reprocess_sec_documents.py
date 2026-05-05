"""
Drop 3.21 — Re-fetch SEC documents stored with raw SGML wrapper.

Finds transaction_document rows where raw_text starts with '<DOCUMENT>'
(the SGML submission-package wrapper stored before the Drop 3.21 fix),
re-fetches each using the fixed fetch_filing_full_text(), updates the
row's raw_text / raw_text_length / document_title in-place, deletes the
existing section rows, and re-runs the section tagger.

Usage:
    python scripts/reprocess_sec_documents.py [--db-path PATH] [--dry-run]

Options:
    --db-path   Path to the SQLite DB (defaults to DB_PATH env var or data/ma_mvp.db)
    --dry-run   Report which docs would be reprocessed without making any changes
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

import adapters.sec_api as _sec
from adapters.sec_api import extract_document_title
from config import get_config
from db import get_connection
from lib.section_tagger import tag_sections

_SLEEP = 2.0


def _retag_document(conn, document_id: int, raw_text: str) -> int:
    """Delete existing sections and re-run tagger. Returns new section count."""
    conn.execute(
        "DELETE FROM transaction_document_section WHERE document_id=?",
        (document_id,),
    )
    matches = tag_sections(raw_text)
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
    return len(matches)


def _is_sgml_wrapper(raw_text: str | None) -> bool:
    if not raw_text:
        return False
    return bool(re.match(r'\s*<DOCUMENT>', raw_text, re.IGNORECASE))


def run(db_path: str, dry_run: bool) -> None:
    log = logging.getLogger("reprocess_sec_documents")
    cfg = get_config()

    conn = get_connection(db_path)
    session = requests.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    rows = conn.execute(
        """
        SELECT document_id, transaction_id, filing_type, sec_filing_url,
               raw_text_length
        FROM transaction_document
        WHERE is_current = 1
          AND raw_text IS NOT NULL
          AND (raw_text LIKE '<DOCUMENT>%' OR raw_text LIKE '<document>%')
        ORDER BY transaction_id, filing_type
        """
    ).fetchall()

    log.info("Found %d document(s) with SGML wrapper raw_text", len(rows))
    if not rows:
        log.info("Nothing to reprocess.")
        conn.close()
        return

    if dry_run:
        for row in rows:
            log.info(
                "DRY-RUN  document_id=%d  transaction_id=%s  filing_type=%s  chars=%s  url=%s",
                row["document_id"], row["transaction_id"], row["filing_type"],
                row["raw_text_length"], row["sec_filing_url"],
            )
        conn.close()
        return

    reprocessed = 0
    skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        doc_id = row["document_id"]
        txn_id = row["transaction_id"]
        filing_type = row["filing_type"]
        url = row["sec_filing_url"] or ""

        if not url:
            log.warning(
                "document_id=%d transaction_id=%s filing_type=%s — no sec_filing_url, skipping",
                doc_id, txn_id, filing_type,
            )
            skipped += 1
            continue

        log.info(
            "Reprocessing document_id=%d  transaction_id=%s  filing_type=%s  url=%s",
            doc_id, txn_id, filing_type, url,
        )

        filing = {"linkToHtmlDocument": url}
        time.sleep(_SLEEP)
        try:
            raw_text, status = _sec.fetch_filing_full_text(cfg, session, filing, log)
        except Exception as exc:
            log.warning("document_id=%d fetch error: %s — skipping", doc_id, exc)
            skipped += 1
            continue

        if status == "SKIP" or not raw_text:
            log.warning(
                "document_id=%d status=%s after re-fetch — skipping",
                doc_id, status,
            )
            skipped += 1
            continue

        if _is_sgml_wrapper(raw_text):
            log.warning(
                "document_id=%d still has SGML wrapper after re-fetch — skipping",
                doc_id,
            )
            skipped += 1
            continue

        doc_title = extract_document_title(raw_text)
        conn.execute(
            """
            UPDATE transaction_document
            SET raw_text=?, raw_text_length=?, document_title=?, fetch_timestamp=?
            WHERE document_id=?
            """,
            (raw_text, len(raw_text), doc_title, now, doc_id),
        )

        new_sections = _retag_document(conn, doc_id, raw_text)
        conn.commit()

        log.info(
            "document_id=%d  chars=%d  title=%r  sections=%d",
            doc_id, len(raw_text), doc_title, new_sections,
        )
        reprocessed += 1

    conn.close()
    log.info(
        "Done.  reprocessed=%d  skipped=%d  total=%d",
        reprocessed, skipped, len(rows),
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
