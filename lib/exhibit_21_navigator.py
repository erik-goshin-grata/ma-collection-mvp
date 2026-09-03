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

import logging
import re

from lib.section_tagger import SectionMatch

_log = logging.getLogger(__name__)

# Matches an ARTICLE N line with nothing after the numeral.
ARTICLE_ANCHOR_RE = re.compile(
    r"(?m)^[ \t]*ARTICLE\s+(?P<num>[IVXLCDM]+|\d+)[ \t]*$",
    re.IGNORECASE,
)

_WHEREAS_RE = re.compile(r"\bWHEREAS\b", re.IGNORECASE)
_NOW_THEREFORE_RE = re.compile(r"\bNOW[,\s]+THEREFORE\b", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)

# Matches the "dated [as of] <Month> <Day>, <Year>" phrase that sits inside the
# preamble sentence immediately preceding the recitals in every corpus doc
# (e.g. "dated as of August 25, 2026" / "dated August 26, 2026"). Used to locate
# that preamble so the RECITALS excerpt carries the agreement title, date, and
# opening party/defined-term clause instead of starting cold at the first
# WHEREAS. Bounded lookback below keeps this from ever pulling in a large
# chunk of preceding document (e.g. a Table of Contents).
_PREAMBLE_DATE_RE = re.compile(
    r"\bdated\s+(?:as\s+of\s+)?[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}", re.IGNORECASE
)
_MAX_PREAMBLE_LOOKBACK = 1500  # chars; guards the "small evidence packet" requirement

# CONSIDERATION headings recognized at the ARTICLE level — an article whose own
# title uses one of these phrases (e.g. Aon's "MERGER CONSIDERATION" articles in
# other filings) can be classified whole. Kept as their own list, unchanged from
# the original 10-document corpus audit (Drop 3.24 Change 1), so article-level
# behavior does not shift when the subsection-level patterns below are added.
_CONSIDERATION_ARTICLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bCONVERSION\s+OF\s+(?:SHARES|SECURITIES|STOCK)\b", re.I),
    re.compile(r"\bEXCHANGE\s+OF\s+(?:SHARES|SECURITIES|STOCK|CERTIFICATES)\b", re.I),
    re.compile(r"\bEFFECT\s+OF\s+THE\s+MERGER\s+ON\s+CAPITAL\s+STOCK\b", re.I),
    re.compile(r"\bMERGER\s+CONSIDERATION\b", re.I),
    re.compile(r"\bTREATMENT\s+OF\s+(?:SHARES|SECURITIES|STOCK|EQUITY)\b", re.I),
]

# Heading-to-section-type mapping. Evaluated in order; first match wins.
# Article-level TERMINATION headings reliably scope the termination-fee section
# in this corpus; no corpus doc separates termination and termination fees at the
# article level.
_HEADING_MAP: list[tuple[re.Pattern, str]] = (
    [(p, "CONSIDERATION") for p in _CONSIDERATION_ARTICLE_PATTERNS]
    + [
        # CAPITALIZATION — rare at article level per corpus audit
        (re.compile(r"\bCAPITALI[SZ]ATION\b", re.I), "CAPITALIZATION"),
        # CONDITIONS_TO_CLOSING — "OF CLOSING" added (Step 5): Aon's Article
        # VII is titled "CONDITIONS OF CLOSING", not "...TO..." or
        # "...PRECEDENT...", and was matching neither alternative — the
        # article (mutual + Parent + Company conditions, 6,433 chars,
        # cleanly bounded) was present in the document but never detected at
        # all. Anchored specifically to "OF CLOSING" (not bare "OF") because
        # a bare "OF" alternative false-matched Velocity Financial's Article
        # II — whose captured "heading" is actually body prose ("...subject
        # to the conditions of this Agreement...", a pre-existing
        # _next_nonblank_line quirk on documents with no separate article
        # title line) — which cost that document its CONSIDERATION detection
        # (Article II is really its Purchase-and-Sale article) and produced a
        # bogus second CONDITIONS_TO_CLOSING packet. "OF CLOSING" doesn't
        # match that prose and doesn't reintroduce the false positive.
        (re.compile(r"\bCONDITIONS\s+(?:PRECEDENT|TO\b|OF\s+CLOSING)\b", re.I), "CONDITIONS_TO_CLOSING"),
        # TERMINATION_FEES
        (re.compile(r"\bTERMINATION\b", re.I), "TERMINATION_FEES"),
    ]
)

