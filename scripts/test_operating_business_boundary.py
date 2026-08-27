#!/usr/bin/env python3
"""Classifier 0.16 — an operating business is not an asset set.

TWO COMMENTED ROWS, ONE BOUNDARY

A Product review of a fresh Collection run flagged two `assets` classifications.
They look like different mistakes and are the same one.

  Kristin Manwaring Insurance -- an independent agency, 50+ years old, with clients
  and staff. The classifier's own note recites exactly that: "KMi is an operating
  business with employees, clients, and a 50+ year history. HOWEVER, the release
  explicitly states 'acquired the assets of' ... The team joining as employees and
  the asset-purchase structure together support target_type = assets."

  Adroit Worldwide Media -- "the acquisition of technology, intellectual property
  and talent assets from Adroit Worldwide Media (AWM), a leading technology
  solutions company, for $210 million in cash." AWM continues to exist.

WHY 0.15 DID NOT HOLD

0.15 already forbids choosing `assets` from asset-purchase wording, and the model
quoted that wording as its reason anyway. But it also produced an argument 0.15
never addressed: **the team joining the buyer, offered as support FOR assets**.
That is backwards. A going concern normally moves with its people, so a team
transferring is evidence about what changed hands -- and it points at a business.

The second row exposes the other half. An enumerated bundle -- technology,
intellectual property, talent -- resembles the gloss's own list of asset examples,
so itemizing a software business reads as an asset set. It is not: listing what a
business consists of does not answer whether the business is what changed hands.

WHAT 0.16 ADDS, AND WHAT IT REFUSES TO ADD

People are not an asset class. An enumeration is a description of a deal, not
evidence about it. The test has an order: does an operating concern -- customers,
revenue, ongoing delivery -- pass to the buyer? Then type it by structure. Only
otherwise is it `assets`.

The nuance runs both ways, and the second half is the one an over-correction would
lose. A team moving is evidence FOR an operating concern; it is not proof of one,
because a team can move without a business moving with it. If "the team joined"
became a shortcut to `standalone_company`, that would be the mirror of the mistake
this version exists to stop. Both halves are pinned below.

**No talent asset type is created.** The asset_type vocabulary is asserted
unchanged here, in full, precisely because inventing one would be the easy wrong
answer to the Adroit row.

WHAT MUST NOT REGRESS

The risk is over-correction: a rule that makes everything an operating company
destroys genuine asset deals. Real estate, product lines, contracts and operating
rights keep their licensing text, an asset still does not stop being an asset
because it is operating or income-generating, and the same review's Tuscani Pointe
retail centre remains a straightforward REAL_ESTATE acquisition. Both directions
are pinned.

LAYER

Every assertion reads load_prompt_file(...)["system"], so a rule that drifts
outside the section 4 fence fails here. This change adds no second author of
target_type and no new value to any vocabulary.

Run from project root:
    python scripts/test_operating_business_boundary.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts.base import load_prompt_file  # noqa: E402
import stages.deal_type_classify as dtc  # noqa: E402
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


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_version_floor(md: str, stage_version: str, introduced: str) -> None:
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check(f"versioning table still carries the {introduced} row",
          bool(re.search(rf"^\| {re.escape(introduced)} \|", md, re.M)), True)
    check("prompt declares a version", bool(declared), True)
    if not declared:
        return
    check(f"prompt version >= {introduced} (currently {declared.group(1)})",
          _version_tuple(declared.group(1)) >= _version_tuple(introduced), True)
    check("stage _VERSION agrees with the prompt", stage_version, declared.group(1))


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    prompt = load_prompt_file("deal_type_classifier")
    system = prompt["system"]
    flat = re.sub(r"\s+", " ", system)

    print("\nPeople are not an asset class — the Kristin Manwaring inference, closed:")
    check("stated outright", "People are not an asset class" in flat, True)
    check("a team moving is evidence about WHAT was transacted",
          "is evidence about WHAT was transacted" in flat, True)
    check("a going concern normally moves with its people",
          "a going concern normally moves with its people" in flat, True)
    check("and it is never evidence FOR assets",
          "is never evidence FOR assets" in flat, True)
    check("the exact inference the model made is named and inverted",
          '"the team joined the buyer" supports an operating business having changed '
          'hands, not an asset set' in flat, True)

    # The other half of the same nuance, and the one an over-correction would lose:
    # people moving is evidence FOR an operating concern, and evidence is not proof.
    # Without this, "the team joined" becomes a shortcut to standalone_company --
    # the mirror of the mistake 0.16 exists to stop.
    check("people transferring is NOT sufficient on its own",
          "It is not sufficient on its own, either" in flat, True)
    check("a team can move without a business moving",
          "A team can move without a business moving with it" in flat, True)
    check("it never settles the question by itself",
          "people transferring never settles the question by itself" in flat, True)
    check("the operating-concern test must still be met on the broader evidence",
          "the operating-concern test below still has to be met on customers, revenue "
          "and continuing operations" in flat, True)

    print("\nAn enumeration is a description, not evidence — the Adroit row:")
    check("stated outright", "An enumerated list does not make an asset set" in flat, True)
    check("the actual bundle is quoted",
          '"Technology, intellectual property and talent" names what a software business '
          'consists of' in flat, True)
    check("listing parts does not answer the question",
          "listing its parts does not answer whether the business itself is what changed "
          "hands" in flat, True)
    check("the prohibition now names the enumerated form too",
          'enumerates the purchase as "technology, intellectual property and talent '
          'assets"' in flat, True)
    check("legal form and enumeration are both described as ways of describing a deal",
          "Legal form and an enumerated list are both ways of describing a deal, not "
          "evidence about what the deal was for" in flat, True)

    print("\nThe test now has an order:")
    check("operating concern is asked first",
          "Ask first whether an operating concern" in flat, True)
    check("named by its substance, not its label",
          "customers, revenue, ongoing delivery" in flat, True)
    check("structure wins whatever the release calls it",
          "type it by its structure whatever the release calls the deal" in flat, True)
    check("assets requires separability from a continuing business",
          "separable from any continuing business" in flat, True)

    print("\nNo talent asset type was invented:")
    check("asset_type vocabulary unchanged, in full",
          sorted(hc._VALID_ASSET_TYPES),
          ["BRAND_OR_PRODUCT", "CONTRACTS_OR_RIGHTS", "DATA", "ENERGY", "EQUIPMENT",
           "FACILITY", "INFRASTRUCTURE", "INTELLECTUAL_PROPERTY", "NATURAL_RESOURCES",
           "OTHER", "REAL_ESTATE"])
    for word in ("TALENT", "PEOPLE", "TEAM", "WORKFORCE", "HUMAN_CAPITAL"):
        check(f"no {word} asset type", word in hc._VALID_ASSET_TYPES, False)

    # ------------------------------------------------------------------
    # CONTROLS. Over-correction would destroy genuine asset deals.
    print("\n0.15 survives intact — the prohibition and its repaired destinations:")
    check("'Transaction form alone does not determine target type' still delivered",
          "Transaction form alone does not determine target type" in flat, True)
    check("'solely' retained — the load-bearing word",
          'solely because the source calls it an "asset purchase"' in flat, True)
    check("papered-as-an-asset-purchase still does not convert a company",
          "does not become an asset set because the deal was papered as an asset purchase"
          in flat, True)
    check("all three operating destinations still named",
          "`standalone_company` when it is independent" in flat
          and "`business_unit` or `subsidiary` when it is part of a parent" in flat, True)
    check("researcher review still offered", "Researcher review can resolve genuinely "
          "ambiguous cases" in flat, True)

    print("\nGenuine asset acquisitions keep their licensing text:")
    check("the transaction object is still the test",
          "transaction object itself is an asset" in flat, True)
    for phrase, label in (("real estate or property", "real estate"),
                          ("intellectual", "intellectual property"),
                          ("product line", "product line"),
                          ("contracts or operating rights", "contracts / rights"),
                          ("equipment", "equipment"),
                          ("facilities", "facilities")):
        check(f"{label} still an example", phrase in flat, True)
    check("an operating asset is still an asset — the Yorktown guard",
          "does not stop being an asset because it is operating" in flat, True)
    check("tenanted / income-generating still qualified",
          "tenanted, producing or income-generating" in flat, True)
    check("chosen from what is transacted, not the wording",
          "not from how the release words the deal" in flat, True)

    print("\nTaxonomy and subordination unchanged:")
    if "classify target_type:" in system and "Transaction form alone" in system:
        values = system[system.index("classify target_type:"):
                        system.index("Transaction form alone")]
    else:
        values = ""
    check("still exactly four target_type values, no more",
          sorted(set(re.findall(r"^- ([a-z_]+) —", values, re.M))),
          ["assets", "business_unit", "standalone_company", "subsidiary"])
    check("parent_seller rule intact",
          "When target_type is subsidiary, business_unit, or assets, parent_seller must"
          in flat, True)
    check("spin/split structural-merits block intact",
          "Classify the distributed entity on its own structural merits" in flat, True)
    check("no spinco value", "There is no `spinco` value" in flat, True)

    print("\nThis change adds no second author of target_type:")
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    check("HC still never writes target_type",
          re.search(r"target_type\s*=\s*\?", src) is None, True)
    check("HC still reads the classifier's value to gate asset_type",
          'row["target_type_v2"] or row["target_type"]' in src, True)
    check("asset_type subordination still enforced in HC",
          "asset_type is valid only for assets" in src, True)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "deal_type_classifier.md").read_text(encoding="utf-8")
    check_version_floor(md, dtc._VERSION, "0.16")
    check("the 0.15 row still records the structural rule's origin",
          bool(re.search(r"^\| 0\.15 \|", md, re.M)), True)
    check("the 0.11 row still records the prohibition's origin",
          bool(re.search(r"^\| 0\.11 \|", md, re.M)), True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — a business that changes hands is a business, however the deal is "
          f"worded or itemized")
    return 0


if __name__ == "__main__":
    sys.exit(main())
