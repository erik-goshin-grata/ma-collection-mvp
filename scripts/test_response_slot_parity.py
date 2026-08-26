#!/usr/bin/env python3
"""HC 0.29 — every instructed field has a slot in the response the model is shown.

WHAT WENT WRONG

An executable-capture parity audit traced every fact the delivered HC contract
instructs, from the prompt through to `transaction_record`. Two of them were
instructed in the section 4 prose and had no key in that same fence's
`RESPONSE FORMAT` block:

    deal.stake_transition_type
    round_size

Both are already declared in section 6's output schema. Section 6 is
documentation -- `load_prompt_file` extracts only the section 4 and section 5
fences -- so it reaches no model and cannot supply a slot. The delivered
contract therefore asked for two facts and offered nowhere to put them.

WHY ONLY ONE OF THEM WAS VISIBLY BROKEN

`stake_transition_type` survived on prose alone: the model emitted the key
unprompted by the structure, and it reached canonical. That is exactly what hid
the defect -- a field working by luck looks identical to a field working by
design, until the other one fails.

`round_size` did not survive. The PRIMARY CAPITAL rule tells the model to set
`value.amount = null` and record the figure "in `round_size` (below)". There was
no `round_size` below. On the M&A path a primary-capital amount was therefore
removed from `value` and had nowhere to land. Across two live corpora -- 47 M&A
extractions -- `round_size` was never once populated from this stage; every
populated `round_size` came from the funding stage instead.

NOTHING DOWNSTREAM NEEDED BUILDING

Both links already existed the whole way: Stage 4 reads `deal.stake_transition_type`
and `txn.round_size`, both fields sit in the production HC observation group, and
Stage 9 owns both canonical columns. This change adds no field, no rule and no
derivation -- it restores two keys to the structure.

LAYER

The contract assertions parse the response block out of
load_prompt_file(...)["system"], so a key that drifts outside the section 4 fence
fails here. The canonical assertions run the production observation writer with
the production include_hc flag and the production aggregation, on the configured
read source. Section 7's examples are outside the fence, reach no model, and are
deliberately not asserted.

Run from project root:
    python scripts/test_response_slot_parity.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DEFAULT_AGGREGATION_READ_SOURCE  # noqa: E402
from db import get_connection, init_db  # noqa: E402
from prompts.base import load_prompt_file  # noqa: E402
import stages.aggregate as aggregate  # noqa: E402
import stages.high_confidence_extract as hc  # noqa: E402
from lib.observation_writer import (  # noqa: E402
    HC_FIELDS,
    backfill_observation_transaction_ids,
    write_staging_observations_for_extraction,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def check_version_floor(md: str, stage_version: str, introduced: str) -> None:
    """Pin the rule's provenance without freezing the prompt at one version."""
    declared = re.search(r"^\*\*Version:\*\* ([0-9.]+)", md, re.M)
    check(f"versioning table still carries the {introduced} row",
          bool(re.search(rf"^\| {re.escape(introduced)} \|", md, re.M)), True)
    check("prompt declares a version", bool(declared), True)
    if not declared:
        return
    check(f"prompt version >= {introduced} (currently {declared.group(1)})",
          _version_tuple(declared.group(1)) >= _version_tuple(introduced), True)
    check("stage _VERSION agrees with the prompt", stage_version, declared.group(1))


