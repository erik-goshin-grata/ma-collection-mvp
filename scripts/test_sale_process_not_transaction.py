#!/usr/bin/env python3
"""Relevancy 0.9 — seeking a buyer is not a transaction.

WHAT PROMPTED THIS

A 30-deal Product acceptance run included a Chapter 11 story: BFG Supply filed in
Delaware "seeking to sell all of its assets through a court-supervised auction
process", with SSG Capital Advisors running a going-concern sale on a ~60-day
target, and the Debtors "in active negotiations with parties that could serve as
a stalking horse bidder". No buyer. No agreement. No sale.

Those 30 sources bypassed relevancy, so the row reached Stage 3, which returned
UNKNOWN, and Stage 4, which recorded an acquirer_description reading "No acquirer
has been identified". It then sat unclusterable forever, because Stage 8 needs an
acquirer that does not exist. Three consecutive passes each wrote, in prose, that
there was no transaction -- and the pipeline had no state in which to say so,
because the stage that owns that judgment never ran.

WHY 0.8 DID NOT ALREADY COVER IT

Every in-scope category is counterparty-bearing ("one company buying another",
"a Parent selling ... to a third-party buyer"), and the two maturity extensions
-- definitive agreements, closing or completion -- both presuppose a deal that
exists. But "Carve-outs, divestitures, and asset sales" reads as a plain licence
for an announced asset sale, and BFG announces one. The nearest rule, the rumor
edge case, draws its line at "without a definitive agreement" and does not reach
a formal, court-supervised, advisor-run process. So the contract was genuinely
silent, and its only signal was indirect: the enum-discipline block routes
ADVISORY_ENGAGEMENT_NO_DEFINITIVE_TRANSACTION to OTHER_NOT_RELEVANT.

THE RULE, AND THE PART THAT IS EASY TO GET BACKWARDS

The test is whether a counterparty is established -- NOT how formal or how
likely the sale is. A court-supervised auction with a filed motion, an engaged
banker and a dated timeline reads as far more real than a rumor, and that
plausibility is exactly the trap: it is still out of scope while the buyer is
unidentified. The clause naming formality and likelihood as the wrong basis is
pinned as hard as the rule itself.

The counterparty test is scoped to this boundary by Product ruling. It is not a
general transaction requirement and must not be pinned as one anywhere below.

WHAT MUST NOT REGRESS

The rule this narrows is load-bearing. A clarification that quietly suppressed
real divestitures would trade one wrong record for many, so the licensing text
for actual sales survives verbatim: the in-scope asset-sale line, the alias
ASSET_SALE -> CARVE_OUT_OR_DIVESTITURE, definitive agreements, closing or
completion, and the re-entry clause that lets a stalking-horse or winning-bidder
release back in. Rumor treatment is pinned byte-for-byte: it was explicitly out
of scope for this change.

DOCUMENTED BEHAVIOURAL EXPECTATIONS (not executed here -- see LAYER)

    BFG-shaped: Chapter 11, auction, advisors, interested parties, no buyer
                                            -> NOT_RELEVANT / OTHER_NOT_RELEVANT
    "Parent X sells its Y division to Buyer Z" -> RELEVANT / CARVE_OUT_OR_DIVESTITURE
    Sec. 363 sale naming a stalking-horse bidder -> RELEVANT
    Definitive agreement for an asset sale     -> RELEVANT
    Completed sale to a winning bidder         -> RELEVANT / DEAL_CLOSE_OR_COMPLETION
    Rumored acquisition                        -> NOT_RELEVANT / RUMOR_OR_SPECULATION

LAYER

Delivered contract only -- every assertion reads load_prompt_file(...)["system"],
never the Markdown, so a rule that drifts outside the section 4 fence fails here.
Section 7's worked examples are outside that fence and reach no model; they are
documentation, and this file does not assert behaviour from them. No code path
decides relevancy.

Run from project root:
    python scripts/test_sale_process_not_transaction.py
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
    prompt = load_prompt_file("relevancy_filter")
    system = prompt["system"]
    # Phrase pins run against a whitespace-normalized view: re-wrapping a bullet is
    # formatting, not a contract change. Section scoping still uses `system`.
    flat = re.sub(r"\s+", " ", system)

    # Slice the bullet defensively so a pre-change run reports every missing rule
    # rather than dying on the first assertion.
    marker = "describes only a process for seeking a buyer"
    if marker in system:
        start = system.rindex("\n- ", 0, system.index(marker))
        block = system[start:].split("\n- If a release is about a company being added")[0]
    else:
        block = ""
    block_flat = re.sub(r"\s+", " ", block)

    print("\nThe rule is delivered, as an EDGE CASES bullet:")
    check("bullet present", marker in system, True)
    check("bullet sits inside the EDGE CASES section",
          bool(block) and system.index("EDGE CASES") < system.index(marker)
          < system.index("REASON CODES") if marker in system else False, True)
    check("states the conclusion in plain terms",
          "Seeking a buyer is not a transaction" in block_flat, True)
    check("names OTHER_NOT_RELEVANT as the code",
          "NOT_RELEVANT with OTHER_NOT_RELEVANT" in block_flat, True)

    print("\nAll six trigger shapes are named:")
    for phrase, label in (
        ("formal sale process", "formal sale process"),
        ("court-supervised or bankruptcy auction", "court-supervised / bankruptcy auction"),
        ("strategic-alternatives review", "strategic-alternatives review"),
        ("stated intention to sell", "stated intention to sell"),
        ("solicitation of bids", "solicitation of bids"),
        ("engagement of advisors to pursue a sale", "advisors engaged to pursue a sale"),
    ):
        check(label, phrase in block_flat, True)

    print("\nThe counterparty test is the stated basis:")
    check("no established counterparty is the condition",
          "no counterparty to a specific acquisition or divestiture has been announced or "
          "agreed" in block_flat, True)
    # The whole rule is conditioned on the seeking-a-buyer antecedent. Product ruled the
    # counterparty test is scoped to this boundary, so it must stay inside this bullet and
    # must NOT appear as a free-standing requirement elsewhere in the delivered contract.
    check("counterparty language is confined to this bullet, not generalized",
          flat.count("no counterparty to a specific acquisition"), 1)

    print("\nFormality and likelihood are excluded as a basis (the trap):")
    check("turns on the counterparty, not formality or likelihood",
          "not on how formal or how likely the sale is" in block_flat, True)
    check("the worked hard case is delivered",
          "filed motion" in block_flat and "target completion date" in block_flat, True)
    check("still out of scope while the buyer is unidentified",
          "still out of scope while the buyer is unidentified" in block_flat, True)

    print("\nRe-entry clause — a later, real transaction is still RELEVANT:")
    for phrase, label in (
        ("stalking-horse bidder", "stalking-horse bidder named"),
        ("winning bidder or acquirer", "winning bidder / acquirer named"),
        ("definitive sale agreement", "definitive sale agreement named"),
        ("completed sale", "completed sale named"),
        ("separate source and may be RELEVANT on its own terms", "re-entry stated plainly"),
    ):
        check(label, phrase in block_flat, True)

    # ------------------------------------------------------------------
    # CONTROLS. The clarification must not suppress actual transactions.
    print("\nReal sales are still licensed (the controls):")
    check("in-scope asset-sale/divestiture line untouched",
          "- Carve-outs, divestitures, and asset sales" in system, True)
    check("definitive agreements still in scope",
          "- Definitive agreements for any of the above" in system, True)
    check("closing or completion still in scope",
          "- Closing or completion of any of the above" in system, True)
    check("CARVE_OUT_OR_DIVESTITURE still on the RELEVANT side",
          system.index("CARVE_OUT_OR_DIVESTITURE")
          < system.index("REASON CODES — NOT_RELEVANT side"), True)
    check("DEAL_CLOSE_OR_COMPLETION still on the RELEVANT side",
          system.index("DEAL_CLOSE_OR_COMPLETION")
          < system.index("REASON CODES — NOT_RELEVANT side"), True)
    check("ASSET_SALE alias still maps to CARVE_OUT_OR_DIVESTITURE",
          "ASSET_SALE → use CARVE_OUT_OR_DIVESTITURE" in flat, True)

    print("\nRumor treatment is byte-identical (explicitly out of scope for 0.9):")
    check("rumor bullet unchanged, verbatim",
          "- If a release is about a rumored deal without a definitive agreement, classify "
          "as NOT_RELEVANT (rumor coverage is out of MVP scope)." in system, True)
    check("RUMOR_OR_SPECULATION still delivered", "RUMOR_OR_SPECULATION" in system, True)

    print("\nThe other three edge cases are untouched:")
    for phrase, label in (
        ("the acquisition is the higher-priority signal", "product + acquisition intact"),
        ("previously announced deal being amended, terminated, or extended",
         "amendment / termination intact"),
        ("added to an index, going IPO, or completing a direct listing", "IPO intact"),
    ):
        check(label, phrase in flat, True)

    print("\nVocabulary integrity — 0.9 adds no reason code:")
    import stages.relevancy_filter as rf
    check("OTHER_NOT_RELEVANT is a valid code", "OTHER_NOT_RELEVANT" in rf._VALID_REASON_CODES,
          True)
    check("OTHER_NOT_RELEVANT is still the NOT_RELEVANT fallback",
          rf._normalize_reason_code("NOT_RELEVANT", "SOMETHING_INVENTED"), "OTHER_NOT_RELEVANT")
    check("vocabulary still 24 codes", len(rf._VALID_REASON_CODES), 24)
    for claim in (re.findall(r"exactly one of the (\d+) values", system)
                  + re.findall(r"one of the (\d+) enum values", system)):
        check(f"prompt's self-stated count ({claim}) still matches the vocabulary",
              int(claim), 24)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "relevancy_filter.md").read_text(encoding="utf-8")
    check_version_floor(md, rf._VERSION, "0.9")
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — a sale process with no counterparty is out; real sales still in")


if __name__ == "__main__":
    main()
