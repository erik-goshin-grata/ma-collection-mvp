#!/usr/bin/env python3
"""Regression guard for the Funding HC baseline fixtures.

No network and no model calls. This validates the FIXTURES, not the model — the model
run is `scripts/run_funding_hc_baseline.py` and needs credentials.

Its job is to stop the failure that already happened once in this branch: fixtures
written as tidy paraphrases that exercise the checker's own assumptions rather than the
real source. A synthetic rewrite of the Elektrik release that drops the "About Lead Edge
Capital" boilerplate removes the only adversarial content in it, and the fixture then
passes while the real article fails.

So the assertions below are mostly about the TEXT: every figure a fixture claims to trap
must actually appear in its `clean_text`, and every expected round size must appear
there too. A fixture cannot claim to test something its text does not contain.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.funding_hc_baseline_fixtures import (  # noqa: E402
    DISCARDED_CLASSES, ECONOMIC_CLASSES, FIXTURES, fixture,
)

FUNDING_TYPES = {"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"}


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _mentions(text: str, amount: float) -> bool:
    """Does the text state this amount in any ordinary press form?"""
    forms = []
    if amount >= 1e9:
        b = amount / 1e9
        forms += [f"{b:.10g} billion", f"${b:.10g}B"]
    if amount >= 1e6:
        m = amount / 1e6
        forms += [f"{m:.10g} million", f"${m:.10g}M"]
    forms.append(f"{int(amount):,}")
    low = text.lower()
    return any(f.lower().lstrip("$") in low for f in forms)


def main() -> None:
    failures: list[str] = []

    _check(failures, "fixture count", len(FIXTURES), 8)
    labels = [f["label"] for f in FIXTURES]
    _check(failures, "labels are unique", len(set(labels)), len(labels))

    # The structural finding this whole exercise turns on: four economic classes have a
    # home in the 0.1 schema and one does not.
    # AUM has no field because it is correctly DISCARDED, not because one is missing.
    # The baseline proved round.size is not contaminated without it, so calling this a
    # structural gap overstated the finding.
    homeless = [k for k, v in ECONOMIC_CLASSES.items() if v is None]
    _check(failures, "the only class without a field is the discarded one",
           set(homeless), DISCARDED_CLASSES)
    for cls in ("round size", "valuation", "cumulative funding", "investor check"):
        if not ECONOMIC_CLASSES.get(cls):
            failures.append(f"{cls!r} should have a schema home and does not")

    for f in FIXTURES:
        label = f["label"]
        if f["v2_event_type"] not in FUNDING_TYPES:
            failures.append(f"{label}: event type {f['v2_event_type']!r} is not funding")
        for key in ("title", "clean_text", "why"):
            if not (f.get(key) or "").strip():
                failures.append(f"{label}: {key} is empty")

        text = f"{f['title']}\n{f['clean_text']}"

        # --- The anti-paraphrase guard -----------------------------------
        # Every trapped figure must really be in the text. A trap the source does not
        # contain is a fixture testing its own imagination.
        for trap in f["traps"]:
            if not _mentions(text, trap):
                failures.append(
                    f"{label}: claims to trap {trap:,.0f} but the text never states it — "
                    "the fixture has been paraphrased away from the real source"
                )

        expected_size = f["expected"].get("round.size")
        if isinstance(expected_size, (int, float)) and not _mentions(text, expected_size):
            failures.append(
                f"{label}: expects round.size {expected_size:,.0f} but the text never "
                "states it"
            )

        # A trap must never equal the answer, or the fixture is self-contradictory.
        for trap in f["traps"]:
            if isinstance(expected_size, (int, float)) and abs(trap - expected_size) < 0.5:
                failures.append(f"{label}: {trap:,.0f} is both the expected size and a trap")

        # No fixture declines to assert a size any more. The lower-bound case was the
        # only one, and the researcher-normalization convention resolved it: every
        # expectation in the set is now scored.
        if expected_size == "REPRESENTATION_GAP":
            failures.append(
                f"{label}: REPRESENTATION_GAP is no longer an expectation in this set — "
                "the qualified-anchor convention scores the lower-bound case"
            )

    # --- The specific adversarial content must survive verbatim ----------
    # These passages are the whole point of using real text; losing any of them silently
    # converts an adversarial fixture into an easy one.
    must_contain = {
        "elektrik_investor_aum": ["About Lead Edge Capital", "$9 billion", "growth equity firm"],
        "attotude_cumulative_trap": ["total funding to $143 million", "raised $52 million"],
        "cellares_check_inside_round": ["bringing the total Series D to $327 million",
                                        "made a $50 million"],
        "flutterwave_valuation_only": ["values the company at $3.2 billion", "Series E"],
        "aston_power_deal_totaling": ["totaling $20 million in new funding"],
        "chronograph_lower_bound": ["over $140 million"],
    }
    for label, needles in must_contain.items():
        text = fixture(label)["clean_text"]
        for needle in needles:
            if needle not in text:
                failures.append(f"{label}: verbatim passage missing — {needle!r}")

    # Elektrik's only figure lives in investor boilerplate; that is the trap.
    elektrik = fixture("elektrik_investor_aum")
    amounts = re.findall(r"\$[\d.]+\s*(?:billion|million)", elektrik["clean_text"])
    _check(failures, "elektrik states exactly one figure", len(amounts), 1)
    _check(failures, "elektrik expects no round size", elektrik["expected"]["round.size"], None)

    # Computomic is the no-amount control AND the disclosure-taxonomy discriminator.
    computomic = fixture("computomic_no_amount")
    if re.search(r"\$[\d.]", computomic["clean_text"]):
        failures.append("computomic fixture should contain no monetary figure at all")
    # UNKNOWN is correct precisely because the source is SILENT. If the text ever gained
    # an explicit non-disclosure phrase, the right answer would flip to UNDISCLOSED and
    # this fixture would stop testing what it claims to.
    _check(failures, "computomic expects UNKNOWN",
           computomic["expected"]["financials_disclosure_status"], "UNKNOWN")
    if re.search(r"not\s+(?:been\s+)?disclos|were\s+not\s+announc|undisclosed",
                 computomic["clean_text"], re.IGNORECASE):
        failures.append(
            "computomic text now states non-disclosure explicitly — the expectation must "
            "become UNDISCLOSED, since silence is what makes UNKNOWN correct"
        )

    # --- The qualified anchor -------------------------------------------
    # "over $140 million" is normalized to the stated anchor by researcher convention.
    # Three things have to hold together, and the fixture is worthless if any slips:
    #   the expectation is the stated number, not a rounded or invented one;
    #   140M is no longer listed as a trap, since it is now the answer;
    #   the `why` records that this is a convention about the RECORD, and names the
    #   shapes the convention deliberately does not cover, so the next person reading it
    #   cannot mistake a narrow decision for a general rule about qualified numbers.
    chrono = fixture("chronograph_lower_bound")
    _check(failures, "chronograph expects the stated anchor",
           chrono["expected"]["round.size"], 140_000_000)
    if 140_000_000 in chrono["traps"]:
        failures.append("chronograph still traps 140M, which is now its expected answer")
    why = chrono["why"].lower()
    for needle in ("convention", "range", "up to"):
        if needle not in why:
            failures.append(
                f"chronograph `why` must name {needle!r} — a normalization convention "
                "that does not state its own boundary will be read as a general rule"
            )
    if "unverified" not in why:
        failures.append(
            "chronograph `why` must mark the expectation UNVERIFIED against the live "
            "model: the 8/8 baseline ran while this case was unscored, so no run has "
            "ever checked it"
        )

    # The control must be unambiguous: one figure, and it is the answer.
    control = fixture("control_clean_series_b")
    _check(failures, "control has no traps", control["traps"], {})
    _check(failures, "control expects a size", control["expected"]["round.size"], 62_500_000)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print(f"PASS funding HC baseline fixtures: {len(FIXTURES)} real-text cases, "
          "traps verified present in source, disclosure taxonomy pinned")


if __name__ == "__main__":
    main()
