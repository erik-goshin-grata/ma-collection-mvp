#!/usr/bin/env python3
"""Regression guard: a manually remediated observation must reach Stage 9.

No network and no model calls.

A remediation records its correction as an observation in the ledger, stamped
`observation_source_stage = 'MANUAL_REMEDIATION'`. The observation read path filtered
observations by an **allowlist of producing stages** — `DT_CLASSIFY`, `HC_EXTRACT`,
`FUNDING_HC_EXTRACT`, `LC_EXTRACT`, `BACKFILL` — and `MANUAL_REMEDIATION` was not on it.
So the correction was written, stored, and then silently ignored by the derivation that
exists to consume it.

**Why this hid.** Where a remediation's canonical value equals the staged one, the row
still derives correctly — a later observation regeneration re-reads
`staging_extraction.round_size` and emits an `HC_EXTRACT` observation carrying the same
number, which the allowlist admits. The remediated fact reaches the canonical layer by a
route that has nothing to do with the remediation. Cellares is the first case where the
staged figure ($50M, one investor's check) and the canonical figure ($327M, the round)
**differ**, so the accidental route carried the wrong number or none at all, and the
filter became visible.

The fixture below reproduces the live sequence exactly, and it is the sequence that
matters: observations are written from staging *before* the correction, so no
`HC_EXTRACT` observation for `round_size` can exist; then staging is corrected and the
`MANUAL_REMEDIATION` observation is appended, exactly as the planner does. Nothing else
can supply the value, so the assertion is only satisfiable if the read path admits the
remediation.

The fix is general: a remediation is a first-class producer of observations, not a
special case for one row.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stages.aggregate as aggregate
from db import get_connection, init_db
from lib.observation_writer import write_staging_observations_for_extraction

TXN = "tc_manual_remediation_divergent"
STAGED_CHECK = 50_000_000.0     # Prime Radiant's check — the original extraction
CANONICAL_ROUND = 327_000_000.0  # the Series D — what remediation corrected it to

COMPETING_TXN = "tc_manual_remediation_competing"
COMPETING_STALE = 80_000_000.0     # what extraction observed
COMPETING_CORRECTED = 95_000_000.0  # what a human corrected it to


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _build(conn: sqlite3.Connection) -> int:
    """The live sequence: extract, observe, THEN remediate."""
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', 'https://example.test/cellares',
            'Cellares announces Series D investment', '2026-08-13', ?,
            'manual-remediation-fixture', 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """,
        ("Cellares announced that Prime Radiant Fund has made a $50 million growth "
         "equity investment in the company's Series D financing, bringing the total "
         "Series D to $327 million.",),
    )
    source_raw_id = int(cur.lastrowid)

    # 1. The original HC 0.12 extraction: the check landed in value_amount typed
    #    TRANSACTION_VALUE, and round_size was never populated.
    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_name, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            round_size, financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'GROWTH_EQUITY', 'GROWTH_EQUITY', 'ANNOUNCED',
            'PRIVATE', 'Cellares', '2026-08-12', 'exact',
            ?, 'USD', 'TRANSACTION_VALUE', 'HIGH',
            NULL, 'DISCLOSED', 'HIGH', '0.7', '0.12', ?
        )
        """,
        (source_raw_id, STAGED_CHECK, TXN),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )

    # 2. Observations written from that extraction — BEFORE the correction, so there is
    #    no HC_EXTRACT observation for round_size and none can appear later.
    write_staging_observations_for_extraction(
        conn, extraction_id, observation_source_stage="HC_EXTRACT",
        include_stage3=True, include_hc=True,
    )

    # 3. The remediation, exactly as the planner writes it: correct staging, append one
    #    MANUAL_REMEDIATION observation. The original value_amount is left intact as the
    #    record of what the extraction produced.
    conn.execute(
        "UPDATE staging_extraction SET round_size = ?, round_currency = 'USD' "
        "WHERE extraction_id = ?",
        (CANONICAL_ROUND, extraction_id),
    )
    conn.execute(
        """
        INSERT INTO transaction_field_observation (
            transaction_id, field_name, field_value, field_value_numeric,
            staging_extraction_id, source_raw_id, source_type,
            observation_source_stage, extracted_at
        ) VALUES (?, 'round_size', ?, ?, ?, ?, 'WEB_URL', 'MANUAL_REMEDIATION',
                  '2026-08-17T00:00:00Z')
        """,
        (TXN, str(CANONICAL_ROUND), CANONICAL_ROUND, extraction_id, source_raw_id),
    )
    conn.commit()
    return extraction_id


def _build_competing(conn: sqlite3.Connection) -> None:
    """A remediation correcting a field that ALREADY carries an extraction observation.

    Admission alone does not settle this. The stale `round_size` observation and the
    correction share a source and therefore a tier, so `_pick_value` sees a same-tier
    disagreement — and resolves it by confidence, or by asking the LLM. Neither is right:
    a human correction of a machine-extracted fact is not a tie to be broken, it
    supersedes. Without precedence a remediation silently loses on exactly the fields
    most likely to need one.
    """
    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', 'https://example.test/competing', 'Competing round size',
            '2026-08-13', 'The round was reported at $80 million.',
            'competing-fixture', 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """
    )
    source_raw_id = int(cur.lastrowid)
    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_name, announced_date, announced_date_precision,
            round_size, round_currency, financials_disclosure_status, model_confidence,
            dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'VC_ROUND', 'VC_ROUND', 'ANNOUNCED',
            'PRIVATE', 'CompetingCo', '2026-08-12', 'exact',
            80000000, 'USD', 'DISCLOSED', 'HIGH', '0.7', '0.15', ?
        )
        """,
        (source_raw_id, COMPETING_TXN),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    # Extraction observes round_size = 80M.
    write_staging_observations_for_extraction(
        conn, extraction_id, observation_source_stage="HC_EXTRACT",
        include_stage3=True, include_hc=True, include_funding=True,
    )
    # A human corrects it to 95M. Staging is corrected too, as the planner does.
    conn.execute(
        "UPDATE staging_extraction SET round_size = ? WHERE extraction_id = ?",
        (COMPETING_CORRECTED, extraction_id),
    )
    conn.execute(
        """
        INSERT INTO transaction_field_observation (
            transaction_id, field_name, field_value, field_value_numeric,
            staging_extraction_id, source_raw_id, source_type,
            observation_source_stage, extracted_at
        ) VALUES (?, 'round_size', ?, ?, ?, ?, 'WEB_URL', 'MANUAL_REMEDIATION',
                  '2026-08-17T00:00:00Z')
        """,
        (COMPETING_TXN, str(COMPETING_CORRECTED), COMPETING_CORRECTED,
         extraction_id, source_raw_id),
    )
    conn.commit()


def main() -> None:
    failures: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "manual_remediation.db")
        init_db(db_path)
        conn = get_connection(db_path)
        extraction_id = _build(conn)
        _build_competing(conn)

        # The premise: exactly one round_size observation exists, and it is the
        # remediation's. If this ever fails, the fixture has stopped isolating the
        # defect and the assertions below would pass for the wrong reason.
        rows = conn.execute(
            "SELECT observation_source_stage, field_value_numeric "
            "FROM transaction_field_observation "
            "WHERE transaction_id = ? AND field_name = 'round_size'", (TXN,),
        ).fetchall()
        _check(failures, "exactly one round_size observation", len(rows), 1)
        if rows:
            _check(failures, "it is the remediation's",
                   rows[0]["observation_source_stage"], "MANUAL_REMEDIATION")

        original = aggregate._call_agg_prompt

        def _no_conflict(field_name, *_a, **_kw):
            raise AssertionError(f"unexpected aggregation conflict for {field_name!r}")

        aggregate._call_agg_prompt = _no_conflict
        try:
            cfg = SimpleNamespace(log_level="ERROR", aggregation_read_source="observation")
            aggregate.run(conn, cfg, "manual_remediation")
        finally:
            aggregate._call_agg_prompt = original

        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN,)
        ).fetchone()
        if row is None:
            failures.append("no transaction_record produced")
        else:
            # The remediated value must reach the canonical layer.
            _check(failures, "round_size", row["round_size"], CANONICAL_ROUND)
            _check(failures, "transaction_size", row["transaction_size"], CANONICAL_ROUND)
            _check(failures, "transaction_size_basis",
                   row["transaction_size_basis"], "ROUND_SIZE")
            # The check must NOT become the event's magnitude, by any route.
            if row["round_size"] == STAGED_CHECK:
                failures.append(
                    "round_size took the $50M investor check instead of the $327M round"
                )
            if row["transaction_size"] == STAGED_CHECK:
                failures.append(
                    "transaction_size took the $50M investor check instead of the round"
                )
            # Funding derives no M&A values, and no transaction-level check.
            _check(failures, "transaction_value", row["transaction_value"], None)
            _check(failures, "equity_value", row["equity_value"], None)
            _check(failures, "investment_amount", row["investment_amount"], None)

        # --- Scenario 2: the correction competes with a stale observation ---
        competing = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (COMPETING_TXN,)
        ).fetchone()
        if competing is None:
            failures.append("no transaction_record for the competing fixture")
        else:
            _check(failures, "correction supersedes the stale observation",
                   competing["round_size"], COMPETING_CORRECTED)
            _check(failures, "corrected size flows to transaction_size",
                   competing["transaction_size"], COMPETING_CORRECTED)
            if competing["round_size"] == COMPETING_STALE:
                failures.append(
                    "the stale extraction observation beat the human correction — a "
                    "remediation supersedes, it does not tie-break"
                )

        # The superseded extraction record survives — remediation appends, never rewrites.
        staged = conn.execute(
            "SELECT value_amount, value_type, round_size FROM staging_extraction "
            "WHERE extraction_id = ?", (extraction_id,),
        ).fetchone()
        _check(failures, "original value_amount retained", staged["value_amount"], STAGED_CHECK)
        _check(failures, "original value_type retained", staged["value_type"], "TRANSACTION_VALUE")
        _check(failures, "staging round_size corrected", staged["round_size"], CANONICAL_ROUND)

        conn.close()

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS manual remediation observations reach Stage 9 and win over the stale fact")


if __name__ == "__main__":
    main()
