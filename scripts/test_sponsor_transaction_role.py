#!/usr/bin/env python3
"""Guard tests for sponsor_transaction_role (inventory §T7, slice S-G).

How a transaction relates to a financial sponsor's platform. PLATFORM | ADD_ON | null.

This replaces the v0.4 `is_platform_investment` / `is_add_on` pair, which split one question
across two flags and got both halves wrong. `is_add_on` was `acquirer_type == 'PE_PORTFOLIO'`
-- an unconditional proxy with no evidence contract, reading a vocabulary value §T8 removes.
`is_platform_investment` accepted only explicit platform wording, which is narrower than §T7,
where transaction or company context may establish the fact.

Five things are pinned here, and each exists because it could plausibly regress:

  1. THE FOUR HOPS.        staging -> observation ledger -> configured Stage 9 read ->
                           canonical transaction_record, through the production writer and
                           the production include_hc flag. Omitting HC_FIELDS membership
                           strands the field on staging with a NULL canonical column -- the
                           S-A defect, which no prompt or parser test would catch.
  2. AUTHORSHIP STOPPED,   Stage 9 no longer writes is_add_on or is_platform_investment, but
     COLUMNS KEPT.         both columns must still EXIST. "Retired" means unwritten, never
                           dropped: stored history stays readable, and the standing rule is
                           that a retained column is not a claim of continued authorship.
  3. NO SUPPRESSION.       is_secondary_buyout is orthogonal by decision -- a sponsor-to-
                           sponsor deal may legitimately be PLATFORM and a secondary buyout
                           at once, so nothing may clear one on the basis of the other.
  4. VOCABULARY.           Two values plus null, by decision. Anything else is rejected, not
                           merely absent.
  5. NOT DERIVED FROM      §T7 is explicit that the V2 defect was using an acquirer-type
     ACQUIRER TYPE.        value as a stand-in for evidence. A pe_portfolio acquirer with no
                           role extracted must stay NULL.

These are structural regressions. They do NOT validate extraction quality, which needs the
prompt and model against real source text -- a separate gate.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_AGGREGATION_READ_SOURCE
from db import get_connection, init_db
import stages.aggregate as aggregate
import stages.high_confidence_extract as hc
from lib.observation_writer import (
    HC_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HC_PROMPT = os.path.join(ROOT, "prompts", "high_confidence_extraction.md")
TXN = "tc_sponsor_0001"


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_text: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Prompt and stage agree, and neither predates `minimum`.

    Not an equality check against a literal: pinning an exact version asserts the prompt is
    frozen and breaks on the next slice's legitimate bump. Compared numerically because these
    are dotted decimals -- 0.21 > 0.9, which a string comparison inverts.
    """
    m = re.search(r"^\*\*Version:\*\* (\d+)\.(\d+)", prompt_text, re.M)
    if m is None:
        failures.append(f"{label} prompt: no parseable version line")
        return
    _eq(failures, f"{label}/prompt-stage version parity",
        f"{m.group(1)}.{m.group(2)}", stage_version)
    if (int(m.group(1)), int(m.group(2))) < minimum:
        failures.append(f"{label} prompt: version {m.group(0)!r} predates the release that "
                        f"{what} ({minimum[0]}.{minimum[1]})")


def _hc(**deal_over) -> dict:
    """A schema-valid single-transaction HC result; the deal block is overridden per-test."""
    deal = {"pct_acquired": None, "stake_transition_type": None, "offer_mechanism": None,
            "sponsor_transaction_role": None}
    deal.update(deal_over)
    return {
        "target": {"name": "Verity Biosciences", "domain": None, "ticker": None,
                   "description": None, "asset_type": None},
        "acquirer": {"name": "Halden Therapeutics", "domain": None, "ticker": None,
                     "type": "pe_portfolio", "description": None, "sponsor_name": None},
        "parent_seller": {"name": None, "ticker": None, "description": None},
        "deal": deal,
        "dates": {},
        "value": {},
        "reported_multiples": [],
        "value_observations": [],
        "features": {"is_secondary_buyout": None, "is_merger_of_equals": None},
        "target_financials": {},
        "financials_disclosure_status": "UNKNOWN",
        "model_confidence": "HIGH",
    }


