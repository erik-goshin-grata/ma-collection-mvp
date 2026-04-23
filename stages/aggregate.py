"""
Stage 9: aggregate

For each transaction cluster (CLUSTERED staging_extraction rows sharing a
transaction_cluster_id), applies deterministic tier rules (T1 > T2 > T3) to
resolve field values from all cluster members into a single canonical value.

When two sources of equal tier disagree on the same field, the aggregation
prompt is called once for that specific field. All LLM conflict resolutions
are logged to aggregation_conflict_log.

After field resolution:
  - consideration_type is derived from consideration_components
  - is_take_private / is_add_on / is_divestiture are derived from deal context
  - A transaction_record row is upserted (INSERT or UPDATE in place)
  - transaction_source rows are inserted linking the transaction to its sources
  - All cluster members transition to status = AGGREGATED

Tier mapping (from source_raw.source_tier):
  T1: SEC filings (SEC_8K_ITEM_*. SEC_EXHIBIT_*)  — most authoritative
  T2: PR_NEWSWIRE                                  — standard
  T3: (future sources)                             — advisory only

Spec references: prompts/aggregation.md, specs/pipeline.md §2 (Stage 9)
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "aggregation"
_VERSION = "0.2"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_TIER_ORDER = ("T1", "T2", "T3")

# Fields aggregated from staging_extraction → transaction_record.
# Each entry: (field_name, field_type)
_FIELDS = [
    ("deal_type", "string"),
    ("spin_split_type", "string"),
    ("distribution_mechanism", "string"),
    ("target_type", "string"),
    ("event_type", "string"),
    ("target_status", "string"),
    ("target_name", "string"),
    ("target_domain", "string"),
    ("target_ticker", "string"),
    ("acquirer_name", "string"),
    ("acquirer_domain", "string"),
    ("acquirer_ticker", "string"),
    ("acquirer_type", "string"),
    ("parent_seller_name", "string"),
    ("parent_seller_ticker", "string"),
    ("announced_date", "date"),
    ("closed_date", "date"),
    ("signing_date", "date"),
    ("value_amount", "number"),
    ("value_currency", "string"),
    ("value_type", "string"),
    ("per_share_price", "number"),
    ("target_revenue", "number"),
    ("target_revenue_period_type", "string"),
    ("target_revenue_period_end", "date"),
    ("target_ebitda", "number"),
    ("target_ebitda_period_type", "string"),
    ("target_ebitda_period_end", "date"),
    ("financials_currency", "string"),
    ("consideration_components", "json"),
    ("includes_earnout", "boolean"),
    ("hostile", "boolean"),
    ("competing_bid", "boolean"),
    ("regulatory_approvals_required", "boolean"),
    ("has_go_shop", "boolean"),
    ("go_shop_period_days", "number"),
    ("target_fee_amount", "number"),
    ("target_fee_percentage", "number"),
    ("acquirer_fee_amount", "number"),
    ("acquirer_fee_percentage", "number"),
]

_FIELD_NAMES = {f for f, _ in _FIELDS}
_FIELD_TYPE = {f: t for f, t in _FIELDS}


# ---------------------------------------------------------------------------
# Derived-field helpers
# ---------------------------------------------------------------------------

def _derive_consideration_type(components_json: str | None) -> str | None:
    if not components_json:
        return None
    try:
        comps = json.loads(components_json)
    except (ValueError, TypeError):
        return None
    if not comps:
        return None
    forms = {c.get("form") for c in comps if isinstance(c, dict) and c.get("form")}
    stock_forms = {"ACQUIRER_STOCK", "TARGET_STOCK"}
    if forms <= {"CASH"}:
        return "CASH"
    if forms <= stock_forms:
        return "STOCK"
    if forms <= ({"CASH"} | stock_forms):
        return "CASH_AND_STOCK"
    return "OTHER"


def _derive_flags(fields: dict) -> dict:
    target_status = fields.get("target_status")
    acquirer_type = fields.get("acquirer_type")
    target_type = fields.get("target_type")
    return {
        "is_take_private": int(
            target_status == "PUBLIC"
            and acquirer_type in ("PRIVATE_EQUITY", "PE_PORTFOLIO")
        ),
        "is_add_on": int(acquirer_type == "PE_PORTFOLIO"),
        "is_divestiture": int(target_type in ("BUSINESS_UNIT", "SUBSIDIARY")),
    }


# ---------------------------------------------------------------------------
# Tier-based field aggregation
# ---------------------------------------------------------------------------

def _pick_value(
    field_name: str,
    field_type: str,
    observations: list[dict],
) -> tuple[Any, bool, list[dict]]:
    """Apply tier-based selection for a single field.

    Returns (chosen_value, needs_llm, conflicting_obs_for_llm).
    For booleans, any True (1) wins within each tier before cross-tier resolution.
    For JSON fields, comparison uses canonical serialization.
    """
    # Group non-null observations by tier
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for obs in observations:
        v = obs.get("value")
        if v is None:
            continue
        # For booleans, treat 0 as null-equivalent (false/unknown same signal)
        if field_type == "boolean" and v == 0:
            continue
        by_tier[obs["tier"]].append(obs)

    def _canonical(v: Any) -> str:
        if field_type == "json":
            try:
                return json.dumps(json.loads(v) if isinstance(v, str) else v, sort_keys=True)
            except (ValueError, TypeError):
                return str(v)
        return str(v)

    for tier in _TIER_ORDER:
        tier_obs = by_tier.get(tier, [])
        if not tier_obs:
            continue
        if len(tier_obs) == 1:
            return tier_obs[0]["value"], False, []
        canonical_vals = [_canonical(obs["value"]) for obs in tier_obs]
        if all(v == canonical_vals[0] for v in canonical_vals):
            return tier_obs[0]["value"], False, []
        # Same-tier conflict — needs LLM
        return None, True, tier_obs

    # All non-null values exhausted — check 0-valued booleans
    if field_type == "boolean":
        for obs in observations:
            if obs.get("value") is not None:
                return 0, False, []

    return None, False, []


# ---------------------------------------------------------------------------
# Aggregation prompt helpers
# ---------------------------------------------------------------------------

def _format_observations(observations: list[dict]) -> str:
    lines = []
    for obs in observations:
        excerpt = (obs.get("source_text_excerpt") or "")[:200]
        lines.append(
            f"{obs['observation_id']}. Source: {obs['source_type']}, Tier: {obs['tier']}, "
            f"Date: {obs['published_date']}, Value: {obs['value']!r}, "
            f"Confidence: {obs['model_confidence']}\n"
            f"   Excerpt: {excerpt!r}"
        )
    return "\n".join(lines)


def _call_agg_prompt(
    field_name: str,
    field_type: str,
    deal_context: dict,
    observations: list[dict],
    prompt: dict,
    cfg: Any,
    conn: sqlite3.Connection,
    run_id: str,
    transaction_id: str,
    log: Any,
) -> dict | None:
    """Call the aggregation prompt for a conflicted field. Returns result dict or None."""
    obs_fmt = _format_observations(observations)
    obs_fmt_escaped = obs_fmt.replace("{", "{{").replace("}", "}}")

    user_prompt = prompt["user_template"].format(
        field_name=field_name,
        field_type=field_type,
        target_name=deal_context.get("target_name") or "Unknown",
        acquirer_name=deal_context.get("acquirer_name") or "Unknown",
        deal_type=deal_context.get("deal_type") or "Unknown",
        announced_date=deal_context.get("announced_date") or "Unknown",
        observations_formatted=obs_fmt_escaped,
    )

    try:
        result = call_prompt(
            prompt_name=_PROMPT_NAME,
            prompt_version=_FULL_VERSION,
            user_prompt=user_prompt,
            system_prompt=prompt["system"],
            model="opus",
            temperature=0.1,
            max_tokens=1024,
            cfg=cfg,
            conn=conn,
            run_id=run_id,
            log=log,
        )
    except PromptFailure as exc:
        log.warning("Aggregation prompt failed for %s on %s: %s", transaction_id, field_name, exc)
        return None

    # Validate chosen_observation_id is in range
    valid_ids = {obs["observation_id"] for obs in observations}
    if result.get("chosen_observation_id") not in valid_ids:
        log.warning(
            "Aggregation result for %s/%s has invalid chosen_observation_id=%r",
            transaction_id, field_name, result.get("chosen_observation_id"),
        )
        return None

    return result


def _log_conflict(
    conn: sqlite3.Connection,
    transaction_id: str,
    field_name: str,
    observations: list[dict],
    result: dict | None,
) -> None:
    """Write conflict record to aggregation_conflict_log."""
    if result:
        conn.execute(
            """
            INSERT INTO aggregation_conflict_log
                (transaction_id, field_name, observations_json,
                 chosen_observation_id, chosen_value, aggregation_confidence,
                 conflict_severity, flagged_for_review, reasoning,
                 prompt_version, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transaction_id, field_name, json.dumps(observations),
                result.get("chosen_observation_id"),
                str(result.get("chosen_value")),
                result.get("aggregation_confidence"),
                result.get("conflict_severity"),
                1 if result.get("flagged_for_review") else 0,
                result.get("reasoning"),
                result.get("prompt_version"),
                result.get("notes"),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO aggregation_conflict_log
                (transaction_id, field_name, observations_json,
                 conflict_severity, flagged_for_review, notes)
            VALUES (?, ?, ?, 'MATERIAL', 1, 'Aggregation prompt failed — manual review required')
            """,
            (transaction_id, field_name, json.dumps(observations)),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Aggregate clustered extractions into canonical transaction records.

    Returns
    -------
    dict
        Keys: clusters_total, transactions_upserted, conflicts_resolved_by_llm,
              flagged_for_review, failed, transactions_created (run.py alias)
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    # Load all CLUSTERED rows, grouped by cluster_id
    rows = conn.execute(
        """
        SELECT se.extraction_id, se.transaction_cluster_id, se.source_raw_id,
               se.deal_type, se.spin_split_type, se.distribution_mechanism,
               se.target_type, se.event_type, se.target_status,
               se.target_name, se.target_domain, se.target_ticker,
               se.acquirer_name, se.acquirer_domain, se.acquirer_ticker, se.acquirer_type,
               se.parent_seller_name, se.parent_seller_ticker,
               se.announced_date, se.closed_date, se.signing_date,
               se.value_amount, se.value_currency, se.value_type, se.per_share_price,
               se.target_revenue, se.target_revenue_period_type, se.target_revenue_period_end,
               se.target_ebitda, se.target_ebitda_period_type, se.target_ebitda_period_end,
               se.financials_currency,
               se.consideration_components,
               se.includes_earnout, se.hostile, se.competing_bid,
               se.regulatory_approvals_required,
               se.has_go_shop, se.go_shop_period_days,
               se.target_fee_amount, se.target_fee_percentage,
               se.acquirer_fee_amount, se.acquirer_fee_percentage,
               se.model_confidence,
               sr.source_type, sr.source_tier, sr.published_date, sr.clean_text
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.status = 'CLUSTERED'
        """
    ).fetchall()

    # Group by cluster_id
    clusters: dict[str, list] = defaultdict(list)
    for row in rows:
        clusters[row["transaction_cluster_id"]].append(row)

    total_clusters = len(clusters)
    log.info("Stage 9: %d clusters to aggregate", total_clusters)

    upserted = conflicts_llm = flagged = failed = 0
    now = datetime.now(timezone.utc).isoformat()

    for cluster_id, members in clusters.items():
        try:
            # Build one observations list per field
            field_values: dict[str, Any] = {}
            llm_count_this_cluster = 0
            flag_count_this_cluster = 0
            # Defer conflict log writes until after transaction_record exists (FK constraint)
            pending_conflicts: list[tuple[str, list, Any]] = []

            for field_name, field_type in _FIELDS:
                observations = []
                for i, m in enumerate(members):
                    raw_val = m[field_name] if field_name in m.keys() else None
                    observations.append({
                        "observation_id": i + 1,
                        "source_type": m["source_type"],
                        "tier": m["source_tier"],
                        "published_date": m["published_date"] or "",
                        "value": raw_val,
                        "model_confidence": m["model_confidence"] or "MEDIUM",
                        "source_text_excerpt": (m["clean_text"] or "")[:200],
                    })

                chosen, needs_llm, conflict_obs = _pick_value(field_name, field_type, observations)

                if needs_llm:
                    deal_ctx = {
                        "target_name": members[0]["target_name"],
                        "acquirer_name": members[0]["acquirer_name"],
                        "deal_type": members[0]["deal_type"],
                        "announced_date": members[0]["announced_date"],
                    }
                    result = _call_agg_prompt(
                        field_name, field_type, deal_ctx, conflict_obs,
                        prompt, cfg, conn, run_id, cluster_id, log,
                    )
                    pending_conflicts.append((field_name, conflict_obs, result))
                    llm_count_this_cluster += 1
                    if result:
                        chosen = result.get("chosen_value")
                        if result.get("flagged_for_review"):
                            flag_count_this_cluster += 1
                    else:
                        # Fallback: take first T1 or T2 observation
                        chosen = conflict_obs[0]["value"]
                    log.info(
                        "cluster=%s field=%s LLM conflict resolved → %r",
                        cluster_id, field_name, chosen,
                    )

                field_values[field_name] = chosen

            conflicts_llm += llm_count_this_cluster
            flagged += flag_count_this_cluster

            # Derive additional fields
            ctype = _derive_consideration_type(field_values.get("consideration_components"))
            derived = _derive_flags(field_values)

            # Check for existing transaction_record to determine version
            existing = conn.execute(
                "SELECT aggregation_version FROM transaction_record WHERE transaction_id=?",
                (cluster_id,),
            ).fetchone()
            agg_version = (existing["aggregation_version"] + 1) if existing else 1

            # Upsert transaction_record
            conn.execute(
                """
                INSERT OR REPLACE INTO transaction_record (
                    transaction_id, deal_type, spin_split_type, distribution_mechanism,
                    target_type, event_type, target_status,
                    target_name, target_domain, target_ticker,
                    acquirer_name, acquirer_domain, acquirer_ticker, acquirer_type,
                    parent_seller_name, parent_seller_ticker,
                    announced_date, closed_date, signing_date,
                    value_amount, value_currency, value_type, per_share_price,
                    target_revenue, target_revenue_period_type, target_revenue_period_end,
                    target_ebitda, target_ebitda_period_type, target_ebitda_period_end,
                    financials_currency,
                    consideration_type, consideration_components,
                    includes_earnout, hostile, competing_bid, regulatory_approvals_required,
                    has_go_shop, go_shop_period_days,
                    target_fee_amount, target_fee_percentage,
                    acquirer_fee_amount, acquirer_fee_percentage,
                    is_take_private, is_add_on, is_divestiture,
                    is_current, aggregation_version, updated_at
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    cluster_id,
                    field_values.get("deal_type"),
                    field_values.get("spin_split_type"),
                    field_values.get("distribution_mechanism"),
                    field_values.get("target_type"),
                    field_values.get("event_type"),
                    field_values.get("target_status"),
                    field_values.get("target_name"),
                    field_values.get("target_domain"),
                    field_values.get("target_ticker"),
                    field_values.get("acquirer_name"),
                    field_values.get("acquirer_domain"),
                    field_values.get("acquirer_ticker"),
                    field_values.get("acquirer_type"),
                    field_values.get("parent_seller_name"),
                    field_values.get("parent_seller_ticker"),
                    field_values.get("announced_date"),
                    field_values.get("closed_date"),
                    field_values.get("signing_date"),
                    field_values.get("value_amount"),
                    field_values.get("value_currency"),
                    field_values.get("value_type"),
                    field_values.get("per_share_price"),
                    field_values.get("target_revenue"),
                    field_values.get("target_revenue_period_type"),
                    field_values.get("target_revenue_period_end"),
                    field_values.get("target_ebitda"),
                    field_values.get("target_ebitda_period_type"),
                    field_values.get("target_ebitda_period_end"),
                    field_values.get("financials_currency"),
                    ctype,
                    field_values.get("consideration_components"),
                    field_values.get("includes_earnout"),
                    field_values.get("hostile"),
                    field_values.get("competing_bid"),
                    field_values.get("regulatory_approvals_required"),
                    field_values.get("has_go_shop"),
                    field_values.get("go_shop_period_days"),
                    field_values.get("target_fee_amount"),
                    field_values.get("target_fee_percentage"),
                    field_values.get("acquirer_fee_amount"),
                    field_values.get("acquirer_fee_percentage"),
                    derived["is_take_private"],
                    derived["is_add_on"],
                    derived["is_divestiture"],
                    1,
                    agg_version,
                    now,
                ),
            )

            # Flush deferred conflict logs (must follow transaction_record INSERT for FK)
            for f_name, c_obs, c_result in pending_conflicts:
                _log_conflict(conn, cluster_id, f_name, c_obs, c_result)

            # Insert transaction_source rows for each cluster member's source
            for m in members:
                # Primary role for the best-tier member; others are confirmatory
                role = "PRIMARY" if m["source_tier"] == "T1" else "CONFIRMATORY"
                conn.execute(
                    "INSERT OR IGNORE INTO transaction_source (transaction_id, source_raw_id, role) VALUES (?,?,?)",
                    (cluster_id, m["source_raw_id"], role),
                )

            # Also link any SEC-enriched T1 sources attached to these extractions
            for m in members:
                sec_rows = conn.execute(
                    """SELECT source_raw_id FROM source_raw
                       WHERE json_extract(notes, '$.triggered_by_extraction_id') = ?""",
                    (m["extraction_id"],),
                ).fetchall()
                for sr in sec_rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO transaction_source (transaction_id, source_raw_id, role) VALUES (?,?,?)",
                        (cluster_id, sr["source_raw_id"], "ENRICHMENT"),
                    )

            # Transition all cluster members to AGGREGATED
            for m in members:
                conn.execute(
                    "UPDATE staging_extraction SET status='AGGREGATED', updated_at=? WHERE extraction_id=?",
                    (now, m["extraction_id"]),
                )
            conn.commit()

            upserted += 1
            log.info(
                "cluster=%s AGGREGATED  members=%d  deal_type=%s  target=%r  acquirer=%r  v=%d",
                cluster_id, len(members),
                field_values.get("deal_type"), field_values.get("target_name"),
                field_values.get("acquirer_name"), agg_version,
            )

        except Exception as exc:
            log.error("cluster=%s aggregation error: %s — skipping", cluster_id, exc, exc_info=True)
            failed += 1
            try:
                conn.rollback()
            except Exception:
                pass

    log.info(
        "Stage 9 done  clusters=%d upserted=%d llm_conflicts=%d flagged=%d failed=%d",
        total_clusters, upserted, conflicts_llm, flagged, failed,
    )
    return {
        "clusters_total": total_clusters,
        "transactions_upserted": upserted,
        "transactions_created": upserted,
        "conflicts_resolved_by_llm": conflicts_llm,
        "flagged_for_review": flagged,
        "failed": failed,
    }
