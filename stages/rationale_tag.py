"""
Stage 11: rationale_tag

Classifies the primary strategic rationale for each transaction_record with a
current summary. Scans contributing source texts for rationale keywords,
extracts up to 3 short excerpts, and calls the Opus strategic_rationale prompt.

Inserts a new row into rationale_tag with is_current = 1; prior rows for the
same transaction_id are flipped to is_current = 0.

Spec references: prompts/strategic_rationale.md, specs/pipeline.md §2 (Stage 11)
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone

from config import Config
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "strategic_rationale"
_VERSION = "0.3"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"
_REQUIRED_KEYS = frozenset({
    "rationale", "secondary_rationales", "supporting_excerpt_index",
    "model_confidence", "notes", "prompt_version",
})
_VALID_RATIONALES = frozenset({
    "SCALE_CONSOLIDATION", "GEOGRAPHIC_EXPANSION", "PRODUCT_OR_TECH_CAPABILITY",
    "VERTICAL_INTEGRATION", "MARKET_DIVERSIFICATION", "TALENT_ACQUISITION",
    "FINANCIAL_OR_ARBITRAGE", "OTHER",
})
_KEYWORD_RE = re.compile(
    r"accelerate|expand|complement|strengthen|enter|diversify|consolidate|"
    r"integrate|unlock|strategic|synergies|capabilities|platform|adjacent|"
    r"vertical|geographic|talent",
    re.IGNORECASE,
)
_EXCERPT_RADIUS = 150
_SLEEP = 1.0


def _extract_excerpts(text: str, tier: str, max_excerpts: int = 3) -> list[dict]:
    """Find rationale keywords and return up to max_excerpts centered excerpts."""
    excerpts: list[dict] = []
    seen_ranges: list[tuple[int, int]] = []
    for m in _KEYWORD_RE.finditer(text):
        start = max(0, m.start() - _EXCERPT_RADIUS)
        end = min(len(text), m.end() + _EXCERPT_RADIUS)
        if any(s <= m.start() <= e for s, e in seen_ranges):
            continue
        seen_ranges.append((start, end))
        excerpts.append({"source_tier": tier, "excerpt": text[start:end].strip()})
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def _validate(result: dict) -> str | None:
    missing = _REQUIRED_KEYS - result.keys()
    if missing:
        return f"missing required keys: {missing}"
    if result.get("rationale") not in _VALID_RATIONALES:
        return f"invalid rationale: {result.get('rationale')!r}"
    if result.get("secondary_rationales") is None:
        result["secondary_rationales"] = []
    elif not isinstance(result["secondary_rationales"], list):
        return "secondary_rationales must be a list"
    return None


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Tag strategic rationale for summarized transactions.

    Returns
    -------
    dict
        Keys: transactions_total, rationales_tagged, failed
    """
    log = get_logger("rationale_tag", run_id, level=cfg.log_level)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s", _FULL_VERSION, prompt["file_hash"][:12])

    rows = conn.execute(
        """
        SELECT tr.transaction_id, tr.deal_type, tr.target_name, tr.acquirer_name,
               s.summary_text
        FROM transaction_record tr
        JOIN summary s ON s.transaction_id = tr.transaction_id AND s.is_current = 1
        WHERE tr.is_current = 1
          AND NOT EXISTS (
              SELECT 1 FROM rationale_tag rt
              WHERE rt.transaction_id = tr.transaction_id AND rt.is_current = 1
          )
        ORDER BY tr.announced_date DESC
        """
    ).fetchall()

    total = len(rows)
    tagged = failed = 0
    log.info("Stage 11: %d transactions to tag", total)

    for tr in rows:
        tid = tr["transaction_id"]
        now = datetime.now(timezone.utc).isoformat()

        sources = conn.execute(
            """
            SELECT sr.clean_text, sr.source_tier
            FROM source_raw sr
            JOIN staging_extraction se ON se.source_raw_id = sr.source_raw_id
            WHERE se.transaction_cluster_id = ?
            ORDER BY sr.source_tier ASC
            """,
            (tid,),
        ).fetchall()

        all_excerpts: list[dict] = []
        for src in sources:
            if not src["clean_text"] or len(all_excerpts) >= 3:
                break
            excerpts = _extract_excerpts(
                src["clean_text"], src["source_tier"] or "T2",
                max_excerpts=3 - len(all_excerpts),
            )
            all_excerpts.extend(excerpts)

        if all_excerpts:
            exc_lines = "\n".join(
                f"[{i}] {e['source_tier']}: {e['excerpt']!r}"
                for i, e in enumerate(all_excerpts)
            )
        else:
            exc_lines = "(none — classifying from summary only)"

        user_prompt = prompt["user_template"].format(
            deal_type=tr["deal_type"] or "null",
            target_name=tr["target_name"] or "null",
            acquirer_name=tr["acquirer_name"] or "null",
            summary_text=tr["summary_text"],
            source_excerpts_formatted=exc_lines,
        )

        try:
            result = call_prompt(
                prompt_name=_PROMPT_NAME,
                prompt_version=_FULL_VERSION,
                user_prompt=user_prompt,
                system_prompt=prompt["system"],
                model="opus",
                temperature=0.0,
                max_tokens=512,
                cfg=cfg,
                conn=conn,
                run_id=run_id,
                log=log,
            )
        except PromptFailure as exc:
            log.warning("transaction_id=%s rationale prompt failed: %s", tid, exc)
            failed += 1
            time.sleep(_SLEEP)
            continue

        err = _validate(result)
        if err:
            log.warning("transaction_id=%s schema violation: %s — skipping", tid, err)
            failed += 1
            time.sleep(_SLEEP)
            continue

        primary = result["rationale"]
        secondaries = [
            r for r in (result.get("secondary_rationales") or [])
            if r != primary and r in _VALID_RATIONALES
        ][:2]

        if len(result.get("secondary_rationales") or []) > len(secondaries) + (1 if primary in (result.get("secondary_rationales") or []) else 0):
            log.warning(
                "transaction_id=%s secondary_rationales truncated or deduped",
                tid,
            )

        conn.execute(
            "UPDATE rationale_tag SET is_current = 0 WHERE transaction_id = ? AND is_current = 1",
            (tid,),
        )
        conn.execute(
            """
            INSERT INTO rationale_tag (transaction_id, primary_rationale, secondary_rationales,
                supporting_excerpt_index, model_confidence, is_current,
                prompt_version, notes, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                tid,
                primary,
                json.dumps(secondaries),
                result.get("supporting_excerpt_index"),
                result.get("model_confidence"),
                _FULL_VERSION,
                result.get("notes"),
                now,
            ),
        )
        conn.commit()

        tagged += 1
        log.info(
            "transaction_id=%s rationale=%s  secondary=%s  confidence=%s",
            tid, primary, secondaries, result.get("model_confidence"),
        )
        time.sleep(_SLEEP)

    log.info("Stage 11 done  total=%d tagged=%d failed=%d", total, tagged, failed)
    return {"transactions_total": total, "rationales_tagged": tagged, "failed": failed}
