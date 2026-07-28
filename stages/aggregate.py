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
from lib.field_priority import TIER_ORDER
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "aggregation"
_VERSION = "0.3"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_CONF_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

_FUNDING_EVENT_TYPES = frozenset({"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"})

# Fields aggregated into transaction_record.
# Each entry: (field_name, field_type)
_FIELDS = [
    ("deal_type", "string"),
    ("v2_event_type", "string"),
    ("event_history_type", "string"),
    ("spin_split_type", "string"),
    ("distribution_mechanism", "string"),
    ("recap_type", "string"),
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
    ("acquirer_type_v2", "string"),
    ("parent_seller_name", "string"),
    ("parent_seller_ticker", "string"),
    ("target_description", "string"),
    ("acquirer_description", "string"),
    ("acquirer_sponsor_name", "string"),
    ("parent_seller_description", "string"),
    ("announced_date", "date"),
    ("announced_date_precision", "string"),
    ("closed_date", "date"),
    ("closed_date_precision", "string"),
    ("signing_date", "date"),
    ("rumor_date", "date"),
    ("value_amount", "number"),
    ("value_currency", "string"),
    ("value_type", "string"),
    ("per_share_price", "number"),
    ("pct_acquired", "number"),
    ("target_revenue", "number"),
    ("target_revenue_period_type", "string"),
    ("target_revenue_period_type_v2", "string"),
    ("target_revenue_period_end", "date"),
    ("target_ebitda", "number"),
    ("target_ebitda_period_type", "string"),
    ("target_ebitda_period_type_v2", "string"),
    ("target_ebitda_period_end", "date"),
    ("financials_currency", "string"),
    ("financials_disclosure_status", "string"),
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
    # Funding fields
    ("round_label", "string"),
    ("round_size", "number"),
    ("pre_money_valuation", "number"),
    ("post_money_valuation", "number"),
    ("valuation_currency", "string"),
    ("facility_size", "number"),
    ("total_raised_to_date", "number"),
    ("is_extension_round", "boolean"),
    ("is_down_round", "boolean"),
    ("is_bridge_round", "boolean"),
    ("use_of_proceeds", "string"),
    ("has_board_seat", "boolean"),
    ("board_seat_notes", "string"),
]

_FIELD_NAMES = {f for f, _ in _FIELDS}
_FIELD_TYPE = {f: t for f, t in _FIELDS}
_CONTEXT_FIELDS = ("target_name", "acquirer_name", "deal_type", "announced_date")
_PRIVATE_TAKE_PRIVATE_ACQUIRER_TYPES = frozenset({
    "PRIVATE_EQUITY",
    "PE_PORTFOLIO",
    "VENTURE_CAPITAL",
    "SOVEREIGN_WEALTH_FUND",
    "PENSION_FUND",
    "HEDGE_FUND",
    "FAMILY_OFFICE",
    "INDIVIDUAL",
    "MANAGEMENT",
    "EMPLOYEE_GROUP",
    "CONSORTIUM",
    "OTHER_FINANCIAL_SPONSOR",
})
_PRIVATE_STRATEGIC_ACQUIRER_TYPES = frozenset({"STRATEGIC_CORPORATE"})


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


def _has_value(value: Any) -> bool:
    return bool(str(value or "").strip())


def _derive_is_take_private(fields: dict) -> int:
    if fields.get("deal_type") != "ACQUISITION":
        return 0
    if fields.get("target_status") != "PUBLIC":
        return 0
    if fields.get("target_type") != "STANDALONE_COMPANY":
        return 0

    acquirer_type = fields.get("acquirer_type")
    if _has_value(fields.get("acquirer_ticker")):
        return 0
    if acquirer_type in _PRIVATE_TAKE_PRIVATE_ACQUIRER_TYPES:
        return 1
    if acquirer_type in _PRIVATE_STRATEGIC_ACQUIRER_TYPES:
        return 1
    return 0


def _derive_flags(fields: dict) -> dict:
    acquirer_type = fields.get("acquirer_type")
    target_type = fields.get("target_type")
    deal_type = fields.get("deal_type")
    return {
        "is_take_private": _derive_is_take_private(fields),
        "is_add_on": int(acquirer_type == "PE_PORTFOLIO"),
        "is_divestiture": int(target_type in ("BUSINESS_UNIT", "SUBSIDIARY", "ASSETS")),
        "is_de_spac": int(deal_type == "REVERSE_MERGER" and acquirer_type == "SPAC"),
    }


