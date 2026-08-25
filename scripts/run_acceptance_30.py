"""
scripts/run_acceptance_30.py — Product acceptance run over a selected PL sample,
in an isolated database, with two family-split transaction review exports.

WHAT THIS IS

A bounded acceptance harness. It does not reimplement extraction, classification,
clustering or aggregation: it seeds a fresh database from the PL TSV, calls the
real stage run() functions in order, and then reads the resulting database to
write two reviewer CSVs. Seeding and the stage list are imported from
`run_pl_integration.py` rather than copied, so both harnesses stay on one path.

WHAT RUNS

    Stage 3  deal_type_classify      classifier
    Stage 4  high_confidence_extract HC (non-funding seats)
    Stage 4b funding_hc_extract      HC (funding seats)
    Stage 7  low_confidence_extract  LC  -- deal-type-agnostic; runs on funding too
    Stage 8  entity_cluster          clustering            (no model calls)
    Stage 9  aggregate               observations -> canonical
    Stage 12 summarize               Deal Summary

WHAT DOES NOT

    Stage 5/6  SEC trigger + enrichment. Stage 7's gate is
               `status IN ('HC_EXTRACTED','SEC_NOT_TRIGGERED','SEC_ENRICHED')` and
               HC leaves rows at HC_EXTRACTED, so the SEC path is optional by
               construction rather than by accident. No SEC call is made and no
               SEC failure is manufactured.
    Stage 10/11 agreement extraction. Every source here is a news story with no
               attached agreement.
    Stage 13   Strategic Rationale. Out of scope for this acceptance objective;
               the review exports carry no rationale column.
    Stage 14   Production export. This script writes its own reviewer artifacts.

RELEVANCY PROVENANCE

These event IDs are an externally selected Product acceptance sample, not the
output of a Stage-1 relevancy run, so there is no `results.jsonl` to join and
none is invented. Stage 3 reads `notes.relevancy.reason_code` and already
defaults it to "UNKNOWN" when absent (`deal_type_classify.py:300`), so that is
the value seeded -- the existing neutral representation, not a fabricated model
verdict. The provenance block records the selection as external so a reader
cannot mistake it for a relevancy result. Production relevancy behaviour is
untouched, and Stage 1 and Stage 2 are not run.

LIFECYCLE COLUMNS ARE MVP SCALARS

The reference implementation has no event-history child table. Lifecycle lives
as scalar columns, so the review exports carry `mvp_transaction_status`,
`mvp_rumor_date`, `mvp_announced_date` and `mvp_closed_date`, named for what
they are. They are NOT event-history projections, and no termination date is
synthesized because the MVP has no column for one.

ISOLATION

The pipeline reaches a database only through cfg.db_path. Config is a frozen
dataclass, so overriding that one field with dataclasses.replace is complete
isolation -- the production database is never opened. The real config is used
otherwise; no credential is invented, because the stages that would need one are
not called.

Run (from the repository root, with the repo-root .env in place):

    python scripts/run_acceptance_30.py \\
        --tsv <news_events_full_review.tsv> \\
        --ids scripts/acceptance_30_ids.txt \\
        --out-dir out/acceptance_30 [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_config
from db import get_connection, init_db

# Seeding and the stage list come from the validated integration harness rather
# than a second copy that could drift away from it.
from run_pl_integration import PIPELINE, read_manifest, seed  # noqa: E402


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def load_acceptance_corpus(tsv: str, ids: list[str]) -> tuple[list[dict], list[str]]:
    """Join the selected ids to the TSV. Returns (corpus, missing_ids)."""
    rows: dict[str, dict] = {}
    groups: dict[str, list[str]] = {}
    with open(tsv, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            rows[row["event_id"]] = row
            groups.setdefault(row["source_url"], []).append(row["event_id"])

    missing = [i for i in ids if i not in rows]
    corpus = []
    for i in ids:
        if i not in rows:
            continue
        row = rows[i]
        corpus.append({
            "pl_event_id": i,
            "grouped_event_ids": groups.get(row["source_url"], [i]),
            "url": row["source_url"],
            "title": row["source_title"],
            "body": row["source_body_lite"],
            "published": row["source_published_at"],
            # Neutral acceptance provenance. UNKNOWN is the value Stage 3 already
            # falls back to; `selection` records that a person chose this story so
            # the block is never read as a model relevancy result.
            "relevancy": {
                "reason_code": "UNKNOWN",
                "model_confidence": None,
                "notes": "Externally selected Product acceptance sample; "
                         "no Stage-1 relevancy run.",
                "prompt_version": None,
                "selection": "PRODUCT_ACCEPTANCE_SAMPLE",
            },
        })
    return corpus, missing


# ---------------------------------------------------------------------------
# review projections -- reads only
# ---------------------------------------------------------------------------

def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _num(v):
    """Render a whole REAL as an integer: amounts read as money, not floats."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _advisor_label(a: dict) -> str:
    """'Goldman Sachs (financial_advisory)' or '... -> Acme Corp' when the client is named."""
    name = (a.get("name") or "").strip()
    spec = (a.get("specialty") or a.get("type") or "").strip()
    client = (a.get("advised_party_name") or "").strip()
    label = f"{name} ({spec})" if spec else name
    return f"{label} -> {client}" if client else label


