#!/usr/bin/env python3
"""Adversarial baseline fixtures for Funding HC extraction — REAL source text.

No network and no model calls. This module is data plus expectations; the harness
that exercises a live model is `scripts/run_funding_hc_baseline.py`.

**Verbatim, not paraphrased.** Every `clean_text` below is the article text as supplied,
including the investor boilerplate ("About Lead Edge Capital") that carries a $9B AUM
figure and the trailing sentence that carries AttoTude's $143M cumulative total. Those
passages are the adversarial content: a paraphrase that tidies them away tests nothing.
An earlier classifier pass was validated against synthetic language written to match its
own patterns and reported clean while the real text broke it — these fixtures exist so
that cannot recur.

Five economic classes must stay separated. Four have their own schema field in
funding_hc_extraction 0.1; the fifth is correctly **discarded**:

    round size          -> round.size
    valuation           -> round.pre_money_valuation / post_money_valuation
    cumulative funding  -> round.total_raised_to_date
    investor check      -> investors[].investment_amount
    investor/fund AUM   -> none, and none is needed

**AUM having no field is not a gap.** The baseline established that the prompt ignores an
investor's own size rather than routing it into `round.size`, which is the only thing
funding-magnitude extraction needs from it. A field would only be required if AUM were
decided to belong in the data model as a fact worth keeping — a separate product
question about investor profiles, not a defect in magnitude extraction. Recording it as
a "structural gap" overstated the finding; the Elektrik fixture proves the opposite.

`expected` is the canonical outcome. `traps` names the figures in the text that must NOT
become `round.size`, so a harness can report *which* wrong number a failure picked up
rather than only that it was wrong.
"""

from __future__ import annotations

