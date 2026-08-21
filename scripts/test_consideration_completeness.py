#!/usr/bin/env python3
"""Guard tests for contingent consideration components (slice S-F).

`consideration_components` is the authoritative structured extraction of what was paid.
This slice makes generic contingency representable, enforces the form vocabulary that was
never enforced, and removes the flag that competed with the components.

WHAT WAS WRONG

  1. Generic contingency had nowhere to go. A source stating consideration is contingent or
     milestone-based, without establishing an earnout or a CVR, either lost the fact or had
     it forced into a subtype the source did not support.
  2. Component `form` was never validated. The prompt listed eight values and nothing
     checked them, so an off-vocabulary spelling stored silently and then matched neither
     derived filter -- a wrong answer with no error anywhere.
  3. `includes_earnout` was defined as "earnout OR CVR": a third signal, wider in scope
     than the field it appeared to shortcut, for two facts that the components already
     answer specifically.

WHAT IS DELIBERATELY NOT HERE

  No validator tries to rediscover contingent evidence from notes or prose, and no
  replacement assertion flag exists. Whether real earnout / CVR / generic contingent
  language actually causes the model to emit the right component is an extraction-quality
  question for Gate 2, not a cross-field contract. These tests prove the structure carries
  the fact once extracted -- not that extraction is correct.
"""

from __future__ import annotations

