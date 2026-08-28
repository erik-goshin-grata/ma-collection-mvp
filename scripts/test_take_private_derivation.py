#!/usr/bin/env python3
"""Stage 9 take-private flag derivation — unit cases plus the production path.

`is_take_private` means: a PUBLIC, STANDALONE_COMPANY target, acquired by a buyer whose
type satisfies the private-ownership condition, where the SOURCE AFFIRMATIVELY ESTABLISHES
that the target ceases to have publicly held/traded equity. Three conditions, all required.

THE THIRD CONDITION IS NEW AND IS THE POINT OF THIS REVISION.

Before it, the derivation reached 1 on the first two conditions alone. That made every
private-strategic acquisition of a public company a take-private, and it could not
distinguish a sponsor's control investment in a still-listed company from a genuine
privatization. It also carried an acquirer-ticker guard that was never a proxy for the
buyer being private: a listed sponsor (Blackstone, EQT, Apollo) taking a company private is
a genuine take-private, and the guard returned 0 for every one of them. The guard is gone.

The earlier version of this file certified the behaviour Product has now ruled wrong -- it
asserted `private_strategic_take_private` = 1 and `public_acquirer_blocks_flag` = 0 -- so it
would have blocked this fix. Both are inverted below, deliberately.

`is_going_private_outcome` is affirmative-evidence-only: `true | null`, never persisted as
0. The model is not asked to establish that a target REMAINS public, so a model-emitted
`false` is normalized to NULL by Stage 4 before persistence. That normalization is pinned
end-to-end below, in both polarities, through the real Stage 4 -> observation ledger ->
Stage 9 -> canonical chain: `false` must reach canonical NULL with NO ledger row, and `true`
must survive intact, so the normalization cannot silently suppress affirmative evidence.

TWO LAYERS, AND THE SECOND ONE IS THE POINT.

The CASES table below tests `_derive_flags()` directly. That layer is kept because it is a
cheap guard on the condition logic, but on its own it certified a field that was broken in
production for every transaction: it hands the derivation hand-authored UPPERCASE values,
while Stage 3 and Stage 4 store what the prompts emit, which is LOWERCASE. The comparison in
`aggregate.py` was written against the uppercase form, so `target_type != "STANDALONE_COMPANY"`
returned early on every real row and the flag was 0 everywhere. The unit layer could not see
that, because it never touched storage.

The production-path section therefore drives the real chain:

    staging_extraction -> production observation writer (include_stage3 / include_hc)
                       -> observation ledger -> Stage 9 at the CONFIGURED read source
                       -> canonical transaction_record.is_take_private

with the values the shipped prompts actually produce. One qualifying transaction must reach
1; four controls must stay 0 through the identical path, so the proof is isolation rather
than "the flag can be made truthy". A separate legacy-uppercase case pins read tolerance:
stored rows may still carry the uppercase form, and repairing new-production casing must not
strand them.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
import stages.high_confidence_extract as hc
from stages.aggregate import _derive_flags, _load_sponsor_participant_context
from lib.observation_writer import (
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)


def _case(**overrides: object) -> dict:
    """Base case satisfies all three conditions; overrides break one at a time.

    `is_going_private_outcome` is in the base because it is now required -- omitting it
    would silently make every "positive" case a negative and hide a broken condition.
    """
    base = {
        "deal_type": "ACQUISITION",
        "target_status": "PUBLIC",
        "target_type": "STANDALONE_COMPANY",
        "acquirer_type": "PRIVATE_EQUITY",
        "acquirer_ticker": None,
        "is_going_private_outcome": 1,
    }
    base.update(overrides)
    return base


def _no_alias(case: dict) -> dict:
    """Drop the legacy `deal_type` alias, leaving only `v2_event_type`.

    This is the shape Stage 3 produces from classifier 0.14 onward. `_case()` seeds
    `deal_type` because stored rows still carry it; a derivation that reads the alias
    directly passes those and fails these.
    """
    out = dict(case)
    alias = out.pop("deal_type", None)
    out.setdefault("v2_event_type", alias)
    return out


# The five qualifying buyer types. Every other acquirer_type is out BY TYPE ALONE.
_QUALIFYING = ("PRIVATE_EQUITY", "PE_PORTFOLIO", "MANAGEMENT", "EMPLOYEE_GROUP",
               "OTHER_FINANCIAL_SPONSOR")
# strategic_corporate and consortium are the two that CHANGED verdict. The rest were never
# positive, but they are enumerated so that widening the qualifying set silently is a test
# failure rather than a discovery in production.
#
# CONSORTIUM is here as LEGACY READABILITY, not as current vocabulary. HC 0.27 retired it
# from the accepted acquirer types and its owning stage now maps a newly-emitted value to
# `unknown`, so no new row can carry it. Stored rows still do, and this derivation runs over
# stored rows -- it must keep returning a safe non-qualifying verdict for them. Removing this
# case because the value is retired would leave historical data deriving unchecked.
_NON_QUALIFYING = ("STRATEGIC_CORPORATE", "CONSORTIUM", "VENTURE_CAPITAL", "INDIVIDUAL",
                   "FAMILY_OFFICE", "HEDGE_FUND", "PENSION_FUND", "SOVEREIGN_WEALTH_FUND",
                   "GROWTH_EQUITY", "SPAC", "UNKNOWN")

CASES = [
    # --- positives: all three conditions met -------------------------------------
    *[(f"qualifying_{t.lower()}_take_private", _case(acquirer_type=t), 1) for t in _QUALIFYING],
    # The ticker guard is gone. A LISTED sponsor taking a company private is a genuine
    # take-private; this returned 0 before and was a false-negative class, not noise.
    ("listed_sponsor_ticker_does_not_block",
     _case(acquirer_type="PRIVATE_EQUITY", acquirer_ticker="NYSE:BX"), 1),
    # sponsor_transaction_role stays orthogonal: an ADD_ON can also be a take-private.
    ("add_on_can_also_be_take_private",
     _case(acquirer_type="PE_PORTFOLIO", sponsor_transaction_role="ADD_ON"), 1),

    # --- negatives: buyer-side condition fails -----------------------------------
    # Both of these were POSITIVES in the previous revision. strategic_corporate produced
    # the two wrong MPS positives in the PL integration run; consortium is non-qualifying
    # because bare `consortium` establishes no sponsor character anywhere in the data model
    # (representation gap, recorded -- deliberately not proxied).
    *[(f"non_qualifying_{t.lower()}_not_take_private", _case(acquirer_type=t), 0)
      for t in _NON_QUALIFYING],

    # --- negatives: outcome condition fails --------------------------------------
    # The class the previous derivation could not see at all.
    ("pe_control_investment_still_listed",
     _case(acquirer_type="PRIVATE_EQUITY", pct_acquired=60, is_going_private_outcome=None), 0),
    ("pe_acquisition_no_outcome_evidence",
     _case(acquirer_type="PRIVATE_EQUITY", is_going_private_outcome=None), 0),
    # A persisted 0 must never exist for this field, but if one ever did it must read as
    # "not established", never as evidence.
    ("outcome_zero_is_not_evidence",
     _case(acquirer_type="PRIVATE_EQUITY", is_going_private_outcome=0), 0),

    # Classifier 0.14 stopped asking the model to author `deal_type`. New rows carry
    # `v2_event_type` and a `deal_type` column written from the resolved value; this case
    # drops the alias entirely, which is what the derivation must survive. It failed before
    # the derivation moved to the resolved-event-type helper.
    ("v2_event_type_alone_no_deal_type_alias", _no_alias(_case()), 1),
    ("v2_event_type_alone_non_acquisition", _no_alias(_case(v2_event_type="MERGER")), 0),

    # --- negatives: other conditions, unchanged by this revision ------------------
    ("public_public_merger_not_take_private", _case(deal_type="MERGER", acquirer_type="STRATEGIC_CORPORATE"), 0),
    ("public_target_asset_sale_not_take_private", _case(target_type="ASSETS", acquirer_type="PRIVATE_EQUITY"), 0),
    ("public_target_subsidiary_sale_not_take_private", _case(target_type="SUBSIDIARY", acquirer_type="PRIVATE_EQUITY"), 0),
    ("minority_investment_not_take_private", _case(deal_type="MINORITY_INVESTMENT", acquirer_type="PRIVATE_EQUITY"), 0),
    ("private_target_status_not_take_private", _case(target_status="PRIVATE"), 0),
]



# ---------------------------------------------------------------------------
# Production path: staging -> observations -> configured Stage 9 -> canonical
# ---------------------------------------------------------------------------
#
# Values below are the ones the SHIPPED prompts emit. deal_type and target_status are
# genuinely UPPERCASE in production (Stage 3 emits ACQUISITION / PUBLIC); target_type and
# acquirer_type are LOWERCASE (deal_type_classifier and high_confidence_extraction both
# emit the V2 lowercase vocabulary). Mixing the two casings here is not sloppiness -- it is
# what a real row looks like, and the mismatch between the two lowercase fields and the
# uppercase comparisons in aggregate.py is the whole defect.
#
# Note which column carries which value. Stage 3 writes the RAW model output to
# `target_type` and the normalized value to `target_type_v2`; Stage 4 does the same with
# `acquirer_type` / `acquirer_type_v2`. The derivation reads the legacy-named column, so
# that column is what must be seeded to reproduce production.

_PROD_CASES = [
    # (label, expected, overrides)
    ("positive_pe_lowercase", 1, {}),
    # The ticker no longer blocks: a listed sponsor can take a company private. This row was
    # a 0-expecting control in the previous revision and is inverted deliberately.
    ("listed_sponsor_ticker_does_not_block", 1, {"acquirer_ticker": "NYSE:BX"}),
    ("control_assets_target", 0, {"target_type": "assets", "target_type_v2": "assets"}),
    ("control_private_target_status", 0, {"target_status": "PRIVATE"}),
    ("control_minority_investment", 0, {"deal_type": "MINORITY_INVESTMENT",
                                        "v2_event_type": "MINORITY_INVESTMENT"}),
    # Buyer-side condition through the production chain. This is the MPS reproduction: a
    # private strategic buying a public standalone company, which reached 1 before.
    ("control_strategic_corporate_buyer", 0, {"acquirer_type": "strategic_corporate",
                                              "acquirer_type_v2": "strategic_corporate"}),
    # Outcome condition through the production chain. Everything else qualifies; only the
    # affirmative outcome is missing, so the observation is simply absent.
    ("control_no_outcome_evidence", 0, {"is_going_private_outcome": None}),
    # Kept deliberately separate from the controls: this is not a V3-production row.
    # It proves that repairing new-production casing does not strand rows already stored in
    # the legacy uppercase form, which Stage 3 and Stage 4 still accept.
    ("legacy_uppercase_read_tolerance", 1, {"target_type": "STANDALONE_COMPANY",
                                            "acquirer_type": "PRIVATE_EQUITY"}),
]


def _run_production_case(label: str, overrides: dict) -> int | None:
    """Seed one transaction, run the real chain, return canonical is_take_private."""
    txn = f"tc_tp_{label}"
    db_path = os.path.join(tempfile.mkdtemp(), "tp.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        row = {
            "source_raw_id": srid,
            "status": "CLUSTERED",
            "deal_type": "ACQUISITION",           # uppercase in production
            "v2_event_type": "ACQUISITION",       # uppercase in production
            "event_history_type": "ANNOUNCED",
            "target_status": "PUBLIC",            # uppercase in production
            "target_type": "standalone_company",      # lowercase in production
            "target_type_v2": "standalone_company",
            "target_name": "Verity Biosciences",
            "target_ticker": "NASDAQ: VRTY",
            "acquirer_name": "Halden Capital Partners",
            "acquirer_ticker": None,              # no public listing -> stays private
            "acquirer_type": "private_equity",        # lowercase in production
            "acquirer_type_v2": "private_equity",
            "is_going_private_outcome": 1,            # affirmative outcome evidence
            "announced_date": "2026-08-18",
            "announced_date_precision": "exact",
            "financials_disclosure_status": "UNKNOWN",
            "transaction_terms_disclosure_status": "UNKNOWN",
            "model_confidence": "HIGH",
            "dt_prompt_version": "0.11",
            "hc_prompt_version": "0.20",
            "transaction_cluster_id": txn,
        }
        row.update(overrides)
        cols = [c for c in row if row[c] is not None]
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Production writer with the production flags -- not a local field list.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "take-private-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT is_take_private FROM transaction_record WHERE transaction_id=?",
            (txn,)).fetchone()
        return None if canon is None else canon["is_take_private"]
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Stage 4 normalization: model `false` must never be persisted
# ---------------------------------------------------------------------------
#
# `is_going_private_outcome` is `true | null`. The model is never asked to establish that a
# target REMAINS publicly traded, so `false` is the model answering a question we did not
# ask -- not an observed negative. Stage 4 normalizes it to NULL before persistence.
#
# This matters structurally, not just cosmetically: lib/observation_writer skips None but
# NOT 0, so a persisted 0 would author a real ledger row and land a canonical 0 that reads
# as observed evidence that the target stays public. That is the `hostile` failure V3 §T11
# removed, and it is what this test exists to prevent regressing.
#
# Both polarities run through the SAME real chain -- Stage 4 (with only the model transport
# stubbed) -> production observation writer -> Stage 9 at the configured read source ->
# canonical. `true` is pinned as hard as `false`: a normalization that quietly swallowed
# affirmative evidence would pass a false-only test.


def _hc_response(outcome: object) -> dict:
    """One valid HC response whose only variable is features.is_going_private_outcome."""
    return {
        "transactions": [{
            "target": {"name": "Verity Biosciences", "domain": None,
                       "ticker": "NASDAQ: VRTY", "description": "Target company.",
                       "asset_type": None},
            "acquirer": {"name": "Halden Capital Partners", "domain": None, "ticker": None,
                         "type": "private_equity", "description": "Sponsor.",
                         "sponsor_name": None},
            "parent_seller": {"name": None, "ticker": None, "description": None},
            "deal": {"pct_acquired": None, "stake_transition_type": None,
                     "offer_mechanism": None, "sponsor_transaction_role": None},
            "dates": {"announced_date": "2026-08-18", "announced_date_precision": "exact",
                      "closed_date": None, "closed_date_precision": None,
                      "signing_date": None, "signing_date_precision": None,
                      "rumor_date": None},
            "value": {"amount": None, "currency": None, "type": "UNDISCLOSED",
                      "type_confidence": "HIGH", "qualifier": None, "per_share_price": None},
            "reported_multiples": [],
            "acquirers": [],
            "buy_side_sponsors": [],
            "parent_sellers": [],
            "parent_acquirers": [],
            "sell_side_sponsors": [],
            "sellers": [],
            "value_observations": [],
            "features": {"is_secondary_buyout": None, "is_merger_of_equals": None,
                         "is_going_private_outcome": outcome},
            "target_financials": {"revenue_amount": None, "revenue_period_type": None,
                                  "revenue_period_end": None, "ebitda_amount": None,
                                  "ebitda_period_type": None, "ebitda_period_end": None,
                                  "currency": None},
            "financials_disclosure_status": "UNDISCLOSED",
            "transaction_terms_disclosure_status": "UNDISCLOSED",
            "consideration_type": None,
            "model_confidence": "HIGH",
            "notes": None,
        }]
    }


def _run_stage4_case(label: str, outcome: object) -> dict:
    """Drive real Stage 4 with a stubbed transport, then the real chain to canonical."""
    txn = f"tc_gpo_{label}"
    db_path = os.path.join(tempfile.mkdtemp(), "gpo.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u-gpo','t-gpo','2026-08-18','body','RELEVANT',?)",
            (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO staging_extraction
                (source_raw_id, status, deal_type, v2_event_type, event_type,
                 event_history_type, target_status, target_type, target_type_v2,
                 dt_prompt_version, transaction_cluster_id)
            VALUES (?, 'CLASSIFIED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCEMENT',
                    'ANNOUNCED', 'PUBLIC', 'standalone_company', 'standalone_company',
                    'deal_type_classifier:test', ?)
            """,
            (srid, txn),
        )
        conn.commit()

        real_call, real_sleep = hc.call_prompt, hc._SLEEP
        hc.call_prompt, hc._SLEEP = (lambda **_k: _hc_response(outcome)), 0
        try:
            hc.run(conn=conn, cfg=SimpleNamespace(log_level="ERROR"), run_id=f"gpo_{label}")
        finally:
            hc.call_prompt, hc._SLEEP = real_call, real_sleep

        staged = conn.execute(
            "SELECT is_going_private_outcome FROM staging_extraction WHERE source_raw_id=?",
            (srid,)).fetchone()["is_going_private_outcome"]

        # Stand in for Stage 8, which is what promotes HC_EXTRACTED -> CLUSTERED and assigns
        # transaction_cluster_id. The id is seeded at insert above; only the status gate is
        # left, and Stage 9 reads no other clustering output. Stage 4 and Stage 9 themselves
        # are real.
        conn.execute("UPDATE staging_extraction SET status='CLUSTERED' WHERE source_raw_id=?",
                     (srid,))
        backfill_observation_transaction_ids(conn)
        conn.commit()
        ledger_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM transaction_field_observation"
            " WHERE field_name='is_going_private_outcome'").fetchone()["n"]

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, f"gpo_{label}")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT is_going_private_outcome, is_take_private FROM transaction_record"
            " WHERE transaction_id=?", (txn,)).fetchone()
        return {
            "staging": staged,
            "ledger_rows": ledger_rows,
            "canonical": None if canon is None else canon["is_going_private_outcome"],
            "is_take_private": None if canon is None else canon["is_take_private"],
        }
    finally:
        conn.close()


