#!/usr/bin/env python3
"""R4.1 — the Collection review sheet is the contract Product approved.

WHAT THIS IS FOR

This repo is the executable Product specification, not the production
implementation, and `ma_review.csv` is a Product QA surface. Its job is to let a
reviewer tell three things apart that all render as an absent value:

  * the model was asked and said nothing        -> an extraction finding
  * the field is captured and simply not shown  -> a review-export omission
  * the field has no author at all              -> an implementation gap

Sheet 1.0 carried 61 columns and could not distinguish them. A parity audit found
that 22 M&A facts and 4 funding facts cleared the whole pipeline -- prompt,
staging, observation ledger, canonical column -- and were merely not projected.
Sheet 1.1 shows them, so a blank in those columns now means the source was silent.

WHAT THIS FILE PINS, AND WHAT IT DELIBERATELY DOES NOT

It pins the approved contract: the exact column lists, the exclusions Product
ruled on, the readable display names the projection applies, the sheet version,
and that projecting a sheet stores nothing.

It does NOT attempt exhaustive coverage of every Stage-9-owned column. An earlier
draft did, and the reason it was dropped is worth recording: it made the review
sheet answer for the whole reference schema. A canonical column existing is not by
itself a Product-review requirement, and a control that says otherwise turns every
future engineering column into a review-sheet decision. Two exclusions are pinned
here because Product ruled on them specifically -- not because a column existed.

THE TWO RULINGS

  * derived-only multiples stay off the sheet until normalized / as-reported
    multiples are restored;
  * assumption-dependent implied equity and enterprise values stay off it while
    the assumed-100 pct semantics are under review.

Both are asserted as absences, so a later slice cannot quietly surface either.

R4.1 MOVES NO STORED VALUE

The projection is presentation. The aggregation layer is asserted untouched, and
the harness is asserted to insert into exactly one table and never to write to
`transaction_record` behind Stage 9's back.

Run from project root:
    python scripts/test_review_sheet_coverage.py
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import stages.aggregate as aggregate  # noqa: E402

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _load_harness():
    """Import the feeder without running it.

    Loaded by path rather than package import: scripts/ is not a package, and the
    module must be read as the harness actually ships, constants included.
    """
    path = ROOT / "scripts" / "run_collection_validation.py"
    spec = importlib.util.spec_from_file_location("_rcv", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The 22 M&A and 4 funding facts sheet 1.1 surfaces. Each one already cleared the
# whole pipeline before this change; only the projection was missing.
_ADDED_MA = (
    # party identity — entity-resolution QA
    "target_domain", "target_ticker",
    "acquirer_domain", "acquirer_ticker", "acquirer_description",
    "parent_seller_ticker", "parent_seller_description",
    # dates — a precision is half of a date fact, and none of it was visible
    "signing_date", "signing_date_precision",
    "announced_date_precision", "closed_date_precision", "rumor_date",
    # structure and characteristics
    "offer_mechanism", "is_going_private_outcome", "spin_split_type", "hostile",
    "is_minority", "has_earnout", "has_cvr",
    # value
    "deal_value_currency", "transaction_size", "transaction_size_basis",
)
_ADDED_FUNDING = ("closed_date", "announced_date_precision", "closed_date_precision",
                  "company_domain")

# Ruled off the sheet by Product, each for its own reason.
_MULTIPLES = ("ev_to_revenue_ltm", "ev_to_revenue_ntm", "ev_to_ebitda_ltm",
              "ev_to_ebitda_ntm", "multiple_quality")
_ASSUMPTION_DEPENDENT = ("implied_equity_value", "implied_enterprise_value",
                         "implied_enterprise_value_basis")

# Canonical column -> the readable name the projection shows it under. Asserted
# against the projection's own source so a rename cannot silently drop a field.
_ALIASED = {
    "transaction_status": "status",
    "event_history_type": "event_type",
    "v2_event_type": "deal_type",
    "target_type_v2": "target_type",
    "spin_split_type_v2": "spin_split_type",
    "target_revenue_period_type_v2": "target_revenue_period_type",
    "target_ebitda_period_type_v2": "target_ebitda_period_type",
}


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    h = _load_harness()
    ma, funding = list(h._MA_COLS), list(h._FUNDING_COLS)
    ma_set, funding_set = set(ma), set(funding)
    src = (ROOT / "scripts" / "run_collection_validation.py").read_text(encoding="utf-8")

    # ------------------------------------------------------- 1. the approved shape
    print("\nThe approved column set, exactly:")
    # 83 at sheet 1.1; deal_rationale made it 84 when Stage 13 joined the run.
    check("M&A sheet is 84 columns", len(ma), 84)
    check("funding sheet is 45 columns", len(funding), 45)
    check("no duplicate M&A columns", len(ma), len(ma_set))
    check("no duplicate funding columns", len(funding), len(funding_set))
    check("review sheet version", getattr(h, "_REVIEW_SHEET_VERSION", None), "1.2")

    print("\nThe 22 M&A facts sheet 1.1 surfaces:")
    for col in _ADDED_MA:
        check(col, col in ma_set, True)
    print("\nThe 4 funding facts sheet 1.1 surfaces:")
    for col in _ADDED_FUNDING:
        check(col, col in funding_set, True)

    # ------------------------------------------------------- 2. the two rulings
    print("\nRuling: derived-only multiples stay off until R3.2/R3.3 restore them:")
    for col in _MULTIPLES:
        check(f"{col} absent from both sheets", col in ma_set or col in funding_set, False)

    print("\nRuling: assumption-dependent implied values stay off while pct is parked:")
    for col in _ASSUMPTION_DEPENDENT:
        check(f"{col} absent from both sheets", col in ma_set or col in funding_set, False)

    # ------------------------------------------------------- 3. display names
    print("\nThe projection shows MVP scalars under their readable names:")
    for canonical, shown in _ALIASED.items():
        check(f"{shown} is projected from {canonical}",
              f'"{shown}": t["{canonical}"]' in src, True)
        check(f"{canonical} is not also emitted raw",
              canonical in ma_set or canonical in funding_set, False)
    check("company_* names are projected from the target_* columns",
          all(f'"{shown}": t["{canon}"]' in src for shown, canon in
              (("company_name", "target_name"), ("company_domain", "target_domain"),
               ("company_description", "target_description"))), True)
    check("no mvp_ column anywhere", [c for c in ma + funding if c.startswith("mvp_")], [])
    check("no transaction-level acquirer_type — a standing decision",
          "acquirer_type" in ma_set or "acquirer_type" in funding_set, False)

    # ------------------------------------------------------- 4. nothing removed
    print("\nNothing a reviewer relied on in sheet 1.0 was removed:")
    for col in ("source_ref", "source_url", "transaction_id", "status", "event_type",
                "announced_date", "closed_date", "deal_type", "target_name",
                "acquirer_name", "pct_acquired", "pct_acquired_source",
                "stake_transition_type", "transaction_value", "transaction_value_basis",
                "equity_value", "enterprise_value", "per_share_price",
                "buy_side_advisors", "sell_side_advisors", "advisors_side_not_established",
                "deal_summary", "overall_review", "missing_or_wrong_fields",
                "review_notes"):
        check(f"{col} still on the M&A sheet", col in ma_set, True)
    for col in ("investors", "lead_investors", "investor_amounts", "round_size",
                "pre_money_valuation", "post_money_valuation", "funding_advisors",
                # Both are permanently blank -- no prompt authors either. They stay
                # rather than being removed: the sheet has always shown them, and
                # dropping two columns a reviewer expects is a worse surprise than a
                # blank one. R1.4 is what fills them.
                "has_board_seat", "use_of_proceeds"):
        check(f"{col} still on the funding sheet", col in funding_set, True)
    check("the rejection sheet is untouched", list(h._REJECTION_COLS),
          ["source_ref", "title", "url", "classification", "reason_code",
           "model_confidence", "relevancy_notes"])

    # ------------------------------------------------------- 5. presentation only
    print("\nProjecting a sheet stores nothing:")
    check("Stage 9 still owns 120 canonical columns",
          len(aggregate._STAGE9_OWNED_COLUMNS), 120)
    check("the harness writes to exactly one table",
          len(re.findall(r"INSERT INTO", src)), 1)
    check("and that table is source_raw", "INSERT INTO source_raw" in src, True)
    # The harness imports and runs Stage 9 -- that is the pipeline. What it must never
    # do is edit Stage 9's output behind the stage's back.
    check("never updates transaction_record",
          bool(re.search(r"UPDATE\s+transaction_record", src)), False)
    check("never deletes from transaction_record",
          bool(re.search(r"DELETE\s+FROM\s+transaction_record", src)), False)
    # Eight when this file was written; nine since Stage 13 joined. The point of the
    # check is that projecting a sheet does not add stages, not the number itself.
    check("still runs the declared stage list", len(h.PIPELINE), 9)
    check("the sheet version is emitted with the run, not onto every row",
          '"review_sheet_version": _REVIEW_SHEET_VERSION' in src
          and "review_sheet_version" not in ma_set | funding_set, True)

    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — sheet 1.1 is the approved contract; both exclusions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