def advisors_by_transaction(conn) -> dict[str, dict]:
    """transaction_id -> {buy_side, sell_side, unassigned, json}.

    Read from the structured advisor participation model. The canonical model is
    not collapsed for the export: `advised_side` and `specialty` are rendered,
    and an advisor whose side was never established is listed separately rather
    than being forced onto a side.
    """
    out: dict[str, dict] = defaultdict(
        lambda: {"buy": [], "sell": [], "unassigned": [], "raw": []})
    sql = """
        SELECT se.transaction_cluster_id AS txn, a.name, a.type, a.specialty,
               a.advised_party_name, a.advised_side
        FROM advisor a
        JOIN staging_extraction se ON se.extraction_id = a.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY a.advisor_id
    """
    for a in _rows(conn, sql):
        bucket = out[a["txn"]]
        label = _advisor_label(a)
        side = (a.get("advised_side") or "").upper()
        if side == "BUY_SIDE":
            bucket["buy"].append(label)
        elif side == "SELL_SIDE":
            bucket["sell"].append(label)
        else:
            bucket["unassigned"].append(label)
        bucket["raw"].append({k: a[k] for k in
                              ("name", "type", "specialty",
                               "advised_party_name", "advised_side")})
    return out


def investors_by_transaction(conn) -> dict[str, dict]:
    """transaction_id -> {all, leads, amounts} from staging_investor."""
    out: dict[str, dict] = defaultdict(lambda: {"all": [], "leads": [], "amounts": []})
    sql = """
        SELECT se.transaction_cluster_id AS txn, i.name, i.investor_type, i.is_lead,
               i.lead_investor_rank, i.investment_amount, i.investment_currency
        FROM staging_investor i
        JOIN staging_extraction se ON se.extraction_id = i.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY i.is_lead DESC, i.lead_investor_rank, i.investor_id
    """
    for r in _rows(conn, sql):
        b = out[r["txn"]]
        name = (r["name"] or "").strip()
        if not name:
            continue
        itype = (r["investor_type"] or "").strip()
        b["all"].append(f"{name} ({itype})" if itype else name)
        if r["is_lead"]:
            b["leads"].append(name)
        if r["investment_amount"] is not None:
            cur = r["investment_currency"] or ""
            b["amounts"].append(f"{name}: {_num(r['investment_amount'])} {cur}".strip())
    return out