# Gates which articles get scanned for CONSIDERATION sub-sections (below). Real
# merger agreements routinely fold the actual per-share/per-unit consideration
# terms into a compound article titled just "The Merger" / "The Mergers;
# Closing" / "Effect of the Merger" rather than devoting the whole article to
# consideration — validated against Volato ("The Merger"), Victory Capital and
# BitGo ("The Merger(s); Closing"), Black Spade ("The Merger; Closing"), and Aon
# ("Effect of the Merger"). Deliberately does NOT match on "Consideration" or
# "Capital Stock" alone — those already fire the article-level check above.
# Negative lookahead (?!\s+SUB) on both alternatives: "Merger Sub" is a defined
# term whose own name starts with the word "Merger", so "REPRESENTATIONS AND
# WARRANTIES OF THE PARENT AND THE MERGER SUB" otherwise satisfies "THE MERGER"
# as a literal, word-bounded substring — exposed once the Capitalization V3
# alignment item-2 fix stopped that same article being claimed first by
# _COMPANY_REPS_HEADING_RE (Volato). Not a real merger-mechanics article; a
# reps-article heading that happens to contain the acquisition vehicle's name.
_MERGER_ARTICLE_HEADING_RE = re.compile(
    r"\bTHE\s+MERGERS?\b(?!\s+SUB)|\bEFFECTS?\s+OF\s+THE\s+MERGERS?\b(?!\s+SUB)", re.IGNORECASE
)

# CONSIDERATION headings recognized at the SUB-SECTION level, inside a
# _MERGER_ARTICLE_HEADING_RE-gated article. Includes the article-level phrases
# (they occur at sub-section level too, e.g. Volato's "Section 2.02 Merger
# Consideration") plus three additional phrasings validated against the corpus
# but too permissive to add to the article-level list above:
#   - bare "Consideration" as the whole heading (BitGo: "Section 2.03.Consideration")
#   - "Treatment of ... Options/Warrants" (Volato: "Treatment of Aligned Options
#     and Aligned Warrants") — the article-level TREATMENT_OF pattern only covers
#     Shares/Securities/Stock/Equity, not the awards side of the cap table
#   - "Effect(s) of the Merger on ... Share Capital" (Black Spade: Cayman-law
#     agreements say "Share Capital" where Delaware agreements say "Capital Stock")
_CONSIDERATION_SUBSECTION_PATTERNS: list[re.Pattern] = _CONSIDERATION_ARTICLE_PATTERNS + [
    re.compile(r"^\s*CONSIDERATION\s*$", re.I),
    re.compile(r"\bTREATMENT\s+OF\b.{0,40}\b(?:OPTIONS|WARRANTS)\b", re.I),
    re.compile(r"\bEFFECTS?\s+OF\s+THE\s+MERGERS?\s+ON\s+(?:THE\s+)?SHARE\s+CAPITAL\b", re.I),
]

# Gates which articles get scanned for purchase-agreement consideration
# sub-sections (Step 3B). Validated against Sangamo ("PURCHASE AND SALE" —
# asset purchase) and Velocity Financial ("Purchase and Sale of Purchased
# Interests..." — equity purchase); both are bare-titled articles distinct
# from the merger-mechanics gate above.
_PURCHASE_ARTICLE_HEADING_RE = re.compile(r"\bPURCHASE\s+AND\s+SALE\b", re.IGNORECASE)

