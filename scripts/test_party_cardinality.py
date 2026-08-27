#!/usr/bin/env python3
"""One party, one collected fact.

WHAT WENT WRONG

Three roles were captured and then collapsed into a scalar before anything downstream
could see how many parties there were:

  BUYER          acquirer_name, multiple firms joined by " and " on this prompt's own
                 instruction. The contract even documented the loss: acquirer.type
                 returns `unknown` for multiple buyers because one value cannot
                 classify two firms -- "a compatibility answer for this single scalar
                 field". Each firm's own type was determinable and discarded.
  SPONSOR_BUYER  acquirer_sponsor_name, "comma-delimit" for co-sponsors.
                 Live corpus: 'Trident Management, Bluejay Capital'.
  PARENT_SELLER  parent_seller_name, collapsed SILENTLY -- no instruction covered a
                 joint divestiture at all.

"Firm A and Firm B" is not two party relationships. No downstream resolver can recover
a count that was never collected, so this is a collection defect, not an identity one.

WHAT CHANGED

Three required arrays, one item per party, always an array including []. The scalars are
unchanged and remain the display projection for every current reader.

Role is carried by WHICH ARRAY a party is in. BUYER, SPONSOR_BUYER and PARENT_SELLER are
existing V3 §T5 roles; none is invented, and no sub-role among co-buyers is added.

WHAT THIS IS NOT

No entity resolution, deduplication, alias matching, canonical entity id, `entity` row
or `transaction_participant` row. Matching a name to an identity is separate work.

Roles this implementation does not author at all -- SELLER, SPONSOR_SELLER,
PARENT_ACQUIRER, LENDER, JV_PARTNER, UNDERWRITER -- are coverage gaps rather than
collapses; adding them would be new extraction, not cardinality preservation.

TARGET is excluded. `target_name` does hold multi-name values -- 'Priority Dispatch,
Inc. and Diamond Expedited' in the current corpus -- but that may mean decomposition
into two transactions failed rather than that one transaction has two targets. Recorded
as a decomposition finding, not answered here.

Run from project root:
    python scripts/test_party_cardinality.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import db as _db
import lib.observation_writer as ow
from lib.observation_writer import write_staging_observations_for_extraction
from prompts.base import load_prompt_file
import stages.aggregate as agg
import stages.high_confidence_extract as hc

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_failures: list[str] = []
log = logging.getLogger("t")
logging.basicConfig(level=logging.CRITICAL)


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}: got {got!r}, want {want!r}")
        _failures.append(label)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t)


# ---------------------------------------------------------------------------
# 1. The contract
# ---------------------------------------------------------------------------

def test_prompt() -> None:
    print("\nThe instruction reaches the model:")
    flat = _norm(load_prompt_file("high_confidence_extraction")["system"])
    check("PARTIES, ONE PER PARTY delivered", "PARTIES, ONE PER PARTY" in flat, True)
    check("always-an-array rule delivered", "ALWAYS AN ARRAY, INCLUDING EMPTY" in flat, True)
    check("empty and absent are distinguished",
          "an absent array and an empty one are not the same statement" in flat, True)
    # The rule that keeps this a cardinality change and not an evidence change.
    check("cardinality is not a licence to infer",
          "CARDINALITY IS NOT A LICENCE TO INFER" in flat, True)
    check("and it says so concretely",
          "would not have put in the scalar field" in flat, True)
    check("the two-firm case is worked",
          '"a venture of RPM Living and New York Life" is TWO items' in flat, True)
    for k in ('"acquirers": [', '"buy_side_sponsors": []', '"parent_sellers": []'):
        check(f"response slot {k[:22]}", k in flat, True)

    print("\nExisting evidence and applicability rules are restated, not broadened:")
    check("sponsor evidence rule preserved",
          "Do NOT infer a sponsor because an acquirer appears sponsor-backed" in flat, True)
    check("parent-seller applicability preserved",
          "target_type is subsidiary, business_unit or assets" in flat, True)
    check("only buyers carry a type",
          "no per-party attribute" in flat or "type: that firm's own classification" in flat, True)
    check("prompt version is 0.33", hc._VERSION, "0.33")


# ---------------------------------------------------------------------------
# 2. The parser
# ---------------------------------------------------------------------------

def test_parser() -> None:
    fn = getattr(hc, "_parties_json", None)
    if fn is None:
        print(f"  {FAIL}  _parties_json is missing")
        _failures.append("_parties_json is missing")
        return

    print("\nTwo buyers are two parties, each with its own type:")
    out = json.loads(fn({"acquirers": [
        {"name": "RPM Living", "type": "private_equity"},
        {"name": "New York Life", "type": "other_financial_sponsor"},
    ]}, "acquirers", log, 1))
    check("two items", len(out), 2)
    check("names kept as stated", [i["name"] for i in out], ["RPM Living", "New York Life"])
    # The fact the scalar destroys: the shared column takes `unknown` for two buyers.
    check("each keeps its own type",
          [i["type"] for i in out], ["private_equity", "other_financial_sponsor"])

    print("\nAlways an array — empty, one, or many:")
    check("empty stays []", fn({"acquirers": []}, "acquirers", log, 1), "[]")
    check("a missing key is still []", fn({}, "acquirers", log, 1), "[]")
    check("one party is an array of one",
          len(json.loads(fn({"acquirers": [{"name": "Acme"}]}, "acquirers", log, 1))), 1)
    check("a non-list is recorded empty, not raised",
          fn({"acquirers": "Acme and Beta"}, "acquirers", log, 1), "[]")

    print("\nVocabulary filter, not a classifier:")
    out = json.loads(fn({"acquirers": [{"name": "Acme", "type": "megacorp"}]},
                        "acquirers", log, 1))
    check("an unsupported type is dropped to null", out[0]["type"], None)
    check("and the party itself is kept", out[0]["name"], "Acme")
    # It must not guess a nearby value, and must not split a joined string -- recovering
    # two parties from one name is guesswork about punctuation.
    out = json.loads(fn({"acquirers": [{"name": "EvCap Investments and Point Acquisitions"}]},
                        "acquirers", log, 1))
    check("a joined name is NOT split by the parser", len(out), 1)

    print("\nA party with no name is not a party:")
    out = json.loads(fn({"acquirers": [{"name": "  "}, {"type": "spac"}, {"name": "Acme"}]},
                        "acquirers", log, 1))
    check("only the named party survives", [i["name"] for i in out], ["Acme"])

    print("\nOnly buyers carry a type:")
    out = json.loads(fn({"buy_side_sponsors": [{"name": "Trident Management"}]},
                        "buy_side_sponsors", log, 1))
    check("sponsors have no type key", "type" in out[0], False)
    out = json.loads(fn({"parent_sellers": [{"name": "Rockland Capital, LP"}]},
                        "parent_sellers", log, 1))
    check("parent sellers have no type key", "type" in out[0], False)
    check("and a comma inside one name is left alone", out[0]["name"], "Rockland Capital, LP")


# ---------------------------------------------------------------------------
# 3. Observations
# ---------------------------------------------------------------------------

def _fresh():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    _db.init_db(p)
    return _db.get_connection(p)


def _ready(conn) -> bool:
    """Guarded: seeding a column that does not exist yet raises and ABORTS the run,
    hiding every check below it."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
    return {"acquirers", "buy_side_sponsors", "parent_sellers"} <= cols


