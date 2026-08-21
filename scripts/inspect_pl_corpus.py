"""
scripts/inspect_pl_corpus.py — read-only inventory of a PredictLeads export TSV.

Makes ZERO model calls and never writes to the input file. Answers the questions
that have to be settled before the corpus can be fed to the production Relevancy
stage: what the columns are, which one is a stable ID, where the body text is,
how complete it is, and whether the rows are actually distinct stories.

Deliberately schema-agnostic. Nothing about the PredictLeads export format is
assumed -- the column roles are inferred from content and reported as candidates
with the evidence behind each guess, so a wrong guess is visible rather than
silent. The Stage-1 runner takes the chosen columns as explicit flags; this
script only proposes them.

Run:
    python scripts/inspect_pl_corpus.py --tsv <path> [--json-out <path>]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter

# Press-release bodies are large and may legitimately contain tabs, quotes and
# newlines. Python's default field cap will refuse them outright.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

_ID_NAME_RE = re.compile(r"(^|_)(id|uuid|guid|key|hash)($|_)", re.I)
_TITLE_NAME_RE = re.compile(r"(title|headline|subject|name)", re.I)
_BODY_NAME_RE = re.compile(r"(body|text|content|article|story|description|summary)", re.I)
_URL_NAME_RE = re.compile(r"(url|link|href|domain|site|source)", re.I)
_DATE_NAME_RE = re.compile(r"(date|time|published|created|found|_at$)", re.I)
_LABEL_NAME_RE = re.compile(
    r"(label|review|reviewed|relevant|relevance|class|classif|categor|decision|"
    r"verdict|status|judg|annotat|gold|truth|flag|type)", re.I
)
_DATE_VAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_URL_VAL_RE = re.compile(r"^https?://", re.I)

# A press release that ends without terminal punctuation, or on an ellipsis, is
# the usual signature of a capped export field.
_TRUNC_TAIL_RE = re.compile(r"(\.\.\.|…|\[\+\d+\s*chars\]|\bRead more\b)\s*$", re.I)


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "n/a"


def _percentiles(values: list[int]) -> dict:
    if not values:
        return {}
    s = sorted(values)
    def at(p: float) -> int:
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]
    return {
        "min": s[0], "p05": at(.05), "p25": at(.25), "p50": at(.50),
        "p75": at(.75), "p95": at(.95), "max": s[-1],
        "mean": int(sum(s) / len(s)),
    }


def _read(path: str) -> tuple[list[str], list[list[str]], list[dict]]:
    """Parse the TSV, returning (header, rows, anomalies).

    Anomalies are ragged rows -- a field count that disagrees with the header is
    the single most likely way a body containing a raw tab silently shifts every
    later column, so it is reported rather than repaired.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        try:
            header = next(reader)
        except StopIteration:
            return [], [], []
        rows, anomalies = [], []
        for i, row in enumerate(reader, start=2):
            if len(row) != len(header):
                if len(anomalies) < 25:
                    anomalies.append({"line": i, "fields": len(row), "expected": len(header)})
                # Normalize so downstream indexing is safe; the anomaly is recorded.
                row = (row + [""] * len(header))[:len(header)]
            rows.append(row)
    return header, rows, anomalies


def _column_stats(header: list[str], rows: list[list[str]]) -> list[dict]:
    n = len(rows)
    out = []
    for idx, name in enumerate(header):
        vals = [(r[idx] or "").strip() for r in rows]
        nonempty = [v for v in vals if v]
        lens = [len(v) for v in nonempty]
        distinct = len(set(nonempty))
        out.append({
            "index": idx,
            "name": name,
            "nonempty": len(nonempty),
            "fill_rate": _pct(len(nonempty), n),
            "distinct": distinct,
            "distinct_ratio": round(distinct / n, 4) if n else 0.0,
            "len": _percentiles(lens),
            "url_like": _pct(sum(1 for v in nonempty if _URL_VAL_RE.match(v)), len(nonempty)),
            "date_like": _pct(sum(1 for v in nonempty if _DATE_VAL_RE.match(v)), len(nonempty)),
            "sample": [v[:120] for v in nonempty[:3]],
        })
    return out


