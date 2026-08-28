"""
Binding test for round_currency capture in Stage 4b (2026-08-11).

The funding prompt emits round.currency, but it was never stored: in the i==0
UPDATE the round-currency parameter bound to the FIRST of two duplicate
`valuation_currency = ?` assignments, which SQLite overwrites with the second
(the real valuation currency). Fix: the first assignment is repurposed to
`round_currency = ?`, and the i>0 INSERT gains an explicit round_currency column.

Because that binding is positional and sits in the exact spot bug #6 lived, this
test PROVES it rather than tracing it: it drives the real stage (LLM stubbed) with
a known round.currency and reads round_currency back, on BOTH write paths:
  - i==0  UPDATE branch  -> EUR
  - i>0   INSERT branch  -> GBP

No backfill is owed for historical rows: round.currency was never stored before
this (the dead first assignment), not stored wrong — verified empirically that
SQLite's last-write-wins left valuation_currency correct.

No live API calls (call_prompt is monkeypatched). Runs against a temp DB.

Usage:
    python scripts/test_round_currency_capture.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection, init_db
import stages.funding_hc_extract as fhc


def _run() -> int:
    tmp = tempfile.mkdtemp(prefix="round_ccy_")
    db_path = os.path.join(tmp, "t.db")
    init_db(db_path)
    conn = get_connection(db_path)

    conn.execute(
        """
        INSERT INTO source_raw (source_raw_id, source_type, source_tier, url, fetched_at,
                                title, clean_text, published_date)
        VALUES (1, 'PR_NEWSWIRE', 'T2', 'https://example.test/round', '2026-08-12',
                'Two funding rounds', 'body text', '2026-08-12')
        """
    )
    # One CLASSIFIED funding row; the stubbed prompt returns TWO transactions, so
    # txn[0] exercises the UPDATE branch on eid=10 and txn[1] the INSERT branch.
    conn.execute(
        """
        INSERT INTO staging_extraction (extraction_id, source_raw_id, status,
                                        v2_event_type, dt_prompt_version)
        VALUES (10, 1, 'CLASSIFIED', 'VC_ROUND', '0.6')
        """
    )
    conn.commit()

    def _fake_call_prompt(**kwargs):
        return {
            "transactions": [
                {  # UPDATE branch (i==0) — round currency EUR
                    "company": {"name": "Acme", "domain": "acme.test"},
                    "investors": [],
                    "round": {"label": "Series B", "size": 50000000, "currency": "EUR",
                              "post_money_valuation": 250000000, "valuation_currency": "EUR"},
                    "dates": {"announced_date": "2026-08-01"},
                    "financials_disclosure_status": "DISCLOSED",
                    "transaction_terms_disclosure_status": "DISCLOSED",
                    "model_confidence": "HIGH",
                },
                {  # INSERT branch (i>0) — round currency GBP
                    "company": {"name": "Beta", "domain": "beta.test"},
                    "investors": [],
                    "round": {"label": "Seed", "size": 3000000, "currency": "GBP"},
                    "dates": {"announced_date": "2026-08-01"},
                    "financials_disclosure_status": "DISCLOSED",
                    "transaction_terms_disclosure_status": "DISCLOSED",
                    "model_confidence": "HIGH",
                },
            ]
        }

    fhc.call_prompt = _fake_call_prompt  # monkeypatch the module-bound reference
    cfg = SimpleNamespace(log_level="WARNING")
    fhc.run(conn, cfg, "test_round_ccy")

    failures = []

    # UPDATE branch: eid=10
    r0 = conn.execute(
        "SELECT round_currency, round_size, valuation_currency FROM staging_extraction WHERE extraction_id = 10"
    ).fetchone()
    if r0["round_currency"] != "EUR":
        failures.append(f"UPDATE branch: round_currency={r0['round_currency']!r}, expected 'EUR'")
    # The real valuation currency must survive (the bug that the fix's twin guarded against)
    if r0["valuation_currency"] != "EUR":
        failures.append(f"UPDATE branch: valuation_currency={r0['valuation_currency']!r}, expected 'EUR'")

    # INSERT branch: the second row (Beta)
    r1 = conn.execute(
        "SELECT round_currency FROM staging_extraction WHERE target_name = 'Beta'"
    ).fetchone()
    if r1 is None:
        failures.append("INSERT branch: no row inserted for the second transaction")
    elif r1["round_currency"] != "GBP":
        failures.append(f"INSERT branch: round_currency={r1['round_currency']!r}, expected 'GBP'")

    conn.close()

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("PASS round_currency capture: UPDATE branch round_currency=EUR "
          "(valuation_currency=EUR survives), INSERT branch round_currency=GBP")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