# CONSIDERATION headings recognized at the SUB-SECTION level inside a
# _PURCHASE_ARTICLE_HEADING_RE-gated article. Two families, both required per
# the task: what is being purchased (so the price means something) and the
# price formula itself. Validated against Sangamo ("Purchase and Sale of
# Assets" / "Purchase Price") and Velocity Financial ("Purchase and Sale of
# Purchased Interests" / "Purchase Price"). The negative lookahead on
# "Adjustment" excludes the separate post-closing adjustment-calculation
# sub-section (Velocity's "Purchase Price Adjustment") — that's mechanics for
# computing a true-up, not part of establishing what's being bought or the
# base price formula, so it's deliberately not included here.
_PURCHASE_SUBSECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bPURCHASE\s+AND\s+SALE\s+OF\b", re.IGNORECASE),
    re.compile(r"\b(?:Calculation\s+of\s+|Base\s+)?Purchase\s+Price\b(?!\s+Adjustment)", re.IGNORECASE),
]

# Economic defined terms whose value/formula a selected consideration
# sub-section may reference by name without restating (Step 3A). Narrow and
# specific on purpose — this is not general Definitions-article parsing, only
# lookup for these named terms when a selected sub-section actually uses them.
_ECONOMIC_TERMS: tuple[str, ...] = (
    "Per Share Merger Consideration",
    "Merger Consideration",
    "Cash Consideration",
    "Stock Consideration",
    "Exchange Ratio",
    "Base Purchase Price",
    "Purchase Price",
)

# Sub-section heading line: an optional "Section" prefix, a decimal numeral, then
# the heading text up to the next period/semicolon/newline. Matches every
# rendering style seen in the corpus: "Section 2.02 Merger Consideration.",
# "Section 2.03.Consideration" (period glued, no space), "1.04 Determination of
# Final Merger Consideration." (no "Section" prefix).
_SUBSECTION_HEADING_LINE_RE = re.compile(
    r"(?m)^[ \t]*(?:Section\s+)?\d+\.\d+\.?[ \t]*(?P<heading>[A-Z][^\n.;]{0,90})"
)

# Matches the company's Representations and Warranties article heading.
# Negative lookahead excludes parent/buyer/seller/merger-sub variants so we only
# descend into the company reps article, not acquiror reps. Tolerates an
# intervening "THE" ("... OF THE PARENT AND THE MERGER SUB") — confirmed via
# Volato's actual Article IV heading, which the lookahead (anchored right after
# "OF ") did not previously exclude because "THE PARENT" does not start with
# the literal string "PARENT". This let Volato's Parent/Merger Sub Capitalization
# sub-section leak through as if it were the target's (Capitalization V3
# alignment review, item 2).
_COMPANY_REPS_HEADING_RE = re.compile(
    r"\bREPRESENTATIONS\s+AND\s+WARRANTIES\s+OF\s+"
    r"(?!(?:THE\s+)?(?:PARENT\b|BUYER\b|PURCHASER\b|SELLER\b|MERGER\s+SUB\b|ACQUI))",
    re.IGNORECASE,
)

# Three rendering patterns observed across corpus docs 8, 9, 10, 13:
#   "Section 3.02.Capitalization."
#   "3.2 Capitalization."
#   "Section 3.2 Capitalization."
# MULTILINE + line-anchor rejects inline cross-references
# ("the company's capitalization described in Section 3.2 Capitalization").
_CAPITALIZATION_SUBSECTION_RE = re.compile(
    r"(?m)^[ \t]*(?:Section\s+)?\d+\.\d+\.?\s*"
    r"(?:Capitaliz(?:ation)?|Capital\s+Structure)\.?",
    re.IGNORECASE,
)

# Matches the next sub-section heading to delimit the capitalization excerpt end.
# Requires the numeral to be followed by a capital letter or end-of-line so that
# cross-references like "(a)" after a numeral are not treated as headings.
_NEXT_SUBSECTION_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(?:Section\s+)?\d+\.\d+(?:[.\s][A-Z]|[ \t]*$)",
)

