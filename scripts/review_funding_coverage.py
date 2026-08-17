#!/usr/bin/env python3
"""Read-only coverage review: every VC_ROUND / GROWTH_EQUITY row without a round_size.

DIAGNOSTIC ONLY. Opens the database read-only, writes nothing, migrates nothing.

After the nine-row remediation, the remaining funding rows carry a null `round_size`.
Each one is either correctly null or a gap, and the difference is only visible in the
source text. This classifies every one against the settled model and prints the evidence
that justifies the classification, so a human is judging sentences rather than guesses.

Classifications, in the order they are tested — the first match wins because the earlier
ones are disqualifying:

  NOT_ROUND_SECONDARY   a purchase of existing shares. Ordinary acquisition consideration.
  NOT_ROUND_VALUATION   a valuation or market cap. Never an as-transacted magnitude.
  NOT_ROUND_FACILITY    a debt facility. Belongs in facility_size, not round_size.
  INVESTOR_CHECK_ONLY   one named investor's amount, no round total. Party-level detail.
  NON_EXACT_AMOUNT      a bounded or approximate figure ("over $140M"). See below.
  ROUND_SIZE_CANDIDATE  an exact primary-capital amount.
  NO_AMOUNT_DISCLOSED   no figure anywhere. Null is correct, not a gap.
  AMBIGUOUS             a figure with no language settling what it measures.

**NON_EXACT_AMOUNT is a representation gap, not a candidate.** The model has nowhere to
record that a number is a bound: the funding prompt's `round` object has no qualifier
field, `staging_extraction` has no `round_size_qualifier`, and `transaction_record` has
no `*_qualifier` column at all — `value_qualifier` exists at the staging and ledger
layers but never reaches the canonical row. Writing "over $140 million" as
`round_size = 140000000` would assert an exact figure the source declined to give, and
nothing downstream could tell it apart from a stated $140M. Null plus this flag is the
honest state until the model can carry the bound.

Also asserts the family-integrity invariant: no row in the funding family may carry
`transaction_size_basis = 'TRANSACTION_VALUE'`.

Usage:
    python scripts/review_funding_coverage.py --db data/ma_mvp.db
    python scripts/review_funding_coverage.py --db data/ma_mvp.db --tsv
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.quantify_net_debt_currency_gap import table_columns  # noqa: E402

IN_SCOPE = ("VC_ROUND", "GROWTH_EQUITY")
FUNDING_FAMILY = ("VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# A money figure plus whatever qualifies it. The qualifier must be captured with the
# amount: "over $140 million" and "$140 million" are different facts.
_MONEY = re.compile(
    r"(?P<qual>(?:in\s+excess\s+of|more\s+than|no\s+less\s+than|at\s+least|greater\s+than|"
    r"north\s+of|upwards\s+of|over|approximately|approx\.?|about|around|nearly|almost|"
    r"roughly|up\s+to|as\s+much\s+as|~)\s+)?"
    r"(?P<cur>US\$|\$|€|£|¥)\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<mag>billion|bn|million|mm|m\b|k\b|thousand)?",
    re.IGNORECASE,
)
_RANGE = re.compile(
    r"(?:between\s+)?(?:US\$|\$|€|£|¥)\s?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:billion|bn|million|mm|m\b)?\s*(?:to|-|–|—|and)\s*"
    r"(?:US\$|\$|€|£|¥)?\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|bn|million|mm|m\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Binding, not proximity.
#
# The first version asked "does this sentence contain financing language, and does it
# contain a number?" and took the first number it found. That produced four confirmed
# false positives on the live corpus, every one of them a figure that belonged to a
# different entity or a different concept than the financing event:
#
#   AIRS Medical  $65B  — the INVESTOR's capital raised to date
#   Elektrik       $9B  — Lead Edge Capital's firm size / AUM
#   Flutterwave   $3.2B — a post-money VALUATION
#   Fortus  £400M/£1.1B — the investment firm's historical portfolio figures
#
# Co-occurrence in a sentence says nothing about what a number measures. So each amount
# is now judged on ITS OWN span — the text between the previous money figure and the
# next one — and must be *bound* to the financing by an explicit construction, with no
# disqualifying scope marker attached to it.
# ---------------------------------------------------------------------------

# The amount must be the object of the financing. `<AMT>` marks its position.
_BINDING = [
    re.compile(p, re.IGNORECASE) for p in (
        r"rais\w*\s+(?:a\s+|an\s+|its\s+)?(?:new\s+)?<AMT>",
        r"secur\w*\s+(?:a\s+|an\s+|its\s+)?<AMT>",
        r"clos\w*\s+(?:on\s+|of\s+)?(?:its\s+)?(?:a\s+)?<AMT>",
        r"<AMT>\s+(?:in|of)\s+(?:new\s+)?(?:series\s+[a-k]|seed|pre-seed|funding|"
        r"financing|growth|capital|venture|equity|primary)",
        r"<AMT>\s+series\s+[a-k]\b",
        r"<AMT>\s+(?:seed|pre-seed)\s+(?:round|funding|financing)",
        r"(?:series\s+[a-k]|seed|pre-seed|financing|funding)\s*(?:round)?\s*"
        r"(?:of|totall?ing|worth|at)\s+<AMT>",
        r"(?:investment|financing|raise|round|infusion)\s+(?:of|totall?ing)\s+<AMT>",
        r"invest\w*\s+<AMT>\s+(?:in|into)\b",
        r"<AMT>\s+(?:growth\s+)?(?:equity\s+)?investment\b",
        r"announc\w*\s+<AMT>\s+(?:in|of)\b",
        # The round's own total: "bringing the total Series D to $327 million".
        r"total\s+(?:series\s+[a-k]|seed|pre-seed|round|financing)\s+to\s+<AMT>",
    )
]

# Constructions identifying the amount as the ROUND's magnitude. When one sentence
# carries both a round total and an investor's check — "$50 million investment in the
# Series D, bringing the total Series D to $327 million" — the round wins. Both figures
# are real and both are bound to the financing; they are different facts, and only the
# round is the event's magnitude.
_ROUND_TOTAL = [
    re.compile(p, re.IGNORECASE) for p in (
        r"total\s+(?:series\s+[a-k]|seed|pre-seed|round|financing)\s+to\s+<AMT>",
        r"rais\w*\s+(?:a\s+|an\s+|its\s+)?(?:new\s+)?<AMT>",
        r"(?:series\s+[a-k]|seed|pre-seed|financing|funding)\s*(?:round)?\s*"
        r"(?:of|totall?ing|worth|at)\s+<AMT>",
        r"clos\w*\s+(?:on\s+|of\s+)?(?:its\s+)?(?:a\s+)?<AMT>",
        r"<AMT>\s+(?:in|of)\s+(?:new\s+)?(?:series\s+[a-k]|seed|pre-seed|funding|financing)",
        r"<AMT>\s+series\s+[a-k]",
    )
]

# Constructions identifying the amount as ONE INVESTOR's contribution.
_CHECK_SHAPED = [
    re.compile(p, re.IGNORECASE) for p in (
        r"(?:has\s+)?made\s+(?:a\s+)?<AMT>",
        r"invest\w*\s+<AMT>\s+(?:in|into)",
        r"<AMT>\s+(?:growth\s+)?(?:equity\s+)?investment\s+in",
        r"committed\s+<AMT>",
    )
]

# Attached to the amount, these disqualify it regardless of financing language nearby.
_SCOPE_MARKERS = [
    # The investor or the firm, not the target's event.
    (re.compile(r"(assets?\s+under\s+management|\bAUM\b|manages?\b|managing\s+(?:over|more)|"
                r"firm\s+with|fund\s+(?:with|of|size)|portfolio\s+(?:of|companies)|"
                r"across\s+its|since\s+inception|has\s+invested|deployed|"
                r"under\s+management)", re.IGNORECASE), "investor/firm scope"),
    # Cumulative or historical, not this event.
    # "bringing the total to $X" is cumulative ONLY when the total is lifetime-scoped.
    # "bringing the total Series D to $327 million" is the size of THIS round, and
    # the round label is what distinguishes them — so it is excluded by lookahead
    # here and matched as a binding construction instead.
    (re.compile(r"(to\s+date|total\w*\s+rais\w*|"
                r"bringing\s+(?:its\s+|the\s+)?total(?!\s+(?:series\s+[a-k]|seed|"
                r"pre-seed|round|financing))|"
                r"cumulativ\w*|lifetime|since\s+\d{4}|previously\s+rais\w*|"
                r"prior\s+round|to\s+date\b)", re.IGNORECASE), "cumulative/historical"),
    # A valuation is never an as-transacted magnitude.
    (re.compile(r"(valuation|valu(?:ing|ed)\s+(?:the\s+\w+\s+)?at|post-?money|pre-?money|"
                r"market\s+cap\w*|enterprise\s+value)", re.IGNORECASE), "valuation"),
]

_SECONDARY = re.compile(
    r"(acquired?\s+the\s+[\d.]+\s*%\s*(stake\s+)?held\s+by|purchas\w*\s+the\s+stake\s+held\s+by|"
    r"secondary\s+(sale|purchase|transaction|offering)|from\s+existing\s+(share|stock)holders|"
    r"tender\s+offer|bought\s+out)", re.IGNORECASE,
)
_FACILITY = re.compile(
    r"(credit\s+facility|debt\s+facility|term\s+loan|revolving|venture\s+debt|"
    r"loan\s+facility|debt\s+financing)", re.IGNORECASE,
)
_INVESTOR_CHECK = re.compile(
    r"(invest\w*\s+<AMT>\s+(?:in|into)\b|committed\s+<AMT>)", re.IGNORECASE,
)

_MAG = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mm": 1e6, "m": 1e6,
        "k": 1e3, "thousand": 1e3}


def _amount(match: re.Match) -> float | None:
    try:
        n = float(match.group("num").replace(",", ""))
    except (ValueError, AttributeError):
        return None
    mag = (match.group("mag") or "").lower().strip()
    return n * _MAG.get(mag, 1.0)


def _own_span(sentence: str, matches: list[re.Match], i: int) -> str:
    """The text that belongs to amount i, with the amount itself marked `<AMT>`.

    Bounded by the neighbouring money figures so a qualifier attached to one amount
    cannot be read as qualifying another. This is what keeps "raised $250 million at a
    $3.2 billion post-money valuation" from rejecting the $250M: "valuation" lives in
    the $3.2B span, not the $250M one.
    """
    m = matches[i]
    lo = matches[i - 1].end() if i > 0 else 0
    hi = matches[i + 1].start() if i + 1 < len(matches) else len(sentence)
    return sentence[lo:m.start()] + "<AMT>" + sentence[m.end():hi]


def _evaluate(sentence: str) -> list[dict]:
    """Judge every money figure in a sentence on its own span."""
    matches = list(_MONEY.finditer(sentence))
    out = []
    for i, m in enumerate(matches):
        span = _own_span(sentence, matches, i)
        disqualified = next(
            (why for rx, why in _SCOPE_MARKERS if rx.search(span)), None
        )
        bound = any(rx.search(span) for rx in _BINDING)
        out.append({
            "match": m, "span": span, "amount": _amount(m),
            "disqualified": disqualified, "bound": bound,
            "is_round_total": any(rx.search(span) for rx in _ROUND_TOTAL),
            "is_check": any(rx.search(span) for rx in _CHECK_SHAPED),
            "qualifier": (m.group("qual") or "").strip() or None,
        })
    return out


def classify(title: str, body: str) -> dict:
    """-> {classification, amount, exact, qualifier, evidence, action}"""
    blob = f"{title}\n{body}"
    sentences = [s.strip() for s in _SENT_SPLIT.split(blob[:14000]) if s.strip()]

    any_money = False
    rejected: list[tuple[str, dict, str]] = []
    candidates: list[tuple[str, dict]] = []

    for sentence in sentences:
        for cand in _evaluate(sentence):
            any_money = True
            if _SECONDARY.search(sentence):
                rejected.append((sentence, cand, "secondary"))
                continue
            if _FACILITY.search(sentence):
                rejected.append((sentence, cand, "facility"))
                continue
            if cand["disqualified"]:
                rejected.append((sentence, cand, cand["disqualified"]))
                continue
            if cand["bound"]:
                candidates.append((sentence, cand))
            else:
                rejected.append((sentence, cand, "not bound to the financing event"))

    if not any_money:
        return {"classification": "NO_AMOUNT_DISCLOSED", "amount": None, "exact": None,
                "qualifier": None, "evidence": [],
                "action": "leave round_size NULL — correct, not a gap"}

    if not candidates:
        # Every figure was disqualified. Report why, using the first reason found.
        sentence, cand, why = rejected[0]
        label = {
            "secondary": ("NOT_ROUND_SECONDARY",
                          "not round_size — purchase of existing shares; verify the "
                          "event is classified ACQUISITION"),
            "facility": ("NOT_ROUND_FACILITY",
                         "not round_size — facility belongs in facility_size"),
            "valuation": ("NOT_ROUND_VALUATION",
                          "not round_size — a valuation is never an as-transacted magnitude"),
            "investor/firm scope": ("NOT_ROUND_INVESTOR_SCOPE",
                                    "not round_size — the figure describes the investor "
                                    "or firm, not the target's financing event"),
            "cumulative/historical": ("NOT_ROUND_CUMULATIVE",
                                      "not round_size — a cumulative or historical figure, "
                                      "not this event's magnitude"),
        }.get(why, ("AMBIGUOUS",
                    "human review — a figure with no language binding it to the "
                    "financing event"))
        return {"classification": label[0], "amount": cand["amount"], "exact": None,
                "qualifier": None, "evidence": [sentence], "action": label[1]}

    # A round total outranks an investor's check — the Cellares shape: a $50M
    # check inside a $327M Series D. Both are bound to the financing; only the
    # round is the event's magnitude.
    round_totals = [(s, c) for s, c in candidates
                    if c["is_round_total"] and not c["is_check"]]
    sentence, cand = (round_totals or candidates)[0]
    if _INVESTOR_CHECK.search(cand["span"]) and not re.search(
        r"(round|rais\w*|financing)", sentence, re.IGNORECASE
    ):
        return {"classification": "INVESTOR_CHECK_ONLY", "amount": cand["amount"],
                "exact": None, "qualifier": None, "evidence": [sentence],
                "action": "not round_size — one investor's check is party-level detail"}

    is_range = bool(_RANGE.search(sentence))
    if cand["qualifier"] or is_range:
        return {
            "classification": "NON_EXACT_AMOUNT", "amount": cand["amount"], "exact": False,
            "qualifier": cand["qualifier"] or "range", "evidence": [sentence],
            "action": "REPRESENTATION GAP — leave round_size NULL. The model has no "
                      "qualifier field for round_size at any layer, so writing this "
                      "number would assert an exactness the source withheld.",
        }
    return {
        "classification": "ROUND_SIZE_CANDIDATE", "amount": cand["amount"], "exact": True,
        "qualifier": None, "evidence": [sentence],
        "action": "candidate round_size — confirm against the source, then remediate",
    }


def family_integrity(conn: sqlite3.Connection) -> tuple[int, list[sqlite3.Row]]:
    """No funding-family row may carry transaction_size_basis = 'TRANSACTION_VALUE'."""
    tr_cols = table_columns(conn, "transaction_record")
    if "transaction_size_basis" not in tr_cols:
        return -1, []
    ph = ",".join("?" * len(FUNDING_FAMILY))
    total = conn.execute(
        "SELECT COUNT(*) FROM transaction_record "
        "WHERE transaction_size_basis = 'TRANSACTION_VALUE'"
    ).fetchone()[0]
    violations = conn.execute(
        f"SELECT transaction_id, v2_event_type, target_name, transaction_size "
        f"FROM transaction_record "
        f"WHERE transaction_size_basis = 'TRANSACTION_VALUE' "
        f"  AND v2_event_type IN ({ph})",
        FUNDING_FAMILY,
    ).fetchall()
    return total, violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--tsv", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    tr_cols = table_columns(conn, "transaction_record")
    if "v2_event_type" not in tr_cols:
        print("FATAL: transaction_record lacks v2_event_type")
        return 2

    ph = ",".join("?" * len(IN_SCOPE))
    size_col = ("tr.transaction_size AS transaction_size"
                if "transaction_size" in tr_cols else "NULL AS transaction_size")
    rows = conn.execute(
        f"""
        SELECT tr.transaction_id, tr.v2_event_type, tr.target_name, tr.round_size,
               tr.transaction_value, tr.investment_amount, {size_col},
               se.value_amount AS staged_amount, se.value_type, se.hc_prompt_version,
               sr.title, sr.clean_text, sr.url
        FROM transaction_record tr
        JOIN staging_extraction se ON se.transaction_cluster_id = tr.transaction_id
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE tr.v2_event_type IN ({ph}) AND tr.round_size IS NULL
        GROUP BY tr.transaction_id
        ORDER BY tr.target_name
        """,
        IN_SCOPE,
    ).fetchall()

    results = [(r, classify(r["title"] or "", r["clean_text"] or "")) for r in rows]

    if args.tsv:
        print("transaction_id\ttarget\tevent_type\tsource_evidence\tamount\t"
              "exact_or_non_exact\tclassification\tproposed_canonical_action")
        for r, c in results:
            ev = " | ".join(c["evidence"]).replace("\t", " ")[:400]
            amt = "" if c["amount"] is None else f"{c['amount']:,.0f}"
            ex = {True: "exact", False: f"non-exact ({c['qualifier']})",
                  None: "n/a"}[c["exact"]]
            print(f"{r['transaction_id']}\t{r['target_name']}\t{r['v2_event_type']}\t"
                  f"{ev}\t{amt}\t{ex}\t{c['classification']}\t{c['action']}")
    else:
        print(f"\n{'=' * 78}\nFUNDING COVERAGE REVIEW — VC_ROUND / GROWTH_EQUITY, "
              f"round_size IS NULL\n{'=' * 78}")
        print(f"  rows in scope: {len(results)}   (VENTURE_DEBT deliberately excluded)\n")
        buckets: dict[str, int] = {}
        for _r, c in results:
            buckets[c["classification"]] = buckets.get(c["classification"], 0) + 1
        for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<24} {v}")
        for r, c in results:
            print(f"\n{'-' * 78}")
            print(f"  {r['target_name']}  ({r['transaction_id']})  [{r['v2_event_type']}]")
            amt = "none found" if c["amount"] is None else f"{c['amount']:,.0f}"
            ex = {True: "EXACT", False: f"NON-EXACT ({c['qualifier']})",
                  None: "n/a"}[c["exact"]]
            print(f"      amount        : {amt}    {ex}")
            print(f"      classification: {c['classification']}")
            print(f"      evidence      :")
            for e in c["evidence"] or ["(no money-bearing sentence found)"]:
                print(f"          “{e[:280]}”")
            print(f"      action        : {c['action']}")
            print(f"      current state : round_size=NULL "
                  f"transaction_value={r['transaction_value']} "
                  f"investment_amount={r['investment_amount']} "
                  f"transaction_size={r['transaction_size']}")

    total_tv, violations = family_integrity(conn)
    print(f"\n{'=' * 78}\nMODEL-INTEGRITY ASSERTION\n{'=' * 78}")
    if total_tv < 0:
        print("  transaction_size_basis column absent — cannot assert on this database.")
    else:
        print(f"  rows at transaction_size_basis = TRANSACTION_VALUE : {total_tv}")
        print(f"  of those, in the funding family                    : {len(violations)}")
        if violations:
            print("\n  *** VIOLATION — a funding row must never take the M&A rung ***")
            for v in violations:
                print(f"      {v['transaction_id']}  {v['target_name']}  "
                      f"[{v['v2_event_type']}]  {v['transaction_size']}")
        else:
            print("  PASS — no funding-family row carries the M&A basis.")

    conn.close()
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
