#!/usr/bin/env python3
"""Drift guard for the aggregation prompt's field vocabulary (prompt/stage parity).

`prompts/aggregation.md` is the only prompt that reasons about EVERY canonical field: Stage 9
calls it once per disputed field, iterating `_FIELDS`. It is also the prompt most likely to go
stale silently, because no slice that adds a field has any reason to open it -- which is what
happened. Six V3 slices shipped and this prompt still enumerated MERGER and REVERSE_MERGER as
event types, still lacked MARKET_CAPITALIZATION, and had never heard of combination_structure,
asset_type, offer_mechanism, deal_attitude, approach_type, sponsor_transaction_role or
round_price_direction. Every prompt/stage version pair was in parity the whole time.

That is the lesson this test encodes: VERSION PARITY PROVES PACKAGING, NOT SEMANTICS. It
answers "was the number bumped when the file changed", never "does the file agree with the
decisions". So this test asserts the vocabulary itself against the live stage enums, the way
scripts/test_reason_code_parity.py does for the relevancy pair -- and for the same reason, that
the drift it guards has already happened once.

Three things are pinned:

  1. PARITY.     Every value list in the prompt's marker-delimited block matches the frozenset
                 the owning stage validates against. Not a copied literal -- the stage enum is
                 imported and compared.
  2. NO RETIRED  MERGER and REVERSE_MERGER must not appear as event types, and `spinco` must
     VALUES.     not appear at all. They are read-tolerated on stored rows, which the block
                 states on a separate, clearly labelled legacy line -- never as valid output.
  3. COVERAGE.   Every enum-valued string field that can actually escalate through
                 `_pick_value` is either in the block or on the reasoned exclusion list below.
                 This is the assertion that makes the NEXT field's omission fail loudly.

Scope note. `_pick_value` skips null observations before tiering, and treats boolean 0 as
null-equivalent, so the prompt is only ever asked to choose between two or more NON-NULL values
of one field. It therefore needs value sets and tie-breaks, and deliberately not extraction
rules, evidence bars or null policy -- those belong to the owning prompts and are tested there.

Run from project root:
    python scripts/test_aggregation_vocabulary_parity.py
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import stages.aggregate as aggregate
import stages.deal_type_classify as dtc
import stages.funding_hc_extract as fhc
import stages.high_confidence_extract as hc
import stages.low_confidence_extract as lc

PROMPT = os.path.join(ROOT, "prompts", "aggregation.md")

# prompt label -> the frozenset the owning stage validates against.
EXPECTED = {
    "v2_event_type": dtc._VALID_V2_EVENT_TYPES,
    "combination_structure": dtc._VALID_COMBINATION_STRUCTURE,
    "target_type": dtc._VALID_TARGET_TYPES_V2,
    "asset_type": hc._VALID_ASSET_TYPES,
    "offer_mechanism": hc._VALID_OFFER_MECHANISM,
    "sponsor_transaction_role": hc._VALID_SPONSOR_TRANSACTION_ROLE,
    "value_type": hc._VALID_VALUE_TYPES,
    "acquirer_type": hc._VALID_ACQUIRER_TYPES_V2,
    "deal_attitude": lc._VALID_DEAL_ATTITUDE,
    "approach_type": lc._VALID_APPROACH_TYPE,
    "consideration_components.form": lc._VALID_CONSIDERATION_FORMS,
    "round_price_direction": fhc._VALID_ROUND_PRICE_DIRECTION,
}

# Enum-valued string fields deliberately NOT given their own line. Each reason is the test's
# real content: an exclusion without one is how a field goes missing on purpose and stays
# missing by accident.
EXCLUDED = {
    "deal_type": "transitional alias of v2_event_type — same vocabulary, stated once",
    "event_type": "legacy alias of event_history_type — same vocabulary, stated once",
    "target_type_v2": "normalized twin of target_type — same vocabulary",
    "acquirer_type_v2": "normalized twin of acquirer_type — same vocabulary",
    "spin_split_type_v2": "normalized twin of spin_split_type",
    "target_revenue_period_type_v2": "normalized twin of target_revenue_period_type",
    "target_ebitda_period_type_v2": "normalized twin of target_ebitda_period_type",
    "event_history_type": "listed in the prompt's existing context vocabulary",
    "target_revenue_period_type": "listed as period_type, which carries its own LTM/NTM rule",
    "target_ebitda_period_type": "listed as period_type, which carries its own LTM/NTM rule",
    "financials_disclosure_status": "listed in the prompt's existing context vocabulary",
    "announced_date_precision": "listed as date_precision",
    "closed_date_precision": "listed as date_precision",
    "signing_date_precision": "listed as date_precision",
    "spin_split_type": "plain enum: the resolver chooses among observed values, and the value "
                       "set adds no tie-break information beyond tier and confidence",
    "distribution_mechanism": "plain enum — see spin_split_type",
    "recap_type": "plain enum — see spin_split_type",
    "target_status": "plain enum — see spin_split_type",
    "stake_transition_type": "plain enum — see spin_split_type",
}

# Fields whose stage-side vocabulary lives in a frozenset, keyed by canonical field name.
_FIELD_ENUMS = {
    "v2_event_type": dtc._VALID_V2_EVENT_TYPES,
    "deal_type": dtc._VALID_V2_EVENT_TYPES,
    "event_history_type": dtc._VALID_EVENT_HISTORY_TYPES,
    "event_type": dtc._VALID_EVENT_HISTORY_TYPES,
    "combination_structure": dtc._VALID_COMBINATION_STRUCTURE,
    "spin_split_type": dtc._VALID_SPIN_SPLIT_TYPES,
    "spin_split_type_v2": dtc._VALID_SPIN_SPLIT_TYPES,
    "recap_type": dtc._VALID_RECAP_TYPES,
    "target_type": dtc._VALID_TARGET_TYPES_V2,
    "target_type_v2": dtc._VALID_TARGET_TYPES_V2,
    "target_status": dtc._VALID_TARGET_STATUSES,
    "distribution_mechanism": frozenset({"PRO_RATA", "EXCHANGE_OFFER"}),
    "acquirer_type": hc._VALID_ACQUIRER_TYPES_V2,
    "acquirer_type_v2": hc._VALID_ACQUIRER_TYPES_V2,
    "asset_type": hc._VALID_ASSET_TYPES,
    "value_type": hc._VALID_VALUE_TYPES,
    "offer_mechanism": hc._VALID_OFFER_MECHANISM,
    "sponsor_transaction_role": hc._VALID_SPONSOR_TRANSACTION_ROLE,
    "stake_transition_type": hc._VALID_STAKE_TRANSITION_TYPES,
    "financials_disclosure_status": hc._VALID_FINANCIALS_DISCLOSURE,
    "announced_date_precision": hc._VALID_DATE_PRECISIONS,
    "closed_date_precision": hc._VALID_DATE_PRECISIONS,
    "signing_date_precision": hc._VALID_DATE_PRECISIONS,
    "target_revenue_period_type": frozenset({"LTM", "NTM", "ANNUAL", "QUARTERLY", "INTERIM_YTD"}),
    "target_revenue_period_type_v2": frozenset({"LTM", "NTM", "ANNUAL", "QUARTERLY", "INTERIM_YTD"}),
    "target_ebitda_period_type": frozenset({"LTM", "NTM", "ANNUAL", "QUARTERLY", "INTERIM_YTD"}),
    "target_ebitda_period_type_v2": frozenset({"LTM", "NTM", "ANNUAL", "QUARTERLY", "INTERIM_YTD"}),
    "deal_attitude": lc._VALID_DEAL_ATTITUDE,
    "approach_type": lc._VALID_APPROACH_TYPE,
    "round_price_direction": fhc._VALID_ROUND_PRICE_DIRECTION,
}

# Values the owning stage still ACCEPTS but the prompt must no longer OFFER. A retired
# value stays in the stage frozenset deliberately -- _validate rejects a whole extraction
# on an unknown type, so delisting it there would turn a model still emitting it into a
# total loss -- but it must not appear as current output vocabulary. Subtracted from the
# expected set, and required on the legacy line instead.
RETIRED = {
    "value_type": hc._RETIRED_VALUE_TYPES,
}

_BLOCK_RE = re.compile(r"<!-- AGG_VOCAB_START.*?-->(.*?)<!-- AGG_VOCAB_END -->", re.S)
_LINE_RE = re.compile(r"^- ([A-Za-z0-9_.]+): (.+)$", re.M)   # digits matter: v2_event_type
_VERSION_RE = re.compile(r"^\*\*Version:\*\* (\d+)\.(\d+)", re.M)


def _parse_block(text: str, failures: list[str]) -> dict[str, set[str]]:
    m = _BLOCK_RE.search(text)
    if m is None:
        failures.append("prompt: no AGG_VOCAB_START/END block — the aggregation prompt has no "
                        "machine-checkable vocabulary, which is how it drifted six slices behind")
        return {}
    out = {}
    for label, values in _LINE_RE.findall(m.group(1)):
        out[label] = {v.strip().strip("`") for v in values.split("|") if v.strip()}
    return out


def main() -> None:
    failures: list[str] = []
    text = open(PROMPT, encoding="utf-8").read()

    # 1. Version parity + floor.
    vm = _VERSION_RE.search(text)
    if vm is None:
        failures.append("prompt: no parseable version line")
    else:
        got = f"{vm.group(1)}.{vm.group(2)}"
        if got != aggregate._VERSION:
            failures.append(f"version parity: prompt {got} vs stage {aggregate._VERSION}")
        if (int(vm.group(1)), int(vm.group(2))) < (0, 5):
            failures.append(f"prompt: version {got} predates the V3 vocabulary refresh (0.5)")

    declared = _parse_block(text, failures)

    # 2. Parity against the live stage enums.
    for label, expected in EXPECTED.items():
        if label not in declared:
            failures.append(f"block: {label} is missing")
            continue
        expected = set(expected) - RETIRED.get(label, set())
        got = declared[label]
        for extra in sorted(got - set(expected)):
            failures.append(f"{label}: prompt declares {extra!r}, which the owning stage rejects")
        for missing in sorted(set(expected) - got):
            failures.append(f"{label}: stage accepts {missing!r} but the prompt omits it")

    # 3. Retired values must not appear as current output vocabulary.
    for retired in ("MERGER", "REVERSE_MERGER", "MINORITY_INVESTMENT"):
        if retired in declared.get("v2_event_type", set()):
            failures.append(f"v2_event_type: {retired} is listed as a current event type — "
                            "V3 §T2/classifier 0.7 removed it; stored rows are read-tolerated "
                            "on the separate legacy line, never chosen for new output")
    # `consortium` is retired current vocabulary, not a deleted value. Check 2 already fails
    # if it returns to the acquirer_type line (the stage no longer accepts it); this pins the
    # other half -- that it stays READABLE on the legacy line. Dropping it from both would
    # leave stored rows with a value the prompt does not acknowledge at all.
    for retired in sorted(hc._RETIRED_VALUE_TYPES):
        if retired in declared.get("value_type", set()):
            failures.append(f"value_type: {retired} is listed as a current value type — HC 0.28 "
                            "retired it; a market cap is not a deal-value fact, and stored rows "
                            "are read-tolerated on the separate legacy line")
        if retired not in declared.get("legacy_read_only", set()):
            failures.append(f"legacy_read_only: {retired} should be listed as a read-tolerated "
                            "historical value")
    if "consortium" in declared.get("acquirer_type", set()):
        failures.append("acquirer_type: consortium is listed as a current buyer type — HC 0.27 "
                        "retired it; classification describes an individual firm, and stored "
                        "rows are read-tolerated on the separate legacy line")
    legacy = declared.get("legacy_read_only", set())
    for expected_legacy in ("MERGER", "REVERSE_MERGER", "MINORITY_INVESTMENT", "consortium"):
        if expected_legacy not in legacy:
            failures.append(f"legacy_read_only: {expected_legacy} should be listed as a "
                            "read-tolerated historical value")
    if "spinco" in text.split("## 9. Versioning")[0]:
        failures.append("prompt: `spinco` appears in the active body — V3 §T3 removed it")

    # 4. Coverage: every enum-valued escalating string field is declared or reasonably excluded.
    declared_fields = set(declared) | {"consideration_components"}
    for field, ftype in aggregate._FIELDS:
        if ftype != "string" or field not in _FIELD_ENUMS:
            continue
        if field in declared_fields or field in EXCLUDED:
            continue
        failures.append(f"coverage: {field} is an enum-valued field that can escalate through "
                        "_pick_value, but it is neither in the vocabulary block nor on the "
                        "reasoned exclusion list")
    for field in EXCLUDED:
        if field not in dict(aggregate._FIELDS):
            failures.append(f"coverage: {field} is excluded but no longer exists in _FIELDS — "
                            "stale exclusion")

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"PASS  aggregation vocabulary parity  ({len(EXPECTED)} vocabularies vs live stage "
          f"enums, {len(EXCLUDED)} reasoned exclusions, retired values absent, version parity)")


if __name__ == "__main__":
    main()