def _response_object(system: str) -> dict | None:
    """The RESPONSE FORMAT block, parsed.

    Parsing rather than substring-matching does double duty: it proves the two keys
    are at the right nesting AND that the block the model is shown is still valid
    JSON. A stray comma in the example would otherwise pass a substring test while
    handing the model a malformed structure to imitate.
    """
    start = system.find('{\n  "transactions": [')
    if start == -1:
        return None
    depth, end = 0, None
    for i in range(start, len(system)):
        if system[i] == "{":
            depth += 1
        elif system[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(system[start:end])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. The delivered response structure
# ---------------------------------------------------------------------------

def test_response_slots() -> None:
    print("\nThe response block the model is shown carries both keys:")
    prompt = load_prompt_file("high_confidence_extraction")
    system = prompt["system"]

    obj = _response_object(system)
    check("RESPONSE FORMAT block found and parses as JSON", obj is not None, True)
    if obj is None:
        return
    txns = obj.get("transactions")
    check("it has a transactions array", isinstance(txns, list) and len(txns) == 1, True)
    if not txns:
        return
    txn = txns[0]

    deal = txn.get("deal")
    check("deal object present", isinstance(deal, dict), True)
    check("deal.stake_transition_type has a slot",
          isinstance(deal, dict) and "stake_transition_type" in deal, True)
    check("round_size has a transaction-level slot", "round_size" in txn, True)

    # Key ORDER follows section 6, so the two documents cannot drift apart silently.
    if isinstance(deal, dict):
        keys = list(deal)
        check("stake_transition_type sits after pct_acquired in deal",
              keys.index("stake_transition_type") == keys.index("pct_acquired") + 1
              if "stake_transition_type" in keys and "pct_acquired" in keys else False, True)
    tkeys = list(txn)
    check("round_size sits after features, as section 6 orders it",
          tkeys.index("round_size") == tkeys.index("features") + 1
          if "round_size" in tkeys and "features" in tkeys else False, True)

    print("\nNothing else in the response structure moved:")
    check("deal still carries offer_mechanism and sponsor_transaction_role",
          isinstance(deal, dict)
          and {"offer_mechanism", "sponsor_transaction_role"} <= set(deal), True)
    check("value_observations still an array of one worked example",
          isinstance(txn.get("value_observations"), list)
          and len(txn["value_observations"]) == 1, True)
    check("the observation still carries basis and evidence",
          {"basis", "evidence"} <= set(txn["value_observations"][0])
          if isinstance(txn.get("value_observations"), list) and txn["value_observations"]
          else False, True)
    # This began as a 0.29 scope control -- R1.1 must not silently do R1.2's work, so
    # target_financials was pinned to its seven revenue/EBITDA/currency keys. R1.2 was
    # then approved and added the five balance-sheet keys at 0.30, so the control is
    # updated to its successor form rather than deleted: the object is still pinned
    # exactly, and scripts/test_balance_sheet_slots.py owns the five it gained.
    check("target_financials carries exactly the 0.30 key set",
          sorted(txn.get("target_financials", {})),
          ["balance_sheet_as_of_date", "cash_st", "cash_st_currency", "currency",
           "ebitda_amount", "ebitda_period_end", "ebitda_period_type",
           "revenue_amount", "revenue_period_end", "revenue_period_type",
           "total_debt", "total_debt_currency"])
    check("the required-fields sentence still closes the block",
          "All fields in each transaction element are required" in system, True)


# ---------------------------------------------------------------------------
# 2. The diagnosis itself — section 6 always had both
# ---------------------------------------------------------------------------

def test_schema_document_parity() -> None:
    """Section 6 declares both fields and reaches no model.

    Pinned so the versioning row's account stays true, and so a future edit cannot
    "fix" this defect by touching section 6 -- which would change nothing the model
    sees -- and have the fix look done.
    """
    print("\nSection 6 declared both all along, and is not what the model reads:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    i = md.find("## 6. Output Schema")
    j = md.find("## 7. Few-Shot Examples")
    schema_section = md[i:j] if i != -1 and j != -1 else ""
    check("section 6 present", bool(schema_section), True)
    check("section 6 declares stake_transition_type",
          '"stake_transition_type":' in schema_section, True)
    check("section 6 declares round_size", '"round_size":' in schema_section, True)

    system = load_prompt_file("high_confidence_extraction")["system"]
    check("section 6 is NOT part of the delivered system prompt",
          "## 6. Output Schema" in system, False)
    check("section 7 is NOT part of the delivered system prompt",
          "Few-Shot Examples" in system, False)


# ---------------------------------------------------------------------------
# 3. The readers were always there
# ---------------------------------------------------------------------------

def test_readers_exist() -> None:
    print("\nStage 4 already reads both paths, and both reach canonical:")
    src = (ROOT / "stages" / "high_confidence_extract.py").read_text(encoding="utf-8")
    check("Stage 4 reads deal.stake_transition_type",
          '(txn.get("deal") or {}).get("stake_transition_type")' in src, True)
    check("Stage 4 reads txn.round_size", 'txn.get("round_size")' in src, True)
    check("stake_transition_type is in the production HC observation group",
          "stake_transition_type" in HC_FIELDS, True)
    check("round_size is in the production HC observation group",
          "round_size" in HC_FIELDS, True)
    owned = set(aggregate._STAGE9_OWNED_COLUMNS)
    check("Stage 9 owns the stake_transition_type column",
          "stake_transition_type" in owned, True)
    check("Stage 9 owns the round_size column", "round_size" in owned, True)


# ---------------------------------------------------------------------------
# 4. Canonical path — production writer, production include_* flag
# ---------------------------------------------------------------------------

def test_canonical_path() -> None:
    """Four hops for round_size, with stake_transition_type as the control.

    The control is an HC_FIELDS member on the identical path that already worked
    before this change. If both fail, the harness is broken; if only round_size
    fails, the field is.

    The row is a MINORITY_INVESTMENT: section 4.1 keeps that event type on the M&A /
    HC path, and PRIMARY CAPITAL is exactly the rule that diverts its amount into
    round_size, so `value_amount` is deliberately seeded NULL.
    """
    print("\nround_size reaches canonical, end to end:")
    db_path = os.path.join(tempfile.mkdtemp(), "slots.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO source_raw (source_type, source_tier, url, title, published_date,"
            " clean_text, source_status, fetched_at)"
            " VALUES ('PR_NEWSWIRE','T1','u1','t1','2026-08-26','body','RELEVANT',?)", (now,))
        srid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        cols = ["source_raw_id", "status", "deal_type", "v2_event_type", "event_history_type",
                "target_status", "target_type", "target_type_v2", "target_name",
                "acquirer_name", "acquirer_type", "acquirer_type_v2",
                "stake_transition_type", "offer_mechanism", "round_size", "round_currency",
                "announced_date", "announced_date_precision", "financials_disclosure_status",
                "model_confidence", "dt_prompt_version", "hc_prompt_version",
                "transaction_cluster_id"]
        vals = [srid, "CLUSTERED", "MINORITY_INVESTMENT", "MINORITY_INVESTMENT", "ANNOUNCED",
                "PRIVATE", "standalone_company", "standalone_company", "Northwind Systems",
                "Cascade Growth Partners", "growth_equity", "growth_equity",
                "NEW_MINORITY_STAKE", "TENDER_OFFER", 40000000.0, "USD",
                "2026-08-26", "exact", "DISCLOSED", "HIGH", "0.15", hc._VERSION,
                "tc_slot_0001"]
        conn.execute(f"INSERT INTO staging_extraction ({', '.join(cols)})"
                     f" VALUES ({', '.join('?' * len(cols))})", vals)
        eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        write_staging_observations_for_extraction(
            conn, eid, observation_source_stage="HC_EXTRACT",
            include_stage3=True, include_hc=True)
        backfill_observation_transaction_ids(conn)
        conn.commit()

        for field, expected in (("round_size", "40000000.0"),
                                ("stake_transition_type", "NEW_MINORITY_STAKE"),
                                ("offer_mechanism", "TENDER_OFFER")):
            row = conn.execute(
                "SELECT field_value FROM transaction_field_observation"
                " WHERE transaction_id='tc_slot_0001' AND field_name=?", (field,)).fetchone()
            check(f"observation/{field} written", row is not None, True)
            if row is not None and field != "round_size":
                check(f"observation/{field} value", row["field_value"], expected)

        cfg = SimpleNamespace(log_level="ERROR",
                              aggregation_read_source=DEFAULT_AGGREGATION_READ_SOURCE)
        original = aggregate._call_agg_prompt
        aggregate._call_agg_prompt = lambda f, *a, **k: (_ for _ in ()).throw(
            AssertionError(f"unexpected aggregation conflict on {f!r}"))
        try:
            aggregate.run(conn, cfg, "slot-test")
        finally:
            aggregate._call_agg_prompt = original
        conn.commit()

        canon = conn.execute(
            "SELECT * FROM transaction_record WHERE transaction_id='tc_slot_0001'").fetchone()
        check("canonical row written", canon is not None, True)
        if canon is None:
            return
        src_label = DEFAULT_AGGREGATION_READ_SOURCE
        check(f"canonical/round_size (read_source={src_label})",
              canon["round_size"], 40000000.0)
        check(f"canonical/stake_transition_type CONTROL (read_source={src_label})",
              canon["stake_transition_type"], "NEW_MINORITY_STAKE")
        check(f"canonical/offer_mechanism CONTROL (read_source={src_label})",
              canon["offer_mechanism"], "TENDER_OFFER")
        # PRIMARY CAPITAL: the diverted amount must not reappear as a deal value.
        check("no equity_value manufactured from a primary-capital round",
              canon["equity_value"], None)
        check("no transaction_value manufactured from a primary-capital round",
              canon["transaction_value"], None)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Nothing else in the contract moved
# ---------------------------------------------------------------------------

def test_no_rule_drift() -> None:
    print("\nNo rule text changed — the earlier contracts are all still delivered:")
    prompt = load_prompt_file("high_confidence_extraction")
    system = prompt["system"]
    for marker, label in (
        ("WHAT IS NOT A DEAL-VALUE FACT", "0.28 value-scope boundary"),
        ("MULTIPLE BUYERS", "0.27 multiple buyers"),
        ("ONE ECONOMIC FACT, ONE OBSERVATION", "0.26 currency representation"),
        ("BUY-SIDE COHERENCE", "0.25 buy-side coherence"),
    ):
        check(f"{label} still delivered", marker in system, True)
    check("the pct_acquired rule text is untouched — semantics stay parked",
          "Do not\n  extract 100 — leave null for full acquisitions." in system, True)
    check("PRIMARY CAPITAL still routes the amount to round_size",
          "in round_size" in system, True)
    check("balance-sheet items still instructed but NOT opened in this slice",
          "Balance-sheet items: extract total_debt and cash_st" in system, True)
    check("user template unchanged in shape", "{title}" in prompt["user_template"], True)

    print("\nVersion and contract integrity:")
    md = (ROOT / "prompts" / "high_confidence_extraction.md").read_text(encoding="utf-8")
    check_version_floor(md, hc._VERSION, "0.29")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_response_slots()
    test_schema_document_parity()
    test_readers_exist()
    test_canonical_path()
    test_no_rule_drift()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — every instructed field has a slot; both reach canonical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
