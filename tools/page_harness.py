"""
page_harness.py -- take a URL, return the page as HTML and clean text.

Site-agnostic. Escalates through fetch strategies until one returns a page that
isn't a block wall, then extracts text by running every available extractor and
keeping the best result.

    from page_harness import harness
    page = harness("https://example.com/article")
    page.html, page.text, page.title, page.jsonld

    python page_harness.py URL [URL ...] --out ./pages
    python page_harness.py --urls urls.txt --out ./pages --workers 4

Fetch ladder, cheapest first:
    1. curl_cffi impersonating Chrome   -- browser TLS fingerprint, ~0.5s
    2. curl_cffi impersonating Safari   -- different JA3, some walls key on one
    3. Playwright persistent Chromium   -- real browser, ~3s, needs --profile

Design rules learned the hard way:
    * Block walls often return HTTP 200. Status codes are not a health check.
    * Never let one extractor win by default -- run them all, take the best,
      and flag the result when even the best looks like a fragment.
    * Site-specific selectors are extra candidates, never overrides.

Deps:  pip install curl_cffi trafilatura selectolax
       optional: pip install readability-lxml playwright
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

__all__ = ["harness", "Page", "save", "SITE_RULES"]

# --------------------------------------------------------------------------
# block detection -- the part that matters most in production
# --------------------------------------------------------------------------

# Phrases that mean "you got a wall, not the page". Checked case-insensitively
# against the head of the document. Extend freely; false positives here cost
# you one wasted browser fetch, false negatives poison your dataset.
BLOCK_MARKERS = (
    "just a moment",                    # Cloudflare
    "checking your browser",            # Cloudflare
    "enable javascript and cookies",    # Cloudflare
    "cf-browser-verification",
    "request unsuccessful",             # Imperva/Incapsula
    "incapsula incident id",
    "_incapsula_resource",
    "access denied",                    # Akamai and friends
    "reference #",                      # Akamai error page
    "you have been blocked",
    "attention required",
    "are you a robot",
    "unusual traffic",
    "captcha",
    "px-captcha",                       # PerimeterX
    "datadome",
    "temporarily unavailable",          # generic maintenance interstitial
    "scheduled maintenance",
    "rate limit",
)

BLOCKED_STATUS = {401, 403, 405, 406, 409, 418, 429, 503}
RETRY_STATUS = {408, 429, 500, 502, 503, 504}

MIN_REAL_PAGE_BYTES = 3000   # below this with no article structure = suspect
STRUCTURE_HINTS = ("application/ld+json", "<article", "articlebody",
                   'property="og:', "<h1")


def looks_blocked(html: str | None, status: int | None) -> tuple[bool, str]:
    """Return (blocked, why). Deliberately conservative about calling a page
    good -- a wall stored as a record is worse than a retry."""
    if status in BLOCKED_STATUS:
        return True, f"status {status}"
    if not html:
        return True, "empty body"
    head = html[:8000].lower()
    for m in BLOCK_MARKERS:
        if m in head:
            return True, f"marker: {m}"
    low = html.lower()
    if any(h in low for h in STRUCTURE_HINTS):
        return False, ""
    if len(html) < MIN_REAL_PAGE_BYTES:
        return True, f"no article structure and only {len(html)} bytes"
    return False, ""


# --------------------------------------------------------------------------
# optional per-site selectors -- extra candidates, never overrides
# --------------------------------------------------------------------------

SITE_RULES: dict[str, dict] = {
    "businesswire.com": {"body": [".bw-release-story", "[itemprop='articleBody']"]},
    "prnewswire.com": {"body": [".release-body", ".col-lg-10 .release-body"]},
    "globenewswire.com": {"body": ["#main-body-container", ".main-body-container"]},
}


def rules_for(url: str) -> dict:
    host = (urlparse(url).hostname or "").lower()
    for domain, rules in SITE_RULES.items():
        if host == domain or host.endswith("." + domain):
            return rules
    return {}


# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------


@dataclass
class Page:
    url: str
    ok: bool = False
    status: int | None = None
    final_url: str | None = None
    via: str | None = None            # which strategy won
    blocked: bool = False
    block_reason: str | None = None
    error: str | None = None
    elapsed_ms: int = 0

    html: str | None = None
    text: str | None = None
    title: str | None = None
    jsonld: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)      # og:/description/canonical
    extractor: str | None = None
    suspect: bool = False                          # text looks like a fragment
    candidates: dict = field(default_factory=dict)  # extractor -> char count

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("html", None)
        d.pop("text", None)
        d["text_chars"] = len(self.text or "")
        d["html_bytes"] = len(self.html or "")
        return d


# --------------------------------------------------------------------------
# fetch strategies
# --------------------------------------------------------------------------

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def _try_curl_cffi(url: str, impersonate: str, timeout: int):
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        return None, None, None, "curl_cffi not installed"
    try:
        r = cffi.get(url, impersonate=impersonate, timeout=timeout,
                     headers=DEFAULT_HEADERS, allow_redirects=True)
    except Exception as e:
        return None, None, None, f"{type(e).__name__}: {e}"
    return r.status_code, r.text, str(r.url), None


def _try_playwright(url: str, profile_dir: str, timeout: int, wait_ms: int = 1200,
                    disable_http2: bool = False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, None, None, "playwright not installed"
    try:
        with sync_playwright() as p:
            # Opt-in HTTP/1.1-only launch. Built as conditional kwargs rather than
            # args=[] so the default call is byte-for-byte the one it has always made.
            extra = {"args": ["--disable-http2"]} if disable_http2 else {}
            ctx = p.chromium.launch_persistent_context(
                profile_dir, headless=True,
                viewport={"width": 1440, "height": 900},
                **extra,
            )
            page = ctx.new_page()
            resp = page.goto(url, wait_until="domcontentloaded",
                             timeout=timeout * 1000)
            page.wait_for_timeout(wait_ms)
            html, final, status = page.content(), page.url, (resp.status if resp else None)
            ctx.close()
        return status, html, final, None
    except Exception as e:
        return None, None, None, f"{type(e).__name__}: {e}"


def _strategies(profile_dir: str | None, timeout: int, disable_http2: bool = False):
    yield "curl_cffi:chrome", lambda u: _try_curl_cffi(u, "chrome", timeout)
    yield "curl_cffi:safari", lambda u: _try_curl_cffi(u, "safari17_0", timeout)
    if profile_dir:
        # The label is the provenance. `via` is written to meta.json and read
        # downstream, so a capture that needed the HTTP/1.1 fallback stays
        # distinguishable from an ordinary browser capture. It is set here, before
        # the attempt, so it labels the failures too -- which is the half that
        # tells you whether the fallback actually helped.
        yield ("playwright:h1" if disable_http2 else "playwright"), \
            lambda u: _try_playwright(u, profile_dir, timeout,
                                      disable_http2=disable_http2)


# --------------------------------------------------------------------------
# extraction -- run everything, keep the best
# --------------------------------------------------------------------------


def _soup_text(html: str, selector: str | None = None) -> str:
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        return ""
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript", "svg"):
        for n in tree.css(tag):
            n.decompose()
    if selector:
        parts = [n.text(separator="\n", strip=True) for n in tree.css(selector)]
        return "\n\n".join(p for p in parts if p)
    node = tree.css_first("body") or tree
    return node.text(separator="\n", strip=True)


def _extract_candidates(html: str, url: str) -> list[tuple[str, str]]:
    out = []
    try:
        import trafilatura
        for name, kw in (("trafilatura", {}),
                         ("trafilatura:recall", {"favor_recall": True})):
            t = trafilatura.extract(html, url=url, include_comments=False,
                                    include_tables=True, **kw)
            if t and len(t) > 200:
                out.append((t, name))
    except ImportError:
        pass
    try:
        from readability import Document
        doc = Document(html)
        t = _soup_text(doc.summary())
        if t and len(t) > 200:
            out.append((t, "readability"))
    except Exception:
        pass
    for sel in rules_for(url).get("body", []):
        t = _soup_text(html, sel)
        if t and len(t) > 200:
            out.append((t, f"site:{sel}"))
    return out


def extract_text(html: str, url: str) -> tuple[str | None, str | None, dict, bool]:
    """(text, extractor, candidate_lengths, suspect)"""
    cands = _extract_candidates(html, url)
    lengths = {how: len(t) for t, how in cands}
    if not cands:
        full = _soup_text(html)
        return (full or None), ("raw_body" if full else None), lengths, True
    text, how = max(cands, key=lambda c: len(c[0]))
    full = _soup_text(html)
    # Chrome (nav/footer) is usually a minority of a real article page. Well
    # under a quarter of the visible text means we probably kept a fragment.
    suspect = bool(full) and len(text) < len(full) * 0.20
    return text, how, lengths, suspect


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)
META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE)


def extract_meta(html: str) -> tuple[str | None, list, dict]:
    m = TITLE_RE.search(html)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    blocks = []
    for raw in JSONLD_RE.findall(html):
        try:
            blocks.append(json.loads(raw.strip()))
        except json.JSONDecodeError:
            continue
    keep = ("og:title", "og:description", "og:type", "og:url", "og:site_name",
            "description", "author", "article:published_time",
            "article:modified_time", "publish-date", "date")
    meta = {k: v for k, v in META_RE.findall(html) if k.lower() in keep}
    return title, blocks, meta


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------


def harness(url: str, profile_dir: str | None = None, timeout: int = 30,
            retries: int = 2, backoff: float = 2.0,
            disable_http2: bool = False) -> Page:
    """Fetch a URL and return it as HTML + clean text.

    Escalates strategies on a block, retries with backoff on transient status,
    and reports which strategy won so you can tell cheap pages from expensive
    ones without instrumenting anything else.
    """
    t0 = time.time()
    page = Page(url=url)
    last_reason = None

    for name, strategy in _strategies(profile_dir, timeout, disable_http2):
        for attempt in range(retries + 1):
            status, html, final, err = strategy(url)
            page.status, page.via = status, name
            if err:
                page.error = err
                break                       # tool missing / network -- escalate
            if status in RETRY_STATUS and attempt < retries:
                time.sleep(backoff ** attempt)
                continue
            blocked, why = looks_blocked(html, status)
            if blocked:
                last_reason = f"{name}: {why}"
                break                       # escalate rather than hammer
            page.ok = True
            page.html, page.final_url, page.error = html, final or url, None
            break
        if page.ok:
            break

    if not page.ok:
        page.blocked = True
        page.block_reason = last_reason or page.error
        page.elapsed_ms = int((time.time() - t0) * 1000)
        return page

    page.title, page.jsonld, page.meta = extract_meta(page.html)
    page.text, page.extractor, page.candidates, page.suspect = \
        extract_text(page.html, url)
    page.elapsed_ms = int((time.time() - t0) * 1000)
    return page


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------


def slug(url: str) -> str:
    h = hashlib.sha1(url.encode()).hexdigest()[:10]
    tail = re.sub(r"[^a-zA-Z0-9]+", "-", urlparse(url).path.strip("/"))[-60:]
    return f"{tail.strip('-') or 'page'}-{h}"


def save(page: Page, outdir: str) -> str:
    d = os.path.join(outdir, slug(page.url))
    os.makedirs(d, exist_ok=True)
    if page.html:
        open(os.path.join(d, "page.html"), "w", encoding="utf-8").write(page.html)
    if page.text:
        open(os.path.join(d, "page.txt"), "w", encoding="utf-8").write(page.text)
    meta = page.summary()
    meta["fetched_at"] = datetime.now(timezone.utc).isoformat()
    meta["html_sha256"] = hashlib.sha256((page.html or "").encode()).hexdigest()
    json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"),
              indent=2, ensure_ascii=False)
    return d


def harness_many(urls, outdir=None, workers=1, delay=1.0, **kw):
    def one(u):
        p = harness(u, **kw)
        if outdir:
            save(p, outdir)
        return p
    if workers <= 1:
        out = []
        for i, u in enumerate(urls):
            if i:
                time.sleep(delay)
            out.append(one(u))
        return out
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(one, urls))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch a URL as HTML + clean text")
    ap.add_argument("url", nargs="*")
    ap.add_argument("--urls", help="file with one URL per line")
    ap.add_argument("--out", help="write page.html / page.txt / meta.json here")
    ap.add_argument("--profile", help="Chromium profile dir, enables Playwright tier")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--disable-http2", action="store_true",
                    help="launch the Playwright tier with --disable-http2 "
                         "(HTTP/1.1 only); no effect without --profile")
    ap.add_argument("--text", action="store_true", help="print text to stdout")
    args = ap.parse_args(argv)

    urls = list(args.url)
    if args.urls:
        urls += [l.strip() for l in open(args.urls)
                 if l.strip() and not l.startswith("#")]
    if not urls:
        ap.error("give a URL or --urls FILE")

    pages = harness_many(urls, outdir=args.out, workers=args.workers,
                         delay=args.delay, profile_dir=args.profile,
                         timeout=args.timeout,
                         disable_http2=args.disable_http2)
    for p in pages:
        if args.text and p.text:
            print(p.text)
        else:
            print(json.dumps(p.summary(), ensure_ascii=False))
    return 0 if all(p.ok for p in pages) else 1


if __name__ == "__main__":
    sys.exit(main())