# ---------------------------------------------------------------------------
# 1. Canonical-field gate: staging -> observation -> aggregation -> canonical
# ---------------------------------------------------------------------------

def _test_canonical_path(failures: list[str]) -> None:
    """Four hops, with pct_acquired as the unchanged neighbouring control.

    The control is seeded and asserted even when sponsor_transaction_role cannot be, never
    skipped by the failure it exists to isolate: on pre-S-G code it passes while the new
    field fails, which separates "this field is broken" from "this harness is broken".
    """
    if "sponsor_transaction_role" not in HC_FIELDS:
        failures.append("observation/HC_FIELDS: sponsor_transaction_role is absent, so Stage 4's "
                        "include_hc write will not observe it and the canonical column stays "
                        "NULL regardless of what the model extracts")
    if "pct_acquired" not in HC_FIELDS:
        failures.append("observation/control: pct_acquired missing from HC_FIELDS")
    if "is_platform_investment" in HC_FIELDS:
        failures.append("observation/HC_FIELDS: is_platform_investment is still observed, but "
                        "Stage 4 no longer writes that column (§T7), so the observation would "
                        "be a permanent NULL")

    db_path = os.path.join(tempfile.mkdtemp(), "sponsor.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        staging_cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
        have = "sponsor_transaction_role" in staging_cols
        if not have:
            failures.append("schema/staging_extraction: sponsor_transaction_role column is "
                            "missing — migration 009 did not run")
        tr_cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
        if "sponsor_transaction_role" not in tr_cols:
            failures.append("schema/transaction_record: sponsor_transaction_role is missing")

        # Retired but RETAINED. Dropping either column would lose stored history, which the
        # standing rule forbids: authorship stops, the column stays.
        for col, table, cols in (("is_add_on", "transaction_record", tr_cols),
                                 ("is_platform_investment", "transaction_record", tr_cols),
                                 ("is_platform_investment", "staging_extraction", staging_cols)):
            if col not in cols:
                failures.append(f"schema/{table}: retired column {col} was DROPPED — retiring "
                                "authorship must never remove stored history")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # PLATFORM together with is_secondary_buyout=1, seeded on purpose: §T7 keeps the two
        # orthogonal, so a sponsor-to-sponsor deal may be both. If anything ever suppresses
        # one because the other is set, this row catches it.
        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "event_history_type",
                "target_status", "target_type", "target_type_v2", "target_name",
                "acquirer_name", "acquirer_type", "acquirer_type_v2", "is_secondary_buyout",
                "pct_acquired", "announced_date", "announced_date_precision",
                "financials_disclosure_status", "model_confidence", "dt_prompt_version",
                "hc_prompt_version", "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "ACQUISITION", "ACQUISITION", "ANNOUNCED", "PRIVATE",
                "standalone_company", "standalone_company", "Verity Biosciences",
                "Halden Therapeutics", "private_equity", "private_equity", 1,
                45.0, "2026-08-18", "exact", "UNKNOWN", "HIGH", "0.11", "0.21", TXN]
        if have:
            cols.append("sponsor_transaction_role")
            vals.append("PLATFORM")
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # The production write with the production flag, not a local reimplementation.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        checks = [("pct_acquired", 45.0)]                        # control, always asserted
        if have:
            checks.insert(0, ("sponsor_transaction_role", "PLATFORM"))
        for field, expected in checks:
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id=? AND field_name=?", (TXN, field)).fetchone()
            if row is None:
                failures.append(f"observation/{field}: no observation row was written")
            else:
                got = row["field_value"]
                _eq(failures, f"observation/{field}",
                    float(got) if isinstance(expected, float) else got, expected)

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "sponsor-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id=?", (TXN,)).fetchone()
        if canon is None:
            failures.append("canonical: no transaction_record row")
            return
        src = DEFAULT_AGGREGATION_READ_SOURCE
        _eq(failures, f"canonical/pct_acquired CONTROL (read_source={src})",
            canon["pct_acquired"], 45.0)
        if have:
            _eq(failures, f"canonical/sponsor_transaction_role (read_source={src})",
                canon["sponsor_transaction_role"], "PLATFORM")
            # Orthogonality survives the whole path, not just the write.
            _eq(failures, "canonical/is_secondary_buyout coexists", canon["is_secondary_buyout"], 1)

        # Authorship stopped. A pe_portfolio acquirer is present on this row, which under the
        # V2 derivation would have forced is_add_on=1; §T7 removes that derivation entirely.
        for retired in ("is_add_on", "is_platform_investment"):
            if canon[retired] not in (None, 0):
                failures.append(f"canonical/{retired}: still authored ({canon[retired]!r}) — "
                                "§T7 replaces it with sponsor_transaction_role")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Vocabulary, and independence from acquirer_type
