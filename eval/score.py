"""
eval/score.py — Gold-set scoring CLI for the M&A collection pipeline.

Joins a labeled gold-set CSV against the live database, computes per-field
precision metrics, and writes a markdown scorecard.

Usage:
    python eval/score.py --gold eval/gold_set_test.csv
    python eval/score.py --gold eval/gold_set_20260423.csv --run-id run_20260423_120000
    python eval/score.py --gold eval/gold_set_20260423.csv --output eval/scorecard.md
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from rapidfuzz.fuzz import token_set_ratio
except ImportError:
    print("rapidfuzz not installed: pip install rapidfuzz", file=sys.stderr)
    sys.exit(1)

# ── Field registry ─────────────────────────────────────────────────────────────

# (field_name, match_type, precision_target)
# match_type: "string" | "numeric" | "date" | "enum"
_FULL_COVERAGE = [
    ("target_name",        "string",  "> 95%"),
    ("acquirer_name",      "string",  "> 95%"),
    ("parent_seller_name", "string",  "> 95%"),
    ("deal_type",          "enum",    "> 90%"),
    ("target_type",        "enum",    "> 90%"),
    ("target_status",      "enum",    "> 90%"),
    ("event_type",         "enum",    "> 90%"),
    ("value_amount",       "numeric", "> 90%"),
    ("value_currency",     "enum",    "> 90%"),
    ("value_type",         "enum",    "> 90%"),
    ("announced_date",     "date",    "> 98%"),
    ("closed_date",        "date",    "> 98%"),
    ("consideration_type", "enum",    "> 90%"),
]

_SPOT_CHECK = [
    ("target_revenue",        "numeric"),
    ("target_revenue_period", "string"),
    ("target_ebitda",         "numeric"),
    ("target_ebitda_period",  "string"),
    ("primary_rationale",     "enum"),
    ("advisor_count",         "numeric"),
]

_NUMERIC_TOLERANCE = 0.01
_FUZZY_THRESHOLD = 90

# ── DB helpers ─────────────────────────────────────────────────────────────────

def _fmt_period(period_type: str | None, period_end: str | None) -> str | None:
    """Format DB period_type+period_end into gold-set canonical form."""
    if not period_type or period_type == "UNKNOWN":
        return period_end
    if not period_end:
        return period_type
    year = period_end[:4]
    pt = period_type.upper()
    if pt in ("FY", "FISCAL_YEAR"):
        return f"FY{year}"
    if pt in ("CY", "CALENDAR_YEAR"):
        return f"CY{year}"
    if pt in ("LTM", "TTM"):
        return f"{pt} {period_end}"
    if pt == "NTM":
        return f"NTM {period_end}"
    if pt == "QUARTER":
        try:
            month = int(period_end[5:7])
            q = (month - 1) // 3 + 1
            return f"Q{q} {year}"
        except (ValueError, IndexError):
            return f"Q {period_end}"
    return f"{period_type} {period_end}"


def _get_db_path() -> Path:
    """Read db_path from project config."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from config import get_config
    return Path(get_config().db_path)


def _lookup_pipeline(conn: sqlite3.Connection, source_raw_id: int) -> dict | None:
    """Return all scoreable pipeline fields for a given source_raw_id."""
    row = conn.execute(
        """
        SELECT tr.*,
               rt.primary_rationale,
               (
                   SELECT COUNT(*)
                   FROM advisor a
                   JOIN staging_extraction se2
                        ON se2.extraction_id = a.extraction_id
                   WHERE se2.transaction_cluster_id = tr.transaction_id
               ) AS advisor_count
        FROM staging_extraction se
        JOIN transaction_record tr
             ON tr.transaction_id = se.transaction_cluster_id AND tr.is_current = 1
        LEFT JOIN rationale_tag rt
             ON rt.transaction_id = tr.transaction_id AND rt.is_current = 1
        WHERE se.source_raw_id = ?
        LIMIT 1
        """,
        (source_raw_id,),
    ).fetchone()

    if row is None:
        return None

    d = dict(row)
    d["target_revenue_period"] = _fmt_period(
        d.get("target_revenue_period_type"), d.get("target_revenue_period_end")
    )
    d["target_ebitda_period"] = _fmt_period(
        d.get("target_ebitda_period_type"), d.get("target_ebitda_period_end")
    )
    return d

