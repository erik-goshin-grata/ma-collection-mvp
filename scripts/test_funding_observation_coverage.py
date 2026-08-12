"""
Regression test for funding-path observation coverage (2026-08-11).

Decision: "Observation Write Path Must Cover Every Field Aggregation Reads".
Before this fix, Stage 4b (funding_hc_extract) wrote no observations, so every
funding field nulled on the observation read path. The highest-value case is
round_size — the primary magnitude, first rung of the transaction_size waterfall,
input to _derive_investment_amount — which lives in HC_FIELDS (put there because
MINORITY_INVESTMENT stays on the M&A path per §4.1), NOT the funding group. A
Stage 4b row requesting include_funding alone must still get a round_size
observation, so round_size is included in FUNDING_FIELDS.

Two scenarios:
  A. Self-sufficiency (the design guarantee): include_funding ALONE produces a
     round_size observation stamped FUNDING_HC_EXTRACT with the right value, plus a
     funding-only field (post_money_valuation). This is what fails if round_size is
     left out of FUNDING_FIELDS.
  B. Integration reality (the real Stage 4b call, include_stage3+hc+funding):
     round_size is stamped FUNDING_HC_EXTRACT — funding runs before HC in the writer
     so it wins the dedup and stamps truthfully (round_size is the one field in both
     groups). target_name is observed (HC_EXTRACT), v2_event_type (DT_CLASSIFY).

No live API calls. Runs against a temp DB built by init_db.

Usage:
    python scripts/test_funding_observation_coverage.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection, init_db
from lib.observation_writer import write_staging_observations_for_extraction


def _run() -> int:
    tmp = tempfile.mkdtemp(prefix="funding_obs_")
    db_path = os.path.join(tmp, "t.db")
    init_db(db_path)
    conn = get_connection(db_path)

    conn.execute(
        """
        INSERT INTO source_raw (source_raw_id, source_type, source_tier, url, fetched_at)
        VALUES (1, 'PR_NEWSWIRE', 'T2', 'https://example.test/round', '2026-08-11')
        """
    )

    def _mk_row(eid):
        conn.execute(
            """
            INSERT INTO staging_extraction (
                extraction_id, source_raw_id, status, transaction_cluster_id,
                v2_event_type, event_history_type,
                target_name, target_domain,
                round_label, round_size, valuation_currency,
                pre_money_valuation, post_money_valuation,
                financials_disclosure_status, hc_prompt_version
            ) VALUES (
                ?, 1, 'CLUSTERED', 'cluster-1',
                'VC_ROUND', 'ANNOUNCED',
                'Acme Robotics', 'acme.test',
                'Series B', 50000000, 'EUR',
                200000000, 250000000,
                'DISCLOSED', '0.1'
            )
            """,
            (eid,),
        )

    _mk_row(10)  # scenario A — include_funding alone
    _mk_row(20)  # scenario B — full Stage 4b call
    conn.commit()

    def _obs(eid, field):
        return conn.execute(
            """
            SELECT field_value, field_value_numeric, observation_source_stage
            FROM transaction_field_observation
            WHERE staging_extraction_id = ? AND field_name = ? AND is_current = 1
            """,
            (eid, field),
        ).fetchone()

    _ACCEPTED = {"DT_CLASSIFY", "HC_EXTRACT", "FUNDING_HC_EXTRACT", "LC_EXTRACT", "BACKFILL"}
    failures = []

    # --- Scenario A: include_funding ALONE (the self-sufficiency guarantee) ---
    write_staging_observations_for_extraction(
        conn, 10, observation_source_stage="FUNDING_HC_EXTRACT", include_funding=True,
    )
    conn.commit()
    rs = _obs(10, "round_size")
    if rs is None:
        failures.append("A: round_size NOT observed by include_funding alone "
                        "(FUNDING_FIELDS is not self-sufficient)")
    else:
        if rs["field_value_numeric"] != 50000000:
            failures.append(f"A: round_size numeric wrong: {rs['field_value_numeric']}")
        if rs["observation_source_stage"] != "FUNDING_HC_EXTRACT":
            failures.append(f"A: round_size stage wrong: {rs['observation_source_stage']}")
    if _obs(10, "post_money_valuation") is None:
        failures.append("A: post_money_valuation NOT observed by include_funding")
    if _obs(10, "target_name") is not None:
        failures.append("A: target_name observed without include_hc (group boundary leaked)")

    # --- Scenario B: the real Stage 4b call (stage3 + hc + funding) ---
    write_staging_observations_for_extraction(
        conn, 20, observation_source_stage="FUNDING_HC_EXTRACT",
        include_stage3=True, include_hc=True, include_funding=True,
    )
    conn.commit()
    rs = _obs(20, "round_size")
    if rs is None:
        failures.append("B: round_size NOT observed by the full Stage 4b call")
    elif rs["observation_source_stage"] != "FUNDING_HC_EXTRACT":
        # Funding runs before HC in the writer, so it wins the dedup and stamps
        # truthfully even when HC_FIELDS also contains round_size.
        failures.append(f"B: round_size stage should be FUNDING_HC_EXTRACT, "
                        f"got {rs['observation_source_stage']}")
    tn = _obs(20, "target_name")
    if tn is None:
        failures.append("B: target_name NOT observed")
    elif tn["observation_source_stage"] != "HC_EXTRACT":
        failures.append(f"B: target_name stage wrong: {tn['observation_source_stage']}")
    ve = _obs(20, "v2_event_type")
    if ve is None:
        failures.append("B: v2_event_type NOT observed")
    elif ve["observation_source_stage"] != "DT_CLASSIFY":
        failures.append(f"B: v2_event_type stage wrong: {ve['observation_source_stage']}")

    conn.close()

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("PASS funding observation coverage: A) include_funding alone yields "
          "round_size@FUNDING_HC_EXTRACT + post_money (self-sufficient, no HC leak); "
          "B) full Stage 4b call yields round_size@FUNDING_HC_EXTRACT (funding wins "
          "dedup) + target_name@HC_EXTRACT + v2_event_type@DT_CLASSIFY")
    return 0


if __name__ == "__main__":
    sys.exit(_run())
