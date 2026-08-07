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

M&A rows missing target_name or acquirer_name are left LC_EXTRACTED (unclustered).
Funding rows (VC_ROUND/VENTURE_DEBT/GROWTH_EQUITY) are recipient-centric: they match
on target_name alone with soft conflict checks on round label/size/date (bug #5).
Rows with null announced_date become singletons on the M&A path.

Spec references: specs/entity_resolution.md, specs/pipeline.md §2 (Stage 8)
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone

from rapidfuzz import fuzz

from config import Config
from lib.observation_writer import backfill_observation_transaction_ids
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
# Funding-path matching (bug #5)
#
# Funding rounds are recipient-centric: they have a target (the company raising)
# but no acquirer — investors, not an acquirer. The M&A target+acquirer match
# would strand them at LC_EXTRACTED forever. For funding rows we match on target
# alone, then apply SOFT conflict checks (round label, round size, date): a check
# only blocks a merge on a genuine *conflict*, never requires a field to be present.
# Tunables are module-level — calibrated against data/pl_funding.db (see
# docs/review_bug5_feedback.md), not picked blind.
# ---------------------------------------------------------------------------

FUNDING_TYPES = frozenset({"VC_ROUND", "VENTURE_DEBT", "GROWTH_EQUITY"})
FUNDING_DATE_WINDOW_DAYS = 30          # same-round reports land within a few days; 0-day in fixture
FUNDING_AMOUNT_TOLERANCE = 0.15        # $270.5M vs $312M (OLIX) = 13.3% is a legit same-round merge

_SERIES_RE = re.compile(r"series\s*([a-k])", re.IGNORECASE)


def _round_stage(label: str | None) -> str | None:
    """Canonical base stage for a free-text round label, or None if undetermined.

    'Series A' / 'Series A-1' / 'Series A Extension' all map to 'series_a'. Returns
    None for unknown/empty labels so the caller treats them as compatible (soft).
    """
    if not label:
        return None
    l = label.lower()
    m = _SERIES_RE.search(l)
    if m:
        return "series_" + m.group(1).lower()
    if "pre-seed" in l or "pre seed" in l or "preseed" in l or "pre_seed" in l:
        return "pre_seed"
    if "seed" in l:
        return "seed"
    if "angel" in l:
        return "angel"
    return None


def _label_compatible(a: str | None, b: str | None) -> bool:
    """True unless two labels resolve to a genuinely different stage (soft check).

    Null/unknown labels are compatible — Valar Atomics has a null-label row that must
    still merge with its 'Series B' sibling.
    """
    sa, sb = _round_stage(a), _round_stage(b)
    if sa is None or sb is None:
        return True
    return sa == sb


def _funding_match(i: int, j: int, norm_t, labels, sizes, dates, precisions) -> bool:
    """Recipient-centric match for two funding rows, with soft conflict checks."""
    if fuzz.token_set_ratio(norm_t[i], norm_t[j]) < 90:
        return False
    # Date: only block on a real conflict, and only when BOTH dates are exact.
    if precisions[i] == "exact" and precisions[j] == "exact" and dates[i] and dates[j]:
        diff = _date_diff_days(dates[i], dates[j])
        if diff is not None and diff > FUNDING_DATE_WINDOW_DAYS:
            return False
    # Round label: block only on an incompatible stage.
    if not _label_compatible(labels[i], labels[j]):
        return False
    # Round size: block only when both present and beyond tolerance.
    if sizes[i] and sizes[j]:
        if abs(sizes[i] - sizes[j]) / max(sizes[i], sizes[j]) > FUNDING_AMOUNT_TOLERANCE:
            return False
    return True


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
        SELECT extraction_id, target_name, acquirer_name, announced_date,
               v2_event_type, round_label, round_size, announced_date_precision
        FROM staging_extraction
        WHERE status = 'LC_EXTRACTED'
          AND transaction_cluster_id IS NULL
        """
    ).fetchall()

    total = len(rows)
    log.info("Stage 8: %d LC_EXTRACTED rows to cluster", total)

    # Funding rows are eligible on target_name alone (no acquirer); M&A rows still
    # need both target and acquirer. (bug #5)
    def _eligible(r) -> bool:
        if not r["target_name"]:
            return False
        return bool(r["acquirer_name"]) or (r["v2_event_type"] in FUNDING_TYPES)

    eligible = [r for r in rows if _eligible(r)]
    skipped_count = total - len(eligible)
    if skipped_count:
        log.warning(
            "%d rows skipped — no target_name, or non-funding row missing acquirer_name "
            "(left LC_EXTRACTED)",
            skipped_count,
        )

    n = len(eligible)
    if n == 0:
        observation_updates = backfill_observation_transaction_ids(conn)
        if observation_updates:
            conn.commit()
            log.info("Stage 8: populated transaction_id on %d pending observations", observation_updates)
        log.info("Stage 8 done  total=%d eligible=0", total)
        return {
            "eligible_total": total, "clusters_formed": 0, "singletons": 0,
            "multi_member_clusters": 0, "clusters": 0, "merged_duplicates": 0,
        }

    norm_t = [_normalize(r["target_name"]) for r in eligible]
    norm_a = [_normalize(r["acquirer_name"]) for r in eligible]
    has_date = [bool(r["announced_date"]) for r in eligible]
    is_funding = [r["v2_event_type"] in FUNDING_TYPES for r in eligible]
    labels = [r["round_label"] for r in eligible]
    sizes = [r["round_size"] for r in eligible]
    dates = [r["announced_date"] for r in eligible]
    precisions = [r["announced_date_precision"] for r in eligible]

    uf = _UF(n)
    for i in range(n):
        for j in range(i + 1, n):
            # Funding path (bug #5): recipient-centric, soft conflict checks.
            if is_funding[i] and is_funding[j]:
                if _funding_match(i, j, norm_t, labels, sizes, dates, precisions):
                    uf.union(i, j)
                    log.info(
                        "Matched funding eid=%d (%r, %s)  eid=%d (%r, %s)",
                        eligible[i]["extraction_id"], norm_t[i], labels[i],
                        eligible[j]["extraction_id"], norm_t[j], labels[j],
                    )
                continue
            # Cross-path (one funding, one M&A): never merge.
            if is_funding[i] or is_funding[j]:
                continue

            # M&A path (unchanged): require target AND acquirer.
            t_match = fuzz.token_set_ratio(norm_t[i], norm_t[j]) >= 90
            a_match = fuzz.token_set_ratio(norm_a[i], norm_a[j]) >= 90
            if not (t_match and a_match):
                continue

            if has_date[i] and has_date[j]:
                # Both dated: require ±3-day window (original behaviour).
                diff = _date_diff_days(eligible[i]["announced_date"], eligible[j]["announced_date"])
                if diff is None or diff > 3:
                    continue
                uf.union(i, j)
                log.info(
                    "Matched eid=%d (%r/%r)  eid=%d (%r/%r)  date_diff=%d",
                    eligible[i]["extraction_id"], norm_t[i], norm_a[i],
                    eligible[j]["extraction_id"], norm_t[j], norm_a[j],
                    diff,
                )
            else:
                # At least one row lacks a date: name-only match.
                uf.union(i, j)
                log.info(
                    "Matched (name-only, null date) eid=%d (%r/%r)  eid=%d (%r/%r)",
                    eligible[i]["extraction_id"], norm_t[i], norm_a[i],
                    eligible[j]["extraction_id"], norm_t[j], norm_a[j],
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
        # When no member has a date, use the lowest extraction_id as a tiebreaker
        # so two different null-date groups with identical names get distinct IDs.
        if not earliest:
            earliest = f"ext_{min(r['extraction_id'] for r in member_rows)}"
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

    observation_updates = backfill_observation_transaction_ids(conn)
    if observation_updates:
        conn.commit()
        log.info("Stage 8: populated transaction_id on %d pending observations", observation_updates)

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
