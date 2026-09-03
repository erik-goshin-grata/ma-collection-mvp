"""Deterministic source-authority (tier) resolution.

Two independent things can establish a transaction source's authority tier:

1. A known document/ingestion identity. The acquisition path already knows
   what kind of thing this is -- a SEC regulatory/operative filing, for
   example -- without regard to whose "voice" it is in. Pass `known_tier`.
   SEC provenance alone does not make every SEC-hosted component T1: an
   operative EX-2.x agreement is regulatory/operative evidence (T1), but an
   EX-99.x that is the company's own press release is a first-party
   announcement, not operative evidence -- it resolves through
   `source_character`, not `known_tier` (see adapters/sec_api.py's
   `_classify_exhibit_99`).

2. Source CHARACTER -- whose voice a piece of content is in: the
   transaction participant's own substantive announcement, a thin firsthand
   record, credible third-party reporting, or a derivative rewrite. This is
   the one bounded vocabulary, shared by every path that knows its own
   character deterministically (a PR Newswire / Business Wire / GlobeNewswire
   subscribed issuer feed, an SEC press-release exhibit, a future
   portfolio/tombstone/company-news crawler) and by Relevancy, for generic
   discovered web sources where character cannot be established at
   ingestion. Pass `source_character`.

Known acquisition-path classification always takes precedence: `known_tier`
short-circuits before `source_character` is even consulted, so a document
identity we already have with certainty is never subordinated to an
unnecessary model inference.

This module is policy resolution only -- it does not classify domains, URLs,
or web content. URL-only site typing is a domain-registry problem, not
something a small bounded policy table should try to reproduce. The one
signal this module cannot get from a known path -- source character --
comes from the existing Relevancy content-reading gate, which already reads
full article text for its relevance decision.
"""

from __future__ import annotations

# The bounded source_character vocabulary. Narrow, mutually exclusive by
# design -- see prompts/relevancy_filter.md §4 SOURCE CHARACTER for the
# definitions delivered to an inferring caller (Relevancy). A deterministic
# caller (an adapter or crawler that already knows its own content) uses the
# same values without needing the definitions restated.
SOURCE_CHARACTER_VALUES: frozenset[str] = frozenset({
    "FIRST_PARTY_ANNOUNCEMENT",
    "THIN_FIRST_PARTY_RECORD",
    "ORIGINAL_REPORTING",
    "DERIVATIVE_REPORTING",
    "UNKNOWN",
})

# character -> tier. This is the only place this mapping is allowed to live;
# every caller (deterministic or inferred) resolves through it.
_CHARACTER_TIER: dict[str, str] = {
    "FIRST_PARTY_ANNOUNCEMENT": "T2",
    "THIN_FIRST_PARTY_RECORD": "T3",
    "ORIGINAL_REPORTING": "T3",
    "DERIVATIVE_REPORTING": "T4",
    # Conservative, explicitly documented fallback. Absence of evidence is
    # never promoted to authority -- UNKNOWN sits at the floor of the ladder,
    # the same T4 a confirmed derivative rewrite gets, not a safe middle tier.
    "UNKNOWN": "T4",
}


def resolve_tier(*, known_tier: str | None = None, source_character: str | None = None) -> str:
    """Resolve a source_raw row's authority tier.

    `known_tier` is for document identity an acquisition path already has
    with certainty -- e.g. a SEC regulatory/operative filing -- and always
    wins outright: `source_character` is not consulted when it is given.

    `source_character` resolves through the fixed character->tier table
    above, whether the value was declared deterministically by an ingestion
    path or inferred by Relevancy. A value outside `SOURCE_CHARACTER_VALUES`
    (including None) is treated as UNKNOWN -- never invents authority.
    """
    if known_tier is not None:
        return known_tier
    if source_character not in _CHARACTER_TIER:
        source_character = "UNKNOWN"
    return _CHARACTER_TIER[source_character]
