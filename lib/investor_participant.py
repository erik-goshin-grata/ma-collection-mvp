"""Materialize funding investors into the canonical entity / transaction_participant
model.

Bridges stages/funding_hc_extract.py's structured `investors[]` output --
already persisted to `staging_investor`, one row per investor per source
extraction -- into the existing canonical participant model, once transaction
identity exists (Stage 9's `transaction_record` upsert). Follows the same
per-transaction entity-identity scheme `lib/participant_backfill.py` already
uses: `entity_id` is a hash of `(transaction_id, normalized investor name)`,
so the same investor named by two independent sources for the same
transaction resolves to exactly one canonical `transaction_participant` row.

WHAT THIS IS NOT

- Not a new canonical table. Reuses `entity` / `transaction_participant`
  exactly as they exist today.
- Not cross-transaction entity resolution. Like `lib/participant_backfill.py`,
  entity identity is scoped to `(transaction_id, normalized_name)` -- the same
  investor named in two different deals gets two different `entity_id`
  values. A true cross-transaction canonical entity is separate, larger work.
- Not a synthesizer. Only `staging_investor` rows that already carry a name
  are read; Funding HC 0.8 already refuses to author an unnamed investor
  entry (an unnamed group never becomes a structured row), so there is
  nothing to filter here on that account.
- Does not persist `lead_investor_rank`, `investment_amount`, or
  `investment_currency` -- `transaction_participant` has no columns for
  these today, and none is added here. Reported as an unsupported-attribute
  gap rather than answered by adding schema.

NEW VOCABULARY: `participant_role = "INVESTOR"`, `side = "INVESTOR"`. Neither
value existed before this file. An investor in a funding round is not a
BUYER, SELLER or TARGET in the M&A sense that vocabulary was built for, so
reusing one of those values would misrepresent the relationship; both
columns are unconstrained TEXT with no CHECK/ENUM in schema, so adding this
value costs nothing structurally.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lib.participant_backfill import (
    _normalized_name,
    _participant_id as _pb_participant_id,
    _transaction_entity_id,
)

_PARTICIPANT_ROLE = "INVESTOR"
_SIDE = "INVESTOR"
_SOURCE_STAGE = "FUNDING_HC_EXTRACT"


def _participant_id(transaction_id: str, entity_id: str) -> str:
    # Reuses lib.participant_backfill's own participant-id scheme so the two
    # writers can never collide or diverge on how a participant_id is formed.
    return _pb_participant_id(transaction_id, entity_id, _PARTICIPANT_ROLE, _SIDE, None)


def materialize_investor_participants(
    conn: sqlite3.Connection, transaction_id: str, log: Any = None,
) -> dict[str, int]:
    """Bridge staging_investor rows for one transaction into transaction_participant.

    Reads every staging_investor row for every staging_extraction row sharing
    this transaction_cluster_id (i.e. every source contributing to this
    transaction), reconciles duplicate observations of the same investor
    across sources, and upserts exactly one entity + transaction_participant
    row per resolved investor.

    Must be called after the transaction_record row for `transaction_id`
    already exists -- transaction_participant.transaction_id is a real FK.

    Source-level staging_investor rows are read only, never modified.

    Returns {"investors_seen": N, "participants_written": M}, where N counts
    source-level rows read and M counts canonical participants newly written
    (idempotent re-runs report 0 written once a transaction's investors are
    already materialized, matching INSERT OR IGNORE / lib.participant_backfill
    convention -- a later source that changes an already-written flag does
    not retroactively update the canonical row; see the module docstring).
    """
    rows = conn.execute(
        """
        SELECT si.name, si.is_lead, si.is_new_investor, si.is_existing_investor
        FROM staging_investor si
        JOIN staging_extraction se ON se.extraction_id = si.extraction_id
        WHERE se.transaction_cluster_id = ?
        """,
        (transaction_id,),
    ).fetchall()

    if not rows:
        return {"investors_seen": 0, "participants_written": 0}

    # Reconcile duplicate observations of the same investor across sources
    # into one resolved record before writing anything. "Any source says so"
    # wins for each boolean flag: a single source stating is_lead=1, or
    # is_new_investor/is_existing_investor=1, establishes the fact even when
    # another source for the same transaction was silent (NULL) on it.
    resolved: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = (row["name"] or "").strip()
        if not name:
            continue  # staging_investor.name is NOT NULL; defensive only.
        key = _normalized_name(name)
        entry = resolved.setdefault(key, {
            "name": name, "is_lead": False,
            "is_new_investor": None, "is_existing_investor": None,
        })
        if row["is_lead"]:
            entry["is_lead"] = True
        for flag in ("is_new_investor", "is_existing_investor"):
            val = row[flag]
            if val is not None:
                entry[flag] = bool(val) or bool(entry[flag])

    written = 0
    for entry in resolved.values():
        entity_id = _transaction_entity_id(transaction_id, entry["name"])
        conn.execute(
            """
            INSERT OR IGNORE INTO entity (
                entity_id, entity_kind, canonical_name, normalized_name, display_name,
                review_status
            ) VALUES (?, 'ORGANIZATION', ?, ?, ?, 'UNREVIEWED')
            """,
            (entity_id, entry["name"], _normalized_name(entry["name"]), entry["name"]),
        )
        participant_id = _participant_id(transaction_id, entity_id)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO transaction_participant (
                participant_id, transaction_id, entity_id, side, participant_role,
                is_lead, is_new_investor, is_existing_investor,
                source_stage, review_status, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'UNREVIEWED', 1)
            """,
            (
                participant_id, transaction_id, entity_id, _SIDE, _PARTICIPANT_ROLE,
                1 if entry["is_lead"] else 0,
                entry["is_new_investor"],
                entry["is_existing_investor"],
                _SOURCE_STAGE,
            ),
        )
        if cur.rowcount:
            written += 1

    if log is not None:
        log.info(
            "transaction_id=%s materialized %d canonical investor participant(s) "
            "from %d source-level staging_investor row(s)",
            transaction_id, written, len(rows),
        )
    return {"investors_seen": len(rows), "participants_written": written}