# Capitalization-only variant of the above (Step 4). Black Spade renders its
# next heading as "Section 4.07. Financial Statements." — a period AND a
# space between the numeral and the capital letter, two separator characters
# where _NEXT_SUBSECTION_HEADING_RE allows only one. That mismatch made the
# original regex find no next heading anywhere in the rest of the document,
# so the capitalization excerpt ran all the way to end-of-document (82,479
# characters — the entire remainder of the Representations and Warranties
# article and beyond). Kept as its own pattern, used only by
# _extract_capitalization_subsection, so Consideration's boundary-finding
# (which uses _NEXT_SUBSECTION_HEADING_RE directly and has shown no sign of
# this failure mode) is untouched.
_CAPITALIZATION_NEXT_HEADING_RE = re.compile(
    r"(?m)^[ \t]*(?:Section\s+)?\d+\.\d+(?:[.\s]{1,2}[A-Z]|[ \t]*$)",
)

# Once inside a capitalization sub-section that uses lettered clauses —
# "(a) ... (b) ... (c) ..." — the actual outstanding-security-count statement
# (or, when unavailable, its Disclosure Schedule cross-reference) is reliably
# in clause (a) alone; validated against Aon ((a) has all 5 counts; (b)-(e)
# are negative representations, subsidiary cap tables, and JV lists — no
# target outstanding securities) and Black Spade ((a) references the
# Disclosure Schedule; (b)-(c) are Subsidiary/negative-representation
# clauses). Narrowing to clause (a) alone when lettered clauses are present
# is a purely structural rule — which lettered clause, not what it says — so
# it locates evidence without interpreting it. Subsections without lettered
# clauses (Volato, Victory Capital — continuous prose) are left as they were.
_LETTERED_CLAUSE_A_RE = re.compile(r"(?m)^\(a\)")
_LETTERED_CLAUSE_B_RE = re.compile(r"(?m)^\(b\)")

# CONDITIONS_TO_CLOSING and the CONSIDERATION-subsection scan above already
# validated: sub-section-level fee/remedy language, not the bare
# grounds-listing "Termination" sub-section, is what agreement_termination
# actually needs (Step 6). Validated against all 9 pre-existing
# TERMINATION_FEES documents: 8 of 9 fold the fee/remedy statement (or its
# absence) into "Effect of Termination" alone; BitGo additionally has a
# dedicated "Termination Fee" sub-section. The bare "Termination" heading
# (grounds for termination — mutual consent, breach, Outside Date, etc.) is
# deliberately excluded: it's the largest sub-section in most of these
# articles and is not needed to state or rule out a fee — confirmed directly
# by reading each one (e.g. Aon's "Effect of Termination" defines "Willful
# Breach" and states its damages remedy entirely on its own).
_TERMINATION_SUBSECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bEFFECT\s+OF\s+TERMINATION\b", re.IGNORECASE),
    re.compile(r"\bTERMINATION\s+FEE\b", re.IGNORECASE),
]

# Economic defined terms a selected termination provision may reference by
# name without restating (Step 6, same narrow principle as _ECONOMIC_TERMS
# for Consideration). Not validated as needed anywhere in this corpus — every
# document either states its fee inline (BitGo) or has none — but kept ready
# for a document where a fee is expressed only as a referenced defined term.
_TERMINATION_ECONOMIC_TERMS: tuple[str, ...] = (
    "Company Termination Fee",
    "Parent Termination Fee",
    "Reverse Termination Fee",
    "Termination Fee",
)

_TOC_ONLY_PCT = 0.05  # anchors all within first 5% → ToC-only
_MIN_ANCHORS = 3       # fewer surviving anchors → structure too sparse to navigate


def _is_roman(num_str: str) -> bool:
    return bool(_ROMAN_RE.match(num_str))


