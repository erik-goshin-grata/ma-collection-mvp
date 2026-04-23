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
from adapters.sec_api import _FILING_QUERY_URL

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
# sec-api.io validation
# ---------------------------------------------------------------------------

def validate_sec_api() -> None:
    print("=" * 60)
    print("sec-api.io — Filing Query for recent 8-K Item 1.01 filings")
    print("=" * 60)

    sec_api_key = os.getenv("SEC_API_KEY", "").strip()
    if not sec_api_key:
        print("SKIP — SEC_API_KEY not set in environment")
        return

    # Query: any 8-K Item 1.01 filed in the last 7 days, any company
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
    print(f"Payload: {json.dumps(payload, indent=2)}")

    session = requests.Session()
    try:
        resp = session.post(
            _FILING_QUERY_URL,
            json=payload,
            headers={"Authorization": sec_api_key},
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
    print("Validation complete.")
