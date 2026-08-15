#!/usr/bin/env python3
"""Export a compact one-row-per-transaction XLSX review sheet.

This is a standalone human-review export. It intentionally does not alter the
production Stage 14 CSV export.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import zipfile
from collections import defaultdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Iterable


COLUMNS = [
    "review_verdict",
    "review_notes",
    "transaction_id",
    "source_title",
    "source_url",
    "announced_date",
    "closed_date",
    "transaction_status",
    "event_type",
    "target_name",
    "target_type",
    "acquirers",
    "investors",
    "sellers",
    "parent_seller",
    "acquirer_sponsors",
    "seller_sponsors",
    "lenders_financing_providers",
    "target_financial_advisors",
    "target_legal_advisors",
    "buyer_financial_advisors",
    "buyer_legal_advisors",
    "seller_financial_advisors",
    "seller_legal_advisors",
    "other_advisors",
    "is_minority",
    "stake_transition_type",
    "pct_acquired",
    "is_take_private",
    "is_de_spac",
    "is_add_on",
    "is_divestiture",
    "is_platform_investment",
    "is_secondary_buyout",
    "is_merger_of_equals",
    "per_share_price",
    "exchange_ratio",
    "consideration_type",
    "consideration_summary",
    "round_label",
    "round_stage_category",
    "round_size",
    "pre_money_valuation",
    "post_money_valuation",
    "lead_investors",
    "deal_value_currency",
    "equity_value",
    "equity_value_basis",
    "transaction_value",
    "transaction_value_basis",
    "transaction_size",
    "transaction_size_basis",
    "implied_equity_value",
    "implied_enterprise_value",
    "implied_enterprise_value_basis",
    "total_debt",
    "cash_equivalents",
    "net_debt",
    "financials_currency",
    "revenue",
    "revenue_period",
    "ebitda",
    "ebitda_period",
    "ev_revenue_multiple",
    "ev_ebitda_multiple",
    "multiple_quality",
    "transaction_summary",
]


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _has_table(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _append_unique(values: list[str], value: object) -> None:
    text = "" if value is None else str(value).strip()
    if text and text not in values:
        values.append(text)


def _join(values: Iterable[str]) -> str:
    return " | ".join(v for v in values if v)


def _compact_bool(value: object) -> str:
    if value is None:
        return ""
    try:
        return "Y" if int(value) else "N"
    except (TypeError, ValueError):
        return str(value)


def _fmt_num(value: object) -> object:
    return "" if value is None else value


def _period(period_type: object, period_end: object) -> str:
    parts = [str(p).strip() for p in (period_type, period_end) if p not in (None, "")]
    return " / ".join(parts)


def _multiple(ltm: object, ntm: object) -> str:
    parts: list[str] = []
    for label, value in (("LTM", ltm), ("NTM", ntm)):
        if value is None:
            continue
        try:
            parts.append(f"{label}={float(value):.1f}x")
        except (TypeError, ValueError):
            parts.append(f"{label}={value}")
    return " | ".join(parts)


def _review_transaction_size(r: dict[str, object], tr_cols: set[str]) -> tuple[object, str]:
    if "transaction_size" in tr_cols and r.get("transaction_size") is not None:
        return r.get("transaction_size"), str(r.get("transaction_size_basis") or "")
    event_type = r.get("v2_event_type") or r.get("deal_type") or r.get("event_type")
    if event_type in {"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"} and r.get("round_size") is not None:
        return r.get("round_size"), "ROUND_SIZE"
    if r.get("transaction_value") is not None:
        return r.get("transaction_value"), "TRANSACTION_VALUE"
    return "", ""


def _load_sources(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not (_has_table(conn, "transaction_source") and _has_table(conn, "source_raw")):
        return {}
    rows = conn.execute(
        """
        SELECT ts.transaction_id, sr.title, sr.url
        FROM transaction_source ts
        JOIN source_raw sr ON sr.source_raw_id = ts.source_raw_id
        ORDER BY ts.transaction_id, ts.role, sr.source_raw_id
        """
    ).fetchall()
    titles: dict[str, list[str]] = defaultdict(list)
    urls: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        _append_unique(titles[row["transaction_id"]], row["title"])
        _append_unique(urls[row["transaction_id"]], row["url"])
    return {
        tid: {"source_title": _join(titles[tid]), "source_url": _join(urls[tid])}
        for tid in set(titles) | set(urls)
    }


def _load_advisors(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not (_has_table(conn, "advisor") and _has_table(conn, "staging_extraction")):
        return {}
    rows = conn.execute(
        """
        SELECT se.transaction_cluster_id AS transaction_id, a.name, a.type, a.advised_party
        FROM advisor a
        JOIN staging_extraction se ON se.extraction_id = a.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY se.transaction_cluster_id, a.advised_party, a.type, a.name
        """
    ).fetchall()
    field_map = {
        ("TARGET", "FINANCIAL"): "target_financial_advisors",
        ("TARGET", "LEGAL"): "target_legal_advisors",
        ("ACQUIRER", "FINANCIAL"): "buyer_financial_advisors",
        ("ACQUIRER", "LEGAL"): "buyer_legal_advisors",
        ("PARENT_SELLER", "FINANCIAL"): "seller_financial_advisors",
        ("PARENT_SELLER", "LEGAL"): "seller_legal_advisors",
    }
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        tid = row["transaction_id"]
        name = (row["name"] or "").strip()
        if not tid or not name:
            continue
        advisor_type = (row["type"] or "UNKNOWN").strip()
        advised_party = (row["advised_party"] or "UNKNOWN").strip()
        field = field_map.get((advised_party, advisor_type))
        if field:
            _append_unique(grouped[tid][field], name)
        else:
            _append_unique(grouped[tid]["other_advisors"], f"{name} [{advisor_type}, {advised_party}]")
    return {
        tid: {field: _join(values) for field, values in fields.items()}
        for tid, fields in grouped.items()
    }


def _load_participants(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not (_has_table(conn, "transaction_participant") and _has_table(conn, "entity")):
        return {}
    rows = conn.execute(
        """
        SELECT
            tp.transaction_id,
            tp.side,
            tp.participant_role,
            tp.is_lead,
            tp.is_existing_investor,
            tp.is_new_investor,
            e.canonical_name,
            e.entity_type
        FROM transaction_participant tp
        JOIN entity e ON e.entity_id = tp.entity_id
        WHERE tp.is_current = 1
        ORDER BY tp.transaction_id, tp.side, tp.participant_role, tp.is_primary DESC, e.canonical_name
        """
    ).fetchall()
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        tid = row["transaction_id"]
        name = (row["canonical_name"] or "").strip()
        role = (row["participant_role"] or "").strip()
        side = (row["side"] or "").strip()
        if not tid or not name:
            continue
        label = name
        if role == "INVESTOR":
            flags = []
            if row["is_lead"]:
                flags.append("lead")
            if row["is_existing_investor"]:
                flags.append("existing")
            if row["is_new_investor"]:
                flags.append("new")
            if flags:
                label = f"{name} [{', '.join(flags)}]"
        if role in {"ACQUIRER", "BUYER_PLATFORM", "PARENT_ACQUIRER", "MERGER_SUB"} or side == "BUYER" and role not in {"BUYER_SPONSOR", "INVESTOR", "LENDER"}:
            _append_unique(grouped[tid]["acquirers"], name)
        elif role == "INVESTOR":
            _append_unique(grouped[tid]["investors"], label)
            if row["is_lead"]:
                _append_unique(grouped[tid]["lead_investors"], name)
        elif role in {"SELLER", "PARENT_SELLER", "SELLER_PLATFORM"} or side == "SELLER" and role != "SELLER_SPONSOR":
            _append_unique(grouped[tid]["sellers"], name)
        elif role == "BUYER_SPONSOR":
            _append_unique(grouped[tid]["buyer_sponsors"], name)
        elif role == "SELLER_SPONSOR":
            _append_unique(grouped[tid]["seller_sponsors"], name)
        elif role == "LENDER":
            _append_unique(grouped[tid]["lenders_financing_providers"], name)
    return {
        tid: {field: _join(values) for field, values in fields.items()}
        for tid, fields in grouped.items()
    }


def _load_staging_investors(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    if not (_has_table(conn, "staging_investor") and _has_table(conn, "staging_extraction")):
        return {}
    rows = conn.execute(
        """
        SELECT
            se.transaction_cluster_id AS transaction_id,
            si.name,
            si.investor_type,
            si.is_lead,
            si.is_existing_investor,
            si.is_new_investor
        FROM staging_investor si
        JOIN staging_extraction se ON se.extraction_id = si.extraction_id
        WHERE se.transaction_cluster_id IS NOT NULL
        ORDER BY se.transaction_cluster_id, si.is_lead DESC, si.lead_investor_rank, si.name
        """
    ).fetchall()
    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        tid = row["transaction_id"]
        name = (row["name"] or "").strip()
        if not tid or not name:
            continue
        flags = []
        if row["is_lead"]:
            flags.append("lead")
        if row["is_existing_investor"]:
            flags.append("existing")
        if row["is_new_investor"]:
            flags.append("new")
        if row["investor_type"]:
            flags.append(row["investor_type"])
        label = f"{name} [{', '.join(flags)}]" if flags else name
        _append_unique(grouped[tid]["investors"], label)
        if row["is_lead"]:
            _append_unique(grouped[tid]["lead_investors"], name)
        if row["investor_type"] == "lender":
            _append_unique(grouped[tid]["lenders_financing_providers"], name)
    return {
        tid: {field: _join(values) for field, values in fields.items()}
        for tid, fields in grouped.items()
    }


def _summarize_consideration(raw: object) -> tuple[str, str]:
    if not raw:
        return "", ""
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return str(raw), ""
    if not isinstance(data, list):
        return str(raw), ""
    parts: list[str] = []
    exchange_ratios: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        form = item.get("form") or item.get("consideration_form") or item.get("type") or "COMPONENT"
        attrs: list[str] = []
        amount = item.get("amount") or item.get("per_share_amount") or item.get("value")
        currency = item.get("currency") or item.get("value_currency")
        if amount is not None:
            attrs.append(f"{currency + ' ' if currency else ''}{amount}")
        per_share = item.get("per_share") or item.get("per_share_price")
        if per_share is not None:
            attrs.append(f"{per_share}/share")
        ratio = item.get("exchange_ratio")
        if ratio is not None:
            attrs.append(f"{ratio}x")
            _append_unique(exchange_ratios, ratio)
        trigger = item.get("trigger") or item.get("trigger_description")
        if trigger:
            attrs.append(str(trigger))
        parts.append(f"{form} {'; '.join(attrs)}".strip())
    return _join(parts), _join(exchange_ratios)


def _load_observed_exchange_ratios(conn: sqlite3.Connection) -> dict[str, str]:
    if not _has_table(conn, "transaction_field_observation"):
        return {}
    rows = conn.execute(
        """
        SELECT transaction_id, field_value, field_value_numeric
        FROM transaction_field_observation
        WHERE is_current = 1
          AND transaction_id IS NOT NULL
          AND lower(field_name) LIKE '%exchange_ratio%'
        ORDER BY transaction_id, observation_id
        """
    ).fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        value = row["field_value_numeric"] if row["field_value_numeric"] is not None else row["field_value"]
        _append_unique(grouped[row["transaction_id"]], value)
    return {tid: _join(values) for tid, values in grouped.items()}


def _load_summaries(conn: sqlite3.Connection) -> dict[str, str]:
    if not _has_table(conn, "summary"):
        return {}
    rows = conn.execute(
        """
        SELECT transaction_id, summary_text
        FROM summary
        WHERE is_current = 1
        ORDER BY transaction_id, summary_id DESC
        """
    ).fetchall()
    out: dict[str, str] = {}
    for row in rows:
        out.setdefault(row["transaction_id"], row["summary_text"] or "")
    return out


def _base_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM transaction_record
        WHERE is_current = 1
        ORDER BY announced_date DESC, transaction_id
        """
    ).fetchall()