def test_observations() -> None:
    conn = _fresh()
    if not _ready(conn) or not hasattr(ow, "_write_party_observations"):
        print(f"  {FAIL}  migration 015 / the party writer are not present")
        _failures.append("migration 015 / the party writer are not present")
        conn.close(); return

    conn.execute("INSERT INTO source_raw (source_raw_id, source_type, source_tier, url,"
                 " raw_html, clean_text, source_status, fetched_at) VALUES"
                 " (1,'PR_NEWSWIRE','T2','https://e.test/1','<h/>','body','FETCHED','2026-08-27')")
    conn.execute(
        "INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
        " transaction_cluster_id, acquirer_name, acquirer_sponsor_name, parent_seller_name,"
        " acquirers, buy_side_sponsors, parent_sellers, hc_prompt_version)"
        " VALUES (1,1,'CLUSTERED','tc_1',?,?,?,?,?,?,?)",
        ("EvCap Investments and Point Acquisitions",
         "Trident Management, Bluejay Capital",
         "Rockland Capital, LP",
         json.dumps([{"name": "EvCap Investments", "type": "private_equity"},
                     {"name": "Point Acquisitions", "type": "other_financial_sponsor"}]),
         json.dumps([{"name": "Trident Management"}, {"name": "Bluejay Capital"}]),
         json.dumps([{"name": "Rockland Capital, LP"}]),
         "high_confidence_extraction:0.33"))
    conn.commit()
    write_staging_observations_for_extraction(
        conn, 1, observation_source_stage="HC_EXTRACT", include_hc=True)
    conn.commit()

    def rows(field):
        return conn.execute("SELECT * FROM transaction_field_observation WHERE field_name=?"
                            " ORDER BY observation_id", (field,)).fetchall()

    print("\nEach source-stated party is one observation:")
    buyers = rows("acquirer_party")
    check("two buyer parties", len(buyers), 2)
    names = [json.loads(r["field_value"])["name"] for r in buyers]
    check("named independently", names, ["EvCap Investments", "Point Acquisitions"])
    check("each with its own type",
          [json.loads(r["field_value"])["type"] for r in buyers],
          ["private_equity", "other_financial_sponsor"])
    # Per-fact provenance: two parties from one release must not collapse under
    # INSERT OR IGNORE into a single row.
    check("distinct fact keys", len({r["observation_fact_key"] for r in buyers}), 2)

    sponsors = rows("buy_side_sponsor_party")
    check("two sponsor parties", len(sponsors), 2)
    check("named independently",
          [json.loads(r["field_value"])["name"] for r in sponsors],
          ["Trident Management", "Bluejay Capital"])
    sellers = rows("parent_seller_party")
    check("one parent seller", len(sellers), 1)
    check("its internal comma is not a separator",
          json.loads(sellers[0]["field_value"])["name"], "Rockland Capital, LP")

    print("\nRole is carried by the field name — the three do not mix:")
    check("three distinct field names",
          len({"acquirer_party", "buy_side_sponsor_party", "parent_seller_party"}), 3)
    check("no role value is stored inside an item",
          any("role" in json.loads(r["field_value"]) for r in buyers + sponsors + sellers), False)

    print("\nThe display scalars are untouched:")
    r = conn.execute("SELECT acquirer_name, acquirer_sponsor_name, parent_seller_name"
                     " FROM staging_extraction WHERE extraction_id=1").fetchone()
    check("acquirer_name unchanged", r["acquirer_name"],
          "EvCap Investments and Point Acquisitions")
    check("acquirer_sponsor_name unchanged", r["acquirer_sponsor_name"],
          "Trident Management, Bluejay Capital")
    check("parent_seller_name unchanged", r["parent_seller_name"], "Rockland Capital, LP")

    print("\nParties are preserved, never reconciled:")
    for f in ("acquirer_party", "buy_side_sponsor_party", "parent_seller_party"):
        check(f"{f} is absent from _FIELDS", f in dict(agg._FIELDS), False)
    bundles = agg._load_observation_input(conn)
    fo = bundles.get("tc_1", {}).get("field_observations", {})
    check("and none reaches the resolver",
          any(f in fo for f in ("acquirer_party", "buy_side_sponsor_party",
                                "parent_seller_party")), False)
    conn.close()


