#!/usr/bin/env python3
"""HC 0.28 — only supported deal-value facts are captured.

WHAT WENT WRONG

A de-SPAC release stated: "The transaction reflects a pre-money equity valuation of
approximately $1.6 billion and a post-transaction equity valuation of approximately
$2.3 billion." No purchase price appears anywhere in it. The extraction emitted two
observations typed MARKET_CAPITALIZATION and promoted the first into the canonical
value_amount -- so a transaction whose consideration was never stated acquired a
$1.6B "value".

The source never uses the words market capitalization, and the target was private
pre-deal, so the figure did not satisfy even the type it was given. Two failures
compounded: a figure outside the boundary was captured, then relabelled to fit.

WHY THE TYPE HAD TO GO RATHER THAN THE LABELLING BE FIXED

The array's own scope rule already said to return `[]` when the source has no
"explicitly supported, qualified deal-value fact". MARKET_CAPITALIZATION contradicted
it from inside the same vocabulary: defined as "a property of the company, not of the
transaction", yet listed as a deal-value type, with an instruction to capture it on
source-statedness alone. While a type existed for whole-company valuations, any such
figure had somewhere plausible to land.

It was never a Product-approved transaction field either -- absent from both Data
Dictionaries and the schema. It originated as engineering containment in the
2026-08-17 `equity_value` scope finding, to stop market caps being grossed up by pct
into manufactured implied equity. The boundary now does that job by declining the
figure at the door.

A SCOPE TEST, NOT A SIZE TEST

The easy over-correction is to exclude "whole-company valuations". That would be
wrong: a source-stated ENTERPRISE_VALUE is whole-company and fully supported. What
decides capture is whether the figure IS one of the supported concepts. The prompt
says so explicitly, and this file pins that sentence, because losing it would take
ENTERPRISE_VALUE down with the market cap.

UNDISCLOSED is not the fallback either. It means the source SAID terms are not
disclosed -- a different statement from a source that simply never states
consideration. Ursa Major gets null amount, null currency, null type.

THE TRAP: RETIRING A TYPE CAN COST A WHOLE TRANSACTION

`_validate` returns an error for an unknown value type, and in HC a validation error
discards the ENTIRE extraction -- every party, date, advisor and feature with it. So
MARKET_CAPITALIZATION deliberately STAYS in `_VALID_VALUE_TYPES` as tolerance while
being retired from the prompt; the retirement filter drops the one observation and
keeps the transaction. Control 7 pins both halves, so delisting it from the frozenset
in future breaks this file loudly rather than silently costing extractions.

LAYER

Contract assertions read load_prompt_file(...)["system"], never the Markdown, so a
rule that drifts outside the section 4 fence fails here. Derivation assertions call
the real aggregate functions. No model calls, no network, no database.

Run from project root:
    python scripts/test_value_scope_boundary.py
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

    marker = "WHAT IS NOT A DEAL-VALUE FACT"
    block = system[system.index(marker):].split("\n- ENTERPRISE_VALUE")[0] \
        if marker in system else ""
    block_flat = re.sub(r"\s+", " ", block)

    import stages.high_confidence_extract as hc
    import stages.aggregate as agg

    # ---------------------------------------------- 1. unsupported -> no observation
    print("\n1. An unsupported valuation yields no value observation:")
    check("boundary block delivered", marker in system, True)
    check("capture requires a supported definition",
          "captured here ONLY when it satisfies one of the supported definitions"
          in block_flat, True)
    check("not captured at all — not under a nearby type",
          "not captured at all — not under a nearby type" in block_flat, True)
    check("source-stated and interesting is explicitly not the test",
          "is not the test" in block_flat, True)
    for phrase, label in (
        ("a market capitalization", "market capitalization named"),
        ("pre-money, post-money or post-transaction valuation", "pre/post-money named"),
        ("implied, reference or headline valuation", "implied/reference valuation named"),
    ):
        check(label, phrase in block_flat, True)
    check("the worked Ursa Major case is delivered",
          "pre-money equity valuation of approximately $1.6" in block_flat, True)
    check("worked case says emit NO observation",
          "emit NO observation for either" in block_flat, True)
    check("and leave the legacy value object null",
          "leave the legacy value object null" in block_flat, True)
    check("relabelling to fit is forbidden",
          "Do not relabel such a figure as EQUITY_VALUE, ENTERPRISE_VALUE or "
          "TRANSACTION_VALUE to make it fit" in block_flat, True)
    check("MARKET_CAPITALIZATION gone from the delivered contract",
          "MARKET_CAPITALIZATION" in system, False)

    # The wording guard Product specified: scope test, not size test.
    print("\n   …stated as a scope test, not a size test:")
    check("says it is a scope rule, not a size rule",
          "This is a scope rule, not a size rule" in block_flat, True)
    check("a whole-company figure can be in scope",
          "A whole-company figure can be perfectly in scope" in block_flat, True)
    check("source-stated ENTERPRISE_VALUE named as the in-scope example",
          "a source-stated ENTERPRISE_VALUE is whole-company and supported"
          in block_flat, True)

    # ------------------------------------------------ 2-5. supported types survive
    print("\n2. Genuine EQUITY_VALUE remains capturable:")
    check("definition delivered",
          "EQUITY_VALUE — the equity purchase price for the stake actually acquired"
          in flat, True)
    check("still an accepted type", "EQUITY_VALUE" in hc._VALID_VALUE_TYPES, True)
    check("derivation still returns a stated stake price",
          agg._derive_equity_value({"value_amount": 600_000_000, "value_type": "EQUITY_VALUE"}),
          (600_000_000.0, "STATED"))

    print("\n3. Genuine TRANSACTION_VALUE remains capturable:")
    check("definition delivered",
          "TRANSACTION_VALUE — total consideration including assumed debt" in flat, True)
    check("still an accepted type", "TRANSACTION_VALUE" in hc._VALID_VALUE_TYPES, True)
    check("derivation still returns a stated total",
          agg._derive_transaction_value(
              {"value_amount": 220_500_000, "value_type": "TRANSACTION_VALUE"},
              None, None,
            is_control=False),
          (220_500_000.0, "STATED"))

    print("\n4. Genuine ENTERPRISE_VALUE remains capturable:")
    check("definition delivered",
          "ENTERPRISE_VALUE — source-stated whole-company EV" in flat, True)
    check("still an accepted type", "ENTERPRISE_VALUE" in hc._VALID_VALUE_TYPES, True)
    check("derivation still returns a stated EV",
          agg._derive_enterprise_value(900_000_000, "ENTERPRISE_VALUE", None, None),
          (900_000_000.0, "STATED"))

    print("\n5. UNDISCLOSED remains supported, and is not the fallback:")
    check("definition delivered",
          "UNDISCLOSED — source explicitly states terms are not disclosed" in flat, True)
    check("still an accepted type", "UNDISCLOSED" in hc._VALID_VALUE_TYPES, True)
    check("the boundary says not to report UNDISCLOSED for an unsupported figure",
          "do not report UNDISCLOSED" in block_flat, True)
    check("and says why — it is reserved for an explicit statement",
          "reserved for a source that explicitly says the terms or value are not disclosed"
          in block_flat, True)

    # -------------------------------- 6. legacy market cap cannot contaminate economics
    print("\n6. A legacy MARKET_CAPITALIZATION reaches no canonical economic field:")
    mc = {"value_amount": 2_200_000_000, "value_type": "MARKET_CAPITALIZATION"}
    check("equity_value", agg._derive_equity_value(mc), (None, None))
    check("transaction_value",
          agg._derive_transaction_value(mc, None, None, is_control=False), (None, None))
    check("enterprise_value",
          agg._derive_enterprise_value(2_200_000_000, "MARKET_CAPITALIZATION", None, None),
          (None, None))
    check("implied_equity_value (equity-only source, so nothing to gross up)",
          agg._derive_implied_equity(None, 27.0), None)
    agg_sys = load_prompt_file("aggregation")["system"]
    m = re.search(r"^- legacy_read_only: (.+)$", agg_sys, re.M)
    legacy = {v.strip() for v in m.group(1).split("|")} if m else set()
    check("read-tolerated on the aggregation legacy line",
          "MARKET_CAPITALIZATION" in legacy, True)
    check("absent from the current aggregation value_type line",
          bool(re.search(r"^- value_type: .*\bMARKET_CAPITALIZATION\b", agg_sys, re.M)), False)
    check("aggregation states why it is legacy",
          "is not a deal-value fact at all" in re.sub(r"\s+", " ", agg_sys), True)

    # ------------------------------------- 7. no wholesale parse/validation failure
    print("\n7. Retirement drops the observation, never the extraction:")
    check("still tolerated by the validator",
          "MARKET_CAPITALIZATION" in hc._VALID_VALUE_TYPES, True)
    # getattr, not attribute access: on a pre-change tree the symbol does not exist, and
    # dying here would leave every control below unproven. A run that reports each missing
    # guard is the point of a pre-change run.
    retired = getattr(hc, "_RETIRED_VALUE_TYPES", frozenset())
    check("declared retired from authoring", "MARKET_CAPITALIZATION" in retired, True)
    base = {"target": {}, "acquirer": {}, "parent_seller": {}, "dates": {}, "value": {},
            "deal": {}, "features": {}, "target_financials": {},
            "financials_disclosure_status": "UNKNOWN", "transaction_terms_disclosure_status": "UNKNOWN", "model_confidence": "HIGH",
            "reported_multiples": [],
            "acquirers": [],
            "buy_side_sponsors": [],
            "parent_sellers": [],
            "parent_acquirers": [],
            "sell_side_sponsors": [],
            "sellers": [],
            "value_observations": []}
    check("a clean response validates", hc._validate(dict(base)) is None, True)
    check("a response carrying a retired observation still validates",
          hc._validate({**base, "value_observations": [
              {"amount": 2_200_000_000, "type": "MARKET_CAPITALIZATION"}]}) is None, True)
    check("a retired type in the legacy value slot still validates",
          hc._validate({**base, "value": {
              "amount": 1_600_000_000, "type": "MARKET_CAPITALIZATION"}}) is None, True)
    # The tolerance is specific, not a blanket loosening.
    check("a genuinely unknown type is still rejected",
          hc._validate({**base, "value_observations": [
              {"amount": 1, "type": "NOT_A_TYPE"}]}) is not None, True)

    print("\n   …and the drop is surgical — siblings survive:")
    import json as _json
    kept = _json.loads(hc._value_observations_json({"value_observations": [
        {"amount": 2_200_000_000, "type": "MARKET_CAPITALIZATION"},
        {"amount": 600_000_000, "type": "EQUITY_VALUE"},
    ]}))
    check("retired observation dropped, supported one kept",
          [(o["amount"], o["type"]) for o in kept], [(600_000_000, "EQUITY_VALUE")])
    check("an all-retired array becomes empty, not null",
          _json.loads(hc._value_observations_json({"value_observations": [
              {"amount": 2_200_000_000, "type": "MARKET_CAPITALIZATION"}]})), [])
    check("a retired first observation is not promoted to primary",
          hc._primary_value({"value": {}, "value_observations": [
              {"amount": 2_200_000_000, "type": "MARKET_CAPITALIZATION"},
              {"amount": 600_000_000, "type": "EQUITY_VALUE"}]})["type"], "EQUITY_VALUE")
    # The Ursa Major shape end to end: a retired legacy slot and nothing else.
    ursa = hc._primary_value({"value": {"amount": 1_600_000_000, "currency": "USD",
                                        "type": "MARKET_CAPITALIZATION"},
                              "reported_multiples": [],
                              "acquirers": [],
                              "buy_side_sponsors": [],
                              "parent_sellers": [],
                              "parent_acquirers": [],
                              "sell_side_sponsors": [],
                              "sellers": [],
                              "value_observations": []})
    check("Ursa Major: amount null", ursa.get("amount"), None)
    check("Ursa Major: currency null", ursa.get("currency"), None)
    check("Ursa Major: type null", ursa.get("type"), None)

    # ------------------------------------------------------------------ controls
    print("\nThe surrounding value contract is untouched:")
    for phrase, label in (
        ("The `value_observations` key is required on every transaction element",
         "key still required"),
        ("Return an empty array", "empty-array rule intact"),
        ("Do not infer a value merely to populate the array", "no-inference rule intact"),
        ("Return one item per distinct deal-value fact", "one-item-per-fact intact"),
        ("Do not collapse facts merely because amount, currency, or source are identical",
         "do-not-collapse intact"),
        ("$210 million 2025 net sales", "target_financials routing example intact"),
        ("ONE ECONOMIC FACT, ONE OBSERVATION", "0.26 currency block intact"),
        ("BUY-SIDE COHERENCE", "0.25 coherence intact"),
        ("MULTIPLE BUYERS", "0.27 buyers rule intact"),
        ('Use basis="STATED"', "basis guidance intact"),
    ):
        check(label, phrase in flat, True)
    check("the EQUITY_VALUE exclusion of market caps survives",
          "A market capitalization is not an EQUITY_VALUE" in flat, True)
    check("no new value type was introduced",
          sorted(hc._VALID_VALUE_TYPES),
          ["ENTERPRISE_VALUE", "EQUITY_VALUE", "MARKET_CAPITALIZATION",
           "TRANSACTION_VALUE", "UNDISCLOSED"])

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, hc._VERSION, "0.28")
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        sys.exit(1)
    print(f"{PASS} — only supported deal-value facts are captured; retirement costs an "
          f"observation, never an extraction")


if __name__ == "__main__":
    main()