def _candidates(stats: list[dict], n: int) -> dict:
    """Propose a role for each column, with the evidence that supports it."""
    def score_id(c):
        s = c["distinct_ratio"] * 2
        if _ID_NAME_RE.search(c["name"]): s += 1.5
        if c["nonempty"] == n: s += 0.5
        # A body column is ~unique too; length disqualifies it as an ID.
        if c["len"].get("p50", 0) > 120: s -= 3
        return s

    def score_title(c):
        s = 0.0
        if _TITLE_NAME_RE.search(c["name"]): s += 1.5
        p50 = c["len"].get("p50", 0)
        if 20 <= p50 <= 250: s += 1.0
        if c["distinct_ratio"] > 0.5: s += 0.5
        if float(c["url_like"].rstrip("%") or 0) > 20: s -= 2
        return s

    def score_body(c):
        s = 0.0
        if _BODY_NAME_RE.search(c["name"]): s += 1.0
        p50 = c["len"].get("p50", 0)
        if p50 > 400: s += 2.0
        elif p50 > 200: s += 1.0
        return s

    def score_url(c):
        s = float(c["url_like"].rstrip("%") or 0) / 50.0
        if _URL_NAME_RE.search(c["name"]): s += 0.5
        return s

    def score_date(c):
        s = float(c["date_like"].rstrip("%") or 0) / 50.0
        if _DATE_NAME_RE.search(c["name"]): s += 0.5
        return s

    def top(fn, k=3):
        ranked = sorted(stats, key=fn, reverse=True)
        return [{"name": c["name"], "score": round(fn(c), 2), "distinct_ratio": c["distinct_ratio"],
                 "fill_rate": c["fill_rate"], "median_len": c["len"].get("p50", 0)}
                for c in ranked[:k] if fn(c) > 0]

    # Label-like means genuinely categorical: a small closed set relative to the
    # corpus. The ratio guard matters -- on a small file a bare "distinct <= 12"
    # test flags every column, which reports noise as a finding.
    labels = [c["name"] for c in stats
              if c["nonempty"] and (
                  _LABEL_NAME_RE.search(c["name"])
                  or (c["distinct"] <= 12 and c["distinct_ratio"] < 0.10))]
    return {
        "id": top(score_id), "title": top(score_title), "body": top(score_body),
        "url": top(score_url), "date": top(score_date),
        "label_like": labels,
    }


