"""Drop 3.32a organization participant backfill helpers.

This module is intentionally additive. It reads existing flat transaction
fields and writes only the 3.32a participant/entity tables.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any

PARTICIPANT_TABLES = (
    "entity",
    "entity_alias",
    "transaction_participant_group",
    "transaction_participant",
)

_HASH_LEN = 16
_SOURCE_STAGE_TR = "TRANSACTION_RECORD_BACKFILL_332A"
_SOURCE_STAGE_AGREEMENT = "AGREEMENT_OBSERVATION_BACKFILL_332A"
_MAX_REVIEW_EXAMPLES = 12

_WHITESPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_GENERIC_GROUP_RE = re.compile(
    r"^(buyer group|investor group|seller group|consortium|acquirer group)$"
    r"|(\bgroup\s+(led|formed|organized|comprised)\s+by\b)"
    r"|(\b(consortium|investor group|buyer group)\s+led\s+by\b)"
    r"|(\bconsortium\b)",
    re.IGNORECASE,
)
_UNSAFE_SPLIT_RE = re.compile(
    r"\b(together with|alongside|including|plus|led by|consisting of|comprised of)\b|/",
    re.IGNORECASE,
)
_AND_RE = re.compile(r"\s+and\s+", re.IGNORECASE)

_LEGAL_SUFFIX_SEGMENTS = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "lp",
    "llp",
    "ltd",
    "limited",
    "plc",
}


@dataclass(frozen=True)
class _ParseResult:
    names: tuple[str, ...]
    review_status: str
    reason: str | None = None


class _Plan:
    def __init__(self) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        self.aliases: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.participants: dict[str, dict[str, Any]] = {}
        self.review_examples: list[dict[str, Any]] = []

    def add_review_example(
        self,
        *,
        transaction_id: str,
        field_name: str,
        field_value: str,
        reason: str,
    ) -> None:
        if len(self.review_examples) >= _MAX_REVIEW_EXAMPLES:
            return
        self.review_examples.append(
            {
                "transaction_id": transaction_id,
                "field_name": field_name,
                "field_value": field_value,
                "reason": reason,
            }
        )


def _clean_name(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", str(value).strip())
    cleaned = cleaned.strip(" '\"\t\r\n")
    return cleaned or None


def _normalized_name(name: str) -> str:
    normalized = name.lower().replace("&", " and ")
    normalized = _NON_WORD_RE.sub(" ", normalized)
    return _WHITESPACE_RE.sub(" ", normalized).strip()


def _hash_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{prefix}_{digest}"


def _transaction_entity_id(transaction_id: str, name: str) -> str:
    return _hash_id("ent", "txn", transaction_id, _normalized_name(name))


def _participant_id(
    transaction_id: str,
    entity_id: str,
    participant_role: str,
    side: str,
    group_id: str | None,
) -> str:
    return _hash_id("tp", transaction_id, entity_id, participant_role, side, group_id or "")


def _group_id(
    transaction_id: str,
    group_type: str,
    side: str,
    disclosed_group_name: str | None,
) -> str:
    return _hash_id("tpg", transaction_id, group_type, side, disclosed_group_name or "")


def _is_legal_suffix_segment(value: str) -> bool:
    normalized = _normalized_name(value).replace(" ", "")
    return normalized in _LEGAL_SUFFIX_SEGMENTS


def _safe_split_piece(value: str) -> bool:
    words = _normalized_name(value).split()
    if len(words) >= 2:
        return True
    alnum = re.sub(r"[^A-Za-z0-9]", "", value)
    if len(words) == 1 and alnum.isupper() and len(alnum) >= 2:
        return True
    return False


def _parse_name_list(value: str) -> _ParseResult:
    """Conservatively parse a flat name list.

    Comma-delimited sponsor fields are common, but commas also occur inside
    firm names. When splitting is not clearly safe, keep the full disclosed
    string as one reviewable participant.
    """
    name = _clean_name(value)
    if not name:
        return _ParseResult((), "UNREVIEWED")

    if _UNSAFE_SPLIT_RE.search(name):
        return _ParseResult((name,), "NEEDS_REVIEW", "ambiguous joiner in name list")

    if ";" in name:
        parts = tuple(_clean_name(part) for part in name.split(";"))
        names = tuple(part for part in parts if part)
        if len(names) > 1 and all(_safe_split_piece(part) for part in names):
            return _ParseResult(names, "UNREVIEWED")
        return _ParseResult((name,), "NEEDS_REVIEW", "semicolon-delimited list is not safely parseable")

    if "," in name:
        parts = tuple(_clean_name(part) for part in name.split(","))
        names = tuple(part for part in parts if part)
        if len(names) == 2 and _is_legal_suffix_segment(names[1]):
            return _ParseResult((name,), "UNREVIEWED")
        if any(_is_legal_suffix_segment(part) for part in names[1:]):
            return _ParseResult((name,), "NEEDS_REVIEW", "comma may separate legal suffix, not entities")
        if len(names) > 1 and all(_safe_split_piece(part) for part in names):
            return _ParseResult(names, "UNREVIEWED")
        return _ParseResult((name,), "NEEDS_REVIEW", "comma-delimited list is not safely parseable")

    and_parts = tuple(_clean_name(part) for part in _AND_RE.split(name))
    and_names = tuple(part for part in and_parts if part)
    if len(and_names) == 2 and all(_safe_split_piece(part) for part in and_names):
        return _ParseResult(and_names, "UNREVIEWED")
    if len(and_names) > 2:
        return _ParseResult((name,), "NEEDS_REVIEW", "multi-party 'and' list is not safely parseable")

    return _ParseResult((name,), "UNREVIEWED")


def _is_generic_group_label(name: str | None) -> bool:
    if not name:
        return False
    return bool(_GENERIC_GROUP_RE.search(name.strip()))


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _existing_ids(conn: sqlite3.Connection, table_name: str, id_col: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {row[0] for row in conn.execute(f"SELECT {id_col} FROM {table_name}")}


def _alias_exists(conn: sqlite3.Connection, alias: dict[str, Any]) -> bool:
    if not _table_exists(conn, "entity_alias"):
        return False
    return conn.execute(
        """
        SELECT 1
        FROM entity_alias
        WHERE entity_id=?
          AND normalized_alias=?
          AND COALESCE(alias_type, '')=COALESCE(?, '')
          AND is_current=1
        """,
        (alias["entity_id"], alias["normalized_alias"], alias["alias_type"]),
    ).fetchone() is not None


def _table_count(conn: sqlite3.Connection, table_name: str) -> int | None:
    if not _table_exists(conn, table_name):
        return None
    return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]


def _add_entity(plan: _Plan, transaction_id: str, name: str, review_status: str) -> str:
    entity_id = _transaction_entity_id(transaction_id, name)
    normalized = _normalized_name(name)
    plan.entities.setdefault(
        entity_id,
        {
            "entity_id": entity_id,
            "canonical_name": name,
            "normalized_name": normalized,
            "display_name": name,
            "review_status": review_status,
            "researcher_notes": (
                "Created by Drop 3.32a transaction-scoped participant backfill."
                if review_status == "UNREVIEWED"
                else "Needs review: ambiguous parsed organization name from flat transaction field."
            ),
        },
    )
    plan.aliases.setdefault(
        (entity_id, normalized, "DISCLOSED_NAME"),
        {
            "entity_id": entity_id,
            "alias_name": name,
            "normalized_alias": normalized,
            "alias_type": "DISCLOSED_NAME",
        },
    )
    return entity_id


def _add_group(
    plan: _Plan,
    *,
    transaction_id: str,
    side: str,
    group_type: str,
    group_label: str,
    disclosed_group_name: str | None,
    review_status: str = "UNREVIEWED",
    notes: str | None = None,
) -> str:
    group_id = _group_id(transaction_id, group_type, side, disclosed_group_name or group_label)
    plan.groups.setdefault(
        group_id,
        {
            "group_id": group_id,
            "transaction_id": transaction_id,
            "side": side,
            "group_type": group_type,
            "group_label": group_label,
            "disclosed_group_name": disclosed_group_name,
            "review_status": review_status,
            "notes": notes,
        },
    )
    return group_id


def _add_participant(
    plan: _Plan,
    *,
    transaction_id: str,
    entity_id: str,
    side: str,
    participant_role: str,
    group_id: str | None = None,
    is_primary: int | None = None,
    is_lead: int | None = None,
    is_existing_investor: int | None = None,
    is_new_investor: int | None = None,
    source_stage: str = _SOURCE_STAGE_TR,
    model_confidence: str | None = None,
    evidence_text: str | None = None,
    review_status: str = "UNREVIEWED",
    researcher_notes: str | None = None,
) -> str:
    participant_id = _participant_id(transaction_id, entity_id, participant_role, side, group_id)
    plan.participants.setdefault(
        participant_id,
        {
            "participant_id": participant_id,
            "transaction_id": transaction_id,
            "entity_id": entity_id,
            "group_id": group_id,
            "side": side,
            "participant_role": participant_role,
            "is_primary": is_primary,
            "is_lead": is_lead,
            "is_existing_investor": is_existing_investor,
            "is_new_investor": is_new_investor,
            "source_stage": source_stage,
            "model_confidence": model_confidence,
            "evidence_text": evidence_text,
            "review_status": review_status,
            "researcher_notes": researcher_notes,
        },
    )
    return participant_id


def _load_parent_acquirer_observations(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    if not _table_exists(conn, "transaction_field_observation"):
        return {}
    rows = conn.execute(
        """
        SELECT transaction_id, field_value, model_confidence
        FROM transaction_field_observation
        WHERE field_name='parent_acquirer_name'
          AND is_current=1
          AND transaction_id IS NOT NULL
          AND field_value IS NOT NULL
          AND TRIM(field_value)!=''
        ORDER BY transaction_id, observation_id
        """
    ).fetchall()
    by_txn: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        name = _clean_name(row["field_value"])
        if not name:
            continue
        by_txn.setdefault(row["transaction_id"], {})[_normalized_name(name)] = {
            "name": name,
            "model_confidence": row["model_confidence"],
        }
    return {txn_id: list(values.values()) for txn_id, values in by_txn.items()}


def _plan_named_participant(
    plan: _Plan,
    *,
    transaction_id: str,
    field_name: str,
    name: str | None,
    side: str,
    participant_role: str,
    group_id: str | None = None,
    is_primary: int | None = None,
    review_status: str = "UNREVIEWED",
    source_stage: str = _SOURCE_STAGE_TR,
    model_confidence: str | None = None,
) -> str | None:
    cleaned = _clean_name(name)
    if not cleaned:
        return None
    entity_id = _add_entity(plan, transaction_id, cleaned, review_status)
    _add_participant(
        plan,
        transaction_id=transaction_id,
        entity_id=entity_id,
        group_id=group_id,
        side=side,
        participant_role=participant_role,
        is_primary=is_primary,
        source_stage=source_stage,
        model_confidence=model_confidence,
        evidence_text=f"{field_name}: {cleaned}",
        review_status=review_status,
        researcher_notes=(
            "Needs review: flat field could not be safely split."
            if review_status == "NEEDS_REVIEW"
            else None
        ),
    )
    return entity_id


def _plan_parsed_participants(
    plan: _Plan,
    *,
    transaction_id: str,
    field_name: str,
    value: str | None,
    side: str,
    participant_role: str,
    group_id: str | None,
    multi_group_type: str | None,
    multi_group_label: str | None,
) -> list[str]:
    cleaned = _clean_name(value)
    if not cleaned:
        return []

    parsed = _parse_name_list(cleaned)
    review_status = parsed.review_status
    if review_status == "NEEDS_REVIEW" and parsed.reason:
        plan.add_review_example(
            transaction_id=transaction_id,
            field_name=field_name,
            field_value=cleaned,
            reason=parsed.reason,
        )

    target_group_id = group_id
    if not target_group_id and len(parsed.names) > 1 and multi_group_type and multi_group_label:
        target_group_id = _add_group(
            plan,
            transaction_id=transaction_id,
            side=side,
            group_type=multi_group_type,
            group_label=multi_group_label,
            disclosed_group_name=cleaned,
        )

    entity_ids: list[str] = []
    for idx, name in enumerate(parsed.names):
        entity_id = _add_entity(plan, transaction_id, name, review_status)
        _add_participant(
            plan,
            transaction_id=transaction_id,
            entity_id=entity_id,
            group_id=target_group_id,
            side=side,
            participant_role=participant_role,
            is_primary=(
                1
                if len(parsed.names) == 1
                and participant_role in {"ACQUIRER", "BUYER_PLATFORM"}
                else None
            ),
            source_stage=_SOURCE_STAGE_TR,
            evidence_text=f"{field_name}: {cleaned}",
            review_status=review_status,
            researcher_notes=(
                f"Needs review: {parsed.reason}."
                if review_status == "NEEDS_REVIEW" and parsed.reason
                else None
            ),
        )
        entity_ids.append(entity_id)
    return entity_ids


def _plan_transaction(
    plan: _Plan,
    row: sqlite3.Row,
    parent_acquirer_obs: list[dict[str, Any]],
) -> None:
    txn_id = row["transaction_id"]
    acquirer_type = row["acquirer_type"]
    acquirer_name = _clean_name(row["acquirer_name"])
    sponsor_name = _clean_name(row["acquirer_sponsor_name"])
    merger_sub_name = _clean_name(row["acquirer_merger_sub_name"])

    _plan_named_participant(
        plan,
        transaction_id=txn_id,
        field_name="transaction_record.target_name",
        name=row["target_name"],
        side="TARGET",
        participant_role="TARGET",
        is_primary=1,
    )

    consortium_group_id = None
    if acquirer_type == "CONSORTIUM":
        consortium_group_id = _add_group(
            plan,
            transaction_id=txn_id,
            side="BUYER",
            group_type="CONSORTIUM",
            group_label="Acquirer consortium",
            disclosed_group_name=acquirer_name,
        )

    if acquirer_type == "CONSORTIUM":
        if _is_generic_group_label(acquirer_name):
            if acquirer_name:
                plan.add_review_example(
                    transaction_id=txn_id,
                    field_name="transaction_record.acquirer_name",
                    field_value=acquirer_name,
                    reason="generic consortium label stored as group, not synthetic entity",
                )
        else:
            _plan_parsed_participants(
                plan,
                transaction_id=txn_id,
                field_name="transaction_record.acquirer_name",
                value=acquirer_name,
                side="BUYER",
                participant_role="ACQUIRER",
                group_id=consortium_group_id,
                multi_group_type=None,
                multi_group_label=None,
            )
    else:
        acquirer_role = "BUYER_PLATFORM" if acquirer_type == "PE_PORTFOLIO" else "ACQUIRER"
        _plan_named_participant(
            plan,
            transaction_id=txn_id,
            field_name="transaction_record.acquirer_name",
            name=acquirer_name,
            side="BUYER",
            participant_role=acquirer_role,
            is_primary=1,
        )

    _plan_parsed_participants(
        plan,
        transaction_id=txn_id,
        field_name="transaction_record.parent_seller_name",
        value=row["parent_seller_name"],
        side="SELLER",
        participant_role="PARENT_SELLER",
        group_id=None,
        multi_group_type="SELLER_GROUP",
        multi_group_label="Seller group",
    )

    _plan_parsed_participants(
        plan,
        transaction_id=txn_id,
        field_name="transaction_record.acquirer_sponsor_name",
        value=sponsor_name,
        side="BUYER",
        participant_role="BUYER_SPONSOR",
        group_id=consortium_group_id,
        multi_group_type="INVESTOR_GROUP",
        multi_group_label="Buyer sponsor group",
    )

    _plan_named_participant(
        plan,
        transaction_id=txn_id,
        field_name="transaction_record.acquirer_merger_sub_name",
        name=merger_sub_name,
        side="BUYER",
        participant_role="MERGER_SUB",
    )

    parent_review_status = "NEEDS_REVIEW" if len(parent_acquirer_obs) > 1 else "UNREVIEWED"
    for obs in parent_acquirer_obs:
        if parent_review_status == "NEEDS_REVIEW":
            plan.add_review_example(
                transaction_id=txn_id,
                field_name="transaction_field_observation.parent_acquirer_name",
                field_value=obs["name"],
                reason="multiple parent acquirer observations exist for transaction",
            )
        _plan_named_participant(
            plan,
            transaction_id=txn_id,
            field_name="transaction_field_observation.parent_acquirer_name",
            name=obs["name"],
            side="BUYER",
            participant_role="PARENT_ACQUIRER",
            source_stage=_SOURCE_STAGE_AGREEMENT,
            model_confidence=obs.get("model_confidence"),
            review_status=parent_review_status,
        )


def _build_plan(conn: sqlite3.Connection) -> tuple[_Plan, int]:
    parent_acquirers = _load_parent_acquirer_observations(conn)
    rows = conn.execute(
        """
        SELECT transaction_id, target_name, acquirer_name, acquirer_type,
               parent_seller_name, acquirer_sponsor_name, acquirer_merger_sub_name
        FROM transaction_record
        WHERE is_current=1
        ORDER BY transaction_id
        """
    ).fetchall()
    plan = _Plan()
    for row in rows:
        _plan_transaction(plan, row, parent_acquirers.get(row["transaction_id"], []))
    return plan, len(rows)


def _planned_counts(plan: _Plan) -> dict[str, int]:
    return {
        "entity": len(plan.entities),
        "entity_alias": len(plan.aliases),
        "transaction_participant_group": len(plan.groups),
        "transaction_participant": len(plan.participants),
    }


def _would_insert_counts(conn: sqlite3.Connection, plan: _Plan) -> dict[str, int]:
    entity_ids = _existing_ids(conn, "entity", "entity_id")
    group_ids = _existing_ids(conn, "transaction_participant_group", "group_id")
    participant_ids = _existing_ids(conn, "transaction_participant", "participant_id")
    return {
        "entity": sum(1 for entity_id in plan.entities if entity_id not in entity_ids),
        "entity_alias": sum(1 for alias in plan.aliases.values() if not _alias_exists(conn, alias)),
        "transaction_participant_group": sum(
            1 for group_id in plan.groups if group_id not in group_ids
        ),
        "transaction_participant": sum(
            1 for participant_id in plan.participants if participant_id not in participant_ids
        ),
    }


def _insert_plan(conn: sqlite3.Connection, plan: _Plan) -> dict[str, int]:
    inserted = Counter()
    for entity in plan.entities.values():
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO entity (
                entity_id, entity_kind, canonical_name, normalized_name,
                display_name, review_status, researcher_notes
            ) VALUES (?, 'ORGANIZATION', ?, ?, ?, ?, ?)
            """,
            (
                entity["entity_id"],
                entity["canonical_name"],
                entity["normalized_name"],
                entity["display_name"],
                entity["review_status"],
                entity["researcher_notes"],
            ),
        )
        inserted["entity"] += cur.rowcount

    for alias in plan.aliases.values():
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO entity_alias (
                entity_id, alias_name, normalized_alias, alias_type, is_current
            ) VALUES (?, ?, ?, ?, 1)
            """,
            (
                alias["entity_id"],
                alias["alias_name"],
                alias["normalized_alias"],
                alias["alias_type"],
            ),
        )
        inserted["entity_alias"] += cur.rowcount

    for group in plan.groups.values():
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO transaction_participant_group (
                group_id, transaction_id, side, group_type, group_label,
                disclosed_group_name, review_status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group["group_id"],
                group["transaction_id"],
                group["side"],
                group["group_type"],
                group["group_label"],
                group["disclosed_group_name"],
                group["review_status"],
                group["notes"],
            ),
        )
        inserted["transaction_participant_group"] += cur.rowcount

    for participant in plan.participants.values():
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO transaction_participant (
                participant_id, transaction_id, entity_id, group_id, side,
                participant_role, is_primary, is_lead, is_existing_investor,
                is_new_investor, source_stage, model_confidence, evidence_text,
                review_status, researcher_notes, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                participant["participant_id"],
                participant["transaction_id"],
                participant["entity_id"],
                participant["group_id"],
                participant["side"],
                participant["participant_role"],
                participant["is_primary"],
                participant["is_lead"],
                participant["is_existing_investor"],
                participant["is_new_investor"],
                participant["source_stage"],
                participant["model_confidence"],
                participant["evidence_text"],
                participant["review_status"],
                participant["researcher_notes"],
            ),
        )
        inserted["transaction_participant"] += cur.rowcount

    return {table: inserted[table] for table in PARTICIPANT_TABLES}


def participant_table_counts(conn: sqlite3.Connection) -> dict[str, int | None]:
    return {table: _table_count(conn, table) for table in PARTICIPANT_TABLES}


def participant_counts_by_role(conn: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(conn, "transaction_participant"):
        return {}
    return {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT participant_role, COUNT(*)
            FROM transaction_participant
            WHERE is_current=1
            GROUP BY participant_role
            ORDER BY participant_role
            """
        )
    }


