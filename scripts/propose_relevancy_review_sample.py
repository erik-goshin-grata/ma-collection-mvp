"""
scripts/propose_relevancy_review_sample.py — analyse a Relevancy validation run and
draw a blinded human-review sample from it.

Reads results.jsonl only. Makes no model calls, opens no database, and changes
nothing. Two jobs:

  1. Report the observed distribution -- classification split, every reason_code
     with counts and rates, catch-all rates, normalization rate with the raw
     values the model actually returned and what they became, confidence
     distribution, unused and rare codes, and publisher patterns.

  2. Draw a stratified sample for human review, allocated from that observed
     distribution rather than from any prior expectation of it.

The sample is BLINDED by construction. Two files come out:

  * review_sample_blinded.csv -- what the reviewer sees: the story and empty
    columns to fill in. No classification, no reason_code, no confidence, no
    stratum label. A reviewer who can see the machine's answer is checking the
    machine's answer rather than reading the source, and the disagreement rate
    stops meaning anything.
  * review_sample_key.csv -- the machine's answers plus the stratum each row was
    drawn from, keyed by corpus_id. Held back until the review is returned.

Neither file carries the corpus's own classification or generated summary, on
the same reasoning: a reviewer anchored to a third-party label is not an
independent check.

Selection is deterministic -- rows are ordered by a stable hash of corpus_id, so
the same run and the same target size always yield the same sample and the
result is a repeatable regression corpus rather than a one-off draw.

Run:
    python scripts/propose_relevancy_review_sample.py \\
        --results out/relevancy_val/results.jsonl \\
        --out-dir out/relevancy_val --target 120
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from stages.relevancy_filter import _VALID_REASON_CODES  # noqa: E402

_CATCH_ALL = ("AMBIGUOUS_BUT_LIKELY_DEAL", "OTHER_NOT_RELEVANT")
_LOWER_CONF = ("MEDIUM", "LOW", "NONE", None, "")

# Deal language in a headline whose story was ruled NOT_RELEVANT. Not evidence of
# an error -- plenty of releases mention an acquisition without announcing one --
# but it is where a false negative would hide, so those rows get looked at.
_FN_TITLE_RE = re.compile(
    r"\b(acquir\w*|acquisition|merge\w*|merger|takeover|take-?private|buy\w*|"
    r"purchase[sd]?|sells?|sold|divest\w*|carve-?out|spin-?off|spin\s?out|"
    r"stake|majority|minority|joint venture|jv|recapitali\w*|"
    r"series\s+[a-z]\b|seed round|funding round|raises?|raised|investment|"
    r"invests?|backs?|definitive agreement|letter of intent)\b", re.I)

# Ordinary deal traffic: the volume categories where precision matters most.
_ORDINARY = ("ACQUISITION_ANNOUNCEMENT", "MINORITY_INVESTMENT", "VC_ROUND_OR_FUNDING",
             "CARVE_OUT_OR_DIVESTITURE", "DEAL_CLOSE_OR_COMPLETION", "TAKE_PRIVATE")


def _order(rows: list[dict]) -> list[dict]:
    """Stable, seed-free ordering so the same inputs always draw the same sample."""
    return sorted(rows, key=lambda r: hashlib.sha256(r["corpus_id"].encode()).hexdigest())


def _load(path: str) -> list[dict]:
    out, bad = [], 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                bad += 1
    if bad:
        print(f"  WARNING: {bad} unparseable line(s) in {path}", file=sys.stderr)
    return out


def _pct(k: int, n: int) -> float:
    return round(100.0 * k / n, 2) if n else 0.0


def report(recs: list[dict]) -> dict:
    n = len(recs)
    P = print
    codes = Counter(r["reason_code"] for r in recs)
    cls = Counter(r["classification"] for r in recs)
    conf = Counter((r.get("model_confidence") or "(none)") for r in recs)
    norm = [r for r in recs if r.get("normalization_applied")]
    doms = Counter(r.get("domain") or "(none)" for r in recs)

    P("=" * 78)
    P(f"RELEVANCY VALIDATION — OBSERVED DISTRIBUTION  ({n} stories)")
    P("=" * 78)

    P("\n--- classification " + "-" * 58)
    for k in ("RELEVANT", "NOT_RELEVANT"):
        P(f"  {k:<14} {cls.get(k, 0):5}  {_pct(cls.get(k, 0), n):6.2f}%")

    P("\n--- reason_code (all observed) " + "-" * 46)
    P(f"  {'code':<34} {'n':>5} {'rate':>8}   side")
    for c, k in codes.most_common():
        side = Counter(r["classification"] for r in recs if r["reason_code"] == c).most_common(1)[0][0]
        P(f"  {c:<34} {k:5} {_pct(k, n):7.2f}%   {side}")

    P("\n--- catch-alls " + "-" * 61)
    tot = 0
    for c in _CATCH_ALL:
        tot += codes.get(c, 0)
        P(f"  {c:<34} {codes.get(c, 0):5} {_pct(codes.get(c, 0), n):7.2f}%")
    P(f"  {'COMBINED':<34} {tot:5} {_pct(tot, n):7.2f}%")

    P("\n--- normalization " + "-" * 58)
    P(f"  outputs requiring normalization: {len(norm)}  ({_pct(len(norm), n)}%)")
    if norm:
        pairs = Counter((r.get("raw_reason_code"), r["reason_code"]) for r in norm)
        P(f"  {'model returned (raw)':<38} -> {'normalized to':<32}  n   kind")
        for (raw, final), k in pairs.most_common():
            kind = next((x.get("normalization_kind", "") for x in norm
                         if x.get("raw_reason_code") == raw), "")
            P(f"  {str(raw):<38} -> {final:<32} {k:3}   {kind}")

    P("\n--- model_confidence " + "-" * 55)
    for c, k in conf.most_common():
        P(f"  {c:<14} {k:5}  {_pct(k, n):6.2f}%")

    unused = sorted(c for c in _VALID_REASON_CODES if c not in codes)
    rare = sorted((c for c, k in codes.items() if k <= max(3, int(0.01 * n))), key=lambda c: codes[c])
    P("\n--- coverage of the 24-code vocabulary " + "-" * 37)
    P(f"  observed: {len(codes)} of {len(_VALID_REASON_CODES)}")
    P(f"  never occurring ({len(unused)}): {', '.join(unused) or 'none'}")
    P(f"  rare (<= max(3, 1%)): {', '.join(f'{c}={codes[c]}' for c in rare) or 'none'}")

    P("\n--- publishers " + "-" * 61)
    P(f"  distinct domains: {len(doms)}")
    for d, k in doms.most_common(15):
        rel = sum(1 for r in recs if r.get("domain") == d and r["classification"] == "RELEVANT")
        P(f"  {d:<36} {k:4}   RELEVANT {rel}/{k}")

    P("\n--- patterns that merit review (observations, NOT defect claims) " + "-" * 11)
    obs = []
    if tot:
        obs.append(f"combined catch-all rate is {_pct(tot, n)}% — every one of those is a story the "
                   f"model could not place in a named category")
    if norm:
        obs.append(f"{len(norm)} outputs ({_pct(len(norm), n)}%) came back off-enum and were rescued by "
                   f"normalization; the raw values above show what the model reached for instead")
    fn = [r for r in recs if r["classification"] == "NOT_RELEVANT" and _FN_TITLE_RE.search(r.get("title") or "")]
    if fn:
        obs.append(f"{len(fn)} NOT_RELEVANT stories carry deal language in the headline — where a false "
                   f"negative would hide; not evidence of one")
    single = [c for c, k in codes.items() if k == 1]
    if single:
        obs.append(f"{len(single)} code(s) fired exactly once ({', '.join(sorted(single))}) — too thin to "
                   f"read anything from without looking at the story")
    skew = doms.most_common(1)
    if skew and skew[0][1] >= max(10, int(0.05 * n)):
        obs.append(f"one publisher ({skew[0][0]}) accounts for {skew[0][1]} stories "
                   f"({_pct(skew[0][1], n)}%) — check it is not driving a category on its own")
    lowconf = sum(1 for r in recs if (r.get("model_confidence") or "") in ("LOW", "MEDIUM"))
    if lowconf:
        obs.append(f"{lowconf} stories ({_pct(lowconf, n)}%) were classified at MEDIUM or LOW confidence")
    trunc = sum(1 for r in recs if r.get("body_truncated_by_stage1"))
    if trunc:
        obs.append(f"{trunc} stories ({_pct(trunc, n)}%) reached the model truncated at the production "
                   f"4,000-char cap")
    for o in obs:
        P(f"  - {o}")
    P("\n  These are observations. None is a defect until a human has read the source text.")

    return {"n": n, "codes": codes, "cls": cls, "conf": conf, "norm": norm,
            "unused": unused, "rare": rare, "fn_risk": fn, "doms": doms}


def draw(recs: list[dict], stats: dict, target: int, census_max: int = 10,
         catch_all_cap: int = 20, coverage_floor: int = 3, fn_risk: int = 15,
         fp_risk: int = 10, ordinary_share: float = 0.18,
         ordinary_min: int = 22) -> "OrderedDict[str, list[dict]]":
    """Allocate the sample from the observed distribution, most-informative first.

    Sized so a reviewer can finish it. The knobs are exposed rather than tuned
    inline because the right allocation depends on what the run actually produced:
    a corpus with heavy normalization wants a different shape from one with none.
    """
    codes = stats["codes"]
    taken: set[str] = set()
    strata: "OrderedDict[str, list[dict]]" = OrderedDict()

    def take(name: str, pool: list[dict], k: int) -> None:
        pick = [r for r in _order(pool) if r["corpus_id"] not in taken][:k]
        for r in pick:
            taken.add(r["corpus_id"])
        if pick:
            strata[name] = pick

    # 1. Every normalized output, if the count is manageable. These are the only
    #    rows that are direct evidence about the delivered-vocabulary change.
    norm = stats["norm"]
    if norm:
        take("normalized_outputs (census)" if len(norm) <= 60 else "normalized_outputs (sampled)",
             norm, len(norm) if len(norm) <= 60 else 60)

    # 2. CENSUS of every thin code. One rule replaces three special cases: it takes
    #    both AMBIGUOUS rows, every rare code, and -- at the default threshold --
    #    all of MERGER_ANNOUNCEMENT, which is where the questionable merger calls
    #    live. A code that fired this few times cannot be judged from a sample of
    #    it, so the whole thing gets read.
    for c in sorted(codes, key=lambda x: codes[x]):
        if codes[c] <= census_max and c != "OTHER_NOT_RELEVANT":
            take(f"census::{c}", [r for r in recs if r["reason_code"] == c], codes[c])

    # 3. The residual catch-all, heavily but capped -- 107 rows is more than a
    #    reviewer needs to see the shape of what lands there.
    pool = [r for r in recs if r["reason_code"] == "OTHER_NOT_RELEVANT"]
    if pool:
        take("catch_all::OTHER_NOT_RELEVANT", pool, min(len(pool), catch_all_cap))

    # 4. Every remaining observed code gets a floor. Without this, mid-frequency
    #    codes fall through the gap between the census and the ordinary-traffic
    #    list -- which on the first draw silently left the whole NOT_RELEVANT side
    #    apart from the catch-all with zero rows. A code that fired and was never
    #    looked at is a code we know nothing about.
    for c in sorted(codes, key=lambda x: -codes[x]):
        if c in _CATCH_ALL or c in _ORDINARY or codes[c] <= census_max:
            continue
        take(f"coverage::{c}", [r for r in recs if r["reason_code"] == c],
             min(codes[c], coverage_floor))

    # 5. Lower-confidence rows, wherever they landed. Often empty -- if the model
    #    is HIGH on everything, confidence is not a usable stratifier.
    take("lower_confidence", [r for r in recs if (r.get("model_confidence") or "") in _LOWER_CONF], 15)

    # 6. False-negative risk: NOT_RELEVANT with deal language in the headline.
    take("fn_risk::not_relevant_with_deal_language", stats["fn_risk"], fn_risk)

    # 7. False-positive risk: RELEVANT at HIGH confidence on a very short body --
    #    confident calls made on the least evidence.
    take("fp_risk::relevant_high_conf_short_body",
         [r for r in recs if r["classification"] == "RELEVANT"
          and (r.get("model_confidence") or "") == "HIGH"
          and (r.get("body_chars") or 0) < 900], fp_risk)

    # 8. Ordinary deal traffic, from a RESERVED budget rather than leftovers.
    #    Allocating it last from what the priority strata had not eaten starved the
    #    dominant categories -- three rows out of a hundred and seventy says nothing
    #    about the precision that matters most.
    reserve = max(ordinary_min, int(round(ordinary_share * target)))
    ordinary = [(c, codes[c]) for c in _ORDINARY if codes.get(c)]
    denom = sum(k for _, k in ordinary) or 1
    for c, k in ordinary:
        take(f"ordinary::{c}", [r for r in recs if r["reason_code"] == c],
             max(3, round(reserve * k / denom)))

    return strata


_BLIND_COLS = ["corpus_id", "grouped_ids", "group_size", "title", "domain", "url",
               "published", "party_1_name", "party_2_name", "amount", "body_chars",
               "body_excerpt"]
_REVIEWER_COLS = ["reviewer_classification", "reviewer_reason_code", "reviewer_notes"]


def write(out_dir: str, strata: "OrderedDict[str, list[dict]]") -> None:
    rows = [(name, r) for name, pool in strata.items() for r in pool]

    # Hash-ordered, NOT stratum-ordered. Writing the blinded file grouped by stratum
    # would leak the grouping through row order -- a reviewer who notices that rows
    # 1-40 are all one kind of story has been told something about the answer.
    with open(os.path.join(out_dir, "review_sample_blinded.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(_BLIND_COLS + _REVIEWER_COLS)
        for r in _order([r for _, r in rows]):
            p = r.get("passthrough") or {}
            w.writerow([
                r["corpus_id"], ";".join(r.get("grouped_ids") or []), r.get("group_size", 1),
                r.get("title", ""), r.get("domain", ""), r.get("url", ""), r.get("published", ""),
                p.get("party_1_name", ""), p.get("party_2_name", ""), p.get("amount", ""),
                r.get("body_chars", ""), r.get("body_excerpt", ""),
            ] + ["", "", ""])

    with open(os.path.join(out_dir, "review_sample_key.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["corpus_id", "stratum", "classification", "reason_code", "raw_reason_code",
                    "normalization_applied", "normalization_kind", "model_confidence",
                    "model_notes", "grouped_ids", "prompt_version"])
        for name, r in rows:
            w.writerow([r["corpus_id"], name, r["classification"], r["reason_code"],
                        r.get("raw_reason_code", ""), r.get("normalization_applied", ""),
                        r.get("normalization_kind", ""), r.get("model_confidence", ""),
                        r.get("model_notes", "") or "", ";".join(r.get("grouped_ids") or []),
                        r.get("prompt_version", "")])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True, help="results.jsonl from the validation run.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--target", type=int, default=120,
                    help="Sizing basis for the sample (default 120). The priority strata are drawn in "
                         "full, so the actual total can exceed this; the printed total is the truth.")
    ap.add_argument("--census-max", type=int, default=10,
                    help="Any observed code with this many rows or fewer is taken in full (default 10).")
    ap.add_argument("--catch-all-cap", type=int, default=20,
                    help="Cap on OTHER_NOT_RELEVANT rows (default 20).")
    ap.add_argument("--coverage-floor", type=int, default=3,
                    help="Rows per remaining observed code (default 3).")
    ap.add_argument("--fn-risk", type=int, default=15)
    ap.add_argument("--fp-risk", type=int, default=10)
    ap.add_argument("--ordinary-share", type=float, default=0.18,
                    help="Share of --target reserved for ordinary deal traffic (default 0.18).")
    ap.add_argument("--ordinary-min", type=int, default=22,
                    help="Floor on the ordinary-traffic reserve (default 22).")
    ap.add_argument("--report-only", action="store_true", help="Print the distribution, draw no sample.")
    args = ap.parse_args()

    recs = _load(args.results)
    if not recs:
        print(f"ERROR: no records in {args.results}", file=sys.stderr)
        return 1
    stats = report(recs)
    if args.report_only:
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    strata = draw(recs, stats, args.target, args.census_max, args.catch_all_cap,
                  args.coverage_floor, args.fn_risk, args.fp_risk,
                  args.ordinary_share, args.ordinary_min)
    write(args.out_dir, strata)

    total = sum(len(v) for v in strata.values())
    print("\n" + "=" * 78)
    print(f"PROPOSED BLINDED REVIEW SAMPLE — {total} stories "
          f"({_pct(total, stats['n'])}% of {stats['n']})")
    print("=" * 78)
    for name, pool in strata.items():
        print(f"  {name:<52} {len(pool):4}")
    print(f"\n  review_sample_blinded.csv  — {total} rows, reviewer fills the last 3 columns.")
    print("                               No classification, reason_code, confidence or stratum.")
    print("  review_sample_key.csv      — the machine's answers. Hold back until review returns.")
    print("  Selection is hash-ordered: same run + same target reproduces this exact sample.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