def _derive_has_earnout(consideration_components_json: str | None) -> int:
    if not consideration_components_json:
        return 0
    try:
        comps = json.loads(consideration_components_json) if isinstance(consideration_components_json, str) else consideration_components_json
        return int(any(c.get("form") == "EARNOUT" for c in comps if isinstance(c, dict)))
    except (ValueError, TypeError, AttributeError):
        return 0


def _derive_has_cvr(consideration_components_json: str | None) -> int:
    if not consideration_components_json:
        return 0
    try:
        comps = json.loads(consideration_components_json) if isinstance(consideration_components_json, str) else consideration_components_json
        return int(any(c.get("form") == "CVR" for c in comps if isinstance(c, dict)))
    except (ValueError, TypeError, AttributeError):
        return 0


def _derive_transaction_status(event_type: str | None, closed_date: str | None) -> str:
    if event_type == "TERMINATION":
        return "TERMINATED"
    if event_type == "RUMOR":
        return "RUMORED"
    if closed_date is not None:
        return "CLOSED"
    if event_type in ("ANNOUNCEMENT", "AMENDMENT"):
        return "PENDING"
    return "UNKNOWN"


def _derive_round_stage_category(round_label: str | None) -> str | None:
    """Derive round_stage_category from round_label."""
    if not round_label:
        return None
    label = round_label.lower().strip()
    if any(x in label for x in ("pre-seed", "pre_seed", "preseed")):
        return "PRE_SEED"
    if any(x in label for x in ("seed", "angel")):
        return "SEED"
    if "series a" in label or "series-a" in label:
        return "EARLY_STAGE"
    if any(x in label for x in ("series b", "series-b", "series c", "series-c")):
        return "GROWTH"
    if any(x in label for x in (
        "series d", "series e", "series f", "series g",
        "growth equity", "growth", "late stage", "late-stage",
    )):
        return "LATE_STAGE"
    return None


def _compute_multiples(
    value_amount: float | None,
    value_type: str | None,
    value_currency: str | None,
    target_revenue: float | None,
    target_revenue_period_type: str | None,
    target_ebitda: float | None,
    target_ebitda_period_type: str | None,
    financials_currency: str | None,
    log: Any,
    cluster_id: str,
    v2_event_type: str | None = None,
) -> dict:
    """Compute EV/Revenue and EV/EBITDA multiples.

    Requires value_type = ENTERPRISE_VALUE. TTM is treated as LTM (interchangeable
    industry usage). Cross-currency pairs are flagged NM without conversion.
    Plausible ranges: EV/Revenue 0.1x–50x, EV/EBITDA 1x–100x.
    """
    result: dict[str, Any] = {
        "ev_to_revenue_ltm": None,
        "ev_to_revenue_ntm": None,
        "ev_to_ebitda_ltm": None,
        "ev_to_ebitda_ntm": None,
        "multiple_quality": "NOT_CALCULABLE",
    }

    # Multiples not applicable for funding events
    if v2_event_type in _FUNDING_EVENT_TYPES:
        return result

    if value_type != "ENTERPRISE_VALUE" or not value_amount or value_amount <= 0:
        return result

    currency_mismatch = bool(
        value_currency and financials_currency and value_currency != financials_currency
    )

    _RANGES = {"revenue": (0.1, 50.0), "ebitda": (1.0, 100.0)}

    def _slot(period_type: str | None, metric: str) -> str | None:
        p = (period_type or "").upper()
        if p in ("LTM", "TTM"):
            return f"ev_to_{metric}_ltm"
        if p == "NTM":
            return f"ev_to_{metric}_ntm"
        log.debug("cluster=%s %s period_type=%r not LTM/NTM — skipping multiple", cluster_id, metric, period_type)
        return None

    calculated_in_range = False
    calculated_out_of_range = False

    for metric, raw_value, period_type in (
        ("revenue", target_revenue, target_revenue_period_type),
        ("ebitda", target_ebitda, target_ebitda_period_type),
    ):
        if not raw_value or raw_value <= 0:
            continue
        slot = _slot(period_type, metric)
        if slot is None:
            continue
        if currency_mismatch:
            calculated_out_of_range = True
            continue
        multiple = round(value_amount / raw_value, 2)
        result[slot] = multiple
        lo, hi = _RANGES[metric]
        if lo <= multiple <= hi:
            calculated_in_range = True
        else:
            calculated_out_of_range = True

    if calculated_in_range:
        result["multiple_quality"] = "CALCULATED"
    elif calculated_out_of_range:
        result["multiple_quality"] = "NM"

    return result


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

    for tier in TIER_ORDER:
        tier_obs = by_tier.get(tier, [])
        if not tier_obs:
            continue
        if len(tier_obs) == 1:
            return tier_obs[0]["value"], False, []
        canonical_vals = [_canonical(obs["value"]) for obs in tier_obs]
        if all(v == canonical_vals[0] for v in canonical_vals):
            return tier_obs[0]["value"], False, []
        # Confidence tiebreak before LLM conflict resolution.
        # LLM is invoked only when confidence is tied within the same tier.
        ranked = sorted(
            tier_obs,
            key=lambda o: _CONF_RANK.get(o.get("model_confidence") or "MEDIUM", 1),
        )
        top_rank = _CONF_RANK.get(ranked[0].get("model_confidence") or "MEDIUM", 1)
        second_rank = _CONF_RANK.get(ranked[1].get("model_confidence") or "MEDIUM", 1)
        if top_rank < second_rank:
            return ranked[0]["value"], False, []
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
# Aggregation input loaders
# ---------------------------------------------------------------------------

