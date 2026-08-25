#!/usr/bin/env python3
"""Classifier 0.15 — target_type is structural, not transaction form.

THE MIRROR PAIR

A 30-deal Product acceptance run produced two failures that are each other's
opposite, from one clause:

    Elan Yorktown   295-unit apartment property, $99M, "the property",
                    "$335K per unit"        -> target_type = standalone_company
    Daymark         "acquisition of assets of Daymark Solutions", but an IT
                    services business founded 2001, its own customers,
                    capabilities, Microsoft CSP status, its own CEO
                                            -> target_type = assets

Expected was exactly the reverse: assets/REAL_ESTATE for Yorktown,
standalone_company/null for Daymark.

The classifier's own notes show it identified the structure correctly and then
classified against it. On Yorktown: "Target is a standalone multifamily property
(real estate asset); classified as standalone_company as the property is a
discrete operating asset sold as a going concern." On Daymark: "Although Daymark
appears to be an operating business with employees and customers, the transaction
is structured as an asset purchase."

ONE CLAUSE, CUTTING BOTH WAYS

The assets gloss said: "Use when the press release frames the deal as a sale of
specific assets rather than a going-concern unit."

  * "frames the deal" is wording language. It licensed selecting assets from the
    title of the Daymark release -- the exact thing the paragraph two lines below
    prohibits. The gloss is attached to the value being chosen; the prohibition
    is a general paragraph. The gloss won.
  * "rather than a going-concern unit" excluded an operating apartment complex
    from assets, and the classifier quoted that reasoning back verbatim.

WHY 0.11 DID NOT ALREADY CATCH DAYMARK

It should have. 0.11 was added for this exact failure -- its versioning row says
so: the classifier "selected `assets` from asset-purchase wording ... on a source
whose substance was the acquisition of a continuing operating business." The rule
was correct but named ONE destination: "or for an operating business
(`business_unit`)". Daymark is an independent company, not a unit of a parent. The
escape hatch did not fit, so the model fell back to the wording. The tie-breaker
had the same hole -- it decided only between business_unit and assets, and
Daymark's employees, customers and revenue pointed at a destination that was
unavailable.

So 0.15 does not re-add the prohibition. It keeps it, keeps its load-bearing
"solely", and repairs where it sends an operating business.

WHAT MUST NOT REGRESS

The risk of this change is over-correction: a rule that makes everything an
operating company would destroy genuine asset deals, and one that widened assets
too far would swallow real carve-outs. Both directions are pinned -- genuine
business_unit, genuine subsidiary and genuine asset acquisitions all keep their
licensing text, as do the spin/split rules, the parent_seller rule, the
four-value taxonomy and asset_type subordination.

LAYER

Delivered contract only -- every assertion reads load_prompt_file(...)["system"],
never the Markdown, so a rule that drifts outside the section 4 fence fails here.
Section 7's worked examples are outside that fence and reach no model; Examples
19-20 document the mirror pair and nothing is asserted from them. asset_type
behaviour is not re-tested: it is HC-authored, subordinate to this field, and
scripts/test_asset_type.py already pins it end to end. This change adds no second
author of target_type.

Run from project root:
    python scripts/test_structural_target_typing.py
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
    prompt = load_prompt_file("deal_type_classifier")
    system = prompt["system"]
    # Phrase pins run against a whitespace-normalized view: re-wrapping a paragraph is
    # formatting, not a contract change. Section scoping still uses `system`.
    flat = re.sub(r"\s+", " ", system)

    # Slice the assets gloss defensively so a pre-change run reports every missing rule
    # rather than dying on the first assertion.
    if "- assets —" in system and "There is no `spinco`" in system:
        gloss = system[system.index("- assets —"):system.index("There is no `spinco`")]
    else:
        gloss = ""
    gloss_flat = re.sub(r"\s+", " ", gloss)

    print("\nassets is defined by the transaction object, not the wording:")
    check("gloss present", bool(gloss), True)
    check("transaction object is the test",
          "transaction object itself is an asset" in gloss_flat, True)
    check("chosen from what is transacted, not how the release words it",
          "not from how the release words the deal" in gloss_flat, True)
    # The Yorktown trap: the old clause excluded an operating property from assets.
    check("an operating asset is still an asset",
          "does not stop being an asset because it is operating" in gloss_flat, True)
    check("the going-concern exclusion is gone",
          "rather than a going-concern unit" in system, False)
    check("the framing-based selection rule is gone",
          "frames the deal as a sale of specific assets" in system, False)

    print("\nThe asset kinds Product named are enumerated:")
    for phrase, label in (
        ("real estate or property", "real estate / property"),
        ("intellectual", "intellectual property"),
        ("product line", "product line"),
        ("contracts or operating rights", "contracts / operating rights"),
        ("equipment", "equipment"),
        ("facilities", "facilities"),
    ):
        check(label, phrase in gloss_flat, True)

    print("\nAn operating business routes by its actual structure:")
    check("employees/customers/revenue means operating business, not asset set",
          "employees, customers and revenue as a business" in gloss_flat, True)
    for phrase, label in (
        ("standalone_company if it is independent", "standalone_company when independent"),
        ("business_unit or subsidiary if it is", "business_unit / subsidiary when part of a parent"),
    ):
        check(label, phrase in gloss_flat, True)
    check("assets reserved for a discrete asset set being transferred",
          "only when a discrete asset or asset set is the thing being transferred"
          in gloss_flat, True)

    print("\n0.11's prohibition survives, with its destination repaired:")
    check("'Transaction form alone does not determine target type' still delivered",
          "Transaction form alone does not determine target type" in flat, True)
    check("'solely' is retained — the load-bearing word",
          "solely because the source calls it an \"asset purchase\"" in flat, True)
    check("'the assets of' wording still named",
          'says the buyer acquired "the assets of" a company' in flat, True)
    # The Daymark trap: 0.11 offered only business_unit, so an independent company
    # had nowhere to go and the model fell back to the wording.
    check("destination now names all three operating structures",
          "`standalone_company` when it is independent" in flat
          and "`business_unit` or `subsidiary` when it is part of a parent" in flat, True)
    check("papered-as-an-asset-purchase does not convert a company",
          "does not become an asset set because the deal was papered as an asset purchase"
          in flat, True)
    check("researcher review still offered for genuine ambiguity",
          "Researcher review can resolve genuinely ambiguous cases" in flat, True)

    # ------------------------------------------------------------------
    # CONTROLS. Over-correcting would destroy genuine asset and carve-out deals.
    print("\nGenuine business_unit and subsidiary still licensed (the controls):")
    check("business_unit definition intact",
          "business_unit — A division or operating segment of a Parent company" in system, True)
    check("business_unit language cues intact",
          '"division," "business unit," "operating segment."' in flat, True)
    check("subsidiary definition intact",
          "subsidiary — A separate legal entity owned by a Parent" in system, True)
    check("subsidiary language cues intact",
          '"a subsidiary of [Parent]," "wholly owned subsidiary."' in flat, True)
    check("standalone_company definition intact",
          "standalone_company — An independent company being acquired" in system, True)
    check("carve-out still routes to business_unit or subsidiary, not a new type",
          "target_type =   business_unit or subsidiary" in flat
          or "target_type = business_unit or subsidiary" in flat, True)

    print("\nThe spin/split rules are untouched:")
    check("structural-merits block intact",
          "Classify the distributed entity on its own structural merits" in flat, True)
    check("'Do NOT use standalone_company merely because' intact",
          "Do NOT use standalone_company merely because" in flat, True)
    check("distributed-entity routing table intact",
          "a discrete asset set" in flat and "an existing subsidiary being distributed" in flat,
          True)
    check("transacted-now-not-what-it-becomes intact",
          "what is being transacted now, not" in flat, True)
    check("no spinco value", "There is no `spinco` value" in flat, True)

    print("\nTaxonomy, parent_seller and subordination unchanged:")
    # Capture whatever the value list actually declares, not a pre-agreed allowlist:
    # an allowlist regex confirms the four are present and is blind to a fifth being
    # added beside them. Mutation testing caught exactly that.
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
    check("JOINT_VENTURE target_type still null",
          "For JOINT_VENTURE, target_type is null" in flat, True)
    check("minority/funding default intact",
          "For minority stake purchases and funding rounds, use target_type =" in flat, True)
    check("REVERSE_MERGER / DE_SPAC default intact",
          "combination_structure = REVERSE_MERGER or DE_SPAC, target_type = standalone_company"
          in flat, True)
    check("legacy uppercase still declared invalid output",
          "Legacy uppercase values" in flat and "are no longer valid" in flat, True)

    print("\nThis change adds no second author of target_type:")
    import stages.deal_type_classify as dtc
    import stages.high_confidence_extract as hcs
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    check("HC still never writes target_type",
          re.search(r"target_type\s*=\s*\?", src) is None, True)
    check("HC still reads the classifier's value to gate asset_type",
          'row["target_type_v2"] or row["target_type"]' in src, True)
    check("asset_type subordination still enforced in HC",
          "asset_type is valid only for assets" in src, True)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "deal_type_classifier.md").read_text(encoding="utf-8")
    check_version_floor(md, dtc._VERSION, "0.15")
    check("0.11 row still records the prohibition's origin",
          bool(re.search(r"^\| 0\.11 \|", md, re.M)), True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — target_type follows the transaction object; operating businesses "
          f"route by structure")


if __name__ == "__main__":
    main()
