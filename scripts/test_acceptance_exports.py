#!/usr/bin/env python3
"""Export projections for the 30-deal acceptance harness — no model calls.

`run_acceptance_30.py` splits into two halves: the seeded run (real stages, needs
credentials) and the read-back projections (pure SQL over a finished database).
This exercises the second half against a hand-built database, so the reviewer
CSVs can be validated without spending a single model call.

What is pinned:

  * an M&A transaction lands in ma_review.csv and NOT in funding_review.csv, and
    a VC_ROUND lands in funding_review.csv and NOT in ma_review.csv -- the split
    is on the canonical event type, not on which columns happen to be populated
  * advisors render readably from the structured participation model, split by
    `advised_side`, carrying specialty and the named client. An advisor whose
    side was never established is reported separately rather than being forced
    onto a side -- the canonical model is projected, never collapsed
  * investors, leads and per-investor amounts reach the funding export
  * multi-source transactions expose every contributing URL and a source count,
    with the seed event id preserved
  * the lifecycle columns are the MVP scalars under mvp_* names, and there is no
    termination-date column to synthesize one into
  * the three reviewer columns exist and ship blank

Run from project root:
    python scripts/test_acceptance_exports.py
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db import get_connection, init_db
from run_acceptance_30 import (  # noqa: E402
    _FUNDING_COLS,
    _MA_COLS,
    build_review_rows,
    load_acceptance_corpus,
    write_csv,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _seed_db(path: str) -> None:
    init_db(path)
    conn = get_connection(path)
    now = datetime.now(timezone.utc).isoformat()

    def source(url, event_id):
        notes = ('{"relevancy": {"reason_code": "UNKNOWN"}, "pl_provenance": '
                 f'{{"event_id": "{event_id}", "source_url": "{url}"}}}}')
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, content_hash, source_status, notes, fetched_at)"
            " VALUES ('WEB_URL','T2',?,?,?,?,?, 'RELEVANT', ?, ?)",
            (url, "t", "2026-08-22", "body", url, notes, now))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def extraction(srid, txn, etype, conf="HIGH"):
        conn.execute(
            "INSERT INTO staging_extraction (source_raw_id, status, v2_event_type,"
            " deal_type, transaction_cluster_id, model_confidence)"
            " VALUES (?, 'CLUSTERED', ?, ?, ?, ?)", (srid, etype, etype, txn, conf))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # --- M&A transaction, two contributing sources -------------------------
    s1 = source("https://example.test/pr-1", "aaaaaaaa-0000-0000-0000-000000000001")
    s2 = source("https://example.test/pr-2", "bbbbbbbb-0000-0000-0000-000000000002")
    e1 = extraction(s1, "tc_ma", "ACQUISITION", "HIGH")
    extraction(s2, "tc_ma", "ACQUISITION", "MEDIUM")     # weakest must win
    conn.execute(
        "INSERT INTO transaction_record (transaction_id, v2_event_type, target_name,"
        " acquirer_name, acquirer_sponsor_name, parent_seller_name, sponsor_transaction_role,"
        " pct_acquired, pct_acquired_source, transaction_status, rumor_date, announced_date,"
        " closed_date, is_take_private, is_current, created_at, updated_at)"
        " VALUES ('tc_ma','ACQUISITION','Verity Bio','Halden Capital','Halden Capital',"
        "'Northwind plc','ADD_ON', 65.0, 'stated', 'ANNOUNCED', '2026-08-01','2026-08-22',"
        " NULL, 0, 1, ?, ?)", (now, now))
    for name, spec, client, side in (
        ("Goldman Sachs", "financial_advisory", "Verity Bio", "SELL_SIDE"),
        ("Wachtell", "legal", "Halden Capital", "BUY_SIDE"),
        ("Ernst & Young", "accounting", "", None),        # side not established
    ):
        conn.execute(
            "INSERT INTO advisor (extraction_id, name, type, advised_party, specialty,"
            " advised_party_name, advised_side, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (e1, name, "FINANCIAL", "UNKNOWN", spec, client or None, side, now))
    conn.execute("INSERT INTO summary (transaction_id, summary_text, prompt_version,"
                 " is_current, created_at)"
                 " VALUES ('tc_ma','Halden Capital agreed to acquire Verity Bio.',"
                 "'deal_summary:test',1,?)", (now,))

    # --- Funding transaction ------------------------------------------------
    s3 = source("https://example.test/round", "cccccccc-0000-0000-0000-000000000003")
    e3 = extraction(s3, "tc_vc", "VC_ROUND", "HIGH")
    conn.execute(
        "INSERT INTO transaction_record (transaction_id, v2_event_type, target_name,"
        " round_label, round, vc_stage, round_size, round_currency, pre_money_valuation,"
        " pct_acquired, pct_acquired_source, transaction_status, announced_date,"
        " is_current, created_at, updated_at)"
        " VALUES ('tc_vc','VC_ROUND','TechCo','Series B','SERIES_B','EARLY_STAGE',"
        " 50000000,'USD', 275000000, NULL, NULL, 'ANNOUNCED','2026-08-23',1,?,?)",
        (now, now))
    for name, itype, lead, rank, amt in (
        ("Venture Partners", "vc_firm", 1, 1, 30000000),
        ("Seed Capital", "vc_firm", 0, 2, None),
    ):
        conn.execute(
            "INSERT INTO staging_investor (extraction_id, name, investor_type, is_lead,"
            " lead_investor_rank, investment_amount, investment_currency, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)", (e3, name, itype, lead, rank, amt, "USD", now))
    conn.execute(
        "INSERT INTO advisor (extraction_id, name, type, advised_party, specialty,"
        " advised_party_name, advised_side, created_at)"
        " VALUES (?,'Cooley','LEGAL','UNKNOWN','legal','TechCo','SELL_SIDE',?)", (e3, now))
    conn.commit()
    conn.close()


def main() -> None:
    print(__doc__.strip().split("\n")[0])
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "acceptance.db")
    _seed_db(db)
    conn = get_connection(db)
    ma, funding, other = build_review_rows(conn)

    print("\nFamily split:")
    check("one M&A row", len(ma), 1)
    check("one funding row", len(funding), 1)
    check("nothing unclassified", len(other), 0)
    check("M&A row is the acquisition", ma[0]["transaction_id"], "tc_ma")
    check("funding row is the round", funding[0]["transaction_id"], "tc_vc")

    m = ma[0]
    print("\nM&A projections:")
    check("target", m["target_name"], "Verity Bio")
    check("acquirer", m["acquirer_name"], "Halden Capital")
    check("acquirer sponsor", m["acquirer_sponsor_name"], "Halden Capital")
    check("parent seller", m["parent_seller_name"], "Northwind plc")
    check("sponsor_transaction_role", m["sponsor_transaction_role"], "ADD_ON")
    check("pct_acquired", m["pct_acquired"], 65.0)
    check("pct_acquired_source", m["pct_acquired_source"], "stated")
    check("buy-side advisors", m["buy_side_advisors"],
          "Wachtell (legal) -> Halden Capital")
    check("sell-side advisors", m["sell_side_advisors"],
          "Goldman Sachs (financial_advisory) -> Verity Bio")
    check("side-not-established advisor is separate",
          m["advisors_side_not_established"], "Ernst & Young (accounting)")
    check("advisor JSON diagnostic present", bool(m["advisors_json"]), True)
    check("two source URLs", m["source_urls"],
          "https://example.test/pr-1; https://example.test/pr-2")
    check("source count", m["source_count"], 2)
    check("seed event id", m["pl_event_id"], "aaaaaaaa-0000-0000-0000-000000000001")
    check("all seed event ids", m["pl_event_ids_all"],
          "aaaaaaaa-0000-0000-0000-000000000001; bbbbbbbb-0000-0000-0000-000000000002")
    check("weakest model confidence surfaces", m["model_confidence"], "MEDIUM")
    check("deal summary", m["deal_summary"],
          "Halden Capital agreed to acquire Verity Bio.")

    print("\nMVP lifecycle columns (scalars, not event-history projections):")
    check("mvp_transaction_status", m["mvp_transaction_status"], "ANNOUNCED")
    check("mvp_rumor_date", m["mvp_rumor_date"], "2026-08-01")
    check("mvp_announced_date", m["mvp_announced_date"], "2026-08-22")
    check("mvp_closed_date is null, not invented", m["mvp_closed_date"], None)
    check("no termination-date column in M&A export",
          any("termination_date" in c for c in _MA_COLS), False)
    check("no termination-date column in funding export",
          any("termination_date" in c for c in _FUNDING_COLS), False)
    check("no unprefixed lifecycle date columns leak into M&A export",
          [c for c in _MA_COLS if c in ("announced_date", "closed_date", "rumor_date",
                                        "transaction_status")], [])

    f = funding[0]
    print("\nFunding projections:")
    check("company", f["company_name"], "TechCo")
    check("round label", f["round_label"], "Series B")
    check("normalized round", f["round"], "SERIES_B")
    check("vc stage", f["vc_stage"], "EARLY_STAGE")
    check("round size", f["round_size"], 50000000)
    check("pre-money", f["pre_money_valuation"], 275000000)
    check("pct_acquired stays null when unstated", f["pct_acquired"], None)
    check("investors", f["investors"],
          "Venture Partners (vc_firm); Seed Capital (vc_firm)")
    check("lead investors", f["lead_investors"], "Venture Partners")
    check("investor amounts", f["investor_amounts"], "Venture Partners: 30000000 USD")
    check("funding advisors, no buy/sell framing forced",
          f["funding_advisors"], "Cooley (legal) -> TechCo")
    check("no buy_side_advisors column in funding export",
          "buy_side_advisors" in _FUNDING_COLS, False)

    print("\nReviewer columns:")
    for col in ("overall_review", "missing_or_wrong_fields", "review_notes"):
        check(f"{col} present in M&A", col in _MA_COLS, True)
        check(f"{col} present in funding", col in _FUNDING_COLS, True)
        check(f"{col} ships blank", m[col], "")

    print("\nCSV round-trip:")
    ma_path = os.path.join(tmp, "ma_review.csv")
    fu_path = os.path.join(tmp, "funding_review.csv")
    write_csv(ma_path, _MA_COLS, ma)
    write_csv(fu_path, _FUNDING_COLS, funding)
    with open(ma_path, newline="", encoding="utf-8") as fh:
        got = list(csv.DictReader(fh))
    check("M&A csv row count", len(got), 1)
    check("M&A csv header matches spec", list(got[0].keys()), _MA_COLS)
    check("advisor text survives the csv", got[0]["sell_side_advisors"],
          "Goldman Sachs (financial_advisory) -> Verity Bio")
    with open(fu_path, newline="", encoding="utf-8") as fh:
        gotf = list(csv.DictReader(fh))
    check("funding csv row count", len(gotf), 1)
    check("funding csv header matches spec", list(gotf[0].keys()), _FUNDING_COLS)

    print("\nNeutral acceptance provenance (no fabricated relevancy verdict):")
    tsv = os.path.join(tmp, "mini.tsv")
    with open(tsv, "w", encoding="utf-8") as fh:
        fh.write("event_id\tsource_url\tsource_title\tsource_body_lite\tsource_published_at\n")
        fh.write("id-1\thttps://e.test/a\tTitle A\tBody A\t2026-08-22\n")
    corpus, missing = load_acceptance_corpus(tsv, ["id-1", "id-absent"])
    check("present id loaded", len(corpus), 1)
    check("absent id reported, not fatal", missing, ["id-absent"])
    rel = corpus[0]["relevancy"]
    check("reason_code is the existing neutral value", rel["reason_code"], "UNKNOWN")
    check("no model confidence invented", rel["model_confidence"], None)
    check("no prompt version invented", rel["prompt_version"], None)
    check("selection marked external", rel["selection"], "PRODUCT_ACCEPTANCE_SAMPLE")

    conn.close()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for x in _failures:
            print(f"    {x}")
        sys.exit(1)
    print(f"{PASS} — acceptance export projections hold; no model call was made")


if __name__ == "__main__":
    main()
