"""Observation-layer writers and backfills for Drop 3.31b.

The helpers in this module are intentionally write-side only. Stage 9 continues
to read from staging_extraction in Drop 3.31b.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from typing import Any

STAGE3_FIELDS = (
    "deal_type",
    "v2_event_type",
    "event_history_type",
    "recap_type",
    # V3 §T2 (2026-08-20). Stage 3 writes no observations of its own — this group is
    # written when Stage 4 runs with include_stage3=True, stamped with dt_prompt_version.
    # Omitting a classifier field here strands it on staging_extraction: Stage 9 reads
    # observations by default, so the canonical column would stay NULL forever.
    "combination_structure",
    "spin_split_type",
    # Observation coverage (2026-08-11): _v2 classifier fields previously written
    # to transaction_record as perpetual NULL (absent from _FIELDS/observation).
    "spin_split_type_v2",
    "distribution_mechanism",
    "target_type",
    "target_type_v2",
    "event_type",
    "target_status",
)

HC_FIELDS = (
    "target_name",
    "target_domain",
    "target_ticker",
    "acquirer_name",
    "acquirer_domain",
    "acquirer_ticker",
    "acquirer_type",
    "parent_seller_name",
    "parent_seller_ticker",
    "target_description",
    # V3 §T13 (2026-08-20). Subordinate to target_type = assets. Stage 4 writes this
    # group itself via include_hc=True; omitting a field here strands it on
    # staging_extraction, since Stage 9 reads observations by default.
    "asset_type",
    "acquirer_description",
    "acquirer_sponsor_name",
    "parent_seller_description",
    "announced_date",
    "closed_date",
    "signing_date",
    "value_amount",
    "value_currency",
    "value_type",
    "per_share_price",
    "round_size",
    "pct_acquired",
    "stake_transition_type",
    # V3 §T12 (2026-08-20). Ordinary-source owner of the tender-offer fact. Stage 11 may
    # additionally observe it from merger_structure = TENDER_OFFER; both land here and are
    # resolved by ordinary tier logic. Omitting this line strands the field on
    # staging_extraction, since Stage 9 reads observations by default.
    "offer_mechanism",
    # is_platform_investment left this group in the same change (V3 §T7, S-G): Stage 4 no
    # longer writes that column, so observing it would produce a permanent NULL. Governs NEW
    # observation writing only -- the historical column and existing rows are untouched.
    "sponsor_transaction_role",
    "is_secondary_buyout",
    "is_merger_of_equals",
    # V3 take-private ownership outcome. Affirmative-evidence-only: staging holds 1 or
    # NULL and never 0 (Stage 4 normalizes a model `false` away), and _write_field_group
    # skips None, so "not established" produces NO observation row at all rather than an
    # observed negative. Omitting this line strands the field on staging_extraction,
    # since Stage 9 reads observations by default.
    "is_going_private_outcome",
    "target_revenue",
    "target_revenue_period_type",
    "target_revenue_period_end",
    "target_ebitda",
    "target_ebitda_period_type",
    "target_ebitda_period_end",
    "financials_currency",
    # Point-in-time balance-sheet items and their per-source qualifiers.
    "total_debt",
    "total_debt_currency",
    "cash_st",
    "cash_st_currency",
    "balance_sheet_as_of_date",
    # Observation coverage (2026-08-11): V2 + precision HC fields, present in
    # _FIELDS / staging read but never written as observations (all HC-stage).
    "acquirer_type_v2",
    "announced_date_precision",
    "closed_date_precision",
    "signing_date_precision",
    "rumor_date",
    "target_revenue_period_type_v2",
    "target_ebitda_period_type_v2",
    "financials_disclosure_status",
)

# `hostile` was removed here on 2026-08-20 (V3 §T11). Stage 7 no longer writes that
# column, so observing it produced a permanent NULL. Its replacements are the two typed
# dimensions below. This governs NEW Stage 7 observation writing only — the historical
# `hostile` column and existing rows are untouched.
# `includes_earnout` left this group on 2026-08-20 with the same reasoning that removed
# `hostile`: Stage 7 no longer writes it, so observing it produced a permanent NULL. Its
# replacement is not another flag -- consideration_components is authoritative, and
# has_earnout / has_cvr derive from specific component forms.
LC_SCALAR_FIELDS = (
    "deal_attitude",
    "approach_type",
    "competing_bid",
    "regulatory_approvals_required",
    "has_go_shop",
    "go_shop_period_days",
    "target_fee_amount",
    "target_fee_percentage",
    "acquirer_fee_amount",
    "acquirer_fee_percentage",
)

# Stage 4b funding extraction (observation coverage, 2026-08-11). round_size is
# tier-1 and also lives in HC_FIELDS — it was put there because MINORITY_INVESTMENT
# stays on the M&A/HC path per §4.1, not because of the funding path. It is included
# here so this group is self-sufficient: a VC_ROUND row that requests include_funding
# alone still gets a round_size observation (its primary magnitude, first rung of the
# transaction_size waterfall, input to _derive_investment_amount). The HC_FIELDS
# overlap is dedup-safe (partial unique index on (staging_extraction_id, field_name,
# field_value) + INSERT OR IGNORE).
FUNDING_FIELDS = (
    "round_label",
    "round_size",
    "pre_money_valuation",
    "post_money_valuation",
    "valuation_currency",
    "round_currency",
    "facility_size",
    "total_raised_to_date",
    "is_extension_round",
    # V3 §A6.3 (2026-08-20). `is_down_round` left this group in the same change: Stage 4b
    # no longer writes that column, so observing it produced a permanent NULL. Governs NEW
    # observation writing only -- the historical column and existing rows are untouched.
    "round_price_direction",
    "is_bridge_round",
    "use_of_proceeds",
    "has_board_seat",
    "board_seat_notes",
)

SOURCE_ROW_PRESENT_FIELD = "__source_row_present"

_OBSERVATION_INSERT_ORDER = (
    "transaction_id",
    "field_name",
    "field_value",
    "field_value_numeric",
    "source_document_id",
    "source_section_id",
    "staging_extraction_id",
    "source_raw_id",
    "source_type",
    "source_tier",
    "model_confidence",
    "source_published_date",
    "filing_type",
    "agreement_dated_as_of",
    "observed_as_of_date",
    "filing_date",
    "extraction_prompt_version",
    "observation_source_stage",
    "observation_fact_key",
)

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_DATED_AS_OF_RE = re.compile(
    r"\bdated\s+as\s+of\s+("
    + "|".join(_MONTHS)
    + r")\s+([0-9]{1,2}),\s+([0-9]{4})\b",
    re.IGNORECASE,
)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _string_value(value: Any, *, bool_as_int_text: bool) -> str:
    if isinstance(value, bool) and bool_as_int_text:
        return "1" if value else "0"
    return str(value)


def insert_observation(
    conn: sqlite3.Connection,
    *,
    transaction_id: str | None,
    field_name: str,
    field_value: Any,
    source_document_id: int | None = None,
    source_section_id: int | None = None,
    staging_extraction_id: int | None = None,
    source_raw_id: int | None = None,
    source_type: str | None = None,
    source_tier: str | None = None,
    model_confidence: str | None = None,
    source_published_date: str | None = None,
    filing_type: str | None = None,
    agreement_dated_as_of: str | None = None,
    observed_as_of_date: str | None = None,
    filing_date: str | None = None,
    extraction_prompt_version: str | None = None,
    observation_source_stage: str | None = None,
    observation_fact_key: str | None = None,
    bool_as_int_text: bool = False,
    columns: set[str] | None = None,
) -> int:
    """Insert one current field observation idempotently.

    The column filtering keeps focused unit scripts with older in-memory table
    definitions working while production databases receive the full 3.31b shape.
    """
    if field_value is None:
        return 0

    available = columns or _table_columns(conn, "transaction_field_observation")
    values = {
        "transaction_id": transaction_id,
        "field_name": field_name,
        "field_value": _string_value(field_value, bool_as_int_text=bool_as_int_text),
        "field_value_numeric": _numeric_value(field_value),
        "source_document_id": source_document_id,
        "source_section_id": source_section_id,
        "staging_extraction_id": staging_extraction_id,
        "source_raw_id": source_raw_id,
        "source_type": source_type,
        "source_tier": source_tier,
        "model_confidence": model_confidence,
        "source_published_date": source_published_date,
        "filing_type": filing_type,
        "agreement_dated_as_of": agreement_dated_as_of,
        "observed_as_of_date": observed_as_of_date,
        "filing_date": filing_date,
        "extraction_prompt_version": extraction_prompt_version,
        "observation_source_stage": observation_source_stage,
        "observation_fact_key": observation_fact_key,
    }
    cols = [col for col in _OBSERVATION_INSERT_ORDER if col in available]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO transaction_field_observation (
            {", ".join(cols)}
        ) VALUES ({placeholders})
        """,
        tuple(values[col] for col in cols),
    )
    return 1


