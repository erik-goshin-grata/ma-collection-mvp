#!/usr/bin/env python3
"""Collection-corpus validation — the real decision chain, in an isolated database.

WHAT THIS IS FOR

The acceptance harness seeds every source at `source_status='RELEVANT'` and never
invokes Stage 2. That was deliberate there, but it means relevancy has never run
in a validation, and the downstream noise it produced -- government grants,
permits, legislative proposals reaching high-confidence extraction -- was an
artefact of the bypass rather than a finding about the pipeline.

This runs the chain the product actually has:

    source -> Relevancy -> (if RELEVANT) Classifier -> extraction path -> downstream

A NOT_RELEVANT source stops structurally, not by a filter written here: Stage 3's
own gate is `source_status = 'RELEVANT'`, so a rejected row is never selected.

WHAT THIS IS NOT

Plumbing only. It seeds rows, calls the existing stage modules unchanged, and
reads results back out. It contains no extraction, classification, normalization,
clustering or reconciliation logic, and it is not wired into run.py. Every
judgement belongs to a stage; anything this script had to decide would be a
product decision smuggled into validation tooling.

TWO SOURCE CLASSES, ONE TABLE

  Collection URLs   captured by the page harness. `page.txt` is the interface --
                    the stages read `clean_text` -- and `page.html` rides along in
                    `raw_html` as archive, so a re-extraction never needs a refetch.
  PL events         resolved against the PL exports; `source_body_lite` is the text.
                    No HTML exists for these, and none is invented.

Each source is an independent `source_raw` row. Nothing is merged or deduplicated
before extraction: whether two sources describe one transaction is Stage 8's
question, and answering it here would pre-empt the clustering being validated.

SEEDING AT FETCHED, AND WHY IT MATTERS

Rows land at `source_status='FETCHED'` with **no** `notes["relevancy"]` block.
That absence is the whole point -- it is what makes Stage 2 select the row, and
Stage 2's own `_write()` then populates `notes["relevancy"]`, which is exactly
what Stage 3 reads for its `relevancy_reason_code` input. The chain is
self-consistent with no stage modification and no synthesized verdict.

`source_tier` is 'T2' for every row. T1 is SEC-only and tiering is out of scope
for this exercise; this is existing-reference plumbing, not a source-tier ruling.

REVIEW OUTPUTS

Two clean sheets for Product plus a rejection list. Deliberately excluded:
`mvp_*` labels, transaction-level `acquirer_type` (buyer classification belongs to
the participating entity, not the transaction), a repeated RELEVANT column on
transaction rows, and derived multiples/implied values -- those stay diagnostic in
the DB. `seller_sponsor` and `transaction_terms_disclosure_status` are unavailable
in the reference implementation and are neither proxied nor invented.

Run from project root:
    python scripts/run_collection_validation.py --pages out/collection_20260826/pages \
        --pl-ids sheet2_pl_ids.txt --pl-tsv <pl_export.tsv> \
        --out-dir out/collection_20260826 [--dry-run]

The PL half is optional, as a pair. A URL-only corpus simply omits both:
    python scripts/run_collection_validation.py --pages out/collection_20260901/pages \
        --out-dir out/collection_20260901 [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import get_config                                  # noqa: E402
from db import get_connection, init_db                         # noqa: E402

# The stage modules, imported and invoked unchanged. Stage 2 is the addition that
# distinguishes this from the acceptance harness; stages 5/6 (SEC), 10/11
# (agreement) and 14 (production export) are deliberately absent.
import stages.relevancy_filter as _stage_2                     # noqa: E402
import stages.deal_type_classify as _stage_3                   # noqa: E402
import stages.high_confidence_extract as _stage_4              # noqa: E402
import stages.funding_hc_extract as _stage_4b                  # noqa: E402
import stages.low_confidence_extract as _stage_7               # noqa: E402
import stages.entity_cluster as _stage_8                       # noqa: E402
import stages.aggregate as _stage_9                            # noqa: E402
import stages.summarize as _stage_12                          # noqa: E402
import stages.rationale_tag as _stage_13                      # noqa: E402

PIPELINE = [
    ("stage_2_relevancy", _stage_2),
    ("stage_3_deal_type_classify", _stage_3),
    ("stage_4_high_confidence", _stage_4),
    ("stage_4b_funding_hc", _stage_4b),
    ("stage_7_low_confidence", _stage_7),
    ("stage_8_entity_cluster", _stage_8),
    ("stage_9_aggregate", _stage_9),
    ("stage_12_summarize", _stage_12),
    # Stage 13 reads the current summary row, so it can only follow Stage 12. It is a
    # model call per transaction -- the validation run is one stage longer and one
    # prompt more expensive, which is the cost of putting rationale in front of Product.
    ("stage_13_rationale_tag", _stage_13),
]

# Hosts whose releases are newswire distributions. This picks between two values
# the relevancy gate already accepts -- `source_type IN ('PR_NEWSWIRE','WEB_URL')` --
# and changes no behaviour beyond the label carried on the row.
_NEWSWIRE_HOSTS = (
    "businesswire.com", "prnewswire.com", "globenewswire.com", "cision.com",
)

_FUNDING_TYPES = ("VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT")


# ---------------------------------------------------------------------------
# input assembly
# ---------------------------------------------------------------------------

def _source_type_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return "PR_NEWSWIRE" if any(host.endswith(h) for h in _NEWSWIRE_HOSTS) else "WEB_URL"


def load_url_captures(pages_dir: str) -> tuple[list[dict], list[dict]]:
    """Read page-harness capture directories. Returns (healthy, quarantined).

    The harness gate is applied exactly as the handoff specifies -- `ok and not
    suspect` -- and is not re-derived here. A degraded capture is quarantined
    rather than fed: a truncated page and a complete one look identical to the
    stages, which is the asymmetry the harness exists to catch.
    """
    healthy: list[dict] = []
    quarantined: list[dict] = []
    root = Path(pages_dir)
    if not root.is_dir():
        raise SystemExit(f"ERROR: pages directory not found: {pages_dir}")

    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        meta_path, txt_path, html_path = d / "meta.json", d / "page.txt", d / "page.html"
        if not meta_path.exists():
            quarantined.append({"capture_dir": d.name, "url": None,
                                "reason": "no meta.json in capture directory"})
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        url = meta.get("url") or ""
        ok = bool(meta.get("ok"))
        suspect = bool(meta.get("suspect"))
        blocked = bool(meta.get("blocked"))

        if not (ok and not suspect):
            quarantined.append({
                "capture_dir": d.name, "url": url,
                "ok": ok, "blocked": blocked, "suspect": suspect,
                "via": meta.get("via"),
                "reason": meta.get("block_reason") or ("suspect capture" if suspect
                                                       else "not ok"),
            })
            continue

        text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
        if not text.strip():
            # ok+not-suspect but no text on disk is a harness/storage inconsistency,
            # not a healthy source. Quarantine rather than seed an empty row that
            # the relevancy gate would silently skip.
            quarantined.append({"capture_dir": d.name, "url": url,
                                "reason": "gate passed but page.txt is empty"})
            continue

        healthy.append({
            "kind": "COLLECTION_URL",
            "capture_dir": d.name,
            "url": url,
            "title": meta.get("title") or "",
            "published": meta.get("published") or meta.get("published_date"),
            "text": text,
            "html": html_path.read_text(encoding="utf-8") if html_path.exists() else None,
            "via": meta.get("via"),
            "candidates": meta.get("candidates"),
        })
    return healthy, quarantined


def load_pl_sources(pl_ids_path: str | None,
                    pl_tsv: str | None) -> tuple[list[dict], list[str]]:
    """Resolve PL event IDs against a PL export. Returns (sources, unresolved).

    The two arguments are a pair. Neither is a URL-only corpus and resolves to no PL
    sources -- an empty result, not a failure. One without the other is a mistake
    rather than a corpus: ids with no export resolve nothing and would report every
    id unresolved, and an export with no ids would silently contribute nothing. Both
    look like a successful run in the summary, so this refuses instead of guessing.
    """
    if not pl_ids_path and not pl_tsv:
        return [], []
    if not (pl_ids_path and pl_tsv):
        raise ValueError("load_pl_sources needs a PL id file and a PL export "
                         "together, or neither")
    csv.field_size_limit(10 ** 9)
    wanted = [ln.strip() for ln in open(pl_ids_path, encoding="utf-8")
              if ln.strip() and not ln.startswith("#")]
    by_id: dict[str, dict] = {}
    with open(pl_tsv, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["event_id"] in wanted and row["event_id"] not in by_id:
                by_id[row["event_id"]] = row

    sources = []
    for eid in wanted:                      # preserve the supplied order
        r = by_id.get(eid)
        if not r:
            continue
        sources.append({
            "kind": "PL_EVENT",
            "event_id": eid,
            "url": r.get("source_url") or "",
            "title": r.get("source_title") or "",
            "published": r.get("source_published_at"),
            "text": r.get("source_body_lite") or "",
            "html": None,                   # PL supplies no HTML; none is invented
            "category": r.get("category"),
            "pl_export": os.path.basename(pl_tsv),
        })
    return sources, [e for e in wanted if e not in by_id]


# ---------------------------------------------------------------------------
# seeding -- a plain INSERT into the table Stage 1 writes. No stage is touched.
# ---------------------------------------------------------------------------

def seed(conn, sources: list[dict]) -> dict[str, int]:
    """Insert one source_raw row per source at FETCHED. Returns source_ref -> id.

    No `notes["relevancy"]` key is written. Stage 2 selects on FETCHED and writes
    that key itself; pre-seeding it is exactly the bypass this run exists to remove.
    """
    now = datetime.now(timezone.utc).isoformat()
    mapping: dict[str, int] = {}
    for i, s in enumerate(sources, 1):
        if s["kind"] == "COLLECTION_URL":
            ref = f"url_{i:02d}"
            provenance = {
                "source": "COLLECTION_URL", "source_ref": ref, "url": s["url"],
                "capture_dir": s["capture_dir"], "via": s.get("via"),
                "candidates": s.get("candidates"),
            }
            source_type = _source_type_for(s["url"])
        else:
            ref = f"pl_{s['event_id'][:8]}"
            provenance = {
                "source": "PL_EVENT", "source_ref": ref, "event_id": s["event_id"],
                "pl_export": s.get("pl_export"), "pl_category": s.get("category"),
                "url": s["url"],
            }
            source_type = "WEB_URL"

        body = s["text"]
        cur = conn.execute(
            """
            INSERT INTO source_raw
                (source_type, source_tier, url, title, published_date,
                 raw_html, clean_text, content_hash, source_status, notes, fetched_at)
            VALUES (?, 'T2', ?, ?, ?, ?, ?, ?, 'FETCHED', ?, ?)
            """,
            (source_type, s["url"], s["title"], s["published"], s["html"], body,
             hashlib.sha256(re.sub(r"\s+", " ", body).strip().encode()).hexdigest(),
             json.dumps({"provenance": provenance}), now),
        )
        mapping[ref] = cur.lastrowid
    conn.commit()
    return mapping


# ---------------------------------------------------------------------------
# review projections -- read-only SELECTs over what the stages produced
# ---------------------------------------------------------------------------

def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _num(v):
    """Render a whole REAL as an int so 30000000.0 does not reach a reviewer."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _advisor_label(a: dict) -> str:
    name = (a.get("name") or "").strip()
    spec = (a.get("specialty") or a.get("type") or "").strip()
    client = (a.get("advised_party_name") or "").strip()
    label = f"{name} ({spec})" if spec else name
    return f"{label} -> {client}" if client else label


