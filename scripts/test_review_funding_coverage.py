#!/usr/bin/env python3
"""Regression guard for the funding coverage review.

No network and no model calls.

Two things are asserted, and the second is the one that matters most:

1. **Classification.** Every branch of the taxonomy is exercised with realistic press
   language, including the three named live cases — Chronograph ("over $140 million"),
   Computomic and Elektrik (investment announced, no figure).

2. **A non-exact amount is never promoted to an exact `round_size`.** This is the whole
   point of separating the class. "over $140 million" is a lower bound; recording it as
   `round_size = 140000000` asserts an exactness the source withheld, and nothing
   downstream could distinguish it from a stated $140M. The model has no qualifier
   field for `round_size` at any layer — the funding prompt's `round` object has none,
   `staging_extraction` has no `round_size_qualifier`, and `transaction_record` has no
   `*_qualifier` column at all — so null plus a flag is the only honest state.

Also pins the family-integrity invariant, and that the review never writes.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.review_funding_coverage import classify, family_integrity  # noqa: E402

# (label, title, body, expected classification, expected exact-flag)
CASES = [
    # --- Confirmed ROUND_SIZE from the live review ------------------------
    (
        "aston_power_20m", "Aston Power raises $20M Series A",
        "Aston Power today announced it has raised $20 million in Series A funding to "
        "expand its manufacturing capacity.",
        "ROUND_SIZE_CANDIDATE", True,
    ),
    (
        "attotude_52m", "AttoTude closes $52M round",
        "AttoTude announced the closing of a $52 million financing round led by an "
        "existing investor.",
        "ROUND_SIZE_CANDIDATE", True,
    ),
    # --- Confirmed correct nulls ------------------------------------------
    (
        "computomic_no_amount", "Computomic announces strategic growth investment",
        "Computomic today announced a strategic growth investment to accelerate its "
        "platform. The company will expand its engineering team.",
        "NO_AMOUNT_DISCLOSED", None,
    ),
    (
        "emmecell_no_amount", "Emmecell announces investment",
        "Emmecell today announced an investment to advance its clinical programs. "
        "Terms were not disclosed.",
        "NO_AMOUNT_DISCLOSED", None,
    ),
    (
        "elektrik_no_amount", "Elektrik announces new investment from Lead Edge Capital",
        "Elektrik, the leading procurement platform for sourcing critical electrical "
        "infrastructure components, today announced a new investment from Lead Edge "
        "Capital, a growth equity firm.",
        "NO_AMOUNT_DISCLOSED", None,
    ),
    # --- The four confirmed FALSE POSITIVES -------------------------------
    # Each is a number that co-occurs with financing language but measures something
    # else. Proximity matched them; binding must not.
    (
        "airs_medical_investor_cumulative", "AIRS Medical announces investment",
        "AIRS Medical today announced a growth investment from Vertex Growth, a firm "
        "that has raised $65 billion to date across its funds.",
        # Investor-scoped AND cumulative. The investor-scope diagnosis is the sharper
        # one — it is the firm's money, not a figure about this event at all — so that
        # is the reason reported.
        "NOT_ROUND_INVESTOR_SCOPE", None,
    ),
    (
        "elektrik_firm_aum", "Elektrik announces new investment from Lead Edge Capital",
        "Elektrik today announced a new investment from Lead Edge Capital, a growth "
        "equity firm with $9 billion in assets under management.",
        "NOT_ROUND_INVESTOR_SCOPE", None,
    ),
    (
        "flutterwave_postmoney", "Flutterwave announces round",
        "Flutterwave announced new funding at a $3.2 billion post-money valuation.",
        "NOT_ROUND_VALUATION", None,
    ),
    (
        "fortus_portfolio", "Fortus announces growth investment",
        "Fortus received a growth investment from Palatine, an investment firm managing "
        "£400 million whose portfolio companies have generated £1.1 billion in revenue "
        "since inception.",
        "NOT_ROUND_INVESTOR_SCOPE", None,
    ),
    # --- The amount and a valuation in ONE sentence ------------------------
    # The raise must survive; only the valuation figure is disqualified. This is the
    # case that a whole-sentence rejection rule would get wrong.
    (
        "raise_and_valuation_together", "Northwind raises",
        "Northwind raised $250 million at a $3.2 billion post-money valuation.",
        "ROUND_SIZE_CANDIDATE", True,
    ),
    # --- Non-exact: representation gap ------------------------------------
    (
        "chronograph_over", "Chronograph announces growth investment",
        "Chronograph today announced a minority growth equity investment of over "
        "$140 million led by a global investment firm.",
        "NON_EXACT_AMOUNT", False,
    ),
    (
        "approximately", "Halcyon closes round",
        "Halcyon announced the closing of a financing round of approximately "
        "$80 million.",
        "NON_EXACT_AMOUNT", False,
    ),
    (
        "range", "Vertex raises",
        "Vertex raised between $40 million and $50 million in new funding this quarter.",
        "NON_EXACT_AMOUNT", False,
    ),
    # --- Other disqualifying classes --------------------------------------
    (
        "secondary", "Fund acquires stake",
        "The fund acquired the 12% stake held by an early backer for $25 million in a "
        "secondary sale.",
        "NOT_ROUND_SECONDARY", None,
    ),
    (
        "facility", "Company secures facility",
        "The company secured a $50 million credit facility to support working capital.",
        "NOT_ROUND_FACILITY", None,
    ),
    # --- Cellares, resolved: a check INSIDE a round -----------------------
    # Both figures are real and both are bound to the financing. The classifier must
    # surface the ROUND, not the check — this is the case that proves an investor's
    # contribution and the event's magnitude are separate facts that can coexist in one
    # sentence. A $50M check inside a $327M round leaves round_size = $327M.
    (
        "cellares_check_inside_round", "Cellares announces Series D investment",
        "Cellares announced that Prime Radiant Fund has made a $50 million growth "
        "equity investment in the company's Series D financing, bringing the total "
        "Series D to $327 million.",
        "ROUND_SIZE_CANDIDATE", True,
    ),
]


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _integrity_db(path: str, *, violating: bool) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE transaction_record (transaction_id TEXT PRIMARY KEY, "
        "v2_event_type TEXT, target_name TEXT, transaction_size REAL, "
        "transaction_size_basis TEXT)"
    )
    rows = [
        ("tc_ma", "ACQUISITION", "AcquiredCo", 450e6, "TRANSACTION_VALUE"),
        ("tc_round", "VC_ROUND", "RoundCo", 60e6, "ROUND_SIZE"),
    ]
    if violating:
        rows.append(("tc_bad", "VC_ROUND", "LeakedCo", 60e6, "TRANSACTION_VALUE"))
    conn.executemany(
        "INSERT INTO transaction_record VALUES (?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


def main() -> None:
    failures: list[str] = []

    # --- 1. Classification ------------------------------------------------
    for label, title, body, expected_class, expected_exact in CASES:
        result = classify(title, body)
        _check(failures, f"{label} classification", result["classification"], expected_class)
        _check(failures, f"{label} exact flag", result["exact"], expected_exact)
        if expected_class not in ("NO_AMOUNT_DISCLOSED",) and not result["evidence"]:
            failures.append(f"{label}: classified {expected_class} with no evidence quoted")

    # --- 1a. No pattern contains a control character ----------------------
    # A `\b` written inside a non-raw string becomes a literal backspace (0x08), which
    # silently disables the pattern: it then requires a backspace character that no
    # source text contains. Eight of them were introduced this way and went unnoticed
    # because the classifier still produced the right answer for the wrong reason.
    import scripts.review_funding_coverage as rfc
    groups = {
        "_BINDING": rfc._BINDING, "_ROUND_TOTAL": rfc._ROUND_TOTAL,
        "_CHECK_SHAPED": rfc._CHECK_SHAPED,
        "_SCOPE_MARKERS": [rx for rx, _l in rfc._SCOPE_MARKERS],
        "misc": [rfc._MONEY, rfc._RANGE, rfc._SECONDARY, rfc._FACILITY, rfc._INVESTOR_CHECK],
    }
    for group, patterns in groups.items():
        for rx in patterns:
            bad = [c for c in rx.pattern if ord(c) < 32 and c not in "\n\t"]
            if bad:
                failures.append(
                    f"{group}: pattern contains a control character {bad[0]!r} — a "
                    f"literal escape that silently disables it: {rx.pattern[:60]!r}"
                )

    # --- 1b. A check inside a round yields the ROUND ----------------------
    cellares_case = next(c for c in CASES if c[0] == "cellares_check_inside_round")
    cellares = classify(cellares_case[1], cellares_case[2])
    if cellares["amount"] == 50_000_000.0:
        failures.append(
            "Cellares resolved to Prime Radiant's $50M check rather than the $327M "
            "round — an investor's contribution is never the event's magnitude"
        )
    _check(failures, "Cellares resolves to the round", cellares["amount"], 327_000_000.0)

    # --- 2. A non-exact amount is never a round_size candidate ------------
    # The load-bearing assertion. Chronograph must not become an exact 140,000,000.
    chrono_case = next(c for c in CASES if c[0] == "chronograph_over")
    chrono = classify(chrono_case[1], chrono_case[2])
    if chrono["classification"] == "ROUND_SIZE_CANDIDATE":
        failures.append(
            "'over $140 million' was promoted to an exact round_size candidate — that "
            "asserts an exactness the source withheld"
        )
    _check(failures, "chronograph qualifier captured", chrono["qualifier"], "over")
    if "REPRESENTATION GAP" not in chrono["action"]:
        failures.append("non-exact amount must be flagged as a representation gap")
    # The amount is still surfaced for the human, just not proposed as canonical.
    _check(failures, "chronograph amount surfaced", chrono["amount"], 140_000_000.0)

    # No case in the taxonomy may propose writing a non-exact figure.
    for label, title, body, expected_class, _e in CASES:
        r = classify(title, body)
        if r["exact"] is False and "NULL" not in r["action"]:
            failures.append(f"{label}: non-exact amount without a leave-NULL action")

    # --- 3. Family-integrity invariant ------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        clean = str(Path(tmp) / "clean.db")
        _integrity_db(clean, violating=False)
        conn = sqlite3.connect(f"file:{clean}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        total, violations = family_integrity(conn)
        conn.close()
        _check(failures, "clean corpus TRANSACTION_VALUE count", total, 1)
        _check(failures, "clean corpus violations", len(violations), 0)

        dirty = str(Path(tmp) / "dirty.db")
        _integrity_db(dirty, violating=True)
        conn = sqlite3.connect(f"file:{dirty}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        total, violations = family_integrity(conn)
        conn.close()
        # The assertion must actually catch a funding row on the M&A rung.
        _check(failures, "violating corpus detected", len(violations), 1)
        if violations:
            _check(failures, "violation identified", violations[0]["transaction_id"], "tc_bad")

        # A database predating transaction_size must not crash the assertion.
        legacy = str(Path(tmp) / "legacy.db")
        lc = sqlite3.connect(legacy)
        lc.execute("CREATE TABLE transaction_record (transaction_id TEXT, v2_event_type TEXT)")
        lc.commit()
        lc.close()
        conn = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        total, violations = family_integrity(conn)
        conn.close()
        _check(failures, "legacy DB reports unavailable rather than crashing", total, -1)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS funding coverage review: taxonomy pinned, non-exact never promoted, "
          "family integrity asserted")


if __name__ == "__main__":
    main()
