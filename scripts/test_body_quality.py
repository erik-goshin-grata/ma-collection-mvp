#!/usr/bin/env python3
"""Regression guard for the source-body quality gate.

No network and no model calls — the gate is a pure function over extracted text
and raw HTML, so every case here is a fixture.

Two properties matter, and they pull against each other:

1. A block/interstitial page served as HTTP 200 must be rejected even when it is
   long enough to pass a length check. This is the defect the gate exists for: a
   rejected body is a visible failure, whereas an accepted one silently becomes
   article text for the whole downstream pipeline.
2. Genuine article text must never be rejected. A false positive discards real
   source material, so the fixtures below include press releases that *discuss*
   blocking, security, and cookies — the wording most likely to trip a naive
   substring match.

The gate must also stay domain-agnostic: the interstitial fixtures cover several
different bot-management vendors, not one publisher.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.csv_url import fetch_body
from lib.body_quality import (
    GENERIC_MARKER_MAX_CHARS,
    MIN_BODY_CHARS,
    block_page_reason,
    body_rejection_reason,
)


# The page that actually poisoned the corpus: served HTTP 200, extracts cleanly,
# and reads as prose. Reproduced from the run logs quoted in the 2026-08-15
# handoff.
BW_INTERSTITIAL = (
    "Please be advised that this page is unavailable. "
    "Call +1.888.381.9473 for our Web Support team or open a support ticket. "
    "We apologize for the inconvenience. Please try again later. "
    "Business Wire Information Services. Contact us for assistance with your "
    "release or account. Our support hours are Monday through Friday."
)

CLOUDFLARE_INTERSTITIAL = (
    "Attention Required! | Cloudflare. Please enable cookies. "
    "Checking your browser before accessing the site. This process is automatic. "
    "Your browser will redirect to your requested content shortly. "
    "Please allow up to 5 seconds. Cloudflare Ray ID: 8f2a1c0d4e5b6789. "
    "Performance and security by Cloudflare."
)

INCAPSULA_INTERSTITIAL = (
    "Request unsuccessful. Incapsula incident ID: 1234-567890123456789-0987654321. "
    "Your request has been blocked by our security policy. If you believe this is "
    "an error, please contact the site administrator and provide the incident ID "
    "shown above so the request can be reviewed by the security team."
)

CAPTCHA_WALL = (
    "Verify you are a human by completing the action below. "
    "We have detected unusual traffic from your computer network. "
    "Please complete the CAPTCHA to continue to the page you requested. "
    "This helps us protect the site from automated queries and abuse."
)

# Title-only block: the extractor returned navigation chrome that reads as
# plausible, and the verdict is only available from the document title.
NAV_CHROME_BODY = (
    "Home News Products Solutions Company Investors Careers Contact Newsroom "
    "Media Library Events Leadership Governance Sustainability Partners Support "
    "Resources Documentation Developers Community Blog Legal Privacy Terms "
    "Sitemap Accessibility Cookie Preferences Region Language English"
)
NAV_CHROME_HTML = f"<html><head><title>Access Denied</title></head><body>{NAV_CHROME_BODY}</body></html>"

# A real press release, in the shape the pipeline is built to consume.
REAL_ARTICLE = (
    "NEW YORK--(BUSINESS WIRE)--Samsonite Group S.A. (SEHK: 1910) today announced "
    "that it has entered into a definitive agreement to acquire an 85% interest in "
    "BÉIS, LLC, the fast-growing travel and lifestyle brand, for $178.5 million in "
    "cash. The transaction values BÉIS at a total enterprise value of approximately "
    "$210 million on a cash-free, debt-free basis. BÉIS generated net sales of "
    "approximately $210 million in 2025. The acquisition is expected to close in the "
    "fourth quarter of 2026, subject to customary closing conditions and regulatory "
    "approvals. Kyle Gendreau, Chief Executive Officer of Samsonite Group, said the "
    "combination extends the company's reach into the premium travel segment. "
    "Advisors to the transaction included counsel for both parties."
)

# The adversarial cases: real articles whose subject matter is blocking, bot
# detection, and cookies. These must pass. They are written at realistic press
# release length (a genuine release runs thousands of characters, not hundreds),
# because the gate's length-sensitive tiers are only fairly exercised by a
# realistically sized article.
SECURITY_VENDOR_ARTICLE = (
    "SAN FRANCISCO--(BUSINESS WIRE)--Sentinel Security Holdings, Inc. (NASDAQ: SNTL) "
    "today announced that it has completed the acquisition of BotShield Inc., a "
    "provider of bot protection and CAPTCHA technology used to verify you are a human "
    "before granting access to protected resources. BotShield's platform blocks "
    "automated queries and unusual traffic from suspicious computer networks, and its "
    "access denied workflows are deployed across more than 4,000 enterprise sites "
    "worldwide. Under the terms of the agreement, Sentinel acquired all outstanding "
    "equity of BotShield for $340 million in cash, funded from balance sheet cash and "
    "a new term loan facility. The transaction closed on August 12, 2026, following "
    "receipt of all required regulatory approvals.\n\n"
    "\"BotShield has built the most accurate bot detection engine in the market, and "
    "our customers have been asking for exactly this capability,\" said Dana Whitfield, "
    "Chief Executive Officer of Sentinel Security Holdings. \"Bringing these teams "
    "together lets us defend the full request path, from the edge through the "
    "application tier, without forcing customers to stitch together three vendors.\"\n\n"
    "BotShield, founded in 2017 and headquartered in Austin, Texas, serves financial "
    "services, retail, and travel customers. The company generated revenue of "
    "approximately $58 million in the trailing twelve months ended June 30, 2026, and "
    "employs 210 people. All BotShield employees have received offers to join Sentinel, "
    "and the founding team will continue to lead the product line within Sentinel's "
    "application security division.\n\n"
    "The combined company will offer DDoS protection alongside its existing web "
    "application firewall and API security products. Sentinel expects the acquisition "
    "to be accretive to non-GAAP operating margin beginning in fiscal 2027, and "
    "reaffirmed its full-year revenue guidance of $1.02 billion to $1.05 billion. "
    "Goldman Stone & Co. acted as financial advisor to Sentinel and Harper Vance LLP "
    "acted as legal counsel. Meridian Partners advised BotShield.\n\n"
    "About Sentinel Security Holdings: Sentinel protects more than 12,000 organizations "
    "against automated abuse, fraud, and application-layer attacks. Forward-looking "
    "statements in this release are subject to risks and uncertainties, including "
    "integration risk and competitive dynamics, that could cause actual results to "
    "differ materially. Sentinel undertakes no obligation to update these statements."
)

COOKIE_PLATFORM_ARTICLE = (
    "LONDON--(BUSINESS WIRE)--ConsentWorks plc (LSE: CNSW), whose cookie policy "
    "management platform powers accept all cookies banners for thousands of publishers, "
    "today announced it has agreed to acquire Privacy Layer GmbH for EUR 92 million in "
    "cash and stock. ConsentWorks helps sites explain how we use cookies and manage "
    "subscriber preferences where readers sign in to continue reading gated content.\n\n"
    "The acquisition adds consent orchestration for connected television and mobile "
    "applications, extending ConsentWorks beyond the web properties that make up the "
    "majority of its installed base today. Privacy Layer, based in Berlin, was founded "
    "in 2019 and serves approximately 1,400 customers across the DACH region. The "
    "company recorded annual recurring revenue of EUR 21 million as of December 31, "
    "2025, representing growth of 44 percent year over year.\n\n"
    "\"Publishers are managing consent across four or five surfaces now, and they are "
    "doing it with tooling that was designed for one,\" said Ruth Adeyemi, Chief "
    "Executive Officer of ConsentWorks. \"Privacy Layer solved the television problem "
    "properly, and their compliance engineering is genuinely excellent. Together we can "
    "give a publisher one consent record that follows the reader everywhere.\"\n\n"
    "The consideration comprises EUR 68 million in cash and EUR 24 million in newly "
    "issued ConsentWorks ordinary shares, subject to a two-year lock-up for the "
    "founding shareholders. The transaction is expected to complete in the first "
    "quarter of 2027, subject to regulatory approval and customary closing conditions, "
    "and will be funded from existing cash resources and a new revolving credit "
    "facility arranged earlier this year.\n\n"
    "ConsentWorks expects the acquisition to be earnings neutral in the first full year "
    "and accretive thereafter, with anticipated cost synergies of EUR 6 million per "
    "annum by the end of 2028. The board has confirmed that the transaction does not "
    "require shareholder approval. Northbank Advisory acted as financial advisor to "
    "ConsentWorks; Privacy Layer was advised by Kellner Rechtsanwälte."
)


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_blocked(failures: list[str], name: str, text: str, *, raw_html: str | None = None) -> None:
    reason = body_rejection_reason(text, raw_html=raw_html)
    if reason is None:
        failures.append(f"{name}: expected rejection, got None (body would have been persisted)")
    elif not reason.startswith("block_page"):
        failures.append(f"{name}: expected a block_page verdict, got {reason!r}")


def _check_accepted(failures: list[str], name: str, text: str, *, raw_html: str | None = None) -> None:
    reason = body_rejection_reason(text, raw_html=raw_html)
    if reason is not None:
        failures.append(f"{name}: expected acceptance, got {reason!r} (real article would be discarded)")


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _FakeSession:
    """Minimal stand-in for requests.Session — no network."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def get(self, _url: str, timeout: int | None = None) -> _FakeResponse:
        return self._response


