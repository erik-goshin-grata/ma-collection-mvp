#!/usr/bin/env python3
"""Providing financing and advising on it are two participations.

WHAT WENT WRONG

The low-confidence contract has always drawn the boundary:

    "A FINANCING PROVIDER is NOT an advisor specialty. Providing capital and
     transaction are different participations; a firm doing both appears twice."

It had nowhere to put the provider. One was correctly kept out of the
advisor list and then dropped -- the rule was honoured and the fact was lost.

The advisor half was no better. A firm that arranged the financing had no specialty to
land in: the vocabulary's nearest value, `financial_advisory`, means M&A advisory, so
"acted as financing advisor" either collapsed into it or went null. Either way the
distinction between advising on capital and advising on the deal disappeared.

WHAT CHANGED

  financing_providers a new LC array, one item per party the source states PROVIDES,
                      COMMITS or LEADS the financing. {name} only. The role is
                      deliberately uncommitted about instrument and capacity -- deciding
                      whether a firm is technically a lender, an underwriter or a
                      commitment party is a judgement this collection does not make.
  financing_advisory  a new advisor specialty for the firm that advises on, structures,
                      arranges or places the financing. Its deferred candidate name was
                      `capital_markets`, deferred for want of extraction evidence rather
                      than on the semantics.

ARRANGING IS NOT PROVIDING, and that is the whole distinction. "Arranged the financing",
"placed the notes", "lead arranger" describe advice ABOUT capital, not capital.

The two are independent in both directions: arranging never implies lending, lending
never implies advising, and a firm the source establishes in both is recorded once in
each. That works because the advisor table carries no uniqueness constraint on the
advisor name -- the same property that already lets one firm advise two parties.

WHAT THIS IS NOT

No entity resolution, deduplication, alias matching, `entity` or `transaction_participant`
write. SELLER, JV_PARTNER and UNDERWRITER stay unauthored.

Run from project root:
    python scripts/test_financing_participation.py
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
import stages.low_confidence_extract as lc

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
    flat = _norm(load_prompt_file("low_confidence_extraction")["system"])
    check("FINANCING PROVIDERS block delivered", "FINANCING PROVIDERS:" in flat, True)
    check("the old LENDERS block is gone", "WHAT MAKES A LENDER" in flat, False)
    check("financing_advisory is in the delivered specialty list",
          "financing_advisory —" in flat, True)
    check("financing_providers response slot", '"financing_providers": []' in flat, True)

    print("\nThe boundary is stated in both directions:")
    check("the original rule survives",
          "Providing capital and advising on a transaction are different participations"
          in flat, True)
    check("arranging is not providing", "ARRANGING IS NOT PROVIDING" in flat, True)
    check("an arranger is an advisor, not a provider",
          "A firm that ARRANGES financing is a financing advisor, not a financing provider"
          in flat, True)
    # The role is about the participation, not the instrument or the capacity.
    check("instrument and capacity are not classified",
          "Do not classify the instrument or the capacity" in flat, True)
    check("provides, commits or leads", "PROVIDES, COMMITS or LEADS the financing" in flat, True)
    # Reachable only because the name widened: leading provision vs leading arrangement.
    check("leading is not arranging", "LEADING IS NOT ARRANGING" in flat, True)
    check("and a lead arranger stays an advisor",
          "stays an advisor however senior it sounds" in flat or
          "it is an advisor however senior the arranging role sounds" in flat, True)
    # The inference this must not make.
    check("no reasoning that an arranger must also be providing",
          "do not reason that a firm arranging financing must also be providing some of it"
          in flat, True)
    check("and providing does not make a firm an advisor",
          "providing financing does not make a firm an advisor" in flat, True)
    check("a firm may be both when the source establishes both",
          "A FIRM MAY BE BOTH" in flat, True)
    check("an acquirer does not finance itself from outside",
          "not financing itself from outside" in flat, True)

    print("\nVocabulary:")
    check("financing_advisory is valid",
          "financing_advisory" in lc._VALID_ADVISOR_SPECIALTIES, True)
    check("capital_markets is NOT used",
          "capital_markets" in lc._VALID_ADVISOR_SPECIALTIES, False)
    check("ordinary M&A advisory is untouched",
          "financial_advisory" in lc._VALID_ADVISOR_SPECIALTIES, True)
    check("LC version is 0.13", lc._VERSION, "0.13")


# ---------------------------------------------------------------------------
# 2. The financing-provider parser
# ---------------------------------------------------------------------------

def test_parser() -> None:
    fn = getattr(lc, "_financing_providers_json", None)
    if fn is None:
        print(f"  {FAIL}  _financing_providers_json is missing")
        _failures.append("_financing_providers_json is missing")
        return

    print("\nOne provider, and many:")
    check("one provider", json.loads(fn({"financing_providers": [{"name": "Blackstone Credit"}]}, log, 1)),
          [{"name": "Blackstone Credit"}])
    out = json.loads(fn({"financing_providers": [{"name": "Blackstone Credit"},
                                                 {"name": "Bank of America"}]}, log, 1))
    check("two providers are two facts", len(out), 2)
    check("named independently", [i["name"] for i in out],
          ["Blackstone Credit", "Bank of America"])

    print("\nNo invented subtype:")
    out = json.loads(fn({"financing_providers": [{"name": "X", "lender_role": "AGENT",
                                                  "role": "lead"}]}, log, 1))
    check("only the name is kept", out, [{"name": "X"}])

    print("\nAlways an array:")
    check("empty stays []", fn({"financing_providers": []}, log, 1), "[]")
    check("a missing key is still []", fn({}, log, 1), "[]")
    check("a non-list is recorded empty, not raised",
          fn({"financing_providers": "Bank of America"}, log, 1), "[]")
    check("a nameless entry is not a provider",
          json.loads(fn({"financing_providers": [{"name": " "}, {"name": "Y"}]}, log, 1)),
          [{"name": "Y"}])

    print("\nThe parser infers nothing from the advisor list:")
    import inspect
    body = inspect.getsource(fn).split('"""', 2)[-1]
    check("it never reads advisors", "advisor" in body.lower(), False)


