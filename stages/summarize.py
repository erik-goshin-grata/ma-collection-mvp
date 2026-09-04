"""
Stage 12: summarize

Generates an 80–150 word natural-language summary for each transaction_record
row where is_current = 1 and no current summary exists. Calls the Opus deal
summary prompt with the full aggregated transaction context.

Inserts a new row into summary with is_current = 1; any prior summary rows
for the same transaction_id are flipped to is_current = 0.

Spec references: prompts/deal_summary.md, specs/pipeline.md §2
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "deal_summary"
_VERSION = "0.17"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"
# prompt_version is NOT here: provenance is caller-owned, stamped from _FULL_VERSION
# below. Requiring it rejected otherwise valid responses for omitting a field the
# model should not be authoring.
_REQUIRED_KEYS = frozenset({"summary_text", "word_count", "model_confidence", "notes"})
_SLEEP = 1.0


def _fmt_period(period_type: str | None, period_end: str | None) -> str | None:
    """Format (period_type, period_end) into a human-readable string."""
    if not period_type or period_type == "UNKNOWN":
        return period_end
    if not period_end:
        return period_type
    year = period_end[:4]
    pt = period_type.upper()
    if pt in ("FY", "FISCAL_YEAR"):
        return f"FY{year}"
    if pt in ("CY", "CALENDAR_YEAR"):
        return f"CY{year}"
    if pt in ("LTM", "TTM"):
        return f"{pt} {period_end}"
    if pt == "NTM":
        return f"NTM {period_end}"
    if pt == "QUARTER":
        try:
            month = int(period_end[5:7])
            q = (month - 1) // 3 + 1
            return f"Q{q} {year}"
        except (ValueError, IndexError):
            return f"Q {period_end}"
    return f"{period_type} {period_end}"


def _build_advisors_summary(conn: sqlite3.Connection, cluster_id: str) -> str | None:
    """Build a natural-language advisors sentence from the advisor table."""
    rows = conn.execute(
        """
        SELECT a.name, a.type, a.advised_party, a.advised_party_name, a.advised_side
        FROM advisor a
        JOIN staging_extraction se ON se.extraction_id = a.extraction_id
        WHERE se.transaction_cluster_id = ?
        ORDER BY a.advised_party, a.type, a.name
        """,
        (cluster_id,),
    ).fetchall()

    if not rows:
        return None

    # Prefer the stated client name, then the side, then the legacy role. LC 0.11 stopped
    # asking the model for a role, so `advised_party` is UNKNOWN on new rows unless the
    # response carried one -- reading it alone would render every new advisor as "Unknown"
    # while the row holds the client's actual name. Rows stored before 0.11 have no name or
    # side and still resolve through the legacy role, so nothing regresses.
    def _label(r) -> str:
        name = r["advised_party_name"] if "advised_party_name" in r.keys() else None
        if name:
            return name
        side = r["advised_side"] if "advised_side" in r.keys() else None
        if side:
            return "the buy side" if side == "BUY_SIDE" else "the sell side"
        return (r["advised_party"] or "UNKNOWN").replace("_", " ").title()

    by_party: dict[str, list[str]] = {}
    for r in rows:
        by_party.setdefault(_label(r), []).append(r["name"])

    parts = []
    for label, names in by_party.items():
        unique = list(dict.fromkeys(names))
        parts.append(f"{' and '.join(unique)} advised {label}")

    return "; ".join(parts) + "." if parts else None


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"
    if not result.get("summary_text"):
        return "summary_text is empty"
    return None


def _build_investors_summary(conn: sqlite3.Connection, transaction_id: str) -> list[dict]:
    """Named canonical funding investors for this transaction, lead first.

    Reads the canonical transaction_participant / entity rows materialized by
    lib.investor_participant (called from Stage 9, after transaction_record
    exists) -- not staging_investor directly, so this reflects the same
    deduplicated-across-sources record Stage 9 wrote. [] when the transaction
    has no materialized investors (an M&A transaction, or a funding round
    that named none), not None: a real, meaningful state, not "unknown".
    """
    rows = conn.execute(
        """
        SELECT e.canonical_name AS name, tp.is_lead,
               tp.is_new_investor, tp.is_existing_investor
        FROM transaction_participant tp
        JOIN entity e ON e.entity_id = tp.entity_id
        WHERE tp.transaction_id = ?
          AND tp.participant_role = 'INVESTOR'
          AND tp.is_current = 1
        ORDER BY tp.is_lead DESC, e.canonical_name
        """,
        (transaction_id,),
    ).fetchall()
    return [
        {
            "name": r["name"],
            "is_lead": bool(r["is_lead"]),
            "is_new_investor": (bool(r["is_new_investor"])
                                 if r["is_new_investor"] is not None else None),
            "is_existing_investor": (bool(r["is_existing_investor"])
                                      if r["is_existing_investor"] is not None else None),
        }
        for r in rows
    ]


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Generate deal summaries for current transaction records.

    Returns
    -------
    dict
        Keys: transactions_total, summaries_generated, failed
    """
    log = get_logger("summarize", run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT tr.*
        FROM transaction_record tr
        WHERE tr.is_current = 1
          AND NOT EXISTS (
              SELECT 1 FROM summary s
              WHERE s.transaction_id = tr.transaction_id AND s.is_current = 1
          )
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    total = len(rows)
    summarized = failed = 0
    log.info("Stage 10: %d transactions to summarize", total)

    for tr in rows:
        tid = tr["transaction_id"]
        now = datetime.now(timezone.utc).isoformat()

        rev_period = _fmt_period(tr["target_revenue_period_type"], tr["target_revenue_period_end"])
        ebitda_period = _fmt_period(tr["target_ebitda_period_type"], tr["target_ebitda_period_end"])
        advisors_summary = _build_advisors_summary(conn, tid)

        # V3 §T11 (deal_summary 0.12). `hostile` was retired from Stage 7 when the fused
        # boolean split into deal_attitude and approach_type, so this key had been arriving
        # permanently false — asserting "not hostile" on every deal, including hostile ones.
        # The two canonical fields are passed through as themselves and NOT coerced: they are
        # nullable by design, absence is not FRIENDLY, and json.dumps writes None as null.
        # They are independent dimensions; nothing here derives one from the other.
        flags_json = json.dumps({
            "is_take_private": bool(tr["is_take_private"]),
            "deal_attitude": tr["deal_attitude"],
            "approach_type": tr["approach_type"],
            # V3 §T7 (deal_summary 0.13). Carried as itself for the same reason as the two
            # above: PLATFORM / ADD_ON / null, where null means no sponsor role is
            # established -- not that one is denied. Before this the summary was told the
            # acquirer's type and left to infer the role, which is the derivation §T7 removed.
            "sponsor_transaction_role": tr["sponsor_transaction_role"],
            "competing_bid": bool(tr["competing_bid"]),
            "regulatory_approvals_required": bool(tr["regulatory_approvals_required"]),
        })
        go_shop_json = json.dumps({
            "has_go_shop": bool(tr["has_go_shop"]),
            "go_shop_period_days": tr["go_shop_period_days"],
        })
        fees_json = json.dumps({
            "target_fee_amount": tr["target_fee_amount"],
            "target_fee_percentage": tr["target_fee_percentage"],
            "acquirer_fee_amount": tr["acquirer_fee_amount"],
            "acquirer_fee_percentage": tr["acquirer_fee_percentage"],
        })
        # deal_summary 0.16. Canonical funding fields were fetched by SELECT tr.* and then
        # dropped: the template had no funding placeholder, so a correct canonical round
        # reached the model as nothing at all. With the value block empty -- funding events
        # derive no transaction value, by design, because a round is primary capital rather
        # than a purchase price -- the model met VALUE FRAMING's UNDISCLOSED line and
        # asserted "Financial terms were not disclosed" on rounds whose size, valuation and
        # total-raised were all correctly stored.
        #
        # Passed through as themselves, deliberately. No bool(), no or-default, no
        # coercion: json.dumps writes None as null, and null here means the fact is not
        # established -- which is not the same as a disclosed zero or a denial. This is the
        # same reasoning recorded above for deal_attitude/approach_type, and the same
        # mistake bool() made of has_go_shop.
        funding_json = json.dumps({
            "round_label": tr["round_label"],
            "round": tr["round"],
            "vc_stage": tr["vc_stage"],
            "round_size": tr["round_size"],
            "round_currency": tr["round_currency"],
            "pre_money_valuation": tr["pre_money_valuation"],
            "post_money_valuation": tr["post_money_valuation"],
            "valuation_currency": tr["valuation_currency"],
            "facility_size": tr["facility_size"],
            "total_raised_to_date": tr["total_raised_to_date"],
            "round_price_direction": tr["round_price_direction"],
            "is_extension_round": tr["is_extension_round"],
            "is_bridge_round": tr["is_bridge_round"],
            "use_of_proceeds": tr["use_of_proceeds"],
            # Canonical, deduplicated-across-sources named investors (see
            # lib.investor_participant). [] when none were named -- plumbing
            # only; the prompt is not yet updated to narrate this (deferred).
            "investors": _build_investors_summary(conn, tid),
        })

        def _f(v) -> str:
            return str(v) if v is not None else "null"

        user_prompt = prompt["user_template"].format(
            deal_type=_f(tr["deal_type"]),
            v2_event_type=_f(tr["v2_event_type"]),
            event_history_type=_f(tr["event_history_type"]),
            recap_type=_f(tr["recap_type"]),
            combination_structure=_f(tr["combination_structure"]),
            spin_split_type=_f(tr["spin_split_type"]),
            distribution_mechanism=_f(tr["distribution_mechanism"]),
            event_type=_f(tr["event_type"]),
            target_type=_f(tr["target_type"]),
            target_status=_f(tr["target_status"]),
            announced_date=_f(tr["announced_date"]),
            closed_date=_f(tr["closed_date"]),
            target_name=_f(tr["target_name"]),
            target_ticker=_f(tr["target_ticker"]),
            target_description=_f(tr["target_description"]),
            acquirer_name=_f(tr["acquirer_name"]),
            acquirer_type=_f(tr["acquirer_type"]),
            acquirer_description=_f(tr["acquirer_description"]),
            acquirer_sponsor_name=_f(tr["acquirer_sponsor_name"]),
            pct_acquired=_f(tr["pct_acquired"]),
            parent_seller_name=_f(tr["parent_seller_name"]),
            parent_seller_ticker=_f(tr["parent_seller_ticker"]),
            parent_seller_description=_f(tr["parent_seller_description"]),
            value_amount=_f(tr["value_amount"]),
            value_currency=_f(tr["value_currency"]),
            value_type=_f(tr["value_type"]),
            per_share_price=_f(tr["per_share_price"]),
            consideration_type=_f(tr["consideration_type"]),
            consideration_components_json=tr["consideration_components"] or "[]",
            flags_json=flags_json,
            go_shop_json=go_shop_json,
            termination_fees_json=fees_json,
            target_revenue=_f(tr["target_revenue"]),
            target_revenue_period=_f(rev_period),
            target_ebitda=_f(tr["target_ebitda"]),
            target_ebitda_period=_f(ebitda_period),
            ev_to_revenue_ltm=_f(tr["ev_to_revenue_ltm"]),
            ev_to_revenue_ntm=_f(tr["ev_to_revenue_ntm"]),
            ev_to_ebitda_ltm=_f(tr["ev_to_ebitda_ltm"]),
            ev_to_ebitda_ntm=_f(tr["ev_to_ebitda_ntm"]),
            multiple_quality=_f(tr["multiple_quality"]),
            funding_json=funding_json,
            # Sent for every deal type, not just funding. It is the only affirmative
            # disclosure signal the summary has ever had; without it the prompt could only
            # infer non-disclosure from absent input, which is what produced the false
            # claims. Its canonical meaning is narrow and the prompt says so: DISCLOSED is
            # "at least one financial value is stated", NOT "all terms are known".
            financials_disclosure_status=_f(tr["financials_disclosure_status"]),
            transaction_terms_disclosure_status=_f(
                tr["transaction_terms_disclosure_status"]),
            advisors_summary=_f(advisors_summary),
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="sonnet",
                temperature=0.3,
                max_tokens=768,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("transaction_id=%s summary prompt failed: %s", tid, exc)
            failed += 1
            time.sleep(_SLEEP)
            continue

        err = _validate(result)
        if err:
            log.warning("transaction_id=%s schema violation: %s — skipping", tid, err)
            failed += 1
            time.sleep(_SLEEP)
            continue

        word_count = result.get("word_count")
        if word_count and (word_count < 50 or word_count > 500):
            log.warning(
                "transaction_id=%s word_count=%s outside 80–150 range — accepting",
                tid, word_count,
            )

        conn.execute(
            "UPDATE summary SET is_current = 0 WHERE transaction_id = ? AND is_current = 1",
            (tid,),
        )
        conn.execute(
            """
            INSERT INTO summary (transaction_id, summary_text, word_count, is_current,
                                 prompt_version, model_confidence, notes, created_at)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                tid,
                result["summary_text"],
                word_count,
                _FULL_VERSION,
                result.get("model_confidence"),
                result.get("notes"),
                now,
            ),
        )
        conn.commit()

        summarized += 1
        log.info(
            "transaction_id=%s summarized  words=%s  confidence=%s",
            tid, word_count, result.get("model_confidence"),
        )
        time.sleep(_SLEEP)

    log.info("Stage 10 done  total=%d summarized=%d failed=%d", total, summarized, failed)
    return {"transactions_total": total, "summaries_generated": summarized, "failed": failed}
