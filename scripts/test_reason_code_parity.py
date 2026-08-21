"""
scripts/test_reason_code_parity.py — drift guard for the relevancy prompt/stage pair.

Asserts parity between stages/relevancy_filter.py and the reason_code vocabulary the
model is ACTUALLY SENT -- load_prompt_file(...)["system"], not the Markdown file.

That distinction is the whole point. Until 0.8 this test parsed a REASON_CODES_START /
REASON_CODES_END block that lived in §6, and load_prompt_file only ever delivers §4 and
§5. The test passed on 24 == 24 for the prompt's entire history while the model was
shown none of them in an authoritative list: ten codes never reached it at all, and the
prompt told it the codes were "listed in the in-scope and out-of-scope enumerations
above" -- prose category descriptions containing no enum values. A test that certifies
content the model never sees proves nothing about behaviour, so this one reads the
delivered string.

It also checks the direction that actually matters. The old test asserted
prompt-declared codes were covered by the stage; it never asserted that stage-valid
codes were delivered to the prompt, which is precisely the gap the undelivered ten sat
in.

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

from prompts.base import load_prompt_file
from stages.relevancy_filter import (
    _REASON_CODE_ALIASES, _VALID_REASON_CODES, _VERSION,
)

PROMPT = os.path.join(os.path.dirname(__file__), "..", "prompts", "relevancy_filter.md")
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

# Scoped to the two REASON CODES lists. The negative lookahead is load-bearing: without
# it the terminator fires on the second REASON CODES heading and captures only the
# RELEVANT side, which reports a false failure against a correct prompt. Scoping also
# keeps the "invented values that must NOT be produced" list out -- its entries have the
# identical `- CODE` shape, so an unscoped parse would assert the stage must cover
# SHARE_BUYBACK and friends. Assertion 4 pins that.
_SECTION_RE = re.compile(
    r"REASON CODES — RELEVANT side:(.*?)(?=\n(?!REASON CODES)[A-Z][A-Z ]{3,}|\Z)", re.S
)
# Digits belong in the class even though no code uses one today: the same regex written
# without them silently skipped v2_event_type in an earlier test on this codebase.
_CODE_RE = re.compile(r"^-\s+([A-Z][A-Z0-9_]+)", re.M)


def delivered_section() -> str:
    """The REASON CODES region of the system prompt the model is actually sent."""
    system = load_prompt_file("relevancy_filter")["system"]
    m = _SECTION_RE.search(system)
    if not m:
        print(f"{FAIL} — no 'REASON CODES — RELEVANT side:' section in the DELIVERED "
              f"system prompt. The vocabulary must live in §4; §6 is not sent.")
        sys.exit(1)
    return m.group(1)


def prompt_reason_codes() -> set[str]:
    return set(_CODE_RE.findall(delivered_section()))


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

    # 3. THE DIRECTION THAT MATTERS: every stage-valid code is actually DELIVERED.
    #    This was informational before 0.8 and is why ten undelivered codes survived --
    #    a code the model is never shown can only be reached by guessing, and eight of
    #    the ten had no alias path, so they folded into the catch-alls instead.
    undelivered = sorted(c for c in _VALID_REASON_CODES if c not in declared)
    if undelivered:
        ok = False
        print(f"{FAIL} — {len(undelivered)} of {len(_VALID_REASON_CODES)} valid codes are "
              f"never delivered to the model: {undelivered}")
    else:
        print(f"{PASS} — all {len(_VALID_REASON_CODES)} valid codes are delivered to the model")

    # 3b. The prompt's own stated count must match what it delivers. Both "24" claims are
    #     scanned, so a vocabulary change that updates one and misses the other fails here
    #     rather than shipping a prompt that misstates its own contract.
    system = load_prompt_file("relevancy_filter")["system"]
    claims = re.findall(r"exactly one of the (\d+) values", system) + \
             re.findall(r"one of the (\d+) enum values", system)
    if not claims:
        ok = False
        print(f"{FAIL} — the delivered prompt no longer states how many codes it lists")
    for n in claims:
        if int(n) != len(declared):
            ok = False
            print(f"{FAIL} — prompt claims {n} values are listed; {len(declared)} are "
                  f"actually delivered")

    # 3c. Side assignments, asserted on delivered content. Moved here from
    #     test_pipe_recognition.py, which anchored on the undelivered §6 block: marking
    #     PIPE NOT_RELEVANT drops the row before Stage 3 and destroys the recognized-
    #     exclusion record the RELEVANT assignment exists to preserve.
    section = delivered_section()
    relevant_side, _, not_relevant_side = section.partition("NOT_RELEVANT side")
    if "PIPE" not in relevant_side:
        ok = False
        print(f"{FAIL} — PIPE is not on the delivered RELEVANT side")
    if "PIPE" in not_relevant_side:
        ok = False
        print(f"{FAIL} — PIPE is on the delivered NOT_RELEVANT side; that drops the row "
              f"before Stage 3 and loses the recognized-exclusion record entirely")
    if not (relevant_side.strip() and not_relevant_side.strip()):
        ok = False
        print(f"{FAIL} — the delivered section no longer has two populated sides")

    # 4. CONTROL: the invented-value correction list must never be read as declared
    #    vocabulary. Its entries share the `- CODE` shape, so this pins the scoping.
    if "SHARE_BUYBACK" in declared:
        ok = False
        print(f"{FAIL} — CONTROL BROKEN: the invented-values list was parsed as declared "
              f"vocabulary; the section scoping has stopped working")
    else:
        print(f"{PASS} — invented-value examples are not read as declared vocabulary")

    # 5. VERSION parity. The stage stamps every row with `relevancy_filter:<_VERSION>`,
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