import json
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
import stages.low_confidence_extract as lc
from lib.observation_writer import (
    LC_SCALAR_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LC_PROMPT = os.path.join(ROOT, "prompts", "low_confidence_extraction.md")
SUMMARY_PROMPT = os.path.join(ROOT, "prompts", "deal_summary.md")
TXN = "tc_consid_0001"


def _eq(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _check_version(failures: list[str], label: str, prompt_path: str, stage_version: str,
                   minimum: tuple[int, int], what: str) -> None:
    """Prompt and stage agree, and neither predates `minimum`. Compared numerically:
    these are dotted decimals, so 0.10 > 0.9 and a string comparison inverts it."""
    text = open(prompt_path, encoding="utf-8").read()
    m = re.search(r"^\*\*Version:\*\* (\d+)\.(\d+)", text, re.M)
    if m is None:
        failures.append(f"{label} prompt: no parseable version line")
        return
    _eq(failures, f"{label}/prompt-stage version parity",
        f"{m.group(1)}.{m.group(2)}", stage_version)
    if (int(m.group(1)), int(m.group(2))) < minimum:
        failures.append(f"{label} prompt: version {m.group(0)!r} predates the release that "
                        f"{what} ({minimum[0]}.{minimum[1]})")


def _lc(components: list) -> dict:
    return {
        "advisors": [],
        "consideration_components": components,
        "flags": {"deal_attitude": None, "approach_type": None,
                  "competing_bid": False, "regulatory_approvals_required": False},
        "go_shop": {"has_go_shop": False, "go_shop_period_days": None},
        "termination_fees": {},
        "model_confidence": "HIGH",
    }


# ---------------------------------------------------------------------------
# 1. Form vocabulary — the three contingent forms are valid, junk is not
# ---------------------------------------------------------------------------

def _test_form_vocabulary(failures: list[str]) -> None:
    vocab = getattr(lc, "_VALID_CONSIDERATION_FORMS", None)
    if vocab is None:
        failures.append("vocab: stages.low_confidence_extract defines no "
                        "_VALID_CONSIDERATION_FORMS — component forms are unvalidated, so an "
                        "off-vocabulary form stores silently and matches no derived filter")
        return

    for form in ("EARNOUT", "CVR", "CONTINGENT_CONSIDERATION"):
        if form not in vocab:
            failures.append(f"vocab/{form}: not a valid consideration component form")
        _eq(failures, f"validate/{form}",
            lc._validate(_lc([{"form": form, "amount": 1000}])), None)

    # The base forms must survive untouched.
    for form in ("CASH", "ACQUIRER_STOCK", "TARGET_STOCK", "DEBT_ASSUMED",
                 "RETAINED_EQUITY", "OTHER"):
        if form not in vocab:
            failures.append(f"vocab/{form}: pre-existing form was dropped")

    # Off-vocabulary spellings are the failure this validator exists to stop.
    for bad in ("EARN_OUT", "Earnout", "CONTINGENT", "MILESTONE", None):
        if lc._validate(_lc([{"form": bad, "amount": 1}])) is None:
            failures.append(f"validate/{bad!r}: accepted as a component form")


# ---------------------------------------------------------------------------
# 2. Derived filters are specific, and the generic form triggers neither
# ---------------------------------------------------------------------------

def _test_derived_filters(failures: list[str]) -> None:
    cases = [
        ("earnout",            [{"form": "CASH"}, {"form": "EARNOUT"}],                 1, 0),
        ("cvr",                [{"form": "CASH"}, {"form": "CVR"}],                     0, 1),
        ("generic contingent", [{"form": "CASH"}, {"form": "CONTINGENT_CONSIDERATION"}], 0, 0),
        ("both subtypes",      [{"form": "EARNOUT"}, {"form": "CVR"}],                  1, 1),
        ("cash only",          [{"form": "CASH"}],                                      0, 0),
        ("no components",      [],                                                      0, 0),
    ]
    for label, comps, want_e, want_c in cases:
        raw = json.dumps(comps)
        _eq(failures, f"has_earnout/{label}", aggregate._derive_has_earnout(raw), want_e)
        _eq(failures, f"has_cvr/{label}", aggregate._derive_has_cvr(raw), want_c)


# ---------------------------------------------------------------------------
# 3. The component path: Stage 7 storage -> observations -> Stage 9 -> canonical
# ---------------------------------------------------------------------------

def _test_component_path(failures: list[str]) -> None:
    """Components use the production component writers, not a scalar field.

    Two observation shapes exist and both are asserted: the whole array as one json
    observation, and compound `consideration.{form}.{attr}` rows. CASH is the control --
    it is unchanged by this slice, so if it survives while the contingent component does
    not, the failure is the new form and not the harness.
    """
    db_path = os.path.join(tempfile.mkdtemp(), "consid.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        components = [
            {"form": "CASH", "amount": 50000000, "percentage": 92.6,
             "description": "$50M cash at closing"},
            {"form": "CONTINGENT_CONSIDERATION", "amount": 4000000, "percentage": 7.4,
             "description": "up to $4M contingent on unspecified milestones"},
        ]
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-18','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO staging_extraction (
                   source_raw_id, status, deal_type, v2_event_type, event_history_type,
                   target_status, target_type, target_type_v2, target_name, acquirer_name,
                   acquirer_type, acquirer_type_v2, announced_date, announced_date_precision,
                   consideration_components, financials_disclosure_status, model_confidence,
                   dt_prompt_version, hc_prompt_version, lc_prompt_version,
                   transaction_cluster_id
               ) VALUES (?, 'CLUSTERED', 'ACQUISITION', 'ACQUISITION', 'ANNOUNCED', 'PRIVATE',
                         'standalone_company', 'standalone_company', 'Target', 'Acquirer',
                         'strategic_corporate', 'strategic_corporate', '2026-08-18', 'exact',
                         ?, 'DISCLOSED', 'HIGH', '0.10', '0.20', '0.7', ?)""",
            (srid, json.dumps(components), TXN))
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="LC_EXTRACT",
            include_stage3=True, include_hc=True, include_lc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        obs = {r["field_name"]: r["field_value"] for r in conn.execute(
            "SELECT field_name, field_value FROM transaction_field_observation"
            " WHERE transaction_id=?", (TXN,))}

        if "consideration_components" not in obs:
            failures.append("observation/json: no whole-array observation was written")
        # Control and subject through the identical compound path.
        if "consideration.CASH.amount" not in obs:
            failures.append("observation/CONTROL consideration.CASH.amount: missing")
        if "consideration.CONTINGENT_CONSIDERATION.amount" not in obs:
            failures.append("observation/consideration.CONTINGENT_CONSIDERATION.amount: "
                            "missing — the contingent amount is not represented in the "
                            "compound observation path")

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "consid-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        row = conn.execute(
            "SELECT consideration_components, has_earnout, has_cvr FROM transaction_record"
            " WHERE transaction_id=?", (TXN,)).fetchone()
        if row is None:
            failures.append("canonical: no transaction_record row")
            return

        canon = json.loads(row["consideration_components"] or "[]")
        forms = [c.get("form") for c in canon]
        src = DEFAULT_AGGREGATION_READ_SOURCE
        # Additive: the contingent component must not have replaced the base consideration.
        if "CASH" not in forms:
            failures.append(f"canonical/CONTROL (read_source={src}): CASH component lost")
        if "CONTINGENT_CONSIDERATION" not in forms:
            failures.append(f"canonical (read_source={src}): CONTINGENT_CONSIDERATION "
                            f"component lost; got {forms!r}")
        _eq(failures, "canonical/component count", len(canon), 2)

        amounts = {c.get("form"): c.get("amount") for c in canon}
        _eq(failures, "canonical/contingent amount preserved",
            amounts.get("CONTINGENT_CONSIDERATION"), 4000000)
        _eq(failures, "canonical/CONTROL cash amount preserved", amounts.get("CASH"), 50000000)

        # Generic contingency triggers neither specific filter.
        _eq(failures, "canonical/has_earnout not invented", row["has_earnout"], 0)
        _eq(failures, "canonical/has_cvr not invented", row["has_cvr"], 0)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. includes_earnout is gone, end to end
# ---------------------------------------------------------------------------

def _test_flag_retired(failures: list[str]) -> None:
    if "includes_earnout" in LC_SCALAR_FIELDS:
        failures.append("observation/LC_SCALAR_FIELDS: includes_earnout still observed — "
                        "Stage 7 no longer writes it, so this yields a permanent NULL")
    if "includes_earnout" in {f for f, _ in aggregate._FIELDS}:
        failures.append("aggregate/_FIELDS: includes_earnout still aggregated")
    if "includes_earnout" in aggregate._STAGE9_OWNED_COLUMNS:
        failures.append("aggregate/_STAGE9_OWNED_COLUMNS: includes_earnout still written")

    stage = open(os.path.join(ROOT, "stages", "low_confidence_extract.py"), encoding="utf-8").read()
    if "includes_earnout" in stage:
        failures.append("stage: includes_earnout still referenced in Stage 7")
    summarize = open(os.path.join(ROOT, "stages", "summarize.py"), encoding="utf-8").read()
    if "includes_earnout" in summarize:
        failures.append("summarize: still passes includes_earnout into the summary prompt")

    # The version label and the changelog name it deliberately -- a retirement that leaves
    # no trace is worse than one that explains itself. What must not survive is the
    # contract: the field definitions, the output schema, and the worked examples.
    for label, path in (("prompt", LC_PROMPT), ("deal_summary", SUMMARY_PROMPT)):
        text = open(path, encoding="utf-8").read()
        contract = "\n".join(
            line for line in text.split("\n")
            if not line.startswith("**Version:**") and not line.startswith("| 0.")
        ).split("## 9. Versioning")[0]
        if "includes_earnout" in contract:
            failures.append(f"{label}: includes_earnout survives in the contract, schema or "
                            f"an example — only the version label and changelog may name it")


# ---------------------------------------------------------------------------
# 5. Prompt contract
# ---------------------------------------------------------------------------

def _test_prompt_contract(failures: list[str]) -> None:
    text = open(LC_PROMPT, encoding="utf-8").read()
    _check_version(failures, "low_confidence_extraction", LC_PROMPT, lc._VERSION,
                   (0, 7), "added CONTINGENT_CONSIDERATION")

    if "CONTINGENT_CONSIDERATION" not in text:
        failures.append("prompt: CONTINGENT_CONSIDERATION absent from the form enum")

    # Both directions of the most-specific rule. One without the other is a bias.
    if "Do NOT reach for CONTINGENT_CONSIDERATION when the source supports EARNOUT or CVR" not in text:
        failures.append("prompt: missing the rule against using the generic form when a "
                        "subtype is established")
    if "do not promote a vague" not in text:
        failures.append("prompt: missing the rule against promoting unspecified contingency "
                        "to EARNOUT")
    if "ADDITIVE" not in text:
        failures.append("prompt: missing the rule that a contingent component never replaces "
                        "base consideration")

    # Stale active vocabulary. The versioning table may name retired values in its history
    # rows; the instruction body may not present them as receivable.
    body = text.split("## 9. Versioning")[0] if "## 9. Versioning" in text else text
    if "spinco" in body:
        failures.append("prompt: `spinco` is still listed in the active target_type input "
                        "vocabulary — V3 §T3 removed the value, so no upstream stage can "
                        "supply it")


def main() -> int:
    failures: list[str] = []
    _test_form_vocabulary(failures)
    _test_derived_filters(failures)
    _test_component_path(failures)
    _test_flag_retired(failures)
    _test_prompt_contract(failures)

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("PASS consideration: three contingent forms valid, derived filters specific, "
          "components additive through the canonical path, includes_earnout retired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