def advisors_by_transaction(conn) -> dict:
    """transaction_id -> {buy, sell, unassigned}.

    An advisor whose side was never established is listed separately rather than
    forced onto a side -- the participation model records four independent facts
    and collapsing them here would assert one the source did not state.
    """
    out = defaultdict(lambda: {"buy": [], "sell": [], "unassigned": []})
    sql = """
        SELECT se.transaction_cluster_id AS txn, a.name, a.type, a.specialty,
               a.advised_party_name, a.advised_side
        FROM advisor a
        JOIN staging_extraction se ON se.extraction_id = a.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY a.advisor_id
    """
    for a in _rows(conn, sql):
        side = (a.get("advised_side") or "").upper()
        key = "buy" if side == "BUY_SIDE" else "sell" if side == "SELL_SIDE" else "unassigned"
        out[a["txn"]][key].append(_advisor_label(a))
    return out


def investors_by_transaction(conn) -> dict:
    """transaction_id -> {all, leads, amounts} from the funding-path investor model."""
    out = defaultdict(lambda: {"all": [], "leads": [], "amounts": []})
    sql = """
        SELECT se.transaction_cluster_id AS txn, i.name, i.investor_type, i.is_lead,
               i.lead_investor_rank, i.investment_amount, i.investment_currency
        FROM staging_investor i
        JOIN staging_extraction se ON se.extraction_id = i.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY i.is_lead DESC, i.lead_investor_rank, i.investor_id
    """
    for r in _rows(conn, sql):
        name = (r["name"] or "").strip()
        if not name:
            continue
        b = out[r["txn"]]
        itype = (r["investor_type"] or "").strip()
        b["all"].append(f"{name} ({itype})" if itype else name)
        if r["is_lead"]:
            b["leads"].append(name)
        if r["investment_amount"] is not None:
            cur = r["investment_currency"] or ""
            b["amounts"].append(f"{name}: {_num(r['investment_amount'])} {cur}".strip())
    return out