def sources_by_transaction(conn) -> dict[str, dict]:
    """transaction_id -> {urls, count, pl_event_ids} via the seeded provenance."""
    out: dict[str, dict] = defaultdict(lambda: {"urls": [], "events": []})
    sql = """
        SELECT DISTINCT se.transaction_cluster_id AS txn, sr.url, sr.notes
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY sr.source_raw_id
    """
    for r in _rows(conn, sql):
        b = out[r["txn"]]
        if r["url"] and r["url"] not in b["urls"]:
            b["urls"].append(r["url"])
        try:
            prov = (json.loads(r["notes"] or "{}") or {}).get("pl_provenance", {})
        except (ValueError, TypeError):
            prov = {}
        eid = prov.get("event_id")
        if eid and eid not in b["events"]:
            b["events"].append(eid)
    return out


def summaries_by_transaction(conn) -> dict[str, dict]:
    out = {}
    for r in _rows(conn, "SELECT transaction_id, summary_text, model_confidence "
                         "FROM summary WHERE is_current = 1"):
        out[r["transaction_id"]] = r
    return out


def staging_confidence(conn) -> dict[str, str]:
    """Highest-priority model_confidence seen on the cluster's staging rows."""
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    best: dict[str, str] = {}
    for r in _rows(conn, "SELECT transaction_cluster_id AS txn, model_confidence "
                         "FROM staging_extraction WHERE transaction_cluster_id IS NOT NULL"):
        mc = (r["model_confidence"] or "").upper()
        if not mc:
            continue
        cur = best.get(r["txn"])
        if cur is None or order.get(mc, 0) < order.get(cur, 0):
            best[r["txn"]] = mc          # keep the WEAKEST, so review sees the floor
    return best


# ---------------------------------------------------------------------------
# export column definitions
# ---------------------------------------------------------------------------

_FUNDING_TYPES = {"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"}

# Blank reviewer columns. PASS / NEEDS_CORRECTION / MATERIAL_MISS.
_REVIEW_COLS = ["overall_review", "missing_or_wrong_fields", "review_notes"]

_COMMON_HEAD = [
    "pl_event_id", "pl_event_ids_all", "transaction_id", "source_url", "source_urls",
    "source_count", "event_type",
    # MVP scalar lifecycle -- NOT event-history projections. No termination date exists.
    "mvp_transaction_status", "mvp_rumor_date", "mvp_announced_date", "mvp_closed_date",
    "model_confidence",
]

_MA_COLS = _COMMON_HEAD + [
    # parties
    "target_name", "target_domain", "target_ticker", "target_status",
    "acquirer_name", "acquirer_domain", "acquirer_ticker", "acquirer_type",
    "parent_seller_name", "parent_seller_ticker", "acquirer_sponsor_name",
    # structure / ownership
    "target_type", "asset_type", "combination_structure", "offer_mechanism",
    "pct_acquired", "pct_acquired_source", "stake_transition_type",
    "sponsor_transaction_role",
    # consideration / value
    "consideration_type", "consideration_components",
    "value_amount", "value_currency", "value_type", "per_share_price",
    "transaction_value", "transaction_value_basis",
    "transaction_size", "transaction_size_basis",
    "equity_value", "equity_value_basis", "implied_equity_value",
    "enterprise_value", "enterprise_value_basis",
    "implied_enterprise_value", "implied_enterprise_value_basis",
    # financials / multiples
    "target_revenue", "target_revenue_period_type", "target_revenue_period_end",
    "target_ebitda", "target_ebitda_period_type", "target_ebitda_period_end",
    "financials_currency", "financials_disclosure_status",
    "net_debt", "total_debt", "cash_st",
    "ev_to_revenue_ltm", "ev_to_ebitda_ltm", "multiple_quality",
    # characteristics
    "is_take_private", "is_going_private_outcome", "is_secondary_buyout",
    "is_merger_of_equals", "deal_attitude", "approach_type", "competing_bid",
    "regulatory_approvals_required", "has_go_shop", "go_shop_period_days",
    "target_fee_amount", "target_fee_percentage",
    "acquirer_fee_amount", "acquirer_fee_percentage",
    # advisors
    "buy_side_advisors", "sell_side_advisors", "advisors_side_not_established",
    # narrative
    "deal_summary",
] + _REVIEW_COLS + ["advisors_json"]

