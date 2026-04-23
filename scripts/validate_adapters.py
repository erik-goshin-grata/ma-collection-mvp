"""
Standalone validation script for PR Newswire and sec-api.io adapters.

Performs live fetches to verify connectivity and parser correctness.
No DB writes.  No pipeline stage logic.

Usage:
    python scripts/validate_adapters.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Allow imports from project root when run as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)

from adapters.pr_newswire import _parse_listing_page, _LISTING_BASE, _PAGESIZE
from adapters.sec_api import (
    _FILING_QUERY_URL,
    _EXTRACTOR_URL,
    _ITEM_EXTRACTOR_CODE,
    _EX_21_RE,
    _EX_99_RE,
    _parse_filing_items,
    _select_item,
    _find_exhibits,
)

_UA = os.getenv(
    "USER_AGENT_STRING",
    f"MA-Collection-MVP/0.1 (contact: {os.getenv('OPERATOR_CONTACT_EMAIL', 'test@example.com')})",
)


# ---------------------------------------------------------------------------
# PR Newswire validation
# ---------------------------------------------------------------------------

def validate_pr_newswire() -> None:
    print("=" * 60)
    print("PR Newswire — page 1 listing fetch")
    print("=" * 60)

    url = f"{_LISTING_BASE}?page=1&pagesize={_PAGESIZE}"
    print(f"URL: {url}")

    session = requests.Session()
    session.headers.update({"User-Agent": _UA})

    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as exc:
        print(f"FAIL — network error: {exc}")
        return

    print(f"HTTP status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"FAIL — unexpected status {resp.status_code}")
        return

    records = _parse_listing_page(resp.text, _NullLog())
    print(f"Records parsed: {len(records)}")
    if records:
        first = records[0]
        print(f"First record:")
        print(f"  title:          {first['title'][:80]!r}")
        print(f"  url:            {first['url']}")
        print(f"  published_date: {first['published_date']}")
    else:
        print("WARNING — no records parsed; HTML structure may have changed")

    print()


# ---------------------------------------------------------------------------
# sec-api.io v0.1 connectivity check (Filing Query only)
# ---------------------------------------------------------------------------

def validate_sec_api() -> None:
    print("=" * 60)
    print("sec-api.io v0.1 — Filing Query connectivity check")
    print("=" * 60)

    sec_api_key = os.getenv("SEC_API_KEY", "").strip()
    if not sec_api_key:
        print("SKIP — SEC_API_KEY not set in environment")
        return

    payload = {
        "query": {
            "query_string": {
                "query": 'formType:"8-K" AND items:"1.01"',
            }
        },
        "from": "0",
        "size": "5",
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    print(f"URL: {_FILING_QUERY_URL}")

    session = requests.Session()
    try:
        resp = session.post(
            _FILING_QUERY_URL,
            json=payload,
            params={"token": sec_api_key},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"FAIL — network error: {exc}")
        return

    print(f"HTTP status: {resp.status_code}")
    if resp.status_code in (401, 403):
        print("FAIL — authentication error; check SEC_API_KEY")
        return
    if resp.status_code != 200:
        print(f"FAIL — unexpected status {resp.status_code}: {resp.text[:300]}")
        return

    try:
        data = resp.json()
    except ValueError as exc:
        print(f"FAIL — non-JSON response: {exc}")
        return

    filings = data.get("filings") or data.get("hits", {}).get("hits", [])
    total = data.get("total", {})
    print(f"Total matched: {total}")
    print(f"Filings returned: {len(filings)}")
    if filings:
        first = filings[0]
        print(f"First filing:")
        print(f"  accessionNo:  {first.get('accessionNo')}")
        print(f"  formType:     {first.get('formType')}")
        print(f"  filedAt:      {first.get('filedAt')}")
        print(f"  companyName:  {first.get('companyName')}")
        print(f"  ticker:       {first.get('ticker')}")

    print()


# ---------------------------------------------------------------------------
# sec-api.io v0.2 — items array, event-type item selection, exhibit matching
# ---------------------------------------------------------------------------

def validate_sec_api_v2() -> None:
    print("=" * 60)
    print("sec-api.io v0.2 — items array, item selection, exhibit matching")
    print("=" * 60)

    sec_api_key = os.getenv("SEC_API_KEY", "").strip()
    if not sec_api_key:
        print("SKIP — SEC_API_KEY not set in environment")
        return

    # Broad query: any 8-K with items 1.01 OR 2.01, last 30 days, no ticker filter.
    # size=10 to increase chances of finding a filing with documentFormatFiles.
    payload = {
        "query": {
            "query_string": {
                "query": '(items:"1.01" OR items:"2.01" OR items:"8.01" OR items:"1.02")',
            }
        },
        "from": "0",
        "size": "10",
        "sort": [{"filedAt": {"order": "desc"}}],
    }
    print(f"Query: {payload['query']['query_string']['query']}")

    session = requests.Session()
    try:
        resp = session.post(
            _FILING_QUERY_URL,
            json=payload,
            params={"token": sec_api_key},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"FAIL — network error: {exc}")
        return

    print(f"HTTP status: {resp.status_code}")
    if resp.status_code in (401, 403):
        print("FAIL — authentication error; check SEC_API_KEY")
        return
    if resp.status_code != 200:
        print(f"FAIL — unexpected status {resp.status_code}: {resp.text[:300]}")
        return

    try:
        data = resp.json()
    except ValueError as exc:
        print(f"FAIL — non-JSON response: {exc}")
        return

    filings = data.get("filings") or data.get("hits", {}).get("hits", [])
    total = data.get("total", {})
    print(f"Total matched: {total}")
    print(f"Filings returned: {len(filings)}")

    if not filings:
        print("WARNING — no filings returned; cannot test item/exhibit logic")
        print()
        return

    # Prefer a filing that has both documentFormatFiles and exhibit 99.x matches
    target_filing = None
    for f in filings:
        if f.get("documentFormatFiles") and _find_exhibits(f, _EX_99_RE):
            target_filing = f
            break
    if target_filing is None:
        for f in filings:
            if f.get("documentFormatFiles"):
                target_filing = f
                break
    if target_filing is None:
        print("WARNING — no filing has documentFormatFiles; using first filing")
        target_filing = filings[0]

    accession = target_filing.get("accessionNo", "(none)")
    filer = target_filing.get("companyName", "(unknown)")
    filed_at = target_filing.get("filedAt", "")[:10]
    filing_url = target_filing.get("linkToFilingDetails", "(none)")

    print(f"\nTarget filing:")
    print(f"  accessionNo:  {accession}")
    print(f"  companyName:  {filer}")
    print(f"  filedAt:      {filed_at}")
    print(f"  filingUrl:    {filing_url}")

    # Items array
    filing_items = _parse_filing_items(target_filing)
    print(f"\nItems array (raw): {target_filing.get('items')!r}")
    print(f"Items parsed:      {filing_items}")

    # Event-type-conditional item selection (no Extractor calls)
    print("\nItem selection by event_type:")
    for event_type in ("ANNOUNCEMENT", "CLOSE", "TERMINATION", "AMENDMENT"):
        chosen = _select_item(filing_items, event_type)
        print(f"  {event_type:<14}  →  {chosen!r}")

    # Exhibit matching
    doc_files = target_filing.get("documentFormatFiles") or []
    print(f"\nAll documentFormatFiles ({len(doc_files)} entries):")
    for doc in doc_files[:15]:  # cap output for readability
        t = (doc.get("type") or "").strip()
        desc = (doc.get("description") or "").strip()
        url = (doc.get("documentUrl") or doc.get("url") or "").strip()
        print(f"  type={t!r:25} desc={desc!r:30} url=...{url[-40:] if url else ''}")
    if len(doc_files) > 15:
        print(f"  ... ({len(doc_files) - 15} more not shown)")

    ex21_matches = _find_exhibits(target_filing, _EX_21_RE)
    print(f"\nExhibit 2.1 matches ({len(ex21_matches)}):")
    if ex21_matches:
        for label, url in ex21_matches:
            print(f"  label={label!r:20} url=...{url[-50:] if url else ''}")
    else:
        print("  (none matched)")

    ex99_matches = _find_exhibits(target_filing, _EX_99_RE)
    print(f"\nExhibit 99.x matches ({len(ex99_matches)}):")
    if ex99_matches:
        for label, url in ex99_matches:
            print(f"  label={label!r:20} url=...{url[-50:] if url else ''}")
    else:
        print("  (none matched)")

    # If the current filing had no exhibits, scan the rest for one that does
    all_ex21 = sum(1 for f in filings if _find_exhibits(f, _EX_21_RE))
    all_ex99 = sum(1 for f in filings if _find_exhibits(f, _EX_99_RE))
    print(f"\nAcross all {len(filings)} returned filings:")
    print(f"  filings with Exhibit 2.1:   {all_ex21}")
    print(f"  filings with Exhibit 99.x:  {all_ex99}")

    # --- Extractor API live call (end-to-end confirmation) ---
    print("\n--- Extractor API live call ---")
    extractor_item = _select_item(filing_items, "ANNOUNCEMENT")
    if extractor_item is None:
        print(f"SKIP — no ANNOUNCEMENT-eligible item in {filing_items!r}")
    elif not filing_url or filing_url == "(none)":
        print("SKIP — no linkToFilingDetails in target filing")
    else:
        extractor_code = _ITEM_EXTRACTOR_CODE[extractor_item]
        params = {
            "url": filing_url,
            "item": extractor_code,
            "type": "text",
            "token": sec_api_key,
        }
        print(f"item={extractor_item!r}  extractor_code={extractor_code!r}")
        print(f"GET {_EXTRACTOR_URL}?url=<filing_url>&item={extractor_code}&type=text&token=...")
        try:
            ex_resp = session.get(_EXTRACTOR_URL, params=params, timeout=60)
        except requests.RequestException as exc:
            print(f"FAIL — network error: {exc}")
            ex_resp = None
        if ex_resp is not None:
            print(f"HTTP status: {ex_resp.status_code}")
            if ex_resp.status_code in (401, 403):
                print("FAIL — authentication error on Extractor")
            elif ex_resp.status_code != 200:
                print(f"FAIL — unexpected status: {ex_resp.text[:200]}")
            else:
                text = ex_resp.text.strip()
                if not text:
                    print("WARNING — empty response body")
                elif text.lower() == "processing":
                    print("WARNING — 'processing' response (would retry in production)")
                else:
                    print(f"Response length: {len(text)} chars")
                    print(f"First 200 chars: {text[:200]!r}")
                    print("Extractor call: OK")

    print()


# ---------------------------------------------------------------------------
# Simple no-op logger for validation
# ---------------------------------------------------------------------------

class _NullLog:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def error(self, *a, **kw): pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    validate_pr_newswire()
    validate_sec_api()
    validate_sec_api_v2()
    print("Validation complete.")
