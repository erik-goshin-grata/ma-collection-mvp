#!/usr/bin/env python3
"""Baseline matrix for Funding HC extraction against real article text.

Calls the LIVE funding HC prompt, once per fixture, and prints expected vs actual with
each failure classified. **Never touches the live corpus**: prompt-call logging goes to a
throwaway temp database, and nothing is written to `data/`.

It reproduces the production call exactly — same prompt file, same system prompt, same
model tier, same temperature and token budget as `stages/funding_hc_extract.py` — so a
result here is a result about the pipeline, not about a lookalike.

Failure classification, which is the point of the exercise:

  PROMPT_WORDING      the schema has the right field; the prompt did not steer the model
                      to it. Fixable by rewording.
  SCHEMA_LIMITATION   there is no field that can hold the correct answer, and the
                      correct answer is not "discard". No wording fixes it. No fixture
                      currently qualifies: Chronograph's lower bound was the one case,
                      and the qualified-anchor convention resolved it without a schema
                      change. The branch is kept because the next such case needs it.
  PARSING             the model answered correctly and the value was lost or mangled
                      between response and record.
  DOWNSTREAM_ONLY     extraction is right; the defect is later in the pipeline.

Usage:
    python scripts/run_funding_hc_baseline.py                 # all fixtures
    python scripts/run_funding_hc_baseline.py --only cellares_check_inside_round
    python scripts/run_funding_hc_baseline.py --dry-run       # no model calls
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.funding_hc_baseline_fixtures import (  # noqa: E402
    DISCARDED_CLASSES, ECONOMIC_CLASSES, FIXTURES,
)

_PROMPT_NAME = "funding_hc_extraction"


def _get(payload: dict, path: str):
    """Resolve 'round.size' or 'investors[Name].investment_amount' against a result."""
    if path.startswith("investors["):
        name, _, field = path[len("investors["):].partition("].")
        for inv in payload.get("investors") or []:
            if (inv.get("name") or "").strip().lower() == name.strip().lower():
                return inv.get(field)
        return "<investor absent>"
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict):
            return "<no such path>"
        node = node.get(part)
    return node


def _classify(path: str, expected, actual, fixture: dict) -> str:
    """Name the layer at fault, not just the symptom."""
    if expected == "REPRESENTATION_GAP":
        return ("SCHEMA_LIMITATION — no field can hold the correct answer and discarding "
                "it is not correct either. Reported, never coerced.")
    if path == "round.size" and isinstance(actual, (int, float)):
        for trap, why in fixture["traps"].items():
            if abs(float(actual) - float(trap)) < 0.5:
                # An investor's AUM leaking into round.size is a WORDING failure, not a
                # schema one. The correct handling is to discard it, which needs no field
                # — so adding one would not fix a contamination, and the baseline showed
                # the prompt already discards it.
                return f"PROMPT_WORDING — took {actual:,.0f}: {why}"
    if actual in (None, "<no such path>") and expected not in (None, "REPRESENTATION_GAP"):
        return "PROMPT_WORDING — correct field exists and was left empty"
    if actual == "<investor absent>":
        return "PROMPT_WORDING — the investor was not extracted at all"
    return "PROMPT_WORDING — value differs from expected"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="fixture label(s) to run")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the matrix shape and make no model calls")
    args = ap.parse_args()

    selected = [f for f in FIXTURES if not args.only or f["label"] in args.only]

    print(f"\n{'=' * 78}\nFUNDING HC BASELINE — real article text, verbatim\n{'=' * 78}")
    print(f"  prompt   : {_PROMPT_NAME} (version read from the prompt file at run time)")
    print(f"  fixtures : {len(selected)}")
    print("  corpus   : NOT touched — prompt logging goes to a temp database\n")
    print("  Economic classes and where each belongs in the 0.1 schema:")
    for cls, home in ECONOMIC_CLASSES.items():
        if home:
            print(f"    {cls:<22} {home}")
        else:
            note = ("correctly DISCARDED — not a gap; a field would only be needed if "
                    "AUM itself belonged in the data model"
                    if cls in DISCARDED_CLASSES else "no field")
            print(f"    {cls:<22} {note}")

    if args.dry_run:
        print(f"\n{'-' * 78}\nDRY RUN — no model calls. Fixtures and their traps:\n{'-' * 78}")
        for f in selected:
            print(f"\n  {f['label']}  [{f['v2_event_type']}]")
            for path, val in f["expected"].items():
                shown = "REPRESENTATION_GAP" if val == "REPRESENTATION_GAP" else (
                    f"{val:,.0f}" if isinstance(val, (int, float)) and not isinstance(val, bool)
                    else repr(val))
                print(f"      expect {path:<48} = {shown}")
            for trap, why in f["traps"].items():
                print(f"      TRAP   {trap:>15,.0f}  {why}")
        return 0

    try:
        from config import get_config
        from prompts.base import PromptFailure, call_prompt, load_prompt_file
        from db import get_connection, init_db
        cfg = get_config()
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'!' * 78}")
        print("CANNOT RUN THE BASELINE — no usable configuration:")
        print(f"  {type(exc).__name__}: {exc}")
        print("\nThe matrix needs live model calls. config reads .env from the project")
        print("root via python-dotenv, and real environment variables take precedence.")
        print("Create .env with ANTHROPIC_API_KEY set, then re-run. Use --dry-run to see")
        print("the fixtures and expectations without any model call.")
        print('!' * 78)
        return 2

    prompt = load_prompt_file(_PROMPT_NAME)
    results, failures = [], []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "baseline.db")
        init_db(db_path)
        conn = get_connection(db_path)

        for f in selected:
            title = f["title"].replace("{", "{{").replace("}", "}}")
            body = f["clean_text"].replace("{", "{{").replace("}", "}}")
            user_prompt = prompt["user_template"].format(
                source_type="PR_NEWSWIRE", source_tier="T2",
                v2_event_type=f["v2_event_type"], event_history_type="ANNOUNCED",
                published_date=f["published_date"], title=title, clean_text=body,
            )
            try:
                raw = call_prompt(
                    prompt_name=_PROMPT_NAME,
                    prompt_version=f"{_PROMPT_NAME}:{prompt.get('version', '0.1')}",
                    user_prompt=user_prompt, system_prompt=prompt["system"],
                    model="sonnet", temperature=0.0, max_tokens=2048,
                    cfg=cfg, conn=conn, run_id="funding_hc_baseline", log=None,
                )
            except PromptFailure as exc:
                results.append((f, None, str(exc)))
                continue
            txns = raw.get("transactions") or []
            results.append((f, txns[0] if txns else {}, None))

        conn.close()

    print(f"\n{'=' * 78}\nEXPECTED vs ACTUAL\n{'=' * 78}")
    for f, payload, err in results:
        print(f"\n{'-' * 78}\n  {f['label']}  [{f['v2_event_type']}]")
        print(f"  {f['why']}")
        if err:
            print(f"      PROMPT FAILURE: {err[:200]}")
            failures.append((f["label"], "prompt call", "PARSING — the call itself failed"))
            continue
        for path, expected in f["expected"].items():
            actual = _get(payload, path)
            if expected == "REPRESENTATION_GAP":
                verdict = "REPORTED SEPARATELY"
                ok = None
            else:
                ok = (actual == expected) or (
                    isinstance(expected, (int, float)) and isinstance(actual, (int, float))
                    and abs(float(actual) - float(expected)) < 0.5
                )
                verdict = "OK" if ok else "MISMATCH"
            fmt = lambda v: (f"{v:,.0f}" if isinstance(v, (int, float))
                             and not isinstance(v, bool) else repr(v))
            print(f"      {path:<48} expected {fmt(expected):>16}  "
                  f"actual {fmt(actual):>16}  {verdict}")
            if ok is False:
                cls = _classify(path, expected, actual, f)
                failures.append((f["label"], path, cls))
                print(f"          -> {cls}")
            elif expected == "REPRESENTATION_GAP":
                print(f"          -> actual round.size = {fmt(actual)}; "
                      f"{_classify(path, expected, actual, f)}")

    print(f"\n{'=' * 78}\nFAILURES BY LAYER\n{'=' * 78}")
    if not failures:
        print("  none — every asserted expectation matched.")
    else:
        for layer in ("SCHEMA_LIMITATION", "PROMPT_WORDING", "PARSING", "DOWNSTREAM_ONLY"):
            hits = [f for f in failures if f[2].startswith(layer)]
            if hits:
                print(f"\n  {layer} ({len(hits)})")
                for label, path, cls in hits:
                    print(f"      {label} :: {path}")
                    print(f"          {cls}")
    print("\nNo prompt or schema change is proposed from this run. Baseline first.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