def _test_outcome_normalization(failed: list) -> None:
    # `false` is normalized away entirely: nothing persisted, nothing observed, flag 0.
    got = _run_stage4_case("false", False)
    want = {"staging": None, "ledger_rows": 0, "canonical": None, "is_take_private": 0}
    if got != want:
        failed.append(("stage4/model_false_normalized_to_null", want, got))

    # `true` survives the same path intact -- the normalization must not suppress evidence.
    got = _run_stage4_case("true", True)
    want = {"staging": 1, "ledger_rows": 1, "canonical": 1, "is_take_private": 1}
    if got != want:
        failed.append(("stage4/model_true_survives_production_path", want, got))

    # null behaves exactly as false does, which is the whole point of normalizing.
    got = _run_stage4_case("null", None)
    want = {"staging": None, "ledger_rows": 0, "canonical": None, "is_take_private": 0}
    if got != want:
        failed.append(("stage4/model_null_not_established", want, got))


def _test_production_path(failed: list) -> None:
    src = DEFAULT_AGGREGATION_READ_SOURCE
    for label, expected, overrides in _PROD_CASES:
        got = _run_production_case(label, overrides)
        if got is None:
            failed.append((f"production/{label}", expected, "no transaction_record row"))
        elif got != expected:
            failed.append((f"production/{label} (read_source={src})", expected, got))