def group_counts_by_type(conn: sqlite3.Connection) -> dict[str, int]:
    if not _table_exists(conn, "transaction_participant_group"):
        return {}
    return {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT group_type, COUNT(*)
            FROM transaction_participant_group
            GROUP BY group_type
            ORDER BY group_type
            """
        )
    }


def backfill_participants(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict[str, Any]:
    """Plan or write 3.32a organization participants.

    The function does not commit. Callers should commit after a successful
    non-dry-run invocation or roll back on errors.
    """
    plan, transaction_count = _build_plan(conn)
    planned = _planned_counts(plan)
    would_insert = _would_insert_counts(conn, plan)
    inserted = {table: 0 for table in PARTICIPANT_TABLES}
    if not dry_run:
        inserted = _insert_plan(conn, plan)

    return {
        "dry_run": dry_run,
        "transactions_scanned": transaction_count,
        "planned_counts": planned,
        "would_insert_counts": would_insert,
        "inserted_counts": inserted,
        "needs_review_planned": sum(
            1
            for participant in plan.participants.values()
            if participant["review_status"] == "NEEDS_REVIEW"
        ),
        "needs_review_examples": plan.review_examples,
        "planned_participant_counts_by_role": dict(
            sorted(
                Counter(
                    participant["participant_role"]
                    for participant in plan.participants.values()
                ).items()
            )
        ),
        "planned_group_counts_by_type": dict(
            sorted(Counter(group["group_type"] for group in plan.groups.values()).items())
        ),
        "row_counts": participant_table_counts(conn),
        "participant_counts_by_role": participant_counts_by_role(conn),
        "group_counts_by_type": group_counts_by_type(conn),
    }