_FUNDING_COLS = _COMMON_HEAD + [
    "company_name", "company_domain", "company_description",
    # round
    "round_label", "round", "vc_stage", "round_size", "round_currency",
    "pre_money_valuation", "post_money_valuation", "valuation_currency",
    "facility_size", "total_raised_to_date", "round_price_direction",
    "is_extension_round", "is_bridge_round",
    "pct_acquired", "pct_acquired_source", "stake_transition_type",
    "consideration_type", "financials_disclosure_status",
    # participants
    "investors", "lead_investors", "investor_amounts",
    # advisors
    "funding_advisors", "advisors_side_not_established",
    # financials
    "target_revenue", "target_revenue_period_type",
    "target_ebitda", "target_ebitda_period_type", "financials_currency",
    # narrative
    "deal_summary",
] + _REVIEW_COLS + ["advisors_json"]


def build_review_rows(conn) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (ma_rows, funding_rows, other_rows)."""
    adv = advisors_by_transaction(conn)
    inv = investors_by_transaction(conn)
    src = sources_by_transaction(conn)
    summ = summaries_by_transaction(conn)
    conf = staging_confidence(conn)

    ma, funding, other = [], [], []
    for t in _rows(conn, "SELECT * FROM transaction_record WHERE is_current = 1 "
                         "ORDER BY transaction_id"):
        tid = t["transaction_id"]
        a = adv.get(tid, {"buy": [], "sell": [], "unassigned": [], "raw": []})
        s = src.get(tid, {"urls": [], "events": []})
        etype = t.get("v2_event_type") or t.get("deal_type") or ""

        base = {
            "pl_event_id": s["events"][0] if s["events"] else "",
            "pl_event_ids_all": "; ".join(s["events"]),
            "transaction_id": tid,
            "source_url": s["urls"][0] if s["urls"] else "",
            "source_urls": "; ".join(s["urls"]),
            "source_count": len(s["urls"]),
            "event_type": etype,
            "mvp_transaction_status": t.get("transaction_status"),
            "mvp_rumor_date": t.get("rumor_date"),
            "mvp_announced_date": t.get("announced_date"),
            "mvp_closed_date": t.get("closed_date"),
            "model_confidence": conf.get(tid, ""),
            "advisors_side_not_established": "; ".join(a["unassigned"]),
            "advisors_json": json.dumps(a["raw"], ensure_ascii=False) if a["raw"] else "",
            "deal_summary": (summ.get(tid) or {}).get("summary_text", ""),
            "overall_review": "",
            "missing_or_wrong_fields": "",
            "review_notes": "",
        }

        if etype in _FUNDING_TYPES:
            i = inv.get(tid, {"all": [], "leads": [], "amounts": []})
            row = dict(base)
            row.update({
                "company_name": t.get("target_name"),
                "company_domain": t.get("target_domain"),
                "company_description": t.get("target_description"),
                "investors": "; ".join(i["all"]),
                "lead_investors": "; ".join(i["leads"]),
                "investor_amounts": "; ".join(i["amounts"]),
                "funding_advisors": "; ".join(a["buy"] + a["sell"]),
            })
            for c in _FUNDING_COLS:
                row.setdefault(c, t.get(c))
            funding.append(row)
        else:
            row = dict(base)
            row.update({
                "buy_side_advisors": "; ".join(a["buy"]),
                "sell_side_advisors": "; ".join(a["sell"]),
            })
            for c in _MA_COLS:
                row.setdefault(c, t.get(c))
            (ma if etype else other).append(row)
    return ma, funding, other


def write_csv(path: str, cols: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--ids", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "acceptance_30_ids.txt"))
    ap.add_argument("--out-dir", default="out/acceptance_30")
    ap.add_argument("--dry-run", action="store_true",
                    help="Seed the isolated DB and stop. No stage runs, no model call.")
    args = ap.parse_args()

    ids = read_manifest(args.ids)
    corpus, missing = load_acceptance_corpus(args.tsv, ids)
    os.makedirs(args.out_dir, exist_ok=True)
    db_path = os.path.abspath(os.path.join(args.out_dir, "acceptance.db"))

    print("=" * 74)
    print("30-DEAL PRODUCT ACCEPTANCE RUN — isolated database")
    print("=" * 74)
    print(f"  ids requested : {len(ids)}")
    print(f"  ids found     : {len(corpus)}   not found: {len(missing)}")
    for m in missing:
        print(f"     NOT FOUND  {m}")
    print(f"  isolated DB   : {db_path}")
    print(f"  stages        : {', '.join(n for n, _ in PIPELINE)}")
    print("  NOT run       : relevancy 1/2, SEC 5/6, agreement 10/11, rationale 13, export 14")
    print("  relevancy     : neutral acceptance provenance (reason_code=UNKNOWN)")

    if os.path.exists(db_path):
        raise SystemExit(f"ERROR: {db_path} already exists. Delete it for a clean run — "
                         "this script never appends to an existing acceptance database.")

    init_db(db_path)
    conn = get_connection(db_path)
    seeded = seed(conn, corpus)
    print(f"\n  seeded {len(seeded)} source_raw rows at source_status='RELEVANT'")

    if args.dry_run:
        print("\nDRY RUN — database seeded, no stage invoked, no model call made.")
        conn.close()
        return 0

    cfg = dataclasses.replace(get_config(), db_path=db_path)
    results = {}
    for name, mod in PIPELINE:
        print(f"\n  ── {name} ──")
        try:
            results[name] = mod.run(conn=conn, cfg=cfg, run_id=f"acceptance30_{name}")
            print(f"     {results[name]}")
        except Exception as exc:                                  # noqa: BLE001
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"     STAGE FAILED — {results[name]['error']}")
        conn.commit()

    ma, funding, other = build_review_rows(conn)
    ma_path = os.path.join(args.out_dir, "ma_review.csv")
    fu_path = os.path.join(args.out_dir, "funding_review.csv")
    write_csv(ma_path, _MA_COLS, ma)
    write_csv(fu_path, _FUNDING_COLS, funding)

    n_src = len(seeded)
    n_ext = conn.execute("SELECT COUNT(*) FROM staging_extraction").fetchone()[0]
    n_cl = conn.execute("SELECT COUNT(DISTINCT transaction_cluster_id) FROM "
                        "staging_extraction WHERE transaction_cluster_id IS NOT NULL"
                        ).fetchone()[0]
    n_txn = conn.execute("SELECT COUNT(*) FROM transaction_record WHERE is_current=1"
                         ).fetchone()[0]
    fails = _rows(conn, "SELECT status, COUNT(*) AS n FROM staging_extraction "
                        "WHERE status LIKE '%FAILED%' GROUP BY status")

    print("\n" + "=" * 74)
    print(f"  sources seeded      : {n_src}")
    print(f"  staging extractions : {n_ext}")
    print(f"  clusters            : {n_cl}")
    print(f"  transactions        : {n_txn}   (M&A {len(ma)} / funding {len(funding)}"
          f" / unclassified {len(other)})")
    for f in fails:
        print(f"  {f['status']:<24}: {f['n']}")
    print(f"\n  M&A review     : {ma_path}   ({len(ma)} rows, {len(_MA_COLS)} cols)")
    print(f"  Funding review : {fu_path}   ({len(funding)} rows, {len(_FUNDING_COLS)} cols)")
    if other:
        print(f"  NOTE: {len(other)} transaction(s) had no event_type and are in neither "
              f"export.")

    json.dump({"stage_results": results,
               "counts": {"sources": n_src, "extractions": n_ext, "clusters": n_cl,
                          "transactions": n_txn, "ma": len(ma), "funding": len(funding),
                          "other": len(other)},
               "ids_not_found": missing},
              open(os.path.join(args.out_dir, "run_summary.json"), "w"), indent=2)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