def main() -> None:
    failed = []
    for name, fields, expected in CASES:
        actual = _derive_flags(fields)["is_take_private"]
        if actual != expected:
            failed.append((name, expected, actual))

    # The two is_platform_investment cases that stood here were retired with the field
    # (V3 §T7, S-G). They asserted NEW production authorship of a flag Stage 9 no longer
    # writes, so keeping them would pin behaviour the decision removed. Their subject matter
    # -- platform evidence -- now lives in scripts/test_sponsor_transaction_role.py, and the
    # retained is_platform_investment column and its stored rows are untouched by this.
    feature_cases = [
        (
            "secondary_explicit_true",
            _case(is_secondary_buyout=1, acquirer_type="PRIVATE_EQUITY"),
            "is_secondary_buyout",
            1,
        ),
        (
            "secondary_side_qualified_sponsors_true",
            _case(
                acquirer_type="PRIVATE_EQUITY",
                _has_buyer_sponsor_party=1,
                _has_seller_sponsor_party=1,
            ),
            "is_secondary_buyout",
            1,
        ),
        (
            "secondary_pe_buyer_alone_false",
            _case(acquirer_type="PRIVATE_EQUITY"),
            "is_secondary_buyout",
            0,
        ),
        (
            "secondary_one_sponsor_side_false",
            _case(acquirer_type="PRIVATE_EQUITY", _has_buyer_sponsor_party=1),
            "is_secondary_buyout",
            0,
        ),
        (
            "secondary_non_ma_even_with_sponsors_false",
            _case(
                deal_type="GROWTH_EQUITY",
                acquirer_type="PRIVATE_EQUITY",
                _has_buyer_sponsor_party=1,
                _has_seller_sponsor_party=1,
            ),
            "is_secondary_buyout",
            0,
        ),
        (
            "moe_explicit_true",
            _case(deal_type="MERGER", is_merger_of_equals=1),
            "is_merger_of_equals",
            1,
        ),
        (
            "moe_merger_structure_alone_false",
            _case(deal_type="MERGER"),
            "is_merger_of_equals",
            0,
        ),
    ]
    for name, fields, flag_name, expected in feature_cases:
        actual = _derive_flags(fields)[flag_name]
        if actual != expected:
            failed.append((name, expected, actual))

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE transaction_participant (
            transaction_id TEXT,
            party_name TEXT,
            side TEXT,
            participant_role TEXT,
            is_current INTEGER
        )
        """
    )
    conn.executemany(
        "INSERT INTO transaction_participant (transaction_id, party_name, side, participant_role, is_current) VALUES (?,?,?,?,?)",
        [
            ("side_qualified", "Buyer Sponsor", "BUYER", "BUYER_SPONSOR", 1),
            ("side_qualified", "Seller Sponsor", "SELLER", "SELLER_SPONSOR", 1),
            ("generic_mentions", "Buyer Sponsor", None, "BUYER_SPONSOR", 1),
            ("generic_mentions", "Seller Sponsor", None, "SELLER_SPONSOR", 1),
        ],
    )
    context = _load_sponsor_participant_context(conn)
    if context.get("side_qualified") != {"_has_buyer_sponsor_party": 1, "_has_seller_sponsor_party": 1}:
        failed.append(("secondary_loader_side_qualified", {"buyer": 1, "seller": 1}, context.get("side_qualified")))
    generic_fields = _case(acquirer_type="PRIVATE_EQUITY")
    generic_fields.update(context.get("generic_mentions", {}))
    if _derive_flags(generic_fields)["is_secondary_buyout"] != 0:
        failed.append(("secondary_loader_generic_mentions", 0, context.get("generic_mentions")))

    _test_production_path(failed)
    _test_outcome_normalization(failed)

    if failed:
        for name, expected, actual in failed:
            print(f"FAIL {name}: expected {expected}, got {actual}")
        raise SystemExit(1)

    print(f"PASS transaction feature derivation  unit={len(CASES) + len(feature_cases)}"
          f"  production-path={len(_PROD_CASES)}  stage4-normalization=3")


if __name__ == "__main__":
    main()
