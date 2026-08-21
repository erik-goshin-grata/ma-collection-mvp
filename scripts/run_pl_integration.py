"""
scripts/run_pl_integration.py — run the production transaction path over the PL
integration corpus in an isolated database.

Validation only. No production prompt, stage or schema is modified, and no
extraction, classification, clustering or aggregation logic is reimplemented:
this script seeds a fresh database, calls the real stage run() functions in
order, and then reads the resulting database to write review artifacts.

WHAT RUNS

    Stage 3  deal_type_classify      classifier
    Stage 4  high_confidence_extract HC (non-funding seats)
    Stage 4b funding_hc_extract      HC (funding seats)
    Stage 7  low_confidence_extract  LC
    Stage 8  entity_cluster          clustering            (no model calls)
    Stage 9  aggregate               observations -> canonical
    Stage 12 summarize               Deal Summary

WHAT DOES NOT, AND WHY IT IS SAFE

    Stage 5/6  SEC trigger + enrichment. Stage 7's gate is
               `status IN ('HC_EXTRACTED','SEC_NOT_TRIGGERED','SEC_ENRICHED')`
               and HC leaves rows at HC_EXTRACTED, so the SEC path is optional
               by construction rather than by accident. No SEC call is made and
               no SEC failure is manufactured.
    Stage 10/11 agreement extraction. Runs after Stage 9; transaction_record is
               complete without it. Every PL source is a news story with no
               attached agreement, so this is the production-normal state for
               this corpus.
    Stage 13   Strategic Rationale. Tabled -- its NULL/OTHER semantics are an
               open Product question, and nothing upstream depends on it.
    Stage 14   Production export. This script writes its own artifacts instead.

ISOLATION

    The pipeline reaches a database only through cfg.db_path. Config is a frozen
    dataclass, so overriding that single field with dataclasses.replace is
    complete isolation -- the production database is never opened. The real
    config is used otherwise, including whatever SEC_API_KEY is already present:
    no dummy credential is invented, because the SEC stages are simply not
    called.

Run:
    python scripts/run_pl_integration.py \\
        --tsv <corpus.tsv> --relevancy-results out/relevancy_val/results.jsonl \\
        --corpus scripts/pl_integration_corpus.txt \\
        --out-dir out/pl_integration [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

from config import ConfigurationError, get_config            # noqa: E402
from db import get_connection, init_db                       # noqa: E402
from logger import get_logger                                # noqa: E402

import stages.deal_type_classify as _stage_3                 # noqa: E402
import stages.high_confidence_extract as _stage_4            # noqa: E402
import stages.funding_hc_extract as _stage_4b                # noqa: E402
import stages.low_confidence_extract as _stage_7             # noqa: E402
import stages.entity_cluster as _stage_8                     # noqa: E402
import stages.aggregate as _stage_9                          # noqa: E402
import stages.summarize as _stage_12                         # noqa: E402

# Order matters and mirrors run.py's extraction sequence with the SEC pair and
# the second HC pass removed -- the second pass exists only to pick up sources
# that Stage 6 attached, and nothing attaches any here.
PIPELINE = [
    ("stage_3_deal_type_classify", _stage_3),
    ("stage_4_high_confidence", _stage_4),
    ("stage_4b_funding_hc", _stage_4b),
    ("stage_7_low_confidence", _stage_7),
    ("stage_8_entity_cluster", _stage_8),
    ("stage_9_aggregate", _stage_9),
    ("stage_12_summarize", _stage_12),
]

# Stress groups, asserted on rather than assumed. MPS is deliberately absent:
# one source may carry two transactions and the point is to observe what the
# architecture does, not to force a number.
EXPECTED_GROUPS = {
    "EFG / Canaccord (Harris Allday)": ("7c0130b3", "c60b0263", "fda9b3c7"),
    "EnerPure US$35M round": ("81c16cb1", "e394e751"),
    "TANAKA / Clean Planet": ("d63f0908", "4a3c908e"),
}
MPS_PREFIXES = ("8f14efd5", "1e7d5fc8")

# Fields whose authorship by an extractor is itself the finding. These were
# retired or superseded; a value appearing here means something is still writing
# them. Sourced from the S-G retirement and the classifier 0.7/0.8 vocabulary.
RETIRED_CANONICAL_FIELDS = ("is_add_on", "is_platform_investment")
RETIRED_VALUES = {
    "target_type": ("spinco", "SPINCO", "STANDALONE_COMPANY", "BUSINESS_UNIT", "SUBSIDIARY", "ASSETS"),
    "v2_event_type": ("MINORITY_INVESTMENT", "SPIN_SPLIT"),
}


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------

def read_manifest(path: str) -> list[str]:
    ids = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    if len(set(ids)) != len(ids):
        raise SystemExit("ERROR: duplicate ids in the manifest")
    return ids


def load_corpus(tsv: str, results: str, ids: list[str]) -> list[dict]:
    """Join the manifest to the corpus TSV and the Stage-1 Relevancy results."""
    rel = {}
    for line in open(results, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            rel[r["corpus_id"]] = r

    rows = {}
    groups: dict[str, list[str]] = {}
    with open(tsv, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            rows[row["event_id"]] = row
            groups.setdefault(row["source_url"], []).append(row["event_id"])

    missing = [i for i in ids if i not in rows]
    if missing:
        raise SystemExit(f"ERROR: {len(missing)} manifest id(s) not in the TSV: {missing[:3]}")
    no_rel = [i for i in ids if i not in rel]
    if no_rel:
        raise SystemExit(
            f"ERROR: {len(no_rel)} manifest id(s) have no Stage-1 result: {no_rel[:3]}\n"
            "       notes.relevancy is load-bearing -- Stage 3 reads reason_code from it "
            "and silently degrades to UNKNOWN when it is absent.")

    out = []
    for i in ids:
        row, r = rows[i], rel[i]
        out.append({
            "pl_event_id": i,
            "grouped_event_ids": groups.get(row["source_url"], [i]),
            "url": row["source_url"],
            "title": row["source_title"],
            "body": row["source_body_lite"],
            "published": row["source_published_at"],
            "relevancy": {
                "reason_code": r["reason_code"],
                "model_confidence": r.get("model_confidence"),
                "notes": r.get("model_notes"),
                "prompt_version": r.get("prompt_version"),
            },
        })
    return out


def seed(conn: sqlite3.Connection, corpus: list[dict]) -> dict[str, int]:
    """Insert one source_raw row per story. Returns pl_event_id -> source_raw_id."""
    now = datetime.now(timezone.utc).isoformat()
    mapping = {}
    for c in corpus:
        # notes carries two things: the real Stage-1 result, which Stage 3 reads
        # (deal_type_classify.py reads notes["relevancy"]["reason_code"]), and PL
        # provenance so every downstream row traces back to the corpus.
        notes = json.dumps({
            "relevancy": c["relevancy"],
            "pl_provenance": {
                "event_id": c["pl_event_id"],
                "grouped_event_ids": c["grouped_event_ids"],
                "source_url": c["url"],
            },
        })
        cur = conn.execute(
            """
            INSERT INTO source_raw
                (source_type, source_tier, url, title, published_date,
                 raw_html, clean_text, content_hash, source_status, notes, fetched_at)
            VALUES ('WEB_URL', 'T2', ?, ?, ?, NULL, ?, ?, 'RELEVANT', ?, ?)
            """,
            (c["url"], c["title"], c["published"], c["body"],
             hashlib.sha256(re.sub(r"\s+", " ", c["body"]).strip().encode()).hexdigest(),
             notes, now),
        )
        mapping[c["pl_event_id"]] = cur.lastrowid
    conn.commit()
    return mapping


# ---------------------------------------------------------------------------
# artifacts -- reads only; no extraction or reconciliation logic here
# ---------------------------------------------------------------------------

def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def _dump(path, rows):
    if not rows:
        open(path, "w", encoding="utf-8").write("")
        return
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c) for c in cols])


def traceability(conn) -> list[dict]:
    out = []
    for sr in _rows(conn, "SELECT source_raw_id, url, title, notes FROM source_raw ORDER BY source_raw_id"):
        prov = {}
        try:
            prov = (json.loads(sr["notes"] or "{}") or {}).get("pl_provenance", {})
        except ValueError:
            pass
        ses = _rows(conn,
                    "SELECT extraction_id, status, v2_event_type, deal_type, transaction_cluster_id, "
                    "target_name, acquirer_name FROM staging_extraction WHERE source_raw_id = ?",
                    (sr["source_raw_id"],))
        if not ses:
            ses = [{"extraction_id": None, "status": "(no staging row)", "v2_event_type": None,
                    "deal_type": None, "transaction_cluster_id": None,
                    "target_name": None, "acquirer_name": None}]
        for se in ses:
            tr = _rows(conn, "SELECT transaction_id FROM transaction_record WHERE transaction_id = ? "
                             "AND is_current = 1", (se["transaction_cluster_id"],))
            out.append({
                "pl_event_id": prov.get("event_id", ""),
                "grouped_event_ids": ";".join(prov.get("grouped_event_ids", []) or []),
                "source_url": sr["url"],
                "title": (sr["title"] or "")[:120],
                "source_raw_id": sr["source_raw_id"],
                "extraction_id": se["extraction_id"],
                "staging_status": se["status"],
                "v2_event_type": se["v2_event_type"] or se["deal_type"],
                "target_name": se["target_name"],
                "acquirer_name": se["acquirer_name"],
                "transaction_cluster_id": se["transaction_cluster_id"],
                "transaction_id": tr[0]["transaction_id"] if tr else "",
            })
    return out


def _prefix_to_cluster(trace: list[dict]) -> dict[str, set]:
    m: dict[str, set] = {}
    for t in trace:
        if t["pl_event_id"]:
            m.setdefault(t["pl_event_id"][:8], set()).add(t["transaction_cluster_id"])
    return m


def exceptions(conn, trace, stage_results, seeded) -> list[tuple[str, str]]:
    """Everything worth a human's attention. Empty list means nothing surfaced."""
    ex: list[tuple[str, str]] = []
    A = ex.append

    # 1. stage failures
    # The statuses a row legitimately rests at once this path has run. AGGREGATED is
    # the terminal one; the earlier ones mean a row stalled, which is itself the
    # finding. RECOGNIZED_NOT_PROFILED is a deliberate Stage-3 exclusion, reported
    # separately rather than as a failure.
    _OK_STATUS = ("AGGREGATED", "CLUSTERED", "LC_EXTRACTED", "HC_EXTRACTED", "CLASSIFIED")
    for st in _rows(conn, "SELECT status, COUNT(*) n FROM staging_extraction GROUP BY status"):
        if st["status"] == "RECOGNIZED_NOT_PROFILED":
            A(("stage-3 exclusion", f"{st['n']} row(s) recognized as PIPE and not profiled"))
        elif st["status"] not in _OK_STATUS:
            A(("stage failure", f"{st['n']} staging row(s) at status {st['status']!r}"))
        elif st["status"] != "AGGREGATED":
            A(("stalled", f"{st['n']} staging row(s) stopped at {st['status']!r} — expected "
                          f"AGGREGATED after Stage 9"))
    for f in _rows(conn, "SELECT stage, failure_type, COUNT(*) n FROM extraction_failure_log "
                         "GROUP BY stage, failure_type"):
        A(("stage failure", f"{f['n']} × {f['stage']} / {f['failure_type']}"))
    for name, res in stage_results.items():
        for k, v in (res or {}).items():
            if "fail" in k.lower() and v:
                A(("stage failure", f"{name} reported {k}={v}"))

    # 2. cardinality
    n_src = len(seeded)
    n_se = conn.execute("SELECT COUNT(*) FROM staging_extraction").fetchone()[0]
    n_cl = conn.execute("SELECT COUNT(DISTINCT transaction_cluster_id) FROM staging_extraction "
                        "WHERE transaction_cluster_id IS NOT NULL").fetchone()[0]
    n_tx = conn.execute("SELECT COUNT(*) FROM transaction_record WHERE is_current = 1").fetchone()[0]
    # Stage 4 and Stage 4b both accept a `transactions` ARRAY, so one source
    # producing several extractions is architecturally normal, not an anomaly --
    # it is exactly what the Monte dei Paschi case is here to observe. Fewer
    # extractions than sources is the direction that means something was lost.
    if n_se > n_src:
        A(("cardinality", f"{n_src} sources produced {n_se} staging extractions — "
                          f"{n_se - n_src} source(s) decomposed into multiple transactions"))
    elif n_se < n_src:
        A(("cardinality", f"{n_src} sources produced only {n_se} staging extractions — "
                          f"{n_src - n_se} source(s) produced nothing"))
    if n_cl != n_tx:
        A(("cardinality", f"{n_cl} clusters but {n_tx} canonical transactions"))

    # 3. the named stress groups
    p2c = _prefix_to_cluster(trace)
    for label, prefixes in EXPECTED_GROUPS.items():
        clusters = set()
        for p in prefixes:
            clusters |= {c for c in p2c.get(p, set()) if c}
        if len(clusters) != 1:
            A(("clustering", f"{label}: {len(prefixes)} sources landed in {len(clusters)} cluster(s) "
                             f"{sorted(clusters)} — one expected"))
    mps = set()
    for p in MPS_PREFIXES:
        mps |= {c for c in p2c.get(p, set()) if c}
    A(("stress: MPS", f"the 2 Monte dei Paschi sources produced {len(mps)} cluster(s): {sorted(mps)}. "
                      f"Reported, not graded — one source may carry two transactions."))
    # unexpected merges: distinct manifest stories sharing a cluster without being a known group
    known = {p for g in EXPECTED_GROUPS.values() for p in g} | set(MPS_PREFIXES)
    by_cluster: dict[str, set] = {}
    for t in trace:
        if t["transaction_cluster_id"] and t["pl_event_id"]:
            by_cluster.setdefault(t["transaction_cluster_id"], set()).add(t["pl_event_id"][:8])
    for cid, pref in by_cluster.items():
        if len(pref) > 1 and not (pref <= known):
            A(("clustering", f"cluster {cid} merged unrelated stories: {sorted(pref)}"))

    # 4. observation conflicts and escalations
    for c in _rows(conn, "SELECT transaction_id, field_name, conflict_severity, chosen_value, "
                         "flagged_for_review FROM aggregation_conflict_log ORDER BY transaction_id"):
        A(("conflict", f"{c['transaction_id']} {c['field_name']}: severity={c['conflict_severity']}, "
                       f"chose {c['chosen_value']!r}"
                       f"{' — FLAGGED FOR REVIEW' if c['flagged_for_review'] else ''}"))
    for d in _rows(conn, """
            SELECT transaction_id, field_name, COUNT(DISTINCT field_value) n
            FROM transaction_field_observation
            WHERE is_current = 1 AND transaction_id IS NOT NULL AND field_value IS NOT NULL
            GROUP BY transaction_id, field_name HAVING n > 1"""):
        A(("conflict", f"{d['transaction_id']} {d['field_name']}: {d['n']} distinct observed values"))

    # 5. suspicious fact loss -- observed, then absent from canonical
    tx_cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
    for o in _rows(conn, """
            SELECT DISTINCT transaction_id, field_name FROM transaction_field_observation
            WHERE is_current = 1 AND transaction_id IS NOT NULL
              AND field_value IS NOT NULL AND TRIM(field_value) NOT IN ('', 'null')"""):
        if o["field_name"] not in tx_cols:
            continue
        v = conn.execute(f"SELECT {o['field_name']} FROM transaction_record "
                         "WHERE transaction_id = ? AND is_current = 1",
                         (o["transaction_id"],)).fetchone()
        if v is not None and v[0] is None:
            A(("fact loss", f"{o['transaction_id']}: {o['field_name']} was observed but canonical is NULL"))

    # 6. canonical inconsistencies
    for t in _rows(conn, "SELECT * FROM transaction_record WHERE is_current = 1"):
        tid = t["transaction_id"]
        if (t.get("target_type") or "").lower() == "assets" and not t.get("asset_type"):
            A(("canonical", f"{tid}: target_type=assets with no asset_type (§T13 subordinate field)"))
        if t.get("asset_type") and (t.get("target_type") or "").lower() != "assets":
            A(("canonical", f"{tid}: asset_type set while target_type={t.get('target_type')!r}"))
        if t.get("closed_date") and t.get("announced_date") and t["closed_date"] < t["announced_date"]:
            A(("canonical", f"{tid}: closed_date {t['closed_date']} precedes announced_date "
                            f"{t['announced_date']}"))
        if not t.get("target_name"):
            A(("canonical", f"{tid}: no target_name"))
        for f in RETIRED_CANONICAL_FIELDS:
            if t.get(f) not in (None, 0):
                A(("retired field", f"{tid}: {f}={t.get(f)!r} — retired, nothing should author it"))
        for f, bad in RETIRED_VALUES.items():
            if t.get(f) in bad:
                A(("retired field", f"{tid}: {f}={t.get(f)!r} — retired/superseded value"))
        filled = sum(1 for k, v in t.items() if v not in (None, "", 0))
        if filled < max(6, int(0.12 * len(t))):
            A(("null-heavy", f"{tid}: only {filled} of {len(t)} canonical fields populated"))

    # 7. summary vs canonical
    for s in _rows(conn, """
            SELECT s.transaction_id, s.summary_text, s.word_count,
                   tr.target_name, tr.acquirer_name, tr.value_amount
            FROM summary s JOIN transaction_record tr
              ON tr.transaction_id = s.transaction_id AND tr.is_current = 1
            WHERE s.is_current = 1"""):
        txt = s["summary_text"] or ""
        for who in ("target_name", "acquirer_name"):
            nm = (s[who] or "").strip()
            if nm and nm.split()[0].lower() not in txt.lower():
                A(("summary", f"{s['transaction_id']}: summary does not mention {who}={nm!r}"))
        if s["word_count"] and not (60 <= s["word_count"] <= 260):
            A(("summary", f"{s['transaction_id']}: word_count={s['word_count']} outside the "
                          f"prompt's stated length band"))
    n_tx_ = conn.execute("SELECT COUNT(*) FROM transaction_record WHERE is_current = 1").fetchone()[0]
    n_sum = conn.execute("SELECT COUNT(*) FROM summary WHERE is_current = 1").fetchone()[0]
    if n_sum != n_tx_:
        A(("summary", f"{n_tx_} canonical transactions but {n_sum} current summaries"))
    return ex


