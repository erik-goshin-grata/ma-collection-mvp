#!/usr/bin/env python3
"""Regression tests for removing MINORITY_INVESTMENT from core classification.

No LLM calls. These tests exercise the deterministic classifier validator, the
relevancy reason-code validator, and the aggregation flag foundation together.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stages.aggregate import _derive_flags
from stages.deal_type_classify import _validate
from stages.relevancy_filter import _normalize_reason_code

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_PATH = os.path.join(ROOT, "prompts", "deal_type_classifier.md")


BASE_RESULT = {
    "v2_event_type": "ACQUISITION",
    "deal_type": "ACQUISITION",
    "spin_split_type": None,
    "distribution_mechanism": None,
    "recap_type": None,
    "target_type": "standalone_company",
    "event_history_type": "ANNOUNCED",
    "target_status": "PRIVATE",
    "overrides_relevancy_hint": False,
    "model_confidence": "HIGH",
    "notes": None,
    "prompt_version": "deal_type_classifier:0.8",
}


def _result(event_type: str) -> dict:
    out = dict(BASE_RESULT)
    out["v2_event_type"] = event_type
    out["deal_type"] = event_type
    return out


def _assert_equal(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _test_classifier_accepts_underlying_core_types(failures: list[str]) -> None:
    for event_type in ("ACQUISITION", "GROWTH_EQUITY", "VC_ROUND", "UNKNOWN"):
        _assert_equal(
            failures,
            f"classifier accepts {event_type}",
            _validate(_result(event_type)),
            None,
        )


def _test_classifier_rejects_minority_core_type(failures: list[str]) -> None:
    err = _validate(_result("MINORITY_INVESTMENT"))
    if err != "invalid v2_event_type: 'MINORITY_INVESTMENT'":
        failures.append(f"classifier rejects MINORITY_INVESTMENT: unexpected error {err!r}")

    mixed = _result("ACQUISITION")
    mixed["v2_event_type"] = "MINORITY_INVESTMENT"
    err = _validate(mixed)
    if err != "invalid v2_event_type: 'MINORITY_INVESTMENT'":
        failures.append(f"classifier rejects invalid v2_event_type even with valid deal_type: {err!r}")


def _test_relevancy_preserves_minority_reason_code(failures: list[str]) -> None:
    _assert_equal(
        failures,
        "relevancy direct minority reason",
        _normalize_reason_code("MINORITY_INVESTMENT", "RELEVANT"),
        "MINORITY_INVESTMENT",
    )


def _test_minority_flag_coexists_with_core_types(failures: list[str]) -> None:
    cases = [
        ("minority_secondary_acquisition", "ACQUISITION", 35.0),
        ("minority_growth_investment", "GROWTH_EQUITY", 20.0),
        ("minority_vc_transaction", "VC_ROUND", 12.5),
    ]
    for name, event_type, pct in cases:
        flags = _derive_flags({"v2_event_type": event_type, "deal_type": event_type, "pct_acquired": pct})
        _assert_equal(failures, name, flags["is_minority"], 1)

    for event_type in ("GROWTH_EQUITY", "VC_ROUND"):
        flags = _derive_flags({"v2_event_type": event_type, "deal_type": event_type, "pct_acquired": None})
        _assert_equal(failures, f"{event_type} does not imply minority without pct", flags["is_minority"], 0)


def _test_prompt_public_pipe_routing_language(failures: list[str]) -> None:
    text = open(PROMPT_PATH, encoding="utf-8").read()
    required = [
        "GROWTH_EQUITY only when the source supports genuine growth/private",
        "VC_ROUND only when the source supports",
        "A public-company\n  PIPE, primary share issuance, registered direct offering",
        "not automatically GROWTH_EQUITY or VC_ROUND merely\n  because it is primary capital",
        # Prompt 0.8 gave PIPE its own type, so the fallback sentence changed shape. The
        # guard's intent is unchanged and is what matters: public-company primary capital
        # must never be swept into a funding type. What moved is only where a recognized
        # PIPE goes — to PIPE now, instead of to UNKNOWN.
        "the source explicitly identifies the\n  structure as a PIPE",
        "and whenever no supported core type\n  clearly fits, use UNKNOWN with notes",
    ]
    for snippet in required:
        if snippet not in text:
            failures.append(f"prompt missing routing language: {snippet!r}")
    forbidden = "PIPE or other public-company primary capital is the closest\n  funding type"
    if forbidden in text:
        failures.append("prompt still forces PIPE/public primary capital toward funding type")
    # A recognized PIPE must not land back in UNKNOWN either: UNKNOWN satisfies Stage 4's
    # NOT IN gate, which is how PIPEs reached M&A extraction in the first place.
    if "use PIPE" not in text:
        failures.append("prompt no longer routes an explicitly identified PIPE to PIPE")


def _test_prompt_remaining_stake_routing_language(failures: list[str]) -> None:
    text = open(PROMPT_PATH, encoding="utf-8").read()
    required = [
        "Classify the current event based on what is being transacted now",
        "previously acquired an 80% controlling stake",
        "acquisition of the remaining 20%",
        "the current event is ACQUISITION",
        "not by changing the core event type or treating",
    ]
    for snippet in required:
        if snippet not in text:
            failures.append(f"prompt missing remaining-stake classifier language: {snippet!r}")


def main() -> int:
    failures: list[str] = []
    _test_classifier_accepts_underlying_core_types(failures)
    _test_classifier_rejects_minority_core_type(failures)
    _test_relevancy_preserves_minority_reason_code(failures)
    _test_minority_flag_coexists_with_core_types(failures)
    _test_prompt_public_pipe_routing_language(failures)
    _test_prompt_remaining_stake_routing_language(failures)

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS minority core classification: underlying routing, rejection, relevancy preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
