#!/usr/bin/env python3
"""HC 0.25 buy-side coherence — the contradiction yields, the parties do not move.

THE RECORD THAT PROMPTED THIS

A trade-press article led with "Rotunda Capital Partners announced it is acquiring
Revv... and merging it with AirPro Diagnostics." HC 0.24 returned, from one source:

    acquirer.name            = Rotunda Capital Partners
    acquirer.type            = private_equity          -> the buyer IS the sponsor
    acquirer.sponsor_name    = null
    sponsor_transaction_role = ADD_ON                  -> the buyer is sponsor-BACKED

Both cannot be true of one buyer. ADD_ON is defined as an acquisition by a company
that is already sponsor/PE-backed; a sponsor making a direct investment cannot
satisfy it. Nothing in 0.24 made the model check the four fields against each other,
so it asserted each one plausibly and the record contradicted itself.

WHAT 0.25 CHANGES, AND WHAT IT DELIBERATELY DOES NOT

The source genuinely states that Rotunda is acquiring Revv, so HC is still allowed
to return that acquirer and that type. The error was the classification, not the
parties. 0.25 therefore resolves the contradiction in ONE direction: withhold
ADD_ON, keep the source-stated acquirer.

It must NOT reseat parties. Promoting the portfolio company into the acquirer seat
to make the fields agree would have the model override its source to satisfy an
internal consistency rule -- inventing a buy side the article does not describe.
The parties are what the source says; the classification is what the evidence
supports; when they disagree, the classification yields.

WHY THE CONTROLS MATTER MORE THAN THE FIX

Four rows in the 30-deal acceptance run were already correct, all of the ordinary
shape "X, a Y-backed platform, acquired Z": Valor/Osceola, Flow/Quad-C,
NexTech/Clairvest, Arbor/Caravel. The real risk of this change is that a coherence
rule quietly suppresses ADD_ON on those too, trading one wrong record for four. The
delivered contract must keep licensing them explicitly.

LAYER

Delivered contract only -- every assertion reads `load_prompt_file(...)["system"]`,
never the Markdown, so a rule that drifts outside the §4 fence fails here. There is
no behavioural layer: the change is a model-facing rule, and no code path decides
sponsor_transaction_role.

Run from project root:
    python scripts/test_buy_side_coherence.py
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


def main() -> None:
    print(__doc__.strip().split("\n")[0])
    prompt = load_prompt_file("high_confidence_extraction")
    system = prompt["system"]
    # Line wrapping inside a prompt is formatting, not contract. Phrase pins run
    # against a whitespace-normalized view, so re-wrapping a paragraph cannot fail
    # a rule that is still delivered. Block/section scoping still uses `system`.
    flat = re.sub(r"\s+", " ", system)

    # -----------------------------------------------------------------------
    print("\nThe coherence block is delivered:")
    check("BUY-SIDE COHERENCE block present", "BUY-SIDE COHERENCE" in system, True)
    # Slice the block defensively: when it is absent the run must report every
    # missing rule below, not die on the first assertion. A pre-change run that
    # stops at check one demonstrates nothing about the rest of the contract.
    if "BUY-SIDE COHERENCE" in system:
        block = system[system.index("BUY-SIDE COHERENCE"):]
        block = block.split("\nfeatures:")[0]
    else:
        block = ""
    for field in ("acquirer.name", "acquirer.type", "acquirer.sponsor_name",
                  "deal.sponsor_transaction_role"):
        # Each of the four fields must be named inside the block itself, not merely
        # somewhere in the prompt -- the point is that they are tied together.
        check(f"{field} named in the block", field in block, True)

    # -----------------------------------------------------------------------
    print("\nThe contradiction is named concretely, not gestured at:")
    check("private_equity + ADD_ON named as the contradiction",
          "acquirer.type = private_equity" in flat and "ADD_ON" in flat, True)
    check("'no distinct sponsor-backed operating-company acquirer' condition stated",
          "no distinct" in flat and "operating-company acquirer" in flat, True)

    # -----------------------------------------------------------------------
    # The direction of resolution is the whole ruling. Pin it four ways, so
    # deleting three of the four sentences still fails.
    print("\nResolution direction — withhold the classification, never move a party:")
    for phrase, label in (
        ("WITHHOLDING ADD_ON, NEVER BY MOVING A PARTY", "withhold-not-move rule delivered"),
        ("sponsor_transaction_role = null", "explicit instruction to return null"),
        ("Do not promote a", "no promoting a portfolio company into the acquirer seat"),
        ("invent, rename or reassign", "no inventing/renaming/reassigning a party"),
        ("the classification yields", "the classification yields, not the parties"),
        ("This cuts one way only", "rule is stated as one-directional"),
    ):
        check(label, phrase in flat, True)

    # -----------------------------------------------------------------------
    print("\nADD_ON's evidence bar is raised in the way Product ruled:")
    check("ADD_ON requires the backed company to BE the operating-company buyer",
          "operating-company buyer" in flat, True)
    check("'NOT ENOUGH ON ITS OWN' carve-out delivered",
          "NOT ENOUGH ON ITS OWN" in flat, True)
    check("sponsor-owns-another-company-and-intends-to-combine is excluded",
          "intends to combine the target with it" in flat, True)
    check("the worked shape of the excluded case is delivered",
          "merge it with P" in flat, True)
    check("excluded case instructs keeping the source-stated acquirer",
          "keep the source-stated acquirer" in flat, True)

    # -----------------------------------------------------------------------
    # THE CONTROLS. A coherence rule that suppresses ordinary sponsor-backed
    # add-ons would trade one wrong record for four. The licensing wording for
    # them must survive verbatim.
    print("\nOrdinary sponsor-backed acquisitions are still licensed (the controls):")
    check("portfolio-company wording still qualifies",
          "a portfolio company of Y Capital, acquired Z" in flat, True)
    check("private-equity-backed wording still qualifies",
          "a private-equity-backed company, acquired Z" in flat, True)
    check("literal add-on/bolt-on/tuck-in wording still not required",
          "is NOT required" in flat, True)
    check("sponsor still need not be named",
          "sponsor does not have to be named" in flat, True)
    check("company description may still supply the context",
          "company description" in flat, True)

    print("\nThe other three roles are untouched:")
    check("PLATFORM still requires affirmative new-platform evidence",
          "creates or acquires the company as a NEW sponsor platform" in flat, True)
    check("PLATFORM still not established by a PE buyer alone",
          "A PE firm being the buyer does NOT establish this on its own" in flat, True)
    check("null still expected to be common",
          "null is expected to be common" in flat, True)
    check("generic VC backing still excluded", "Generic VC backing is not ADD_ON" in flat,
          True)
    check("role still independent of acquirer.type",
          "independent of acquirer.type" in flat, True)

    # -----------------------------------------------------------------------
    # The party definitions themselves must NOT have acquired a reseating rule.
    # This is the negative pin for the ruling: 0.25 changes the classification
    # bar and adds a check; it does not teach HC to pick a different acquirer.
    print("\nParty definitions unchanged — no reseating language was introduced:")
    parties = system[system.index("\nPARTIES"):system.index("\nDATES")]
    check("acquirer.name still defined as the entity as stated",
          "- name: Acquiring entity name as stated" in parties, True)
    check("sponsor_name still 'associated with the acquirer'",
          "sponsor associated with the acquirer" in parties, True)
    check("sponsor_name still not gated on acquirer.type",
          "Not gated on any acquirer.type value" in parties, True)
    check("no instruction to prefer an operating company over the stated acquirer",
          "the acquirer is the OPERATING COMPANY" in parties, False)

    # -----------------------------------------------------------------------
    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    check("prompt declares 0.25",
          bool(re.search(r"^\*\*Version:\*\* 0\.25\b", md, re.M)), True)
    check("versioning table carries a 0.25 row",
          bool(re.search(r"^\| 0\.25 \|", md, re.M)), True)
    import stages.high_confidence_extract as hc
    check("stage _VERSION matches the prompt", hc._VERSION, "0.25")
    check("user template unchanged in shape",
          "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — buy-side coherence delivered; ADD_ON yields, parties stay put")


if __name__ == "__main__":
    main()
