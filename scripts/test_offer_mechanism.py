#!/usr/bin/env python3
"""Guard tests for offer_mechanism (inventory §T12, slice S-D).

Whether the acquisition is effected through an offer made directly to target
securityholders. TENDER_OFFER | null.

Before this slice the fact existed only as `merger_structure = TENDER_OFFER` on the
Stage 11 agreement path, gated on an SEC filing. A transaction with no filing could not
record a tender offer at all, however plainly its release described one. Ordinary-source
HC extraction now owns the field; the agreement path corroborates rather than owns.

Four things are pinned here, and each exists because it could plausibly regress:

  1. THE FOUR HOPS.        staging -> observation ledger -> configured Stage 9 read ->
                           canonical transaction_record, using the production writer and
                           the production include_hc flag. Omitting HC_FIELDS membership
                           strands the field on staging with a NULL canonical column --
                           the S-A defect, which no prompt or parser test would catch.
  2. NO SUPPRESSION.       Unlike asset_type there is deliberately no cross-field guard.
                           A tender offer legitimately coexists with a back-end merger,
                           so nothing may clear it on the basis of another field.
  3. CORROBORATION, NOT    Stage 11 emits an offer_mechanism observation only for
     ABSORPTION.           TENDER_OFFER. DIRECT / FORWARD_TRIANGULAR / REVERSE_TRIANGULAR
                           have no V3 destination (§T2 defers them) and must not acquire
                           one by the back door. merger_structure keeps all four values.
  4. VOCABULARY.           One value plus null, by decision. MANDATORY_OFFER,
                           SCHEME_OF_ARRANGEMENT, ONE_STEP_MERGER and TWO_STEP_MERGER are
                           excluded by §T12, so they must be rejected, not merely absent.

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
import stages.agreement_extract as agree
import stages.high_confidence_extract as hc
from lib.observation_writer import (
    HC_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HC_PROMPT = os.path.join(ROOT, "prompts", "high_confidence_extraction.md")
TXN = "tc_offer_0001"


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_text: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Prompt and stage agree, and neither predates `minimum`.

    Not an equality check against a literal: pinning an exact version asserts the prompt is
    frozen and breaks on the next slice's legitimate bump. Compared numerically because
    these are dotted decimals -- 0.20 > 0.9, which a string comparison inverts.
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


def _hc(**over) -> dict:
    """A schema-valid single-transaction HC result; the deal block is overridden per-test.

    `_validate` takes one transaction, not the `transactions` envelope.
    """
    deal = {"pct_acquired": None, "stake_transition_type": None, "offer_mechanism": None}
    deal.update(over)
    return {
        "target": {"name": "Verity Biosciences", "domain": None, "ticker": None,
                   "description": None, "asset_type": None},
        "acquirer": {"name": "Halden Therapeutics", "domain": None, "ticker": None,
                     "type": "strategic_corporate", "description": None, "sponsor_name": None},
        "parent_seller": {"name": None, "ticker": None, "description": None},
        "deal": deal,
        "dates": {},
        "value": {},
        "reported_multiples": [],
        "acquirers": [],
        "buy_side_sponsors": [],
        "parent_sellers": [],
        "value_observations": [],
        "features": {"is_platform_investment": None, "is_secondary_buyout": None,
                     "is_merger_of_equals": None},
        "target_financials": {},
        "financials_disclosure_status": "UNKNOWN",
        "model_confidence": "HIGH",
    }


# ---------------------------------------------------------------------------
# 1. Canonical-field gate: staging -> observation -> aggregation -> canonical
# ---------------------------------------------------------------------------

def _test_canonical_path(failures: list[str]) -> None:
    """Four hops through the production writer and the production include_hc flag.

    `pct_acquired` is the control: an unchanged HC_FIELDS member carried through the
    identical path. On pre-S-D code it passes while offer_mechanism fails, which separates
    "this field is broken" from "this harness is broken". It is therefore seeded and
    asserted even when offer_mechanism cannot be, never skipped by the failure it exists
    to isolate.
    """
    if "offer_mechanism" not in HC_FIELDS:
        failures.append("observation/HC_FIELDS: offer_mechanism is absent, so Stage 4's "
                        "include_hc write will not observe it and the canonical column "
                        "stays NULL regardless of what the model extracts")
    if "pct_acquired" not in HC_FIELDS:
        failures.append("observation/control: pct_acquired missing from HC_FIELDS")

    db_path = os.path.join(tempfile.mkdtemp(), "offer.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        staging_cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
        have = "offer_mechanism" in staging_cols
        if not have:
            failures.append("schema/staging_extraction: offer_mechanism column is missing "
                            "— migration 007 did not run")
        tr_cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
        if "offer_mechanism" not in tr_cols:
            failures.append("schema/transaction_record: offer_mechanism column is missing")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # A two-step deal: tender offer AND a back-end merger. Seeded together on purpose
        # -- if anything ever suppresses one because the other is set, this row catches it.
        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "combination_structure",
                "event_history_type", "target_status", "target_type", "target_type_v2",
                "target_name", "acquirer_name", "acquirer_type", "acquirer_type_v2",
                "pct_acquired", "announced_date", "announced_date_precision",
                "financials_disclosure_status", "model_confidence", "dt_prompt_version",
                "hc_prompt_version", "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "ACQUISITION", "ACQUISITION", "MERGER", "ANNOUNCED",
                "PUBLIC", "standalone_company", "standalone_company", "Verity Biosciences",
                "Halden Therapeutics", "strategic_corporate", "strategic_corporate",
                45.0, "2026-08-18", "exact", "UNKNOWN", "HIGH", "0.10", "0.20", TXN]
        if have:
            cols.append("offer_mechanism")
            vals.append("TENDER_OFFER")
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # The production write with the production flag, not a local reimplementation.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        checks = [("pct_acquired", 45.0)]                       # control, always asserted
        if have:
            checks.insert(0, ("offer_mechanism", "TENDER_OFFER"))
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
            aggregate.run(conn, cfg, "offer-test")
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
            _eq(failures, f"canonical/offer_mechanism (read_source={src})",
                canon["offer_mechanism"], "TENDER_OFFER")
            # Coexistence survives the whole path, not just the write.
            _eq(failures, "canonical/combination_structure coexists",
                canon["combination_structure"], "MERGER")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Vocabulary — one value plus null, by decision
# ---------------------------------------------------------------------------

def _test_vocabulary(failures: list[str]) -> None:
    vocab = getattr(hc, "_VALID_OFFER_MECHANISM", None)
    if vocab is None:
        failures.append("vocab: stages.high_confidence_extract defines no "
                        "_VALID_OFFER_MECHANISM — offer_mechanism is not implemented")
        return
    _eq(failures, "vocab/exact", set(vocab), {"TENDER_OFFER"})

    _eq(failures, "validate/tender_offer", hc._validate(_hc(offer_mechanism="TENDER_OFFER")), None)
    _eq(failures, "validate/null", hc._validate(_hc()), None)

    # Deferred by §T12. Rejected, not merely unused -- otherwise they drift in later.
    for bad in ("MANDATORY_OFFER", "SCHEME_OF_ARRANGEMENT", "ONE_STEP_MERGER",
                "TWO_STEP_MERGER", "DIRECT", "REVERSE_TRIANGULAR"):
        if hc._validate(_hc(offer_mechanism=bad)) is None:
            failures.append(f"validate/{bad}: accepted, but §T12 excludes it from the "
                            f"offer_mechanism vocabulary")


# ---------------------------------------------------------------------------
# 3. No suppression — the deliberate contrast with asset_type
# ---------------------------------------------------------------------------

def _test_no_cross_field_guard(failures: list[str]) -> None:
    """asset_type is cleared when target_type is not assets. offer_mechanism must NOT be.

    A tender offer followed by a back-end merger is one transaction with both facts, so a
    guard keyed on combination_structure, target_status or event type would delete a true
    value. This reads the stage source because the write path needs a live model call.
    """
    src = open(os.path.join(ROOT, "stages", "high_confidence_extract.py"), encoding="utf-8").read()
    if re.search(r"offer_mechanism\s*=\s*None", src):
        failures.append("stage: offer_mechanism is cleared somewhere in the write path — "
                        "no cross-field rule may suppress it (§T12)")
    if 'flags.get("offer_mechanism")' in src or "1 if" in src.split("offer_mechanism")[0][-40:]:
        failures.append("stage: offer_mechanism appears to be coerced rather than passed "
                        "through; null must stay null")
    if '(txn.get("deal") or {}).get("offer_mechanism")' not in src:
        failures.append("stage: offer_mechanism is not read from the deal block")


# ---------------------------------------------------------------------------
# 4. SEC corroboration — contributes evidence, does not absorb mechanics
# ---------------------------------------------------------------------------

def _test_sec_corroboration(failures: list[str]) -> None:
    """Stage 11 maps TENDER_OFFER only.

    The mapping is a source-level guard rather than a function call, so it is asserted at
    source level. What matters is the asymmetry: exactly one merger_structure value has a
    V3 destination, and the other three must not acquire one here.
    """
    src = open(os.path.join(ROOT, "stages", "agreement_extract.py"), encoding="utf-8").read()

    if 'result.get("merger_structure") == "TENDER_OFFER"' not in src:
        failures.append("agreement: no corroboration mapping from merger_structure = "
                        "TENDER_OFFER to an offer_mechanism observation")
    if '"offer_mechanism": "TENDER_OFFER"' not in src:
        failures.append("agreement: corroboration does not emit offer_mechanism")

    # The three deferred mechanics values must not be mapped anywhere in this stage.
    for mech in ("DIRECT", "FORWARD_TRIANGULAR", "REVERSE_TRIANGULAR"):
        if re.search(rf'"{mech}"\s*:\s*"?offer_mechanism|offer_mechanism.*{mech}', src):
            failures.append(f"agreement: {mech} appears mapped to offer_mechanism — §T2 "
                            f"defers merger mechanics and gives them no V3 destination")

    # merger_structure keeps its own canonical write and is not replaced.
    if '_CANONICAL_FIELD_OBSERVATION_MAP' in src and '"merger_structure"' not in src:
        failures.append("agreement: merger_structure lost its canonical mapping — S-D "
                        "corroborates it, it does not retire it")
    if "offer_mechanism" in getattr(agree, "_CANONICAL_FIELD_OBSERVATION_MAP", {}):
        failures.append("agreement: offer_mechanism was added to the Stage 11 canonical "
                        "map — Stage 9 owns this column, so Stage 11 must only observe it")


# ---------------------------------------------------------------------------
# 5. Prompt contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(HC_PROMPT, encoding="utf-8").read()
    _check_version(failures, "high_confidence_extraction", text, hc._VERSION,
                   (0, 20), "introduced offer_mechanism")

    if "offer_mechanism" not in text:
        failures.append("prompt: offer_mechanism absent from the contract")

    # The two anti-inference rules. Without them the model reaches for the field on every
    # public-company deal, which is the failure mode this vocabulary invites.
    if "Do NOT infer TENDER_OFFER because the target is public" not in text:
        failures.append("prompt: missing the public-target anti-inference rule")
    if "Do NOT infer it from a merger agreement alone" not in text:
        failures.append("prompt: missing the merger-agreement anti-inference rule")

    # A worked example where both facts hold, so coexistence is demonstrated, not just told.
    if '"offer_mechanism": "TENDER_OFFER"' not in text:
        failures.append("prompt: no worked TENDER_OFFER example")


def main() -> int:
    failures: list[str] = []
    _test_canonical_path(failures)
    _test_vocabulary(failures)
    _test_no_cross_field_guard(failures)
    _test_sec_corroboration(failures)
    _test_prompt_contract(failures)

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("PASS offer_mechanism: four-hop canonical path, vocabulary closed, no suppression, "
          "SEC corroborates without absorbing mechanics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
