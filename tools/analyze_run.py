"""
analyze_run.py -- fleet diagnosis over a page_harness run.

Eats the meta.json files a run leaves behind and reports, per domain, what your
scraper is actually doing: which fetch tier is winning, what's blocked and why,
where extraction looks degraded, and which site rules are earning their keep.

    python analyze_run.py ./pages
    python analyze_run.py ./pages --json report.json
    python analyze_run.py ./pages --problems      # only domains needing work

Stdlib only. Reads no page content -- meta.json alone.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse


def load(root: str) -> list[dict]:
    metas = []
    for dirpath, _, files in os.walk(root):
        if "meta.json" not in files:
            continue
        try:
            with open(os.path.join(dirpath, "meta.json"), encoding="utf-8") as f:
                m = json.load(f)
            m["_dir"] = dirpath
            metas.append(m)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  !! unreadable: {dirpath} ({e})", file=sys.stderr)
    return metas


def domain(url: str) -> str:
    host = (urlparse(url or "").hostname or "unknown").lower()
    return host[4:] if host.startswith("www.") else host


def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "-"


def analyze(metas: list[dict]) -> dict:
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for m in metas:
        by_domain[domain(m.get("url"))].append(m)

    report = {}
    for dom, items in sorted(by_domain.items()):
        n = len(items)
        ok = [m for m in items if m.get("ok")]
        blocked = [m for m in items if m.get("blocked")]
        suspect = [m for m in ok if m.get("suspect")]
        lengths = [m.get("text_chars", 0) for m in ok if m.get("text_chars")]

        # Which tier won, and how often we had to escalate past the cheap one.
        tiers = Counter(m.get("via") or "-" for m in ok)
        expensive = sum(v for k, v in tiers.items() if not k.startswith("curl_cffi"))

        extractors = Counter(m.get("extractor") or "-" for m in ok)
        site_rule_wins = sum(v for k, v in extractors.items() if k.startswith("site:"))

        # A site rule that never wins is dead weight; one that wins but produces
        # less text than a generic extractor is actively harmful.
        rule_losses = []
        for m in ok:
            cands = m.get("candidates") or {}
            site = {k: v for k, v in cands.items() if k.startswith("site:")}
            gen = {k: v for k, v in cands.items() if not k.startswith("site:")}
            if site and gen and max(site.values()) < max(gen.values()) * 0.8:
                rule_losses.append({
                    "url": m.get("url"), "site": max(site.values()),
                    "generic": max(gen.values()),
                })

        # Short outliers: pages far below the domain's own norm. The best signal
        # for silent truncation, because it needs no absolute threshold.
        med = statistics.median(lengths) if lengths else 0
        short = sorted(
            ({"url": m.get("url"), "chars": m.get("text_chars", 0)}
             for m in ok if med and m.get("text_chars", 0) < med * 0.35),
            key=lambda x: x["chars"])

        report[dom] = {
            "pages": n,
            "ok": len(ok),
            "ok_rate": pct(len(ok), n),
            "blocked": len(blocked),
            "block_reasons": Counter(
                (m.get("block_reason") or "?").split(":")[-1].strip()
                for m in blocked).most_common(5),
            "suspect": len(suspect),
            "tiers": tiers.most_common(),
            "escalated": expensive,
            "extractors": extractors.most_common(),
            "site_rule_wins": site_rule_wins,
            "site_rule_losses": rule_losses[:5],
            "median_chars": int(med),
            "min_chars": min(lengths) if lengths else 0,
            "max_chars": max(lengths) if lengths else 0,
            "short_outliers": short[:5],
            "median_ms": int(statistics.median(
                [m.get("elapsed_ms", 0) for m in items])) if items else 0,
        }
    return report


def verdicts(dom: str, r: dict) -> list[str]:
    """Plain statements of what needs doing, in priority order."""
    out = []
    n = r["pages"]
    if r["blocked"]:
        top = r["block_reasons"][0][0] if r["block_reasons"] else "?"
        out.append(f"BLOCKED {r['blocked']}/{n} ({top}) -- route this domain "
                   f"straight to the browser tier")
    if r["suspect"]:
        out.append(f"SUSPECT extraction on {r['suspect']}/{n} -- inspect saved "
                   f"HTML before trusting these records")
    if r["short_outliers"]:
        out.append(f"{len(r['short_outliers'])} page(s) far below this domain's "
                   f"median ({r['median_chars']:,} chars) -- likely truncation")
    if r["site_rule_losses"]:
        w = r["site_rule_losses"][0]
        out.append(f"site rule underperforms generic extraction "
                   f"({w['site']:,} vs {w['generic']:,} chars) -- fix or drop it")
    if r["escalated"]:
        out.append(f"{r['escalated']}/{r['ok']} needed an expensive tier -- "
                   f"budget accordingly")
    if not r["site_rule_wins"] and r["ok"] >= 5:
        out.append("no site rule in play; running on generic extraction only")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diagnose a page_harness run")
    ap.add_argument("root", help="directory a run wrote into")
    ap.add_argument("--json", help="also write the full report here")
    ap.add_argument("--problems", action="store_true",
                    help="only show domains with something wrong")
    args = ap.parse_args(argv)

    metas = load(args.root)
    if not metas:
        print(f"no meta.json found under {args.root}")
        return 1
    rep = analyze(metas)

    total = len(metas)
    ok = sum(r["ok"] for r in rep.values())
    blocked = sum(r["blocked"] for r in rep.values())
    suspect = sum(r["suspect"] for r in rep.values())
    print(f"\n{total} pages across {len(rep)} domains  |  "
          f"ok {ok} ({pct(ok, total)})  blocked {blocked}  suspect {suspect}\n")

    for dom, r in sorted(rep.items(), key=lambda kv: (-kv[1]["blocked"],
                                                      -kv[1]["suspect"])):
        v = verdicts(dom, r)
        if args.problems and not v:
            continue
        print(f"{dom}")
        print(f"  {r['pages']} pages | ok {r['ok_rate']} | median "
              f"{r['median_chars']:,} chars (min {r['min_chars']:,} / "
              f"max {r['max_chars']:,}) | {r['median_ms']:,}ms")
        print(f"  tier: {', '.join(f'{k} x{c}' for k, c in r['tiers']) or '-'}")
        print(f"  extractor: {', '.join(f'{k} x{c}' for k, c in r['extractors']) or '-'}")
        for line in v:
            print(f"  -> {line}")
        for s in r["short_outliers"]:
            print(f"     short: {s['chars']:>6,}  {s['url']}")
        print()

    if args.json:
        json.dump(rep, open(args.json, "w", encoding="utf-8"), indent=2)
        print(f"full report -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