# ── Comparison ─────────────────────────────────────────────────────────────────

def _norm(val: Any) -> str | None:
    """Normalize a value: blank/null/None all become None."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("none", "null") else None


def _compare(gold: Any, pipeline: Any, match_type: str) -> bool:
    g = _norm(gold)
    p = _norm(pipeline)

    if g is None and p is None:
        return True
    if g is None or p is None:
        return False

    if match_type == "string":
        return token_set_ratio(g, p) >= _FUZZY_THRESHOLD

    if match_type in ("enum", "date"):
        return g.upper() == p.upper()

    if match_type == "numeric":
        try:
            gf, pf = float(g), float(p)
            if gf == 0.0:
                return pf == 0.0
            return abs(gf - pf) / abs(gf) <= _NUMERIC_TOLERANCE
        except ValueError:
            return g.upper() == p.upper()

    return g == p

# ── Stats accumulator ──────────────────────────────────────────────────────────

class _Stats:
    def __init__(self):
        self.correct = 0
        self.incorrect = 0

    def record(self, ok: bool) -> None:
        if ok:
            self.correct += 1
        else:
            self.incorrect += 1

    @property
    def total(self) -> int:
        return self.correct + self.incorrect

    @property
    def precision(self) -> float | None:
        return self.correct / self.total if self.total else None

    def pct(self) -> str:
        p = self.precision
        return f"{p * 100:.1f}%" if p is not None else "—"

# ── Scoring engine ─────────────────────────────────────────────────────────────

def score_gold(gold_path: Path, db_path: Path, run_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    full_stats: dict[str, _Stats] = {f: _Stats() for f, _, _ in _FULL_COVERAGE}
    spot_stats: dict[str, _Stats] = {f: _Stats() for f, _ in _SPOT_CHECK}

    total_labeled = 0
    not_found: list[int] = []
    cluster_to_gold: dict[str, set[int]] = {}

    with open(gold_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("labeled", "").strip().lower() != "true":
                continue
            total_labeled += 1

            try:
                src_id = int(row["source_raw_id"])
            except (KeyError, ValueError):
                not_found.append(row.get("source_raw_id", "?"))
                continue

            pipeline = _lookup_pipeline(conn, src_id)
            if pipeline is None:
                not_found.append(src_id)
                continue

            cid = pipeline["transaction_id"]
            cluster_to_gold.setdefault(cid, set()).add(src_id)

            for field, match_type, _ in _FULL_COVERAGE:
                ok = _compare(row.get(field), pipeline.get(field), match_type)
                full_stats[field].record(ok)

            for field, match_type in _SPOT_CHECK:
                gold_val = row.get(field, "").strip()
                if not gold_val:
                    continue  # spot-check field not labeled for this row
                ok = _compare(gold_val, pipeline.get(field), match_type)
                spot_stats[field].record(ok)

    conn.close()

    # Dedup: false merge = one cluster contains gold rows from 2+ distinct deals.
    # Without per-row deal labels we flag clusters where multiple gold source_raw_ids
    # mapped to the same cluster; operator inspects to confirm whether they are the
    # same deal or a false merge.
    suspicious_merges = {
        cid: sorted(ids)
        for cid, ids in cluster_to_gold.items()
        if len(ids) > 1
    }

    return {
        "run_id": run_id,
        "gold_path": str(gold_path),
        "total_labeled": total_labeled,
        "matched": total_labeled - len(not_found),
        "not_found": not_found,
        "full_stats": full_stats,
        "spot_stats": spot_stats,
        "clusters_seen": len(cluster_to_gold),
        "suspicious_merges": suspicious_merges,
    }

# ── Scorecard renderer ─────────────────────────────────────────────────────────

def _target_status(precision: float | None, target_str: str) -> str:
    """Return PASS/FAIL/— based on precision vs target string like '> 95%'."""
    if precision is None:
        return "—"
    try:
        threshold = float(target_str.replace(">", "").replace("%", "").strip()) / 100
    except ValueError:
        return "—"
    return "PASS" if precision >= threshold else "FAIL"


def render_scorecard(result: dict) -> str:
    lines: list[str] = []
    run_id = result["run_id"]
    lines.append(f"# Pipeline Scorecard — {run_id}")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Gold set: {result['gold_path']}")
    lines.append(f"- Labeled rows evaluated: {result['matched']} of {result['total_labeled']}")
    if result["not_found"]:
        lines.append(f"- Not found in DB: {result['not_found']}")
    lines.append(f"- Clusters seen: {result['clusters_seen']}")
    lines.append("")

    # ── Full-coverage table
    lines.append("## Per-field precision (full coverage)")
    lines.append("")
    lines.append("| Field | Correct | Incorrect | Precision | Target | Status |")
    lines.append("| :--- | ---: | ---: | ---: | :--- | :--- |")
    for field, _, target in _FULL_COVERAGE:
        st = result["full_stats"][field]
        if st.total == 0:
            lines.append(f"| {field} | — | — | — | {target} | — |")
        else:
            status = _target_status(st.precision, target)
            lines.append(
                f"| {field} | {st.correct} | {st.incorrect} | {st.pct()} | {target} | {status} |"
            )
    lines.append("")

    # ── Spot-check table
    spot_rows = [
        (f, result["spot_stats"][f])
        for f, _ in _SPOT_CHECK
        if result["spot_stats"][f].total > 0
    ]
    if spot_rows:
        lines.append("## Per-field precision (spot check)")
        lines.append("")
        lines.append("| Field | Correct | Incorrect | Precision |")
        lines.append("| :--- | ---: | ---: | ---: |")
        for field, st in spot_rows:
            lines.append(f"| {field} | {st.correct} | {st.incorrect} | {st.pct()} |")
        lines.append("")

    # ── Dedup
    lines.append("## Dedup")
    lines.append("")
    suspicious = result["suspicious_merges"]
    if suspicious:
        lines.append(
            f"- Suspicious merges (multiple gold source_raw_ids in one cluster): "
            f"{len(suspicious)} — operator review required"
        )
        for cid, ids in suspicious.items():
            lines.append(f"  - cluster {cid}: source_raw_ids {ids}")
    else:
        lines.append("- No suspicious merges detected")
    lines.append("")

    # ── Failures
    if result["not_found"]:
        lines.append("## Not Found")
        lines.append("")
        for src_id in result["not_found"]:
            lines.append(f"- source_raw_id={src_id} not joined to any current transaction_record")
        lines.append("")

    lines.append(
        f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"by eval/score.py*"
    )

    return "\n".join(lines)

# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eval/score.py",
        description="Score pipeline output against a labeled gold set.",
    )
    parser.add_argument("--gold", required=True, metavar="PATH",
                        help="Path to labeled gold set CSV.")
    parser.add_argument("--run-id", default=None, metavar="RUN_ID",
                        help="Run ID for scorecard header (inferred from filename if omitted).")
    parser.add_argument("--output", default=None, metavar="PATH",
                        help="Write scorecard to this path (stdout if omitted).")
    args = parser.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.exists():
        print(f"error: gold set not found: {gold_path}", file=sys.stderr)
        sys.exit(1)

    db_path = _get_db_path()
    if not db_path.exists():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    run_id = args.run_id or gold_path.stem

    result = score_gold(gold_path, db_path, run_id)
    scorecard = render_scorecard(result)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(scorecard, encoding="utf-8")
        print(f"Scorecard written to {out_path}")
    else:
        print(scorecard)


if __name__ == "__main__":
    main()