def sources_by_transaction(conn) -> dict:
    """transaction_id -> (source_ref, source_url) from the seeded provenance."""
    out = defaultdict(lambda: {"refs": [], "urls": []})
    sql = """
        SELECT se.transaction_cluster_id AS txn, sr.url, sr.notes
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY se.extraction_id
    """
    for r in _rows(conn, sql):
        ref = None
        try:
            ref = (json.loads(r["notes"] or "{}").get("provenance") or {}).get("source_ref")
        except (ValueError, TypeError):
            pass
        b = out[r["txn"]]
        if ref and ref not in b["refs"]:
            b["refs"].append(ref)
        if r["url"] and r["url"] not in b["urls"]:
            b["urls"].append(r["url"])
    return out


# Bumped whenever the review column set changes. Emitted into diagnostics/run_summary.json
# so a sheet can be tied to the columns that produced it -- otherwise a 61-column and an
# 83-column ma_review.csv are indistinguishable after the fact. Deliberately NOT a column:
# it describes the sheet, not any transaction.
_REVIEW_SHEET_VERSION = "1.3"

# Column order is the review order. Lifecycle scalars are displayed under readable
# names -- status/announced_date/closed_date -- while their MVP provenance stays in
# the database. No mvp_* labels, no transaction-level acquirer_type, no RELEVANT.
_MA_COLS = [
    "source_ref", "source_url", "transaction_id",
    "status", "event_type",
    "announced_date", "announced_date_precision",
    "closed_date", "closed_date_precision",
    "signing_date", "signing_date_precision", "rumor_date",
    "deal_type", "combination_structure", "spin_split_type",
    "target_name", "target_domain", "target_ticker", "target_status",
    "target_type", "asset_type", "target_description",
    "acquirer_name", "acquirer_domain", "acquirer_ticker", "acquirer_description",
    "acquirer_sponsor_name", "sponsor_transaction_role",
    "parent_seller_name", "parent_seller_ticker", "parent_seller_description",
    "pct_acquired", "pct_acquired_source", "stake_transition_type", "is_minority",
    "offer_mechanism",
    "consideration_type", "consideration_components", "has_earnout", "has_cvr",
    "transaction_value", "transaction_value_basis",
    "transaction_size", "transaction_size_basis",
    "equity_value", "equity_value_basis",
    "enterprise_value", "enterprise_value_basis",
    "per_share_price",
    "value_amount", "value_currency", "value_type", "deal_value_currency",
    "target_revenue", "target_revenue_period_type", "target_revenue_period_end",
    "target_ebitda", "target_ebitda_period_type", "target_ebitda_period_end",
    "financials_currency", "financials_disclosure_status", "transaction_terms_disclosure_status",
    "is_take_private", "is_going_private_outcome",
    "is_secondary_buyout", "is_merger_of_equals", "hostile",
    "deal_attitude", "approach_type", "competing_bid", "regulatory_approvals_required",
    "has_go_shop", "go_shop_period_days",
    "target_fee_amount", "target_fee_percentage",
    "acquirer_fee_amount", "acquirer_fee_percentage",
    "buy_side_advisors", "sell_side_advisors", "advisors_side_not_established",
    "deal_rationale", "deal_summary",
    "overall_review", "missing_or_wrong_fields", "review_notes",
]

