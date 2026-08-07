"""
scripts/test_reason_code_parity.py — drift guard for relevancy reason codes (bug #7).

Parses the authoritative reason_code enum block in prompts/relevancy_filter.md
(between the REASON_CODES_START / REASON_CODES_END markers) and asserts that every
code the prompt declares is covered by stages/relevancy_filter.py — either directly
in _VALID_REASON_CODES or reachable as an alias target. Also checks that every alias
target is itself a valid code.

This stops the next prompt bump from silently reintroducing the drift that made the
stage squash VC_ROUND_OR_FUNDING / RECAPITALIZATION to AMBIGUOUS_BUT_LIKELY_DEAL.

Run from project root:
    python scripts/test_reason_code_parity.py
Exit code 0 = parity holds; 1 = drift.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stages.relevancy_filter import _VALID_REASON_CODES, _REASON_CODE_ALIASES

PROMPT = os.path.join(os.path.dirname(__file__), "..", "prompts", "relevancy_filter.md")
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

_BLOCK_RE = re.compile(r"REASON_CODES_START(.*?)REASON_CODES_END", re.S)
_CODE_RE = re.compile(r"^-\s+`([A-Z][A-Z0-9_]+)`", re.M)


def prompt_reason_codes() -> set[str]:
    text = open(PROMPT, encoding="utf-8").read()
    m = _BLOCK_RE.search(text)
    if not m:
        print(f"{FAIL} — REASON_CODES_START/END markers not found in {PROMPT}")
        sys.exit(1)
    return set(_CODE_RE.findall(m.group(1)))


def main() -> int:
    declared = prompt_reason_codes()
    if not declared:
        print(f"{FAIL} — no reason codes parsed from the demarcated block")
        return 1

    alias_targets = set(_REASON_CODE_ALIASES.values())
    covered = _VALID_REASON_CODES | alias_targets

    ok = True

    # 1. every prompt-declared code is covered
    missing = sorted(c for c in declared if c not in covered)
    if missing:
        ok = False
        print(f"{FAIL} — prompt declares codes the stage doesn't cover: {missing}")
    else:
        print(f"{PASS} — all {len(declared)} prompt-declared codes are covered by the stage")

    # 2. every alias target resolves to a valid code
    bad_targets = sorted(t for t in alias_targets if t not in _VALID_REASON_CODES)
    if bad_targets:
        ok = False
        print(f"{FAIL} — alias targets not in _VALID_REASON_CODES: {bad_targets}")
    else:
        print(f"{PASS} — all {len(alias_targets)} alias targets are valid codes")

    # 3. informational: stage codes the prompt no longer declares (not a failure)
    extra = sorted(c for c in _VALID_REASON_CODES if c not in declared)
    if extra:
        print(f"  (info) stage accepts codes not in the prompt block: {extra}")

    print(("\n" + PASS + " parity holds") if ok else ("\n" + FAIL + " drift detected"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
