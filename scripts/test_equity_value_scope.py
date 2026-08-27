#!/usr/bin/env python3
"""Regression guard: `equity_value` is stake-level, and only stake-level.

No network and no model calls.

`equity_value` is defined as the consideration for the stake actually acquired
(§4.2), while `implied_equity_value` is the 100%-basis figure. Two writers could
put a whole-company amount into the stake-level field, and nothing downstream
could tell the difference:

1. **The prompt admitted market capitalization as an `EQUITY_VALUE`.** A market cap
   is whole-company. It now has its own type, `MARKET_CAPITALIZATION`, which is
   retained as a fact but never routed into canonical consideration.
2. **`PER_SHARE_X_SHARES` is 100%-basis by construction** — per-share price times the
   target's *total* fully diluted share count is the price of 100% of the equity, not
   of the stake. It is now gated to `pct_acquired == 100`, the only case where the two
   coincide.

The damage both caused is the same: `_derive_implied_equity` divides by pct, so a
figure already at 100% is grossed up a second time. A $2.2B market cap at pct 27
yields $8.15B of implied equity, and a multiple struck off that is manufactured.

**The gate must not scale.** Scaling `per_share x total_shares` by pct would invent a
stake amount no source stated — we hold total shares, never acquired shares. Below
100 the correct output is None, and the assertions below pin that rather than any
scaled value.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stages.aggregate as aggregate
from db import get_connection, init_db
from lib.observation_writer import write_staging_observations_for_extraction
from stages.high_confidence_extract import _VALID_VALUE_TYPES, _validate, _value_observations_json

REPO = Path(__file__).resolve().parents[1]
PROMPT = REPO / "prompts" / "high_confidence_extraction.md"

STAKE_TXN = "tc_equity_scope_stake"
MARKETCAP_TXN = "tc_equity_scope_marketcap_only"


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


# --------------------------------------------------------------------------
# A. The PER_SHARE_X_SHARES gate
# --------------------------------------------------------------------------
def _check_per_share_gate(failures: list[str]) -> None:
    # The PER_SHARE_X_SHARES basis is gone (aggregation 0.11), so the gate it needed is
    # gone with it. The branch never produced a value in this implementation --
    # `sec_shares` was hardcoded None because SEC enrichment does not run -- and what it
    # computed was not what its name claimed: per-share x TOTAL shares outstanding
    # prices the whole company, and the pct == 100 gate was compensating for the count
    # rather than establishing anything. What this section still has to prove is that
    # NOTHING reconstructs a stake-level equity value out of a per-share price.
    no_stated = {"value_amount": None, "value_type": None,
                 "per_share_price": 40.0}      # x 50,000,000 shares = 2,000,000,000
    value, basis = aggregate._derive_equity_value(no_stated)
    _check(failures, "a per-share price alone yields no equity value", value, None)
    _check(failures, "and no basis", basis, None)
    # The BODY, not the docstring: the docstring deliberately explains why the basis was
    # removed, and that explanation should not be what makes this check pass or fail.
    import inspect
    body = inspect.getsource(aggregate._derive_equity_value).split('"""')[-1]
    _check(failures, "PER_SHARE_X_SHARES is no longer returnable",
           "PER_SHARE_X_SHARES" in body, False)

    # A source-stated equity figure is stake-level by definition and still populates.
    stated = {"value_amount": 600_000_000, "value_type": "EQUITY_VALUE"}
    value, basis = aggregate._derive_equity_value(stated)
    _check(failures, "stated equity value", value, 600_000_000.0)
    _check(failures, "stated equity basis", basis, "STATED")

    # A market cap in the legacy slot must never become equity_value.
    market_cap = {"value_amount": 2_200_000_000, "value_type": "MARKET_CAPITALIZATION"}
    value, basis = aggregate._derive_equity_value(market_cap)
    _check(failures, "market cap in legacy slot -> equity_value", value, None)
    _check(failures, "market cap in legacy slot -> basis", basis, None)


