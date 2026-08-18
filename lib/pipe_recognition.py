"""Recognize an explicit PIPE and route it to a recognized-but-not-profiled exclusion.

A PIPE — private investment in public equity — is a real, identifiable structure that
this pipeline **recognizes but does not profile**. It is not an M&A event, and it is not
a venture or growth round; forcing it into either would put a public-company primary
raise under semantics built for a different thing.

WHY THIS EXISTS
---------------
Before this module, a PIPE had no seat. `prompts/deal_type_classifier.md` (0.7) tells
the classifier not to force a public-company primary raise into `VC_ROUND` or
`GROWTH_EQUITY` and to fall back to `UNKNOWN` — which is correct as far as it goes. But
Stage 4's gate is::

    WHERE se.status = 'CLASSIFIED'
      AND COALESCE(se.v2_event_type, se.deal_type) NOT IN ('VC_ROUND','GROWTH_EQUITY','VENTURE_DEBT')

`UNKNOWN` satisfies that `NOT IN`, so an `UNKNOWN` PIPE fell into **M&A high-confidence
extraction** and came out the far end as a transaction_record carrying M&A semantics.
Declining to call it a funding round routed it into the other family instead. That is
the noise this removes.

THE DESIGN, IN ONE LINE
-----------------------
Stage 3 stamps a recognized PIPE with `v2_event_type = 'PIPE'` and the terminal status
`RECOGNIZED_NOT_PROFILED`. Both extraction gates select `status = 'CLASSIFIED'`, so
neither sees the row — **without either gate naming PIPE at all**. Nothing downstream
changes: no HC extraction, no clustering, no Stage 9, therefore no `round_size`, no
`transaction_size`, no valuation, no transaction_record. The row stops at staging.

Provenance is preserved at every layer. `source_raw` is untouched, the
`staging_extraction` row is written normally with the full classifier result, and the
classifier's own verdict plus the quoted evidence are recorded under the `pipe_exclusion`
key in `notes`. The exclusion is therefore reviewable, and reversible.

PROMOTION
---------
If PIPEs become in-scope for profiling, the change is to stop stamping the terminal
status — the row then flows as `CLASSIFIED` like any other, and the only remaining
question is which extractor should own it. `PIPE_OVERRIDABLE_EVENT_TYPES` is the single
knob controlling which seats a PIPE may take, kept explicit for that reason.

TWO WAYS A ROW BECOMES A PIPE
-----------------------------
1. **The classifier says so.** `prompts/deal_type_classifier.md` 0.8 offers `PIPE` as a
   type, so the model can return it directly. That verdict is honoured.
2. **The deterministic recognizer says so**, for a row the classifier seated elsewhere.

Both land in the same terminal state, and the branch matters: `PIPE` is not in the
funding family, so a classifier-emitted `PIPE` left at status `CLASSIFIED` would satisfy
Stage 4's `NOT IN` gate and go straight into M&A extraction — the original leak,
reopened through the front door by the very change that was supposed to close it.

A classifier verdict is recorded with `corroborated`: whether the deterministic
recognizer independently found explicit PIPE language in the source. An uncorroborated
verdict is still honoured — the exclusion deletes nothing, the row keeps every field, and
it is trivially reversible — but the flag makes an over-classifying prompt findable
instead of letting it drop deals quietly. It is a review queue, not a second policy.

RECOGNITION IS DELIBERATELY NARROW
----------------------------------
Only an explicit PIPE counts: the acronym **bound** to a financing construction, or the
phrase spelled out. A private placement, a convertible note, a preferred issuance, a
registered direct offering — private capital into a public issuer is *not* a PIPE unless
the source says so. Over-recognition is the worse failure here: under-recognition leaves
a row where it already was, while over-recognition silently deletes a deal that is in
scope. Binding rather than proximity is the same discipline the funding coverage review
arrived at after four confirmed false positives from co-occurrence.
"""

from __future__ import annotations

import re

PIPE_EVENT_TYPE = "PIPE"

# Terminal status. Chosen so that both extraction gates — which select
# `status = 'CLASSIFIED'` — skip the row without either of them naming PIPE.
PIPE_EXCLUDED_STATUS = "RECOGNIZED_NOT_PROFILED"

# The seats a recognized PIPE may take.
#
# `UNKNOWN` is where the classifier prompt already sends a public-company primary raise.
# The funding family is the mis-seating this module exists to prevent.
#
# Structural types — ACQUISITION, MERGER, REVERSE_MERGER, SPIN_OFF, SPLIT_OFF,
# JOINT_VENTURE, RECAPITALIZATION — are **never** overridden, and that exclusion is
# load-bearing rather than cautious. "$150 million PIPE" is standard de-SPAC language:
# the PIPE is a financing component of a REVERSE_MERGER that is genuinely in scope, and
# an acquisition financed by a concurrent PIPE is still an acquisition. Taking those
# seats would delete deals rather than reduce noise.
PIPE_OVERRIDABLE_EVENT_TYPES = frozenset({
    "UNKNOWN", "VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT",
})

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# The acronym, case-SENSITIVE. "pipe financing" in lower case is a plumbing contract;
# the securities structure is always written PIPE. `\b` after PIPE also means PIPELINE
# never matches, since L is a word character.
#
# `<PIPE>` marks the token's position in the span under test.
_ACRONYM_BINDING = [
    re.compile(p) for p in (
        # The structure named as the thing being done.
        r"<PIPE>\s+(?:financing|offering|transaction|investment|placement|deal|"
        r"round|subscription|purchase\s+agreement|securities\s+purchase)",
        # The parties or instruments of the structure.
        r"<PIPE>\s+(?:investors?|purchasers?|participants?|shares?|units?|"
        r"proceeds|commitments?)",
        # "in a PIPE", "through a PIPE", "via a PIPE".
        r"(?:in|through|via|by\s+way\s+of)\s+(?:a\s+|the\s+)?<PIPE>\b",
        # "a $75 million PIPE", "the $150 million PIPE".
        r"(?:\$|US\$|€|£)\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|bn|million|mm|m)?\s+<PIPE>\b",
        # A defining parenthetical: ... ("PIPE") — always a securities definition.
        r"[(\[]\s*[\"'“‘]?<PIPE>[\"'”’]?\s*[)\]]",
    )
]

