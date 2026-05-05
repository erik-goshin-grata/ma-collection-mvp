"""
Stage 10: sec_documents

For each aggregated transaction that triggered a public-party SEC signal,
fetches an expanded set of SEC filings and stores them in
transaction_document with heuristic section tags in
transaction_document_section.

Filing types fetched (in order):
  8K_ITEM_201   — 8-K Item 2.01 (completion of acquisition)
  8K_EXHIBIT_21 — Exhibit 2.1 attached to 8-K (merger agreement)
  DEFM14A       — Definitive proxy for merger
  SC_TOT        — Tender offer document (SC TO-T)
  S4            — S-4 registration statement (stock-for-stock)

Rate limiting: 2s between requests (conservative for sec-api.io $55 tier).
Cap 3 filings per transaction per type. Stage runs only on transactions
where sec_lookup_status = TRIGGERED on any contributing staging_extraction.

Idempotent: skips (transaction_id, filing_type) pairs already present in
transaction_document.

Updates transaction_record.linked_filings_count after processing each
transaction.

Spec references: specs/adapter_sec_api.md §Drop3.19,
                 specs/pipeline.md §2 (Stage 10)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import requests

import adapters.sec_api as _sec
from adapters.sec_api import extract_document_title
from config import Config
from lib.section_tagger import tag_sections
from logger import get_logger

# (database filing_type label, sec_form_type_key, use_extractor_item_code)
# use_extractor_item_code: if non-None, use the Item Extractor API for this type
_FILING_JOBS: list[tuple[str, str, str | None]] = [
    ("8K_ITEM_201",   "8-K",     "2.01"),
    ("8K_EXHIBIT_21", "8-K",     None),    # uses exhibit fetcher, not item extractor
    ("DEFM14A",       "DEFM14A", None),
    ("SC_TOT",        "SC_TOT",  None),
    ("S4",            "S4",      None),
]

_MAX_FILINGS_PER_TYPE = 3
_SLEEP = 2.0  # conservative sec-api.io throttle for this stage


def _strip_ticker(ticker_raw: str | None) -> str | None:
    """Strip exchange prefix from a ticker string ('NYSE:AWK' → 'AWK')."""
    if not ticker_raw:
        return None
    stripped = ticker_raw.split(":")[-1].strip()
    return stripped or None


def _get_ticker_for_transaction(conn: sqlite3.Connection, transaction_id: str) -> tuple[str | None, str | None]:
    """Return (ticker, company_name) for the triggered extraction linked to this transaction."""
    row = conn.execute(
        """
        SELECT se.target_ticker, se.acquirer_ticker, se.target_name, se.acquirer_name,
               sr.notes AS source_notes
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.transaction_cluster_id = ?
          AND se.sec_lookup_status = 'TRIGGERED'
        LIMIT 1
        """,
        (transaction_id,),
    ).fetchone()
    if not row:
        return None, None

    # Prefer ticker from trigger_info stored in source_raw.notes
    ticker: str | None = None
    try:
        nd = json.loads(row["source_notes"] or "{}")
        if isinstance(nd, dict):
            trigger = nd.get("sec_trigger") or {}
            ticker = trigger.get("ticker")
    except (ValueError, TypeError):
        pass

    if not ticker:
        ticker = row["target_ticker"] or row["acquirer_ticker"]

    company_name = row["target_name"] or row["acquirer_name"]
    return ticker, company_name


def _already_fetched(conn: sqlite3.Connection, transaction_id: str, filing_type: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM transaction_document WHERE transaction_id=? AND filing_type=? AND is_current=1 LIMIT 1",
        (transaction_id, filing_type),
    ).fetchone()
    return row is not None


def _insert_document(
    conn: sqlite3.Connection,
    transaction_id: str,
    filing_type: str,
    filing: dict,
    raw_text: str | None,
    fetch_timestamp: str,
) -> int:
    """Insert a transaction_document row; returns document_id."""
    accession = filing.get("accessionNo") or filing.get("id")
    filing_url = (
        filing.get("linkToHtmlDocument")
        or filing.get("linkToFilingDetails")
        or ""
    )
    doc_title = extract_document_title(raw_text) if raw_text else None
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO transaction_document (
            transaction_id, filing_type, sec_accession_number, sec_filing_url,
            filer_cik, filer_name, filing_date, document_title,
            raw_text, raw_text_length, fetch_timestamp, is_current
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            transaction_id,
            filing_type,
            accession,
            filing_url,
            filing.get("cik"),
            filing.get("companyName"),
            (filing.get("filedAt") or "")[:10] or None,
            doc_title,
            raw_text,
            len(raw_text) if raw_text else None,
            fetch_timestamp,
        ),
    )
    if cur.lastrowid:
        return cur.lastrowid
    # Row already existed (UNIQUE constraint hit) — look it up
    existing = conn.execute(
        "SELECT document_id FROM transaction_document WHERE transaction_id=? AND filing_type=? AND sec_accession_number=?",
        (transaction_id, filing_type, accession),
    ).fetchone()
    return existing["document_id"] if existing else 0


def _tag_and_insert_sections(
    conn: sqlite3.Connection,
    document_id: int,
    raw_text: str,
    log,
) -> int:
    """Run section tagger and insert results; returns count of sections inserted."""
    matches = tag_sections(raw_text)
    inserted = 0
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
        inserted += 1
    return inserted


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Fetch expanded SEC filings for triggered transactions.

    Returns
    -------
    dict
        Keys: transactions_total, transactions_processed, documents_fetched,
              sections_tagged, not_triggered, errors
    """
    log = get_logger("sec_documents", run_id, level=cfg.log_level)
    session = requests.Session()
    session.headers.update({"User-Agent": cfg.user_agent_string})

    # Find aggregated transactions that have at least one TRIGGERED extraction
    rows = conn.execute(
        """
        SELECT DISTINCT tr.transaction_id, tr.announced_date,
                        tr.target_name, tr.acquirer_name,
                        tr.target_ticker, tr.acquirer_ticker
        FROM transaction_record tr
        WHERE tr.is_current = 1
          AND EXISTS (
              SELECT 1 FROM staging_extraction se
              WHERE se.transaction_cluster_id = tr.transaction_id
                AND se.sec_lookup_status = 'TRIGGERED'
          )
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 10: %d transactions with SEC trigger signal", total)

    processed = docs_fetched = sections_tagged = error_count = 0
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        txn_id = row["transaction_id"]
        announced_date = row["announced_date"] or ""
        target_name = row["target_name"]
        acquirer_name = row["acquirer_name"]

        ticker, company_name = _get_ticker_for_transaction(conn, txn_id)
        log.info(
            "transaction_id=%s target=%r acquirer=%r ticker=%r announced=%s",
            txn_id, target_name, acquirer_name, ticker, announced_date,
        )

        # Tickers for 8K_EXHIBIT_21 dual-filer queries (target files the signing 8-K
        # with near-100% reliability; acquirer often files one too).
        tgt_ticker = _strip_ticker(row["target_ticker"])
        acq_ticker = _strip_ticker(row["acquirer_ticker"])
        _seen_ex21: set[str] = set()
        exhibit_queries: list[tuple[str | None, str | None]] = []
        for tk, nm in [(tgt_ticker, target_name), (acq_ticker, acquirer_name)]:
            key = tk or nm or ""
            if key and key not in _seen_ex21:
                _seen_ex21.add(key)
                exhibit_queries.append((tk, nm))
        if not exhibit_queries:
            # Neither TR ticker populated — fall back to trigger ticker
            exhibit_queries.append((ticker, company_name or target_name))

        txn_docs = 0

        for db_filing_type, form_type_key, item_code in _FILING_JOBS:
            if _already_fetched(conn, txn_id, db_filing_type):
                log.debug("transaction_id=%s %s already fetched — skipping", txn_id, db_filing_type)
                continue

            try:
                if db_filing_type == "8K_EXHIBIT_21":
                    # Query both target and acquirer for Item 1.01 signing 8-Ks;
                    # deduplicate by accession so the same filing isn't double-stored.
                    signing_filings: dict[str, dict] = {}
                    for ex_ticker, ex_name in exhibit_queries:
                        filer_filings = _sec.query_8k_signing_filings(
                            cfg, session, ex_ticker, ex_name, announced_date, log
                        )
                        time.sleep(_SLEEP)
                        for f in filer_filings[:_MAX_FILINGS_PER_TYPE]:
                            acc = f.get("accessionNo") or f.get("id") or ""
                            if acc and acc not in signing_filings:
                                signing_filings[acc] = f

                    for filing in signing_filings.values():
                        exhibits = _sec._find_exhibits(filing, _sec._EX_21_RE)
                        if not exhibits:
                            log.info(
                                "transaction_id=%s 8K_EXHIBIT_21: 0 exhibits found in accession=%s",
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
                                doc_id = _insert_document(conn, txn_id, db_filing_type, filing, ex_text, now)
                                conn.commit()
                                if doc_id and ex_text:
                                    secs = _tag_and_insert_sections(conn, doc_id, ex_text, log)
                                    conn.commit()
                                    sections_tagged += secs
                                    docs_fetched += 1
                                    txn_docs += 1
                                    log.info(
                                        "transaction_id=%s 8K_EXHIBIT_21 fetched  label=%r"
                                        "  accession=%s  chars=%d  sections=%d",
                                        txn_id, label, filing.get("accessionNo", "?"),
                                        len(ex_text), secs,
                                    )
                                elif doc_id:
                                    docs_fetched += 1
                                    txn_docs += 1
                                    log.info(
                                        "transaction_id=%s 8K_EXHIBIT_21 UNREADABLE  accession=%s",
                                        txn_id, filing.get("accessionNo", "?"),
                                    )
                            break  # one Exhibit 2.1 per 8-K filing

                elif db_filing_type == "8K_ITEM_201" and item_code:
                    # Query for 8-K filings with item 2.01, extract via Item Extractor
                    filings_8k = _sec.query_filings(
                        cfg, session, ticker, company_name or target_name, announced_date, log
                    )
                    time.sleep(_SLEEP)
                    for filing in filings_8k[:_MAX_FILINGS_PER_TYPE]:
                        filing_items = _sec._parse_filing_items(filing)
                        if item_code not in filing_items:
                            continue
                        filing_url = filing.get("linkToFilingDetails", "")
                        if not filing_url:
                            continue
                        item_text = _sec.fetch_item_text(cfg, session, filing_url, item_code, log)
                        time.sleep(_SLEEP)
                        if item_text:
                            doc_id = _insert_document(conn, txn_id, db_filing_type, filing, item_text, now)
                            conn.commit()
                            if doc_id:
                                secs = _tag_and_insert_sections(conn, doc_id, item_text, log)
                                conn.commit()
                                sections_tagged += secs
                                docs_fetched += 1
                                txn_docs += 1
                                log.info(
                                    "transaction_id=%s %s fetched  chars=%d  sections=%d",
                                    txn_id, db_filing_type, len(item_text), secs,
                                )
                        break  # one 8-K

                else:
                    # DEFM14A / SC_TOT / S4 — query by form type, fetch full document
                    filings = _sec.query_filings_by_formtype(
                        cfg, session, form_type_key,
                        ticker, company_name or target_name,
                        announced_date, log,
                        window_days=90,
                        max_results=_MAX_FILINGS_PER_TYPE,
                    )
                    time.sleep(_SLEEP)
                    if not filings:
                        log.debug("transaction_id=%s no %s filings found", txn_id, db_filing_type)
                        continue

                    for filing in filings[:_MAX_FILINGS_PER_TYPE]:
                        raw_text, status = _sec.fetch_filing_full_text(cfg, session, filing, log)
                        time.sleep(_SLEEP)
                        if status == "SKIP":
                            continue
                        if raw_text or status == "UNREADABLE":
                            doc_id = _insert_document(conn, txn_id, db_filing_type, filing, raw_text, now)
                            conn.commit()
                            if doc_id and raw_text:
                                secs = _tag_and_insert_sections(conn, doc_id, raw_text, log)
                                conn.commit()
                                sections_tagged += secs
                                docs_fetched += 1
                                txn_docs += 1
                                log.info(
                                    "transaction_id=%s %s fetched  accession=%s  chars=%d  sections=%d",
                                    txn_id, db_filing_type,
                                    filing.get("accessionNo", "?"),
                                    len(raw_text), secs,
                                )
                            elif doc_id:
                                docs_fetched += 1
                                txn_docs += 1
                                log.info(
                                    "transaction_id=%s %s UNREADABLE  accession=%s",
                                    txn_id, db_filing_type, filing.get("accessionNo", "?"),
                                )
                        break  # one definitive filing per type

            except RuntimeError as exc:
                # Auth failure — fatal
                log.error("SEC auth failure for transaction_id=%s: %s — halting", txn_id, exc)
                raise
            except Exception as exc:
                log.warning(
                    "transaction_id=%s %s fetch error: %s — continuing",
                    txn_id, db_filing_type, exc,
                )
                error_count += 1

        # Update linked_filings_count on transaction_record
        total_linked = conn.execute(
            "SELECT COUNT(*) FROM transaction_document WHERE transaction_id=?",
            (txn_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE transaction_record SET linked_filings_count=?, updated_at=? WHERE transaction_id=?",
            (total_linked, now, txn_id),
        )
        conn.commit()
        processed += 1
        log.info(
            "transaction_id=%s done  new_docs=%d  total_linked=%d",
            txn_id, txn_docs, total_linked,
        )

    log.info(
        "Stage 10 done  total=%d processed=%d docs_fetched=%d sections_tagged=%d errors=%d",
        total, processed, docs_fetched, sections_tagged, error_count,
    )
    return {
        "transactions_total": total,
        "transactions_processed": processed,
        "documents_fetched": docs_fetched,
        "sections_tagged": sections_tagged,
        "not_triggered": total - processed,
        "sec_doc_errors": error_count,
    }