_FUNDING_COLS = [
    "source_ref", "source_url", "transaction_id",
    "status", "event_type",
    "announced_date", "announced_date_precision",
    "closed_date", "closed_date_precision",
    "company_name", "company_domain", "company_description",
    "round_label", "round", "vc_stage",
    "round_size", "round_currency",
    "pre_money_valuation", "post_money_valuation", "valuation_currency",
    "total_raised_to_date", "facility_size",
    "round_price_direction", "is_extension_round", "is_bridge_round",
    "pct_acquired", "pct_acquired_source", "stake_transition_type",
    "has_board_seat", "use_of_proceeds",
    "investors", "lead_investors", "investor_amounts",
    "funding_advisors", "advisors_side_not_established",
    "target_revenue", "target_revenue_period_type",
    "target_ebitda", "target_ebitda_period_type",
    "financials_currency", "financials_disclosure_status", "transaction_terms_disclosure_status",
    "deal_summary",
    "overall_review", "missing_or_wrong_fields", "review_notes",
]

_REJECTION_COLS = [
    "source_ref", "title", "url", "classification", "reason_code",
    "model_confidence", "relevancy_notes",
]


def rationales_by_transaction(conn) -> dict:
    """One review string per transaction: primary first, then secondaries as stored.

    A projection, not a replacement. `rationale_tag` keeps its own columns and its
    JSON `secondary_rationales` array untouched, Stage 14 still exports them
    separately, and nothing here writes. The sheet gets one readable cell because a
    reviewer reads rows, not arrays.

    Confidence and notes are deliberately absent: this cell answers what the
    rationale was, and a per-cell confidence invites reading it as a score.
    """
    out: dict[str, str] = {}
    for r in _rows(conn, "SELECT transaction_id, primary_rationale, secondary_rationales "
                         "FROM rationale_tag WHERE is_current = 1"):
        parts = [r["primary_rationale"]] if r["primary_rationale"] else []
        try:
            secondaries = json.loads(r["secondary_rationales"] or "[]")
        except (ValueError, TypeError):
            secondaries = []
        parts += [x for x in secondaries if isinstance(x, str) and x and x not in parts]
        if parts:
            out[r["transaction_id"]] = ", ".join(parts)
    return out