def _empty_cluster() -> dict:
    return {
        "field_observations": {field_name: [] for field_name, _ in _FIELDS},
        "deal_context": {},
        "sources": [],
    }


def _load_staging_input(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT se.extraction_id, se.transaction_cluster_id, se.source_raw_id,
               se.deal_type, se.spin_split_type, se.distribution_mechanism,
               se.target_type, se.event_type, se.target_status,
               se.target_name, se.target_domain, se.target_ticker,
               se.acquirer_name, se.acquirer_domain, se.acquirer_ticker, se.acquirer_type,
               se.parent_seller_name, se.parent_seller_ticker,
               se.target_description, se.acquirer_description, se.acquirer_sponsor_name, se.parent_seller_description,
               se.announced_date, se.closed_date, se.signing_date,
               se.value_amount, se.value_currency, se.value_type, se.per_share_price, se.pct_acquired,
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

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["transaction_cluster_id"]].append(row)

    clusters: dict[str, dict] = {}
    for cluster_id, members in grouped.items():
        bundle = _empty_cluster()
        bundle["deal_context"] = {
            "target_name": members[0]["target_name"],
            "acquirer_name": members[0]["acquirer_name"],
            "deal_type": members[0]["deal_type"],
            "announced_date": members[0]["announced_date"],
        }
        for i, member in enumerate(members):
            bundle["sources"].append({
                "source_raw_id": member["source_raw_id"],
                "source_tier": member["source_tier"],
                "staging_extraction_id": member["extraction_id"],
            })
            for field_name, _field_type in _FIELDS:
                raw_val = member[field_name] if field_name in member.keys() else None
                bundle["field_observations"][field_name].append({
                    "observation_id": i + 1,
                    "source_type": member["source_type"],
                    "tier": member["source_tier"],
                    "published_date": member["published_date"] or "",
                    "value": raw_val,
                    "model_confidence": member["model_confidence"] or "MEDIUM",
                    "source_text_excerpt": (member["clean_text"] or "")[:200],
                })
        clusters[cluster_id] = bundle
    return clusters


def _value_from_observation(row: sqlite3.Row, field_type: str) -> Any:
    text_value = row["field_value"]
    numeric_value = row["field_value_numeric"]
    if text_value is None and numeric_value is None:
        return None

    if field_type == "number":
        if numeric_value is not None:
            return float(numeric_value)
        try:
            return float(text_value)
        except (TypeError, ValueError):
            return None

    if field_type == "boolean":
        if numeric_value is not None:
            return 1 if float(numeric_value) != 0.0 else 0
        normalized = str(text_value).strip().lower()
        if normalized in {"1", "true"}:
            return 1
        if normalized in {"0", "false"}:
            return 0
        return None

    if field_type == "json":
        try:
            return json.loads(text_value)
        except (TypeError, ValueError):
            return text_value

    return text_value


