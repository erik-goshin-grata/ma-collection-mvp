#!/usr/bin/env python3
"""HC 0.26 — currency representations of one economic fact are ONE observation.

WHAT WENT WRONG

A renewables article stated the same deal two ways: "EUR 850 million transaction"
in its summary line, then "Transaction value is approximately $1 billion USD
(approximately EUR 850 million)" in the body. HC 0.25 emitted TWO
value_observations, both typed TRANSACTION_VALUE:

    [0] 850000000   EUR
    [1] 1000000000  USD

That was the contract's instructed reading, not a model slip. The block said
"Return one item per distinct deal-value fact, even when two facts have the same
numeric amount" and "Do not collapse facts merely because amount, currency, or
source are identical" -- and said nothing at all about currency equivalents. The
model's own note shows it knew: "the source's own stated USD equivalent of EUR
850 million... captured as a second value observation".

WHY A DUPLICATE IS EXPENSIVE

The observation writer decomposes each array item into independent value_amount,
value_currency and value_type observations. Two representations therefore put a
EUR amount and a USD currency into the SAME two candidate pools, and Stage 9
resolves those pools field by field. On this transaction it took the amount from
the EUR fact and the currency from the USD fact and wrote 850,000,000 USD -- a
monetary pair no source ever stated.

Two other transactions in the same 30-deal run had the identical authoring shape
and survived only because their EUR evidence outvoted their USD evidence. Nothing
structural protected them.

THE RULE, AND THE PART THAT IS EASY TO GET BACKWARDS

Retain the representation the source presents as primary or headline, judged
across the whole source. This case is exactly why "whole source" matters: the
body sentence puts USD first with EUR in parentheses, so a naive keep-the-
non-parenthetical rule picks USD -- the wrong answer, and the opposite of what
the model itself concluded. The summary line states EUR plainly and first.

Failing a clear primary, keep the first clearly stated representation. Never
choose from geography, party nationality, transaction location, or an assumed
"natural" currency for the deal. That last one is not hypothetical: it is the
reasoning Stage 9 used when it picked USD.

WHAT MUST NOT REGRESS

The rule this narrows is load-bearing. Genuinely distinct economic facts stay
separate observations even in one sentence with similar amounts -- a
consideration and an enterprise value are two facts. The distinguishing test is
whether converting one figure into the other's currency would produce the other.
Those controls are pinned as hard as the new rule.

LAYER

Delivered contract only -- every assertion reads load_prompt_file(...)["system"],
never the Markdown, so a rule that drifts outside the section 4 fence fails here.
No code path decides how many value observations a source yields.

Run from project root:
    python scripts/test_value_currency_representation.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts.base import load_prompt_file  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_version_floor(md: str, stage_version: str, introduced: str) -> None:
    """Pin the rule's provenance without freezing the prompt at one version.

    A rule guard that asserts the CURRENT version equals the version that
    introduced it fails on the next unrelated bump, which is a false alarm about
    a rule that is still delivered. What actually matters is: the versioning row
    recording this rule still exists, the prompt is at or beyond it, and the
    stage agrees with the prompt.
    """
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check(f"versioning table still carries the {introduced} row",
          bool(re.search(rf"^\| {re.escape(introduced)} \|", md, re.M)), True)
    check("prompt declares a version", bool(declared), True)
    if not declared:
        return
    check(f"prompt version >= {introduced} (currently {declared.group(1)})",
          _version_tuple(declared.group(1)) >= _version_tuple(introduced), True)
    check("stage _VERSION agrees with the prompt", stage_version, declared.group(1))


def main() -> None:
    print(__doc__.strip().split("\n")[0])
    prompt = load_prompt_file("high_confidence_extraction")
    system = prompt["system"]
    # Phrase pins run against a whitespace-normalized view: re-wrapping a
    # paragraph is formatting, not a contract change. Section scoping uses `system`.
    flat = re.sub(r"\s+", " ", system)

    # Slice the block defensively so a pre-change run reports every missing rule
    # rather than dying on the first assertion.
    marker = "ONE ECONOMIC FACT, ONE OBSERVATION"
    if marker in system:
        block = system[system.index(marker):].split("\n- ENTERPRISE_VALUE")[0]
    else:
        block = ""
    block_flat = re.sub(r"\s+", " ", block)

    print("\nThe rule is delivered, inside the value_observations block:")
    check("block present", marker in system, True)
    check("block sits inside value_observations, before the ENTERPRISE_VALUE bullet",
          bool(block) and system.index("value_observations:") < system.index(marker)
          if marker in system else False, True)
    check("states one fact yields one observation",
          "Emit ONE observation for it" in block_flat, True)
    check("names currency representations as the case",
          "same economic value in more than one currency" in block_flat, True)
    check("worked example of the collapse",
          "is one transaction value, not two" in block_flat, True)

    print("\nAn equivalent is an alternate representation, not an observation:")
    check("ALTERNATE REPRESENTATION named", "ALTERNATE REPRESENTATION" in block, True)
    for phrase, label in (
        ("equivalent, conversion, translated amount", "the four equivalent forms named"),
        ("or about", "'or about' recognized as an equivalent marker"),
        ("equivalent to", "'equivalent to' recognized"),
        ("approximately X (approximately Y)", "nested-approximation shape recognized"),
        ("It is not another structured observation", "explicitly not a second observation"),
    ):
        check(label, phrase in block_flat, True)

    print("\nRetention rule — primary first, then order of statement:")
    check("retain the primary/headline representation",
          "primary or headline value for that fact" in block_flat, True)
    check("judged across the WHOLE source, not one sentence",
          "WHOLE source rather than one sentence" in block_flat, True)
    # The Enel trap: a later sentence restates the figure with a conversion in
    # front of it. Without this clause the rule picks the wrong currency.
    check("headline beats a later restatement with a conversion in front",
          "primary even if a later sentence restates it with a conversion" in block_flat,
          True)
    check("fallback is the FIRST clearly stated representation",
          "FIRST clearly stated representation" in block_flat, True)

    print("\nForbidden bases for the choice — all four, pinned separately:")
    for phrase, label in (
        ("geography", "geography excluded"),
        ("party nationality", "party nationality excluded"),
        ("transaction location", "transaction location excluded"),
        ("natural\" currency", "assumed 'natural' currency excluded"),
    ):
        check(label, phrase in block_flat, True)
    check("stated as NEVER, not a preference", "NEVER choose" in block, True)
    check("says these are not statements about denomination",
          "not statements about how the value was denominated" in block_flat, True)

    print("\nThe alternate figure is kept, just not structured:")
    check("kept in evidence or notes",
          "evidence phrase or in notes" in block_flat, True)
    check("no array item for it", "Do not give it its own array item" in block_flat, True)
    check("no amount or currency for it anywhere",
          "own amount or currency anywhere in the response" in block_flat, True)

    # ------------------------------------------------------------------
    # CONTROLS. The narrowed rule must not swallow genuinely distinct facts.
    print("\nDistinct-fact controls (must not regress):")
    check("do-not-collapse rule still delivered",
          "Do not collapse facts merely because amount, currency, or source are identical"
          in flat, True)
    check("EV vs net-sales example still delivered",
          "$210 million enterprise value" in flat and "$210 million 2025 net sales" in flat,
          True)
    check("one-item-per-distinct-fact rule still delivered",
          "Return one item per distinct deal-value fact" in flat, True)
    check("same-sentence, same-amount facts still stay separate",
          "even when two facts have the same numeric amount" in flat, True)
    check("new block says it does NOT relax the old rule",
          "This does NOT relax the rule above" in block, True)
    check("consideration vs enterprise value named as staying separate",
          "consideration and an enterprise value are two facts" in block_flat, True)
    check("a decidable test is given, not a vibe",
          "converting one into the other's currency would produce the other"
          in block_flat, True)

    print("\nSurrounding value_observations contract untouched:")
    for phrase, label in (
        ("The `value_observations` key is required on every transaction element",
         "key still required on every element"),
        ("Return an empty array", "empty-array instruction intact"),
        ("Keep the legacy value object populated with the primary/most transaction-",
         "legacy value object rule intact"),
        ("do not create or imply a separate canonical", "ENTERPRISE_VALUE rule intact"),
        ('Use basis="STATED"', "basis/qualifier guidance intact"),
    ):
        check(label, phrase in flat, True)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    import stages.high_confidence_extract as hc
    check_version_floor(md, hc._VERSION, "0.26")
    check("0.25 buy-side coherence still delivered",
          "BUY-SIDE COHERENCE" in system, True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — one economic fact, one value observation; distinct facts still split")


if __name__ == "__main__":
    main()
