#!/usr/bin/env python3
"""Guard tests for asset_type and the target_type cleanup (§T13, §T3, §T4 — slice S-C).

Three changes, one slice:

  * asset_type is added as a SUBORDINATE classification of target_type = assets. It
    answers what kind of asset is transacted, and it is not sector: a pipeline is
    INFRASTRUCTURE because a pipeline is the thing transacted, whoever buys it.
  * `spinco` leaves target_type. It named an event/role, not a structure, and duplicated
    what v2_event_type already says. A spin-off is now typed on the distributed entity's
    own structural merits.
  * is_divestiture stops being authored. §T4 removes it rather than repairing it -- its
    V2 derivation compared uppercase constants against lowercase output and returned 0
    for every real divestiture.

The subordination rule creates a specific hazard this file is built around: a suppressed
asset_type and a correctly-empty one look identical downstream. So the tests assert the
suppression is a DECISION (a non-null value on a non-asset target is refused) rather than
an absence, and they assert the populated case survives all four hops to canonical
storage -- the boundary where S-A's equivalent field was silently lost.
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
import stages.deal_type_classify as dtc
import stages.high_confidence_extract as hc
from lib.observation_writer import (
    HC_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTLED_ASSET_TYPES = {
    "REAL_ESTATE", "INFRASTRUCTURE", "ENERGY", "NATURAL_RESOURCES",
    "INTELLECTUAL_PROPERTY", "DATA", "FACILITY", "EQUIPMENT",
    "CONTRACTS_OR_RIGHTS", "BRAND_OR_PRODUCT", "OTHER",
}


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_text: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Assert prompt and stage agree, and that neither predates `minimum`.

    Deliberately not an equality check against a literal. Pinning an exact version asserts
    the prompt is frozen, which breaks on the next slice's legitimate bump -- exactly what
    happened to two existing tests when this slice moved the classifier to 0.10. Compare
    numerically: these are dotted decimals, so 0.10 > 0.9 and a string comparison inverts.
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


def _clf(**over) -> dict:
    base = {
        "v2_event_type": "ACQUISITION", "deal_type": "ACQUISITION",
        "combination_structure": None, "spin_split_type": None,
        "distribution_mechanism": None, "recap_type": None,
        "target_type": "standalone_company", "event_history_type": "ANNOUNCED",
        "target_status": "PRIVATE", "model_confidence": "HIGH",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Canonical-field gate: staging -> observation -> aggregation -> canonical
# ---------------------------------------------------------------------------

def _test_canonical_path(failures: list[str]) -> None:
    """Four hops, production writer, production include_* flag.

    `stake_transition_type` is the control: an unchanged HC_FIELDS member carried through
    the identical path. On pre-S-C code it passes while asset_type fails, which is what
    distinguishes "this field is broken" from "this harness is broken". It is therefore
    seeded and asserted even when asset_type cannot be, rather than skipped.
    """
    if "asset_type" not in HC_FIELDS:
        failures.append("observation/HC_FIELDS: asset_type is absent, so Stage 4's "
                        "include_hc write will not observe it and the canonical column "
                        "stays NULL")
    if "stake_transition_type" not in HC_FIELDS:
        failures.append("observation/control: stake_transition_type missing from HC_FIELDS")

    db_path = os.path.join(tempfile.mkdtemp(), "asset.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
        have_asset = "asset_type" in have
        if not have_asset:
            failures.append("schema/staging_extraction: asset_type column is missing — "
                            "migration 006 did not run")
        tr_cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
        if "asset_type" not in tr_cols:
            failures.append("schema/transaction_record: asset_type column is missing")

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "event_history_type",
                "target_status", "target_type", "target_type_v2", "target_name",
                "acquirer_name", "acquirer_type", "acquirer_type_v2",
                "stake_transition_type", "announced_date", "announced_date_precision",
                "financials_disclosure_status", "model_confidence", "dt_prompt_version",
                "hc_prompt_version", "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "ACQUISITION", "ACQUISITION", "ANNOUNCED", "PRIVATE",
                "assets", "assets", "Gulf Coast pipeline system", "Cascade Midstream",
                "strategic_corporate", "strategic_corporate", "FULL_ACQUISITION",
                "2026-08-18", "exact", "UNKNOWN", "HIGH", "0.10", "0.19", "tc_asset_0001"]
        if have_asset:
            cols.append("asset_type")
            vals.append("INFRASTRUCTURE")
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # The production write with the production flag, not a local reimplementation.
        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        checks = [("stake_transition_type", "FULL_ACQUISITION")]          # control, always
        if have_asset:
            checks.insert(0, ("asset_type", "INFRASTRUCTURE"))
        for field, expected in checks:
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id='tc_asset_0001' AND field_name=?", (field,)).fetchone()
            if row is None:
                failures.append(f"observation/{field}: no observation row was written")
            else:
                _eq(failures, f"observation/{field}", row["field_value"], expected)

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "asset-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id='tc_asset_0001'").fetchone()
        if canon is None:
            failures.append("canonical: no transaction_record row")
            return
        src = DEFAULT_AGGREGATION_READ_SOURCE
        _eq(failures, f"canonical/stake_transition_type CONTROL (read_source={src})",
            canon["stake_transition_type"], "FULL_ACQUISITION")
        if have_asset:
            _eq(failures, f"canonical/asset_type (read_source={src})",
                canon["asset_type"], "INFRASTRUCTURE")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Subordination — suppression must be a refusal, not an absence
# ---------------------------------------------------------------------------

def _test_subordination(failures: list[str]) -> None:
    vocab = getattr(hc, "_VALID_ASSET_TYPES", None)
    if vocab is None:
        failures.append("vocab: stages.high_confidence_extract defines no "
                        "_VALID_ASSET_TYPES — asset_type is not implemented")
        return

    # The settled §T13 vocabulary, exactly. Guards against silent expansion as much as
    # against omission: "settled but extensible" means extended deliberately, not drifting.
    _eq(failures, "vocab/settled §T13 set", set(vocab), SETTLED_ASSET_TYPES)

    def _hc_result(asset_type):
        """A schema-valid HC response whose only variable is target.asset_type.

        It must be complete: _validate returns on the first problem, so a stub missing
        required keys would fail for the wrong reason and the asset_type check would
        never run.
        """
        return {
            "target": {"name": "T", "domain": None, "ticker": None,
                       "description": None, "asset_type": asset_type},
            "acquirer": {"name": "A", "domain": None, "ticker": None,
                         "type": "strategic_corporate", "description": None,
                         "sponsor_name": None},
            "parent_seller": {"name": None, "ticker": None, "description": None},
            "dates": {}, "deal": {}, "target_financials": {},
            "value": {}, "value_observations": [], "reported_multiples": [], "acquirers": [], "buy_side_sponsors": [],
            "parent_sellers": [],
            "parent_acquirers": [],
            "sell_side_sponsors": [],
            "jv_partners": [],
            "sellers": [],
            "features": {"is_platform_investment": None, "is_secondary_buyout": None,
                         "is_merger_of_equals": None},
            "financials_disclosure_status": "UNKNOWN", "transaction_terms_disclosure_status": "UNKNOWN", "model_confidence": "HIGH",
        }

    # Sanity: the baseline must validate, or every assertion below is vacuous.
    baseline_err = hc._validate(_hc_result(None))
    if baseline_err is not None:
        failures.append(f"validator/test-fixture: baseline HC result does not validate "
                        f"({baseline_err}); the asset_type assertions would be vacuous")

    for good in ("INFRASTRUCTURE", "FACILITY", "REAL_ESTATE"):
        err = hc._validate(_hc_result(good))
        if err is not None and "asset_type" in err:
            failures.append(f"validator: rejected valid asset_type {good}: {err}")
    bad = hc._validate(_hc_result("PIPELINE"))
    if bad is None or "asset_type" not in bad:
        failures.append("validator: an out-of-vocabulary asset_type was accepted")

    # FACILITY and REAL_ESTATE are deliberately separate (§T13). Collapsing them would
    # silently lose the operating-plant / property-holding distinction.
    for v in ("FACILITY", "REAL_ESTATE"):
        if v not in vocab:
            failures.append(f"vocab: {v} missing — §T13 keeps these distinct")

    # The subordination rule itself is enforced at the write, where target_type is known.
    src = open(os.path.join(ROOT, "stages", "high_confidence_extract.py"), encoding="utf-8").read()
    if 'effective_target_type != "assets"' not in src:
        failures.append("stage: no write-time guard clearing asset_type when target_type "
                        "is not assets — a suppressed value and a correct null are then "
                        "indistinguishable")


# ---------------------------------------------------------------------------
# 3. target_type: spinco removed, spin/split typed structurally
# ---------------------------------------------------------------------------

def _test_target_type(failures: list[str]) -> None:
    if "spinco" in dtc._VALID_TARGET_TYPES_V2:
        failures.append("enum: spinco is still an accepted target_type")
    if dtc._validate(_clf(target_type="spinco")) is None:
        failures.append("validator: new output naming target_type=spinco was accepted")

    _eq(failures, "enum/surviving values", set(dtc._VALID_TARGET_TYPES_V2),
        {"standalone_company", "subsidiary", "business_unit", "assets"})

    # A spin-off is typed on the distributed entity's structure. Both shapes are valid;
    # the event stays on v2_event_type.
    for tt in ("subsidiary", "business_unit"):
        for event in ("SPIN_OFF", "SPLIT_OFF"):
            r = _clf(v2_event_type=event, deal_type=event, target_type=tt,
                     spin_split_type=event,
                     distribution_mechanism="EXCHANGE_OFFER" if event == "SPLIT_OFF" else "PRO_RATA")
            err = dtc._validate(r)
            if err is not None:
                failures.append(f"validator: {event} + {tt} rejected: {err}")

    # The spin fact must remain on the event axis, never reconstructed from target_type.
    for event in ("SPIN_OFF", "SPLIT_OFF"):
        if event not in dtc._VALID_V2_EVENT_TYPES:
            failures.append(f"enum: {event} left v2_event_type — the spin fact must stay there")

    clf = open(os.path.join(ROOT, "prompts", "deal_type_classifier.md"), encoding="utf-8").read()
    _check_version(failures, "classifier", clf, dtc._VERSION, (0, 10),
                   "removed spinco from target_type")
    if '"target_type": "spinco"' in clf:
        failures.append("classifier prompt: a worked example still emits target_type=spinco")
    if "Do NOT use standalone_company merely because" not in clf:
        failures.append("classifier prompt: missing the rule against typing a spin-off by "
                        "what it becomes rather than what is transacted")


# ---------------------------------------------------------------------------
# 3b. Classifier casing: lowercase is output, uppercase is tolerated input
# ---------------------------------------------------------------------------
#
# Two different facts that are easy to collapse into one:
#
#   lowercase  = the valid CURRENT OUTPUT vocabulary. What the model must emit.
#   uppercase  = tolerated LEGACY INPUT, accepted for rollout compatibility and
#                normalized into target_type_v2. Never valid new output.
#
# The prompt had drifted on both halves. Its IMPORTANT DISTINCTIONS block instructed
# `target_type = BUSINESS_UNIT or SUBSIDIARY` while the same file declared uppercase no
# longer valid, and its failure-mode table claimed the parser REJECTS uppercase, which it
# does not. That combination is how uppercase reaches the raw `target_type` column -- the
# column every Stage 9 derivation reads, and the one that made is_take_private return 0 for
# every transaction until the comparison was case-folded.

# Text that legitimately names uppercase target types: the labelled legacy sentence and the
# failure-mode row that describes tolerance. Everything else is instruction to the model.
_LEGACY_LINE_MARKERS = (
    "Legacy uppercase values",
    "legacy uppercase target_type",
)

# `\s*` spans newlines deliberately. The two sites this guards are line-wrapped --
# "acquirer_type =" ends one line and "PRIVATE_EQUITY" begins the next -- so a per-line
# scan sees neither, which is exactly how they survived the audit's first pass.
_UPPER_TARGET_TYPE = re.compile(
    r"target_type\s*=\s*`?(STANDALONE_COMPANY|BUSINESS_UNIT|SUBSIDIARY|ASSETS)\b", re.S)
_UPPER_ACQUIRER_TYPE = re.compile(r"acquirer_type\s*=\s*`?([A-Z][A-Z_]{3,})\b", re.S)


def _flag_uppercase(failures: list[str], body: str, pattern, field: str, why: str) -> None:
    for m in pattern.finditer(body):
        span = body[m.start():m.end()]
        line_start = body.rfind("\n", 0, m.start()) + 1
        context = body[line_start:m.end() + 60]
        if any(marker in context for marker in _LEGACY_LINE_MARKERS):
            continue
        lineno = body[:m.start()].count("\n") + 1
        failures.append(f"classifier prompt L{lineno}: active text instructs "
                        f"{field} = {m.group(1)} ({span.split('=')[-1].strip()!r}) — {why}")


def _test_classifier_casing(failures: list[str]) -> None:
    clf = open(os.path.join(ROOT, "prompts", "deal_type_classifier.md"), encoding="utf-8").read()
    body = clf.split("## 9. Versioning")[0]

    # (a) No uppercase target_type / acquirer_type in active instruction text.
    _flag_uppercase(failures, body, _UPPER_TARGET_TYPE, "target_type",
                    "uppercase is tolerated legacy INPUT, not valid current output, so the "
                    "prompt must not ask the model for it")
    _flag_uppercase(failures, body, _UPPER_ACQUIRER_TYPE, "acquirer_type",
                    "that vocabulary is lowercase and is extracted downstream by "
                    "high_confidence_extraction, not emitted by this prompt")

    # (b) The failure-mode row must describe what the parser does.
    if "Parser rejects — lowercase required in V2" in clf:
        failures.append("classifier prompt: the failure-mode table still claims the parser "
                        "REJECTS legacy uppercase target_type. It accepts it — see "
                        "_VALID_LEGACY_TARGET_TYPES — so the table documents a guard that "
                        "does not exist")
    if "target_type_v2" not in clf.split("## 9. Versioning")[0]:
        failures.append("classifier prompt: the active body never mentions target_type_v2, so "
                        "it cannot explain where a tolerated legacy value actually lands")

    # (c) Pin the tolerance BEHAVIOURALLY, in both directions. Ending rollout compatibility
    #     is a decision, and this assertion forces it to be an explicit one rather than a
    #     quiet tightening that leaves the prompt describing the old world.
    for legacy, expected in (("STANDALONE_COMPANY", "standalone_company"),
                             ("BUSINESS_UNIT", "business_unit"),
                             ("SUBSIDIARY", "subsidiary"),
                             ("ASSETS", "assets")):
        if dtc._validate(_clf(target_type=legacy)) is not None:
            failures.append(f"parser: legacy uppercase {legacy} is now REJECTED. If rollout "
                            "compatibility was deliberately ended, update this test and the "
                            "prompt's failure-mode row together")
        got = dtc._normalize_target_type_v2(legacy)
        _eq(failures, f"normalization/{legacy}", got, expected)
        # ...and the current lowercase form must survive normalization untouched.
        _eq(failures, f"normalization/{expected} idempotent",
            dtc._normalize_target_type_v2(expected), expected)


# ---------------------------------------------------------------------------
# 4. is_divestiture no longer authored, column retained
# ---------------------------------------------------------------------------

def _test_is_divestiture_removed(failures: list[str]) -> None:
    if "is_divestiture" in aggregate._STAGE9_OWNED_COLUMNS:
        failures.append("aggregate: is_divestiture is still authored by Stage 9 — §T4 "
                        "removes it rather than repairing it")
    if "is_divestiture" in aggregate._derive_flags(
            {"target_type": "business_unit", "deal_type": "ACQUISITION"}):
        failures.append("aggregate: _derive_flags still produces is_divestiture")

    # The column stays: removal is migration work, and exports still read it.
    db_path = os.path.join(tempfile.mkdtemp(), "div.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(transaction_record)")}
        if "is_divestiture" not in cols:
            failures.append("schema: the is_divestiture column was dropped — S-C stops "
                            "authoring it but must not remove it")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. HC contract
# ---------------------------------------------------------------------------

def _test_hc_contract(failures: list[str]) -> None:
    text = open(os.path.join(ROOT, "prompts", "high_confidence_extraction.md"),
                encoding="utf-8").read()
    _check_version(failures, "hc", text, hc._VERSION, (0, 19), "introduced asset_type")
    if "asset_type" not in text:
        failures.append("hc prompt: asset_type absent from the contract")
    if '"asset_type": "INFRASTRUCTURE"' not in text:
        failures.append("hc prompt: no worked asset example")
    if "Asset type is NOT sector" not in text:
        failures.append("hc prompt: missing the asset-type-is-not-sector rule, which is the "
                        "distinction §T13 exists to draw")

    # Stale active notes. The versioning table legitimately names retired fields in its
    # history rows, so assertions below run against the instruction body only.
    body = text.split("## 9. Versioning")[0] if "## 9. Versioning" in text else text
    if "spinco" in body:
        failures.append("hc prompt: `spinco` is still listed in the active target_type "
                        "vocabulary — V3 §T3 removed the value, so it can no longer arrive")
    if "When classifier is updated to v0.6+" in body:
        failures.append("hc prompt: the V2 note still promises a rename 'when classifier is "
                        "updated to v0.6+'. The classifier is well past that and the rename "
                        "never happened: the template keeps legacy LABELS while the stage "
                        "supplies current values")
    if "is_divestiture" in body or "is_add_on" in body:
        # Naming them is fine; describing them as currently authored is not.
        if "no longer authored" not in body:
            failures.append("hc prompt: the derivation note still presents is_divestiture "
                            "and/or is_add_on as built derivations. §T4 removed the first and "
                            "§T7 retired the second")
    if "target_type_v2" not in body:
        failures.append("hc prompt: the input note does not say that target_type arrives in "
                        "its normalized current representation, which is what the stage "
                        "actually passes (target_type_v2 or target_type)")


def main() -> int:
    failures: list[str] = []
    _test_canonical_path(failures)
    _test_subordination(failures)
    _test_target_type(failures)
    _test_classifier_casing(failures)
    _test_is_divestiture_removed(failures)
    _test_hc_contract(failures)

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print("PASS asset_type: canonical path holds, subordination refused not absent, "
          "spinco rejected, spin/split typed structurally, is_divestiture unauthored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
