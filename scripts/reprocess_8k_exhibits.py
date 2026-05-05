"""
Drop 3.22 — Backfill 8K_EXHIBIT_21 documents for triggered transactions.

Finds triggered transactions that have no 8K_EXHIBIT_21 row in
transaction_document, then queries for Item 1.01 signing 8-Ks from both
the target filer and the acquirer filer using the fixed
query_8k_signing_filings() adapter. For each signing 8-K found, runs
_find_exhibits() to locate the Exhibit 2.1 attachment, fetches and stores
it, re-runs the section tagger, and updates linked_filings_count.

Idempotent: _insert_document() uses INSERT OR IGNORE against the
(transaction_id, sec_accession_number, filing_type) UNIQUE constraint, so
re-running produces no duplicate rows.

Usage:
    python scripts/reprocess_8k_exhibits.py [--db-path PATH] [--dry-run]

Options:
    --db-path   Path to SQLite DB (defaults to DB_PATH env var or data/ma_mvp.db)
    --dry-run   Report which transactions would be queried without making changes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

import adapters.sec_api as _sec
from config import get_config
from db import get_connection
from stages.sec_documents import (
    _insert_document,
    _strip_ticker,
    _tag_and_insert_sections,
)

_SLEEP = 2.0
_MAX_FILINGS = 3


def run(db_path: str, dry_run: bool) -> None:
    log = logging.getLogger("reprocess_8k_exhibits")
    cfg = get_config()
    conn = get_connection(db_path)
    session = requests.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    rows = conn.execute(
        """
        SELECT tr.transaction_id, tr.announced_date,
               tr.target_name, tr.acquirer_name,
               tr.target_ticker, tr.acquirer_ticker
        FROM transaction_record tr
        WHERE tr.is_current = 1
          AND EXISTS (
              SELECT 1 FROM staging_extraction se
              WHERE se.transaction_cluster_id = tr.transaction_id
                AND se.sec_lookup_status = 'TRIGGERED'
          )
          AND NOT EXISTS (
              SELECT 1 FROM transaction_document td
              WHERE td.transaction_id = tr.transaction_id
                AND td.filing_type = '8K_EXHIBIT_21'
                AND td.is_current = 1
          )
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    log.info("Found %d triggered transaction(s) without 8K_EXHIBIT_21 rows", len(rows))
    if not rows:
        log.info("Nothing to reprocess.")
        conn.close()
        return

    if dry_run:
        for row in rows:
            log.info(
                "DRY-RUN  transaction_id=%s  target=%r  tgt_ticker=%s  acq_ticker=%s  announced=%s",
                row["transaction_id"], row["target_name"],
                row["target_ticker"], row["acquirer_ticker"],
                row["announced_date"],
            )
        conn.close()
        return

    processed = exhibits_fetched = skipped = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        txn_id = row["transaction_id"]
        announced_date = row["announced_date"] or ""
        target_name = row["target_name"]
        acquirer_name = row["acquirer_name"]

        tgt_ticker = _strip_ticker(row["target_ticker"])
        acq_ticker = _strip_ticker(row["acquirer_ticker"])

        _seen: set[str] = set()
        exhibit_queries: list[tuple[str | None, str | None]] = []
        for tk, nm in [(tgt_ticker, target_name), (acq_ticker, acquirer_name)]:
            key = tk or nm or ""
            if key and key not in _seen:
                _seen.add(key)
                exhibit_queries.append((tk, nm))

        if not exhibit_queries:
            log.warning("transaction_id=%s no ticker or name available — skipping", txn_id)
            skipped += 1
            continue

        log.info(
            "transaction_id=%s  target=%r  queries=%s  announced=%s",
            txn_id, target_name,
            [(tk or nm) for tk, nm in exhibit_queries],
            announced_date,
        )

        # Collect Item 1.01 8-Ks from both filers, deduplicate by accession
        signing_filings: dict[str, dict] = {}
        for ex_ticker, ex_name in exhibit_queries:
            filer_filings = _sec.query_8k_signing_filings(
                cfg, session, ex_ticker, ex_name, announced_date, log
            )
            time.sleep(_SLEEP)
            for f in filer_filings[:_MAX_FILINGS]:
                acc = f.get("accessionNo") or f.get("id") or ""
                if acc and acc not in signing_filings:
                    signing_filings[acc] = f

        log.info(
            "transaction_id=%s  signing_8ks_found=%d",
            txn_id, len(signing_filings),
        )

        txn_new = 0
        for filing in signing_filings.values():
            exhibits = _sec._find_exhibits(filing, _sec._EX_21_RE)
            if not exhibits:
                log.info(
                    "transaction_id=%s 8K_EXHIBIT_21: 0 exhibits in accession=%s",
                    txn_id, filing.get("accessionNo", "?"),
                )
                continue
            for label, ex_url in exhibits:
                if ex_url.lower().endswith(".pdf"):
                    log.info(
                        "transaction_id=%s 8K_EXHIBIT_21: skipped PDF exhibit at %s",
                        txn_id, ex_url,
                    )
                    continue
                raw_html, ex_text, ex_status = _sec.fetch_exhibit(cfg, session, ex_url, log)
                time.sleep(_SLEEP)
                if ex_status == "SKIP":
                    continue
                if ex_text or ex_status == "UNREADABLE":
                    doc_id = _insert_document(conn, txn_id, "8K_EXHIBIT_21", filing, ex_text, now)
                    conn.commit()
                    if doc_id and ex_text:
                        secs = _tag_and_insert_sections(conn, doc_id, ex_text, log)
                        conn.commit()
                        log.info(
                            "transaction_id=%s 8K_EXHIBIT_21 fetched"
                            "  label=%r  accession=%s  chars=%d  sections=%d",
                            txn_id, label, filing.get("accessionNo", "?"),
                            len(ex_text), secs,
                        )
                        txn_new += 1
                        exhibits_fetched += 1
                    elif doc_id:
                        log.info(
                            "transaction_id=%s 8K_EXHIBIT_21 UNREADABLE  accession=%s",
                            txn_id, filing.get("accessionNo", "?"),
                        )
                        txn_new += 1
                break  # one Exhibit 2.1 per 8-K filing

        # Recompute linked_filings_count
        total_linked = conn.execute(
            "SELECT COUNT(*) FROM transaction_document WHERE transaction_id=? AND is_current=1",
            (txn_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE transaction_record SET linked_filings_count=?, updated_at=? WHERE transaction_id=?",
            (total_linked, now, txn_id),
        )
        conn.commit()

        processed += 1
        log.info(
            "transaction_id=%s done  new_8k_exhibits=%d  total_linked=%d",
            txn_id, txn_new, total_linked,
        )

    conn.close()
    log.info(
        "Done.  processed=%d  exhibits_fetched=%d  skipped=%d  total=%d",
        processed, exhibits_fetched, skipped, len(rows),
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
        help="Report affected transactions without making changes",
    )
    args = parser.parse_args()
    run(args.db_path, args.dry_run)


if __name__ == "__main__":
    main()
