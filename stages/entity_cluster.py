"""
Stage 8: entity_cluster

Clusters LC_EXTRACTED staging_extraction rows that describe the same deal.

Algorithm (per specs/entity_resolution.md):
  1. Normalize target and acquirer names: lowercase, strip legal suffixes and
     parentheticals, collapse whitespace, strip leading "the".
  2. Pairwise compare all eligible rows: rapidfuzz token_set_ratio >= 90 on
     BOTH target and acquirer, plus announced_date within ± 3 days.
  3. Transitive closure via union-find.
  4. Assign cluster_id per §5: tc_<first 12 hex of SHA-256 over sorted
     normalized names + earliest announced_date>.
  5. Update staging_extraction.transaction_cluster_id and status = CLUSTERED.

Rows missing target_name or acquirer_name are left LC_EXTRACTED (unclustered).
Rows with null announced_date become singletons (cannot match any other row).

Spec references: specs/entity_resolution.md, specs/pipeline.md §2 (Stage 8)
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone

from rapidfuzz import fuzz

from config import Config
from logger import get_logger


# ---------------------------------------------------------------------------
# Name normalization (spec §3)
# ---------------------------------------------------------------------------

_PAREN_RE = re.compile(r"\([^)]*\)")
_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]*(inc\.?|incorporated|corp\.?|corporation|co\.?|company|"
    r"ltd\.?|limited|llc\.?|l\.l\.c\.?|lp\.?|l\.p\.?|plc\.?|p\.l\.c\.?|"
    r"ag|s\.a\.?|s\.p\.a\.?|s\.a\.s\.?|s\.r\.l\.?|gmbh|bv|nv|"
    r"holdings?|holding|group)\.?\s*$",
    re.IGNORECASE,
)
_AND_RE = re.compile(r"(?<!\w)(and|&|\+)(?!\w)", re.IGNORECASE)
_LEADING_THE_RE = re.compile(r"^the\s+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def _normalize(name: str | None) -> str:
    if not name:
        return ""
    n = name.strip().lower()
    n = _PAREN_RE.sub(" ", n)
    for _ in range(6):
        stripped = _LEGAL_SUFFIX_RE.sub("", n).strip()
        if stripped == n:
            break
        n = stripped
    n = _AND_RE.sub(" ", n)
    n = _LEADING_THE_RE.sub("", n)
    n = _WS_RE.sub(" ", n).strip()
    return n


# ---------------------------------------------------------------------------
# Union-find
# ---------------------------------------------------------------------------

class _UF:
    __slots__ = ("parent", "rank")

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int) -> None:
        px, py = self.find(x), self.find(y)
        if px == py:
            return
        if self.rank[px] < self.rank[py]:
            px, py = py, px
        self.parent[py] = px
        if self.rank[px] == self.rank[py]:
            self.rank[px] += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_diff_days(d1: str, d2: str) -> int | None:
    try:
        return abs((
            datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")
        ).days)
    except (ValueError, TypeError):
        return None


def _make_cluster_id(all_norm_names: list[str], earliest_date: str) -> str:
    seed = "|".join(sorted(set(n for n in all_norm_names if n))) + "|" + (earliest_date or "")
    return "tc_" + hashlib.sha256(seed.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------

def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Cluster LC-extracted rows by name + date similarity.

    Returns
    -------
    dict
        Keys: eligible_total, clusters_formed, singletons,
              multi_member_clusters, clusters (run.py alias), merged_duplicates
    """
    log = get_logger("entity_cluster", run_id, level=cfg.log_level)

    rows = conn.execute(
        """
        SELECT extraction_id, target_name, acquirer_name, announced_date
        FROM staging_extraction
        WHERE status = 'LC_EXTRACTED'
          AND transaction_cluster_id IS NULL
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 8: %d LC_EXTRACTED rows to cluster", total)

    eligible = [r for r in rows if r["target_name"] and r["acquirer_name"]]
    skipped_count = total - len(eligible)
    if skipped_count:
        log.warning(
            "%d rows skipped — null target_name or acquirer_name (left LC_EXTRACTED)",
            skipped_count,
        )

    n = len(eligible)
    if n == 0:
        log.info("Stage 8 done  total=%d eligible=0", total)
        return {
            "eligible_total": total, "clusters_formed": 0, "singletons": 0,
            "multi_member_clusters": 0, "clusters": 0, "merged_duplicates": 0,
        }

    norm_t = [_normalize(r["target_name"]) for r in eligible]
    norm_a = [_normalize(r["acquirer_name"]) for r in eligible]
    has_date = [bool(r["announced_date"]) for r in eligible]

    uf = _UF(n)
    for i in range(n):
        if not has_date[i]:
            continue
        for j in range(i + 1, n):
            if not has_date[j]:
                continue
            diff = _date_diff_days(eligible[i]["announced_date"], eligible[j]["announced_date"])
            if diff is None or diff > 3:
                continue
            if fuzz.token_set_ratio(norm_t[i], norm_t[j]) < 90:
                continue
            if fuzz.token_set_ratio(norm_a[i], norm_a[j]) < 90:
                continue
            uf.union(i, j)
            log.info(
                "Matched eid=%d (%r/%r)  eid=%d (%r/%r)  date_diff=%d",
                eligible[i]["extraction_id"], norm_t[i], norm_a[i],
                eligible[j]["extraction_id"], norm_t[j], norm_a[j],
                diff,
            )

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    clusters_formed = len(groups)
    singletons = sum(1 for g in groups.values() if len(g) == 1)
    multi_member = clusters_formed - singletons

    now = datetime.now(timezone.utc).isoformat()
    for member_indices in groups.values():
        member_rows = [eligible[i] for i in member_indices]
        dates = [r["announced_date"] for r in member_rows if r["announced_date"]]
        earliest = min(dates) if dates else ""
        all_names = [norm_t[i] for i in member_indices] + [norm_a[i] for i in member_indices]
        cid = _make_cluster_id(all_names, earliest)

        for row in member_rows:
            conn.execute(
                """UPDATE staging_extraction
                   SET transaction_cluster_id=?, status='CLUSTERED', updated_at=?
                   WHERE extraction_id=?""",
                (cid, now, row["extraction_id"]),
            )
        conn.commit()

        if len(member_rows) > 1:
            log.info(
                "Multi-member cluster %s: %d members eids=%s",
                cid, len(member_rows),
                [r["extraction_id"] for r in member_rows],
            )
        else:
            log.debug(
                "Singleton %s: eid=%d (%r / %r)",
                cid, member_rows[0]["extraction_id"],
                norm_t[member_indices[0]], norm_a[member_indices[0]],
            )

    log.info(
        "Stage 8 done  total=%d eligible=%d clusters=%d singletons=%d multi=%d",
        total, n, clusters_formed, singletons, multi_member,
    )
    return {
        "eligible_total": total,
        "clusters_formed": clusters_formed,
        "clusters": clusters_formed,
        "singletons": singletons,
        "multi_member_clusters": multi_member,
        "merged_duplicates": n - clusters_formed,
    }