def write_review(out_dir, corpus, seeded, trace, stage_results, ex, conn) -> None:
    n_src = len(seeded)
    n_se = conn.execute("SELECT COUNT(*) FROM staging_extraction").fetchone()[0]
    n_cl = conn.execute("SELECT COUNT(DISTINCT transaction_cluster_id) FROM staging_extraction "
                        "WHERE transaction_cluster_id IS NOT NULL").fetchone()[0]
    n_tx = conn.execute("SELECT COUNT(*) FROM transaction_record WHERE is_current = 1").fetchone()[0]
    n_ob = conn.execute("SELECT COUNT(*) FROM transaction_field_observation").fetchone()[0]
    n_sum = conn.execute("SELECT COUNT(*) FROM summary WHERE is_current = 1").fetchone()[0]

    L = ["# PL integration run — production transaction path, isolated database\n",
         f"Generated {datetime.now(timezone.utc).isoformat()}\n",
         "## Actual cardinality\n",
         f"**{n_src} sources → {n_se} staging extractions → {n_cl} clusters → "
         f"{n_tx} canonical transactions → {n_sum} summaries**  "
         f"({n_ob} observations written)\n",
         "No transaction count was assumed; the numbers above are counted from the database.\n",
         "## Stage results\n", "| stage | returned |", "| --- | --- |"]
    for name, res in stage_results.items():
        L.append(f"| `{name}` | {json.dumps(res)} |")
    L.append("")
    L.append("## Exceptions\n")
    L.append("This section is the report. A clean run surfaces nothing here; everything "
             "listed is something a human should look at, not necessarily a defect.\n")
    if not ex:
        L.append("**No exceptions surfaced.**\n")
    else:
        cur = None
        for kind, msg in sorted(ex, key=lambda x: x[0]):
            if kind != cur:
                L.append(f"\n**{kind}**\n")
                cur = kind
            L.append(f"- {msg}")
        L.append("")
    L.append("\n## What did not run, and why that is not a gap\n")
    L.append("- **SEC trigger/enrichment (5/6)** — Stage 7 accepts `HC_EXTRACTED` directly, so the "
             "SEC path is optional by construction. No SEC call was made.")
    L.append("- **Agreement extraction (10/11)** — runs after Stage 9. Every source here is a news "
             "story with no attached agreement, so this is the production-normal state for this "
             "corpus. Note that `summarize` renders a NULL `has_go_shop` as `false`; that is "
             "production behaviour, not a finding.")
    L.append("- **Strategic Rationale (13)** — tabled; nothing upstream depends on it.")
    L.append("- **Production export (14)** — replaced by the CSVs beside this file.\n")
    open(os.path.join(out_dir, "review.md"), "w", encoding="utf-8").write("\n".join(L))


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--relevancy-results", required=True,
                    help="results.jsonl from the Stage-1 run; notes.relevancy is seeded from it.")
    ap.add_argument("--corpus", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "pl_integration_corpus.txt"))
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Seed the isolated DB and stop. No stage runs, no model call.")
    args = ap.parse_args()

    ids = read_manifest(args.corpus)
    corpus = load_corpus(args.tsv, args.relevancy_results, ids)
    os.makedirs(args.out_dir, exist_ok=True)
    db_path = os.path.abspath(os.path.join(args.out_dir, "pl_integration.db"))

    print("=" * 74)
    print("PL INTEGRATION RUN — isolated database")
    print("=" * 74)
    print(f"  corpus      : {len(corpus)} stories from {args.corpus}")
    print(f"  isolated DB : {db_path}")
    print(f"  stages      : {', '.join(n for n, _ in PIPELINE)}")
    print("  NOT run     : SEC 5/6, agreement 10/11, rationale 13, export 14")

    if os.path.exists(db_path):
        raise SystemExit(f"ERROR: {db_path} already exists. Delete it for a clean run — this "
                         "script never appends to an existing validation database.")

    init_db(db_path)
    conn = get_connection(db_path)
    seeded = seed(conn, corpus)
    print(f"\n  seeded {len(seeded)} source_raw rows at source_status='RELEVANT'")

    if args.dry_run:
        print("\nDRY RUN — database seeded, no stage invoked, no model call made.")
        for c in corpus[:3]:
            print(f"    {c['pl_event_id'][:8]} {c['relevancy']['reason_code']:<28} "
                  f"{len(c['body']):5}c  {c['title'][:58]}")
        conn.close()
        return 0

    try:
        base = get_config()
    except ConfigurationError as exc:
        print(f"\nCannot run stages: {exc}\nThe --dry-run path needs no credentials.",
              file=sys.stderr)
        conn.close()
        return 3
    # The ONLY override. Everything else is the real production configuration.
    cfg = dataclasses.replace(base, db_path=db_path)
    if cfg.db_path != db_path:
        raise SystemExit("ERROR: db_path override did not take; refusing to run")
    if os.path.abspath(base.db_path) == db_path:
        raise SystemExit("ERROR: the isolated path resolves to the configured production DB; "
                         "choose a different --out-dir")

    run_id = f"pl_integration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    log = get_logger("pl_integration", run_id, level=cfg.log_level)
    results: dict[str, dict] = {}
    print()
    for name, mod in PIPELINE:
        print(f"  → {name} …", flush=True)
        try:
            results[name] = mod.run(conn, cfg, run_id) or {}
        except Exception as exc:                                  # noqa: BLE001
            results[name] = {"EXCEPTION": f"{type(exc).__name__}: {exc}"}
            log.error("%s raised: %s", name, exc)
            print(f"     RAISED {type(exc).__name__}: {exc}")
            break
        print(f"     {json.dumps(results[name])}")

    trace = traceability(conn)
    ex = exceptions(conn, trace, results, seeded)

    _dump(os.path.join(args.out_dir, "traceability.csv"), trace)
    _dump(os.path.join(args.out_dir, "transactions.csv"),
          _rows(conn, "SELECT * FROM transaction_record WHERE is_current = 1 ORDER BY transaction_id"))
    _dump(os.path.join(args.out_dir, "observations.csv"),
          _rows(conn, "SELECT observation_id, transaction_id, field_name, field_value, "
                      "source_raw_id, staging_extraction_id, source_tier, model_confidence, "
                      "observation_source_stage, extraction_prompt_version, is_current "
                      "FROM transaction_field_observation ORDER BY transaction_id, field_name"))
    _dump(os.path.join(args.out_dir, "summaries.csv"),
          _rows(conn, "SELECT transaction_id, summary_text, word_count, model_confidence, "
                      "prompt_version FROM summary WHERE is_current = 1"))
    json.dump(results, open(os.path.join(args.out_dir, "stage_results.json"), "w"), indent=2)
    write_review(args.out_dir, corpus, seeded, trace, results, ex, conn)

    n_tx = conn.execute("SELECT COUNT(*) FROM transaction_record WHERE is_current = 1").fetchone()[0]
    n_cl = conn.execute("SELECT COUNT(DISTINCT transaction_cluster_id) FROM staging_extraction "
                        "WHERE transaction_cluster_id IS NOT NULL").fetchone()[0]
    n_se = conn.execute("SELECT COUNT(*) FROM staging_extraction").fetchone()[0]
    conn.close()
    print(f"\n  {len(seeded)} sources → {n_se} staging extractions → {n_cl} clusters "
          f"→ {n_tx} canonical transactions")
    print(f"  {len(ex)} exception(s) surfaced — see {args.out_dir}/review.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