def build_rows(conn: sqlite3.Connection) -> list[dict[str, object]]:
    tr_cols = _columns(conn, "transaction_record")
    sources = _load_sources(conn)
    advisors = _load_advisors(conn)
    participants = _load_participants(conn)
    staging_investors = _load_staging_investors(conn)
    observed_exchange = _load_observed_exchange_ratios(conn)
    summaries = _load_summaries(conn)

    out_rows: list[dict[str, object]] = []
    for row in _base_rows(conn):
        r = dict(row)
        tid = r["transaction_id"]
        consideration_summary, consideration_exchange = _summarize_consideration(r.get("consideration_components"))
        participant_fields = participants.get(tid, {})
        investor_fields = staging_investors.get(tid, {})

        acquirers = participant_fields.get("acquirers") or r.get("acquirer_name") or ""
        investors = participant_fields.get("investors") or investor_fields.get("investors") or ""
        lead_investors = participant_fields.get("lead_investors") or investor_fields.get("lead_investors") or ""
        sellers = participant_fields.get("sellers") or ""
        parent_seller = r.get("parent_seller_name") or ""
        acquirer_sponsors = participant_fields.get("buyer_sponsors") or r.get("acquirer_sponsor_name") or ""
        seller_sponsors = participant_fields.get("seller_sponsors") or ""
        lenders = participant_fields.get("lenders_financing_providers") or investor_fields.get("lenders_financing_providers") or ""
        transaction_size, transaction_size_basis = _review_transaction_size(r, tr_cols)

        built = {
            "review_verdict": "",
            "review_notes": "",
            "transaction_id": tid,
            "source_title": sources.get(tid, {}).get("source_title", ""),
            "source_url": sources.get(tid, {}).get("source_url", ""),
            "announced_date": r.get("announced_date"),
            "closed_date": r.get("closed_date"),
            "transaction_status": r.get("transaction_status"),
            "event_type": r.get("v2_event_type") or r.get("deal_type") or r.get("event_type"),
            "target_name": r.get("target_name"),
            "target_type": r.get("target_type_v2") or r.get("target_type"),
            "acquirers": acquirers,
            "investors": investors,
            "sellers": sellers,
            "parent_seller": parent_seller,
            "acquirer_sponsors": acquirer_sponsors,
            "seller_sponsors": seller_sponsors,
            "lenders_financing_providers": lenders,
            "target_financial_advisors": advisors.get(tid, {}).get("target_financial_advisors", ""),
            "target_legal_advisors": advisors.get(tid, {}).get("target_legal_advisors", ""),
            "buyer_financial_advisors": advisors.get(tid, {}).get("buyer_financial_advisors", ""),
            "buyer_legal_advisors": advisors.get(tid, {}).get("buyer_legal_advisors", ""),
            "seller_financial_advisors": advisors.get(tid, {}).get("seller_financial_advisors", ""),
            "seller_legal_advisors": advisors.get(tid, {}).get("seller_legal_advisors", ""),
            "other_advisors": advisors.get(tid, {}).get("other_advisors", ""),
            "is_minority": _compact_bool(r.get("is_minority")),
            "stake_transition_type": r.get("stake_transition_type"),
            "pct_acquired": _fmt_num(r.get("pct_acquired")),
            "is_take_private": _compact_bool(r.get("is_take_private")),
            "is_de_spac": _compact_bool(r.get("is_de_spac")),
            "is_add_on": _compact_bool(r.get("is_add_on")),
            "is_divestiture": _compact_bool(r.get("is_divestiture")),
            "is_platform_investment": _compact_bool(r.get("is_platform_investment")),
            "is_secondary_buyout": _compact_bool(r.get("is_secondary_buyout")),
            "is_merger_of_equals": _compact_bool(r.get("is_merger_of_equals")),
            "per_share_price": _fmt_num(r.get("per_share_price")),
            "exchange_ratio": consideration_exchange or observed_exchange.get(tid, ""),
            "consideration_type": r.get("consideration_type"),
            "consideration_summary": consideration_summary,
            "round_label": r.get("round_label"),
            "round_stage_category": r.get("round_stage_category"),
            "round_size": _fmt_num(r.get("round_size")),
            "pre_money_valuation": _fmt_num(r.get("pre_money_valuation")),
            "post_money_valuation": _fmt_num(r.get("post_money_valuation")),
            "lead_investors": lead_investors,
            "deal_value_currency": r.get("deal_value_currency"),
            "equity_value": _fmt_num(r.get("equity_value")),
            "equity_value_basis": r.get("equity_value_basis"),
            "transaction_value": _fmt_num(r.get("transaction_value")),
            "transaction_value_basis": r.get("transaction_value_basis"),
            "transaction_size": _fmt_num(transaction_size),
            "transaction_size_basis": transaction_size_basis,
            "implied_equity_value": _fmt_num(r.get("implied_equity_value")),
            "implied_enterprise_value": _fmt_num(r.get("implied_enterprise_value")),
            "implied_enterprise_value_basis": r.get("implied_enterprise_value_basis"),
            "total_debt": _fmt_num(r.get("total_debt")),
            "cash_equivalents": _fmt_num(r.get("cash_st") if "cash_st" in tr_cols else None),
            "net_debt": _fmt_num(r.get("net_debt")),
            "financials_currency": r.get("financials_currency"),
            "revenue": _fmt_num(r.get("target_revenue")),
            "revenue_period": _period(r.get("target_revenue_period_type_v2") or r.get("target_revenue_period_type"), r.get("target_revenue_period_end")),
            "ebitda": _fmt_num(r.get("target_ebitda")),
            "ebitda_period": _period(r.get("target_ebitda_period_type_v2") or r.get("target_ebitda_period_type"), r.get("target_ebitda_period_end")),
            "ev_revenue_multiple": _multiple(r.get("ev_to_revenue_ltm"), r.get("ev_to_revenue_ntm")),
            "ev_ebitda_multiple": _multiple(r.get("ev_to_ebitda_ltm"), r.get("ev_to_ebitda_ntm")),
            "multiple_quality": r.get("multiple_quality"),
            "transaction_summary": summaries.get(tid, ""),
        }
        out_rows.append({col: built.get(col, "") for col in COLUMNS})
    return out_rows


