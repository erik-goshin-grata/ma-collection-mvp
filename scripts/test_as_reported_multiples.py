#!/usr/bin/env python3
"""A stated multiple is preserved as an observation, reconciled, then made canonical.

WHAT WENT WRONG

The reference implementation had no as-reported concept anywhere. Multiples existed
only as four computed columns on transaction_record, all produced by
aggregate._compute_multiples from implied_enterprise_value, which returns
NOT_CALCULABLE and exits before reading any input when that value is absent. So
nVent's statement about Maverick Power -- "an effective enterprise value multiple of
approximately 11.5x anticipated 2026 adjusted EBITDA", and a tax-adjusted 10.5x --
was lost three times over: no prompt asked for it, no observation could carry it, and
the calculator refused the transaction for want of an EV.

THE CHAIN

    HC 0.31 prompt
      -> staging_extraction.reported_multiples          (JSON parking, as value_observations)
      -> observation_writer._write_reported_multiples
           `reported_multiple`                          PRESERVATION, one row per stated
                                                        multiple, evidence included
           `multiple.{type}.{basis}.{end}`              RECONCILIATION, at the canonical key
      -> Stage 9 _pick_value  (UNCHANGED)               -> conflict -> aggregation_conflict_log
      -> transaction_multiple                           RESOLVED canonical facts only

THE CANONICAL FACT KEY is composed from the dimensions Product actually has:
multiple_type, period_basis and the denominator period end -- the same way the ledger
already composes `shares_outstanding.{type}[.{class}]` and `consideration.{form}.{attr}`.
Nothing describing an adjustment is part of it, because Product does not distinguish
adjusted from unadjusted multiples structurally.

WHAT THIS MEANS FOR MAVERICK, AND WHY IT IS THE RIGHT ANSWER

11.5x and 10.5x compose the SAME key. They share a source, a tier and a confidence, so
_pick_value cannot separate them, and the aggregation prompt sees two values with
identical excerpts. There is nothing to separate them BY -- which is the correct
outcome, not a defect. Both observations survive in full; the disagreement lands in
aggregation_conflict_log flagged for review; and NO canonical multiple is written.
Nothing is silently chosen and two indistinguishable canonical values are never shown.

NO MULTIPLES-SPECIFIC RECONCILIATION EXISTS. _pick_value, _call_agg_prompt and
_log_conflict are untouched and are asserted so here. A multiple key goes through the
same path as every scalar field.

WHAT DID NOT CHANGE

The four flat columns are still computed and exported exactly as before.
`_compute_multiples` is not modified. No value derivation is touched. Calculated rows
are not written -- the table has room for them and nothing fills it yet.

OTHER RULES THIS FILE PINS

  * NO BACK-SOLVING, IN EITHER DIRECTION. target_ebitda and implied_enterprise_value
    stay NULL on the Maverick row.
  * A named year is ANNUAL, not NTM.
  * quality is NULL; denominator_financial_id may be NULL.
  * transaction_multiple has no reconciliation_state column -- absence of a row IS the
    unresolved state, and the conflict log carries the detail.

Run from project root:
    python scripts/test_as_reported_multiples.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db as _db
from stages import aggregate as agg
from stages import high_confidence_extract as hc
import lib.observation_writer as ow
from lib.observation_writer import write_staging_observations_for_extraction

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []

log = logging.getLogger("test_as_reported_multiples")
logging.basicConfig(level=logging.CRITICAL)


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


# ---------------------------------------------------------------------------
# 1. The prompt actually instructs it
# ---------------------------------------------------------------------------

def test_prompt() -> None:
    print("\nThe instruction reaches the model:")
    from prompts.base import load_prompt_file
    loaded = load_prompt_file("high_confidence_extraction")
    system = loaded["system"] if isinstance(loaded, dict) else loaded[0]

    # Section 4 is the only part load_prompt_file delivers. An instruction that lives
    # in section 6 or in a few-shot example reaches no model at all, which is the exact
    # failure class R1.1 and R1.2 were opened for -- so this is asserted on the
    # DELIVERED text, never on the file.
    check("REPORTED MULTIPLES block is delivered", "REPORTED MULTIPLES" in system, True)
    check("the response slot is delivered", '"reported_multiples": []' in system, True)
    check("the no-back-solve rule is delivered",
          "do NOT divide the price by the multiple" in system, True)
    check("the reverse direction is forbidden too",
          "dividing them would manufacture a fact" in system, True)
    check("the named-year rule is delivered", "A NAMED YEAR IS NOT NTM" in system, True)
    check("the Maverick worked example is delivered",
          "11.5x anticipated 2026 adjusted EBITDA" in system, True)
    check("the tax-adjusted variant is shown as a SECOND item",
          "10.5x after adjusting for the tax benefits" in system, True)
    check("and the example pins that EBITDA stays null",
          "ebitda_amount stays null" in system, True)
    # A floor, not a pin: this slice was introduced at 0.31 and must not silently
    # regress below it, but later prompt versions are expected and must not fail here.
    check("prompt version >= 0.31 (currently %s)" % hc._VERSION,
          tuple(int(x) for x in hc._VERSION.split(".")) >= (0, 31), True)


# ---------------------------------------------------------------------------
# 2. The Stage 4 parser is a vocabulary filter, not a classifier
# ---------------------------------------------------------------------------

def test_parser() -> None:
    fn = getattr(hc, "_reported_multiples_json", None)
    if fn is None:
        print(f"  {FAIL}  _reported_multiples_json is missing")
        _failures.append("_reported_multiples_json is missing")
        return

    print("\nThe Maverick item survives the parser intact:")
    out = json.loads(fn({"reported_multiples": [{
        "multiple_type": "EV_EBITDA", "multiple_value": 11.5,
        "period_basis": "ANNUAL", "period_end_date": "2026",
        "numerator_value_type": "implied_enterprise_value",
        "as_reported_text": "approximately 11.5x anticipated 2026 adjusted EBITDA",
    }]}, log, 1))
    check("one item kept", len(out), 1)
    check("value 11.5", out[0]["multiple_value"], 11.5)
    check("basis stays ANNUAL -- not converted to NTM", out[0]["period_basis"], "ANNUAL")
    check("period end stays the bare year", out[0]["period_end_date"], "2026")
    check("verbatim wording retained", out[0]["as_reported_text"],
          "approximately 11.5x anticipated 2026 adjusted EBITDA")

    print("\nBoth Maverick variants are kept, and neither is chosen over the other:")
    out = json.loads(fn({"reported_multiples": [
        {"multiple_type": "EV_EBITDA", "multiple_value": 11.5, "period_basis": "ANNUAL",
         "period_end_date": "2026", "numerator_value_type": "implied_enterprise_value",
         "as_reported_text": "approximately 11.5x anticipated 2026 adjusted EBITDA"},
        {"multiple_type": "EV_EBITDA", "multiple_value": 10.5, "period_basis": "ANNUAL",
         "period_end_date": "2026", "numerator_value_type": "implied_enterprise_value",
         "as_reported_text": "10.5x after adjusting for the tax benefits"},
    ]}, log, 1))
    check("two items kept", len(out), 2)
    check("values preserved in stated order",
          [i["multiple_value"] for i in out], [11.5, 10.5])
    # The only thing separating them. The canonical model carries no qualifier field --
    # recorded as a gap, deliberately not invented here -- so the verbatim wording is
    # what a reader has to go on.
    check("the wordings distinguish them",
          out[0]["as_reported_text"] != out[1]["as_reported_text"], True)

    print("\nVocabulary filter, not a semantic classifier:")
    out = json.loads(fn({"reported_multiples": [
        {"multiple_type": "EV_SALES", "multiple_value": 3.0},           # not in vocabulary
        {"multiple_type": "EV_REVENUE", "multiple_value": "3.4x"},      # not a number
        {"multiple_type": "EV_REVENUE", "multiple_value": -2},          # not positive
        {"multiple_type": None, "multiple_value": 5},                   # no type
        {"multiple_type": "EV_EBITDA", "multiple_value": 9.0,
         "numerator_value_type": "implied_enterprise_value"},           # good
    ]}, log, 1))
    check("only the well-formed item survives", len(out), 1)
    check("and it is the EV_EBITDA one", out[0]["multiple_type"], "EV_EBITDA")
    # It never repairs a near-miss into a valid value: EV_SALES is dropped, not
    # rewritten to EV_REVENUE. That mapping belongs to the model under the contract.
    check("a near-miss type is dropped, never translated",
          [i["multiple_type"] for i in out], ["EV_EBITDA"])

    print("\nAn unusable period basis costs the basis, not the multiple:")
    out = json.loads(fn({"reported_multiples": [{
        "multiple_type": "EV_REVENUE", "multiple_value": 4.0, "period_basis": "FY2026",
        "numerator_value_type": "implied_enterprise_value"}]}, log, 1))
    check("the multiple is kept", len(out), 1)
    check("with the basis nulled", out[0]["period_basis"], None)

    print("\nThe numerator family follows from the type, and is corrected if it does not:")
    out = json.loads(fn({"reported_multiples": [
        {"multiple_type": "PE", "multiple_value": 20.0,
         "numerator_value_type": "implied_enterprise_value"},   # wrong family
        {"multiple_type": "EV_FCF", "multiple_value": 15.0},    # absent
    ]}, log, 1))
    check("PE is an equity multiple", out[0]["numerator_value_type"], "implied_equity_value")
    check("EV_FCF is an EV multiple", out[1]["numerator_value_type"], "implied_enterprise_value")

    print("\nNo multiples stated is different from never having asked:")
    check("an empty array serializes to '[]'", fn({"reported_multiples": []}, log, 1), "[]")
    check("a missing key serializes to None", fn({}, log, 1), None)

    print("\nThe parser computes nothing:")
    import inspect
    body = inspect.getsource(fn)
    check("no division anywhere in the parser", "/" in body.split('"""')[-1], False)


