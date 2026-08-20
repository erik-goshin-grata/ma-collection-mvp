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

5. **A classifier-emitted PIPE is excluded too.** Once the prompt offers `PIPE` as a
   type, the model can return it directly — and a `PIPE` that arrived as the classifier's
   own verdict must not be treated as an ordinary classification. It is not in the
   funding family, so Stage 4's `NOT IN` gate would have swept it into M&A extraction:
   the exact leak this whole design exists to close, reopened through the front door.
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


# Every stage that consumes staging_extraction rows, and therefore every door a
# recognized PIPE must not fit through. Stage 3 is the producer and is excluded.
_CONSUMER_STAGES = [
    "stages/high_confidence_extract.py",   # Stage 4  — M&A HC
    "stages/funding_hc_extract.py",        # Stage 4a — Funding HC
    "stages/sec_trigger_detect.py",        # Stage 5
    "stages/sec_enrich.py",                # Stage 6
    "stages/low_confidence_extract.py",    # Stage 7  — LC
    "stages/entity_cluster.py",            # Stage 8  — clustering
    "stages/aggregate.py",                 # Stage 9  — profiling / transaction_record
]

# `se.status = 'X'`, `status = 'X'`, `se.status IN ('A', 'B')`, `status IN (...)`.
_STATUS_EQ = re.compile(r"\b(?:se\.)?status\s*=\s*'([A-Z_]+)'")
_STATUS_IN = re.compile(r"\b(?:se\.)?status\s+IN\s*\(([^)]*)\)", re.I)


def _selected_statuses(stage_file: str) -> set[str]:
    """Every staging status a stage selects on, read from its own source."""
    text = ROOT.joinpath(stage_file).read_text()
    # Drop the UPDATE ... SET status='X' writes; those are outputs, not gates.
    text = re.sub(r"SET\s+status\s*=\s*'[A-Z_]+'", "", text)
    found = set(_STATUS_EQ.findall(text))
    for group in _STATUS_IN.findall(text):
        found |= set(re.findall(r"'([A-Z_]+)'", group))
    return found


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
        # A de-SPAC whose release carries standard concurrent-PIPE language. Its verdict
        # must survive: this is a real deal. Under classifier 0.9 (V3 §T2) a de-SPAC
        # arrives as ACQUISITION with combination_structure = DE_SPAC, so ACQUISITION is
        # the seat the concurrent PIPE must not take. The protection is unchanged in
        # substance -- PIPE_OVERRIDABLE_EVENT_TYPES is an allowlist, and neither the old
        # nor the new value is in it.
        (4, "PR_NEWSWIRE",
         "Summit Acquisition Corp announces business combination with Orbit Systems",
         "Summit Acquisition Corp announced a business combination with Orbit Systems, "
         "supported by a $150 million PIPE at $10.00 per share.",
         "ACQUISITION"),
        # Same structure, different provider, and the classifier mis-seats it into the
        # funding family rather than UNKNOWN. Both differences must be irrelevant.
        (5, "WEB_URL", "Calder Bio announces financing",
         "Calder Bio entered into a securities purchase agreement for a private "
         "investment in public equity of $32 million.",
         "GROWTH_EQUITY"),
        # The classifier now has PIPE in its vocabulary and returns it directly. This is
        # the row that would leak into M&A extraction if a self-declared PIPE were
        # treated as an ordinary classification.
        (6, "SEC", "FORM 8-K — Talon Metals Corp.",
         "On August 14, 2026, Talon Metals Corp. entered into securities purchase "
         "agreements for a PIPE financing of $55 million with institutional investors.",
         "PIPE"),
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
    _check(failures, "stage excluded all three PIPEs", result["recognized_not_profiled"], 3)

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
    _check(failures, "de-SPAC keeps its ACQUISITION seat", rows[4]["v2_event_type"],
           "ACQUISITION")
    # The second PIPE: different provider, different mis-seating, same outcome.
    _check(failures, "WEB_URL PIPE status", rows[5]["status"], PIPE_EXCLUDED_STATUS)
    _check(failures, "WEB_URL PIPE type", rows[5]["v2_event_type"], PIPE_EVENT_TYPE)
    # The classifier's own verdict, from a third source channel.
    _check(failures, "self-declared PIPE status", rows[6]["status"], PIPE_EXCLUDED_STATUS)
    _check(failures, "self-declared PIPE type", rows[6]["v2_event_type"], PIPE_EVENT_TYPE)

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
    _check(failures, "self-declared PIPE is selected by NEITHER gate", seen[6], [])

    # Nothing canonical was derived: the row never leaves staging.
    _check(failures, "no transaction_record was created for the PIPE row",
           conn.execute("SELECT COUNT(*) FROM transaction_record").fetchone()[0], 0)

    conn.close()
    return failures


