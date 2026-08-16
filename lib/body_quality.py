"""Source-body quality gate — reject block/interstitial pages before they persist.

A body that is long enough to pass a length check is not necessarily an article.
Bot-management interstitials, consent walls, and "temporarily unavailable" notices
are commonly served with **HTTP 200** and extract to several hundred characters of
perfectly well-formed prose. Under a length-only test they enter `source_raw`
as `clean_text`, are logged as a successful fetch/recovery, and are then
indistinguishable from article text for every downstream stage.

That is silent source poisoning: the corpus gains a row that looks extracted and
reviewed but carries no deal content. This module makes the failure explicit and
countable instead, so a blocked page is recorded as a *failed* body rather than a
short one.

The gate is deliberately conservative — a false positive discards a real article,
which is worse than a rejected block page that can simply be re-fetched. Three
marker classes implement that bias, graded by how much evidence each phrase
carries on its own:

- **Decisive markers** are vendor/product strings and full block sentences that
  effectively cannot occur in editorial copy ("cloudflare ray id", "incapsula
  incident id", "checking your browser before accessing"). A head match rejects
  at any length.
- **Generic markers** describe blocking in ordinary words that an article about
  security or access control may legitimately use ("access denied", "captcha",
  "403 forbidden"). A head match rejects only when the body is also short enough
  to be an interstitial rather than an article.
- **Weak markers** are ambiguous even in isolation (cookie banners, "try again
  later", "support team") and reject only for very short bodies.

Both length-gated tiers exist because of a concrete false-positive class: a press
release *about* bot protection, consent management, or a security acquisition
uses this exact vocabulary, and discarding it would lose real deal content.
Matching only the head reinforces that — an interstitial leads with its message,
whereas an article reaches the topic after its dateline and lede.

Domain-agnostic by construction: no host, publisher, or URL is referenced here.
"""

from __future__ import annotations

import re

# Shared minimum usable body length. Both the CSV/URL ingest and the headless
# recovery helper previously carried their own copy of this constant; one
# definition keeps "usable body" meaning the same thing on every write path.
MIN_BODY_CHARS = 100

# Length ceilings for the two evidence-limited tiers. An interstitial is a page
# of chrome and one message; a press release carries a dateline, quotes,
# boilerplate, and contacts, and runs several thousand characters.
GENERIC_MARKER_MAX_CHARS = 1_500
WEAK_MARKER_MAX_CHARS = 600

# Interstitials lead with their message; articles do not.
_HEAD_CHARS = 500

# Reject on a head match regardless of body length.
_DECISIVE_MARKERS = (
    "please be advised that this page is unavailable",
    "access to this page has been denied",
    "request unsuccessful. incapsula incident id",
    "incapsula incident id",
    "attention required!",
    "checking your browser before accessing",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "please enable javascript to continue",
    "unusual traffic from your computer network",
    "cloudflare ray id",
    "ddos protection by",
    "pardon our interruption",
    "one more step before you continue",
    "http error 403",
    "error 1020",
)

# Ordinary words for blocking — legitimate subject matter for a security or
# access-control press release. Reject on a head match only in a short body.
_GENERIC_MARKERS = (
    "this page is unavailable",
    "access denied",
    "your request has been blocked",
    "verify you are a human",
    "are you a robot",
    "please enable cookies",
    "automated queries",
    "captcha",
    "bot protection",
    "rate limit exceeded",
    "too many requests",
    "403 forbidden",
)

# Ambiguous anywhere. Reject only in a very short body.
_WEAK_MARKERS = (
    "temporarily unavailable",
    "service unavailable",
    "page not found",
    "an error occurred",
    "try again later",
    "open a support ticket",
    "support team",
    "javascript is disabled",
    "your browser is not supported",
    "subscribe to continue reading",
    "sign in to continue",
    "accept all cookies",
    "we use cookies",
    "cookie policy",
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace so markers match across wrapping."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _html_title(raw_html: str | None) -> str | None:
    """Return the normalized <title> text, or None."""
    if not raw_html:
        return None
    match = _TITLE_RE.search(raw_html)
    if not match:
        return None
    title = _normalize(re.sub(r"<[^>]+>", " ", match.group(1)))
    return title or None


def block_page_reason(clean_text: str | None, *, raw_html: str | None = None) -> str | None:
    """Return a short reason string if this looks like a block/interstitial page.

    Returns None when the body appears to be genuine article text. The reason is
    formatted for logs and for `source_raw.notes`, e.g.
    ``block_page:body:access denied``.

    `raw_html` is optional; when supplied, the document <title> is checked against
    the strong markers too. Some interstitials put the block message only in the
    title and chrome, leaving body extraction to return navigation text that would
    otherwise read as plausible.
    """
    normalized = _normalize(clean_text) if clean_text else ""
    if normalized:
        head = normalized[:_HEAD_CHARS]

        for marker in _DECISIVE_MARKERS:
            if marker in head:
                return f"block_page:body:{marker}"

        if len(normalized) < GENERIC_MARKER_MAX_CHARS:
            for marker in _GENERIC_MARKERS:
                if marker in head:
                    return f"block_page:short_body_marker:{marker}"

        if len(normalized) < WEAK_MARKER_MAX_CHARS:
            for marker in _WEAK_MARKERS:
                if marker in normalized:
                    return f"block_page:short_body_marker:{marker}"

    # Some interstitials leave only navigation chrome in the extracted body and
    # carry the verdict in the document title. Titles are inherently short, so
    # both marker tiers apply without a length gate.
    title = _html_title(raw_html)
    if title:
        for marker in _DECISIVE_MARKERS + _GENERIC_MARKERS:
            if marker in title:
                return f"block_page:title:{marker}"

    return None


def body_rejection_reason(clean_text: str | None, *, raw_html: str | None = None) -> str | None:
    """Single decision point for "is this body usable as article text?".

    Returns None when usable, else a short reason string:

      ``empty``                — nothing extracted
      ``too_short:<n>``        — under MIN_BODY_CHARS
      ``block_page:...``       — an interstitial / block page (see block_page_reason)

    Callers must not persist `clean_text` when this returns a reason, and must not
    report the fetch as a successful body.
    """
    # Whitespace-only extractions are empty, not short — the distinction matters
    # because "too_short" invites a retry while "empty" points at the extractor.
    if not clean_text or not clean_text.strip():
        return "empty"

    blocked = block_page_reason(clean_text, raw_html=raw_html)
    if blocked:
        return blocked

    if len(clean_text) < MIN_BODY_CHARS:
        return f"too_short:{len(clean_text)}"

    return None
