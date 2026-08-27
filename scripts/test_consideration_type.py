#!/usr/bin/env python3
"""Aggregation 0.9 — consideration_type follows what was offered.

WHAT WENT WRONG

`_derive_consideration_type` tested the full set of component forms against
`{CASH}`, the stock forms, and their union. Anything outside those three fell
through to `OTHER`. A set test has no notion of which forms are structural, so:

  * Leggett & Platt -- an all-stock combination that assumed the target's existing
    indebtedness -- carried {ACQUIRER_STOCK, DEBT_ASSUMED} and was typed `OTHER`.
  * Maverick Power -- a cash purchase with a contingent earnout -- carried
    {CASH, EARNOUT} and was typed `OTHER`.

The second contradicted a delivered contract in as many words.
`low_confidence_extraction` states: "Earnouts and CVRs do NOT change
consideration_type — a cash + earnout deal stays consideration_type=CASH." The
prompt said one thing and the deterministic derivation did another.

THE DISTINCTION

`consideration_type` describes what was OFFERED. `EARNOUT`, `CVR`,
`CONTINGENT_CONSIDERATION`, `DEBT_ASSUMED` and `RETAINED_EQUITY` are transaction
terms: debt the buyer takes on, a payment contingent on later performance, equity a
seller keeps. None of them is what the buyer offered, so none decides the type.
They are set aside before the ladder runs.

`OTHER` is deliberately not set aside. The LC vocabulary defines it as "preferred
stock, exchangeable shares, notes" -- genuinely other consideration, which is what
`OTHER` exists to say.

TERMS WITH NO OFFERED FORM

Components carrying only terms resolve to **null**, not `OTHER`. A source that
described the structure and never said what was paid has not established a
consideration type, and `OTHER` would assert a form nobody stated.

WHAT DOES NOT CHANGE

No value economics. Assumed debt still never reaches `equity_value` -- the reviewer
confirmed that half was already right -- `transaction_value` still carries debt
through its own basis, and LC authoring is untouched. This is one function.

Run from project root:
    python scripts/test_consideration_type.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prompts.base import load_prompt_file  # noqa: E402
import stages.aggregate as aggregate  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def derive(*forms: str):
    return aggregate._derive_consideration_type(
        json.dumps([{"form": f, "amount": None, "percentage": None,
                     "description": f"{f} component"} for f in forms]))


def main() -> int:
    print(__doc__.strip().split("\n")[0])

    print("\nThe two regression cases:")
    # Leggett & Platt: "an all-stock transaction valued at approximately $2.3 billion
    # ... and inclusive of Leggett & Platt's existing indebtedness."
    check("Leggett & Platt: stock + assumed debt is a STOCK deal",
          derive("ACQUIRER_STOCK", "DEBT_ASSUMED"), "STOCK")
    # Maverick Power: $1.75B cash plus up to $550M contingent on 2027-28 performance.
    check("Maverick Power: cash + earnout is a CASH deal",
          derive("CASH", "EARNOUT"), "CASH")

    print("\nThe offered forms still decide, exactly as before:")
    check("cash", derive("CASH"), "CASH")
    check("acquirer stock", derive("ACQUIRER_STOCK"), "STOCK")
    check("target stock", derive("TARGET_STOCK"), "STOCK")
    check("both stock forms", derive("ACQUIRER_STOCK", "TARGET_STOCK"), "STOCK")
    check("cash and stock", derive("CASH", "ACQUIRER_STOCK"), "CASH_AND_STOCK")
    check("cash and target stock", derive("CASH", "TARGET_STOCK"), "CASH_AND_STOCK")

    print("\nOTHER still decides — it is a genuine offered form, not a term:")
    check("other alone", derive("OTHER"), "OTHER")
    check("cash plus notes is genuinely other", derive("CASH", "OTHER"), "OTHER")
    check("stock plus notes is genuinely other", derive("ACQUIRER_STOCK", "OTHER"), "OTHER")
    check("and a term alongside it does not rescue it",
          derive("OTHER", "EARNOUT"), "OTHER")

    print("\nEvery term is non-determining, alone and in combination:")
    for term in ("EARNOUT", "CVR", "CONTINGENT_CONSIDERATION", "DEBT_ASSUMED",
                 "RETAINED_EQUITY"):
        check(f"cash + {term}", derive("CASH", term), "CASH")
        check(f"stock + {term}", derive("ACQUIRER_STOCK", term), "STOCK")
        check(f"cash + stock + {term}", derive("CASH", "ACQUIRER_STOCK", term),
              "CASH_AND_STOCK")
    check("a PE deal: cash, rolled equity and an earnout is still CASH",
          derive("CASH", "RETAINED_EQUITY", "EARNOUT"), "CASH")
    check("every term at once cannot displace the offered form",
          derive("CASH", "EARNOUT", "CVR", "CONTINGENT_CONSIDERATION",
                 "DEBT_ASSUMED", "RETAINED_EQUITY"), "CASH")

    print("\nTerms with no offered form are null, not OTHER:")
    check("assumed debt alone", derive("DEBT_ASSUMED"), None)
    check("earnout alone", derive("EARNOUT"), None)
    check("debt and rolled equity, nothing offered stated",
          derive("DEBT_ASSUMED", "RETAINED_EQUITY"), None)

    print("\nThe absent and malformed cases are unchanged:")
    check("no components json", aggregate._derive_consideration_type(None), None)
    check("empty string", aggregate._derive_consideration_type(""), None)
    check("empty array", aggregate._derive_consideration_type("[]"), None)
    check("not json", aggregate._derive_consideration_type("{not json"), None)
    check("component with no form",
          aggregate._derive_consideration_type('[{"amount": 5}]'), None)
    check("a form that is not in the vocabulary still decides",
          derive("SOMETHING_NEW"), "OTHER")

    print("\nThe term set matches the LC vocabulary it is drawn from:")
    lc = load_prompt_file("low_confidence_extraction")["system"]
    flat = re.sub(r"\s+", " ", lc)
    check("terms are exactly the five non-offered forms",
          sorted(aggregate._NON_DETERMINING_CONSIDERATION_FORMS),
          ["CONTINGENT_CONSIDERATION", "CVR", "DEBT_ASSUMED", "EARNOUT",
           "RETAINED_EQUITY"])
    for form in aggregate._NON_DETERMINING_CONSIDERATION_FORMS:
        check(f"{form} is still a delivered LC form", form in lc, True)
    check("OTHER is NOT treated as a term",
          "OTHER" in aggregate._NON_DETERMINING_CONSIDERATION_FORMS, False)
    check("LC still defines OTHER as preferred stock / exchangeable shares / notes",
          "OTHER — any other form (preferred stock, exchangeable shares, notes)"
          in flat, True)
    check("LC's earnout rule is the one this restores",
          "a cash + earnout deal stays consideration_type=CASH" in flat, True)
    check("LC authoring untouched — the form enum is unchanged",
          "CASH, ACQUIRER_STOCK, TARGET_STOCK, EARNOUT, CVR, CONTINGENT_CONSIDERATION, "
          "DEBT_ASSUMED, RETAINED_EQUITY, OTHER" in flat, True)

    print("\nNo value economics moved:")
    check("has_earnout still reads the components, not the type",
          aggregate._derive_has_earnout(json.dumps([{"form": "EARNOUT"}])), 1)
    check("has_cvr likewise", aggregate._derive_has_cvr(json.dumps([{"form": "CVR"}])), 1)
    check("equity value derivation untouched by this slice",
          "consideration_type" in (aggregate._derive_equity_value.__doc__ or ""), False)
    check("transaction value still names its own debt basis",
          "EQUITY_PLUS_TOTAL_DEBT" in (aggregate._derive_transaction_value.__doc__ or ""),
          True)
    check("Stage 9 still owns 120 canonical columns",
          len(aggregate._STAGE9_OWNED_COLUMNS), 120)

    print("\nVersion:")
    md = (ROOT / "prompts" / "aggregation.md").read_text(encoding="utf-8")
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check("aggregation declares a version", bool(declared), True)
    check("versioning table carries the 0.9 row",
          bool(re.search(r"^\| 0\.9 \|", md, re.M)), True)
    check("stage _VERSION agrees with the prompt",
          aggregate._VERSION, declared.group(1) if declared else None)
    check("the contract now states the distinction",
          "transaction terms rather than offered consideration" in
          re.sub(r"\s+", " ", md), True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — terms describe the deal; the offered forms decide the type")
    return 0


if __name__ == "__main__":
    sys.exit(main())