def _dupes(header, rows, cands) -> dict:
    def col(name):
        return header.index(name) if name in header else None

    out = {}
    for role in ("id", "url", "title"):
        pick = cands[role][0]["name"] if cands[role] else None
        i = col(pick) if pick else None
        if i is None:
            continue
        vals = [(r[i] or "").strip().lower() for r in rows]
        vals = [v for v in vals if v]
        c = Counter(vals)
        rep = [{"value": v[:100], "count": k} for v, k in c.most_common(5) if k > 1]
        out[role] = {"column": pick, "values": len(vals), "distinct": len(c),
                     "duplicated_values": sum(1 for k in c.values() if k > 1),
                     "rows_in_duplicate_groups": sum(k for k in c.values() if k > 1),
                     "examples": rep}

    body = cands["body"][0]["name"] if cands["body"] else None
    if body:
        i = col(body)
        hashes = [hashlib.sha256(re.sub(r"\s+", " ", (r[i] or "").strip().lower()).encode()).hexdigest()
                  for r in rows if (r[i] or "").strip()]
        c = Counter(hashes)
        out["body_exact"] = {"column": body, "values": len(hashes), "distinct": len(c),
                             "duplicated_values": sum(1 for k in c.values() if k > 1),
                             "rows_in_duplicate_groups": sum(k for k in c.values() if k > 1)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tsv", required=True, help="Path to the PredictLeads export TSV (read-only).")
    ap.add_argument("--json-out", default=None, help="Optional path for the machine-readable summary.")
    args = ap.parse_args()

    if not os.path.exists(args.tsv):
        print(f"ERROR: not found: {args.tsv}", file=sys.stderr)
        return 1

    header, rows, anomalies = _read(args.tsv)
    n = len(rows)
    if not header:
        print("ERROR: empty file", file=sys.stderr)
        return 1

    stats = _column_stats(header, rows)
    cands = _candidates(stats, n)
    dupes = _dupes(header, rows, cands)

    body_name = cands["body"][0]["name"] if cands["body"] else None
    body_report = {}
    if body_name:
        i = header.index(body_name)
        vals = [(r[i] or "").strip() for r in rows]
        nonempty = [v for v in vals if v]
        lens = [len(v) for v in nonempty]
        lc = Counter(lens)
        body_report = {
            "column": body_name,
            "empty": n - len(nonempty),
            "empty_rate": _pct(n - len(nonempty), n),
            "length": _percentiles(lens),
            "under_200_chars": sum(1 for L in lens if L < 200),
            "under_500_chars": sum(1 for L in lens if L < 500),
            "over_4000_chars": sum(1 for L in lens if L > 4000),
            "ellipsis_or_readmore_tail": sum(1 for v in nonempty if _TRUNC_TAIL_RE.search(v[-40:])),
            # An export capped at a fixed width shows up as many bodies sharing
            # one exact length. A natural corpus almost never does.
            "most_common_exact_lengths": [{"length": L, "rows": k} for L, k in lc.most_common(5) if k > 1],
        }

    date_name = cands["date"][0]["name"] if cands["date"] else None
    date_report = {}
    if date_name:
        i = header.index(date_name)
        days = Counter((r[i] or "")[:10] for r in rows if (r[i] or "").strip())
        date_report = {"column": date_name, "by_day": dict(sorted(days.items()))}

    P = print
    P("=" * 78)
    P(f"PL CORPUS INVENTORY — {os.path.basename(args.tsv)}")
    P("=" * 78)
    P(f"\nrows (excl. header): {n}")
    P(f"columns:             {len(header)}")
    P(f"ragged rows:         {len(anomalies)}"
      + ("   <-- field count disagrees with header; a raw tab in a body shifts every later column"
         if anomalies else ""))
    for a in anomalies[:10]:
        P(f"    line {a['line']}: {a['fields']} fields, expected {a['expected']}")

    P("\n--- COLUMNS " + "-" * 66)
    P(f"{'#':>3}  {'name':<28} {'fill':>7} {'distinct':>9} {'ratio':>7} {'med_len':>8}  sample")
    for c in stats:
        P(f"{c['index']:>3}  {c['name'][:28]:<28} {c['fill_rate']:>7} {c['distinct']:>9} "
          f"{c['distinct_ratio']:>7} {c['len'].get('p50', 0):>8}  {(c['sample'][0] if c['sample'] else '')[:40]!r}")

    P("\n--- PROPOSED COLUMN ROLES (inferred — confirm before use) " + "-" * 19)
    for role in ("id", "title", "body", "url", "date"):
        picks = cands[role]
        P(f"  {role:<6}: " + (", ".join(f"{p['name']} (score {p['score']})" for p in picks) if picks else "NONE FOUND"))
    P(f"  labels: {cands['label_like'] or 'none detected'}")

    P("\n--- DUPLICATES " + "-" * 63)
    for k, v in dupes.items():
        P(f"  by {k} [{v['column']}]: {v['distinct']} distinct of {v['values']} — "
          f"{v['rows_in_duplicate_groups']} rows sit in {v['duplicated_values']} duplicate groups")
        for ex in v.get("examples", [])[:3]:
            P(f"      x{ex['count']}  {ex['value']!r}")

    if body_report:
        P("\n--- BODY COMPLETENESS " + "-" * 56)
        b = body_report
        P(f"  column: {b['column']}")
        P(f"  empty: {b['empty']} ({b['empty_rate']})")
        P(f"  length: {b['length']}")
        P(f"  < 200 chars: {b['under_200_chars']}   < 500: {b['under_500_chars']}   > 4000: {b['over_4000_chars']}")
        P(f"  ellipsis / 'read more' tail: {b['ellipsis_or_readmore_tail']}")
        P(f"  repeated exact lengths (cap signature): {b['most_common_exact_lengths'] or 'none'}")
        P(f"  NOTE: Stage 1 truncates the body to 4000 chars, so {b['over_4000_chars']} rows would be cut by the")
        P(f"        production path itself. That is production behaviour, not a corpus defect.")

    if date_report:
        P("\n--- DATES " + "-" * 68)
        P(f"  column: {date_report['column']}")
        for d, k in date_report["by_day"].items():
            P(f"    {d}  {k}")

    P("\n--- SUITABILITY FOR THE PRODUCTION RELEVANCY STAGE " + "-" * 27)
    issues = []
    if anomalies:
        issues.append(f"{len(anomalies)} ragged rows — column alignment is not guaranteed")
    if body_report.get("empty", 0):
        issues.append(f"{body_report['empty']} rows have no body ({body_report['empty_rate']}) — "
                      "Stage 1 skips empty clean_text, so these are not classifiable")
    if body_report.get("under_200_chars", 0):
        issues.append(f"{body_report['under_200_chars']} bodies under 200 chars — likely headline-only stubs")
    for k, v in dupes.items():
        if v.get("rows_in_duplicate_groups"):
            issues.append(f"{v['rows_in_duplicate_groups']} rows duplicated by {k} — dedup before spending calls")
    if not cands["id"]:
        issues.append("no stable ID column identified — one is required for a repeatable regression set")
    P("  " + ("\n  ".join(f"- {i}" for i in issues) if issues else "- no blocking issues detected"))

    summary = {
        "file": args.tsv, "rows": n, "columns": header, "ragged_rows": anomalies,
        "column_stats": stats, "candidates": cands, "duplicates": dupes,
        "body": body_report, "dates": date_report, "issues": issues,
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        P(f"\nmachine-readable summary written to {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
