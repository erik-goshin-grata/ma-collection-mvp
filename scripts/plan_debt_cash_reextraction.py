#!/usr/bin/env python3
"""Scope and cost a Path B debt/cash re-extraction. READ ONLY.

Run this BEFORE any re-extraction. It measures the actual corpus rather than
estimating from assumptions: how many sources are candidates, how large they are,
and what the model spend would be.

**Bounding is the point.** Re-extracting every source pays full model cost on
articles that never mention a balance sheet. A deterministic keyword pre-scan over
`clean_text` costs nothing and typically cuts the candidate set to a fraction of the
corpus. The scan is deliberately over-inclusive — it is a cost bound, not a
classifier, and a source it keeps may still yield no debt or cash.

Estimates are labelled as estimates. Token counts here are character-based
approximations, not tokenizer output; treat the cost figure as an order of magnitude
for a go/no-go decision, not a budget line. The prompt is sent per source, so its
size is multiplied by the candidate count — that term usually dominates.

Writes nothing, re-derives nothing, and opens the database read-only.

Usage:
    python scripts/plan_debt_cash_reextraction.py --db data/ma_mvp.db
    python scripts/plan_debt_cash_reextraction.py --db data/ma_mvp.db --list-candidates
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

# Sonnet-tier list price per million tokens (claude-sonnet-4-6, the tier
# stages/high_confidence_extract.py routes to via model="sonnet").
INPUT_PER_MTOK = 3.00
OUTPUT_PER_MTOK = 15.00

# Rough characters-per-token for English prose. Only used for an order-of-magnitude
# estimate; real counts come from the tokenizer.
CHARS_PER_TOKEN = 3.7

# Typical HC output size per source. The extraction returns a bounded JSON object,
# so this varies far less than the input does.
EST_OUTPUT_TOKENS_PER_SOURCE = 900

# Deliberately over-inclusive. A source kept here may still yield nothing; a source
# dropped here would have yielded nothing anyway.
BALANCE_SHEET_PATTERNS = (
    r"\btotal debt\b", r"\bnet debt\b", r"\bgross debt\b", r"\bindebtedness\b",
    r"\bcash and cash equivalents\b", r"\bcash equivalents\b",
    r"\bshort[- ]term investments\b", r"\bmarketable securities\b",
    r"\bcash on hand\b", r"\bcash balance\b",
    r"\bdebt[- ]free\b", r"\bcash[- ]free\b",
    r"\bbalance sheet\b", r"\bleverage\b", r"\bborrowings\b",
    r"\boutstanding debt\b", r"\bterm loan\b", r"\bcredit facility\b",
    r"\bassumed debt\b", r"\bassumption of debt\b",
)
_SCAN = re.compile("|".join(BALANCE_SHEET_PATTERNS), re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--list-candidates", action="store_true")
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"Database not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id, se.status, se.hc_prompt_version,
               se.target_name, se.acquirer_name, se.total_debt, se.cash_st,
               sr.clean_text, sr.url
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        """
    ).fetchall()

    total = len(rows)
    with_text = [r for r in rows if r["clean_text"]]
    candidates = [r for r in with_text if _SCAN.search(r["clean_text"])]
    already = [r for r in rows if r["total_debt"] is not None or r["cash_st"] is not None]
    versions: dict = {}
    for r in rows:
        versions[r["hc_prompt_version"]] = versions.get(r["hc_prompt_version"], 0) + 1

    prompt_path = Path("prompts/high_confidence_extraction.md")
    prompt_chars = len(prompt_path.read_text(encoding="utf-8")) if prompt_path.is_file() else 0
    prompt_tokens = int(prompt_chars / CHARS_PER_TOKEN)

    def _cost(subset) -> tuple[int, int, float]:
        body_tokens = sum(int(len(r["clean_text"]) / CHARS_PER_TOKEN) for r in subset)
        in_tokens = body_tokens + prompt_tokens * len(subset)
        out_tokens = EST_OUTPUT_TOKENS_PER_SOURCE * len(subset)
        dollars = (in_tokens / 1e6) * INPUT_PER_MTOK + (out_tokens / 1e6) * OUTPUT_PER_MTOK
        return in_tokens, out_tokens, dollars

    print(f"Database: {args.db}")
    print(f"  staging_extraction rows:                  {total}")
    print(f"    with usable clean_text:                 {len(with_text)}")
    print(f"    already carry total_debt or cash_st:    {len(already)}")
    print("\n  hc_prompt_version distribution:")
    for version, n in sorted(versions.items(), key=lambda kv: -kv[1]):
        marker = "  <- balance-sheet capable" if version and version >= "0.17" else ""
        print(f"    {version!r}: {n}{marker}")

    print(f"\n  Balance-sheet keyword candidates:         {len(candidates)}"
          f"  ({len(candidates) / len(with_text) * 100:.0f}% of usable)" if with_text else "")

    print(f"\n  Prompt size (sent once per source):       ~{prompt_tokens:,} tokens")
    print("\n  ESTIMATED model spend (order of magnitude, char-based approximation):")
    for label, subset in (("bounded — keyword candidates", candidates),
                          ("unbounded — every usable source", with_text)):
        in_tok, out_tok, dollars = _cost(subset)
        print(f"    {label:34s} n={len(subset):4d}  "
              f"in≈{in_tok/1e6:.2f}M  out≈{out_tok/1e6:.2f}M  ≈${dollars:,.2f}")

    print("\n  Rates used: claude-sonnet-4-6 list price, "
          f"${INPUT_PER_MTOK:.2f}/MTok in, ${OUTPUT_PER_MTOK:.2f}/MTok out.")
    print("  Excludes downstream stages (LC, clustering, aggregation) and any retries.")
    print("  Prompt caching is not modelled; a cached prompt prefix would cut the")
    print("  per-source prompt term substantially on a sequential run.")

    if args.list_candidates and candidates:
        print("\n  Candidate sources (largest first):")
        for r in sorted(candidates, key=lambda r: -len(r["clean_text"]))[:args.limit]:
            hits = sorted({m.group(0).lower() for m in _SCAN.finditer(r["clean_text"])})
            print(f"    eid={r['extraction_id']:<5} src={r['source_raw_id']:<5} "
                  f"{(r['target_name'] or '?')[:28]:28s} "
                  f"{len(r['clean_text']):>7,}ch  {', '.join(hits[:4])}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