# Baseline result 2026-08-17: 8/8 on the economic semantics. That run scored seven
# fixtures; Chronograph was carried as an unscored representation limitation.
#
# On 2026-08-18 the qualified-anchor convention resolved it, so Chronograph now carries a
# scored expectation of 140,000,000. NOTE: that expectation has never been run against
# the live model — the 8/8 result predates it. The next baseline run may report 8/8 or
# 7/8, and either outcome is information rather than a regression.
#
# Each fixture: label, event type, published_date, title, clean_text, expected, traps.
FIXTURES = [
    {
        "label": "cellares_check_inside_round",
        "v2_event_type": "GROWTH_EQUITY",
        "published_date": "2026-06-15",
        "title": "Cellares Announces $50 Million Growth Equity Investment from Prime Radiant Fund",
        "clean_text": (
            "Cellares, the Integrated Development and Manufacturing Organization (IDMO) "
            "for cell therapies, today announced that Prime Radiant Fund, a growth equity "
            "fund, has made a $50 million growth equity investment in the company's "
            "Series D financing, bringing the total Series D to $327 million. The "
            "investment will support the expansion of Cellares' Smart Factory network."
        ),
        "expected": {
            "round.size": 327_000_000,
            "round.label": "Series D",
            "round.total_raised_to_date": None,
            "round.post_money_valuation": None,
            "investors[Prime Radiant Fund].investment_amount": 50_000_000,
        },
        "traps": {
            50_000_000: "Prime Radiant's CHECK — belongs on the investor, not round.size",
        },
        "why": (
            "The check and the round appear in one sentence and both are real. Only the "
            "round is the event's magnitude. This is the case that proves the two are "
            "separate facts rather than competing candidates."
        ),
    },
    {
        "label": "attotude_cumulative_trap",
        "v2_event_type": "VC_ROUND",
        "published_date": "2026-06-10",
        "title": "AttoTude Raises $52 Million in Series C Funding",
        "clean_text": (
            "AttoTude™ Inc., the pioneer of groundbreaking interconnect technology for "
            "AI infrastructure that uniquely enables the transmission of electrical signals "
            "directly over Dielectric fiber (i.e. ASIC over Dielectric), today announced it "
            "has raised $52 million in Series C funding led by The Westly Group. The Westly "
            "Group was joined in this funding round by new strategic investors Keysight, "
            "Allegis Capital, and DNX, with participation from existing investors including "
            "Sutter Hill Ventures, Mayfield, Canaan, and Wing Venture Capital. This latest "
            "investment brings AttoTude's total funding to $143 million."
        ),
        "expected": {
            "round.size": 52_000_000,
            "round.label": "Series C",
            "round.total_raised_to_date": 143_000_000,
            "investors[The Westly Group].is_lead": True,
        },
        "traps": {
            143_000_000: "CUMULATIVE funding to date — belongs in total_raised_to_date",
        },
        "why": (
            "The cumulative figure has its own field, so this tests whether the model "
            "routes it there rather than confusing it with the current round."
        ),
    },
    {
        "label": "aston_power_deal_totaling",
        "v2_event_type": "VC_ROUND",
        "published_date": "2026-06-12",
        "title": "Aston Power Partners with TDK and JLL",
        "clean_text": (
            "Aston Power, a fast growing company that provides reliable, integrated "
            "large-scale power delivery to data centers, today has partnered with two of "
            "the largest companies in the AI physical infrastructure boom, TDK and JLL. As "
            "part of this partnership, the companies' corporate venture arms, TDK Ventures "
            "and JLL Spark Global Ventures made strategic equity investments. The deal, "
            "totaling $20 million in new funding, was led by TDK Ventures and Building "
            "Ventures with participation from JLL Spark Global Ventures, and others."
        ),
        "expected": {
            "round.size": 20_000_000,
            "round.total_raised_to_date": None,
            "investors[TDK Ventures].is_lead": True,
        },
        "traps": {},
        "why": (
            "No round label and no 'raised' verb — the magnitude is carried by 'the deal, "
            "totaling $20 million in new funding'. Tests that an unlabelled round is still "
            "recognised, and that the absent label does not suppress the size."
        ),
    },
    {
        "label": "flutterwave_valuation_only",
        "v2_event_type": "GROWTH_EQUITY",
        "published_date": "2026-06-18",
        "title": "Flutterwave Announces Strategic Investment from Ripple",
        "clean_text": (
            "Flutterwave, Africa's leading payments infrastructure company, today announces "
            "a strategic investment from Ripple, the leading provider of blockchain-based "
            "enterprise solutions for traditional and digital finance. This partnership "
            "marks the definitive next phase of Flutterwave's long-term stablecoin "
            "strategy, seamlessly connecting its existing cross-border settlement "
            "capabilities with enterprise-grade digital liquidity. The investment is a part "
            "of Flutterwave's Series E fundraising, which values the company at $3.2 "
            "billion, reflecting deep institutional alignment with the company's strong "
            "financial fundamentals and long-term value proposition."
        ),
        "expected": {
            "round.size": None,
            "round.label": "Series E",
            "round.post_money_valuation": 3_200_000_000,
        },
        "traps": {
            3_200_000_000: "a VALUATION — belongs in post_money_valuation, never round.size",
        },
        "why": (
            "A Series E is named and a figure is present, but the figure values the "
            "COMPANY, not the raise. The round size is genuinely undisclosed. Note the "
            "verb is 'values', not 'valued at' or 'post-money' — the phrasing a "
            "valuation-detector is most likely to miss."
        ),
    },
    {
        "label": "elektrik_investor_aum",
        "v2_event_type": "GROWTH_EQUITY",
        "published_date": "2026-06-20",
        "title": "Elektrik Announces New Investment from Lead Edge Capital",
        "clean_text": (
            "Elektrik, the leading procurement platform for sourcing critical electrical "
            "infrastructure components, today announced a new investment from Lead Edge "
            "Capital, a growth equity firm. Elektrik is a tech-enabled distributor of "
            "electrical T&D components that simplifies procurement for the contractors and "
            "distributors who build data centers, renewable energy projects, and commercial "
            "& industrial infrastructure.\n\nAbout Lead Edge Capital\n"
            "Lead Edge Capital is a $9 billion growth equity firm investing in software, "
            "internet, and tech-enabled businesses globally. The firm has invested in a "
            "number of major software and internet companies around the world, including "
            "Alibaba Group, Arrive Logistics, Asana, Azul Systems, Bazaarvoice, Clearscore, "
            "Duo Security, Grafana, Holistiplan, LiveView Technologies, SafeSend, Toast, "
            "Wise, and Yousign."
        ),
        "expected": {
            "round.size": None,
            "round.total_raised_to_date": None,
            "round.post_money_valuation": None,
        },
        "traps": {
            9_000_000_000: (
                "the INVESTOR's firm size / AUM, from boilerplate. Correctly discarded — "
                "it is a property of the investor, not a magnitude of this financing."
            ),
        },
        "why": (
            "The only figure in the document belongs to the investor, in an 'About' "
            "boilerplate block. The round size is undisclosed. This is the fixture the "
            "schema cannot express: there is nowhere correct to put $9B, so the model must "
            "discard it rather than route it somewhere."
        ),
    },
    {
        "label": "computomic_no_amount",
        "v2_event_type": "GROWTH_EQUITY",
        "published_date": "2026-06-15",
        "title": "Computomic Announces Strategic Growth Investment from Washington Harbour Partners",
        "clean_text": (
            "Computomic, the leading global Databricks delivery partner, today announced a "
            "strategic growth investment from Washington Harbour Partners LP, a "
            "mission-first defense tech accelerator and force multiplier for U.S. and "
            "allied defense companies, during the Databricks Data + AI Summit. Computomic "
            "has established itself as a trusted partner for enterprise data modernization, "
            "large-scale legacy migrations, and AI transformation initiatives across some "
            "of the world's most complex and highly regulated industries."
        ),
        "expected": {
            "round.size": None,
            "round.total_raised_to_date": None,
            # UNKNOWN, not UNDISCLOSED. Both prompts define the pair the same way:
            # UNDISCLOSED means the source EXPLICITLY says terms were not disclosed;
            # UNKNOWN means it is SILENT on financials, neither stating nor denying.
            # This release never mentions terms at all, so it is silent. An earlier
            # version of this fixture expected UNDISCLOSED and was simply wrong about
            # the taxonomy — the model was right.
            "financials_disclosure_status": "UNKNOWN",
        },
        "traps": {},
        "why": (
            "An announced event whose source is silent on financials. Null round size is "
            "the correct canonical state, not a gap, and this is the control for whether "
            "the model invents a figure when none exists. It is also the discriminator "
            "for the disclosure pair: silence is UNKNOWN; only an explicit "
            "'terms were not disclosed' is UNDISCLOSED."
        ),
    },
    {
        "label": "chronograph_lower_bound",
        "v2_event_type": "GROWTH_EQUITY",
        "published_date": "2026-06-14",
        "title": "Chronograph Announces Minority Growth Equity Investment",
        "clean_text": (
            "Chronograph, the leading provider of portfolio monitoring and data management "
            "solutions for private capital investors, today announced a minority growth "
            "equity investment of over $140 million led by a global investment firm. The "
            "investment will accelerate Chronograph's product roadmap."
        ),
        "expected": {
            # The stated anchor, by researcher-normalization convention. See `why`.
            "round.size": 140_000_000,
        },
        # 140M is no longer a trap: it is the answer. The traps dict names figures that
        # must NOT become round.size, and this fixture no longer has one.
        "traps": {},
        "why": (
            "Economically this IS the funding-event magnitude — unlike the valuation and "
            "AUM traps, the figure is about this raise. What was in doubt was only how to "
            "record it: `round.size` is a bare number with no qualifier field anywhere in "
            "the stack, so 'over $140M' and a stated $140M are indistinguishable once "
            "written. Rather than build qualifier infrastructure for one row, a "
            "researcher-normalization CONVENTION was adopted: a single stated figure "
            "under a lower-bound qualifier is recorded at its stated anchor, here "
            "140,000,000, with the original 'over $140 million' wording preserved in the "
            "source text and the remediation note. Recording the anchor is a normalization "
            "of the record, not a claim that the source stated the figure exactly. "
            "The convention is deliberately narrow and covers a SINGLE stated anchor only "
            "— a range ('$120-150M'), an 'up to' ceiling, and a rumoured figure are all "
            "still undecided and still leave round.size null; each is to be settled from "
            "a real example rather than in advance. "
            "UNVERIFIED against the live model: the 8/8 baseline ran while this "
            "expectation was unscored, so no run has yet checked whether the model "
            "returns the anchor."
        ),
    },
    {
        "label": "control_clean_series_b",
        "v2_event_type": "VC_ROUND",
        "published_date": "2026-06-11",
        "title": "Respond.io Raises $62.5 Million Series B",
        "clean_text": (
            "Respond.io, the AI-powered customer conversation management platform, today "
            "announced it has raised $62.5 million in its Series B funding round. The round "
            "was led by an existing investor with participation from new backers."
        ),
        "expected": {
            "round.size": 62_500_000,
            "round.label": "Series B",
            "round.total_raised_to_date": None,
            "round.post_money_valuation": None,
        },
        "traps": {},
        "why": (
            "The unambiguous control. A failure here means the baseline is broken rather "
            "than the adversarial cases being hard."
        ),
    },
]

# The five classes and where each belongs. The last has no home.
# Where each class belongs. AUM maps to None because it is correctly DISCARDED, not
# because something is missing — see the module docstring.
ECONOMIC_CLASSES = {
    "round size": "round.size",
    "valuation": "round.pre_money_valuation / round.post_money_valuation",
    "cumulative funding": "round.total_raised_to_date",
    "investor check": "investors[].investment_amount",
    "investor / fund AUM": None,
}
DISCARDED_CLASSES = {"investor / fund AUM"}


def fixture(label: str) -> dict:
    for f in FIXTURES:
        if f["label"] == label:
            return f
    raise KeyError(label)
