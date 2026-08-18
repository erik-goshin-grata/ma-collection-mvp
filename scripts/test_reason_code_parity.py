"""
scripts/test_reason_code_parity.py — drift guard for the relevancy prompt/stage pair.

Parses the authoritative reason_code enum block in prompts/relevancy_filter.md
(between the REASON_CODES_START / REASON_CODES_END markers) and asserts that every
code the prompt declares is covered by stages/relevancy_filter.py — either directly
in _VALID_REASON_CODES or reachable as an alias target. Also checks that every alias
target is itself a valid code.

This stops the next prompt bump from silently reintroducing the drift that made the
stage squash VC_ROUND_OR_FUNDING / RECAPITALIZATION to AMBIGUOUS_BUT_LIKELY_DEAL.

Also asserts VERSION parity. The prompt file sat at 0.5 while the stage stamped
`relevancy_filter:0.4` for weeks, so every row carried a version string naming a prompt
that was not the one it was classified by — which makes the provenance actively
misleading rather than merely incomplete, and silently defeats any "which prompt
produced this?" query. Enum parity was guarded here from the start; version parity was
not, which is exactly why this one survived.

Run from project root:
    python scripts/test_reason_code_parity.py
Exit code 0 = parity holds; 1 = drift.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from stages.relevancy_filter import (
    _REASON_CODE_ALIASES, _VALID_REASON_CODES, _VERSION,
)

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


_VERSION_RE = re.compile(r"^\*\*Version:\*\*\s*([0-9][0-9.]*)", re.M)


def prompt_version() -> str | None:
    """The version the prompt file declares for itself."""
    m = _VERSION_RE.search(open(PROMPT, encoding="utf-8").read())
    return m.group(1) if m else None


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

    # 4. VERSION parity. The stage stamps every row with `relevancy_filter:<_VERSION>`,
    #    so a stamp that names a different prompt than the one in the file is worse than
    #    no provenance at all: it answers "which prompt produced this row?" wrongly, and
    #    nothing downstream can tell. Shipping a prompt edit without moving the stamp is
    #    the specific mistake this catches.
    declared_version = prompt_version()
    if declared_version is None:
        ok = False
        print(f"{FAIL} — no '**Version:** X.Y' line found in {PROMPT}")
    elif declared_version != _VERSION:
        ok = False
        print(f"{FAIL} — prompt file is version {declared_version} but the stage stamps "
              f"relevancy_filter:{_VERSION}; every row would carry a version string "
              f"naming a prompt it was not classified by")
    else:
        print(f"{PASS} — prompt and stage agree on version {_VERSION}")

    print(("\n" + PASS + " parity holds") if ok else ("\n" + FAIL + " drift detected"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