# --------------------------------------------------------------------------
# B. Taxonomy: the type exists, and the prompt no longer conflates it
# --------------------------------------------------------------------------
def _check_taxonomy(failures: list[str]) -> None:
    # Still required in the stage frozenset, now as TOLERANCE rather than authorization:
    # prompt 0.28 retired the type, and _validate fails a whole extraction on an unknown
    # value type, so delisting it here would turn a model still emitting it into a total
    # loss of that transaction rather than the loss of one unsupported observation.
    if "MARKET_CAPITALIZATION" not in _VALID_VALUE_TYPES:
        failures.append(
            "MARKET_CAPITALIZATION missing from _VALID_VALUE_TYPES — an extraction "
            "emitting it would be rejected wholesale, not just dropped"
        )

    text = PROMPT.read_text(encoding="utf-8")

    # The EQUITY_VALUE definition runs to the next type in the vocabulary.
    start = text.find("EQUITY_VALUE — ")
    end = text.find("TRANSACTION_VALUE — ", start)
    if start == -1 or end == -1:
        failures.append("could not locate the EQUITY_VALUE definition in the prompt")
    else:
        definition = text[start:end].lower()
        if "market cap" in definition:
            failures.append(
                "prompt still admits market capitalization as an EQUITY_VALUE — that is "
                "a whole-company figure in a stake-level field"
            )

    # Inverted at 0.28. The type was never a Product-approved transaction field; it was
    # engineering containment to keep market caps out of `equity_value`. That job is now
    # done by the supported-concept boundary, which declines to capture the figure at all.
    from prompts.base import load_prompt_file
    delivered = load_prompt_file("high_confidence_extraction")["system"]
    if "MARKET_CAPITALIZATION" in delivered:
        failures.append("prompt still offers MARKET_CAPITALIZATION as a value type — 0.28 "
                        "retired it from current authoring")
    if "WHAT IS NOT A DEAL-VALUE FACT" not in delivered:
        failures.append("prompt lost the supported-concept boundary that replaced the type")

    # The invariant is that the version moved to or past the release that introduced
    # MARKET_CAPITALIZATION -- not that the prompt is frozen there. Compare numerically:
    # these are dotted decimals, so "0.10" > "0.9" and any string comparison is wrong.
    m = re.search(r"^\*\*Version:\*\* (\d+)\.(\d+)", text, re.M)
    if m is None:
        failures.append("HC prompt has no parseable version line")
    elif (int(m.group(1)), int(m.group(2))) < (0, 28):
        failures.append(f"HC prompt version {m.group(0)!r} predates the retirement of "
                        f"MARKET_CAPITALIZATION from current authoring (0.28)")


# --------------------------------------------------------------------------
# C / D. End-to-end through aggregation
# --------------------------------------------------------------------------
def _hc_result(*, with_stake_equity: bool) -> dict:
    """A 27% purchase where the source also states the target's market cap.

    Deliberately the Pinnacle Gas shape: a stake consideration far below the
    whole-company figure sitting in the same article.
    """
    observations = []
    if with_stake_equity:
        observations.append({
            "amount": 600_000_000, "currency": "USD", "type": "EQUITY_VALUE",
            "basis": "STATED", "qualifier": None,
            "evidence": "acquired a 27% stake for $600 million in cash",
        })
    observations.append({
        "amount": 2_200_000_000, "currency": "USD", "type": "MARKET_CAPITALIZATION",
        "basis": "STATED", "qualifier": None,
        "evidence": "Pinnacle Gas, which has a market capitalization of $2.2 billion",
    })
    primary = observations[0]
    return {
        "target": {"name": "Pinnacle Gas Corp."},
        "acquirer": {"name": "Meridian Holdings", "type": "strategic_corporate"},
        "parent_seller": {},
        "deal": {"pct_acquired": 27.0, "stake_transition_type": "NEW_MINORITY_STAKE"},
        "dates": {"announced_date": "2026-08-12", "announced_date_precision": "exact"},
        "value": {
            "amount": primary["amount"], "currency": "USD", "type": primary["type"],
            "type_confidence": "HIGH", "qualifier": None, "per_share_price": None,
        },
        "reported_multiples": [],
        "value_observations": observations,
        "features": {
            "is_platform_investment": None,
            "is_secondary_buyout": None,
            "is_merger_of_equals": None,
        },
        "financials_disclosure_status": "DISCLOSED",
        "target_financials": {},
        "model_confidence": "HIGH",
    }