# The phrase spelled out. Self-identifying: no construction in English uses these six
# words in this order for anything else, so no binding requirement is added.
_EXPANDED = re.compile(
    r"private\s+investments?\s+in\s+public\s+equit(?:y|ies)", re.IGNORECASE,
)

# The bare token, located so its own span can be isolated for the binding test.
_ACRONYM_TOKEN = re.compile(r"\bPIPE\b")

# Exposed for the regression's control-character sweep. A `\b` written inside a non-raw
# string becomes a literal backspace and silently disables the pattern it sits in; that
# has already happened once in this repo and stood unnoticed because the classifier
# still produced the right answer for the wrong reason.
_PATTERN_GROUPS = {
    "_ACRONYM_BINDING": _ACRONYM_BINDING,
    "misc": [_EXPANDED, _ACRONYM_TOKEN],
}


def _own_span(sentence: str, matches: list[re.Match], i: int) -> str:
    """The text belonging to token i, with the token itself marked `<PIPE>`.

    Bounded by the neighbouring occurrences so a financing word attached to one mention
    cannot be read as binding another.
    """
    m = matches[i]
    lo = matches[i - 1].end() if i > 0 else 0
    hi = matches[i + 1].start() if i + 1 < len(matches) else len(sentence)
    return sentence[lo:m.start()] + "<PIPE>" + sentence[m.end():hi]


def recognize_pipe(title: str, clean_text: str) -> dict | None:
    """Is the source explicitly identifying a PIPE structure?

    -> {"form": "ACRONYM" | "EXPANDED", "matched": str, "evidence": str}, or None.

    `evidence` is the sentence the recognition rests on, so a human can check it. An
    exclusion nobody can review is an exclusion nobody can trust.
    """
    blob = f"{title or ''}\n{clean_text or ''}"
    sentences = [s.strip() for s in _SENT_SPLIT.split(blob[:14000]) if s.strip()]

    for sentence in sentences:
        expanded = _EXPANDED.search(sentence)
        if expanded:
            return {"form": "EXPANDED", "matched": expanded.group(0),
                    "evidence": sentence[:400]}

    for sentence in sentences:
        tokens = list(_ACRONYM_TOKEN.finditer(sentence))
        for i, _tok in enumerate(tokens):
            span = _own_span(sentence, tokens, i)
            if any(rx.search(span) for rx in _ACRONYM_BINDING):
                return {"form": "ACRONYM", "matched": "PIPE", "evidence": sentence[:400]}

    return None


_EXCLUSION_RULE = (
    "explicit PIPE structure — recognized, not profiled. No HC extraction, no "
    "clustering, no aggregation, so no round_size, transaction_size, valuation or "
    "transaction_record is derived."
)


def _excluded(classifier_verdict: str, form: str, matched: str | None,
              evidence: str | None, corroborated: bool) -> dict:
    return {
        "v2_event_type": PIPE_EVENT_TYPE,
        "status": PIPE_EXCLUDED_STATUS,
        "excluded": True,
        "provenance": {
            # What the classifier actually said. Kept so the exclusion is reviewable and
            # so a later promotion knows which seat the row came from.
            "classifier_v2_event_type": classifier_verdict,
            "recognition_form": form,
            "matched": matched,
            "evidence": evidence,
            "corroborated": corroborated,
            "rule": _EXCLUSION_RULE,
        },
    }


def _kept(v2_event_type: str | None) -> dict:
    return {"v2_event_type": v2_event_type, "status": "CLASSIFIED",
            "excluded": False, "provenance": None}


def resolve_classification(v2_event_type: str | None, title: str,
                           clean_text: str) -> dict:
    """Decide the row's event type and terminal status.

    -> {"v2_event_type", "status", "excluded": bool, "provenance": dict | None}

    Two ways in, one terminal state:

    * the classifier returned `PIPE` itself — honoured, and corroborated against the
      source text so an over-classifying prompt is findable;
    * the classifier seated the row somewhere a PIPE mis-occupies, and the source text
      explicitly identifies one.

    The recognizer is consulted **only** for that second group. That ordering is the
    safety property: a row the classifier called ACQUISITION or REVERSE_MERGER is never
    re-examined, so no amount of PIPE language anywhere in its body can take its seat.
    """
    # 1. The classifier's own verdict. Not in the funding family, so leaving this at
    #    status CLASSIFIED would route it into M&A extraction — the leak this module
    #    exists to close.
    if v2_event_type == PIPE_EVENT_TYPE:
        found = recognize_pipe(title, clean_text)
        return _excluded(
            PIPE_EVENT_TYPE,
            "CLASSIFIER",
            found["matched"] if found else None,
            found["evidence"] if found else None,
            corroborated=found is not None,
        )

    # 2. A seat a PIPE mis-occupies, plus explicit language in the source.
    if v2_event_type not in PIPE_OVERRIDABLE_EVENT_TYPES:
        return _kept(v2_event_type)

    found = recognize_pipe(title, clean_text)
    if not found:
        return _kept(v2_event_type)

    return _excluded(v2_event_type, found["form"], found["matched"], found["evidence"],
                     corroborated=True)
