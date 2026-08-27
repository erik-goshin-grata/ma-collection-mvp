"""
Stage 4b: funding_hc_extract

Runs the funding high-confidence extraction prompt on every staging_extraction
row with status = CLASSIFIED and v2_event_type IN (VC_ROUND, GROWTH_EQUITY,
VENTURE_DEBT).

Creates or updates staging_extraction with round fields and writes investor
rows to staging_investor. Mirrors the structure of high_confidence_extract.py.

On success: status → HC_EXTRACTED, round/investor fields populated.
On failure: status → PROMPT_FAILED.

Spec references: prompts/funding_hc_extraction.md, docs/funding_path_design.md
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version
from lib.observation_writer import write_staging_observations_for_extraction

_PROMPT_NAME = "funding_hc_extraction"
_VERSION = "0.6"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_FUNDING_EVENT_TYPES = frozenset({"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"})

_REQUIRED_KEYS = frozenset({
    "company", "investors", "round", "dates",
    "financials_disclosure_status", "model_confidence",
})

_VALID_FINANCIALS_DISCLOSURE = frozenset({"DISCLOSED", "UNDISCLOSED", "UNKNOWN"})
# V3 §A6.3 / §T14. Replaces the is_down_round boolean, which could only record DOWN --
# is_up_round never existed -- so 0 fused up, flat and unknown. null stays distinct from
# FLAT: "not stated" and "unchanged" are different facts, so this is NOT coerced.
_VALID_ROUND_PRICE_DIRECTION = frozenset({"UP", "DOWN", "FLAT"})
_VALID_INVESTOR_TYPES = frozenset({
    "vc_firm", "growth_equity", "corporate_vc", "family_office",
    "hedge_fund", "sovereign_wealth_fund", "angel", "accelerator",
    "lender", "unknown",
})

_SLEEP = 1.0


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"

    fds = result.get("financials_disclosure_status")
    if fds not in _VALID_FINANCIALS_DISCLOSURE:
        return f"invalid financials_disclosure_status: {fds!r}"

    investors = result.get("investors")
    if not isinstance(investors, list):
        return "investors must be an array"

    for inv in investors:
        itype = inv.get("investor_type")
        if itype is not None and itype not in _VALID_INVESTOR_TYPES:
            return f"invalid investor_type: {itype!r}"

    rpd = (result.get("round") or {}).get("round_price_direction")
    if rpd is not None and rpd not in _VALID_ROUND_PRICE_DIRECTION:
        return f"invalid round.round_price_direction: {rpd!r}"

    return None


def _fmt(val) -> str:
    return str(val) if val is not None else ""


# A leading approximation word in front of ONE number is hedging language, not a
# second value: "approximately 65%" states 65. The prompt itself tells the model that
# "approximately 65%" and "roughly 65%" are stated, so the parser must accept what the
# contract invites. Closed set -- anything not listed here is not stripped.
_PCT_APPROX_PREFIXES = (
    "approximately", "approx.", "approx", "about", "roughly", "circa", "~",
)

# A bound or a range is NOT a stated value: "at least 65%" and "30-40%" each leave the
# actual percentage unknown, and silently taking the endpoint would invent precision the
# source never gave. Checked before the approximation strip, so "about 30-40%" clears.
_PCT_REJECT_MARKERS = (
    "-", "\u2013", "\u2014", "+", " to ", "between", "at least", "at most", "no less",
    "no more", "more than", "less than", "greater than", "up to", "over ", "under ",
    "above", "below", "minimum", "maximum", "or more", "or less",
)


# `use_of_proceeds` vocabulary (funding_hc_extraction 0.6). Eleven categories plus
# OTHER, enumerated from the phrasing the funding corpus actually uses -- the method
# `advisor_specialty` established -- with ACQUISITIONS, DEBT_REPAYMENT and
# WORKING_CAPITAL added on Product ruling as common, materially distinct uses that
# should not route through OTHER.
_VALID_PROCEEDS_USES = frozenset({
    "HIRING", "PRODUCT_AND_TECHNOLOGY", "MANUFACTURING_AND_SUPPLY_CHAIN",
    "GO_TO_MARKET", "MARKET_EXPANSION", "FACILITIES_AND_EQUIPMENT",
    "REGULATORY_AND_COMPLIANCE", "ACQUISITIONS", "DEBT_REPAYMENT",
    "WORKING_CAPITAL", "GENERAL_CORPORATE", "OTHER",
})


def _clean_proceeds(raw: Any, log: Any, eid: int) -> str | None:
    """Keep the vocabulary values, in the order given, without repeats.

    A bounded vocabulary that is not enforced is not bounded, so the same filter
    `rationale_tag` applies to `secondary_rationales` applies here: values outside
    the taxonomy are dropped and logged rather than stored, and a repeat of a
    category is dropped too -- the field answers which kinds of use were stated,
    and saying one twice adds nothing.

    Clearing, not rejecting, exactly as `_clean_pct` does. A `_validate` failure
    marks the whole extraction PROMPT_FAILED, and losing a funding row over one
    malformed optional field would be out of proportion to the fact lost.

    Case is folded because the vocabulary is the contract, not the capitalization.
    Everything that survives is a value the delivered prompt offered.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    kept: list[str] = []
    dropped: list[str] = []
    for part in text.split(","):
        value = part.strip().upper()
        if not value:
            continue
        if value not in _VALID_PROCEEDS_USES:
            dropped.append(part.strip())
        elif value not in kept:
            kept.append(value)
    if dropped:
        log.warning("extraction_id=%d use_of_proceeds dropping non-vocabulary value(s): %r",
                    eid, dropped)
    if not kept:
        if not dropped:
            log.warning("extraction_id=%d clearing unusable use_of_proceeds: %r", eid, raw)
        return None
    return ", ".join(kept)


