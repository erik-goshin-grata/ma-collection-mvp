#!/usr/bin/env python3
"""A stated percentage is captured, 100 included; a silent source stays null.

WHAT WENT WRONG

The extraction contract instructed the opposite of evidence-only:

    "pct_acquired: Percentage of target being acquired. Null if 100% or unstated.
     Extract for minority investments and partial acquisitions only. Do not
     extract 100 -- leave null for full acquisitions."

Two different facts collapsed into one null. A source stating the whole company
changed hands, and a source never saying how much of it did, produced identical
rows -- and the field that should have told them apart was the one being emptied.

Aggregation then compensated. `_resolve_pct_acquired` assumed 100 for control event
types when the field was silent, and that assumption flowed into
`implied_equity_value`, `implied_enterprise_value` and the calculated multiples,
indistinguishable from a stated fact once it arrived.

WHAT CHANGED HERE

Only the capture contract. A stated percentage is extracted, and 100 is a stated
percentage like any other. A silent source stays null, because null means "the
source did not say" and downstream reads it that way.

WHAT THIS SLICE DOES NOT DO

It does not remove the assumption -- that is the next commit, and the order matters:
removing it first would regress every genuine 100% deal into looking exactly like a
silent one. This commit is the precondition, not the fix.

THE TRAP THIS FILE PINS

"Wholly owned subsidiary" describes ownership AFTER a deal, not the size of the stake
bought in it. Where a prior stake is in play, that phrase is consistent with acquiring
the remainder and is not evidence of 100. The prompt's own worked example -- prior 80%,
acquires the remaining 20%, becomes wholly owned -- is exactly this case, and its
answer is 20.

Run from project root:
    python scripts/test_pct_acquired_evidence_only.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts.base import load_prompt_file  # noqa: E402
import stages.high_confidence_extract as hc  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(text: str) -> str:
    """Whitespace-normalized, so a line wrap cannot fail a phrase check."""
    return re.sub(r"\s+", " ", text)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    system = load_prompt_file("high_confidence_extraction")["system"]
    flat = _norm(system)

    # Section 4 is the only text load_prompt_file delivers. Asserted on the DELIVERED
    # string, never on the file -- an instruction in section 6 reaches no model.
    print("\nThe old prohibition is gone from the delivered contract:")
    check("no 'do not extract 100'", "Do not extract 100" in flat, False)
    check("no 'Null if 100% or unstated'", "Null if 100% or unstated" in flat, False)
    check("no 'minority investments and partial acquisitions only'",
          "partial acquisitions only" in flat, False)

    print("\nA stated 100 is captured:")
    check("evidence-only rule delivered",
          "EVIDENCE ONLY, AND THAT INCLUDES 100" in flat, True)
    for phrase in ('"100% of"', '"all of the outstanding shares"',
                   '"the entire issued share capital"'):
        check(f"equivalent wording {phrase} named", phrase in flat, True)

    print("\nA silent source is null, not 100:")
    check("rule delivered", "A SILENT SOURCE IS NULL, NOT 100" in flat, True)
    check("null is defined as 'the source did not say'",
          "Null means \"the source did not say\"" in flat, True)
    # The specific failure mode: filling the field with the likeliest answer.
    check("guessing is named and refused",
          "is a guess, not a reading of the text" in flat, True)

    print("\nThe post-transaction trap is pinned:")
    check("trap rule delivered", "THAT EXAMPLE IS ALSO THE TRAP FOR 100" in flat, True)
    check("'wholly owned' is identified as after-the-deal ownership",
          "describes ownership AFTER the deal" in flat, True)
    check("a prior stake defeats a whole-ownership phrase",
          "consistent with acquiring the remainder" in flat, True)

    print("\nThe prior-ownership rule is unchanged:")
    check("remaining X% still extracts X",
          'acquires the "remaining X%," extract pct_acquired = X' in flat, True)
    check("resulting ownership still not substituted",
          "Do not substitute resulting ownership" in flat, True)
    check("the 80/20 worked example survives",
          "pct_acquired = 20" in flat, True)

    print("\nThe response slot is unchanged and still present:")
    check("deal.pct_acquired has a slot", '"pct_acquired": null' in flat, True)

    print("\nCapture came first, and the assumption is now gone:")
    # While this slice stood alone it asserted the OPPOSITE -- that the assumption was
    # still present -- so the file could not be misread as having landed the fix. The
    # ordering it protected has now happened: capture (HC 0.32) then removal
    # (aggregation 0.11). Asserting the end state is the stronger claim.
    agg_src = (ROOT / "stages" / "aggregate.py").read_text(encoding="utf-8")
    check("no 100 is assumed anywhere in aggregation",
          '100.0, "assumed"' in agg_src, False)
    check("and the capture rule this depended on is in place",
          "EVIDENCE ONLY, AND THAT INCLUDES 100" in flat, True)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check("versioning table carries the 0.32 row",
          bool(re.search(r"^\| 0\.32 \|", md, re.M)), True)
    check("prompt declares a version", bool(declared), True)
    if declared:
        # Floor, not a pin: later slices legitimately bump this prompt, and the
        # 0.32 row asserted above is what fixes this rule's provenance.
        check("prompt is at or past 0.32",
              tuple(int(x) for x in declared.group(1).split(".")) >= (0, 32), True)
        check("stage _VERSION agrees", hc._VERSION, declared.group(1))

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — a stated percentage is captured; a silent source stays null.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