class _NullLog:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _page(body: str, title: str = "Press Release") -> str:
    return f"<html><head><title>{title}</title></head><body><article><p>{body}</p></article></body></html>"


def _check_ingest_wiring() -> list[str]:
    """fetch_body must reject a 200-served interstitial and explain why."""
    failures: list[str] = []
    log = _NullLog()

    raw_html, clean, reason = fetch_body(
        "https://example.test/blocked",
        _FakeSession(_FakeResponse(200, _page(BW_INTERSTITIAL, title="Business Wire"))),
        0,
        log,
    )
    if clean is not None:
        failures.append("ingest wiring: interstitial returned usable clean_text — it would be persisted")
    if raw_html is None:
        failures.append("ingest wiring: raw_html should be retained for diagnosis")
    if not (reason or "").startswith("block_page"):
        failures.append(f"ingest wiring: expected a block_page reason, got {reason!r}")

    raw_html, clean, reason = fetch_body(
        "https://example.test/article",
        _FakeSession(_FakeResponse(200, _page(REAL_ARTICLE))),
        0,
        log,
    )
    if clean is None:
        failures.append(f"ingest wiring: real article was rejected ({reason!r})")
    if reason is not None:
        failures.append(f"ingest wiring: real article should have no rejection reason, got {reason!r}")

    _raw, clean, reason = fetch_body(
        "https://example.test/forbidden",
        _FakeSession(_FakeResponse(403, "Forbidden")),
        0,
        log,
    )
    if clean is not None or reason != "http_status:403":
        failures.append(f"ingest wiring: expected http_status:403, got clean={clean!r} reason={reason!r}")

    return failures