def _load_observation_input(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT
            tfo.observation_id,
            tfo.transaction_id,
            tfo.field_name,
            tfo.field_value,
            tfo.field_value_numeric,
            tfo.staging_extraction_id,
            tfo.source_raw_id,
            COALESCE(tfo.source_type, sr.source_type) AS source_type,
            COALESCE(tfo.source_tier, sr.source_tier) AS source_tier,
            COALESCE(tfo.source_published_date, sr.published_date) AS published_date,
            COALESCE(tfo.model_confidence, 'MEDIUM') AS model_confidence,
            sr.clean_text
        FROM transaction_field_observation tfo
        JOIN staging_extraction se
          ON se.extraction_id = tfo.staging_extraction_id
        LEFT JOIN source_raw sr
          ON sr.source_raw_id = tfo.source_raw_id
        WHERE tfo.is_current = 1
          AND tfo.transaction_id IS NOT NULL
          AND tfo.staging_extraction_id IS NOT NULL
          AND se.status = 'CLUSTERED'
          AND COALESCE(tfo.observation_source_stage, 'BACKFILL') IN (
              'DT_CLASSIFY',
              'HC_EXTRACT',
              'LC_EXTRACT',
              'BACKFILL'
          )
        ORDER BY tfo.transaction_id, tfo.staging_extraction_id, tfo.observation_id
        """
    ).fetchall()

    clusters: dict[str, dict] = {}
    seen_sources: dict[str, set[tuple[int | None, int | None]]] = defaultdict(set)
    for row in rows:
        cluster_id = row["transaction_id"]
        bundle = clusters.setdefault(cluster_id, _empty_cluster())
        source_key = (row["staging_extraction_id"], row["source_raw_id"])
        if source_key not in seen_sources[cluster_id]:
            seen_sources[cluster_id].add(source_key)
            bundle["sources"].append({
                "source_raw_id": row["source_raw_id"],
                "source_tier": row["source_tier"],
                "staging_extraction_id": row["staging_extraction_id"],
            })

        field_name = row["field_name"]
        if field_name not in _FIELD_TYPE:
            continue

        field_type = _FIELD_TYPE[field_name]
        value = _value_from_observation(row, field_type)
        if field_name in _CONTEXT_FIELDS and bundle["deal_context"].get(field_name) is None:
            bundle["deal_context"][field_name] = value

        bundle["field_observations"][field_name].append({
            "observation_id": row["observation_id"],
            "source_type": row["source_type"],
            "tier": row["source_tier"],
            "published_date": row["published_date"] or "",
            "value": value,
            "model_confidence": row["model_confidence"] or "MEDIUM",
            "source_text_excerpt": (row["clean_text"] or "")[:200],
        })
    return clusters


def _load_aggregation_input(conn: sqlite3.Connection, read_source: str) -> dict[str, dict]:
    if read_source == "observation":
        return _load_observation_input(conn)
    return _load_staging_input(conn)


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
    read_source = getattr(cfg, "aggregation_read_source", "staging")

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s read_source=%s", _FULL_VERSION, prompt["file_hash"][:12], read_source)

    clusters = _load_aggregation_input(conn, read_source)

    total_clusters = len(clusters)
    log.info("Stage 9: %d clusters to aggregate", total_clusters)

    upserted = conflicts_llm = flagged = failed = 0
    now = datetime.now(timezone.utc).isoformat()

    for cluster_id, bundle in clusters.items():
        try:
            # Build one observations list per field
            field_values: dict[str, Any] = {}
            llm_count_this_cluster = 0
            flag_count_this_cluster = 0
            # Defer conflict log writes until after transaction_record exists (FK constraint)
            pending_conflicts: list[tuple[str, list, Any]] = []

            for field_name, field_type in _FIELDS:
                observations = bundle["field_observations"].get(field_name, [])
                chosen, needs_llm, conflict_obs = _pick_value(field_name, field_type, observations)

                if needs_llm:
                    result = _call_agg_prompt(
                        field_name, field_type, bundle["deal_context"], conflict_obs,
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

            # Re-serialize any json-type fields that the LLM returned as Python objects
            for _fname, _ftype in _FIELDS:
                if _ftype == "json" and isinstance(field_values.get(_fname), (list, dict)):
                    field_values[_fname] = json.dumps(field_values[_fname])

            # Derive additional fields
            ctype = _derive_consideration_type(field_values.get("consideration_components"))
            derived = _derive_flags(field_values)
            txn_status = _derive_transaction_status(
                field_values.get("event_type"), field_values.get("closed_date")
            )
            multiples = _compute_multiples(
                value_amount=field_values.get("value_amount"),
                value_type=field_values.get("value_type"),
                value_currency=field_values.get("value_currency"),
                target_revenue=field_values.get("target_revenue"),
                target_revenue_period_type=(
                    field_values.get("target_revenue_period_type_v2")
                    or field_values.get("target_revenue_period_type")
                ),
                target_ebitda=field_values.get("target_ebitda"),
                target_ebitda_period_type=(
                    field_values.get("target_ebitda_period_type_v2")
                    or field_values.get("target_ebitda_period_type")
                ),
                financials_currency=field_values.get("financials_currency"),
                log=log,
                cluster_id=cluster_id,
                v2_event_type=field_values.get("v2_event_type") or field_values.get("deal_type"),
            )

            # Derive round_stage_category from round_label
            round_stage_category = _derive_round_stage_category(
                field_values.get("round_label")
            )

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
                    transaction_id, deal_type, v2_event_type, event_history_type,
                    spin_split_type, spin_split_type_v2, distribution_mechanism, recap_type,
                    target_type, target_type_v2, event_type, transaction_status, target_status,
                    target_name, target_domain, target_ticker,
                    acquirer_name, acquirer_domain, acquirer_ticker, acquirer_type, acquirer_type_v2,
                    parent_seller_name, parent_seller_ticker,
                    target_description, acquirer_description, acquirer_sponsor_name, parent_seller_description,
                    announced_date, announced_date_precision, closed_date, closed_date_precision,
                    signing_date, rumor_date,
                    value_amount, value_currency, value_type, per_share_price, pct_acquired,
                    target_revenue, target_revenue_period_type, target_revenue_period_type_v2, target_revenue_period_end,
                    target_ebitda, target_ebitda_period_type, target_ebitda_period_type_v2, target_ebitda_period_end,
                    financials_currency, financials_disclosure_status,
                    ev_to_revenue_ltm, ev_to_revenue_ntm, ev_to_ebitda_ltm, ev_to_ebitda_ntm, multiple_quality,
                    consideration_type, consideration_components,
                    includes_earnout, hostile, competing_bid, regulatory_approvals_required,
                    has_go_shop, go_shop_period_days,
                    target_fee_amount, target_fee_percentage,
                    acquirer_fee_amount, acquirer_fee_percentage,
                    is_take_private, is_add_on, is_divestiture, is_de_spac,
                    has_earnout, has_cvr,
                    round_label, round_stage_category, round_size,
                    pre_money_valuation, post_money_valuation, valuation_currency,
                    facility_size, total_raised_to_date,
                    is_extension_round, is_down_round, is_bridge_round,
                    use_of_proceeds, has_board_seat, board_seat_notes,
                    is_current, aggregation_version, updated_at
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    cluster_id,
                    field_values.get("deal_type") or field_values.get("v2_event_type"),
                    field_values.get("v2_event_type"),
                    field_values.get("event_history_type"),
                    field_values.get("spin_split_type"),
                    field_values.get("spin_split_type_v2"),
                    field_values.get("distribution_mechanism"),
                    field_values.get("recap_type"),
                    field_values.get("target_type"),
                    field_values.get("target_type_v2"),
                    field_values.get("event_type"),
                    txn_status,
                    field_values.get("target_status"),
                    field_values.get("target_name"),
                    field_values.get("target_domain"),
                    field_values.get("target_ticker"),
                    field_values.get("acquirer_name"),
                    field_values.get("acquirer_domain"),
                    field_values.get("acquirer_ticker"),
                    field_values.get("acquirer_type"),
                    field_values.get("acquirer_type_v2"),
                    field_values.get("parent_seller_name"),
                    field_values.get("parent_seller_ticker"),
                    field_values.get("target_description"),
                    field_values.get("acquirer_description"),
                    field_values.get("acquirer_sponsor_name"),
                    field_values.get("parent_seller_description"),
                    field_values.get("announced_date"),
                    field_values.get("announced_date_precision"),
                    field_values.get("closed_date"),
                    field_values.get("closed_date_precision"),
                    field_values.get("signing_date"),
                    field_values.get("rumor_date"),
                    field_values.get("value_amount"),
                    field_values.get("value_currency"),
                    field_values.get("value_type"),
                    field_values.get("per_share_price"),
                    field_values.get("pct_acquired"),
                    field_values.get("target_revenue"),
                    field_values.get("target_revenue_period_type"),
                    field_values.get("target_revenue_period_type_v2"),
                    field_values.get("target_revenue_period_end"),
                    field_values.get("target_ebitda"),
                    field_values.get("target_ebitda_period_type"),
                    field_values.get("target_ebitda_period_type_v2"),
                    field_values.get("target_ebitda_period_end"),
                    field_values.get("financials_currency"),
                    field_values.get("financials_disclosure_status"),
                    multiples["ev_to_revenue_ltm"],
                    multiples["ev_to_revenue_ntm"],
                    multiples["ev_to_ebitda_ltm"],
                    multiples["ev_to_ebitda_ntm"],
                    multiples["multiple_quality"],
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
                    derived["is_de_spac"],
                    _derive_has_earnout(field_values.get("consideration_components")),
                    _derive_has_cvr(field_values.get("consideration_components")),
                    # Funding fields
                    field_values.get("round_label"),
                    round_stage_category,
                    field_values.get("round_size"),
                    field_values.get("pre_money_valuation"),
                    field_values.get("post_money_valuation"),
                    field_values.get("valuation_currency"),
                    field_values.get("facility_size"),
                    field_values.get("total_raised_to_date"),
                    field_values.get("is_extension_round"),
                    field_values.get("is_down_round"),
                    field_values.get("is_bridge_round"),
                    field_values.get("use_of_proceeds"),
                    field_values.get("has_board_seat"),
                    field_values.get("board_seat_notes"),
                    1,
                    agg_version,
                    now,
                ),
            )

            # Flush deferred conflict logs (must follow transaction_record INSERT for FK)
            for f_name, c_obs, c_result in pending_conflicts:
                _log_conflict(conn, cluster_id, f_name, c_obs, c_result)

            # Insert transaction_source rows for each cluster member's source
            for source in bundle["sources"]:
                # Primary role for the best-tier member; others are confirmatory
                role = "PRIMARY" if source["source_tier"] == "T1" else "CONFIRMATORY"
                conn.execute(
                    "INSERT OR IGNORE INTO transaction_source (transaction_id, source_raw_id, role) VALUES (?,?,?)",
                    (cluster_id, source["source_raw_id"], role),
                )

            # Also link any SEC-enriched T1 sources attached to these extractions
            for source in bundle["sources"]:
                if source["staging_extraction_id"] is None:
                    continue
                sec_rows = conn.execute(
                    """SELECT source_raw_id FROM source_raw
                       WHERE json_extract(notes, '$.triggered_by_extraction_id') = ?""",
                    (source["staging_extraction_id"],),
                ).fetchall()
                for sr in sec_rows:
                    conn.execute(
                        "INSERT OR IGNORE INTO transaction_source (transaction_id, source_raw_id, role) VALUES (?,?,?)",
                        (cluster_id, sr["source_raw_id"], "ENRICHMENT"),
                    )

            # Transition all cluster members to AGGREGATED
            for source in bundle["sources"]:
                if source["staging_extraction_id"] is None:
                    continue
                conn.execute(
                    "UPDATE staging_extraction SET status='AGGREGATED', updated_at=? WHERE extraction_id=?",
                    (now, source["staging_extraction_id"]),
                )
            conn.commit()

            upserted += 1
            log.info(
                "cluster=%s AGGREGATED  members=%d  deal_type=%s  target=%r  acquirer=%r  v=%d",
                cluster_id, len(bundle["sources"]),
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
