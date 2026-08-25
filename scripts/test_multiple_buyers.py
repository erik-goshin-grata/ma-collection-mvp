#!/usr/bin/env python3
"""HC 0.27 — buyers are the firms; `consortium` is retired from new authoring.

WHAT WENT WRONG

An acceptance source read "Greystar sold the 295-unit Elan Yorktown in Lombard to
a venture of RPM Living and New York Life". The extraction returned:

    acquirer_name = "RPM Living and New York Life venture"
    acquirer_type = "consortium"

The name is not in the source. The model reordered "a venture of X and Y" into a
possessive-style name for a company that does not exist, and neither firm can be
found under it. The only guard was one line -- "name: Acquiring entity name as
stated" -- and a single unelaborated sentence did not hold against a type value
whose gloss, "multiple buyers acting jointly", invited treating several firms as
one buyer.

TWO INDEPENDENT DEFECTS, AND WHY ORDER MATTERS

  A  stale vocabulary: consortium remained accepted after Product ruled it is not
     a buyer classification.
  B  cardinality: two named firms compressed into one invented buyer string.

Fixing A alone makes B worse. The participant backfill splits a multi-buyer name
into individual participants only on the CONSORTIUM branch, so retiring the type
without fixing the name would write the invented string as a single participant.
And fixing B without A leaves a joint value asserting a classification that is
true of neither firm -- RPM Living is a multifamily operator, New York Life an
insurer.

THE PASSTHROUGH TRAP

Removing "consortium" from _VALID_ACQUIRER_TYPES_V2 does NOT stop it being
stored. _validate never checks acquirer.type, and _normalize_acquirer_type ends
in `.get(raw, raw)` -- an unrecognized value is returned unchanged and written.
Deleting the CONSORTIUM alias would have RE-ENABLED the value it was meant to
retire, by turning a mapped value into a passed-through one. So the retirement is
an explicit mapping to `unknown`, checked here in both cases, and the behavioural
half of this file exists because the prompt half cannot see that trap at all.

RETIRED IS NOT DELETED

Stored rows still carry `consortium`, and three read paths must keep working: the
aggregation prompt reads it on its legacy_read_only line, the take-private
derivation must keep treating it as safely non-qualifying, and
lib/participant_backfill.py branches on it. None of those are touched. This file
pins the separation in both directions -- impossible to author, still readable.

WHAT MUST NOT REGRESS

Every other buyer type stays. A rule that made multi-party names suspect would
also damage single companies whose names merely look plural: the same acceptance
run contains "Clear Group" and "Behnke Dedicated & Logistics". Those are pinned
against the real parser, as is the comma-delimited co-sponsor rule, which is a
DIFFERENT multi-party representation and must not be disturbed.

LAYER

The vocabulary and rule assertions read load_prompt_file(...)["system"], never the
Markdown, so a rule that drifts outside the section 4 fence fails. The
normalization and name-parsing assertions are behavioural and need no model. This
change does not wire participant_backfill into the pipeline; that stays a later
implementation concern and is asserted to be unchanged here.

Run from project root:
    python scripts/test_multiple_buyers.py
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
    """Pin the rule's provenance without freezing the prompt at one version."""
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
    flat = re.sub(r"\s+", " ", system)

    marker = "MULTIPLE BUYERS"
    if marker in system:
        block = system[system.index(marker):].split("\n- domain:")[0]
    else:
        block = ""
    block_flat = re.sub(r"\s+", " ", block)

    # ---------------------------------------------------------------- B: cardinality
    print("\nB — the multiple-buyers rule is delivered, on acquirer.name:")
    check("block present", marker in system, True)
    check("block sits on acquirer.name",
          bool(block) and system.index("acquirer:") < system.index(marker)
          if marker in system else False, True)
    check("name the actual firms as the source names them",
          "name the actual firms as the source names them" in block_flat, True)
    check("never manufacture a collective entity",
          "Never manufacture a collective entity" in block_flat, True)

    print("\nEvery forbidden collective noun is named separately:")
    for word in ("venture", "joint venture", "consortium", "group", "partnership"):
        check(f'"{word}" named as not-to-be-appended', f'"{word}"' in block_flat, True)
    check("possessive-style reordering excluded",
          "do not reorder the firms into a possessive-style name" in block_flat, True)

    print("\nThe worked case is delivered, with the exact right and wrong answers:")
    check("source phrasing quoted",
          "a venture of RPM Living and New York Life" in block_flat, True)
    check("correct name given",
          'the acquirer name is "RPM Living and New York Life"' in block_flat, True)
    check("the invented name is shown as wrong",
          'not "RPM Living and New York Life venture"' in block_flat, True)
    check("states why — the arrangement is not a company",
          "the arrangement between them is not a company" in block_flat, True)

    # ---------------------------------------------------------------- A: vocabulary
    print("\nA — consortium is retired from the delivered acquirer.type vocabulary:")
    check("consortium gone from the delivered vocabulary",
          "consortium — multiple buyers acting jointly" in system, False)
    check("its absence is explained, not silent",
          'no value for "multiple buyers acting jointly"' in flat, True)
    check("reason given: classification describes an individual firm",
          "Buyer classification describes an individual firm" in flat, True)
    check("multi-buyer scalar returns unknown",
          "When the acquirer name carries more than one distinct buyer, return unknown"
          in flat, True)
    check("labelled as compatibility, not target semantics",
          "compatibility answer for this single scalar field" in flat, True)

    print("\nEvery other buyer type survives (the over-deletion control):")
    for t in ("strategic_corporate", "private_equity", "pe_portfolio", "venture_capital",
              "growth_equity", "sovereign_wealth_fund", "pension_fund", "hedge_fund",
              "family_office", "individual", "management", "employee_group", "spac",
              "other_financial_sponsor", "unknown"):
        check(f"{t} still delivered", re.search(rf"^    {t}\b", system, re.M) is not None, True)

    # ------------------------------------------------------- behavioural: authoring
    print("\nNew authoring of consortium is impossible (the passthrough trap):")
    import stages.high_confidence_extract as hc
    check("not in the accepted set", "consortium" in hc._VALID_ACQUIRER_TYPES_V2, False)
    check("lowercase resolves to unknown", hc._normalize_acquirer_type("consortium"), "unknown")
    check("uppercase resolves to unknown", hc._normalize_acquirer_type("CONSORTIUM"), "unknown")
    # The trap itself: an unmapped value IS stored verbatim, which is why removing the
    # alias would have re-enabled the value rather than retiring it.
    check("unmapped values are still passed through — why a mapping was required",
          hc._normalize_acquirer_type("not_a_real_type"), "not_a_real_type")
    check("ordinary types unaffected",
          [hc._normalize_acquirer_type(v) for v in ("strategic_corporate", "PRIVATE_EQUITY",
                                                    "pe_portfolio", "unknown", None)],
          ["strategic_corporate", "private_equity", "pe_portfolio", "unknown", None])

    # ------------------------------------------------------- behavioural: legacy read
    print("\nRetired is not deleted — stored rows stay readable:")
    agg = load_prompt_file("aggregation")["system"]
    m = re.search(r"^- legacy_read_only: (.+)$", agg, re.M)
    check("aggregation declares a legacy_read_only line", bool(m), True)
    if m:
        legacy = {v.strip() for v in m.group(1).split("|")}
        check("consortium is read-tolerated there", "consortium" in legacy, True)
        check("the retired event types are still there too",
              {"MERGER", "REVERSE_MERGER", "MINORITY_INVESTMENT"} <= legacy, True)
    check("consortium is NOT on the current acquirer_type line",
          bool(re.search(r"^- acquirer_type: .*\bconsortium\b", agg, re.M)), False)
    check("aggregation explains why it is legacy",
          "consortium` is not a buyer classification" in re.sub(r"\s+", " ", agg), True)

    tp = (ROOT / "scripts" / "test_take_private_derivation.py").read_text(encoding="utf-8")
    check("take-private derivation still pins CONSORTIUM as non-qualifying",
          '"CONSORTIUM"' in tp and "_NON_QUALIFYING" in tp, True)
    check("that pin is labelled as legacy readability, not current vocabulary",
          "LEGACY READABILITY" in tp, True)
    pb = (ROOT / "lib" / "participant_backfill.py").read_text(encoding="utf-8")
    check("participant_backfill still branches on stored CONSORTIUM",
          'acquirer_type == "CONSORTIUM"' in pb, True)
    check("participant_backfill still refuses to mint a synthetic entity",
          "generic consortium label stored as group, not synthetic entity" in pb, True)

    # ------------------------------------------------------- controls: name parsing
    print("\nSingle companies with plural-looking names must not be shattered:")
    import lib.participant_backfill as pbmod
    for name in ("Clear Group", "Behnke Dedicated & Logistics", "Johnson & Johnson",
                 "Smith and Sons Inc."):
        check(f"{name!r} stays one party",
              pbmod._parse_name_list(pbmod._clean_name(name)).names, (name,))
    check("the corrected Yorktown name resolves to the two real firms",
          pbmod._parse_name_list(pbmod._clean_name("RPM Living and New York Life")).names,
          ("RPM Living", "New York Life"))

    print("\nOther multi-party representations are untouched:")
    check("co-sponsor comma rule intact", "If multiple co-sponsors, comma-delimit" in flat, True)
    check("sponsor_name still not gated on acquirer.type",
          "Not gated on any acquirer.type value" in flat, True)
    check("parent_seller rule intact",
          "Parent company divesting the target" in flat, True)
    # This remediation deliberately does not populate the participant tables.
    run_py = (ROOT / "run.py").read_text(encoding="utf-8")
    check("participant_backfill still not wired into the pipeline",
          "participant_backfill" in run_py, False)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, hc._VERSION, "0.27")
    check("0.26 currency rule still delivered",
          "ONE ECONOMIC FACT, ONE OBSERVATION" in system, True)
    check("0.25 buy-side coherence still delivered", "BUY-SIDE COHERENCE" in system, True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — buyers stay the firms they are; consortium unauthorable, still readable")


if __name__ == "__main__":
    main()