def _find_recitals_start(raw_text: str, whereas_start: int) -> int:
    """Return the excerpt start that includes the preamble sentence, when found.

    Looks for the last "dated [as of] <date>" phrase before the first WHEREAS —
    that phrase always sits inside the single preamble sentence which states the
    agreement title, its date, and the opening party/defined-term clause
    ("This Agreement and Plan of Merger..., dated as of..., is entered into
    among X (\"Parent\"), Y (\"Merger Sub\")..."). Extending the excerpt back to
    the start of that sentence's line captures title + date + party clause in
    one deterministic step, with no separate title/date/party detection needed.

    Falls back to whereas_start unchanged when no such phrase is found, or when
    including it would pull in more than _MAX_PREAMBLE_LOOKBACK chars (guards
    against swallowing a Table of Contents or other unrelated preceding text).
    """
    matches = list(_PREAMBLE_DATE_RE.finditer(raw_text, 0, whereas_start))
    if not matches:
        return whereas_start
    preamble_line_start = raw_text.rfind("\n", 0, matches[-1].start()) + 1
    if whereas_start - preamble_line_start > _MAX_PREAMBLE_LOOKBACK:
        return whereas_start
    return preamble_line_start


def _next_nonblank_line(text: str, after_pos: int) -> str:
    """Return first non-blank line starting at after_pos."""
    for line in text[after_pos:].split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _find_term_definition(raw_text: str, term: str) -> str | None:
    """Return the authoritative "<term> means ..." definition line, if any.

    Matches the corpus's defined-term convention: each definition is its own
    line/paragraph starting with the quoted term (curly or straight quotes)
    followed by "means" (e.g. Aon's "Per Share Merger Consideration" means...
    $57.50 in cash..."). Deliberately requires "means" specifically — a
    cross-reference entry like "has the meaning set forth in Section 2.08"
    does not match, since it adds no value over what's already selected.

    Returns None when no such definition exists anywhere in the document —
    this is expected and common (e.g. a term that's self-defined inline where
    it's used, or a term the document never separately defines this way).
    """
    pattern = re.compile(
        r'(?m)^[ \t]*[“"]' + re.escape(term) + r'[”"]\s+means\b[^\n]*',
        re.IGNORECASE,
    )
    m = pattern.search(raw_text)
    return m.group(0).strip() if m else None


def _map_heading(heading: str) -> str | None:
    """Return section_type for heading text, or None if outside the emit set."""
    for pat, stype in _HEADING_MAP:
        if pat.search(heading):
            return stype
    return None


def _find_consideration_subsections(
    article_text: str,
    article_start_offset: int,
    patterns: list[re.Pattern],
) -> list[tuple[str, int, int]]:
    """Find (heading, absolute_start, absolute_end) for each matching sub-section.

    Returns one tuple per matching sub-section heading (not merged into a
    single span), so each piece stays narrow — e.g. Volato's "Merger
    Consideration", "Issuance of the Merger Consideration", "Conversion of
    Stock in the Merger", and "Treatment of ... Options and ... Warrants" are
    four separate, adjacent pieces, not one large block. Non-matching
    sub-sections between them (e.g. "Closing", "Effective Time") are excluded.
    Caller combines pieces (possibly from more than one gated article) into a
    single evidence packet — see _assemble_consideration_packet.
    """
    pieces: list[tuple[str, int, int]] = []
    for m in _SUBSECTION_HEADING_LINE_RE.finditer(article_text):
        heading = m.group("heading").strip()
        if not any(pat.search(heading) for pat in patterns):
            continue
        sub_start = m.start()
        next_match = _NEXT_SUBSECTION_HEADING_RE.search(article_text, m.end())
        sub_end = next_match.start() if next_match else len(article_text)
        pieces.append((
            heading[:200],
            article_start_offset + sub_start,
            article_start_offset + sub_end,
        ))
    return pieces