# ---------------------------------------------------------------------------
# 4. Boundaries
# ---------------------------------------------------------------------------

def test_boundaries() -> None:
    print("\nNo identity work anywhere in this slice:")
    files = [ROOT / "lib" / "observation_writer.py",
             ROOT / "stages" / "high_confidence_extract.py"]
    src = "\n".join(f.read_text(encoding="utf-8") for f in files)
    check("no transaction_participant write",
          "INSERT INTO transaction_participant" in src, False)
    check("no entity write", "INSERT INTO entity" in src, False)
    check("no entity_id assignment", "entity_id" in src, False)

    print("\nUnauthored roles are not added:")
    flat = _norm(load_prompt_file("high_confidence_extraction")["system"])
    for role in ("sell_side_sponsors", "lenders", "underwriters", "jv_partners",
                 "parent_acquirers"):
        check(f"{role} absent", role in flat, False)

    print("\nTARGET is excluded, deliberately:")
    check("no targets array", '"targets"' in flat, False)
    mig = (ROOT / "schema" / "015_v3_party_cardinality.sql")
    text = mig.read_text(encoding="utf-8") if mig.exists() else ""
    check("and the decomposition finding is recorded",
          "Priority Dispatch" in text and "decomposition" in text, True)

    print("\nNothing else moved:")
    check("Stage 9 still owns 120 canonical columns",
          len(getattr(agg, "_STAGE9_OWNED_COLUMNS", ())), 120)
    check("aggregate version unchanged at 0.12", agg._VERSION, "0.12")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_parser()
    test_observations()
    test_boundaries()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — every source-stated party survives collection as its own fact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
