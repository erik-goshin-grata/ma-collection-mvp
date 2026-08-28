#!/usr/bin/env python3
"""A source discloses the target's financials and the deal's terms independently.

WHAT WENT WRONG

One field was carrying two different questions. The extraction contract asked
financials_disclosure_status to "classify whether financial terms are disclosed", and
the summary prompt used it to license the sentence "Financial terms were not disclosed"
-- both about the DEAL's value and terms. The target model defines the same field as the
disclosure state for COMPANY FINANCIAL METRICS, which is the target's own operating
financials.

Those are different facts about different things, and a release settles them separately
all the time. "Terms of the transaction were not disclosed. Beta generated $50 million
in revenue last year" is an ordinary sentence, and under one field it could only be
recorded by choosing an axis and being wrong about the other.

Worse than lossy: a source that withheld its revenue could license a claim about the
price it never made.

WHAT CHANGED

  financials_disclosure_status          the TARGET's operating financials
  transaction_terms_disclosure_status   the DEAL's value, price, consideration, terms

Same vocabulary, same meanings, on both:

  DISCLOSED    at least one relevant fact on THAT axis -- never completeness
  UNDISCLOSED  the source affirmatively says so on that axis
  UNKNOWN      the source is silent on that axis

"Financial terms were not disclosed" is a claim about the DEAL, so the summary licence
moves to the terms axis. financials_disclosure_status = UNDISCLOSED no longer licenses
it.

WHAT THIS IS NOT

PARTIALLY_DISCLOSED is not added -- the baseline records it, the reconciliation is open,
and adding a fourth value would freeze an open question. `value_type = UNDISCLOSED` is
untouched and remains an affirmative signal; redesigning it belongs in its own change.

Run from project root:
    python scripts/test_disclosure_axes.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db as _db
import stages.aggregate as agg
import stages.funding_hc_extract as fh
import stages.high_confidence_extract as hc
from lib.observation_writer import HC_FIELDS
from prompts.base import load_prompt_file

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []
AXES = ("financials_disclosure_status", "transaction_terms_disclosure_status")


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t)


def test_contract() -> None:
    flat = _norm(load_prompt_file("high_confidence_extraction")["system"])
    print("\nBoth axes are instructed, and instructed apart:")
    check("the two-axis rule is delivered",
          "DISCLOSURE — TWO AXES, ANSWERED SEPARATELY" in flat, True)
    check("financials names the TARGET's operating financials",
          "The TARGET's own operating financials" in flat, True)
    check("and excludes the deal's terms",
          "NOT the price, the consideration or the deal's terms" in flat, True)
    check("terms names the DEAL's economics", "The DEAL's economics" in flat, True)
    check("and excludes the target's financials",
          "NOT the target's operating financials" in flat, True)
    check("one is never evidence for the other",
          "One is never evidence for the other" in flat, True)

    print("\nThe meanings are stated and unchanged:")
    check("DISCLOSED is not completeness", "DISCLOSED DOES NOT MEAN COMPLETE" in flat, True)
    check("UNDISCLOSED needs the source to say so",
          "UNDISCLOSED REQUIRES THE SOURCE TO SAY SO" in flat, True)
    check("silence is UNKNOWN on both axes", "Silence is UNKNOWN, on both axes" in flat, True)

    print("\nThe mixed answer is taught, not left to inference:")
    check("the worked example is delivered", "THE COMMON CASE IS A MIXED ANSWER" in flat, True)
    check("with both directions shown", "The reverse happens just as often" in flat, True)
    check("and copying one into the other is forbidden",
          "Do not copy one answer into the other field" in flat, True)

    print("\nPARTIALLY_DISCLOSED is not added:")
    check("absent from the delivered contract", "PARTIALLY_DISCLOSED" in flat, False)
    check("absent from the validator vocabulary",
          "PARTIALLY_DISCLOSED" in hc._VALID_FINANCIALS_DISCLOSURE, False)
    check("the vocabulary is exactly the three approved values",
          sorted(hc._VALID_FINANCIALS_DISCLOSURE),
          ["DISCLOSED", "UNDISCLOSED", "UNKNOWN"])

    print("\nvalue_type = UNDISCLOSED is untouched:")
    check("still in the value type vocabulary",
          "UNDISCLOSED" in hc._VALID_VALUE_TYPES, True)
    check("still reserved for an affirmative denial",
          "that value is reserved for a source that explicitly says" in flat, True)


def test_funding_contract() -> None:
    """The funding path asks for what the funding stage requires.

    This section exists because the first cut of this change did not have it. The
    funding stage was made to require the second axis while the funding prompt was
    never told to answer it -- a validator that rejects every funding extraction,
    passed by every test in the suite, because nothing tied a stage's required keys
    to its own delivered text. The generic guard at the end is the real fix; the
    named checks are the specific one.
    """
    flat = _norm(load_prompt_file("funding_hc_extraction")["system"])
    print("\nThe funding path is instructed on both axes too:")
    check("the two-axis rule is delivered",
          "DISCLOSURE — TWO AXES, ANSWERED SEPARATELY" in flat, True)
    check("financials names the COMPANY's operating financials",
          "The COMPANY's own operating financials" in flat, True)
    check("and excludes the round's economics",
          "NOT the round size, the valuation or the round's terms" in flat, True)
    check("terms names the ROUND's economics", "The ROUND's economics" in flat, True)
    check("and excludes the company's financials",
          "NOT the company's operating financials" in flat, True)
    check("the ordinary funding case is worked",
          "THE MIXED ANSWER IS ORDINARY" in flat, True)
    check("a stated round size is the terms axis",
          "DISCLOSED and financials_disclosure_status = UNKNOWN" in flat, True)
    check("PARTIALLY_DISCLOSED is absent here too", "PARTIALLY_DISCLOSED" in flat, False)

    print("\nEvery key a stage requires is named in the text it delivers:")
    for label, mod, name in (("high_confidence_extract", hc, "high_confidence_extraction"),
                             ("funding_hc_extract", fh, "funding_hc_extraction")):
        system = load_prompt_file(name)["system"]
        missing = sorted(k for k in mod._REQUIRED_KEYS if k not in system)
        check(f"{label}: no required key goes unasked", missing, [])


def test_validators() -> None:
    print("\nBoth axes are required on both extraction stages:")
    for name, mod in (("HC", hc), ("Funding HC", fh)):
        req = getattr(mod, "_REQUIRED_KEYS", ())
        for axis in AXES:
            check(f"{name}: {axis} required", axis in req, True)

    print("\nAnd both are validated against the same vocabulary:")
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    for axis in AXES:
        check(f"HC validates {axis}",
              f'invalid {axis}' in src, True)
    fsrc = (ROOT / "stages" / "funding_hc_extract.py").read_text(encoding="utf-8")
    for axis in AXES:
        check(f"Funding validates {axis}", f'invalid {axis}' in fsrc, True)


def test_chain() -> None:
    print("\nThe new axis reaches canonical, not just staging:")
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    _db.init_db(p)
    conn = _db.get_connection(p)
    for table in ("staging_extraction", "transaction_record"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for axis in AXES:
            check(f"{table}.{axis}", axis in cols, True)
    conn.close()

    for axis in AXES:
        check(f"{axis} is reconciled (_FIELDS)", axis in dict(agg._FIELDS), True)
        check(f"{axis} is canonical (Stage 9 owned)",
              axis in agg._STAGE9_OWNED_COLUMNS, True)
        check(f"{axis} is observed (HC_FIELDS)", axis in HC_FIELDS, True)

    print("\nAnd reaches the summary and the review sheets:")
    ds = load_prompt_file("deal_summary")
    check("the template supplies it",
          "{transaction_terms_disclosure_status}" in ds["user_template"], True)
    summarize = (ROOT / "stages" / "summarize.py").read_text(encoding="utf-8")
    check("summarize passes it through",
          "transaction_terms_disclosure_status=" in summarize, True)
    feeder = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")
    for name in ("_MA_COLS", "_FUNDING_COLS"):
        cols = re.findall(r'"([a-z0-9_]+)"',
                          re.search(name + r"\s*=\s*\[(.*?)\]", feeder, re.S).group(1))
        check(f"{name} carries both axes", all(a in cols for a in AXES), True)


def test_summary_licence() -> None:
    flat = _norm(load_prompt_file("deal_summary")["system"])
    print("\nThe non-disclosure licence sits on the axis that makes the claim:")
    check("the two axes are distinguished",
          "TWO DISCLOSURE AXES ARRIVE, AND THEY ANSWER DIFFERENT QUESTIONS" in flat, True)
    check('"Financial terms were not disclosed" is a claim about the DEAL',
          '"Financial terms were not disclosed" IS A CLAIM ABOUT THE DEAL' in flat, True)
    check("licensed by the terms axis",
          "TRANSACTION TERMS DISCLOSURE = UNDISCLOSED, or by value_type = UNDISCLOSED"
          in flat, True)
    # The defect: a withheld revenue figure licensing a claim about the price.
    check("the financials axis explicitly does NOT license it",
          "FINANCIALS DISCLOSURE = UNDISCLOSED does NOT license it" in flat, True)
    check("and the right sentence is named instead",
          "which is a different sentence and must be written as one" in flat, True)
    check("null is still not an affirmative signal",
          "NOT an affirmative signal on either axis" in flat, True)


def test_independence() -> None:
    """The requirement: either axis may hold any value while the other holds another."""
    print("\nThe two axes are independent in the canonical record:")
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    _db.init_db(p)
    conn = _db.get_connection(p)
    combos = [("DISCLOSED", "UNDISCLOSED"), ("UNDISCLOSED", "DISCLOSED"),
              ("UNKNOWN", "UNDISCLOSED"), ("DISCLOSED", "UNKNOWN"),
              ("UNDISCLOSED", "UNDISCLOSED"), ("UNKNOWN", "UNKNOWN")]
    for i, (fin, terms) in enumerate(combos):
        conn.execute("INSERT INTO transaction_record (transaction_id, is_current,"
                     " financials_disclosure_status, transaction_terms_disclosure_status)"
                     " VALUES (?,1,?,?)", (f"tc_{i}", fin, terms))
    conn.commit()
    got = conn.execute("SELECT financials_disclosure_status,"
                       " transaction_terms_disclosure_status FROM transaction_record"
                       " ORDER BY transaction_id").fetchall()
    check("every combination is representable", [tuple(r) for r in got], combos)
    # The two headline cases from the ruling, stated separately.
    check("financials DISCLOSED with terms UNDISCLOSED",
          ("DISCLOSED", "UNDISCLOSED") in [tuple(r) for r in got], True)
    check("financials UNDISCLOSED with terms DISCLOSED",
          ("UNDISCLOSED", "DISCLOSED") in [tuple(r) for r in got], True)
    conn.close()

    print("\nNothing derives one axis from the other:")
    # Both names appear together all over the stages -- in required-key tuples,
    # in SQL column lists, in parameter tuples. Adjacency in a field list is not
    # a derivation, so listing both is expected and fine. What would be a
    # derivation is an operator between them: an assignment, a conditional, a
    # fallback. So keep only the lines that mention both AND carry something
    # other than names, quotes, commas and whitespace.
    enumeration = re.compile(r"""[\s"',\w]*""")
    for f in ("stages/aggregate.py", "stages/high_confidence_extract.py",
              "stages/funding_hc_extract.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        # Guard against a vacuous pass: the file has to carry both axes at all.
        check(f"{f}: carries both axes", [a for a in AXES if a in src], list(AXES))
        both = [l for l in src.splitlines() if all(a in l for a in AXES)]
        coupled = [l for l in both if not enumeration.fullmatch(l)]
        check(f"{f}: no line couples the two axes", coupled, [])


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_contract()
    test_funding_contract()
    test_validators()
    test_chain()
    test_summary_licence()
    test_independence()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — two axes, answered separately, independent end to end.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
