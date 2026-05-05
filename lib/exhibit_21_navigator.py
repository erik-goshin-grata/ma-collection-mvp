"""
Article-level structural navigator for Exhibit 2.1 (merger agreement) documents.

Pure Python — no LLM calls. Returns the same SectionMatch dataclass used by
lib/section_tagger.py so the Stage 10 insertion path is unchanged.

Emits only the five section types consumed by agreement_extract._SECTION_PROMPT_MAP:
    RECITALS | CONSIDERATION | CAPITALIZATION | CONDITIONS_TO_CLOSING | TERMINATION_FEES

Other articles (Definitions, Representations, Covenants, Miscellaneous, etc.) are
walked for boundary delimitation but produce no output rows.

Returns an empty list when no article structure is detected. Caller (Stage 10)
falls back to lib/section_tagger.tag_sections() in that case.

Design notes:
- The $ end-anchor in ARTICLE_ANCHOR_RE rejects cross-references mid-sentence
  ("ARTICLE II. The Merger shall...") and ToC pipe-table entries
  ("ARTICLE I | The Merger | 5") — both have content after the numeral on the line.
- ToC dedup: docs 8 and 9 in the 10-doc corpus have ARTICLE I at two positions
  (ToC ~0% then body ~1-4%). Taking the highest-position anchor per numeral
  consistently selects the body anchor for both duplicated and unduplicated cases.
- Arabic-schedule filter: docs 12 and 13 have Arabic articles 1-8 starting at 92%
  representing the appended corporate charter/bylaws, not the merger agreement.
  Any Arabic anchor appearing after the last Roman anchor is discarded.
- ~40% fallback rate is expected and by design; 4 of 10 existing corpus docs are
  non-navigable (German-law SPA, UK deed, ToC-only anchor doc, heading-text-only body).
"""
from __future__ import annotations

import re

from lib.section_tagger import SectionMatch

# Matches an ARTICLE N line with nothing after the numeral.
ARTICLE_ANCHOR_RE = re.compile(
    r"(?m)^[ \t]*ARTICLE\s+(?P<num>[IVXLCDM]+|\d+)[ \t]*$",
    re.IGNORECASE,
)

_WHEREAS_RE = re.compile(r"\bWHEREAS\b", re.IGNORECASE)
_NOW_THEREFORE_RE = re.compile(r"\bNOW[,\s]+THEREFORE\b", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)

# Heading-to-section-type mapping. Evaluated in order; first match wins.
# Patterns grounded in 10-document corpus audit (Drop 3.24 Change 1).
# Article-level TERMINATION headings reliably scope the termination-fee section
# in this corpus; no corpus doc separates termination and termination fees at the
# article level.
_HEADING_MAP: list[tuple[re.Pattern, str]] = [
    # CONSIDERATION — headings observed in the 6 navigable corpus docs
    (re.compile(r"\bCONVERSION\s+OF\s+(?:SHARES|SECURITIES|STOCK)\b", re.I), "CONSIDERATION"),
    (re.compile(r"\bEXCHANGE\s+OF\s+(?:SHARES|SECURITIES|STOCK|CERTIFICATES)\b", re.I), "CONSIDERATION"),
    (re.compile(r"\bEFFECT\s+OF\s+THE\s+MERGER\s+ON\s+CAPITAL\s+STOCK\b", re.I), "CONSIDERATION"),
    (re.compile(r"\bMERGER\s+CONSIDERATION\b", re.I), "CONSIDERATION"),
    (re.compile(r"\bTREATMENT\s+OF\s+(?:SHARES|SECURITIES|STOCK|EQUITY)\b", re.I), "CONSIDERATION"),
    # CAPITALIZATION — rare at article level per corpus audit
    (re.compile(r"\bCAPITALI[SZ]ATION\b", re.I), "CAPITALIZATION"),
    # CONDITIONS_TO_CLOSING
    (re.compile(r"\bCONDITIONS\s+(?:PRECEDENT|TO)\b", re.I), "CONDITIONS_TO_CLOSING"),
    # TERMINATION_FEES
    (re.compile(r"\bTERMINATION\b", re.I), "TERMINATION_FEES"),
]