# ---------------------------------------------------------------------------
# 3. Stage 9 writes rows -- the regression anchor
# ---------------------------------------------------------------------------

def _fresh_db():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.db")
    _db.init_db(path)
    return _db.get_connection(path)


def _schema_ready(conn) -> bool:
    """Both migrations present?

    Guarded, and this guard has been earned repeatedly: seeding a column or table that
    does not exist yet raises, which ABORTS the run and leaves every check below it
    unproven -- a pre-change run then reports far fewer failures than are really there,
    which reads like partial success. Returning False lets the caller record one honest
    failure and move on.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='transaction_multiple'"
    ).fetchone()
    return "reported_multiples" in cols and table is not None


def _seed_source(conn, *, eid, srid, multiples, tier="T2", confidence="HIGH",
                 txn_id="tc_mav", text="nVent to acquire Maverick Power."):
    conn.execute(
        "INSERT INTO source_raw (source_raw_id, source_type, source_tier, url, raw_html,"
        " clean_text, source_status, fetched_at, published_date) VALUES (?,?,?,?,?,?,?,?,?)",
        (srid, "PR_NEWSWIRE", tier, f"https://example.test/{srid}", "<html/>", text,
         "FETCHED", "2026-08-27T00:00:00", "2026-08-01"))
    conn.execute(
        "INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
        " transaction_cluster_id, reported_multiples, hc_prompt_version, model_confidence)"
        " VALUES (?,?,'CLUSTERED',?,?,?,?)",
        (eid, srid, txn_id, json.dumps(multiples),
         "high_confidence_extraction:0.31", confidence))
    conn.commit()


def _write_obs(conn, eid):
    """The production observation writer, with the production include_hc flag."""
    return write_staging_observations_for_extraction(
        conn, eid, observation_source_stage="HC_EXTRACT", include_hc=True)


def _obs_rows(conn, field_name=None, like=None):
    sql = "SELECT * FROM transaction_field_observation WHERE is_current = 1"
    args: list = []
    if field_name:
        sql += " AND field_name = ?"; args.append(field_name)
    if like:
        sql += " AND field_name LIKE ?"; args.append(like)
    return conn.execute(sql + " ORDER BY observation_id", args).fetchall()


def _rows(conn, txn_id="tc_mav"):
    return conn.execute(
        "SELECT * FROM transaction_multiple WHERE transaction_id = ? ORDER BY multiple_id",
        (txn_id,)).fetchall()


MAV_11_5 = {"multiple_type": "EV_EBITDA", "multiple_value": 11.5, "period_basis": "ANNUAL",
            "period_end_date": "2026", "numerator_value_type": "implied_enterprise_value",
            "as_reported_text": "approximately 11.5x anticipated 2026 adjusted EBITDA"}
MAV_10_5 = {"multiple_type": "EV_EBITDA", "multiple_value": 10.5, "period_basis": "ANNUAL",
            "period_end_date": "2026", "numerator_value_type": "implied_enterprise_value",
            "as_reported_text": "10.5x after adjusting for the tax benefits"}


# ---------------------------------------------------------------------------
# 3. Observations: every stated multiple is preserved
# ---------------------------------------------------------------------------

def test_observations() -> None:
    key_fn = getattr(ow, "reported_multiple_field_name", None)
    if key_fn is None:
        print(f"  {FAIL}  reported_multiple_field_name is missing")
        _failures.append("reported_multiple_field_name is missing")
        return

    print("\nThe canonical fact key is composed from Product's own dimensions:")
    check("Maverick's headline multiple composes a key", key_fn(MAV_11_5),
          "multiple.EV_EBITDA.ANNUAL.2026")
    # The whole point. No adjustment dimension exists, so the variants collide here --
    # deliberately, so they arrive as one conflicted fact rather than two canonical rows.
    check("the tax-adjusted variant composes THE SAME key",
          key_fn(MAV_10_5), key_fn(MAV_11_5))
    check("a different period is a different key",
          key_fn(dict(MAV_11_5, period_end_date="2025")) != key_fn(MAV_11_5), True)
    check("a different basis is a different key",
          key_fn(dict(MAV_11_5, period_basis="LTM")) != key_fn(MAV_11_5), True)
    check("an absent basis does not collide with a stated one",
          key_fn(dict(MAV_11_5, period_basis=None)) != key_fn(MAV_11_5), True)
    check("no type means no key", key_fn({"multiple_value": 5}), None)

    print("\nBoth Maverick observations are preserved, independently:")
    conn = _fresh_db()
    if not _schema_ready(conn):
        print(f"  {FAIL}  migrations 012/013 are not applied")
        _failures.append("migrations 012/013 are not applied")
        conn.close(); return
    _seed_source(conn, eid=1, srid=1, multiples=[MAV_11_5, MAV_10_5])
    _write_obs(conn, 1)
    conn.commit()
    preserved = _obs_rows(conn, field_name="reported_multiple")
    check("two preservation rows", len(preserved), 2)
    payloads = [json.loads(r["field_value"]) for r in preserved]
    check("both values survive", sorted(p["multiple_value"] for p in payloads), [10.5, 11.5])
    check("evidence survives on both",
          sorted(p["as_reported_text"] for p in payloads),
          sorted([MAV_11_5["as_reported_text"], MAV_10_5["as_reported_text"]]))
    # Per-fact provenance (E4 rule 6): two facts from one article must stay
    # distinguishable, or INSERT OR IGNORE would collapse them into one.
    check("distinct fact keys keep them apart",
          len({r["observation_fact_key"] for r in preserved}), 2)
    check("source provenance carried",
          {(r["staging_extraction_id"], r["source_raw_id"]) for r in preserved}, {(1, 1)})

    print("\n  ...and the same two are offered for reconciliation at one key:")
    recon = _obs_rows(conn, like="multiple.%")
    check("two reconciliation rows", len(recon), 2)
    check("both at the one canonical key",
          {r["field_name"] for r in recon}, {"multiple.EV_EBITDA.ANNUAL.2026"})
    check("numeric values carried", sorted(r["field_value_numeric"] for r in recon),
          [10.5, 11.5])
    conn.close()


# ---------------------------------------------------------------------------
# 4. Reconciliation: the EXISTING generic path, unmodified
# ---------------------------------------------------------------------------

def test_reconciliation() -> None:
    print("\nThe generic resolver is what runs — Maverick reaches the conflict path:")
    conn = _fresh_db()
    if not _schema_ready(conn):
        print(f"  {FAIL}  migrations 012/013 are not applied")
        _failures.append("migrations 012/013 are not applied (reconciliation)")
        conn.close(); return
    _seed_source(conn, eid=1, srid=1, multiples=[MAV_11_5, MAV_10_5])
    _write_obs(conn, 1)
    conn.commit()
    bundles = agg._load_observation_input(conn)
    bundle = bundles.get("tc_mav")
    check("the cluster loaded", bundle is not None, True)
    if bundle is None:
        conn.close(); return
    key = "multiple.EV_EBITDA.ANNUAL.2026"
    obs = bundle["field_observations"].get(key, [])
    check("both observations reached the resolver at one key", len(obs), 2)
    check("the preservation rows did NOT — they are a record, not candidates",
          "reported_multiple" in bundle["field_observations"], False)

    # _pick_value verbatim. Same source => same tier and same confidence, so neither the
    # tier ordering nor the confidence tiebreak can separate them.
    chosen, needs_llm, conflict_obs = agg._pick_value(key, "number", obs)
    check("no value is chosen", chosen, None)
    check("it goes to conflict resolution", needs_llm, True)
    check("with both observations attached", len(conflict_obs), 2)
    check("which are the two Maverick values",
          sorted(o["value"] for o in conflict_obs), [10.5, 11.5])
    # The excerpts are identical because it is one source. The reconciler genuinely
    # cannot tell them apart -- and should not, since Product carries no dimension that
    # distinguishes them.
    check("their excerpts are indistinguishable",
          len({o["source_text_excerpt"] for o in conflict_obs}), 1)

    print("\nAgreement still resolves without a conflict, at the same key:")
    conn2 = _fresh_db()
    _seed_source(conn2, eid=1, srid=1, multiples=[MAV_11_5])
    _seed_source(conn2, eid=2, srid=2, multiples=[MAV_11_5], text="Second outlet reports.")
    _write_obs(conn2, 1); _write_obs(conn2, 2)
    conn2.commit()
    b2 = agg._load_observation_input(conn2)["tc_mav"]
    chosen, needs_llm, _ = agg._pick_value(key, "number", b2["field_observations"][key])
    check("two sources agreeing resolve cleanly", (chosen, needs_llm), (11.5, False))
    check("and both are still preserved separately",
          len(_obs_rows(conn2, field_name="reported_multiple")), 2)
    conn2.close()

    print("\nA higher tier still wins, by the ordinary rule:")
    conn3 = _fresh_db()
    _seed_source(conn3, eid=1, srid=1, multiples=[MAV_10_5])
    _seed_source(conn3, eid=2, srid=2, multiples=[MAV_11_5], tier="T1", text="Filing.")
    _write_obs(conn3, 1); _write_obs(conn3, 2)
    conn3.commit()
    b3 = agg._load_observation_input(conn3)["tc_mav"]
    chosen, needs_llm, _ = agg._pick_value(key, "number", b3["field_observations"][key])
    check("T1 beats T2 with no conflict", (chosen, needs_llm), (11.5, False))
    conn3.close()

    print("\nNo multiples-specific reconciliation was introduced:")
    src = (ROOT / "stages" / "aggregate.py").read_text(encoding="utf-8")
    for fname in ("_pick_value", "_call_agg_prompt", "_log_conflict"):
        body = src.split(f"def {fname}(", 1)[1].split("\ndef ", 1)[0]
        check(f"{fname} knows nothing about multiples",
              "multiple" in body.lower(), False)
    conn.close()


# ---------------------------------------------------------------------------
# 5. Canonical: resolved facts only
# ---------------------------------------------------------------------------

def test_canonical() -> None:
    fn = getattr(agg, "_write_as_reported_multiples", None)
    if fn is None:
        print(f"  {FAIL}  _write_as_reported_multiples is missing")
        _failures.append("_write_as_reported_multiples is missing")
        return
    key = "multiple.EV_EBITDA.ANNUAL.2026"

    print("\nREGRESSION ANCHOR — a resolved Maverick multiple becomes canonical:")
    conn = _fresh_db()
    if not _schema_ready(conn):
        print(f"  {FAIL}  migrations 012/013 are not applied")
        _failures.append("migrations 012/013 are not applied (canonical)")
        conn.close(); return
    _seed_source(conn, eid=1, srid=1, multiples=[MAV_11_5])
    _write_obs(conn, 1)
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES ('tc_mav',1)")
    conn.commit()
    obs = agg._load_observation_input(conn)["tc_mav"]["field_observations"]
    fn(conn, "tc_mav", {key: 11.5}, set(), obs, log)
    conn.commit()
    rows = _rows(conn)
    check("one canonical row", len(rows), 1)
    r = rows[0]
    check("11.5x", r["multiple_value"], 11.5)
    check("type recovered from the key", r["multiple_type"], "EV_EBITDA")
    check("basis ANNUAL — not converted to NTM", r["period_basis"], "ANNUAL")
    check("period end is the bare year", r["period_end_date"], "2026")
    # Lowercase since R3.4: one precision vocabulary across both normalized tables,
    # taken from the canonical metric row (exact | month | quarter | year). Spelling,
    # not a change of meaning -- the period end is still the bare year the source gave.
    check("precision year", r["period_end_date_precision"], "year")
    check("source_flag as_reported", r["source_flag"], "as_reported")
    check("numerator family named", r["numerator_value_type"], "implied_enterprise_value")
    check("quality NULL — nothing was calculated", r["quality"], None)
    check("denominator_financial_id NULL", r["denominator_financial_id"], None)
    check("verbatim wording recovered from the preserved observation",
          r["multiple_as_reported"], MAV_11_5["as_reported_text"])
    check("provenance carried", (r["staging_extraction_id"], r["source_raw_id"]), (1, 1))

    print("\n  ...and NOTHING was back-solved:")
    tr = conn.execute("SELECT target_ebitda, implied_enterprise_value FROM transaction_record"
                      " WHERE transaction_id='tc_mav'").fetchone()
    check("target_ebitda still NULL", tr["target_ebitda"], None)
    check("implied_enterprise_value still NULL", tr["implied_enterprise_value"], None)
    conn.close()

    print("\nTHE MAVERICK CASE — 11.5x and 10.5x produce NO canonical multiple:")
    conn = _fresh_db()
    _seed_source(conn, eid=1, srid=1, multiples=[MAV_11_5, MAV_10_5])
    _write_obs(conn, 1)
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES ('tc_mav',1)")
    conn.commit()
    obs = agg._load_observation_input(conn)["tc_mav"]["field_observations"]
    # flagged_keys is what the resolution loop records when the conflict is flagged for
    # review; a fallback value is still in field_values and must NOT be written.
    fn(conn, "tc_mav", {key: 11.5}, {key}, obs, log)
    conn.commit()
    check("no canonical row is asserted", len(_rows(conn)), 0)
    check("both observations still stand",
          len(_obs_rows(conn, field_name="reported_multiple")), 2)
    check("and both are still at the reconciliation key",
          len(_obs_rows(conn, like="multiple.%")), 2)
    # Absence of a row IS the unresolved state -- Product declined a state column.
    cols = {d[0] for d in conn.execute("SELECT * FROM transaction_multiple LIMIT 0").description}
    check("no reconciliation_state column exists", "reconciliation_state" in cols, False)
    check("no precedence column either",
          bool(cols & {"is_preferred", "precedence", "rank", "display_order"}), False)
    conn.close()

    print("\nCoexistence — a calculated row is neither suppressed nor removed:")
    conn = _fresh_db()
    _seed_source(conn, eid=1, srid=1, multiples=[MAV_11_5])
    _write_obs(conn, 1)
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES ('tc_mav',1)")
    conn.execute("INSERT INTO transaction_multiple (transaction_id, multiple_type,"
                 " multiple_value, period_basis, numerator_value_type, source_flag, quality)"
                 " VALUES ('tc_mav','EV_EBITDA',12.0,'LTM','implied_enterprise_value',"
                 "'calculated','CALCULATED')")
    conn.commit()
    obs = agg._load_observation_input(conn)["tc_mav"]["field_observations"]
    fn(conn, "tc_mav", {key: 11.5}, set(), obs, log)
    fn(conn, "tc_mav", {key: 11.5}, set(), obs, log)      # re-aggregation
    conn.commit()
    rows = _rows(conn)
    check("still two rows after re-aggregation, not three", len(rows), 2)
    check("one of each flag", sorted(r["source_flag"] for r in rows),
          ["as_reported", "calculated"])
    check("the calculated row survived the scoped delete",
          [r["multiple_value"] for r in rows if r["source_flag"] == "calculated"], [12.0])
    conn.close()

    print("\nA source stating no multiple produces nothing:")
    conn = _fresh_db()
    _seed_source(conn, eid=1, srid=1, multiples=[])
    _write_obs(conn, 1)
    conn.execute("INSERT INTO transaction_record (transaction_id, is_current) VALUES ('tc_mav',1)")
    conn.commit()
    fn(conn, "tc_mav", {}, set(), {}, log)
    check("no canonical rows", len(_rows(conn)), 0)
    check("no observations either", len(_obs_rows(conn, field_name="reported_multiple")), 0)
    conn.close()


# ---------------------------------------------------------------------------
# 4. Nothing else moved
# ---------------------------------------------------------------------------

def test_unchanged() -> None:
    print("\nThe calculated path is untouched:")
    # The flat columns still behave exactly as before -- this change adds a row, it does
    # not redirect the calculator.
    r = agg._compute_multiples(
        implied_enterprise_value=1_200_000_000.0, value_currency="USD",
        target_revenue=None, target_revenue_period_type=None,
        target_ebitda=100_000_000.0, target_ebitda_period_type="LTM",
        financials_currency="USD", log=log, cluster_id="c1")
    check("EV/EBITDA LTM still computes", r["ev_to_ebitda_ltm"], 12.0)
    check("quality still CALCULATED", r["multiple_quality"], "CALCULATED")

    # And the case that motivated all of this still returns NOT_CALCULABLE -- the
    # as-reported row is an ADDITION, not a replacement for the calculator's verdict.
    r = agg._compute_multiples(
        implied_enterprise_value=None, value_currency="USD",
        target_revenue=None, target_revenue_period_type=None,
        target_ebitda=None, target_ebitda_period_type=None,
        financials_currency="USD", log=log, cluster_id="c2")
    check("no EV still means NOT_CALCULABLE", r["multiple_quality"], "NOT_CALCULABLE")
    check("and no slot is filled", r["ev_to_ebitda_ltm"], None)

    check("aggregate version >= 0.10 (currently %s)" % agg._VERSION,
          tuple(int(x) for x in agg._VERSION.split(".")) >= (0, 10), True)

    print("\nThe canonical column set did not move:")
    owned = getattr(agg, "_STAGE9_OWNED_COLUMNS", None)
    if owned is not None:
        check("Stage 9 still owns 120 columns", len(owned), 120)
        check("no multiple row field leaked into the column list",
              any(c in owned for c in ("source_flag", "multiple_as_reported",
                                       "denominator_financial_id")), False)


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_parser()
    test_observations()
    test_reconciliation()
    test_canonical()
    test_unchanged()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — preserved, reconciled, and canonical only when resolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