# ---------------------------------------------------------------------------
# 3. The worked examples
# ---------------------------------------------------------------------------

def _fresh():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    _db.init_db(p)
    return _db.get_connection(p)


def _ready(conn) -> bool:
    """Guarded: seeding a column that does not exist yet raises and ABORTS the run."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(staging_extraction)")}
    return ("financing_providers" in cols
            and hasattr(ow, "_write_financing_provider_observations"))


def _seed(conn, *, providers, advisors, eid=1, txn="tc_1"):
    conn.execute("INSERT OR IGNORE INTO source_raw (source_raw_id, source_type, source_tier,"
                 " url, raw_html, clean_text, source_status, fetched_at) VALUES"
                 " (1,'PR_NEWSWIRE','T2','https://e.test/1','<h/>','b','FETCHED','2026-08-27')")
    conn.execute("INSERT INTO staging_extraction (extraction_id, source_raw_id, status,"
                 " transaction_cluster_id, financing_providers, lc_prompt_version)"
                 " VALUES (?,1,'CLUSTERED',?,?,?)",
                 (eid, txn, json.dumps(providers), "low_confidence_extraction:0.13"))
    for a in advisors:
        conn.execute("INSERT INTO advisor (extraction_id, name, type, advised_party,"
                     " specialty) VALUES (?,?,?,?,?)",
                     (eid, a["name"], a.get("type", "OTHER"),
                      a.get("advised_party", "UNKNOWN"), a.get("specialty")))
    conn.commit()
    write_staging_observations_for_extraction(
        conn, eid, observation_source_stage="LC_EXTRACT", include_lc=True)
    conn.commit()


def _provider_rows(conn):
    return conn.execute("SELECT * FROM transaction_field_observation"
                        " WHERE field_name='financing_provider_party' ORDER BY observation_id").fetchall()


def _advisors(conn, eid=1):
    return conn.execute("SELECT name, specialty FROM advisor WHERE extraction_id=?"
                        " ORDER BY advisor_id", (eid,)).fetchall()


def test_examples() -> None:
    conn = _fresh()
    if not _ready(conn):
        print(f"  {FAIL}  migrations 017/018 / the provider writer are not present")
        _failures.append("migrations 017/018 / the provider writer are not present")
        conn.close(); return

    print('\n"X provided committed debt financing" -> FINANCING_PROVIDER:')
    _seed(conn, providers=[{"name": "X Capital"}], advisors=[])
    rows = _provider_rows(conn)
    check("one provider observation", len(rows), 1)
    check("named", json.loads(rows[0]["field_value"])["name"], "X Capital")
    check("and it created no advisor", len(_advisors(conn)), 0)
    conn.close()

    print('\n"Y acted as financing advisor" -> ADVISOR / financing_advisory:')
    conn = _fresh()
    _seed(conn, providers=[], advisors=[{"name": "Y & Co", "specialty": "financing_advisory"}])
    check("one advisor", [(a["name"], a["specialty"]) for a in _advisors(conn)],
          [("Y & Co", "financing_advisory")])
    # The point of the whole slice: arranging alone never produces a provider.
    check("and NO financing provider", len(_provider_rows(conn)), 0)
    conn.close()

    print('\n"Z served as financial advisor and arranged the financing" -> TWO advisors:')
    conn = _fresh()
    _seed(conn, providers=[], advisors=[
        {"name": "Z Partners", "specialty": "financial_advisory"},
        {"name": "Z Partners", "specialty": "financing_advisory"}])
    got = [(a["name"], a["specialty"]) for a in _advisors(conn)]
    check("two participations for one firm", len(got), 2)
    check("distinct specialties", sorted(s for _, s in got),
          ["financial_advisory", "financing_advisory"])
    check("same firm both times", {n for n, _ in got}, {"Z Partners"})
    check("still no financing provider", len(_provider_rows(conn)), 0)
    conn.close()

    print('\n"X provided the financing and acted as financing advisor" -> one of each:')
    conn = _fresh()
    _seed(conn, providers=[{"name": "X Capital"}],
          advisors=[{"name": "X Capital", "specialty": "financing_advisory"}])
    check("one provider fact", len(_provider_rows(conn)), 1)
    check("one advisor fact", len(_advisors(conn)), 1)
    check("the same firm, two participations",
          json.loads(_provider_rows(conn)[0]["field_value"])["name"]
          == _advisors(conn)[0]["name"], True)
    conn.close()

    print("\nOrdinary M&A advisory is unchanged:")
    conn = _fresh()
    _seed(conn, providers=[], advisors=[
        {"name": "Goldman Sachs", "specialty": "financial_advisory", "type": "FINANCIAL"},
        {"name": "Wachtell", "specialty": "legal", "type": "LEGAL"}])
    check("specialties untouched", [a["specialty"] for a in _advisors(conn)],
          ["financial_advisory", "legal"])
    check("and no provider appeared", len(_provider_rows(conn)), 0)
    conn.close()

    print("\nTwo providers are two facts, with per-fact provenance:")
    conn = _fresh()
    _seed(conn, providers=[{"name": "Blackstone Credit"}, {"name": "Bank of America"}],
          advisors=[])
    rows = _provider_rows(conn)
    check("two observations", len(rows), 2)
    # Without distinct fact keys INSERT OR IGNORE would collapse them.
    check("distinct fact keys", len({r["observation_fact_key"] for r in rows}), 2)
    check("no provider carries a subtype",
          any(set(json.loads(r["field_value"])) - {"name"} for r in rows), False)

    print("\nProviders are preserved, never reconciled:")
    check("financing_provider_party is absent from _FIELDS",
          "financing_provider_party" in dict(agg._FIELDS), False)
    fo = agg._load_observation_input(conn).get("tc_1", {}).get("field_observations", {})
    check("and never reaches the resolver", "financing_provider_party" in fo, False)
    conn.close()


# ---------------------------------------------------------------------------
# 4. Boundaries
# ---------------------------------------------------------------------------

def test_boundaries() -> None:
    print("\nNo identity work, and no new roles:")
    src = "\n".join((ROOT / f).read_text(encoding="utf-8") for f in
                    ("lib/observation_writer.py", "stages/low_confidence_extract.py"))
    check("no transaction_participant write",
          "INSERT INTO transaction_participant" in src, False)
    check("no entity write", "INSERT INTO entity" in src, False)
    flat = _norm(load_prompt_file("low_confidence_extraction")["system"])
    for role in ("jv_partner", "underwriter", '"sellers"'):
        check(f"{role} not authored", role in flat, False)

    print("\nThe advisor table can hold one firm twice — that is what makes this work:")
    sql = (ROOT / "schema" / "001_initial.sql").read_text(encoding="utf-8")
    adv = sql.split("CREATE TABLE IF NOT EXISTS advisor (", 1)[1].split(");", 1)[0]
    check("no uniqueness constraint on the advisor row",
          "UNIQUE" in adv.upper(), False)

    print("\nNothing else moved:")
    check("Stage 9 still owns 120 canonical columns",
          len(getattr(agg, "_STAGE9_OWNED_COLUMNS", ())), 120)
    check("aggregate version unchanged at 0.12", agg._VERSION, "0.12")


def main() -> int:
    print(__doc__.strip().split("\n")[0])
    test_prompt()
    test_parser()
    test_examples()
    test_boundaries()
    print()
    if _failures:
        print(f"{FAIL} — {len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"    {f}")
        return 1
    print(f"{PASS} — capital and advice about capital are separate facts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