def _clean_pct(raw, log, eid: int) -> float | None:
    """Normalize an explicitly stated ownership percentage, or clear it.

    Normalization only, never inference. A plain number, a simple percentage
    string ("65%", "65.0 %"), or one number behind an approximation word
    ("approximately 65%", "approx. 65%", "about 65%") all become 65.0 -- each
    states a single value and the wording is hedging, not a second number.

    Cleared with a warning: ranges and bounds ("30-40%", "at least 65%", "up to
    65%"), zero, negative, above 100, empty strings, and any other non-numeric
    text including bare control language such as "majority".

    Clearing, not rejecting. `_validate` failure marks the whole extraction
    PROMPT_FAILED, and discarding an entire funding row over one malformed
    optional percentage is out of proportion to the fact lost. This follows the
    advisor precedent: an unusable value clears its own field and the rest of
    the extraction stands.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):                      # bool is an int subclass; not a percentage
        log.warning("extraction_id=%d clearing non-numeric pct_acquired: %r", eid, raw)
        return None
    if isinstance(raw, str):
        text = raw.strip().rstrip("%").strip().lower()
        if any(marker in text for marker in _PCT_REJECT_MARKERS):
            log.warning("extraction_id=%d clearing bounded or ranged pct_acquired: %r",
                        eid, raw)
            return None
        for prefix in _PCT_APPROX_PREFIXES:        # longest-first; see the tuple order
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        try:
            value = float(text)
        except ValueError:
            log.warning("extraction_id=%d clearing unparseable pct_acquired: %r", eid, raw)
            return None
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        log.warning("extraction_id=%d clearing non-numeric pct_acquired: %r", eid, raw)
        return None

    # 0 is not a stake and 100+ is either a whole-company buy the source should have
    # stated differently or a parse error. Both clear rather than propagate.
    if not (0.0 < value <= 100.0):
        log.warning("extraction_id=%d clearing out-of-range pct_acquired: %r", eid, raw)
        return None
    return value


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Run funding HC extraction on all CLASSIFIED funding rows.

    Returns
    -------
    dict
        Keys: funding_total, funding_extracted, funding_failed
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               se.v2_event_type, se.deal_type,
               se.event_history_type, se.event_type,
               se.notes, se.dt_prompt_version,
               sr.source_type, sr.source_tier,
               sr.title, sr.clean_text, sr.published_date
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'CLASSIFIED'
          AND COALESCE(se.v2_event_type, se.deal_type) IN ('VC_ROUND', 'GROWTH_EQUITY', 'VENTURE_DEBT')
        """
    ).fetchall()

    total = len(rows)
    extracted = failed = 0
    log.info("Stage 4b: %d funding rows to extract", total)

    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        base_nd: dict = {}
        if row["notes"]:
            try:
                base_nd = json.loads(row["notes"])
            except (ValueError, TypeError):
                pass

        v2_event_type = row["v2_event_type"] or row["deal_type"]
        event_history_type = row["event_history_type"] or row["event_type"] or "ANNOUNCED"

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            source_tier=row["source_tier"] or "",
            v2_event_type=_fmt(v2_event_type),
            event_history_type=_fmt(event_history_type),
            published_date=_fmt(row["published_date"]),
            title=title,
            clean_text=body,
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="sonnet",
                temperature=0.0,
                max_tokens=2048,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                source_raw_id=row["source_raw_id"],
                log=log,
            )
        except PromptFailure as exc:
            log.warning("source_raw_id=%d prompt failed: %s", row["source_raw_id"], exc)
            conn.execute(
                "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                (now, eid),
            )
            conn.commit()
            failed += 1
            time.sleep(_SLEEP)
            continue

        transactions = result.get("transactions")
        if not transactions or not isinstance(transactions, list):
            log.warning("source_raw_id=%d no transactions in result — PROMPT_FAILED", row["source_raw_id"])
            conn.execute(
                "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                (now, eid),
            )
            conn.commit()
            failed += 1
            time.sleep(_SLEEP)
            continue

        multi_total = len(transactions)

        for i, txn in enumerate(transactions):
            err = _validate(txn)
            if err:
                log.warning("source_raw_id=%d txn[%d] schema violation: %s", row["source_raw_id"], i, err)
                if i == 0:
                    conn.execute(
                        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
                        (now, eid),
                    )
                    conn.commit()
                    failed += 1
                break

            co = txn.get("company") or {}
            rd = txn.get("round") or {}
            dt = txn.get("dates") or {}
            nd = dict(base_nd)
            hc_notes = txn.get("notes")
            if hc_notes:
                nd["hc"] = hc_notes

            # One cleaned value for both write paths below. The INSERT path builds its
            # own tuple by design (bug #6), so a value computed inline in round_params
            # would silently never reach a multi-transaction row.
            pct_acquired = _clean_pct(txn.get("pct_acquired"), log, eid)
            use_of_proceeds = _clean_proceeds(txn.get("use_of_proceeds"), log, eid)

            round_params = (
                co.get("name"), co.get("domain"), co.get("ticker"),
                co.get("description"),
                v2_event_type,
                rd.get("label"),
                rd.get("size"),
                rd.get("currency"),
                rd.get("pre_money_valuation"),
                rd.get("post_money_valuation"),
                rd.get("valuation_currency"),
                rd.get("facility_size"),
                rd.get("total_raised_to_date"),
                1 if rd.get("is_extension_round") else 0,
                rd.get("round_price_direction"),   # three-state: None stays None
                1 if rd.get("is_bridge_round") else 0,
                dt.get("announced_date"),
                dt.get("announced_date_precision"),
                dt.get("closed_date"),
                dt.get("closed_date_precision"),
                txn.get("financials_disclosure_status"),
                txn.get("consideration_type"),
                pct_acquired,
                use_of_proceeds,
                txn.get("model_confidence"),
                _VERSION,
                json.dumps(nd) if nd else None,
                i,
                multi_total,
            )

            if i == 0:
                # Update the existing staging_extraction row
                conn.execute(
                    """
                    UPDATE staging_extraction SET
                        status = 'HC_EXTRACTED',
                        target_name = ?,
                        target_domain = ?,
                        target_ticker = ?,
                        target_description = ?,
                        v2_event_type = ?,
                        round_label = ?,
                        round_size = ?,
                        round_currency = ?,
                        pre_money_valuation = ?,
                        post_money_valuation = ?,
                        valuation_currency = ?,
                        facility_size = ?,
                        total_raised_to_date = ?,
                        is_extension_round = ?,
                        round_price_direction = ?,
                        is_bridge_round = ?,
                        announced_date = ?,
                        announced_date_precision = ?,
                        closed_date = ?,
                        closed_date_precision = ?,
                        financials_disclosure_status = ?,
                        consideration_type = COALESCE(consideration_type, ?),
                        pct_acquired = ?,
                        use_of_proceeds = ?,
                        model_confidence = ?,
                        hc_prompt_version = ?,
                        notes = ?,
                        multi_transaction_index = ?,
                        multi_transaction_total = ?,
                        updated_at = ?
                    WHERE extraction_id = ?
                    """,
                    round_params + (now, eid),
                )
            else:
                # Insert a new row for additional transactions from same source
                cur = conn.execute(
                    """
                    INSERT INTO staging_extraction (
                        source_raw_id, status,
                        deal_type, v2_event_type,
                        event_type, event_history_type,
                        target_name, target_domain, target_ticker, target_description,
                        round_label, round_size, round_currency, valuation_currency,
                        pre_money_valuation, post_money_valuation,
                        facility_size, total_raised_to_date,
                        is_extension_round, round_price_direction, is_bridge_round,
                        announced_date, announced_date_precision,
                        closed_date, closed_date_precision,
                        financials_disclosure_status, consideration_type, pct_acquired,
                        use_of_proceeds,
                        model_confidence, hc_prompt_version, notes,
                        dt_prompt_version,
                        multi_transaction_index, multi_transaction_total,
                        created_at, updated_at
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    # Explicit param tuple matching the 37-column list above.
                    # (Do NOT reuse round_params here — that tuple is shaped for the
                    # i==0 UPDATE SET clause and carries extra fields, causing a
                    # binding crash on multi-transaction funding sources. bug #6)
                    (
                        row["source_raw_id"], "HC_EXTRACTED",
                        v2_event_type, v2_event_type,
                        event_history_type, event_history_type,
                        co.get("name"), co.get("domain"), co.get("ticker"), co.get("description"),
                        rd.get("label"), rd.get("size"), rd.get("currency"), rd.get("valuation_currency"),
                        rd.get("pre_money_valuation"), rd.get("post_money_valuation"),
                        rd.get("facility_size"), rd.get("total_raised_to_date"),
                        1 if rd.get("is_extension_round") else 0,
                        rd.get("round_price_direction"),   # three-state: None stays None
                        1 if rd.get("is_bridge_round") else 0,
                        dt.get("announced_date"), dt.get("announced_date_precision"),
                        dt.get("closed_date"), dt.get("closed_date_precision"),
                        txn.get("financials_disclosure_status"), txn.get("consideration_type"),
                        pct_acquired,
                        use_of_proceeds,
                        txn.get("model_confidence"), _VERSION,
                        json.dumps(nd) if nd else None,
                        row["dt_prompt_version"], i, multi_total,
                        now, now,
                    ),
                )
                new_eid = cur.lastrowid

            # Write investors to staging_investor
            target_eid = eid if i == 0 else (cur.lastrowid if i > 0 else eid)

            # Dual-write source-row observations for this funding extraction so the
            # observation read path reaches parity with staging (decision: "Observation
            # Write Path Must Cover Every Field Aggregation Reads"). include_funding
            # carries the round fields (incl. round_size); include_hc/stage3 carry the
            # target + date + classifier fields the funding row also populates (nulls
            # are skipped). transaction_id is filled later by backfill after clustering.
            write_staging_observations_for_extraction(
                conn,
                int(target_eid),
                observation_source_stage="FUNDING_HC_EXTRACT",
                include_stage3=True,
                include_hc=True,
                include_funding=True,
            )
            investors = txn.get("investors") or []
            for inv in investors:
                conn.execute(
                    """
                    INSERT INTO staging_investor (
                        extraction_id, name, domain, investor_type,
                        is_lead, lead_investor_rank,
                        investment_amount, investment_currency,
                        is_new_investor, is_existing_investor,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_eid,
                        inv.get("name"),
                        inv.get("domain"),
                        inv.get("investor_type"),
                        1 if inv.get("is_lead") else 0,
                        inv.get("lead_investor_rank"),
                        inv.get("investment_amount"),
                        inv.get("investment_currency"),
                        1 if inv.get("is_new_investor") else 0,
                        1 if inv.get("is_existing_investor") else 0,
                        now,
                    ),
                )

        conn.commit()
        extracted += 1
        log.info(
            "source_raw_id=%d FUNDING_HC_EXTRACTED  v2_event_type=%s  company=%r  investors=%d",
            row["source_raw_id"], v2_event_type,
            (transactions[0].get("company") or {}).get("name"),
            len(transactions[0].get("investors") or []),
        )
        time.sleep(_SLEEP)

    log.info(
        "Stage 4b done  total=%d extracted=%d failed=%d",
        total, extracted, failed,
    )
    return {
        "funding_total": total,
        "funding_extracted": extracted,
        "funding_failed": failed,
        "failures": failed,
    }