def _col_name(idx: int) -> str:
    name = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value: object, style: int | None = None) -> str:
    style_attr = f' s="{style}"' if style is not None else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape(str(value), {"'": "&apos;", '"': "&quot;"})
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def write_xlsx(path: str, rows: list[dict[str, object]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sheet_rows: list[str] = []
    header_cells = [_cell(f"{_col_name(i)}1", col, 1) for i, col in enumerate(COLUMNS, start=1)]
    sheet_rows.append(f'<row r="1">{"".join(header_cells)}</row>')
    for r_idx, row in enumerate(rows, start=2):
        cells = [_cell(f"{_col_name(c_idx)}{r_idx}", row.get(col, "")) for c_idx, col in enumerate(COLUMNS, start=1)]
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    widths = "\n".join(
        f'<col min="{i}" max="{i}" width="{min(max(len(col) + 2, 12), 42)}" customWidth="1"/>'
        for i, col in enumerate(COLUMNS, start=1)
    )
    last_col = _col_name(len(COLUMNS))
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<dimension ref="A1:{last_col}{len(rows) + 1}"/>
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols>{widths}</cols>
<sheetData>{"".join(sheet_rows)}</sheetData>
<autoFilter ref="A1:{last_col}{len(rows) + 1}"/>
</worksheet>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Review" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator>M&amp;A Collection MVP</dc:creator><cp:lastModifiedBy>M&amp;A Collection MVP</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>'''
    app = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>M&amp;A Collection MVP</Application></Properties>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)


def populated_counts(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        col: sum(1 for row in rows if row.get(col) not in (None, ""))
        for col in COLUMNS
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=os.getenv("DB_PATH", "data/ma_mvp.db"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    out = args.out
    if out is None:
        stem = Path(args.db_path).stem
        out = f"exports/review_{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    conn = _connect(args.db_path)
    try:
        rows = build_rows(conn)
    finally:
        conn.close()
    write_xlsx(out, rows)

    counts = populated_counts(rows)
    blank = [col for col, count in counts.items() if count == 0]
    populated = [col for col, count in counts.items() if count > 0]
    print(f"wrote {out}")
    print(f"rows={len(rows)} columns={len(COLUMNS)}")
    print("populated_columns=" + ", ".join(populated))
    print("systematically_blank_columns=" + ", ".join(blank))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