def _assemble_consideration_packet(
    raw_text: str,
    pieces: list[tuple[str, int, int]],
) -> SectionMatch:
    """Combine selected consideration sub-sections and required term definitions
    into one evidence packet, in source order, with labels — no summarizing.

    Pieces may come from more than one gated article in the document (e.g. a
    merger-mechanics article and, separately, an awards-treatment
    sub-section) and are joined here in document order. After joining, scans
    the combined text for the fixed _ECONOMIC_TERMS vocabulary and appends the
    authoritative "<term> means ..." definition for each one actually
    referenced and separately resolvable elsewhere in the document — skipped
    when the term isn't referenced, has no such definition (e.g. it's
    self-defined inline, already inside the joined text), or the definition
    text is already part of what's been assembled (no duplication).
    """
    ordered = sorted(pieces, key=lambda p: p[1])
    blocks = [f"[{heading}]\n{raw_text[s:e].strip()}" for heading, s, e in ordered]
    combined = "\n\n".join(blocks)

    for term in _ECONOMIC_TERMS:
        if term not in combined:
            continue
        definition = _find_term_definition(raw_text, term)
        if not definition or definition in combined:
            continue
        blocks.append(f'[Definition: "{term}"]\n{definition}')
        combined = "\n\n".join(blocks)

    headings = [heading for heading, _, _ in ordered]
    starts = [s for _, s, _ in ordered]
    ends = [e for _, _, e in ordered]
    return SectionMatch(
        section_type="CONSIDERATION",
        heading_text="; ".join(headings)[:200],
        excerpt_text=combined,
        excerpt_start_offset=min(starts),
        excerpt_end_offset=max(ends),
        confidence="HIGH",
    )


def _assemble_termination_packet(
    raw_text: str,
    pieces: list[tuple[str, int, int]],
) -> SectionMatch:
    """Combine selected termination sub-sections and required fee-term
    definitions into one evidence packet, in source order, with labels — no
    summarizing. Isolated from _assemble_consideration_packet (own function,
    own term vocabulary) rather than shared, so nothing here can affect
    Consideration's assembly.
    """
    ordered = sorted(pieces, key=lambda p: p[1])
    blocks = [f"[{heading}]\n{raw_text[s:e].strip()}" for heading, s, e in ordered]
    combined = "\n\n".join(blocks)

    for term in _TERMINATION_ECONOMIC_TERMS:
        if term not in combined:
            continue
        definition = _find_term_definition(raw_text, term)
        if not definition or definition in combined:
            continue
        blocks.append(f'[Definition: "{term}"]\n{definition}')
        combined = "\n\n".join(blocks)

    headings = [heading for heading, _, _ in ordered]
    starts = [s for _, s, _ in ordered]
    ends = [e for _, _, e in ordered]
    return SectionMatch(
        section_type="TERMINATION_FEES",
        heading_text="; ".join(headings)[:200],
        excerpt_text=combined,
        excerpt_start_offset=min(starts),
        excerpt_end_offset=max(ends),
        confidence="HIGH",
    )


