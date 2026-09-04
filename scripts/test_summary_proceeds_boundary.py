#!/usr/bin/env python3
"""Deal Summary 0.18 — use_of_proceeds is a classification, not a sentence.

WHAT WENT WRONG

Fresh acceptance testing (NovaGo, Catch, DocPharma) showed Summary converting
normalized use_of_proceeds categories -- PRODUCT_AND_TECHNOLOGY,
MARKET_EXPANSION, FACILITIES_AND_EQUIPMENT -- back into prose ("to build its
product and expand into new markets"). The FUNDING FRAMING instruction said
"report in the source's own terms when present" -- true when this field was
free text, false since funding_hc_extraction 0.6 bounded it to an enum. The
instruction was never updated to match, so the model read the category label
itself as reportable source language.

WHAT CHANGED

§4 FUNDING FRAMING's use_of_proceeds bullet: the category is an analytical
classification, never narrative evidence; the model may state a use of
proceeds only when Summary's input separately carries source-supported
descriptive evidence for that specific purpose, never from the category
alone and never elaborated from company description / round stage /
investor identity / general knowledge. No other funding field, no code path,
no schema, no other stage.

This is a prompt-contract fix, not a code fix: stages/summarize.py never
expanded or translated use_of_proceeds itself -- it always passed the raw
value through uncoerced. There is therefore no live-model behavioral test
here (no API access). What this file verifies is (1) the delivered §4 text
actually carries the fix and the stale, no-longer-true instruction is gone,
and (2) the write path still passes the raw enum value through to the
rendered prompt completely unexpanded, for three NovaGo/Catch/DocPharma-
equivalent funding transactions, confirming there is no code-side
translation to find and nothing here for a correctly-instructed model to
misread as license to translate.

Run from project root:
    python scripts/test_summary_proceeds_boundary.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_connection, init_db
from prompts.base import load_prompt_file
import stages.summarize as summarize

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t)


# ---------------------------------------------------------------------------
# 1. The delivered contract
# ---------------------------------------------------------------------------

def test_prompt() -> None:
    print("\nThe instruction reaches the model:")
    flat = _norm(load_prompt_file("deal_summary")["system"])
    check("use_of_proceeds framed as an analytical classification",
          "ANALYTICAL CLASSIFICATION" in flat, True)
    check("explicitly not narrative evidence",
          "NOT narrative evidence" in flat, True)
    check("translating/expanding the category into prose is forbidden",
          "Do NOT translate or expand the category into prose" in flat, True)
    check("requires separate source-supported descriptive evidence",
          "only when the input ALSO contains source-supported" in flat, True)
    check("forbids elaborating from company description/round stage/investor identity",
          "elaborate a purpose from the category, the company" in flat, True)
    check("the ordinary case is omission",
          "omit use of proceeds from the summary entirely" in flat, True)
    check("prompt version is 0.18", summarize._VERSION, "0.18")

    print("\nThe stale, no-longer-true instruction is gone:")
    check("\"report in the source's own terms when present\" removed",
          "report in the source's own terms when present" in flat, False)

    print("\nOther funding framing rules are untouched:")
    check("round_size vs total_raised_to_date distinction still delivered",
          "total_raised_to_date is CUMULATIVE" in flat, True)
    check("facility_size separateness still delivered",
          "facility_size is a SEPARATE facility" in flat, True)


# ---------------------------------------------------------------------------
# 2. Write path: the raw category reaches the prompt unexpanded, never coded
#    around -- for three NovaGo / Catch / DocPharma-equivalent transactions.
# ---------------------------------------------------------------------------

def _seed_funding_transaction(conn, *, transaction_id: str, target_name: str,
                                round_label: str, round_size: float,
                                use_of_proceeds: str | None) -> None:
    conn.execute(
        """
        INSERT INTO transaction_record (
            transaction_id, is_current, deal_type, v2_event_type,
            event_history_type, target_status, target_name,
            round_label, round_size, round_currency,
            use_of_proceeds, announced_date, financials_disclosure_status,
            transaction_terms_disclosure_status
        ) VALUES (?, 1, 'VC_ROUND', 'VC_ROUND', 'ANNOUNCED', 'PRIVATE', ?,
                  ?, ?, 'USD', ?, '2026-09-01', 'UNKNOWN', 'DISCLOSED')
        """,
        (transaction_id, target_name, round_label, round_size, use_of_proceeds),
    )


def _render_prompt_for(conn, transaction_id: str) -> str:
    captured = {}

    def _spy(**kwargs):
        captured["user_prompt"] = kwargs["user_prompt"]
        return {"summary_text": "Placeholder summary text for this test.",
                "word_count": 5, "model_confidence": "HIGH", "notes": None}

    original_call_prompt = summarize.call_prompt
    original_sleep = summarize._SLEEP
    summarize.call_prompt = _spy
    summarize._SLEEP = 0
    try:
        summarize.run(conn, SimpleNamespace(log_level="ERROR"),
                       f"test-proceeds-{transaction_id}")
    finally:
        summarize.call_prompt = original_call_prompt
        summarize._SLEEP = original_sleep
    return captured.get("user_prompt", "")


def _fresh_db(name: str):
    db_path = os.path.join(tempfile.mkdtemp(), name)
    init_db(db_path)
    return get_connection(db_path)


def test_novago_equivalent() -> None:
    print("\nNovaGo-equivalent: enum-only use_of_proceeds reaches the prompt raw:")
    conn = _fresh_db("proceeds_novago.db")
    try:
        _seed_funding_transaction(
            conn, transaction_id="tc_proceeds_novago", target_name="NovaGo Therapeutics",
            round_label="Series B", round_size=30000000,
            use_of_proceeds="PRODUCT_AND_TECHNOLOGY",
        )
        conn.commit()
        prompt = _render_prompt_for(conn, "tc_proceeds_novago")
        check("the raw category string appears verbatim",
              '"use_of_proceeds": "PRODUCT_AND_TECHNOLOGY"' in prompt, True)
        check("no code-side expansion into a proceeds sentence",
              "to build its product" in prompt, False)
    finally:
        conn.close()


def test_catch_equivalent() -> None:
    print("\nCatch-equivalent: a multi-value enum still reaches the prompt raw:")
    conn = _fresh_db("proceeds_catch.db")
    try:
        _seed_funding_transaction(
            conn, transaction_id="tc_proceeds_catch", target_name="Catch",
            round_label="Seed", round_size=5000000,
            use_of_proceeds="PRODUCT_AND_TECHNOLOGY, MARKET_EXPANSION",
        )
        conn.commit()
        prompt = _render_prompt_for(conn, "tc_proceeds_catch")
        check("both category values appear verbatim, comma-joined",
              '"use_of_proceeds": "PRODUCT_AND_TECHNOLOGY, MARKET_EXPANSION"' in prompt, True)
        check("no code-side expansion into a proceeds sentence",
              "expand into new markets" in prompt, False)
    finally:
        conn.close()


def test_docpharma_equivalent() -> None:
    print("\nDocPharma-equivalent: a third category reaches the prompt raw:")
    conn = _fresh_db("proceeds_docpharma.db")
    try:
        _seed_funding_transaction(
            conn, transaction_id="tc_proceeds_docpharma", target_name="DocPharma",
            round_label="Pre-Series A", round_size=2000000,
            use_of_proceeds="MARKET_EXPANSION, FACILITIES_AND_EQUIPMENT",
        )
        conn.commit()
        prompt = _render_prompt_for(conn, "tc_proceeds_docpharma")
        check("both category values appear verbatim, comma-joined",
              '"use_of_proceeds": "MARKET_EXPANSION, FACILITIES_AND_EQUIPMENT"' in prompt, True)
        check("no code-side expansion into a proceeds sentence",
              "facilities and equipment to" in prompt.lower(), False)
    finally:
        conn.close()


def test_null_case_unaffected() -> None:
    print("\nA transaction with no stated use_of_proceeds is unaffected (still null-through):")
    conn = _fresh_db("proceeds_null.db")
    try:
        _seed_funding_transaction(
            conn, transaction_id="tc_proceeds_null", target_name="QuietCo",
            round_label="Series A", round_size=10000000,
            use_of_proceeds=None,
        )
        conn.commit()
        prompt = _render_prompt_for(conn, "tc_proceeds_null")
        check("null passes through as JSON null, not a fabricated category",
              '"use_of_proceeds": null' in prompt, True)
    finally:
        conn.close()


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_novago_equivalent()
    test_catch_equivalent()
    test_docpharma_equivalent()
    test_null_case_unaffected()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — use_of_proceeds reaches Summary as a raw classification, never "
          f"expanded by code, and the prompt now forbids expanding it into prose.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