# ---------------------------------------------------------------------------

def _test_vocabulary(failures: list[str]) -> None:
    for good in ("PLATFORM", "ADD_ON", None):
        err = hc._validate(_hc(sponsor_transaction_role=good))
        if err is not None:
            failures.append(f"vocabulary: {good!r} should be accepted, got {err!r}")
    # Excluded by decision, not by omission.
    for bad in ("PE_PORTFOLIO", "BOLT_ON", "TUCK_IN", "PLATFORM_INVESTMENT", "platform", ""):
        if hc._validate(_hc(sponsor_transaction_role=bad)) is None:
            failures.append(f"vocabulary: {bad!r} was accepted but is not a §T7 value")

    # is_platform_investment is out of the features contract; a stale key must not resurrect
    # it as a validated feature.
    result = _hc()
    result["features"]["is_platform_investment"] = "yes"
    if hc._validate(result) is not None:
        failures.append("features: a stale is_platform_investment key is still being "
                        "validated — the field left the contract in §T7")


def _test_not_derived_from_acquirer_type(failures: list[str]) -> None:
    """A pe_portfolio acquirer with no role extracted stays NULL.

    §T7 is explicit that the V2 defect was using an acquirer-type value as a stand-in for
    evidence. `_hc()` seeds acquirer.type = pe_portfolio precisely so this cannot pass by
    accident.
    """
    result = _hc(sponsor_transaction_role=None)
    if result["acquirer"]["type"] != "pe_portfolio":
        failures.append("fixture: acquirer.type should be pe_portfolio for this test to mean "
                        "anything")
    if hc._validate(result) is not None:
        failures.append("a pe_portfolio acquirer with a null role should validate")
    if result["deal"]["sponsor_transaction_role"] is not None:
        failures.append("sponsor_transaction_role was populated from acquirer.type alone")


# ---------------------------------------------------------------------------
# 3. Prompt contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(HC_PROMPT, encoding="utf-8").read()
    _check_version(failures, "high_confidence_extraction", text, hc._VERSION, (0, 21),
                   "added sponsor_transaction_role and retired is_platform_investment")
    # Instructions and examples only. The versioning table is deliberately excluded: it is
    # history, and its 0.21 row NAMES the retired field on purpose. Asserting over the whole
    # file would force the changelog to lie about what changed.
    m = re.search(r"^## \d+\. Versioning", text, re.M)
    body = text[:m.start()] if m else text
    if "sponsor_transaction_role" not in body:
        failures.append("prompt: sponsor_transaction_role is not documented")
    if "is_platform_investment" in body:
        failures.append("prompt: is_platform_investment still appears in the instructions or "
                        "examples — §T7 retires it, and a prompt that still asks for it will "
                        "keep producing it")
    for phrase in ("PLATFORM", "ADD_ON"):
        if phrase not in body:
            failures.append(f"prompt: {phrase} is missing from the vocabulary")
    # The evidence contract, not just the value names.
    if "not required" not in body.lower():
        failures.append("prompt: the rule that literal add-on/bolt-on/tuck-in wording is NOT "
                        "required is missing — it is what separates §T7 from keyword matching")
    # sponsor_name must no longer be gated on a vocabulary value §T8 removes.
    if "For pe_portfolio acquirers" in body:
        failures.append("prompt: sponsor_name is still gated on pe_portfolio, a value §T8 "
                        "removes from the acquirer vocabulary")


def main() -> None:
    failures: list[str] = []
    _test_canonical_path(failures)
    _test_vocabulary(failures)
    _test_not_derived_from_acquirer_type(failures)
    _test_prompt_contract(failures)

    if failures:
        print(f"FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS  sponsor_transaction_role  (four-hop canonical path + control, retired-column "
          "retention, orthogonality, vocabulary, acquirer-type independence, prompt contract)")


if __name__ == "__main__":
    main()