def _source_filing_type(source_type: str | None) -> str | None:
    if not source_type:
        return None
    if source_type.startswith("SEC_"):
        return source_type
    return None


def _stage_name(default_stage: str, concrete_stage: str) -> str:
    return "BACKFILL" if default_stage == "BACKFILL" else concrete_stage


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def _write_field_group(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    fields: tuple[str, ...],
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    inserted = 0
    source_type = _row_value(row, "source_type")
    for field_name in fields:
        value = _row_value(row, field_name)
        if value is None:
            continue
        inserted += insert_observation(
            conn,
            transaction_id=_row_value(row, "transaction_cluster_id"),
            field_name=field_name,
            field_value=value,
            staging_extraction_id=_row_value(row, "extraction_id"),
            source_raw_id=_row_value(row, "source_raw_id"),
            source_type=source_type,
            source_tier=_row_value(row, "source_tier"),
            model_confidence=_row_value(row, "model_confidence"),
            source_published_date=_row_value(row, "published_date"),
            filing_type=_source_filing_type(source_type),
            extraction_prompt_version=prompt_version,
            observation_source_stage=observation_source_stage,
            bool_as_int_text=True,
            columns=columns,
        )
    return inserted


def _value_observation_items(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _write_value_observations(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    inserted = 0
    source_type = _row_value(row, "source_type")
    for index, item in enumerate(_value_observation_items(_row_value(row, "value_observations"))):
        fact_key = f"value_observations[{index}]"
        field_map = {
            "value_amount": item.get("amount"),
            "value_currency": item.get("currency"),
            "value_type": item.get("type"),
            "value_qualifier": item.get("qualifier") or item.get("basis"),
        }
        for field_name, value in field_map.items():
            if value is None:
                continue
            inserted += insert_observation(
                conn,
                transaction_id=_row_value(row, "transaction_cluster_id"),
                field_name=field_name,
                field_value=value,
                staging_extraction_id=_row_value(row, "extraction_id"),
                source_raw_id=_row_value(row, "source_raw_id"),
                source_type=source_type,
                source_tier=_row_value(row, "source_tier"),
                model_confidence=_row_value(row, "model_confidence"),
                source_published_date=_row_value(row, "published_date"),
                filing_type=_source_filing_type(source_type),
                extraction_prompt_version=prompt_version,
                observation_source_stage=observation_source_stage,
                observation_fact_key=fact_key,
                bool_as_int_text=True,
                columns=columns,
            )
        audit_payload = {
            "amount": item.get("amount"),
            "currency": item.get("currency"),
            "type": item.get("type"),
            "basis": item.get("basis"),
            "qualifier": item.get("qualifier"),
            "evidence": item.get("evidence"),
        }
        inserted += insert_observation(
            conn,
            transaction_id=_row_value(row, "transaction_cluster_id"),
            field_name="value_observation",
            field_value=json.dumps(audit_payload, ensure_ascii=False, sort_keys=True),
            staging_extraction_id=_row_value(row, "extraction_id"),
            source_raw_id=_row_value(row, "source_raw_id"),
            source_type=source_type,
            source_tier=_row_value(row, "source_tier"),
            model_confidence=_row_value(row, "model_confidence"),
            source_published_date=_row_value(row, "published_date"),
            filing_type=_source_filing_type(source_type),
            extraction_prompt_version=prompt_version,
            observation_source_stage=observation_source_stage,
            observation_fact_key=fact_key,
            columns=columns,
        )
    return inserted


MULTIPLE_FIELD_PREFIX = "multiple."


def reported_multiple_field_name(item: dict) -> str | None:
    """The canonical fact key for one stated multiple.

    Composed from the dimensions Product actually has -- multiple_type, period_basis and
    the denominator period end -- in the same way the ledger already composes
    `shares_outstanding.{type}[.{class}]` and `consideration.{form}.{attr}`. Two
    observations that land on this key are two claims about ONE canonical fact, which is
    what makes them candidates for reconciliation rather than two canonical rows.

    Deliberately NOT part of the key: anything describing an adjustment. Product does
    not distinguish adjusted from unadjusted multiples structurally, so Maverick's
    headline 11.5x and its tax-adjusted 10.5x compose the SAME key. That is the point:
    they arrive as a conflict at one key, not as two canonical values a reader cannot
    tell apart.

    An omitted basis or period end contributes an empty segment rather than being
    dropped, so a multiple with no stated basis cannot silently collide with one that
    has a basis.
    """
    mtype = item.get("multiple_type")
    if not mtype:
        return None
    basis = item.get("period_basis") or ""
    period_end = item.get("period_end_date") or ""
    return f"{MULTIPLE_FIELD_PREFIX}{mtype}.{basis}.{period_end}"


# Which staging array becomes which observation field name. The field name IS the role:
# BUYER, SPONSOR_BUYER and PARENT_SELLER are existing V3 §T5 roles, and a party's role is
# recorded by which array it arrived in rather than by a value inside the item.
PARTY_ARRAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("acquirers", "acquirer_party"),
    ("buy_side_sponsors", "buy_side_sponsor_party"),
    ("parent_sellers", "parent_seller_party"),
    ("parent_acquirers", "parent_acquirer_party"),
    ("sell_side_sponsors", "sell_side_sponsor_party"),
)


def _write_party_observations(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    """One observation per source-stated party, so cardinality survives collection.

    PRESERVATION ONLY. These field names are deliberately absent from aggregate's
    _FIELDS, so _load_observation_input drops them at its field-type gate: a party is a
    record of what a source said, not a candidate to reconcile between. Choosing which
    of two sources' buyer lists is right is an identity question, and identity is not
    this slice.

    Per-fact provenance comes from `observation_fact_key` (E4 rule 6), so two buyers
    named in one release stay distinguishable from one buyer named by two releases --
    INSERT OR IGNORE would otherwise collapse them into a single row.

    The whole item is stored as JSON rather than split across fields, matching how
    `value_observation` and `reported_multiple` preserve their facts: one row is one
    party, and it carries everything the source said about that party.
    """
    inserted = 0
    source_type = _row_value(row, "source_type")
    for column, field_name in PARTY_ARRAY_FIELDS:
        for index, item in enumerate(_value_observation_items(_row_value(row, column))):
            name = item.get("name")
            if not name:
                continue
            payload = {"name": name}
            if "type" in item:
                payload["type"] = item.get("type")
            inserted += insert_observation(
                conn,
                transaction_id=_row_value(row, "transaction_cluster_id"),
                field_name=field_name,
                field_value=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                staging_extraction_id=_row_value(row, "extraction_id"),
                source_raw_id=_row_value(row, "source_raw_id"),
                source_type=source_type,
                source_tier=_row_value(row, "source_tier"),
                model_confidence=_row_value(row, "model_confidence"),
                source_published_date=_row_value(row, "published_date"),
                filing_type=_source_filing_type(source_type),
                extraction_prompt_version=prompt_version,
                observation_source_stage=observation_source_stage,
                observation_fact_key=f"{column}[{index}]",
                columns=columns,
            )
    return inserted


def _write_reported_multiples(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    """Two rows per stated multiple: one preserved, one reconciled.

    Modelled on `_write_value_observations`, which solved this exact problem for deal
    values. Each stated multiple produces:

      `reported_multiple`                  PRESERVATION. The whole item as JSON,
                                           including the source's verbatim wording.
                                           This field_name is absent from aggregate's
                                           _FIELDS, so _load_observation_input drops it
                                           from reconciliation -- it is a record, never
                                           a candidate. Every stated multiple survives
                                           here whatever reconciliation later decides.

      `multiple.{type}.{basis}.{end}`      RECONCILIATION. The numeric value at the
                                           canonical fact key. Two observations at one
                                           key are one fact claimed twice.

    Per-fact provenance comes from `observation_fact_key` (E4 rule 6), so two multiples
    stated in one article stay distinguishable from one multiple corroborated by two
    articles -- INSERT OR IGNORE would otherwise collapse them.

    Nothing is computed, ranked or adjudicated here. This is a writer.
    """
    inserted = 0
    source_type = _row_value(row, "source_type")
    items = _value_observation_items(_row_value(row, "reported_multiples"))
    for index, item in enumerate(items):
        fact_key = f"reported_multiples[{index}]"
        common = {
            "transaction_id": _row_value(row, "transaction_cluster_id"),
            "staging_extraction_id": _row_value(row, "extraction_id"),
            "source_raw_id": _row_value(row, "source_raw_id"),
            "source_type": source_type,
            "source_tier": _row_value(row, "source_tier"),
            "model_confidence": _row_value(row, "model_confidence"),
            "source_published_date": _row_value(row, "published_date"),
            "filing_type": _source_filing_type(source_type),
            "extraction_prompt_version": prompt_version,
            "observation_source_stage": observation_source_stage,
            "observation_fact_key": fact_key,
            "columns": columns,
        }
        # Preservation first, so a multiple whose type is unusable for a key is still
        # recorded rather than lost.
        inserted += insert_observation(
            conn,
            field_name="reported_multiple",
            field_value=json.dumps({
                "multiple_type": item.get("multiple_type"),
                "multiple_value": item.get("multiple_value"),
                "period_basis": item.get("period_basis"),
                "period_end_date": item.get("period_end_date"),
                "numerator_value_type": item.get("numerator_value_type"),
                "as_reported_text": item.get("as_reported_text"),
            }, ensure_ascii=False, sort_keys=True),
            **common,
        )
        field_name = reported_multiple_field_name(item)
        if field_name is None or item.get("multiple_value") is None:
            continue
        inserted += insert_observation(
            conn,
            field_name=field_name,
            field_value=item.get("multiple_value"),
            **common,
        )
    return inserted


def _write_consideration_observations(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    raw = _row_value(row, "consideration_components")
    if not raw:
        return 0
    try:
        components = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return 0
    if not isinstance(components, list):
        return 0

    inserted = 0
    source_type = _row_value(row, "source_type")
    for comp in components:
        if not isinstance(comp, dict):
            continue
        form = comp.get("form") or "UNKNOWN"
        for attr in ("per_share_amount", "amount", "exchange_ratio"):
            value = comp.get(attr)
            if value is None:
                continue
            inserted += insert_observation(
                conn,
                transaction_id=_row_value(row, "transaction_cluster_id"),
                field_name=f"consideration.{form}.{attr}",
                field_value=value,
                staging_extraction_id=_row_value(row, "extraction_id"),
                source_raw_id=_row_value(row, "source_raw_id"),
                source_type=source_type,
                source_tier=_row_value(row, "source_tier"),
                model_confidence=_row_value(row, "model_confidence"),
                source_published_date=_row_value(row, "published_date"),
                filing_type=_source_filing_type(source_type),
                extraction_prompt_version=prompt_version,
                observation_source_stage=observation_source_stage,
                bool_as_int_text=True,
                columns=columns,
            )
    return inserted


def _write_consideration_json_observation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    prompt_version: str | None,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    raw = _row_value(row, "consideration_components")
    if raw is None:
        return 0
    source_type = _row_value(row, "source_type")
    return insert_observation(
        conn,
        transaction_id=_row_value(row, "transaction_cluster_id"),
        field_name="consideration_components",
        field_value=raw,
        staging_extraction_id=_row_value(row, "extraction_id"),
        source_raw_id=_row_value(row, "source_raw_id"),
        source_type=source_type,
        source_tier=_row_value(row, "source_tier"),
        model_confidence=_row_value(row, "model_confidence"),
        source_published_date=_row_value(row, "published_date"),
        filing_type=_source_filing_type(source_type),
        extraction_prompt_version=prompt_version,
        observation_source_stage=observation_source_stage,
        columns=columns,
    )


def _write_source_marker_observation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    observation_source_stage: str,
    columns: set[str],
) -> int:
    source_type = _row_value(row, "source_type")
    return insert_observation(
        conn,
        transaction_id=_row_value(row, "transaction_cluster_id"),
        field_name=SOURCE_ROW_PRESENT_FIELD,
        field_value="1",
        staging_extraction_id=_row_value(row, "extraction_id"),
        source_raw_id=_row_value(row, "source_raw_id"),
        source_type=source_type,
        source_tier=_row_value(row, "source_tier"),
        model_confidence=_row_value(row, "model_confidence") or "MEDIUM",
        source_published_date=_row_value(row, "published_date"),
        filing_type=_source_filing_type(source_type),
        observation_source_stage=observation_source_stage,
        columns=columns,
    )


def write_staging_observations_for_extraction(
    conn: sqlite3.Connection,
    extraction_id: int,
    *,
    observation_source_stage: str,
    include_stage3: bool = False,
    include_hc: bool = False,
    include_lc: bool = False,
    include_funding: bool = False,
) -> int:
    """Dual-write or backfill Stage 3/4/4b/7 source-row observations."""
    row = conn.execute(
        """
        SELECT se.*, sr.source_type, sr.source_tier, sr.published_date
        FROM staging_extraction se
        JOIN source_raw sr ON sr.source_raw_id = se.source_raw_id
        WHERE se.extraction_id = ?
        """,
        (extraction_id,),
    ).fetchone()
    if row is None:
        return 0

    columns = _table_columns(conn, "transaction_field_observation")
    inserted = 0
    inserted += _write_source_marker_observation(
        conn,
        row,
        observation_source_stage=observation_source_stage,
        columns=columns,
    )
    if include_stage3:
        inserted += _write_field_group(
            conn,
            row,
            fields=STAGE3_FIELDS,
            prompt_version=_row_value(row, "dt_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "DT_CLASSIFY"),
            columns=columns,
        )
    # Funding runs BEFORE HC deliberately. round_size is the one field in both
    # FUNDING_FIELDS and HC_FIELDS; whichever group writes first wins the
    # INSERT OR IGNORE dedup and sets the observation_source_stage. Writing funding
    # first means a Stage 4b row stamps round_size FUNDING_HC_EXTRACT (truthful — it
    # came from the funding stage). On the M&A path include_funding is not requested,
    # so round_size stamps HC_EXTRACT (also truthful). Do not reorder back.
    if include_funding:
        inserted += _write_field_group(
            conn,
            row,
            fields=FUNDING_FIELDS,
            prompt_version=_row_value(row, "hc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "FUNDING_HC_EXTRACT"),
            columns=columns,
        )
    if include_hc:
        inserted += _write_field_group(
            conn,
            row,
            fields=HC_FIELDS,
            prompt_version=_row_value(row, "hc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "HC_EXTRACT"),
            columns=columns,
        )
        inserted += _write_value_observations(
            conn,
            row,
            prompt_version=_row_value(row, "hc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "HC_EXTRACT"),
            columns=columns,
        )
        inserted += _write_reported_multiples(
            conn,
            row,
            prompt_version=_row_value(row, "hc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "HC_EXTRACT"),
            columns=columns,
        )
        inserted += _write_party_observations(
            conn,
            row,
            prompt_version=_row_value(row, "hc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "HC_EXTRACT"),
            columns=columns,
        )
    if include_lc:
        inserted += _write_field_group(
            conn,
            row,
            fields=LC_SCALAR_FIELDS,
            prompt_version=_row_value(row, "lc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "LC_EXTRACT"),
            columns=columns,
        )
        inserted += _write_consideration_json_observation(
            conn,
            row,
            prompt_version=_row_value(row, "lc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "LC_EXTRACT"),
            columns=columns,
        )
        inserted += _write_consideration_observations(
            conn,
            row,
            prompt_version=_row_value(row, "lc_prompt_version"),
            observation_source_stage=_stage_name(observation_source_stage, "LC_EXTRACT"),
            columns=columns,
        )
    return inserted


def backfill_observation_transaction_ids(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """
        UPDATE transaction_field_observation
        SET transaction_id = (
            SELECT se.transaction_cluster_id
            FROM staging_extraction se
            WHERE se.extraction_id = transaction_field_observation.staging_extraction_id
        )
        WHERE transaction_id IS NULL
          AND staging_extraction_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM staging_extraction se
              WHERE se.extraction_id = transaction_field_observation.staging_extraction_id
                AND se.transaction_cluster_id IS NOT NULL
          )
        """
    )
    return cur.rowcount if cur.rowcount is not None else 0


def backfill_staging_observations(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT se.extraction_id
        FROM staging_extraction se
        WHERE se.transaction_cluster_id IS NOT NULL
          AND se.status IN ('LC_EXTRACTED', 'CLUSTERED', 'AGGREGATED')
        ORDER BY se.extraction_id
        """
    ).fetchall()
    before = conn.total_changes
    for row in rows:
        write_staging_observations_for_extraction(
            conn,
            row["extraction_id"],
            observation_source_stage="BACKFILL",
            include_stage3=True,
            include_hc=True,
            include_lc=True,
            include_funding=True,
        )
    return conn.total_changes - before


def extract_agreement_dated_as_of(raw_text: str | None, *, char_limit: int = 8000) -> str | None:
    if not raw_text:
        return None
    matches = _DATED_AS_OF_RE.findall(raw_text[:char_limit])
    unique_dates: set[str] = set()
    for month, day, year in matches:
        try:
            parsed = datetime.strptime(f"{month.title()} {int(day)}, {year}", "%B %d, %Y")
        except ValueError:
            continue
        unique_dates.add(parsed.date().isoformat())
    if len(unique_dates) == 1:
        return next(iter(unique_dates))
    return None


def backfill_agreement_observation_provenance(conn: sqlite3.Connection) -> int:
    before = conn.total_changes
    conn.execute(
        """
        UPDATE transaction_field_observation
        SET
            filing_type = COALESCE(
                filing_type,
                (
                    SELECT td.filing_type
                    FROM transaction_document td
                    WHERE td.document_id = transaction_field_observation.source_document_id
                )
            ),
            source_type = COALESCE(
                source_type,
                (
                    SELECT td.filing_type
                    FROM transaction_document td
                    WHERE td.document_id = transaction_field_observation.source_document_id
                )
            ),
            source_tier = COALESCE(source_tier, 'T1'),
            observation_source_stage = COALESCE(observation_source_stage, 'AGREEMENT_EXTRACT')
        WHERE source_document_id IS NOT NULL
        """
    )

    for row in conn.execute(
        """
        SELECT document_id, raw_text
        FROM transaction_document
        WHERE raw_text IS NOT NULL
        """
    ).fetchall():
        agreement_date = extract_agreement_dated_as_of(row["raw_text"])
        if not agreement_date:
            continue
        conn.execute(
            """
            UPDATE transaction_field_observation
            SET agreement_dated_as_of = COALESCE(agreement_dated_as_of, ?)
            WHERE source_document_id = ?
              AND agreement_dated_as_of IS NULL
            """,
            (agreement_date, row["document_id"]),
        )
    return conn.total_changes - before
