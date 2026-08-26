#!/usr/bin/env python3
"""Guard tests for the Collection-corpus validation feeder.

WHAT THIS FILE IS DEFENDING

The feeder is plumbing, and plumbing fails quietly. Two failures would be
invisible in the output and would invalidate the run rather than degrade it:

  1. Seeding a source in a state that skips Relevancy. The acceptance harness
     seeds at RELEVANT and never runs Stage 2; the whole point of this run is
     that it does. A row seeded at the wrong status, or carrying a
     notes["relevancy"] block, produces a plausible-looking transaction sheet
     that silently never asked the relevancy question.
  2. Feeding a degraded capture. A truncated page and a complete one are
     indistinguishable downstream -- that asymmetry is why the page harness has a
     `suspect` flag at all -- so the gate must be applied on the way in, not
     inferred later from a short extraction.

Everything else here is a projection guard: the review sheets must not
reintroduce the fields Product excluded, and must not quietly drop the ones it
asked for.

LAYER

No model calls and no network. Stage 2 is exercised only through its gate -- the
SQL that decides which rows it selects -- run against a real SQLite database
built by the production `init_db`. Synthetic capture directories stand in for the
page harness, so this runs anywhere, including without the real corpus.

Run from project root:
    python scripts/test_collection_validation.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from db import get_connection, init_db                          # noqa: E402
import run_collection_validation as fv                          # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _capture(root: Path, name: str, url: str, *, ok=True, suspect=False,
             blocked=False, text="A" * 900, html="<html>x</html>", title="T",
             write_meta=True, write_txt=True) -> None:
    d = root / name
    d.mkdir(parents=True)
    if write_meta:
        (d / "meta.json").write_text(json.dumps({
            "url": url, "ok": ok, "suspect": suspect, "blocked": blocked,
            "title": title, "published": "2026-08-25", "via": "curl_cffi:chrome",
            "candidates": {"trafilatura": len(text), "readability": len(text) - 12},
            "block_reason": "cloudflare wall" if blocked else None,
        }), encoding="utf-8")
    if write_txt:
        (d / "page.txt").write_text(text, encoding="utf-8")
    (d / "page.html").write_text(html, encoding="utf-8")


def main() -> None:
    print(__doc__.strip().split("\n")[0])
    tmp = Path(tempfile.mkdtemp())

    # ------------------------------------------------------------------ gate
    print("\nThe harness gate is applied on the way in (ok and not suspect):")
    pages = tmp / "pages"
    _capture(pages, "a_healthy", "https://www.businesswire.com/news/home/1/en/A")
    _capture(pages, "b_healthy", "https://kedaara.com/deal/")
    _capture(pages, "c_blocked", "https://www.businesswire.com/news/home/2/en/B",
             ok=False, blocked=True, text="")
    _capture(pages, "d_suspect", "https://example.test/short", suspect=True, text="tiny")
    _capture(pages, "e_no_meta", "https://example.test/nometa", write_meta=False)
    _capture(pages, "f_empty_txt", "https://example.test/empty", text="   ")

    healthy, quarantined = fv.load_url_captures(str(pages))
    check("only healthy captures are eligible", sorted(h["capture_dir"] for h in healthy),
          ["a_healthy", "b_healthy"])
    check("blocked / suspect / meta-less / empty are quarantined",
          sorted(q["capture_dir"] for q in quarantined),
          ["c_blocked", "d_suspect", "e_no_meta", "f_empty_txt"])
    check("a quarantined capture keeps its reason",
          any(q.get("reason") == "cloudflare wall" for q in quarantined), True)
    # ok+not-suspect with no text on disk is an inconsistency, not a healthy source.
    check("gate-passing capture with empty page.txt is still quarantined",
          any(q["capture_dir"] == "f_empty_txt" for q in quarantined), True)

    print("\nCollection URLs and PL events are labelled by host, both gate-eligible:")
    check("newswire host -> PR_NEWSWIRE",
          fv._source_type_for("https://www.prnewswire.com/news-releases/x.html"),
          "PR_NEWSWIRE")
    check("globenewswire -> PR_NEWSWIRE",
          fv._source_type_for("https://www.globenewswire.com/news-release/x"), "PR_NEWSWIRE")
    check("long-tail host -> WEB_URL", fv._source_type_for("https://towerbrook.com/x/"),
          "WEB_URL")

    # ------------------------------------------------------------- PL resolve
    print("\nPL events resolve in supplied order; unresolved ids are reported:")
    tsv = tmp / "pl.tsv"
    tsv.write_text(
        "event_id\tcategory\tsource_url\tsource_title\tsource_published_at\tsource_body_lite\n"
        "id-b\tacquires\thttps://e.test/b\tB title\t2026-08-24\tB body\n"
        "id-a\tinvests_into\thttps://e.test/a\tA title\t2026-08-23\tA body\n",
        encoding="utf-8")
    ids = tmp / "ids.txt"
    ids.write_text("# comment\nid-a\nid-b\nid-missing\n", encoding="utf-8")
    pl, unresolved = fv.load_pl_sources(str(ids), str(tsv))
    check("resolved in the order supplied, not file order",
          [p["event_id"] for p in pl], ["id-a", "id-b"])
    check("unresolved reported, not silently dropped", unresolved, ["id-missing"])
    check("PL sources carry no HTML", [p["html"] for p in pl], [None, None])

    # ------------------------------------------------------------- seeding
    print("\nSeeding lands every source where Stage 2 will actually see it:")
    db = str(tmp / "c.db")
    init_db(db)
    conn = get_connection(db)
    mapping = fv.seed(conn, healthy + pl)
    check("one row per source", len(mapping), 4)

    rows = [dict(r) for r in conn.execute(
        "SELECT source_type, source_tier, source_status, url, clean_text, raw_html, "
        "content_hash, notes FROM source_raw ORDER BY source_raw_id")]
    check("every row seeded at FETCHED", {r["source_status"] for r in rows}, {"FETCHED"})
    # The absence of this key is what makes Stage 2 run at all.
    check("no relevancy verdict is pre-seeded",
          any("relevancy" in json.loads(r["notes"]) for r in rows), False)
    check("provenance recorded for tracing",
          all(json.loads(r["notes"])["provenance"].get("source_ref") for r in rows), True)
    check("source_tier is T2 plumbing only", {r["source_tier"] for r in rows}, {"T2"})
    check("URL sources retain page.html as archive",
          [bool(r["raw_html"]) for r in rows], [True, True, False, False])
    check("PL sources have no raw_html",
          [r["raw_html"] for r in rows if r["url"].startswith("https://e.test")],
          [None, None])
    check("content_hash populated for every row",
          all(len(r["content_hash"]) == 64 for r in rows), True)

    # The real assertion: the production Stage 2 gate selects all of them.
    gate = conn.execute(
        """SELECT COUNT(*) FROM source_raw
           WHERE source_status = 'FETCHED'
             AND source_type IN ('PR_NEWSWIRE', 'WEB_URL')
             AND clean_text IS NOT NULL AND clean_text != ''""").fetchone()[0]
    check("Stage 2's own gate selects every seeded source", gate, 4)
    # And Stage 3's gate selects none of them yet -- relevancy has not run.
    stage3 = conn.execute(
        "SELECT COUNT(*) FROM source_raw WHERE source_status = 'RELEVANT'").fetchone()[0]
    check("Stage 3's gate selects nothing before relevancy runs", stage3, 0)

    # ------------------------------------------------------------- rejections
    print("\nNOT_RELEVANT sources are reported from what Stage 2 wrote:")
    sid = list(mapping.values())[0]
    nd = json.loads(conn.execute(
        "SELECT notes FROM source_raw WHERE source_raw_id=?", (sid,)).fetchone()[0])
    nd["relevancy"] = {"reason_code": "OTHER_NOT_RELEVANT", "model_confidence": "HIGH",
                       "notes": "a sale process, not a transaction",
                       "prompt_version": "relevancy_filter:0.9"}
    conn.execute("UPDATE source_raw SET source_status='NOT_RELEVANT', notes=? "
                 "WHERE source_raw_id=?", (json.dumps(nd), sid))
    conn.commit()
    rej = fv.build_rejection_rows(conn)
    check("one rejection row", len(rej), 1)
    check("reason code carried through", rej[0]["reason_code"], "OTHER_NOT_RELEVANT")
    check("relevancy notes carried through",
          rej[0]["relevancy_notes"], "a sale process, not a transaction")
    check("rejection traces to its source", bool(rej[0]["source_ref"]), True)

    # ------------------------------------------------------------- projections
    print("\nReview sheets project one M&A and one Funding transaction:")
    now = "2026-08-25T00:00:00Z"
    conn.execute("""INSERT INTO staging_extraction
        (extraction_id, source_raw_id, status, transaction_cluster_id, created_at, updated_at)
        VALUES (901, ?, 'AGGREGATED', 'tc_ma', ?, ?)""", (list(mapping.values())[1], now, now))
    conn.execute("""INSERT INTO staging_extraction
        (extraction_id, source_raw_id, status, transaction_cluster_id, created_at, updated_at)
        VALUES (902, ?, 'AGGREGATED', 'tc_fund', ?, ?)""", (list(mapping.values())[2], now, now))
    conn.execute("""INSERT INTO transaction_record
        (transaction_id, v2_event_type, transaction_status, event_history_type,
         announced_date, closed_date, target_name, target_type_v2, asset_type,
         acquirer_name, acquirer_type_v2, acquirer_sponsor_name, sponsor_transaction_role,
         transaction_value, transaction_value_basis, equity_value, enterprise_value,
         per_share_price, value_amount, value_currency, value_type,
         consideration_type, target_fee_amount, target_fee_percentage,
         acquirer_fee_amount, acquirer_fee_percentage, is_take_private,
         is_secondary_buyout, is_merger_of_equals, deal_attitude, approach_type,
         competing_bid, regulatory_approvals_required, has_go_shop,
         pct_acquired, pct_acquired_source, is_current, created_at, updated_at)
        VALUES ('tc_ma','ACQUISITION','ANNOUNCED','ANNOUNCED','2026-08-24',NULL,
                'Target Co','standalone_company',NULL,'Buyer Inc','strategic_corporate',
                NULL,NULL,220500000.0,'STATED',220500000.0,NULL,NULL,220500000.0,'USD',
                'TRANSACTION_VALUE','CASH',5000000.0,3.5,7000000.0,4.25,0,0,0,
                'FRIENDLY',NULL,0,1,0,100.0,'assumed',1,?,?)""", (now, now))
    conn.execute("""INSERT INTO transaction_record
        (transaction_id, v2_event_type, transaction_status, event_history_type,
         announced_date, target_name, target_description, round_label, round, vc_stage,
         round_size, round_currency, pre_money_valuation, post_money_valuation,
         valuation_currency, total_raised_to_date, use_of_proceeds, has_board_seat,
         is_current, created_at, updated_at)
        VALUES ('tc_fund','VC_ROUND','ANNOUNCED','ANNOUNCED','2026-08-24','NewCo',
                'A company','Series A','Series A','EARLY_STAGE',150000000.0,'USD',
                900000000.0,1050000000.0,'USD',200000000.0,'scale ops',1,1,?,?)""",
                 (now, now))
    conn.execute("INSERT INTO advisor (extraction_id, name, type, advised_party, "
                 "specialty, advised_party_name, advised_side) VALUES "
                 "(901,'Goldman','bank','buyer','financial_advisory','Buyer Inc','BUY_SIDE')")
    conn.execute("INSERT INTO advisor (extraction_id, name, type, advised_party, "
                 "specialty, advised_party_name, advised_side) VALUES "
                 "(901,'Latham','law','target','legal_advisory','Target Co','SELL_SIDE')")
    conn.execute("INSERT INTO advisor (extraction_id, name, type, advised_party, "
                 "specialty, advised_party_name, advised_side) VALUES "
                 "(901,'Anon LLP','bank','unknown',NULL,NULL,NULL)")
    conn.execute("INSERT INTO staging_investor (extraction_id, name, investor_type, "
                 "is_lead, lead_investor_rank, investment_amount, investment_currency) "
                 "VALUES (902,'Big Fund','vc_firm',1,1,100000000.0,'USD')")
    conn.execute("INSERT INTO staging_investor (extraction_id, name, investor_type, "
                 "is_lead, investment_amount) VALUES (902,'Small Fund','vc_firm',0,NULL)")
    conn.execute("INSERT INTO summary (transaction_id, summary_text, is_current, "
                 "prompt_version) VALUES ('tc_ma','Buyer Inc acquires Target Co.',1,'0.16')")
    conn.commit()

    ma, funding = fv.build_review_rows(conn)
    check("one M&A row", len(ma), 1)
    check("one Funding row", len(funding), 1)
    # 57 approved fields + the 4 termination-fee fields added on approval.
    check("M&A column count is 61", len(fv._MA_COLS), 61)
    check("Funding column count is 41", len(fv._FUNDING_COLS), 41)
    check("M&A row emits exactly the declared columns", list(ma[0].keys()), fv._MA_COLS)
    check("Funding row emits exactly the declared columns",
          list(funding[0].keys()), fv._FUNDING_COLS)
    check("funding transactions are not on the M&A sheet",
          [r["transaction_id"] for r in ma], ["tc_ma"])

    print("\nThe four termination-fee fields are present, under canonical names:")
    for f, v in (("target_fee_amount", 5000000), ("target_fee_percentage", 3.5),
                 ("acquirer_fee_amount", 7000000), ("acquirer_fee_percentage", 4.25)):
        check(f"{f} projected", ma[0][f], v)

    print("\nThe value ladder is present and the diagnostic triple sits beside it:")
    check("transaction_value", ma[0]["transaction_value"], 220500000)
    check("transaction_value_basis", ma[0]["transaction_value_basis"], "STATED")
    check("equity_value", ma[0]["equity_value"], 220500000)
    check("enterprise_value present as a column even when null",
          "enterprise_value" in ma[0], True)
    check("value_amount retained as diagnostic companion",
          ma[0]["value_amount"], 220500000)
    check("whole REALs render as ints, not 220500000.0",
          isinstance(ma[0]["transaction_value"], int), True)

    print("\nLifecycle scalars are displayed readably, without mvp_ labels:")
    check("status", ma[0]["status"], "ANNOUNCED")
    check("announced_date", ma[0]["announced_date"], "2026-08-24")
    check("no mvp_ column anywhere",
          [c for c in fv._MA_COLS + fv._FUNDING_COLS if c.startswith("mvp_")], [])

    print("\nExcluded surfaces stay excluded:")
    for col in ("acquirer_type", "acquirer_type_v2"):
        check(f"{col} absent from both sheets",
              col in fv._MA_COLS or col in fv._FUNDING_COLS, False)
    for col in ("relevant", "RELEVANT", "classification", "reason_code"):
        check(f"{col!r} absent from transaction sheets",
              col in fv._MA_COLS or col in fv._FUNDING_COLS, False)
    for col in ("ev_to_revenue_ltm", "ev_to_ebitda_ltm", "multiple_quality",
                "implied_equity_value", "implied_enterprise_value"):
        check(f"derived {col} stays diagnostic",
              col in fv._MA_COLS or col in fv._FUNDING_COLS, False)
    # Confirmed reference gaps: absent, and not proxied by a lookalike.
    for col in ("seller_sponsor", "seller_sponsor_name",
                "transaction_terms_disclosure", "transaction_terms_disclosure_status"):
        check(f"unavailable {col} not invented",
              col in fv._MA_COLS or col in fv._FUNDING_COLS, False)
    check("financials_disclosure_status kept under its own name, not repurposed",
          "financials_disclosure_status" in fv._MA_COLS, True)

    print("\nThe three blank reviewer columns are present and empty:")
    for col in ("overall_review", "missing_or_wrong_fields", "review_notes"):
        check(f"{col} on both sheets",
              col in fv._MA_COLS and col in fv._FUNDING_COLS, True)
        check(f"{col} blank on the M&A row", ma[0][col], "")
        check(f"{col} blank on the Funding row", funding[0][col], "")

    print("\nAdvisors and investors project from the structured models:")
    check("buy-side advisor labelled with specialty and client",
          ma[0]["buy_side_advisors"], "Goldman (financial_advisory) -> Buyer Inc")
    check("sell-side advisor separate",
          ma[0]["sell_side_advisors"], "Latham (legal_advisory) -> Target Co")
    # An advisor whose side was never established must not be forced onto a side.
    # The label falls back to `type` when `specialty` is absent -- verbatim existing
    # harness behaviour, kept rather than "improved", so both exports read alike.
    check("side-unestablished advisor listed separately",
          ma[0]["advisors_side_not_established"], "Anon LLP (bank)")
    check("investors listed with type", funding[0]["investors"],
          "Big Fund (vc_firm); Small Fund (vc_firm)")
    check("lead investor identified", funding[0]["lead_investors"], "Big Fund")
    check("per-investor amount projected", funding[0]["investor_amounts"],
          "Big Fund: 100000000 USD")
    check("funding fields projected", funding[0]["vc_stage"], "EARLY_STAGE")
    check("total_raised_to_date projected", funding[0]["total_raised_to_date"], 200000000)
    check("deal_summary projected from the current summary",
          ma[0]["deal_summary"], "Buyer Inc acquires Target Co.")
    check("source provenance reaches the review row",
          bool(ma[0]["source_ref"]) and bool(ma[0]["source_url"]), True)

    # ------------------------------------------------------------- isolation
    print("\nIsolation and scope:")
    src = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")
    check("never references the production DB path", "data/ma_mvp.db" in src, False)
    check("isolates via dataclasses.replace on db_path",
          "dataclasses.replace(get_config(), db_path=db_path)" in src, True)
    check("refuses to append to an existing validation DB",
          "already exists" in src, True)
    check("not wired into run.py",
          "run_collection_validation" in (ROOT / "run.py").read_text(encoding="utf-8"),
          False)
    check("stage modules imported, not reimplemented",
          all(f"import stages.{m}" in src for m in
              ("relevancy_filter", "deal_type_classify", "high_confidence_extract",
               "funding_hc_extract", "low_confidence_extract", "entity_cluster",
               "aggregate", "summarize")), True)
    check("SEC / agreement / rationale / export stages absent",
          any(s in src for s in ("sec_enrich", "sec_trigger", "agreement_extract",
                                 "rationale_tag", "stages.export")), False)
    check("stage 2 runs first", fv.PIPELINE[0][0], "stage_2_relevancy")
    check("pipeline is the eight approved stages", len(fv.PIPELINE), 8)
    check("feeder writes no transaction/staging tables itself",
          any(s in src for s in ("INSERT INTO transaction_record",
                                 "INSERT INTO staging_extraction",
                                 "UPDATE transaction_record")), False)
    check("feeder inserts only into source_raw",
          src.count("INSERT INTO") == 1 and "INSERT INTO source_raw" in src, True)

    conn.close()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — feeder seeds for real relevancy, projects clean reviews, "
          f"touches nothing else")


if __name__ == "__main__":
    main()