def main() -> None:
    failures: list[str] = []

    # --- The defect: long, HTTP-200 interstitials must not pass -------------
    # Each of these clears MIN_BODY_CHARS comfortably, which is precisely why a
    # length-only check let them through.
    for name, text in (
        ("businesswire interstitial", BW_INTERSTITIAL),
        ("cloudflare interstitial", CLOUDFLARE_INTERSTITIAL),
        ("incapsula interstitial", INCAPSULA_INTERSTITIAL),
        ("captcha wall", CAPTCHA_WALL),
    ):
        if len(text) < MIN_BODY_CHARS:
            failures.append(f"{name}: fixture is too short to exercise the defect")
        _check_blocked(failures, name, text)

    _check_blocked(failures, "title-only block", NAV_CHROME_BODY, raw_html=NAV_CHROME_HTML)

    # --- Real articles must survive, including adversarial subject matter ---
    _check_accepted(failures, "real press release", REAL_ARTICLE)
    _check_accepted(failures, "security vendor article", SECURITY_VENDOR_ARTICLE)
    _check_accepted(failures, "cookie platform article", COOKIE_PLATFORM_ARTICLE)
    _check_accepted(
        failures, "real article with block-page HTML title absent",
        REAL_ARTICLE, raw_html="<html><head><title>Samsonite to acquire BEIS</title></head></html>",
    )

    # --- Non-block rejections keep their own verdicts -----------------------
    _check(failures, "empty body", body_rejection_reason(None), "empty")
    _check(failures, "blank body", body_rejection_reason("   "), "empty")
    short = "Too short to use."
    _check(failures, "short body", body_rejection_reason(short), f"too_short:{len(short)}")

    # --- block_page_reason is independent of the length rule ---------------
    if block_page_reason(REAL_ARTICLE) is not None:
        failures.append("block_page_reason flagged a real article")
    if block_page_reason(BW_INTERSTITIAL) is None:
        failures.append("block_page_reason missed the businesswire interstitial")

    # A weak marker alone must not condemn a long article: "support team" and
    # "try again later" appear in plenty of legitimate copy.
    weak_in_long_article = REAL_ARTICLE + " Please contact our support team or try again later."
    _check_accepted(failures, "weak marker in long article", weak_in_long_article)

    # But the same weak marker in a short body is decisive.
    _check_blocked(
        failures, "weak marker in short body",
        "We're sorry. The service is temporarily unavailable. Please try again later.",
    )

    # Guard the fixtures themselves. The length-gated tiers are only meaningfully
    # exercised while the adversarial articles stay longer than the ceiling; if a
    # later edit trims them, these cases would start passing for the wrong reason.
    for name, text in (
        ("security vendor article", SECURITY_VENDOR_ARTICLE),
        ("cookie platform article", COOKIE_PLATFORM_ARTICLE),
    ):
        if len(text) <= GENERIC_MARKER_MAX_CHARS:
            failures.append(
                f"{name}: fixture ({len(text)} chars) must exceed "
                f"GENERIC_MARKER_MAX_CHARS ({GENERIC_MARKER_MAX_CHARS}) to be a fair test"
            )

    # --- Wiring: the ingest path must apply the gate, not just define it ----
    # An HTTP-200 interstitial is the exact case that previously persisted as
    # article text, so assert it at the adapter boundary rather than only against
    # the pure function.
    failures.extend(_check_ingest_wiring())

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS body quality: block/interstitial pages rejected, real articles preserved")


if __name__ == "__main__":
    main()
