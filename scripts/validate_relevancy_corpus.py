"""
scripts/validate_relevancy_corpus.py — run production Relevancy 0.8 over a corpus
TSV for validation, without touching production state.

Reuses the real Stage-1 path rather than approximating it. Everything that shapes
the model's answer is imported from stages/relevancy_filter.py and prompts/base.py:
the prompt file, the system prompt, the user template, the 4000-char body cap, the
brace escaping, the model tier, temperature, max_tokens, the valid-enum sets and
_normalize_reason_code. Nothing about relevancy classification is reimplemented here.

What it deliberately does NOT do:

  * It never calls relevancy_filter.run(). run() UPDATEs source_raw.source_status
    and source_raw.notes, which is production state.
  * It never opens the pipeline database. call_prompt needs a connection for
    failure logging, so it gets a throwaway scratch DB created in the output
    directory. Delete the directory and nothing remains.
  * It never writes to the input TSV.

Because the corpus is a TSV rather than source_raw rows, the classification is
identical but the persistence is not -- results go to JSONL/CSV artifacts keyed by
the corpus's own stable ID, so reviewed cases can later be promoted into a
repeatable integration regression set.

Run:
    # 1. zero model calls -- renders prompts and prints the cost shape
    python scripts/validate_relevancy_corpus.py --tsv <path> --id-col X --title-col Y \\
        --body-col Z --out-dir out/relevancy_val --dry-run

    # 2. three live stories
    python scripts/validate_relevancy_corpus.py --tsv <path> ... --out-dir out/relevancy_val --limit 3

    # 3. the corpus (resumable: re-running skips IDs already in results.jsonl)
    python scripts/validate_relevancy_corpus.py --tsv <path> ... --out-dir out/relevancy_val
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

from config import ConfigurationError, get_config             # noqa: E402
from db import get_connection, init_db                          # noqa: E402
from logger import get_logger                                   # noqa: E402
from prompts.base import PromptFailure, call_prompt, load_prompt_file  # noqa: E402
from stages.relevancy_filter import (                           # noqa: E402
    _FULL_VERSION, _PROMPT_NAME, _REASON_CODE_ALIASES, _SLEEP,
    _VALID_CLASSIFICATIONS, _VALID_REASON_CODES, _normalize_reason_code,
)

# Mirrors stages/relevancy_filter.py exactly. Imported constants are used where
# they exist; these two are inline in run() there, so they are restated with the
# assertion below standing guard over the copy.
_BODY_CAP = 4000
_MAX_TOKENS = 256
_MODEL = "haiku"

_HAIKU_IN_PER_MTOK = 1.00    # Claude Haiku 4.5, Anthropic first-party rates
_HAIKU_OUT_PER_MTOK = 5.00

# The relevancy prompt tells the model it is reading a press release. Whether the
# corpus actually is one is a reported characteristic, not a gate -- but the report
# must say so, because a precision figure over mostly third-party news coverage is
# not the same measurement as one over wire copy. Computed, never hardcoded.
_WIRE_DOMAINS = frozenset({
    "prnewswire.com", "businesswire.com", "globenewswire.com", "newswire.ca",
    "accesswire.com", "einpresswire.com", "prweb.com", "marketwired.com",
    "prnewswire.co.uk", "globenewswire.no", "businesswire.fr",
})


def _render(title: str, body: str, template: str) -> str:
    """Byte-identical to the production render in relevancy_filter.run()."""
    t = (title or "").replace("{", "{{").replace("}", "}}")
    b = (body or "")[:_BODY_CAP].replace("{", "{{").replace("}", "}}")
    return template.format(title=t, clean_text=b)


def _read_tsv(path: str, cols: dict, dedupe_by: str | None,
              passthrough: list[str]) -> tuple[list[dict], int]:
    """Read the corpus and collapse it to the unit Stage 1 actually classifies.

    A PredictLeads export is one row per *event*, not per *story*: a funding round
    with seven investors emits seven rows sharing one article. Relevancy classifies
    a story, so classifying the raw rows would both waste calls and skew the
    reason-code distribution toward whatever kind of deal happens to name the most
    parties. Deduping keeps the first row per distinct --dedupe-by value and
    records every collapsed ID on it, so the story->events mapping survives into
    the regression set rather than being thrown away.
    """
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        known = reader.fieldnames or []
        wanted = list(cols.values()) + ([dedupe_by] if dedupe_by else []) + passthrough
        missing = [v for v in wanted if v and v not in known]
        if missing:
            raise SystemExit(f"ERROR: column(s) not in TSV header: {missing}\n"
                             f"       header is: {known}")
        out, index = [], {}
        for row in reader:
            rec = {
                "corpus_id": (row.get(cols["id"]) or "").strip(),
                "title": (row.get(cols["title"]) or "").strip() if cols["title"] else "",
                "body": (row.get(cols["body"]) or "").strip(),
                "url": (row.get(cols["url"]) or "").strip() if cols["url"] else "",
                "published": (row.get(cols["date"]) or "").strip() if cols["date"] else "",
                "grouped_ids": [], "group_size": 1,
                # Verbatim corpus context for the human reviewer. Deliberately opt-in:
                # carrying the source's own classification into a review sheet anchors
                # the reviewer to it, which is the contamination we are trying to avoid.
                "passthrough": {c: (row.get(c) or "").strip() for c in passthrough},
            }
            if not dedupe_by:
                out.append(rec)
                continue
            key = (row.get(dedupe_by) or "").strip()
            if key and key in index:
                kept = index[key]
                kept["grouped_ids"].append(rec["corpus_id"])
                kept["group_size"] += 1
                continue
            rec["grouped_ids"] = [rec["corpus_id"]]
            if key:
                index[key] = rec
            out.append(rec)
    collapsed = sum(r["group_size"] - 1 for r in out)
    return out, collapsed


def _domain(url: str) -> str:
    if "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0].lower()


def _aggregate(records: list[dict]) -> dict:
    n = len(records)
    cls = Counter(r["classification"] for r in records)
    codes = Counter(r["reason_code"] for r in records)
    conf = Counter(r.get("model_confidence") or "(none)" for r in records)
    normalized = [r for r in records if r.get("normalization_applied")]
    catchalls = {"AMBIGUOUS_BUT_LIKELY_DEAL", "OTHER_NOT_RELEVANT"}

    def rate(k):
        return round(100.0 * k / n, 2) if n else 0.0

    never = sorted(c for c in _VALID_REASON_CODES if c not in codes)
    return {
        "corpus_size": n,
        "classification": {
            "RELEVANT": cls.get("RELEVANT", 0),
            "NOT_RELEVANT": cls.get("NOT_RELEVANT", 0),
            "RELEVANT_pct": rate(cls.get("RELEVANT", 0)),
            "NOT_RELEVANT_pct": rate(cls.get("NOT_RELEVANT", 0)),
        },
        "reason_code_distribution": [
            {"reason_code": c, "count": k, "pct": rate(k)} for c, k in codes.most_common()
        ],
        "catch_all": {
            "AMBIGUOUS_BUT_LIKELY_DEAL": codes.get("AMBIGUOUS_BUT_LIKELY_DEAL", 0),
            "AMBIGUOUS_BUT_LIKELY_DEAL_pct": rate(codes.get("AMBIGUOUS_BUT_LIKELY_DEAL", 0)),
            "OTHER_NOT_RELEVANT": codes.get("OTHER_NOT_RELEVANT", 0),
            "OTHER_NOT_RELEVANT_pct": rate(codes.get("OTHER_NOT_RELEVANT", 0)),
            "combined_pct": rate(sum(codes.get(c, 0) for c in catchalls)),
        },
        "never_occurring_reason_codes": {"count": len(never), "codes": never},
        "normalization": {
            "count": len(normalized),
            "pct": rate(len(normalized)),
            "raw_values": [
                {"raw": v, "count": k}
                for v, k in Counter(r["raw_reason_code"] for r in normalized).most_common()
            ],
        },
        "model_confidence": {k: v for k, v in conf.most_common()},
        "validation_shapes": {
            shape: [
                {"corpus_id": r["corpus_id"], "title": r["title"][:140], "url": r["url"],
                 "reason_code": r["reason_code"], "model_confidence": r.get("model_confidence")}
                for r in records if r["reason_code"] == shape
            ][:10]
            for shape in ("MERGER_ANNOUNCEMENT", "SPIN_OFF_OR_SPLIT", "EARNINGS_OR_FINANCIAL_REPORTING")
        },
        "source_composition": {
            "distinct_domains": len({r["domain"] for r in records if r["domain"]}),
            "pr_wire_stories": sum(1 for r in records if r["domain"] in _WIRE_DOMAINS),
            "pr_wire_pct": rate(sum(1 for r in records if r["domain"] in _WIRE_DOMAINS)),
            "top_domains": [{"domain": d, "count": k} for d, k in
                            Counter(r["domain"] for r in records if r["domain"]).most_common(15)],
        },
        "stage1_truncated": sum(1 for r in records if r.get("body_truncated_by_stage1")),
        "prompt_version": _FULL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_outputs(out_dir: str, records: list[dict], agg: dict) -> None:
    extra = sorted({k for r in records for k in (r.get("passthrough") or {})})
    with open(os.path.join(out_dir, "results.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["corpus_id", "title", "domain", "url", "published", "classification",
                    "reason_code", "model_confidence", "raw_reason_code",
                    "normalization_applied", "model_notes", "body_chars",
                    "body_truncated_by_stage1", "group_size", "grouped_ids",
                    "body_excerpt", "prompt_version"] + extra)
        for r in records:
            w.writerow([
                r["corpus_id"], r["title"], r["domain"], r["url"], r["published"],
                r["classification"], r["reason_code"], r.get("model_confidence") or "",
                r["raw_reason_code"], "yes" if r["normalization_applied"] else "",
                r.get("model_notes") or "", r["body_chars"],
                "yes" if r["body_truncated_by_stage1"] else "",
                r.get("group_size", 1), ";".join(r.get("grouped_ids") or []),
                r["body_excerpt"], r["prompt_version"],
            ] + [(r.get("passthrough") or {}).get(k, "") for k in extra])
    with open(os.path.join(out_dir, "aggregate.json"), "w", encoding="utf-8") as fh:
        json.dump(agg, fh, indent=2)

    L = []
    a = agg
    L.append(f"# Relevancy {_FULL_VERSION} on natural PL traffic\n")
    L.append(f"Corpus size: **{a['corpus_size']}** stories  ·  generated {a['generated_at']}\n")
    sc = a["source_composition"]
    L.append("## How to read this\n")
    L.append(f"This measures **{_FULL_VERSION} on natural PredictLeads traffic** — the distribution "
             "that arrives on an ordinary couple of days, not a curated boundary corpus.\n")
    L.append(f"- **Source composition.** Only **{sc['pr_wire_stories']} of {a['corpus_size']} stories "
             f"({sc['pr_wire_pct']}%) come from PR-wire domains**; the rest is third-party news "
             f"coverage across {sc['distinct_domains']} distinct publishers. The delivered prompt tells "
             "the model it is reading a press release, so most of this corpus is a document type the "
             "prompt does not describe. Reported as a characteristic of the corpus, not a defect.")
    L.append("- **This is not a clean 0.7 → 0.8 regression measurement.** No 0.7 baseline was run over "
             "this corpus, and the corpus is not the curated Gate 2 boundary set. Treat these figures "
             "as the observed behaviour of 0.8 on this traffic — not as an attributable delta against "
             "the prior prompt version.")
    L.append("- **Targeted Relevancy validation, not PL production-readiness.** Broader questions about "
             "ingesting PL are out of scope here.")
    tr = a["stage1_truncated"]
    L.append(f"- **{tr} {'story' if tr == 1 else 'stories'} exceeded the production 4,000-char body cap** "
             f"and reached the model truncated. That is Stage 1 behaviour, applied identically to "
             f"production.")
    L.append("- PL's own `category` and generated `summary` are descriptive metadata only. They are not "
             "ground truth, are not inputs to classification, and are not exposed in the review "
             "artifact.\n")
    L.append("## Classification\n")
    L.append(f"- RELEVANT: {a['classification']['RELEVANT']} ({a['classification']['RELEVANT_pct']}%)")
    L.append(f"- NOT_RELEVANT: {a['classification']['NOT_RELEVANT']} ({a['classification']['NOT_RELEVANT_pct']}%)\n")
    L.append("## reason_code distribution\n")
    L.append("| reason_code | count | pct |\n| --- | ---: | ---: |")
    for row in a["reason_code_distribution"]:
        L.append(f"| `{row['reason_code']}` | {row['count']} | {row['pct']}% |")
    L.append("")
    L.append("## Catch-all and normalization\n")
    c = a["catch_all"]
    L.append(f"- `AMBIGUOUS_BUT_LIKELY_DEAL`: {c['AMBIGUOUS_BUT_LIKELY_DEAL']} ({c['AMBIGUOUS_BUT_LIKELY_DEAL_pct']}%)")
    L.append(f"- `OTHER_NOT_RELEVANT`: {c['OTHER_NOT_RELEVANT']} ({c['OTHER_NOT_RELEVANT_pct']}%)")
    L.append(f"- combined catch-all rate: {c['combined_pct']}%")
    L.append(f"- outputs requiring alias/catch-all normalization: {a['normalization']['count']} "
             f"({a['normalization']['pct']}%)")
    for rv in a["normalization"]["raw_values"]:
        L.append(f"    - model returned `{rv['raw']}` ×{rv['count']}")
    L.append(f"- reason codes never occurring: {a['never_occurring_reason_codes']['count']} of "
             f"{len(_VALID_REASON_CODES)}")
    L.append(f"    - {', '.join('`%s`' % x for x in a['never_occurring_reason_codes']['codes']) or 'none'}\n")
    L.append("## Publisher domains\n")
    L.append("| domain | stories |\n| --- | ---: |")
    for d in a["source_composition"]["top_domains"]:
        L.append(f"| {d['domain']} | {d['count']} |")
    L.append("")
    L.append("## Validation shapes — surfaced for Product review, not asserted correct\n")
    for shape, rows in a["validation_shapes"].items():
        L.append(f"**`{shape}`** — {len(rows)} shown")
        if not rows:
            L.append("- none present in this corpus (natural distribution; absence is not a failure)")
        for r in rows:
            L.append(f"- `{r['corpus_id']}` — {r['title']} ({r['model_confidence']}) {r['url']}")
        L.append("")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tsv", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--id-col", required=True, help="Stable per-story ID column.")
    ap.add_argument("--body-col", required=True, help="Story body column.")
    ap.add_argument("--title-col", default=None)
    ap.add_argument("--url-col", default=None)
    ap.add_argument("--date-col", default=None)
    ap.add_argument("--limit", type=int, default=0, help="Classify at most N rows (0 = all).")
    ap.add_argument("--dry-run", action="store_true", help="Render prompts, make zero model calls.")
    ap.add_argument("--dedupe-by", default=None,
                    help="Column identifying one story (e.g. source_url). Rows sharing a value are "
                         "collapsed to one classification; the collapsed IDs are kept on the record.")
    ap.add_argument("--passthrough-cols", default="",
                    help="Comma-separated corpus columns copied verbatim into results.csv for human "
                         "review context. Opt-in on purpose: do NOT pass a column carrying the "
                         "source's own classification -- it anchors the reviewer to it.")
    ap.add_argument("--min-body-chars", type=int, default=1,
                    help="Skip rows whose body is shorter than this. Stage 1 itself skips empty bodies.")
    args = ap.parse_args()

    # Guard the two constants restated from the stage. If production changes them,
    # this runner must not silently keep validating the old shape.
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "stages", "relevancy_filter.py"), encoding="utf-8").read()
    for needle, what in ((f"[:{_BODY_CAP}]", "body cap"), (f"max_tokens={_MAX_TOKENS}", "max_tokens"),
                         (f'model="{_MODEL}"', "model tier")):
        if needle not in src:
            print(f"ERROR: production {what} no longer matches this runner ({needle!r} not found in "
                  f"stages/relevancy_filter.py). Update the runner before validating.", file=sys.stderr)
            return 2

    os.makedirs(args.out_dir, exist_ok=True)
    cols = {"id": args.id_col, "title": args.title_col, "body": args.body_col,
            "url": args.url_col, "date": args.date_col}
    passthrough = [c.strip() for c in args.passthrough_cols.split(",") if c.strip()]
    rows, collapsed = _read_tsv(args.tsv, cols, args.dedupe_by, passthrough)

    prompt = load_prompt_file(_PROMPT_NAME)
    system = prompt["system"]

    skipped = [r for r in rows if len(r["body"]) < args.min_body_chars]
    work = [r for r in rows if len(r["body"]) >= args.min_body_chars]

    jsonl_path = os.path.join(args.out_dir, "results.jsonl")
    done: dict[str, dict] = {}
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    done[rec["corpus_id"]] = rec
                except ValueError:
                    continue
        print(f"resume: {len(done)} results already present in {jsonl_path}")

    pending = [r for r in work if r["corpus_id"] not in done]
    if args.limit:
        pending = pending[:args.limit]

    sys_chars = len(system)
    user_chars = [len(_render(r["title"], r["body"], prompt["user_template"])) for r in work]
    avg_user = int(sum(user_chars) / len(user_chars)) if user_chars else 0
    # ~3.7 chars/token is a rough English estimate, not a tokenizer measurement.
    est_in = int((sys_chars + avg_user) / 3.7)
    est_out = 60
    calls = len(pending)
    est_cost = (calls * est_in / 1e6) * _HAIKU_IN_PER_MTOK + (calls * est_out / 1e6) * _HAIKU_OUT_PER_MTOK

    print("=" * 74)
    print(f"RELEVANCY CORPUS VALIDATION — {_FULL_VERSION}")
    print("=" * 74)
    print(f"  stories to classify    : {len(rows)}"
          + (f"   ({collapsed} duplicate rows collapsed by {args.dedupe_by})" if args.dedupe_by else ""))
    print(f"  skipped (body < {args.min_body_chars} chars): {len(skipped)}")
    print(f"  already done (resume)  : {len(done)}")
    print(f"  calls this invocation  : {calls}")
    print(f"  system prompt          : {sys_chars} chars")
    print(f"  user prompt (mean)     : {avg_user} chars   [body capped at {_BODY_CAP} by production]")
    print(f"  est. input tokens/call : ~{est_in}   (estimate, not a tokenizer count)")
    print(f"  est. cost              : ~${est_cost:.2f} at Haiku 4.5 "
          f"${_HAIKU_IN_PER_MTOK:.2f}/${_HAIKU_OUT_PER_MTOK:.2f} per MTok")
    print(f"  throttle               : {_SLEEP}s between calls "
          f"(~{int(calls * _SLEEP // 60)}m of sleep alone)")
    print("=" * 74)

    if args.dry_run:
        print("\nDRY RUN — no model calls. First rendered user prompt:\n")
        if pending:
            r = pending[0]
            print("-" * 74)
            print(_render(r["title"], r["body"], prompt["user_template"])[:1500])
            print("-" * 74)
        print("\nRe-run without --dry-run to classify.")
        return 0

    try:
        cfg = get_config()
    except ConfigurationError as exc:
        print(f"\nCannot make live calls: {exc}\n"
              f"The dry run above needs no credentials and is still valid.", file=sys.stderr)
        return 3
    log = get_logger("relevancy_corpus_validation", "validation", level=cfg.log_level)
    # Scratch DB. call_prompt writes extraction_failure_log on failure and needs a
    # connection; it must not be the pipeline database.
    # Order and argument type follow db.py's contract and run.py:236-237: init_db
    # takes a PATH and opens its own connection to run the DDL, then get_connection
    # returns the connection to use. Handing init_db an open Connection raises
    # TypeError inside pathlib before a single row is read.
    scratch = os.path.join(args.out_dir, "_scratch.db")
    init_db(scratch)
    conn = get_connection(scratch)

    run_id = f"relevancy_val_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out = open(jsonl_path, "a", encoding="utf-8")
    failed = 0

    for i, r in enumerate(pending, start=1):
        user_prompt = _render(r["title"], r["body"], prompt["user_template"])
        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME, prompt_version=_FULL_VERSION,
                user_prompt=user_prompt, system_prompt=system,
                model=_MODEL, temperature=0.0, max_tokens=_MAX_TOKENS,
                cfg=cfg, conn=conn, run_id=run_id, source_raw_id=None, log=log,
            )
        except PromptFailure as exc:
            failed += 1
            print(f"  [{i}/{len(pending)}] {r['corpus_id']}  PROMPT_FAILED: {exc}")
            time.sleep(_SLEEP)
            continue

        classification = result.get("classification")
        raw_code = result.get("reason_code")
        if classification not in _VALID_CLASSIFICATIONS:
            failed += 1
            print(f"  [{i}/{len(pending)}] {r['corpus_id']}  SCHEMA_VIOLATION: "
                  f"classification={classification!r}")
            time.sleep(_SLEEP)
            continue

        code = raw_code
        normalized = False
        if code not in _VALID_REASON_CODES:
            code = _normalize_reason_code(raw_code, classification)
            normalized = True

        rec = {
            "corpus_id": r["corpus_id"], "title": r["title"], "url": r["url"],
            "domain": _domain(r["url"]), "published": r["published"],
            "classification": classification, "reason_code": code,
            "raw_reason_code": raw_code, "normalization_applied": normalized,
            "normalization_kind": (
                "" if not normalized
                else "alias" if (raw_code or "").strip().upper() in _REASON_CODE_ALIASES
                else "catch_all_or_suffix"
            ),
            "model_confidence": result.get("model_confidence"),
            "model_notes": result.get("notes"),
            "body_chars": len(r["body"]),
            "body_truncated_by_stage1": len(r["body"]) > _BODY_CAP,
            "passthrough": r["passthrough"],
            "grouped_ids": r["grouped_ids"],
            "group_size": r["group_size"],
            "body_excerpt": r["body"][:600],
            "prompt_version": _FULL_VERSION,
        }
        out.write(json.dumps(rec) + "\n")
        out.flush()
        done[r["corpus_id"]] = rec
        flag = f"  [normalized {raw_code!r}→{code}]" if normalized else ""
        print(f"  [{i}/{len(pending)}] {r['corpus_id']}  {classification:<12} {code:<32} "
              f"{result.get('model_confidence')}{flag}")
        time.sleep(_SLEEP)

    out.close()
    conn.close()

    records = list(done.values())
    agg = _aggregate(records)
    agg["skipped_short_or_empty_body"] = len(skipped)
    agg["prompt_failures_this_run"] = failed
    _write_outputs(args.out_dir, records, agg)

    print(f"\nclassified {len(records)} stories, {failed} failures this run")
    print(f"artifacts in {args.out_dir}/:  results.jsonl  results.csv  aggregate.json  report.md")
    print(f"(scratch DB at {scratch} — safe to delete; no pipeline state was touched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