def _queryable() -> list[str]:
    """A terminal row is still a first-class record: findable, and carrying its reason."""
    import json
    import sqlite3
    import tempfile

    failures: list[str] = []
    path = str(Path(tempfile.mkdtemp(prefix="pipe_query_")) / "q.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE staging_extraction (extraction_id INTEGER PRIMARY KEY, "
                 "source_raw_id INTEGER, status TEXT, v2_event_type TEXT, notes TEXT)")
    outcome = resolve_classification("UNKNOWN", "Ridgeline announces", _PIPE_TEXT)
    conn.execute(
        "INSERT INTO staging_extraction (source_raw_id, status, v2_event_type, notes) "
        "VALUES (7, ?, ?, ?)",
        (outcome["status"], outcome["v2_event_type"],
         json.dumps({"dt": None, "pipe_exclusion": outcome["provenance"]})),
    )
    conn.commit()

    # Findable by either handle a reviewer would reach for.
    for name, sql in (
        ("by status", "SELECT * FROM staging_extraction WHERE status = ?"),
        ("by type", "SELECT * FROM staging_extraction WHERE v2_event_type = ?"),
    ):
        key = outcome["status"] if name == "by status" else outcome["v2_event_type"]
        rows = conn.execute(sql, (key,)).fetchall()
        _check(failures, f"recognized PIPE is findable {name}", len(rows), 1)

    row = conn.execute("SELECT notes FROM staging_extraction").fetchone()
    prov = (json.loads(row["notes"]) or {}).get("pipe_exclusion") or {}
    for field in ("classifier_v2_event_type", "recognition_form", "evidence",
                  "corroborated", "rule"):
        if field not in prov:
            failures.append(f"provenance lost {field!r} on the round trip to the DB")
    if "PIPE financing" not in (prov.get("evidence") or ""):
        failures.append("the source sentence did not survive into the stored record")
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

    # The de-SPAC case stated in full: a concurrent PIPE inside a real de-SPAC, which
    # classifier 0.9 seats as ACQUISITION. The legacy REVERSE_MERGER seat is still
    # asserted non-overridable in _OVERRIDE_CASES above, for stored rows.
    despac = resolve_classification("ACQUISITION", "Summit announces combination",
                                    _DESPAC_TEXT)
    _check(failures, "de-SPAC PIPE does not displace the acquisition",
           despac["v2_event_type"], "ACQUISITION")
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

    # --- 6. The classifier's own PIPE verdict ------------------------------
    # `PIPE` is not in the funding family, so a classifier-emitted PIPE left at status
    # CLASSIFIED satisfies Stage 4's NOT IN gate and lands in M&A extraction. Adding the
    # type to the prompt without this branch would reopen the original leak.
    self_declared = resolve_classification("PIPE", "Ridgeline announces", _PIPE_TEXT)
    _check(failures, "classifier-emitted PIPE is excluded", self_declared["excluded"], True)
    _check(failures, "classifier-emitted PIPE type",
           self_declared["v2_event_type"], PIPE_EVENT_TYPE)
    _check(failures, "classifier-emitted PIPE status",
           self_declared["status"], PIPE_EXCLUDED_STATUS)
    _check(failures, "classifier-emitted PIPE records its origin",
           self_declared["provenance"]["recognition_form"], "CLASSIFIER")
    _check(failures, "classifier-emitted PIPE keeps its own verdict",
           self_declared["provenance"]["classifier_v2_event_type"], "PIPE")
    # The deterministic recognizer agrees here, and that agreement is recorded.
    _check(failures, "corroborated when the text says PIPE too",
           self_declared["provenance"]["corroborated"], True)

    # A PIPE verdict the source text does not support is still honoured — the exclusion
    # is non-destructive and reversible, the row keeps every field, and nothing is
    # deleted — but it is FLAGGED, so an over-classifying prompt is findable rather than
    # silently dropping deals. Uncorroborated is the review queue, not a second policy.
    uncorroborated = resolve_classification(
        "PIPE", "Apex to acquire Beta Industries",
        "Apex Industrial Holdings announced a definitive agreement to acquire Beta "
        "Industries for $450 million in cash.")
    _check(failures, "uncorroborated classifier PIPE is still excluded",
           uncorroborated["excluded"], True)
    _check(failures, "uncorroborated classifier PIPE is flagged",
           uncorroborated["provenance"]["corroborated"], False)
    if uncorroborated["provenance"].get("evidence"):
        failures.append("uncorroborated PIPE must not fabricate supporting evidence")

    # A deterministic recognition is never labelled as the classifier's.
    _check(failures, "recognizer-driven exclusion keeps its own form",
           resolve_classification("UNKNOWN", "x", _PIPE_TEXT)["provenance"]["recognition_form"],
           "ACRONYM")

    # --- 7. Prompt and stage vocabularies agree ---------------------------
    # The type has to exist in three places at once: the classifier prompt that emits it,
    # the stage enum that validates it, and the relevancy vocabulary upstream. Any one
    # of them missing it turns a recognized structure back into an unrecognized one.
    import stages.deal_type_classify as dtc
    import stages.relevancy_filter as rf

    dt_prompt = ROOT.joinpath("prompts/deal_type_classifier.md").read_text()
    pipe_def = re.search(r"^\s*\d+\.\s+PIPE\s+—.*?(?=^\s*\d+\.\s+[A-Z])",
                         dt_prompt, re.M | re.S)
    if not pipe_def:
        failures.append("deal_type_classifier.md does not define PIPE as a deal type")
        pipe_block = ""
    else:
        pipe_block = pipe_def.group(0).lower()
    if "private investment in public equity" not in pipe_block:
        failures.append("the PIPE definition never spells out what PIPE stands for")
    # The negative list is what keeps the type narrow: a new bucket with no stated
    # boundary gets over-filled. Scoped to the PIPE definition on purpose — these words
    # appear elsewhere in the prompt, so an unscoped search passes even after the
    # negative list is deleted. That exact false pass showed up in revert-verification.
    for needle in ("private placement", "convertible", "preferred",
                   "registered direct", "underwritten"):
        if needle not in pipe_block:
            failures.append(
                f"the PIPE definition must name {needle!r} as NOT a PIPE on its own — "
                "a new type without a negative list gets over-used"
            )
    if "only when the source explicitly identifies" not in pipe_block:
        failures.append("the PIPE definition must state that it is a recognition, "
                        "not an inference")
    if "UNKNOWN" not in dt_prompt:
        failures.append("UNKNOWN must survive for public-company raises that are not PIPEs")

    _check(failures, "stage enum accepts PIPE", PIPE_EVENT_TYPE in dtc._VALID_V2_EVENT_TYPES, True)

    # Full parity between the prompt's output-schema enum row and the stage's validator.
    # A type defined in the DEAL TYPES prose but missing from the schema table is a type
    # the model is told to use and then told is invalid — which is exactly what happened
    # to PIPE on the first pass here, and is the same drift class the reason_code parity
    # test exists to catch on the relevancy side.
    row = re.search(r"^\|\s*`v2_event_type`\s*\|\s*enum\s*\|(.+?)\|\s*$",
                    dt_prompt, re.M)
    if not row:
        failures.append("deal_type_classifier.md has no v2_event_type enum row to check")
    else:
        declared = set(re.findall(r"`([A-Z][A-Z0-9_]*)`", row.group(1)))
        missing_from_prompt = dtc._VALID_V2_EVENT_TYPES - declared
        missing_from_stage = declared - dtc._VALID_V2_EVENT_TYPES
        if missing_from_prompt:
            failures.append(
                f"the stage validates {sorted(missing_from_prompt)} but the prompt's "
                "output-schema enum does not list them"
            )
        if missing_from_stage:
            failures.append(
                f"the prompt offers {sorted(missing_from_stage)} but the stage rejects "
                "them — every row so classified becomes PROMPT_FAILED"
            )

    # Prompt file version and stage version must not drift apart.
    m = re.search(r"^\*\*Version:\*\*\s*([0-9.]+)", dt_prompt, re.M)
    _check(failures, "deal_type_classifier prompt version matches the stage",
           m.group(1) if m else None, dtc._VERSION)

    # Relevancy: PIPE is a RELEVANT reason code. It must stay on the RELEVANT side —
    # marking it NOT_RELEVANT would drop the row before Stage 3 and destroy the very
    # provenance the exclusion is supposed to preserve.
    rel_prompt = ROOT.joinpath("prompts/relevancy_filter.md").read_text()
    block = re.search(r"REASON_CODES_START(.*?)REASON_CODES_END", rel_prompt, re.S)
    if not block:
        failures.append("relevancy prompt lost its REASON_CODES block")
    else:
        relevant_side, _, not_relevant_side = block.group(1).partition("NOT_RELEVANT side")
        if "`PIPE`" not in relevant_side:
            failures.append("PIPE is not declared on the RELEVANT side of the relevancy enum")
        if "`PIPE`" in not_relevant_side:
            failures.append(
                "PIPE is on the NOT_RELEVANT side — that drops the row before Stage 3 "
                "and loses the recognized-exclusion record entirely"
            )
    _check(failures, "relevancy stage accepts PIPE", "PIPE" in rf._VALID_REASON_CODES, True)

    # --- 8. Terminality: no consumer stage can see the row ----------------
    # The exclusion is only worth the name if EVERY downstream door is shut, not just
    # the two extraction gates. This reads each consuming stage's own status selection
    # out of its source and asserts RECOGNIZED_NOT_PROFILED appears in none of them —
    # so M&A HC, Funding HC, SEC trigger/enrich, LC, clustering and Stage 9 profiling
    # are all unreachable, and with Stage 9 unreachable so is the transaction_size
    # waterfall and every canonical field it feeds.
    for stage_file in _CONSUMER_STAGES:
        statuses = _selected_statuses(stage_file)
        # A regex that matched nothing would pass this vacuously — the wrong-reason
        # trap. Every consumer gates on at least one status, so an empty set is a
        # broken reader, not a stage with no gate.
        if not statuses:
            failures.append(
                f"{stage_file}: no status gate found. Either the stage stopped gating "
                "on staging status — in which case the PIPE exclusion must be "
                "re-verified against whatever replaced it — or this reader is broken "
                "and every assertion below it is vacuous."
            )
            continue
        if PIPE_EXCLUDED_STATUS in statuses:
            failures.append(
                f"{stage_file} selects {PIPE_EXCLUDED_STATUS} — a recognized PIPE can "
                f"enter it. Statuses selected: {sorted(statuses)}"
            )

    # Stage 9's waterfall, asserted directly as well as by unreachability. Belt and
    # braces: if a PIPE ever did reach aggregation, it must still derive no magnitude.
    from stages.aggregate import _derive_transaction_size
    size, basis = _derive_transaction_size(
        {"v2_event_type": PIPE_EVENT_TYPE, "round_size": 55_000_000,
         "value_amount": 55_000_000, "value_type": "TRANSACTION_VALUE"},
        transaction_value=55_000_000,
    )
    _check(failures, "PIPE derives no transaction_size", size, None)
    _check(failures, "PIPE derives no transaction_size_basis", basis, None)

    # --- 9. Terminal does not mean invisible ------------------------------
    # The row must stay queryable, with its provenance intact. An exclusion nobody can
    # find is indistinguishable from a row that was never ingested.
    failures.extend(_queryable())

    # --- 10. Promotion stays a one-line change ----------------------------
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
          f"cases, prompt/stage vocabulary in parity, structural types never displaced, "
          f"terminal in all {len(_CONSUMER_STAGES)} consumer stages, still queryable")


if __name__ == "__main__":
    main()
