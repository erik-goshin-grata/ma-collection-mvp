#!/usr/bin/env python3
"""Regression guard for Stage 9's default aggregation read source.

No network and no model calls.

The default is `observation`. That is a behavioural claim, not a string: only the
observation ledger carries a per-fact source key
(`staging_extraction_id, source_raw_id, observation_fact_key`), so it is the only
read path that can keep multiple independently typed values from a single source
distinct. The staging read carries one collapsed value pair per extraction and
structurally cannot. Asserting only that a constant reads "observation" would not
catch a regression that leaves the constant alone and changes the wiring, so the
final check runs Stage 9 with a default-constructed config and verifies the typed
facts actually survive.

`staging` must remain selectable — it is the rollback and debug path, and removing
it is not the intent of the default switch.
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as config_module

TXN_ID = "tc_read_default_fixture"

# load_config() requires these regardless of the setting under test.
_REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-key",
    "SEC_API_KEY": "test-key",
    "OPERATOR_CONTACT_EMAIL": "test@example.test",
}


def _load_config_with(env: dict[str, str]):
    """Load a fresh Config under a controlled environment.

    config.py calls load_dotenv() at import time, so the module is reloaded here
    to keep a developer's local .env from deciding the outcome of this test.
    """
    saved = {k: os.environ.get(k) for k in set(env) | {"AGGREGATION_READ_SOURCE"}}
    try:
        os.environ.pop("AGGREGATION_READ_SOURCE", None)
        os.environ.update(env)
        module = importlib.reload(config_module)
        return module.load_config(), module
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _insert_multi_typed_fixture(conn: sqlite3.Connection) -> None:
    """One source stating both a stake-level equity value and a whole-company EV."""
    from lib.observation_writer import write_staging_observations_for_extraction
    from stages.high_confidence_extract import _value_observations_json

    cur = conn.execute(
        """
        INSERT INTO source_raw (
            source_type, source_tier, url, title, published_date, clean_text,
            content_hash, source_status, fetched_at
        ) VALUES (
            'WEB_URL', 'T2', 'https://example.test/read-default',
            'Acquirer to acquire Target', '2026-08-13',
            'Acquirer Inc. will acquire 80% of Target LLC for $80 million. The
             transaction implies a total enterprise value of $150 million.',
            'read-default-fixture', 'RELEVANT', '2026-08-14T00:00:00Z'
        )
        """
    )
    source_raw_id = int(cur.lastrowid)

    hc_result = {
        "value_observations": [
            {
                "amount": 80_000_000,
                "currency": "USD",
                "type": "EQUITY_VALUE",
                "basis": "STATED",
                "qualifier": None,
                "evidence": "acquire 80% of Target LLC for $80 million",
            },
            {
                "amount": 150_000_000,
                "currency": "USD",
                "type": "ENTERPRISE_VALUE",
                "basis": "STATED",
                "qualifier": None,
                "evidence": "total enterprise value of $150 million",
            },
        ]
    }

    conn.execute(
        """
        INSERT INTO staging_extraction (
            source_raw_id, status, deal_type, v2_event_type, event_history_type,
            target_status, target_type, target_type_v2,
            target_name, acquirer_name, acquirer_type, acquirer_type_v2,
            pct_acquired, announced_date, announced_date_precision,
            value_amount, value_currency, value_type, value_type_confidence,
            value_observations, financials_disclosure_status,
            model_confidence, dt_prompt_version, hc_prompt_version, transaction_cluster_id
        ) VALUES (
            ?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED',
            'PRIVATE', 'standalone_company', 'standalone_company',
            'Target LLC', 'Acquirer Inc.', 'strategic_corporate', 'strategic_corporate',
            80.0, '2026-08-12', 'exact',
            80000000, 'USD', 'EQUITY_VALUE', 'HIGH',
            ?, 'DISCLOSED',
            'HIGH', '0.7', '0.15', ?
        )
        """,
        (source_raw_id, _value_observations_json(hc_result), TXN_ID),
    )
    extraction_id = int(
        conn.execute(
            "SELECT extraction_id FROM staging_extraction WHERE source_raw_id = ?",
            (source_raw_id,),
        ).fetchone()[0]
    )
    write_staging_observations_for_extraction(
        conn,
        extraction_id,
        observation_source_stage="HC_EXTRACT",
        include_stage3=True,
        include_hc=True,
    )
    conn.commit()


def _legacy_pair_stub(field_name, _field_type, _context, observations, *_args, **_kwargs):
    """Resolve the collapsed legacy value pair deterministically, no model call.

    Two typed amounts at equal tier and confidence conflict on the legacy
    value_amount/value_type pair. Which one wins is irrelevant here — the point of
    this test is that the canonical typed fields survive regardless.
    """
    chosen = {"value_amount": 150_000_000.0, "value_type": "ENTERPRISE_VALUE"}
    if field_name not in chosen:
        raise AssertionError(f"unexpected aggregation conflict for {field_name}")
    return {
        "chosen_observation_id": observations[0]["observation_id"],
        "chosen_value": chosen[field_name],
        "aggregation_confidence": "HIGH",
        "conflict_type": "SEMANTIC",
        "flagged_for_review": False,
        "reasoning": "deterministic stub",
        "notes": None,
        "prompt_version": "test",
    }


def main() -> None:
    failures: list[str] = []

    # --- The default itself -------------------------------------------------
    cfg, module = _load_config_with(dict(_REQUIRED_ENV))
    if cfg.aggregation_read_source != "observation":
        failures.append(
            f"default aggregation_read_source: expected 'observation', got "
            f"{cfg.aggregation_read_source!r}"
        )
    if module.DEFAULT_AGGREGATION_READ_SOURCE != "observation":
        failures.append(
            f"DEFAULT_AGGREGATION_READ_SOURCE: expected 'observation', got "
            f"{module.DEFAULT_AGGREGATION_READ_SOURCE!r}"
        )

    # --- staging must remain selectable (rollback / debug) ------------------
    cfg_staging, _ = _load_config_with({**_REQUIRED_ENV, "AGGREGATION_READ_SOURCE": "staging"})
    if cfg_staging.aggregation_read_source != "staging":
        failures.append(
            f"explicit staging: expected 'staging', got {cfg_staging.aggregation_read_source!r}"
        )

    cfg_obs, _ = _load_config_with({**_REQUIRED_ENV, "AGGREGATION_READ_SOURCE": "observation"})
    if cfg_obs.aggregation_read_source != "observation":
        failures.append(
            f"explicit observation: expected 'observation', got "
            f"{cfg_obs.aggregation_read_source!r}"
        )

    # --- Invalid values still rejected -------------------------------------
    try:
        _load_config_with({**_REQUIRED_ENV, "AGGREGATION_READ_SOURCE": "ledger"})
    except config_module.ConfigurationError:
        pass
    else:
        failures.append("invalid AGGREGATION_READ_SOURCE was accepted")

    # --- Stage 9's own fallback must not contradict the config default -----
    # A cfg stub without the attribute must not silently take the legacy path.
    import stages.aggregate as aggregate

    importlib.reload(aggregate)
    if aggregate.DEFAULT_AGGREGATION_READ_SOURCE != "observation":
        failures.append("stages.aggregate does not share the config default")

    # --- Behaviour: the default read path preserves typed facts ------------
    # This is the assertion that a string check cannot make. Under the staging
    # read these two same-source facts collapse to one value pair.
    from types import SimpleNamespace

    from db import get_connection, init_db

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "read_default.db")
        init_db(db_path)
        conn = get_connection(db_path)
        _insert_multi_typed_fixture(conn)

        # Only log_level and the read source are consumed by Stage 9 here; the
        # read source is taken from the default-constructed config, not hardcoded.
        run_cfg = SimpleNamespace(
            log_level="ERROR", aggregation_read_source=cfg.aggregation_read_source
        )
        original_call_agg_prompt = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = _legacy_pair_stub
        try:
            aggregate.run(conn, run_cfg, "read_default_test")
        finally:
            aggregate._call_agg_prompt = original_call_agg_prompt

        row = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id = ?", (TXN_ID,)
        ).fetchone()
        if row is None:
            failures.append("default read path produced no transaction_record")
        else:
            if row["equity_value"] != 80_000_000.0:
                failures.append(
                    f"default read path equity_value: expected 80000000.0, got {row['equity_value']!r}"
                )
            if row["implied_enterprise_value"] != 150_000_000.0:
                failures.append(
                    "default read path implied_enterprise_value: expected 150000000.0, "
                    f"got {row['implied_enterprise_value']!r}"
                )
            if row["implied_enterprise_value_basis"] != "STATED":
                failures.append(
                    "default read path implied_enterprise_value_basis: expected 'STATED', "
                    f"got {row['implied_enterprise_value_basis']!r}"
                )
        conn.close()

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS aggregation read default: observation-backed, staging still selectable")


if __name__ == "__main__":
    main()