def _extract_capitalization_subsection(
    article_text: str,
    article_start_offset: int,
) -> SectionMatch | None:
    """Find the Capitalization sub-section within a company Reps article span.

    Returns None when no matching sub-section heading is found (caller logs).
    """
    cap_match = _CAPITALIZATION_SUBSECTION_RE.search(article_text)
    if not cap_match:
        return None
    sub_start = cap_match.start()
    heading_text = cap_match.group(0).strip()
    next_match = _CAPITALIZATION_NEXT_HEADING_RE.search(article_text, cap_match.end())
    sub_end = next_match.start() if next_match else len(article_text)

    a_match = _LETTERED_CLAUSE_A_RE.search(article_text, cap_match.end(), sub_end)
    if a_match:
        b_match = _LETTERED_CLAUSE_B_RE.search(article_text, a_match.end(), sub_end)
        if b_match:
            sub_end = b_match.start()

    return SectionMatch(
        section_type="CAPITALIZATION",
        heading_text=heading_text,
        excerpt_text=article_text[sub_start:sub_end],
        excerpt_start_offset=article_start_offset + sub_start,
        excerpt_end_offset=article_start_offset + sub_end,
        confidence="HIGH",
    )


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
        rec_start = _find_recitals_start(raw_text, whereas_m.start())
        results.append(SectionMatch(
            section_type="RECITALS",
            heading_text="RECITALS",
            excerpt_text=raw_text[rec_start:first_anchor],
            excerpt_start_offset=rec_start,
            excerpt_end_offset=first_anchor,
            confidence="HIGH",
        ))

    # --- Article sections: anchor_start → next anchor_start (or EOF) ----
    # Consideration pieces accumulate across every gated article in the
    # document (there can be more than one — e.g. Aon's non-matching "The
    # Merger" mechanics article plus its matching "Effect of the Merger"
    # article) and are combined into a single evidence packet after this loop.
    consideration_pieces: list[tuple[str, int, int]] = []

    for i, (start, num, heading) in enumerate(articles):
        next_start = articles[i + 1][0] if i + 1 < len(articles) else doc_len
        section_type = _map_heading(heading)
        if section_type == "TERMINATION_FEES":
            # Narrow to "Effect of Termination" / "Termination Fee"
            # sub-sections rather than the whole article (Step 6) — but fall
            # back to the whole article, exactly the prior behavior, when
            # neither sub-section heading is found (e.g. Starry Sea's
            # "Termination Without Default" / "Termination Upon Default" /
            # "Survival" naming, which doesn't use either heading). This
            # preserves 100% of the coverage the whole-article approach had.
            article_text = raw_text[start:next_start]
            term_pieces = _find_consideration_subsections(
                article_text, start, _TERMINATION_SUBSECTION_PATTERNS
            )
            if term_pieces:
                results.append(_assemble_termination_packet(raw_text, term_pieces))
            else:
                results.append(SectionMatch(
                    section_type=section_type,
                    heading_text=heading[:200],
                    excerpt_text=article_text,
                    excerpt_start_offset=start,
                    excerpt_end_offset=next_start,
                    confidence="HIGH",
                ))
            continue
        if section_type is not None:
            results.append(SectionMatch(
                section_type=section_type,
                heading_text=heading[:200],
                excerpt_text=raw_text[start:next_start],
                excerpt_start_offset=start,
                excerpt_end_offset=next_start,
                confidence="HIGH",
            ))
            continue
        if _COMPANY_REPS_HEADING_RE.search(heading):
            article_text = raw_text[start:next_start]
            cap_section = _extract_capitalization_subsection(article_text, start)
            if cap_section:
                results.append(cap_section)
            else:
                _log.info(
                    "company reps article scanned, no Capitalization sub-section matched. "
                    "heading=%r first200=%r",
                    heading[:200], article_text[:200],
                )
            continue
        if _MERGER_ARTICLE_HEADING_RE.search(heading):
            article_text = raw_text[start:next_start]
            pieces = _find_consideration_subsections(article_text, start, _CONSIDERATION_SUBSECTION_PATTERNS)
            if pieces:
                consideration_pieces.extend(pieces)
            else:
                _log.info(
                    "merger-mechanics article scanned, no Consideration sub-section matched. "
                    "heading=%r first200=%r",
                    heading[:200], article_text[:200],
                )
            continue
        if _PURCHASE_ARTICLE_HEADING_RE.search(heading):
            article_text = raw_text[start:next_start]
            pieces = _find_consideration_subsections(article_text, start, _PURCHASE_SUBSECTION_PATTERNS)
            if pieces:
                consideration_pieces.extend(pieces)
            else:
                _log.info(
                    "purchase-and-sale article scanned, no Consideration sub-section matched. "
                    "heading=%r first200=%r",
                    heading[:200], article_text[:200],
                )

    if consideration_pieces:
        results.append(_assemble_consideration_packet(raw_text, consideration_pieces))

    return results