_TOC_ONLY_PCT = 0.05  # anchors all within first 5% → ToC-only
_MIN_ANCHORS = 3       # fewer surviving anchors → structure too sparse to navigate


def _is_roman(num_str: str) -> bool:
    return bool(_ROMAN_RE.match(num_str))


def _next_nonblank_line(text: str, after_pos: int) -> str:
    """Return first non-blank line starting at after_pos."""
    for line in text[after_pos:].split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _map_heading(heading: str) -> str | None:
    """Return section_type for heading text, or None if outside the emit set."""
    for pat, stype in _HEADING_MAP:
        if pat.search(heading):
            return stype
    return None


def navigate_exhibit_21(raw_text: str) -> list[SectionMatch]:
    """Return SectionMatch objects for Exhibit 2.1 article-level sections.

    Emits only sections of the five types consumed by agreement_extract:
    RECITALS, CONSIDERATION, CAPITALIZATION, CONDITIONS_TO_CLOSING,
    TERMINATION_FEES. Other articles are walked for boundary delimitation
    but not emitted.

    Returns empty list when no article structure is detected; caller should
    fall back to the existing section tagger.
    """
    if not raw_text:
        return []

    doc_len = len(raw_text)

    # --- Collect all ARTICLE N anchors -----------------------------------
    raw_anchors: list[tuple[int, int, str]] = []
    for m in ARTICLE_ANCHOR_RE.finditer(raw_text):
        raw_anchors.append((m.start(), m.end(), m.group("num")))

    if not raw_anchors:
        return []

    # --- ToC dedup: per numeral, keep the highest-position anchor --------
    by_numeral: dict[str, tuple[int, int]] = {}  # num_str → (start, end)
    for start, end, num in raw_anchors:
        if num not in by_numeral or start > by_numeral[num][0]:
            by_numeral[num] = (start, end)

    # --- Arabic-schedule filter ------------------------------------------
    roman_positions = [pos for num, (pos, _) in by_numeral.items() if _is_roman(num)]
    if roman_positions:
        last_roman = max(roman_positions)
        by_numeral = {
            num: span
            for num, span in by_numeral.items()
            if _is_roman(num) or span[0] <= last_roman
        }

    # --- Fallback triggers -----------------------------------------------
    if not by_numeral:
        return []

    positions = [pos for pos, _ in by_numeral.values()]
    if all(pos / doc_len <= _TOC_ONLY_PCT for pos in positions):
        return []
    if len(by_numeral) < _MIN_ANCHORS:
        return []

    # --- Build sorted article list (by document position) ---------------
    anchors: list[tuple[int, int, str]] = sorted(
        [(pos, end, num) for num, (pos, end) in by_numeral.items()],
        key=lambda t: t[0],
    )

    articles: list[tuple[int, str, str]] = []  # (anchor_start, num, heading)
    for start, end, num in anchors:
        heading = _next_nonblank_line(raw_text, end)
        articles.append((start, num, heading))

    results: list[SectionMatch] = []

    # --- RECITALS: pre-Article-I region ----------------------------------
    # Requires both WHEREAS and NOW, THEREFORE to confirm this is a proper
    # recitals block and not a minimal preamble.
    first_anchor = articles[0][0]
    pre = raw_text[:first_anchor]
    whereas_m = _WHEREAS_RE.search(pre)
    nt_m = _NOW_THEREFORE_RE.search(pre)
    if whereas_m and nt_m:
        rec_start = whereas_m.start()
        results.append(SectionMatch(
            section_type="RECITALS",
            heading_text="RECITALS",
            excerpt_text=raw_text[rec_start:first_anchor],
            excerpt_start_offset=rec_start,
            excerpt_end_offset=first_anchor,
            confidence="HIGH",
        ))

    # --- Article sections: anchor_start → next anchor_start (or EOF) ----
    for i, (start, num, heading) in enumerate(articles):
        next_start = articles[i + 1][0] if i + 1 < len(articles) else doc_len
        section_type = _map_heading(heading)
        if section_type is None:
            continue
        results.append(SectionMatch(
            section_type=section_type,
            heading_text=heading[:200],
            excerpt_text=raw_text[start:next_start],
            excerpt_start_offset=start,
            excerpt_end_offset=next_start,
            confidence="HIGH",
        ))

    return results
