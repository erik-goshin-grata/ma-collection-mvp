"""
Stage 7: low_confidence_extract

Runs the Opus low-confidence extraction prompt on every staging_extraction
row with status IN ('HC_EXTRACTED', 'SEC_NOT_TRIGGERED', 'SEC_ENRICHED').
The broad status set covers rows that finished HC extraction but may not have
gone through Stage 5/6 (e.g., resume after partial run).

Populates on success (status → LC_EXTRACTED):
  - consideration_components (JSON array on staging_extraction)
  - flags: deal_attitude, approach_type, competing_bid, regulatory_approvals_required
  - go_shop: has_go_shop, go_shop_period_days
  - termination_fees: target_fee_amount/percentage, acquirer_fee_amount/percentage
  - lc_prompt_version
  - advisor rows (one INSERT per advisor in the response array)
  - notes["lc"] merged into existing notes dict

On PROMPT_FAILED: sets status = PROMPT_FAILED on the staging_extraction row.
Failed rows are not retried automatically; use --mode=rerun-prompt.

Schema validation:
  - Required top-level keys must be present
  - advisors and consideration_components must be lists
  - go_shop.has_go_shop=false with go_shop_period_days non-null → SCHEMA_VIOLATION
  - Per-advisor: invalid type or advised_party values are skipped with a warning
    rather than failing the entire row
  - deal_attitude / approach_type: null is valid and meaningful (fact not established);
    an out-of-vocabulary non-null value is a SCHEMA_VIOLATION

Spec references: prompts/low_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 7)
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from lib.observation_writer import write_staging_observations_for_extraction
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "low_confidence_extraction"
_VERSION = "0.12"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_REQUIRED_KEYS = frozenset({
    "advisors", "consideration_components", "flags",
    "go_shop", "termination_fees", "model_confidence",
})
# Legacy compatibility vocabularies. Still accepted on input and still written, so rows
# stored before 0.11 stay readable and so anything reading `type` / `advised_party` keeps
# working. `BOTH` is accepted HERE for old responses but is no longer offered by the prompt:
# one advisor serving two participants is two participations, which a single row cannot say.
_VALID_ADVISOR_TYPES = frozenset({"FINANCIAL", "LEGAL", "OTHER"})
_VALID_ADVISED_PARTIES = frozenset({"TARGET", "ACQUIRER", "PARENT_SELLER", "BOTH", "UNKNOWN"})

# V3 advisor participation (prompt 0.11). The specialty the source establishes, at the most
# specific supported level. `financial_advisory`, `legal`, `accounting`, `fairness_opinion`
# and `regulatory` already exist in Grata; `tax`, `proxy_solicitation` and `information_agent`
# were accepted on the strength of being named in the old `OTHER` definition; `communications`
# is a Product addition. `financing_advisory` covers advising on, structuring, arranging or
# placing the transaction's financing -- advice ABOUT capital, which is a different
# participation from supplying it. Its deferred candidate name was `capital_markets`,
# deferred for want of extraction evidence rather than on the semantics; `financing_advisory`
# is the approved name and does not read as a desk. `restructuring` remains deferred.
# A financing PROVIDER is a LENDER, not an advisor specialty, and a firm the source
# establishes in both participations is recorded once in each.
_VALID_ADVISOR_SPECIALTIES = frozenset({
    "financial_advisory", "legal", "accounting", "fairness_opinion", "regulatory",
    "tax", "proxy_solicitation", "information_agent", "communications",
    "financing_advisory",
})
_VALID_ADVISED_SIDES = frozenset({"BUY_SIDE", "SELL_SIDE"})

# Compatibility projection for the legacy `type` column. Only the two specialties the old
# vocabulary could express map to themselves; every other supported specialty projects to
# OTHER, which is what the old contract would have recorded. The projection is lossy by
# construction -- that is the point of the new `specialty` column, which keeps the fact.
_SPECIALTY_TO_LEGACY_TYPE = {"financial_advisory": "FINANCIAL", "legal": "LEGAL"}
# V3 §T11 — two independent nullable dimensions replacing the fused `hostile` boolean.
# null is a valid, meaningful value for both: the source did not establish the fact.
# Component forms were never validated: the prompt listed eight and nothing enforced them,
# so an off-vocabulary spelling ("EARN_OUT", "Earnout") stored silently and then matched
# neither derived filter. consideration_components is the authoritative extraction, so its
# vocabulary is enforced like any other.
_VALID_CONSIDERATION_FORMS = frozenset({
    "CASH", "ACQUIRER_STOCK", "TARGET_STOCK", "EARNOUT", "CVR",
    "CONTINGENT_CONSIDERATION", "DEBT_ASSUMED", "RETAINED_EQUITY", "OTHER",
})
_VALID_DEAL_ATTITUDE = frozenset({"FRIENDLY", "HOSTILE"})
_VALID_APPROACH_TYPE = frozenset({"SOLICITED", "UNSOLICITED"})
_SLEEP = 1.0  # conservative Opus throttle


def _lenders_json(result: dict, log=None, eid=None) -> str:
    """Serialize the lenders array: one item per party stated to PROVIDE financing.

    A name filter, nothing more. There is no lender subtype to validate against -- the
    target model has a `lender_role` with no published vocabulary, and inventing one to
    fill the column would be worse than leaving the distinction uncollected.

    NOTHING IS INFERRED FROM THE ADVISOR LIST, IN EITHER DIRECTION. This reads only what
    the model returned under `lenders`. A firm that arranged financing does not become a
    lender here because arranging appears alongside lending in a sentence, and a lender
    does not become an advisor because lending is a financial service. Both directions
    are the model's judgement under the prompt contract, not this parser's.

    Always returns an array, including "[]" -- most releases name no financing provider,
    and that is a statement worth recording rather than a missing key.
    """
    items = result.get("lenders")
    if not isinstance(items, list):
        if items is not None and log is not None:
            log.warning("extraction_id=%s lenders is not a list: %r -- recorded empty",
                        eid, type(items).__name__)
        return "[]"
    clean: list[dict] = []
    for item in items:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        name = str(name).strip() if name is not None else ""
        if not name:
            if log is not None:
                log.warning("extraction_id=%s dropping lender with no name", eid)
            continue
        clean.append({"name": name})
    return json.dumps(clean, ensure_ascii=False)

def _fmt(v) -> str:
    return str(v) if v is not None else "null"


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"
    if not isinstance(result.get("advisors"), list):
        return "advisors must be a list"
    components = result.get("consideration_components")
    if not isinstance(components, list):
        return "consideration_components must be a list"
    for comp in components:
        if not isinstance(comp, dict):
            return f"consideration component must be an object: {comp!r}"
        form = comp.get("form")
        if form not in _VALID_CONSIDERATION_FORMS:
            return f"invalid consideration component form: {form!r}"
    go_shop = result.get("go_shop") or {}
    if not go_shop.get("has_go_shop", False) and go_shop.get("go_shop_period_days") is not None:
        return "go_shop_period_days must be null when has_go_shop is false"
    flags = result.get("flags") or {}
    for key, vocab in (("deal_attitude", _VALID_DEAL_ATTITUDE),
                       ("approach_type", _VALID_APPROACH_TYPE)):
        value = flags.get(key)
        if value is not None and value not in vocab:
            return f"invalid {key}: {value!r}"
    return None


def _clean_advisors(advisors: list, log, eid: int) -> list[dict]:
    """Normalize advisor participations. Only a nameless entry is dropped.

    An advisor participation is four separate facts: who advised, in what specialty, which
    specific participant they advised, and on which side. They are populated independently
    and an unusable value in one must not discard the others.

    That is a deliberate change from the pre-0.11 behaviour, which skipped the whole entry
    when `type` or `advised_party` was unrecognized. Under that rule a newly-supported
    specialty arriving from a newer prompt would have silently deleted the advisor -- name
    included -- with only a log line. The name is the irreducible fact, so it is the only
    thing whose absence drops a row.

    Neither identity nor side is ever manufactured from the other. A participant name is not
    evidence of a side and a side is not evidence of a participant; each stays None unless the
    source established it.
    """
    valid = []
    for a in advisors:
        if not isinstance(a, dict):
            log.warning("extraction_id=%d skipping non-dict advisor: %r", eid, a)
            continue
        name = (a.get("name") or "").strip()
        if not name:
            log.warning("extraction_id=%d skipping advisor with empty name", eid)
            continue

        specialty = a.get("advisor_specialty") or a.get("specialty")
        if specialty is not None and specialty not in _VALID_ADVISOR_SPECIALTIES:
            log.warning("extraction_id=%d unsupported advisor specialty %r for %r — keeping "
                        "the participation, dropping the specialty", eid, specialty, name)
            specialty = None

        side = a.get("advised_side")
        if side is not None and side not in _VALID_ADVISED_SIDES:
            log.warning("extraction_id=%d invalid advised_side %r for %r — clearing",
                        eid, side, name)
            side = None

        party_name = (a.get("advised_party_name") or "").strip() or None

        # Legacy `type`: projected from specialty when one is established, otherwise the
        # value the response carried. Never invented -- an entry with neither is OTHER,
        # which is exactly what the old contract recorded for anything it could not name.
        atype = a.get("type")
        if specialty is not None:
            atype = _SPECIALTY_TO_LEGACY_TYPE.get(specialty, "OTHER")
        elif atype not in _VALID_ADVISOR_TYPES:
            if atype is not None:
                log.warning("extraction_id=%d invalid advisor type %r for %r — recording as "
                            "OTHER", eid, atype, name)
            atype = "OTHER"

        # Legacy `advised_party`: a ROLE, which the 0.11 contract no longer asks for. It is
        # taken from the response when present and valid, and is otherwise UNKNOWN. It is
        # NOT synthesized from `advised_party_name` or from `advised_side` -- neither
        # establishes which participant role the client holds, and asserting one would state
        # a fact the source did not.
        party = a.get("advised_party")
        if party not in _VALID_ADVISED_PARTIES:
            if party is not None:
                log.warning("extraction_id=%d invalid advised_party %r for %r — recording as "
                            "UNKNOWN", eid, party, name)
            party = "UNKNOWN"

        valid.append({
            "name": name,
            "type": atype,
            "advised_party": party,
            "specialty": specialty,
            "advised_party_name": party_name,
            "advised_side": side,
        })
    return valid


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract advisors, consideration components, and deal flags.

    Returns
    -------
    dict
        Keys: eligible_total, lc_extracted, failed, advisors_inserted
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               se.deal_type, se.target_type, se.event_type, se.v2_event_type,
               se.event_history_type,
               se.value_amount, se.value_currency, se.value_type,
               se.notes,
               sr.source_type, sr.title, sr.clean_text
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status IN ('HC_EXTRACTED', 'SEC_NOT_TRIGGERED', 'SEC_ENRICHED')
        """
    ).fetchall()

    total = len(rows)
    lc_extracted = failed = advisors_inserted = 0
    log.info("Stage 7: %d rows to extract", total)

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            deal_type=_fmt(row["deal_type"]),
            target_type=_fmt(row["target_type"]),
            event_type=_fmt(row["event_type"]),
            v2_event_type=_fmt(row["v2_event_type"]),
            event_history_type=_fmt(row["event_history_type"]),
            value_amount=_fmt(row["value_amount"]),
            value_currency=_fmt(row["value_currency"]),
            value_type=_fmt(row["value_type"]),
            title=title,
            clean_text=body,
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="opus",
                temperature=0.0,
                max_tokens=2048,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                extraction_id=eid,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("extraction_id=%d prompt failed: %s", eid, exc)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        err = _validate(result)
        if err:
            log.warning("extraction_id=%d schema violation: %s — PROMPT_FAILED", eid, err)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        flags = result.get("flags") or {}
        go_shop = result.get("go_shop") or {}
        fees = result.get("termination_fees") or {}

        # Merge LC notes into existing notes dict under "lc" key
        nd: dict = {}
        if row["notes"]:
            try:
                nd = json.loads(row["notes"])
                if not isinstance(nd, dict):
                    nd = {}
            except (ValueError, TypeError):
                nd = {}
        lc_notes = result.get("notes")
        if lc_notes:
            nd["lc"] = lc_notes

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            UPDATE staging_extraction SET
                status = 'LC_EXTRACTED',
                consideration_components = ?,
                deal_attitude = ?,
                approach_type = ?,
                competing_bid = ?,
                regulatory_approvals_required = ?,
                has_go_shop = ?,
                go_shop_period_days = ?,
                target_fee_amount = ?,
                target_fee_percentage = ?,
                acquirer_fee_amount = ?,
                acquirer_fee_percentage = ?,
                lenders = ?,
                model_confidence = ?,
                lc_prompt_version = ?,
                notes = ?,
                updated_at = ?
            WHERE extraction_id = ?
            """,
            (
                json.dumps(result.get("consideration_components") or []),
                # V3 §T11: three-state. None must stay None — "not established" is not
                # "friendly". Do NOT coerce these to a default the way the flags below are.
                flags.get("deal_attitude"),
                flags.get("approach_type"),
                # competing_bid stays a coerced boolean by decision: it names a single fact
                # whose prompt contract is "false otherwise", unlike the two fields above.
                1 if flags.get("competing_bid") else 0,
                1 if flags.get("regulatory_approvals_required") else 0,
                1 if go_shop.get("has_go_shop") else 0,
                go_shop.get("go_shop_period_days"),
                fees.get("target_fee_amount"),
                fees.get("target_fee_percentage"),
                fees.get("acquirer_fee_amount"),
                fees.get("acquirer_fee_percentage"),
                _lenders_json(result, log, eid),
                result.get("model_confidence"),
                _VERSION,
                json.dumps(nd),
                now,
                eid,
            ),
        )
        write_staging_observations_for_extraction(
            conn,
            eid,
            observation_source_stage="LC_EXTRACT",
            include_lc=True,
        )
        conn.commit()

        # Insert advisor rows; bad entries already filtered by _clean_advisors
        clean_advisors = _clean_advisors(result.get("advisors") or [], log, eid)
        for adv in clean_advisors:
            conn.execute(
                "INSERT INTO advisor (extraction_id, name, type, advised_party,"
                " specialty, advised_party_name, advised_side) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (eid, adv["name"], adv["type"], adv["advised_party"],
                 adv["specialty"], adv["advised_party_name"], adv["advised_side"]),
            )
        conn.commit()
        advisors_inserted += len(clean_advisors)

        lc_extracted += 1
        log.info(
            "extraction_id=%d LC_EXTRACTED  advisors=%d  components=%d  regulatory=%s",
            eid,
            len(clean_advisors),
            len(result.get("consideration_components") or []),
            flags.get("regulatory_approvals_required"),
        )
        time.sleep(_SLEEP)

    log.info(
        "Stage 7 done  total=%d lc_extracted=%d failed=%d advisors=%d",
        total, lc_extracted, failed, advisors_inserted,
    )
    return {
        "eligible_total": total,
        "lc_extracted": lc_extracted,
        "failed": failed,
        "advisors_inserted": advisors_inserted,
    }


def _mark_failed(conn: sqlite3.Connection, extraction_id: int) -> None:
    conn.execute(
        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
        (datetime.now(timezone.utc).isoformat(), extraction_id),
    )
    conn.commit()
