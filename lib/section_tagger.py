"""
Heuristic section tagger for SEC merger documents.

Pure Python — no LLM calls. Identifies standard sections in merger agreements,
proxy statements, tender offer documents, and registration statements by
matching heading-style text against known section patterns.

Section types:
    DEFINITIONS | RECITALS | CONSIDERATION | CAPITALIZATION |
    CONDITIONS_TO_CLOSING | TERMINATION_FEES | REPRESENTATIONS |
    BACKGROUND_OF_MERGER | FAIRNESS_OPINION | OTHER
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Ordered dict: type → list of regex patterns (any match → section of that type).
# Applied to heading text first (HIGH confidence), then to first 200 chars of
# section content (MEDIUM confidence).
_SECTION_PATTERNS: dict[str, list[str]] = {
    "RECITALS": [
        r"\bWHEREAS\b",
        r"^RECITALS\b",
        r"^BACKGROUND\s+OF\b",
    ],
    "DEFINITIONS": [
        r"^ARTICLE\s+(I|1)\b.*DEFINIT",
        r"^Section\s+1\.\d+\b.*Definition",
        r"^DEFINITIONS\b",
        r"^DEFINED\s+TERMS\b",
    ],
    "CONSIDERATION": [
        r"MERGER\s+CONSIDERATION",
        r"PER\s+SHARE\s+MERGER\s+CONSIDERATION",
        r"CONVERSION\s+OF\s+SECURITIES",
        r"EXCHANGE\s+RATIO",
        r"PURCHASE\s+PRICE",
        r"OFFER\s+CONSIDERATION",
        r"AGGREGATE\s+CONSIDERATION",
    ],
    "CAPITALIZATION": [
        r"CAPITALI[SZ]ATION",
        r"AUTHORIZED\s+AND\s+OUTSTANDING",
        r"AUTHORIZED\s+CAPITAL\s+STOCK",
        r"CAPITAL\s+STRUCTURE",
    ],
    "CONDITIONS_TO_CLOSING": [
        r"CONDITIONS\s+(PRECEDENT|TO\s+(THE\s+)?(MERGER|CLOSING|OBLIGATIONS))",
        r"OFFER\s+CONDITIONS",
        r"CONDITIONS\s+TO\s+(EACH\s+PARTY'?S?\s+)?OBLIGATIONS",
    ],
    "TERMINATION_FEES": [
        r"TERMINATION\s+FEE",
        r"COMPANY\s+TERMINATION\s+FEE",
        r"PARENT\s+TERMINATION\s+FEE",
        r"BREAK[- ]UP\s+FEE",
        r"Section\s+\d+\.\d+.*Termination\s+Fee",
    ],
    "REPRESENTATIONS": [
        r"REPRESENTATIONS\s+AND\s+WARRANTIES",
        r"REPS\s+AND\s+WARRANTIES",
    ],
    "BACKGROUND_OF_MERGER": [
        r"BACKGROUND\s+OF\s+(THE\s+)?(MERGER|TRANSACTION|OFFER|ACQUISITION)",
    ],
    "FAIRNESS_OPINION": [
        r"OPINION\s+OF\s+.{0,60}FINANCIAL\s+ADVISOR",
        r"FAIRNESS\s+OPINION",
        r"OPINION\s+OF\s+.{0,60}INVESTMENT\s+BANK",
    ],
}

# Compiled (type → list of re.Pattern)
_COMPILED: dict[str, list[re.Pattern]] = {
    stype: [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in pats]
    for stype, pats in _SECTION_PATTERNS.items()
}

# Heading detector: matches lines that look like section headings.
_HEADING_RE = re.compile(
    r"(?m)^[ \t]*("
    r"ARTICLE\s+[IVXLCDM\d]+\b"           # ARTICLE I, ARTICLE IV, ...
    r"|Section\s+\d+\.\d+"                 # Section 1.1, Section 8.02
    r"|\d+\.\s+[A-Z][A-Z\s]{3,}"           # "3.  REPRESENTATIONS..."
    r"|[A-Z][A-Z\s]{4,}"                   # ALL-CAPS line ≥ 5 chars
    r")[ \t]*$"
)


@dataclass
class SectionMatch:
    section_type: str
    heading_text: str
    excerpt_text: str
    excerpt_start_offset: int
    excerpt_end_offset: int
    confidence: str  # HIGH | MEDIUM | LOW


def tag_sections(raw_text: str) -> list[SectionMatch]:
    """Return SectionMatch objects for recognized sections in raw_text.

    Multiple matches of the same type are returned (all occurrences). Sections
    with no recognized type are silently skipped.
    """
    if not raw_text:
        return []

    # Find all heading positions
    heading_spans: list[tuple[int, int, str]] = []
    for m in _HEADING_RE.finditer(raw_text):
        heading_spans.append((m.start(), m.end(), m.group(1).strip()))

    if not heading_spans:
        return []

    # Sentinel at end of text
    heading_spans.append((len(raw_text), len(raw_text), ""))

    results: list[SectionMatch] = []

    for i, (h_start, h_end, h_text) in enumerate(heading_spans[:-1]):
        section_end = heading_spans[i + 1][0]
        # Don't emit zero-length or near-zero sections (two adjacent headings)
        if section_end - h_start < 10:
            continue

        excerpt = raw_text[h_start:section_end]

        matched_type: str | None = None
        confidence = "LOW"

        for stype, patterns in _COMPILED.items():
            # HIGH: pattern matches the heading text itself
            for pat in patterns:
                if pat.search(h_text):
                    matched_type = stype
                    confidence = "HIGH"
                    break
            if matched_type:
                break

            # MEDIUM: pattern matches in first 200 chars of excerpt (heading + start of body)
            if not matched_type:
                lead = excerpt[:200]
                for pat in patterns:
                    if pat.search(lead):
                        matched_type = stype
                        confidence = "MEDIUM"
                        break

            if matched_type:
                break

        if matched_type is None:
            continue

        results.append(SectionMatch(
            section_type=matched_type,
            heading_text=h_text[:200],
            excerpt_text=excerpt,
            excerpt_start_offset=h_start,
            excerpt_end_offset=section_end,
            confidence=confidence,
        ))

    return results
