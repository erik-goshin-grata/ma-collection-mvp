"""
scripts/test_prompt_stage_version_parity.py — generic prompt ↔ stage version parity.

Every prompt-bearing stage stamps the rows it produces with `<prompt_name>:<_VERSION>`
and registers that string in `prompt_version` **against the SHA-256 of the file it
actually loaded**. When the constant and the file disagree, the stored provenance is not
merely incomplete — it is wrong in a way nothing downstream can detect: `prompt_version`
maps a version that never produced the text to the text that did, so "which prompt
produced this row?" returns a confident wrong answer.

Five of eight prompt-bearing stages were drifted when this file was written:

    aggregation                 0.3 stamped / 0.4 on disk
    deal_summary                0.8 / 0.9
    high_confidence_extraction  0.17 / 0.18
    low_confidence_extraction   0.3 / 0.5
    strategic_rationale         0.4 / 0.5

`scripts/test_reason_code_parity.py` already guarded this for `relevancy_filter`, which
is exactly why relevancy was the one pair that had not drifted. This file generalizes
that guard so a new stage or prompt is covered without anyone editing a test.

WHY IT CHECKS BOTH DIRECTIONS
-----------------------------
A one-directional test (walk the stages, check their prompts) silently skips any prompt
no stage claims — which is how an orphaned prompt file stays orphaned. So this walks
both sides and additionally asserts that every declared orphan is *still* an orphan:
if a stage later starts loading `funding_lc_extraction`, the allowlist entry becomes
stale and the test fails until it is removed. The allowlist cannot quietly become a
permanent escape hatch, because carrying an entry that no longer applies is itself a
failure.

TWO STAGE PATTERNS
------------------
Most stages own one prompt via `_PROMPT_NAME` + `_VERSION`. `agreement_extract.py` owns
five via a `_VERSIONS` dict. A test that understood only the first pattern would skip
five prompts while reporting success, so both are parsed.

Run from project root:
    python scripts/test_prompt_stage_version_parity.py
Exit code 0 = parity holds; 1 = drift.
"""

import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROMPTS_DIR = os.path.join(ROOT, "prompts")
STAGES_DIR = os.path.join(ROOT, "stages")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

# Prompt files with no owning stage. Each entry MUST carry a reason, and each is
# re-verified below: an entry that is no longer an orphan fails the test.
ALLOWED_ORPHANS = {
    "prompt_conventions": (
        "Shared authoring conventions, not a callable prompt — it has no System Prompt "
        "or User Prompt Template section for load_prompt_file() to read."
    ),
    "funding_lc_extraction": (
        "Written 2026-07-28; the stage that would load it has never been built. "
        "Recorded as 'stage code not yet written' in docs/project_state.md."
    ),
}

_PROMPT_VERSION_RE = re.compile(r"^\*\*Version:\*\*\s*([0-9]+(?:\.[0-9]+)*)", re.M)
_SCALAR_NAME_RE = re.compile(r"^_PROMPT_NAME\s*=\s*[\"']([^\"']+)[\"']", re.M)
_SCALAR_VERSION_RE = re.compile(r"^_VERSION\s*=\s*[\"']([^\"']+)[\"']", re.M)
_VERSIONS_BLOCK_RE = re.compile(r"^_VERSIONS\s*=\s*\{(.*?)\}", re.M | re.S)
_VERSIONS_ENTRY_RE = re.compile(r"[\"'](\w+)[\"']\s*:\s*[\"']([0-9]+(?:\.[0-9]+)*)[\"']")


def prompt_versions() -> dict:
    """{prompt_name: version} for every prompts/*.md declaring a version line."""
    out = {}
    for fname in sorted(os.listdir(PROMPTS_DIR)):
        if not fname.endswith(".md"):
            continue
        name = fname[:-3]
        text = open(os.path.join(PROMPTS_DIR, fname), encoding="utf-8").read()
        m = _PROMPT_VERSION_RE.search(text)
        out[name] = m.group(1) if m else None
    return out


def stage_stamps() -> dict:
    """{prompt_name: (version, stage_filename)} for every prompt a stage claims."""
    out = {}
    for fname in sorted(os.listdir(STAGES_DIR)):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        text = open(os.path.join(STAGES_DIR, fname), encoding="utf-8").read()

        name_m, ver_m = _SCALAR_NAME_RE.search(text), _SCALAR_VERSION_RE.search(text)
        if name_m and ver_m:
            out[name_m.group(1)] = (ver_m.group(1), fname)

        block = _VERSIONS_BLOCK_RE.search(text)
        if block:
            for pname, pver in _VERSIONS_ENTRY_RE.findall(block.group(1)):
                out[pname] = (pver, fname)
    return out


def main() -> int:
    ok = True
    prompts, stamps = prompt_versions(), stage_stamps()

    # The full table, always — five simultaneous mismatches is precisely the case a
    # fail-fast test hides, and the shape of the drift is the useful signal.
    rows, mismatches = [], []
    for name in sorted(set(prompts) | set(stamps)):
        on_disk = prompts.get(name)
        stamped, stage = stamps.get(name, (None, None))
        if name not in prompts:
            status = "*** NO PROMPT FILE ***"
        elif on_disk is None:
            status = "*** NO VERSION LINE ***"
        elif name not in stamps:
            status = "orphan (allowed)" if name in ALLOWED_ORPHANS else "*** NO OWNING STAGE ***"
        elif stamped != on_disk:
            status = "*** MISMATCH ***"
        else:
            status = "ok"
        if status.startswith("***"):
            mismatches.append(name)
        rows.append((name, on_disk or "—", stamped or "—", stage or "—", status))

    width = max(len(r[0]) for r in rows) + 2
    print(f"{'prompt':<{width}}{'file':<9}{'stamped':<9}{'stage':<28}status")
    for name, on_disk, stamped, stage, status in rows:
        print(f"{name:<{width}}{on_disk:<9}{stamped:<9}{stage:<28}{status}")
    print()

    if mismatches:
        ok = False
        print(f"{FAIL} — {len(mismatches)} prompt(s) not in parity: {', '.join(mismatches)}")
    else:
        owned = sum(1 for n in prompts if n in stamps)
        print(f"{PASS} — {owned} owned prompt(s) in parity, "
              f"{len(ALLOWED_ORPHANS)} declared orphan(s)")

    # An allowlist entry that no longer applies is a failure, not a courtesy. This is
    # what stops ALLOWED_ORPHANS from becoming a place drift goes to be forgotten.
    stale = sorted(n for n in ALLOWED_ORPHANS if n in stamps)
    if stale:
        ok = False
        for n in stale:
            print(f"{FAIL} — {n!r} is in ALLOWED_ORPHANS but is now owned by "
                  f"{stamps[n][1]}; remove the exception")
    missing_file = sorted(n for n in ALLOWED_ORPHANS if n not in prompts)
    if missing_file:
        ok = False
        for n in missing_file:
            print(f"{FAIL} — {n!r} is in ALLOWED_ORPHANS but prompts/{n}.md no longer "
                  f"exists; remove the exception")
    if ok and ALLOWED_ORPHANS:
        print("\nDeclared orphans — each exempt for a stated reason:")
        for n, reason in sorted(ALLOWED_ORPHANS.items()):
            print(f"  {n}: {reason}")

    print(("\n" + PASS + " parity holds") if ok else ("\n" + FAIL + " drift detected"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
