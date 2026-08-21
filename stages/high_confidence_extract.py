"""
Stage 4: high_confidence_extract

Runs the Opus high-confidence extraction prompt on every staging_extraction
row with status = CLASSIFIED.  Updates the row in place with extracted fields
and transitions status to HC_EXTRACTED.

On PROMPT_FAILED, sets status = PROMPT_FAILED on the existing staging row.
Failed rows are not automatically retried; use --mode=rerun-prompt.

Notes field after this stage merges Stage 3's "dt" key with Stage 4's "hc":
    {"dt": "<Stage 3 notes>", "hc": "<Stage 4 notes>"}
The "hc" key is only added when the model returns non-null notes.

Schema validation enforced:
  - Required top-level keys must be present
  - value.type must be in: EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE,
    MARKET_CAPITALIZATION, UNDISCLOSED

Spec references: prompts/high_confidence_extraction.md,
                 specs/pipeline.md §2 (Stage 4)
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

_PROMPT_NAME = "high_confidence_extraction"
_VERSION = "0.23"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_REQUIRED_KEYS = frozenset({
    "target",
    "acquirer",
    "parent_seller",
    "dates",
    "value",
    "value_observations",
    "features",
    "target_financials",
    "model_confidence",
    "deal",
    "financials_disclosure_status",
})
# MARKET_CAPITALIZATION (prompt 0.18) is a whole-company observation, deliberately
# outside the stake-level consideration vocabulary. It must be listed here or a 0.18
# extraction emitting it is rejected wholesale rather than merely ignored.
_VALID_VALUE_TYPES = frozenset({
    "EQUITY_VALUE", "TRANSACTION_VALUE", "ENTERPRISE_VALUE",
    "MARKET_CAPITALIZATION", "UNDISCLOSED",
})
_VALID_ACQUIRER_TYPES_V2 = frozenset({
    "strategic_corporate", "private_equity", "pe_portfolio", "venture_capital",
    "growth_equity", "sovereign_wealth_fund", "pension_fund", "hedge_fund",
    "family_office", "individual", "management", "employee_group", "spac",
    "consortium", "other_financial_sponsor", "unknown",
})
_VALID_PERIOD_TYPES_V2 = frozenset({"LTM", "NTM", "ANNUAL", "QUARTERLY", "INTERIM_YTD"})
_VALID_DATE_PRECISIONS = frozenset({"exact", "month", "quarter", "year"})
_VALID_FINANCIALS_DISCLOSURE = frozenset({"DISCLOSED", "UNDISCLOSED", "UNKNOWN"})
_VALID_CONSIDERATION_TYPES = frozenset({"cash", "stock", "cash_and_stock", "election", "other"})
# V3 §T13 — subordinate to target_type = assets. Answers what kind of asset is being
# transacted, NOT the target's sector: a pipeline is INFRASTRUCTURE because that is the
# thing transacted, whoever buys it. Settled and extensible; do not widen speculatively.
_VALID_ASSET_TYPES = frozenset({
    "REAL_ESTATE", "INFRASTRUCTURE", "ENERGY", "NATURAL_RESOURCES",
    "INTELLECTUAL_PROPERTY", "DATA", "FACILITY", "EQUIPMENT",
    "CONTRACTS_OR_RIGHTS", "BRAND_OR_PRODUCT", "OTHER",
})
# V3 §T12 — deliberately one value plus null. MANDATORY_OFFER, SCHEME_OF_ARRANGEMENT,
# ONE_STEP_MERGER and TWO_STEP_MERGER are excluded by that decision, not pending.
_VALID_OFFER_MECHANISM = frozenset({"TENDER_OFFER"})
# V3 §T7 (S-G). Two values plus null, by decision. Vocabulary only -- there is deliberately
# no cross-field guard: this is independent of acquirer.type and must never be derived from
# it, and it may coexist with is_secondary_buyout, which stays orthogonal.
_VALID_SPONSOR_TRANSACTION_ROLE = frozenset({"PLATFORM", "ADD_ON"})
_VALID_STAKE_TRANSITION_TYPES = frozenset({
    "NEW_MINORITY_STAKE",
    "NEW_MAJORITY_STAKE",
    "FULL_ACQUISITION",
    "MINORITY_ACQUIRING_MAJORITY",
    "MAJORITY_ACQUIRE_REMAINING",
    "MINORITY_ACQUIRING_REMAINING",
    "MAJORITY_INCREASING_STAKE",
    "MINORITY_INCREASING_STAKE",
})
_VALUE_OBSERVATION_KEYS = frozenset({"amount", "currency", "type", "basis", "qualifier", "evidence"})

# Normalize legacy uppercase acquirer_type values to V2 lowercase
_LEGACY_ACQUIRER_TYPE_MAP: dict[str, str] = {
    "STRATEGIC_CORPORATE": "strategic_corporate",
    "PRIVATE_EQUITY": "private_equity",
    "PE_PORTFOLIO": "pe_portfolio",
    "VENTURE_CAPITAL": "venture_capital",
    "GROWTH_EQUITY": "growth_equity",
    "SOVEREIGN_WEALTH_FUND": "sovereign_wealth_fund",
    "PENSION_FUND": "pension_fund",
    "HEDGE_FUND": "hedge_fund",
    "FAMILY_OFFICE": "family_office",
    "INDIVIDUAL": "individual",
    "MANAGEMENT": "management",
    "EMPLOYEE_GROUP": "employee_group",
    "SPAC": "spac",
    "CONSORTIUM": "consortium",
    "OTHER_FINANCIAL_SPONSOR": "other_financial_sponsor",
    "UNKNOWN": "unknown",
}

# Normalize legacy period_type values to V2
_LEGACY_PERIOD_TYPE_MAP: dict[str, str | None] = {
    "FY": "ANNUAL",
    "TTM": "LTM",
    "CY": "ANNUAL",
    "QUARTER": "QUARTERLY",
    "UNKNOWN": None,  # unknown period → null in V2
}

_SLEEP = 1.0  # conservative Opus throttle


def _normalize_acquirer_type(raw: str | None) -> str | None:
    """Normalize acquirer.type to V2 lowercase vocabulary."""
    if raw is None:
        return None
    if raw in _VALID_ACQUIRER_TYPES_V2:
        return raw
    return _LEGACY_ACQUIRER_TYPE_MAP.get(raw, raw)


def _normalize_period_type(raw: str | None) -> str | None:
    """Normalize period_type to V2 vocabulary. Returns None for UNKNOWN/unmapped."""
    if raw is None:
        return None
    if raw in _VALID_PERIOD_TYPES_V2:
        return raw
    if raw in _LEGACY_PERIOD_TYPE_MAP:
        return _LEGACY_PERIOD_TYPE_MAP[raw]
    return raw


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"

    vtype = (result.get("value") or {}).get("type")
    if vtype is not None and vtype not in _VALID_VALUE_TYPES:
        return f"invalid value.type: {vtype!r}"
    value_observations = result.get("value_observations")
    if not isinstance(value_observations, list):
        return "invalid value_observations: expected list"
    for i, obs in enumerate(value_observations):
        if not isinstance(obs, dict):
            return f"invalid value_observations[{i}]: expected object"
        unknown = set(obs) - _VALUE_OBSERVATION_KEYS
        if unknown:
            return f"invalid value_observations[{i}] keys: {unknown}"
        otype = obs.get("type")
        if otype is not None and otype not in _VALID_VALUE_TYPES:
            return f"invalid value_observations[{i}].type: {otype!r}"
    features = result.get("features")
    if not isinstance(features, dict):
        return "invalid features: expected object"
    # is_platform_investment is NOT here (V3 §T7, prompt 0.21). It is replaced by
    # deal.sponsor_transaction_role; the column is retained and simply stops being written.
    for feature_name in ("is_secondary_buyout", "is_merger_of_equals"):
        value = features.get(feature_name)
        if value is not None and not isinstance(value, bool):
            return f"invalid features.{feature_name}: expected boolean or null"

    # financials_disclosure_status — required, must be valid
    fds = result.get("financials_disclosure_status")
    if fds not in _VALID_FINANCIALS_DISCLOSURE:
        return f"invalid financials_disclosure_status: {fds!r}"

    # consideration_type — optional but must be valid if present
    ct = result.get("consideration_type")
    if ct is not None and ct not in _VALID_CONSIDERATION_TYPES:
        return f"invalid consideration_type: {ct!r}"

    stt = (result.get("deal") or {}).get("stake_transition_type")
    if stt is not None and stt not in _VALID_STAKE_TRANSITION_TYPES:
        return f"invalid deal.stake_transition_type: {stt!r}"

    # offer_mechanism — vocabulary only. Unlike asset_type there is deliberately NO
    # cross-field guard: a tender offer legitimately coexists with a back-end merger,
    # so nothing may suppress it on the basis of another field.
    om = (result.get("deal") or {}).get("offer_mechanism")
    if om is not None and om not in _VALID_OFFER_MECHANISM:
        return f"invalid deal.offer_mechanism: {om!r}"

    # sponsor_transaction_role (V3 §T7) -- vocabulary only, same reasoning as above.
    str_role = (result.get("deal") or {}).get("sponsor_transaction_role")
    if str_role is not None and str_role not in _VALID_SPONSOR_TRANSACTION_ROLE:
        return f"invalid deal.sponsor_transaction_role: {str_role!r}"

    # asset_type — vocabulary check here; the subordination rule (null unless
    # target_type = assets) is enforced at the write, where target_type is in scope.
    at = (result.get("target") or {}).get("asset_type")
    if at is not None and at not in _VALID_ASSET_TYPES:
        return f"invalid target.asset_type: {at!r}"

    # date_precision fields — optional but must be valid if present
    dates = result.get("dates") or {}
    for prec_field in ("announced_date_precision", "closed_date_precision", "signing_date_precision"):
        prec = dates.get(prec_field)
        if prec is not None and prec not in _VALID_DATE_PRECISIONS:
            return f"invalid {prec_field}: {prec!r}"

    # acquirer.type — warn on legacy uppercase but don't reject during migration
    # (model may still output old values while prompt rolls out)
    # Normalization happens in _normalize_acquirer_type() below.

    # period_type — warn on legacy values but don't reject; normalized below
    # Validation of V2 values happens post-normalization at write time.

    return None


def _value_observations_json(txn: dict) -> str | None:
    items = txn.get("value_observations")
    if not isinstance(items, list):
        return None
    clean = []
    for item in items:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        vtype = item.get("type")
        if amount is None and vtype is None:
            continue
        clean.append({
            "amount": amount,
            "currency": item.get("currency"),
            "type": vtype,
            "basis": item.get("basis"),
            "qualifier": item.get("qualifier"),
            "evidence": item.get("evidence"),
        })
    return json.dumps(clean, ensure_ascii=False)


def _primary_value(txn: dict) -> dict:
    """Return the compatibility value object, sourced from the first typed fact when present."""
    legacy = txn.get("value") or {}
    items = txn.get("value_observations")
    if isinstance(items, list) and items:
        first = items[0] if isinstance(items[0], dict) else {}
        if first:
            return {
                "amount": first.get("amount"),
                "currency": first.get("currency"),
                "type": first.get("type"),
                "type_confidence": legacy.get("type_confidence"),
                "qualifier": first.get("qualifier"),
                "per_share_price": legacy.get("per_share_price"),
            }
    return legacy


def _fmt(v) -> str:
    """Render a nullable value as its string representation for prompt templates."""
    return str(v) if v is not None else "null"


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Extract high-confidence fields for classified staging rows.

    Returns
    -------
    dict
        Keys: classified_total, hc_extracted, extracted, hc_failed, failures
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT se.extraction_id, se.source_raw_id,
               se.deal_type, se.v2_event_type,
               se.spin_split_type, se.spin_split_type_v2,
               se.distribution_mechanism, se.recap_type,
               se.target_type, se.target_type_v2,
               se.event_type, se.event_history_type,
               se.target_status,
               se.notes, se.dt_prompt_version,
               sr.source_type, sr.source_tier, sr.title, sr.clean_text, sr.published_date
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'CLASSIFIED'
          AND COALESCE(se.v2_event_type, se.deal_type) NOT IN ('VC_ROUND', 'GROWTH_EQUITY', 'VENTURE_DEBT')
        """
    ).fetchall()

    total = len(rows)
    extracted = failed = 0
    log.info("Stage 4: %d rows to extract", total)

    for row in rows:
        eid = row["extraction_id"]
        title = (row["title"] or "").replace("{", "{{").replace("}", "}}")
        body = (row["clean_text"] or "").replace("{", "{{").replace("}", "}}")

        user_prompt = prompt["user_template"].format(
            source_type=row["source_type"] or "",
            source_tier=row["source_tier"] or "",
            # Pass v2_event_type when available, fall back to deal_type
            deal_type=_fmt(row["v2_event_type"] or row["deal_type"]),
            spin_split_type=_fmt(row["spin_split_type_v2"] or row["spin_split_type"]),
            target_type=_fmt(row["target_type_v2"] or row["target_type"]),
            event_type=_fmt(row["event_history_type"] or row["event_type"]),
            target_status=_fmt(row["target_status"]),
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
                extraction_id=eid,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("extraction_id=%d prompt failed: %s", eid, exc)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        # Parse transactions array; fall back gracefully if model returns legacy single-object
        transactions = result.get("transactions")
        if not isinstance(transactions, list):
            log.warning("extraction_id=%d response missing 'transactions' key — wrapping as single element", eid)
            transactions = [result]
        if not transactions:
            log.warning("extraction_id=%d empty transactions array — PROMPT_FAILED", eid)
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        multi_total = len(transactions)

        # Validate all transaction elements before writing any
        had_error = False
        for i, txn in enumerate(transactions):
            err = _validate(txn)
            if err:
                log.warning("extraction_id=%d txn[%d] schema violation: %s — PROMPT_FAILED", eid, i, err)
                had_error = True
                break
        if had_error:
            _mark_failed(conn, eid)
            failed += 1
            time.sleep(_SLEEP)
            continue

        # Parse Stage 3 notes once — shared base for all transactions from this source row
        base_nd: dict = {}
        if row["notes"]:
            try:
                base_nd = json.loads(row["notes"])
                if not isinstance(base_nd, dict):
                    base_nd = {}
            except (ValueError, TypeError):
                base_nd = {}

        now = datetime.now(timezone.utc).isoformat()

        for i, txn in enumerate(transactions):
            t = txn.get("target") or {}
            a = txn.get("acquirer") or {}
            ps = txn.get("parent_seller") or {}
            d = txn.get("dates") or {}
            v = _primary_value(txn)
            features = txn.get("features") or {}
            tf = txn.get("target_financials") or {}
            value_observations_json = _value_observations_json(txn)

            nd = dict(base_nd)
            hc_notes = txn.get("notes")
            if hc_notes:
                nd["hc"] = hc_notes

            # asset_type is subordinate to target_type = assets (V3 §T13). Enforce it here
            # rather than in _validate: the validator sees only the model response, while
            # target_type comes from Stage 3 on the row. A value supplied for any other
            # target type is dropped and logged -- the same treatment the prompt's own
            # parser rules give sponsor_name on a non-sponsor acquirer.
            effective_target_type = row["target_type_v2"] or row["target_type"]
            asset_type = t.get("asset_type")
            if asset_type is not None and effective_target_type != "assets":
                log.warning(
                    "extraction_id/source_raw_id=%s asset_type=%r supplied for "
                    "target_type=%r — clearing; asset_type is valid only for assets",
                    row["source_raw_id"], asset_type, effective_target_type,
                )
                asset_type = None

            # Normalize V2 fields
            acquirer_type_raw = a.get("type")
            acquirer_type_v2 = _normalize_acquirer_type(acquirer_type_raw)
            rev_period_v2 = _normalize_period_type(tf.get("revenue_period_type"))
            ebitda_period_v2 = _normalize_period_type(tf.get("ebitda_period_type"))

            params = (
                t.get("name"), t.get("domain"), t.get("ticker"),
                t.get("description"), asset_type,
                a.get("name"), a.get("domain"), a.get("ticker"),
                acquirer_type_raw,   # acquirer_type — legacy column, keep as-is
                acquirer_type_v2,    # acquirer_type_v2 — new V2 column
                a.get("description"),
                a.get("sponsor_name"),
                ps.get("name"), ps.get("ticker"),
                ps.get("description"),
                (txn.get("deal") or {}).get("pct_acquired"),
                (txn.get("deal") or {}).get("stake_transition_type"),
                (txn.get("deal") or {}).get("offer_mechanism"),
                (txn.get("deal") or {}).get("sponsor_transaction_role"),
                features.get("is_secondary_buyout"),
                features.get("is_merger_of_equals"),
                d.get("announced_date"), d.get("closed_date"), d.get("signing_date"),
                d.get("announced_date_precision"),   # new
                d.get("closed_date_precision"),      # new
                d.get("signing_date_precision"),     # new
                d.get("rumor_date"),                 # new
                v.get("amount"), v.get("currency"), v.get("type"),
                v.get("type_confidence"), v.get("qualifier"), v.get("per_share_price"),
                value_observations_json,
                txn.get("round_size"),   # primary-capital capture (value fields null when set)
                tf.get("revenue_amount"),
                tf.get("revenue_period_type"),       # legacy column
                rev_period_v2,                       # new: target_revenue_period_type_v2
                tf.get("revenue_period_end"),
                tf.get("ebitda_amount"),
                tf.get("ebitda_period_type"),        # legacy column
                ebitda_period_v2,                    # new: target_ebitda_period_type_v2
                tf.get("ebitda_period_end"),
                tf.get("currency"),
                # Point-in-time balance-sheet items. No period_type companion by
                # design — a balance sheet is a position on one date, not a period.
                tf.get("total_debt"),
                tf.get("total_debt_currency"),
                tf.get("cash_st"),
                tf.get("cash_st_currency"),
                tf.get("balance_sheet_as_of_date"),
                txn.get("financials_disclosure_status"),  # new
                txn.get("consideration_type"),            # new (direct from prompt)
                txn.get("model_confidence"), _VERSION, json.dumps(nd) if nd else None,
            )

            if i == 0:
                conn.execute(
                    """
                    UPDATE staging_extraction SET
                        status = 'HC_EXTRACTED',
                        target_name = ?,  target_domain = ?,  target_ticker = ?,
                        target_description = ?,  asset_type = ?,
                        acquirer_name = ?,  acquirer_domain = ?,  acquirer_ticker = ?,
                        acquirer_type = ?,  acquirer_type_v2 = ?,
                        acquirer_description = ?,
                        acquirer_sponsor_name = ?,
                        parent_seller_name = ?,  parent_seller_ticker = ?,
                        parent_seller_description = ?,
                        pct_acquired = ?,  stake_transition_type = ?,  offer_mechanism = ?,
                        sponsor_transaction_role = ?,
                        is_secondary_buyout = ?,  is_merger_of_equals = ?,
                        announced_date = ?,  closed_date = ?,  signing_date = ?,
                        announced_date_precision = ?,  closed_date_precision = ?,  signing_date_precision = ?,
                        rumor_date = ?,
                        value_amount = ?,  value_currency = ?,  value_type = ?,
                        value_type_confidence = ?,  value_qualifier = ?,  per_share_price = ?,
                        value_observations = ?,
                        round_size = ?,
                        target_revenue = ?,
                        target_revenue_period_type = ?,  target_revenue_period_type_v2 = ?,
                        target_revenue_period_end = ?,
                        target_ebitda = ?,
                        target_ebitda_period_type = ?,  target_ebitda_period_type_v2 = ?,
                        target_ebitda_period_end = ?,
                        financials_currency = ?,
                        total_debt = ?,  total_debt_currency = ?,
                        cash_st = ?,  cash_st_currency = ?,
                        balance_sheet_as_of_date = ?,
                        financials_disclosure_status = ?,
                        consideration_type = COALESCE(consideration_type, ?),
                        model_confidence = ?,  hc_prompt_version = ?,  notes = ?,
                        multi_transaction_index = ?,  multi_transaction_total = ?,
                        updated_at = ?
                    WHERE extraction_id = ?
                    """,
                    params + (i, multi_total, now, eid),
                )
                write_staging_observations_for_extraction(
                    conn,
                    eid,
                    observation_source_stage="HC_EXTRACT",
                    include_stage3=True,
                    include_hc=True,
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO staging_extraction (
                        source_raw_id, status,
                        deal_type, v2_event_type,
                        spin_split_type, spin_split_type_v2,
                        distribution_mechanism, recap_type,
                        target_type, target_type_v2,
                        event_type, event_history_type,
                        target_status,
                        target_name, target_domain, target_ticker,
                        target_description, asset_type,
                        acquirer_name, acquirer_domain, acquirer_ticker,
                        acquirer_type, acquirer_type_v2,
                        acquirer_description,
                        acquirer_sponsor_name,
                        parent_seller_name, parent_seller_ticker,
                        parent_seller_description,
                        pct_acquired, stake_transition_type, offer_mechanism,
                        sponsor_transaction_role,
                        is_secondary_buyout, is_merger_of_equals,
                        announced_date, closed_date, signing_date,
                        announced_date_precision, closed_date_precision, signing_date_precision,
                        rumor_date,
                        value_amount, value_currency, value_type,
                        value_type_confidence, value_qualifier, per_share_price,
                        value_observations,
                        round_size,
                        target_revenue,
                        target_revenue_period_type, target_revenue_period_type_v2,
                        target_revenue_period_end,
                        target_ebitda,
                        target_ebitda_period_type, target_ebitda_period_type_v2,
                        target_ebitda_period_end,
                        financials_currency,
                        total_debt, total_debt_currency,
                        cash_st, cash_st_currency,
                        balance_sheet_as_of_date,
                        financials_disclosure_status,
                        consideration_type,
                        model_confidence, hc_prompt_version, notes,
                        dt_prompt_version,
                        multi_transaction_index, multi_transaction_total,
                        created_at, updated_at
                    ) VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (row["source_raw_id"], "HC_EXTRACTED",
                     row["deal_type"], row["v2_event_type"],
                     row["spin_split_type"], row["spin_split_type_v2"],
                     row["distribution_mechanism"], row["recap_type"],
                     row["target_type"], row["target_type_v2"],
                     row["event_type"], row["event_history_type"],
                     row["target_status"])
                    + params
                    + (row["dt_prompt_version"], i, multi_total, now, now),
                )
                write_staging_observations_for_extraction(
                    conn,
                    int(cur.lastrowid),
                    observation_source_stage="HC_EXTRACT",
                    include_stage3=True,
                    include_hc=True,
                )

            log.info(
                "extraction_id=%d txn[%d/%d] HC_EXTRACTED  target=%r  acquirer=%r  value=%s %s",
                eid, i, multi_total - 1, t.get("name"), a.get("name"), v.get("amount"), v.get("currency"),
            )

        conn.commit()
        extracted += 1
        time.sleep(_SLEEP)

    log.info("Stage 4 done  total=%d extracted=%d failed=%d", total, extracted, failed)
    return {
        "classified_total": total,
        "hc_extracted": extracted,
        "extracted": extracted,
        "hc_failed": failed,
        "failures": failed,
    }


def _mark_failed(conn: sqlite3.Connection, extraction_id: int) -> None:
    conn.execute(
        "UPDATE staging_extraction SET status='PROMPT_FAILED', updated_at=? WHERE extraction_id=?",
        (datetime.now(timezone.utc).isoformat(), extraction_id),
    )
    conn.commit()