def build_review_rows(conn):
    """Split canonical transactions into the M&A and Funding review sheets."""
    adv = advisors_by_transaction(conn)
    rationales = rationales_by_transaction(conn)
    inv = investors_by_transaction(conn)
    src = sources_by_transaction(conn)
    summaries = {r["transaction_id"]: r["summary_text"] for r in _rows(
        conn, "SELECT transaction_id, summary_text FROM summary WHERE is_current = 1")}

    ma, funding = [], []
    for t in _rows(conn, "SELECT * FROM transaction_record WHERE is_current = 1 "
                         "ORDER BY transaction_id"):
        txn = t["transaction_id"]
        s, a, i = src.get(txn, {"refs": [], "urls": []}), adv.get(txn), inv.get(txn)
        a = a or {"buy": [], "sell": [], "unassigned": []}
        i = i or {"all": [], "leads": [], "amounts": []}
        common = {
            "source_ref": " | ".join(s["refs"]),
            "source_url": " | ".join(s["urls"]),
            "transaction_id": txn,
            # Readable lifecycle labels over the MVP scalars; provenance is in the DB.
            "status": t["transaction_status"],
            "event_type": t["event_history_type"],
            "announced_date": t["announced_date"],
            "announced_date_precision": t["announced_date_precision"],
            "closed_date": t["closed_date"],
            "closed_date_precision": t["closed_date_precision"],
            "deal_summary": summaries.get(txn),
            "overall_review": "", "missing_or_wrong_fields": "", "review_notes": "",
            "advisors_side_not_established": "; ".join(a["unassigned"]),
            "pct_acquired": _num(t["pct_acquired"]),
            "pct_acquired_source": t["pct_acquired_source"],
            "stake_transition_type": t["stake_transition_type"],
            "target_revenue": _num(t["target_revenue"]),
            "target_revenue_period_type": t["target_revenue_period_type_v2"],
            "target_ebitda": _num(t["target_ebitda"]),
            "target_ebitda_period_type": t["target_ebitda_period_type_v2"],
            "financials_currency": t["financials_currency"],
            "financials_disclosure_status": t["financials_disclosure_status"],
        }

        if t["v2_event_type"] in _FUNDING_TYPES:
            row = dict(common)
            row.update({
                "company_name": t["target_name"],
                "company_domain": t["target_domain"],
                "company_description": t["target_description"],
                "round_label": t["round_label"], "round": t["round"],
                "vc_stage": t["vc_stage"],
                "round_size": _num(t["round_size"]), "round_currency": t["round_currency"],
                "pre_money_valuation": _num(t["pre_money_valuation"]),
                "post_money_valuation": _num(t["post_money_valuation"]),
                "valuation_currency": t["valuation_currency"],
                "total_raised_to_date": _num(t["total_raised_to_date"]),
                "facility_size": _num(t["facility_size"]),
                "round_price_direction": t["round_price_direction"],
                "is_extension_round": t["is_extension_round"],
                "is_bridge_round": t["is_bridge_round"],
                "has_board_seat": t["has_board_seat"],
                "use_of_proceeds": t["use_of_proceeds"],
                "investors": "; ".join(i["all"]),
                "lead_investors": "; ".join(i["leads"]),
                "investor_amounts": "; ".join(i["amounts"]),
                "funding_advisors": "; ".join(a["buy"] + a["sell"]),
            })
            funding.append({c: row.get(c) for c in _FUNDING_COLS})
        else:
            row = dict(common)
            row.update({
                "deal_type": t["v2_event_type"],
                "combination_structure": t["combination_structure"],
                "spin_split_type": t["spin_split_type_v2"],
                "signing_date": t["signing_date"],
                "signing_date_precision": t["signing_date_precision"],
                "rumor_date": t["rumor_date"],
                "target_name": t["target_name"], "target_status": t["target_status"],
                "target_domain": t["target_domain"], "target_ticker": t["target_ticker"],
                "target_type": t["target_type_v2"], "asset_type": t["asset_type"],
                "target_description": t["target_description"],
                "acquirer_name": t["acquirer_name"],
                "acquirer_domain": t["acquirer_domain"],
                "acquirer_ticker": t["acquirer_ticker"],
                "acquirer_description": t["acquirer_description"],
                "acquirer_sponsor_name": t["acquirer_sponsor_name"],
                "sponsor_transaction_role": t["sponsor_transaction_role"],
                "parent_seller_name": t["parent_seller_name"],
                "parent_seller_ticker": t["parent_seller_ticker"],
                "parent_seller_description": t["parent_seller_description"],
                "is_minority": t["is_minority"],
                "offer_mechanism": t["offer_mechanism"],
                "consideration_type": t["consideration_type"],
                "consideration_components": t["consideration_components"],
                "has_earnout": t["has_earnout"], "has_cvr": t["has_cvr"],
                "transaction_value": _num(t["transaction_value"]),
                "transaction_value_basis": t["transaction_value_basis"],
                "transaction_size": _num(t["transaction_size"]),
                "transaction_size_basis": t["transaction_size_basis"],
                "deal_value_currency": t["deal_value_currency"],
                "equity_value": _num(t["equity_value"]),
                "equity_value_basis": t["equity_value_basis"],
                "enterprise_value": _num(t["enterprise_value"]),
                "enterprise_value_basis": t["enterprise_value_basis"],
                "per_share_price": _num(t["per_share_price"]),
                "value_amount": _num(t["value_amount"]),
                "value_currency": t["value_currency"], "value_type": t["value_type"],
                "target_revenue_period_end": t["target_revenue_period_end"],
                "target_ebitda_period_end": t["target_ebitda_period_end"],
                "is_take_private": t["is_take_private"],
                "is_going_private_outcome": t["is_going_private_outcome"],
                "is_secondary_buyout": t["is_secondary_buyout"],
                "is_merger_of_equals": t["is_merger_of_equals"],
                "hostile": t["hostile"],
                "deal_attitude": t["deal_attitude"], "approach_type": t["approach_type"],
                "competing_bid": t["competing_bid"],
                "regulatory_approvals_required": t["regulatory_approvals_required"],
                "has_go_shop": t["has_go_shop"],
                "go_shop_period_days": t["go_shop_period_days"],
                "target_fee_amount": _num(t["target_fee_amount"]),
                "target_fee_percentage": _num(t["target_fee_percentage"]),
                "acquirer_fee_amount": _num(t["acquirer_fee_amount"]),
                "acquirer_fee_percentage": _num(t["acquirer_fee_percentage"]),
                "buy_side_advisors": "; ".join(a["buy"]),
                "sell_side_advisors": "; ".join(a["sell"]),
                "deal_rationale": rationales.get(txn),
            })
            ma.append({c: row.get(c) for c in _MA_COLS})
    return ma, funding