def _insert(conn: sqlite3.Connection, txn_id: str, *, with_stake_equity: bool) -> None:
    hc = _hc_result(with_stake_equity=with_stake_equity)
    err = _validate(hc)
    if err:
        raise AssertionError(f"fixture must validate under the 0.18 schema: {err}")

    body = (
        "Meridian Holdings acquired a 27% stake for $600 million in cash. "
        "Pinnacle Gas, which has a market capitalization of $2.2 billion, will "
        "retain its management team."
    )
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', ?, 'Meridian acquires stake in Pinnacle Gas',
            '2026-08-13', ?, ?, 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        (f"https://example.test/{txn_id}", body, f"equity-scope-{txn_id}"),
    )
    source_raw_id = int(cur.lastrowid)
    value = hc["value"]
    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, stake_transition_type, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            value_observations, financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PUBLIC', 'standalone_company', 'standalone_company',
            'Pinnacle Gas Corp.', 'Meridian Holdings', 'strategic_corporate', 'strategic_corporate',
            27.0, 'NEW_MINORITY_STAKE', '2026-08-12', 'exact',
            ?, 'USD', ?, 'HIGH',
            ?, 'DISCLOSED', 'HIGH', '0.7', '0.18', ?
        )
        """,
        (source_raw_id, value["amount"], value["type"], _value_observations_json(hc), txn_id),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn, extraction_id, observation_source_stage="HC_EXTRACT",
        include_stage3=True, include_hc=True,
    )
    conn.commit()


def _market_cap_wins_legacy_slot(field_name, _field_type, _context, observations, *_a, **_kw):
    """Resolve the single legacy value slot to the MARKET CAP — the adversarial choice.

    A source stating two typed facts conflicts on the collapsed `value_amount` /
    `value_type` pair. Handing that slot to the market cap is the worst case, and it is
    what makes the assertions below meaningful: `equity_value` must still resolve to the
    stake consideration, because each canonical field consumes its *own* semantic type
    rather than whichever fact wins the legacy collapse. A stub that picked the equity
    fact would let a scope-blind implementation pass.
    """
    resolved = {
        "value_amount": 2_200_000_000.0,
        "value_type": "MARKET_CAPITALIZATION",
    }
    if field_name not in resolved:
        raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")
    return {
        "chosen_observation_id": observations[0]["observation_id"],
        "chosen_value": resolved[field_name],
        "aggregation_confidence": "HIGH",
        "conflict_type": "SEMANTIC",
        "flagged_for_review": False,
        "reasoning": "adversarial: legacy slot deliberately resolved to the market cap",
        "notes": None,
        "prompt_version": "test",
    }


def _aggregate(conn: sqlite3.Connection) -> None:
    original = aggregate._call_agg_prompt
    aggregate._call_agg_prompt = _market_cap_wins_legacy_slot
    try:
        cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
        aggregate.run(conn, cfg, "equity_scope")
    finally:
        aggregate._call_agg_prompt = original


def _check_end_to_end(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "equity_scope.db")
        init_db(db_path)
        conn = get_connection(db_path)

        _insert(conn, STAKE_TXN, with_stake_equity=True)
        _insert(conn, MARKETCAP_TXN, with_stake_equity=False)
        _aggregate(conn)

        # C. Stake consideration present alongside a market cap.
        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (STAKE_TXN,)
        ).fetchone()
        if row is None:
            failures.append("stake fixture produced no transaction_record")
        else:
            _check(failures, "stake equity_value", row["equity_value"], 600_000_000.0)
            _check(failures, "stake equity_value_basis", row["equity_value_basis"], "STATED")
            # Grossed from the STAKE figure, not the market cap. 2.2B would be the
            # defect; 8.15B would be the market cap grossed a second time.
            _check(
                failures, "stake implied_equity_value",
                row["implied_equity_value"], round(600_000_000 / 0.27, 2),
            )
            _check(failures, "stake transaction_value", row["transaction_value"], 600_000_000.0)
            _check(
                failures, "stake transaction_value_basis",
                row["transaction_value_basis"], "EQUITY_BELOW_CONTROL",
            )

        # D. Market cap is the ONLY value fact. Nothing canonical may be built on it.
        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (MARKETCAP_TXN,)
        ).fetchone()
        if row is None:
            failures.append("market-cap-only fixture produced no transaction_record")
        else:
            _check(failures, "market-cap-only equity_value", row["equity_value"], None)
            _check(failures, "market-cap-only implied_equity_value", row["implied_equity_value"], None)
            _check(failures, "market-cap-only transaction_value", row["transaction_value"], None)
            _check(failures, "market-cap-only enterprise_value", row["enterprise_value"], None)

        # INVERTED AT 0.28. This previously asserted the fact was retained, on the old
        # position that a market cap should be kept even though it is not canonical.
        # Product reversed that: a market cap is not a deal-value fact, so new authoring
        # does not capture it at all. These fixtures build their observations through
        # `_value_observations_json`, the authoring path, so nothing survives there.
        #
        # The legacy half of the invariant is still covered, by section D above: those
        # rows write the market cap straight into the staging value_amount/value_type
        # columns, exactly as a stored pre-0.28 row carries it, and every canonical
        # economic field it could reach is asserted None.
        # The two fixtures reach the ledger by different routes, and 0.28 treats them
        # differently on purpose:
        #
        #   STAKE_TXN     carries the market cap inside `value_observations`, i.e. through
        #                 `_value_observations_json` -- the AUTHORING path. Dropped.
        #   MARKETCAP_TXN carries it in the staging value_amount/value_type COLUMNS, which
        #                 is what a stored pre-0.28 row looks like -- the LEGACY path.
        #                 Retained and read-tolerated, and section D above proves it
        #                 reaches no canonical economic field.
        def _mc_observations(txn_id: str) -> int:
            return conn.execute(
                "SELECT COUNT(*) FROM transaction_field_observation "
                "WHERE transaction_id = ? AND field_name = 'value_type' "
                "AND field_value = 'MARKET_CAPITALIZATION'",
                (txn_id,),
            ).fetchone()[0]

        if _mc_observations(STAKE_TXN):
            failures.append(
                f"{STAKE_TXN}: a MARKET_CAPITALIZATION observation was authored — the type "
                "is retired from current authoring (prompt 0.28) and must be dropped "
                "before persistence"
            )
        if not _mc_observations(MARKETCAP_TXN):
            failures.append(
                f"{MARKETCAP_TXN}: the legacy market cap was not read-tolerated — retiring "
                "the type from authoring must not erase what stored rows already carry"
            )

        conn.close()


def main() -> None:
    failures: list[str] = []
    _check_per_share_gate(failures)
    _check_taxonomy(failures)
    _check_end_to_end(failures)

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS equity_value scope: stake-level only; market cap retained but not canonical")


if __name__ == "__main__":
    main()
