#!/usr/bin/env python3
"""Regression guard for PIPE recognition as a recognized-but-not-profiled exclusion.

No network and no model calls.

A PIPE — private investment in public equity — is a real, identifiable structure that
this pipeline does not profile. Before this change it had no seat: the classifier prompt
routes it to `UNKNOWN`, and Stage 4's gate is `NOT IN (funding family)`, so an `UNKNOWN`
PIPE fell straight into **M&A high-confidence extraction** and came out the far end as a
transaction_record with M&A semantics. That is the noise.

Three things are asserted, and they fail in different ways:

1. **Recognition is narrow.** Only an explicit PIPE — the acronym bound to a financing
   construction, or the phrase spelled out — is recognized. A private placement, a
   convertible note, a preferred issuance or a registered direct offering is NOT a PIPE
   just because it is private capital into a public company. Over-recognition here is
   worse than under-recognition: it deletes deals that are in scope.

2. **A recognized PIPE never takes a structural type's seat.** "$150 million PIPE" is
   standard de-SPAC language, where the PIPE is a financing component of a REVERSE_MERGER
   that IS in scope. The same holds for an acquisition financed by a concurrent PIPE.
   Only `UNKNOWN` and the funding family can be overridden — those are the seats a PIPE
   is mis-occupying, not seats it is competing for.

3. **The exclusion actually excludes.** The terminal status must be invisible to both
   extraction gates. A row that is recognized but still selected by Stage 4 has changed
   nothing except its label.

4. **Recognition reads the transaction language, never the provider.** The same PIPE
   from PredictLeads, Business Wire, or an SEC filing gets the same treatment. Provider
   identity is not evidence about what a transaction is, and a rule keyed on it would
   silently stop working the moment a source was re-labelled or a new one added.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.pipe_recognition import (  # noqa: E402
    PIPE_EVENT_TYPE, PIPE_EXCLUDED_STATUS, PIPE_OVERRIDABLE_EVENT_TYPES,
    recognize_pipe, resolve_classification,
)

# (label, title, body, expected: True = recognized as a PIPE)
CASES = [
    # --- Explicit acronym, bound to the financing -------------------------
    (
        "acronym_financing", True,
        "Vertex Therapeutics Announces $75 Million PIPE Financing",
        "Vertex Therapeutics (NASDAQ: VRTX) today announced that it has entered into "
        "securities purchase agreements for a $75 million PIPE financing with a group "
        "of institutional investors. The transaction is expected to close next week.",
    ),
    (
        "acronym_investors", True,
        "Helios Corp announces private placement",
        "Helios Corp announced a securities purchase agreement under which the PIPE "
        "investors will purchase 12,000,000 shares of common stock at $4.15 per share.",
    ),
    (
        "acronym_after_amount", True,
        "Northstar closes offering",
        "Northstar Energy announced the closing of a $40 million PIPE transaction led "
        "by two healthcare-focused funds.",
    ),
    # --- The phrase spelled out -------------------------------------------
    (
        "expanded_phrase", True,
        "Cerulean Biosciences Announces Financing",
        "Cerulean Biosciences today announced a private investment in public equity of "
        "approximately $60 million with new and existing institutional investors.",
    ),
    (
        "expanded_with_parenthetical", True,
        "Kestrel announces transaction",
        "Kestrel Holdings entered into definitive agreements for a private investment "
        "in public equity (\"PIPE\") of $28 million.",
    ),
    # --- NOT a PIPE: private capital into a public company is not enough ---
    # Each of these is exactly the over-recognition the design refuses. They are private,
    # they are securities, several are into public issuers — and none of them says PIPE.
    (
        "generic_private_placement", False,
        "Orion Materials Announces Private Placement",
        "Orion Materials announced a private placement of 5,000,000 shares of common "
        "stock to accredited investors for gross proceeds of $22 million.",
    ),
    (
        "convertible_notes", False,
        "Lumen Devices prices convertible notes",
        "Lumen Devices priced an offering of $300 million aggregate principal amount of "
        "convertible senior notes due 2031 in a private offering to qualified "
        "institutional buyers.",
    ),
    (
        "preferred_issuance", False,
        "Ardent Capital invests in Bexley",
        "Bexley Industries issued 100,000 shares of Series A convertible preferred stock "
        "to Ardent Capital for $50 million.",
    ),
    (
        "registered_direct", False,
        "Solstice announces registered direct offering",
        "Solstice Pharmaceuticals announced a registered direct offering priced at the "
        "market of approximately $15 million with a single institutional investor.",
    ),
    (
        "secondary_public_offering", False,
        "Meridian announces underwritten public offering",
        "Meridian Corp announced an underwritten public offering of 8,000,000 shares of "
        "common stock.",
    ),
    # --- The word must be the structure, not a substring or a name --------
    (
        "pipeline_uppercase", False,
        "TRANSCONTINENTAL PIPELINE ANNOUNCES EXPANSION",
        "TRANSCONTINENTAL PIPELINE COMPANY announced a $400 million expansion of its "
        "natural gas PIPELINE network serving the Gulf Coast.",
    ),
    (
        "product_pipeline", False,
        "Aveda reports on its pipeline",
        "Aveda Therapeutics announced a $30 million financing to advance its clinical "
        "pipeline. The pipeline includes three programs in Phase 2.",
    ),
    (
        "company_name_pipe", False,
        "Apex acquires Superior PIPE & Supply",
        "Apex Industrial Holdings announced it has acquired Superior PIPE & Supply, a "
        "distributor of steel pipe and fittings, for $85 million.",
    ),
]

# (label, llm_event_type, expect_excluded) — routing on a text that IS an explicit PIPE.
ROUTING = [
    # The seats a PIPE mis-occupies today. These are the ones it may take.
    ("unknown_seat", "UNKNOWN", True),
    ("vc_seat", "VC_ROUND", True),
    ("growth_seat", "GROWTH_EQUITY", True),
    ("venture_debt_seat", "VENTURE_DEBT", True),
    # Structural types are never overridden. A de-SPAC PIPE, an acquisition financed by
    # a concurrent PIPE — the announced event is real and in scope, and taking its seat
    # would delete a deal rather than reduce noise.
    ("reverse_merger_kept", "REVERSE_MERGER", False),
    ("acquisition_kept", "ACQUISITION", False),
    ("merger_kept", "MERGER", False),
    ("spin_off_kept", "SPIN_OFF", False),
    ("split_off_kept", "SPLIT_OFF", False),
    ("joint_venture_kept", "JOINT_VENTURE", False),
    ("recap_kept", "RECAPITALIZATION", False),
]

_PIPE_TEXT = (
    "Ridgeline Corp announced a $75 million PIPE financing with institutional investors."
)
_DESPAC_TEXT = (
    "Summit Acquisition Corp, a special purpose acquisition company, announced a "
    "business combination with Orbit Systems, supported by a $150 million PIPE at "
    "$10.00 per share."
)


def _check(failures: list[str], name: str, got, expected) -> None:
    if got != expected:
        failures.append(f"{name}: expected {expected!r}, got {got!r}")


def _gate_predicate(stage_file: str) -> str:
    """Lift a stage's own WHERE predicate out of its source.

    Retyping the gate into the test would assert that my copy behaves as expected while
    the stage drifted away from it. Reading it means a changed gate is either exercised
    here or fails to be found at all.
    """
    text = ROOT.joinpath(stage_file).read_text()
    m = re.search(
        r"WHERE se\.status = 'CLASSIFIED'\s*\n(\s*AND COALESCE\(se\.v2_event_type, "
        r"se\.deal_type\)[^\n]*)\n",
        text,
    )
    if not m:
        raise AssertionError(
            f"{stage_file}: could not find the status+family gate this test relies on. "
            "If the gate moved or changed shape, the PIPE exclusion must be re-verified "
            "against the new one rather than assumed."
        )
    return "se.status = 'CLASSIFIED'\n" + m.group(1)


def _end_to_end() -> list[str]:
    """Drive the real Stage 3 with a stubbed classifier, then run the real gates."""
    import json
    import tempfile
    from types import SimpleNamespace

    import db as _db
    import stages.deal_type_classify as dtc

    failures: list[str] = []
    # (source_raw_id, source_type, title, body, what the stubbed classifier returns)
    #
    # Providers are deliberately mixed. Row 1 arrives as PR_NEWSWIRE and row 5 as
    # WEB_URL — the source_type a PredictLeads ingest writes — and both are PIPEs. If
    # recognition ever became provider-keyed, one of them would survive.
    SOURCES = [
        (1, "PR_NEWSWIRE", "Ridgeline Corp Announces $75 Million PIPE Financing",
         "Ridgeline Corp (NASDAQ: RDGL) announced a $75 million PIPE financing with a "
         "group of institutional investors.",
         "UNKNOWN"),
        (2, "PR_NEWSWIRE", "Apex to acquire Beta Industries",
         "Apex Industrial Holdings announced a definitive agreement to acquire Beta "
         "Industries for $450 million in cash.",
         "ACQUISITION"),
        (3, "WEB_URL", "Arcade.dev raises $60M Series B",
         "Arcade.dev today announced it has raised $60 million in Series B funding led "
         "by Redpoint Ventures.",
         "VC_ROUND"),
        # A de-SPAC whose release carries standard concurrent-PIPE language. The
        # classifier's REVERSE_MERGER verdict must survive: this is a real deal.
        (4, "PR_NEWSWIRE",
         "Summit Acquisition Corp announces business combination with Orbit Systems",
         "Summit Acquisition Corp announced a business combination with Orbit Systems, "
         "supported by a $150 million PIPE at $10.00 per share.",
         "REVERSE_MERGER"),
        # Same structure, different provider, and the classifier mis-seats it into the
        # funding family rather than UNKNOWN. Both differences must be irrelevant.
        (5, "WEB_URL", "Calder Bio announces financing",
         "Calder Bio entered into a securities purchase agreement for a private "
         "investment in public equity of $32 million.",
         "GROWTH_EQUITY"),
    ]
    by_title = {t: v for _i, _st, t, _b, v in SOURCES}

    tmp = tempfile.mkdtemp(prefix="pipe_e2e_")
    db_path = str(Path(tmp) / "t.db")
    _db.init_db(db_path)
    conn = _db.get_connection(db_path)
    for sid, source_type, title, body, _v in SOURCES:
        conn.execute(
            "INSERT INTO source_raw (source_raw_id, source_type, source_tier, url,"
            " fetched_at, title, clean_text, published_date, source_status)"
            " VALUES (?, ?, 'T2', ?, '2026-08-18', ?, ?, '2026-08-18', 'RELEVANT')",
            (sid, source_type, f"https://example.test/{sid}", title, body),
        )
    conn.commit()

    def _stub(**kwargs):
        prompt = kwargs["user_prompt"]
        hit = next((t for t in by_title if t in prompt), None)
        assert hit, "stub could not identify which source it was called for"
        return {"v2_event_type": by_title[hit], "deal_type": by_title[hit],
                "event_history_type": "ANNOUNCED", "target_status": "PUBLIC",
                "target_type": "standalone_company", "model_confidence": "HIGH",
                "notes": None}

    real_call, real_sleep = dtc.call_prompt, dtc._SLEEP
    dtc.call_prompt, dtc._SLEEP = _stub, 0.0
    try:
        result = dtc.run(conn, SimpleNamespace(log_level="ERROR"), "pipe_e2e_test")
    finally:
        dtc.call_prompt, dtc._SLEEP = real_call, real_sleep

    _check(failures, "stage classified the three profiled rows", result["classified"], 3)
    _check(failures, "stage excluded both PIPEs", result["recognized_not_profiled"], 2)

    rows = {r["source_raw_id"]: r for r in conn.execute(
        "SELECT source_raw_id, extraction_id, status, v2_event_type, notes "
        "FROM staging_extraction").fetchall()}
    _check(failures, "one staging row per source", len(rows), len(SOURCES))
    _check(failures, "PIPE row status", rows[1]["status"], PIPE_EXCLUDED_STATUS)
    _check(failures, "PIPE row type", rows[1]["v2_event_type"], PIPE_EVENT_TYPE)
    _check(failures, "acquisition row status", rows[2]["status"], "CLASSIFIED")
    _check(failures, "acquisition row type", rows[2]["v2_event_type"], "ACQUISITION")
    _check(failures, "VC row status", rows[3]["status"], "CLASSIFIED")
    _check(failures, "VC row type", rows[3]["v2_event_type"], "VC_ROUND")
    _check(failures, "de-SPAC row status", rows[4]["status"], "CLASSIFIED")
    _check(failures, "de-SPAC keeps REVERSE_MERGER", rows[4]["v2_event_type"],
           "REVERSE_MERGER")
    # The second PIPE: different provider, different mis-seating, same outcome.
    _check(failures, "WEB_URL PIPE status", rows[5]["status"], PIPE_EXCLUDED_STATUS)
    _check(failures, "WEB_URL PIPE type", rows[5]["v2_event_type"], PIPE_EVENT_TYPE)

    # Provenance survives on the excluded row, or the exclusion cannot be reviewed.
    notes = json.loads(rows[1]["notes"] or "{}")
    excl = notes.get("pipe_exclusion") or {}
    _check(failures, "excluded row records the classifier's own verdict",
           excl.get("classifier_v2_event_type"), "UNKNOWN")
    if "PIPE financing" not in (excl.get("evidence") or ""):
        failures.append("excluded row does not quote the source sentence it rests on")
    # source_raw is untouched — the source record is never edited by an exclusion.
    src = conn.execute("SELECT source_status, clean_text FROM source_raw "
                       "WHERE source_raw_id = 1").fetchone()
    _check(failures, "source_raw status untouched", src["source_status"], "RELEVANT")
    if "PIPE financing" not in src["clean_text"]:
        failures.append("source text was modified by the exclusion")

    # --- the two real gates ----------------------------------------------
    ma_gate = _gate_predicate("stages/high_confidence_extract.py")
    fund_gate = _gate_predicate("stages/funding_hc_extract.py")
    seen: dict[int, list[str]] = {row[0]: [] for row in SOURCES}
    for name, predicate in (("stage4_ma", ma_gate), ("stage4a_funding", fund_gate)):
        picked = conn.execute(
            f"SELECT se.source_raw_id FROM staging_extraction se WHERE {predicate}"
        ).fetchall()
        for r in picked:
            seen[r["source_raw_id"]].append(name)

    _check(failures, "PIPE row is selected by NEITHER extraction gate", seen[1], [])
    _check(failures, "acquisition goes to M&A extraction", seen[2], ["stage4_ma"])
    _check(failures, "VC round goes to funding extraction", seen[3], ["stage4a_funding"])
    _check(failures, "de-SPAC goes to M&A extraction", seen[4], ["stage4_ma"])
    _check(failures, "WEB_URL PIPE is selected by NEITHER gate either", seen[5], [])

    # Nothing canonical was derived: the row never leaves staging.
    _check(failures, "no transaction_record was created for the PIPE row",
           conn.execute("SELECT COUNT(*) FROM transaction_record").fetchone()[0], 0)

    conn.close()
    return failures


def main() -> None:
    failures: list[str] = []

    # --- 1. Recognition is narrow ----------------------------------------
    for label, expected, title, body in CASES:
        got = recognize_pipe(title, body)
        _check(failures, f"{label} recognized", got is not None, expected)
        if expected and got is not None:
            # A recognition with no quoted evidence cannot be reviewed by a human, and
            # the whole exclusion rests on a human being able to check it.
            if not (got.get("evidence") or "").strip():
                failures.append(f"{label}: recognized with no evidence quoted")
            if got.get("form") not in ("ACRONYM", "EXPANDED"):
                failures.append(f"{label}: unknown recognition form {got.get('form')!r}")

    # --- 1a. No pattern contains a control character ----------------------
    # A `\b` written inside a non-raw string becomes a literal backspace (0x08) and
    # silently disables the pattern. This has happened before in this repo, and it
    # produced a right-answer-for-the-wrong-reason pass that stood for a whole session.
    import lib.pipe_recognition as pr
    for name, patterns in pr._PATTERN_GROUPS.items():
        for rx in patterns:
            bad = [c for c in rx.pattern if ord(c) < 32 and c not in "\n\t"]
            if bad:
                failures.append(
                    f"{name}: pattern contains control character {bad[0]!r}, which "
                    f"silently disables it: {rx.pattern[:60]!r}"
                )

    # --- 1b. The acronym is case-sensitive --------------------------------
    # "pipe financing" in lower case is a plumbing contract, not a securities structure.
    if recognize_pipe("Acme announces", "Acme announced a $10 million pipe financing.") is not None:
        failures.append("lower-case 'pipe' was recognized — that is a plumbing contract")

    # --- 2. Routing: which seats a PIPE may take --------------------------
    for label, llm_type, expect_excluded in ROUTING:
        outcome = resolve_classification(llm_type, "Ridgeline announces", _PIPE_TEXT)
        _check(failures, f"{label} excluded", outcome["excluded"], expect_excluded)
        if expect_excluded:
            _check(failures, f"{label} type", outcome["v2_event_type"], PIPE_EVENT_TYPE)
            _check(failures, f"{label} status", outcome["status"], PIPE_EXCLUDED_STATUS)
            # Provenance: what the classifier actually said must survive, or the
            # exclusion is unreviewable and cannot be promoted later.
            _check(failures, f"{label} keeps the original type",
                   outcome["provenance"]["classifier_v2_event_type"], llm_type)
            if not (outcome["provenance"].get("evidence") or "").strip():
                failures.append(f"{label}: excluded with no evidence in provenance")
        else:
            _check(failures, f"{label} type untouched", outcome["v2_event_type"], llm_type)
            _check(failures, f"{label} status untouched", outcome["status"], "CLASSIFIED")
            _check(failures, f"{label} no provenance written", outcome["provenance"], None)

    # The de-SPAC case stated in full: a concurrent PIPE inside a real REVERSE_MERGER.
    despac = resolve_classification("REVERSE_MERGER", "Summit announces combination",
                                    _DESPAC_TEXT)
    _check(failures, "de-SPAC PIPE does not displace the REVERSE_MERGER",
           despac["v2_event_type"], "REVERSE_MERGER")
    _check(failures, "de-SPAC row still extracts", despac["status"], "CLASSIFIED")

    # --- 3. Unchanged paths -----------------------------------------------
    # An ordinary VC round and an ordinary acquisition must be untouched. The recognizer
    # is not consulted for a verdict here — it simply must not fire.
    vc = resolve_classification(
        "VC_ROUND", "Arcade.dev raises $60M Series B",
        "Arcade.dev today announced it has raised $60 million in Series B funding led "
        "by Redpoint Ventures to expand its engineering team.")
    _check(failures, "ordinary VC round untouched", vc["v2_event_type"], "VC_ROUND")
    _check(failures, "ordinary VC round still extracts", vc["status"], "CLASSIFIED")

    growth = resolve_classification(
        "GROWTH_EQUITY", "Rejoni announces growth investment",
        "Rejoni announced a $25 million growth equity investment from Summit Partners "
        "to accelerate its go-to-market expansion.")
    _check(failures, "ordinary growth round untouched",
           growth["v2_event_type"], "GROWTH_EQUITY")

    acq = resolve_classification(
        "ACQUISITION", "Apex to acquire Beta Industries",
        "Apex Industrial Holdings announced a definitive agreement to acquire Beta "
        "Industries for $450 million in cash.")
    _check(failures, "acquisition untouched", acq["v2_event_type"], "ACQUISITION")
    _check(failures, "acquisition still extracts", acq["status"], "CLASSIFIED")

    minority = resolve_classification(
        "ACQUISITION", "Fund acquires minority stake",
        "Palatine acquired a 30% minority stake in Fortus Group from an early backer "
        "for £40 million.")
    _check(failures, "minority stake untouched", minority["v2_event_type"], "ACQUISITION")

    # --- 4. End to end: the exclusion actually excludes -------------------
    # The load-bearing assertion. A row that is recognized but still selected by an
    # extraction gate has changed nothing except its label. This drives the REAL Stage 3
    # (classifier stubbed) against a temp DB and then runs the two extraction gates'
    # OWN predicate text, lifted out of the stage sources rather than retyped here — a
    # copy of a gate proves nothing about the gate.
    failures.extend(_end_to_end())

    # --- 5. Recognition never reads the provider --------------------------
    # Asserted structurally, not just by outcome: if the functions cannot see a source
    # identifier, no future edit can quietly key on one. The E2E above already mixes
    # PR_NEWSWIRE and WEB_URL rows; this is the property behind that.
    import inspect
    for fn in (recognize_pipe, resolve_classification):
        params = set(inspect.signature(fn).parameters)
        leaked = {p for p in params
                  if any(k in p.lower() for k in
                         ("source_type", "provider", "adapter", "tier", "feed",
                          "source_raw", "publisher", "vendor"))}
        if leaked:
            failures.append(
                f"{fn.__name__} takes {sorted(leaked)} — recognition must rest on the "
                "transaction language alone. Provider identity is not evidence about "
                "what a transaction is."
            )

    # And behaviourally: identical language, any wrapper, same verdict.
    pipe_body = ("Meridian Bio announced a $45 million PIPE financing with institutional "
                 "investors.")
    verdicts = {
        wrapper: recognize_pipe(title, pipe_body)
        for wrapper, title in (
            ("pr_newswire", "Meridian Bio Announces PIPE Financing"),
            ("predictleads_web", "Meridian Bio raises capital"),
            ("sec_filing", "FORM 8-K CURRENT REPORT — Meridian Bio Inc."),
            ("bare", ""),
        )
    }
    if len({v is not None for v in verdicts.values()}) != 1:
        failures.append(
            "the same PIPE language was recognized under one wrapper and not another: "
            + repr({k: (v is not None) for k, v in verdicts.items()})
        )
    if not all(v is not None for v in verdicts.values()):
        failures.append("the PIPE body was not recognized on its own text")

    # --- 6. Promotion stays a one-line change -----------------------------
    # If PIPE ever becomes in-scope, the seat it takes is what has to change back, so
    # the overridable set is the single knob and must stay explicit.
    _check(failures, "overridable set is exactly UNKNOWN + the funding family",
           sorted(PIPE_OVERRIDABLE_EVENT_TYPES),
           ["GROWTH_EQUITY", "UNKNOWN", "VC_ROUND", "VENTURE_DEBT"])

    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print(f"PASS PIPE recognition: {len(CASES)} recognition cases, {len(ROUTING)} routing "
          "cases, structural types never displaced, exclusion invisible to both gates")


if __name__ == "__main__":
    main()