def build_rejection_rows(conn):
    """Sources Stage 2 rejected. Read from what the stage wrote, not re-derived."""
    out = []
    for r in _rows(conn, "SELECT url, title, notes FROM source_raw "
                         "WHERE source_status = 'NOT_RELEVANT' ORDER BY source_raw_id"):
        try:
            nd = json.loads(r["notes"] or "{}")
        except (ValueError, TypeError):
            nd = {}
        rel = nd.get("relevancy") or {}
        out.append({
            "source_ref": (nd.get("provenance") or {}).get("source_ref"),
            "title": r["title"], "url": r["url"],
            "classification": "NOT_RELEVANT",
            "reason_code": rel.get("reason_code"),
            "model_confidence": rel.get("model_confidence"),
            "relevancy_notes": rel.get("notes"),
        })
    return out


def write_csv(path: str, cols: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", required=True, help="page-harness output directory")
    ap.add_argument("--pl-ids", help="file of PL event IDs, one per line. Optional, "
                                     "but only together with --pl-tsv")
    ap.add_argument("--pl-tsv", help="PL export to resolve those IDs against. Optional, "
                                     "but only together with --pl-ids")
    ap.add_argument("--out-dir", default="out/collection_20260826")
    ap.add_argument("--dry-run", action="store_true",
                    help="Assemble and seed only. No stage runs, no model call.")
    args = ap.parse_args()

    # Enforced here so a half-specified corpus fails on the command line, before the
    # output directory is created and before any capture is read.
    if bool(args.pl_ids) != bool(args.pl_tsv):
        ap.error("--pl-ids and --pl-tsv must be given together, or neither. "
                 "A URL-only corpus supplies neither.")

    healthy, quarantined = load_url_captures(args.pages)
    pl_sources, unresolved = load_pl_sources(args.pl_ids, args.pl_tsv)
    sources = healthy + pl_sources

    os.makedirs(args.out_dir, exist_ok=True)
    diag = os.path.join(args.out_dir, "diagnostics")
    os.makedirs(diag, exist_ok=True)
    db_path = os.path.abspath(os.path.join(args.out_dir, "collection.db"))

    print("=" * 74)
    print("COLLECTION CORPUS VALIDATION — isolated database, real Relevancy")
    print("=" * 74)
    print(f"  URL captures healthy : {len(healthy)}")
    print(f"  URL captures quarantined: {len(quarantined)}")
    print(f"  PL sources resolved  : {len(pl_sources)}   unresolved: {len(unresolved)}")
    for u in unresolved:
        print(f"     UNRESOLVED  {u}")
    print(f"  sources to seed      : {len(sources)}")
    print(f"  isolated DB          : {db_path}")
    print(f"  stages               : {', '.join(n for n, _ in PIPELINE)}")
    print("  NOT run              : scrape 1, SEC 5/6, agreement 10/11, export 14")
    print("  seeding              : source_status=FETCHED, no relevancy pre-seed")

    if os.path.exists(db_path):
        raise SystemExit(f"ERROR: {db_path} already exists. Use a fresh --out-dir — "
                         "this script never appends to an existing validation database.")
    if not sources:
        raise SystemExit("ERROR: no sources assembled; nothing to validate.")

    init_db(db_path)
    conn = get_connection(db_path)
    mapping = seed(conn, sources)
    print(f"\n  seeded {len(mapping)} source_raw rows at source_status='FETCHED'")

    manifest = {
        "sources": [{"source_ref": ref, "source_raw_id": sid} for ref, sid in mapping.items()],
        "quarantined": quarantined,
        "unresolved_pl_ids": unresolved,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    write_csv(os.path.join(diag, "quarantine.csv"),
              ["capture_dir", "url", "ok", "blocked", "suspect", "via", "reason"],
              quarantined)

    if args.dry_run:
        print("\nDRY RUN — sources seeded, no stage invoked, no model call made.")
        conn.close()
        return 0

    cfg = dataclasses.replace(get_config(), db_path=db_path)
    results = {}
    for name, mod in PIPELINE:
        print(f"\n  ── {name} ──")
        try:
            results[name] = mod.run(conn=conn, cfg=cfg, run_id=f"collection_{name}")
            print(f"     {results[name]}")
        except Exception as exc:                                   # noqa: BLE001
            results[name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"     STAGE FAILED — {results[name]['error']}")
        conn.commit()

    ma, funding = build_review_rows(conn)
    rejections = build_rejection_rows(conn)
    write_csv(os.path.join(args.out_dir, "ma_review.csv"), _MA_COLS, ma)
    write_csv(os.path.join(args.out_dir, "funding_review.csv"), _FUNDING_COLS, funding)
    write_csv(os.path.join(args.out_dir, "relevancy_rejections.csv"),
              _REJECTION_COLS, rejections)

    summary = {
        "review_sheet_version": _REVIEW_SHEET_VERSION,
        "stage_results": results,
        "counts": {
            "sources_seeded": len(mapping),
            "url_healthy": len(healthy), "url_quarantined": len(quarantined),
            "pl_resolved": len(pl_sources), "pl_unresolved": len(unresolved),
            "relevancy_rejected": len(rejections),
            "ma_transactions": len(ma), "funding_transactions": len(funding),
        },
    }
    with open(os.path.join(diag, "run_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)

    print("\n" + "=" * 74)
    print(f"  seeded {len(mapping)}  ·  relevancy-rejected {len(rejections)}  ·  "
          f"M&A {len(ma)}  ·  Funding {len(funding)}")
    print(f"  reviews : {args.out_dir}/ma_review.csv, funding_review.csv, "
          "relevancy_rejections.csv")
    print(f"  tracing : {db_path}, {diag}/")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
