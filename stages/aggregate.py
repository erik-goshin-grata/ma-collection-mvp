"""
Stage 9: aggregate

For each transaction cluster (CLUSTERED staging_extraction rows sharing a
transaction_cluster_id), applies deterministic tier rules (T1 > T2 > T3) to
resolve field values from all cluster members into a single canonical value.

When two sources of equal tier disagree on the same field, the aggregation
prompt is called once for that specific field. All LLM conflict resolutions
are logged to aggregation_conflict_log.

After field resolution:
  - consideration_type is derived from consideration_components on the M&A path;
    on the funding path (where no component vocabulary applies) it falls back to
    Funding HC's own collected instrument classification
  - is_take_private / is_minority are derived from deal context. is_add_on and
    is_platform_investment are NOT: V3 §T7 replaces both with the extracted
    sponsor_transaction_role, and their columns are retained but unwritten.
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
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from config import DEFAULT_AGGREGATION_READ_SOURCE, Config
from lib.field_priority import TIER_ORDER
from lib.observation_writer import (
    MULTIPLE_FIELD_PREFIX as _MULTIPLE_FIELD_PREFIX,
    reported_multiple_field_name,
)
from logger import get_logger
from prompts.base import PromptFailure, call_prompt, load_prompt_file, register_prompt_version

_PROMPT_NAME = "aggregation"
_VERSION = "0.13"
_FULL_VERSION = f"{_PROMPT_NAME}:{_VERSION}"

_CONF_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

_FUNDING_EVENT_TYPES = frozenset({"VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT"})
# Non-control investments: an investor takes a (usually minority) stake / injects
# primary capital — there is no whole-company purchase price. The amount is a
# check/round size, NOT the company's equity value. (bug #8)
_NON_CONTROL_TYPES = _FUNDING_EVENT_TYPES | frozenset({"MINORITY_INVESTMENT"})

# §2.6 — event types that convey whole-company control, so a silent pct_acquired
# defaults to 100 (assumed). Inherently-partial types (minority / growth / VC /
# venture-debt, recap, spin/split, JV) never inherit the default; there, silence
# means unknown, and defaulting would convert a minority stake into a whole-company buy.
_CONTROL_DEFAULT_TYPES = frozenset({"ACQUISITION", "MERGER"})

# Fields aggregated into transaction_record.
# Each entry: (field_name, field_type)
_FIELDS = [
    ("deal_type", "string"),
    ("v2_event_type", "string"),
    ("event_history_type", "string"),
    ("spin_split_type", "string"),
    ("spin_split_type_v2", "string"),
    ("distribution_mechanism", "string"),
    ("recap_type", "string"),
    ("combination_structure", "string"),   # MERGER | REVERSE_MERGER | DE_SPAC | null (§T2)
    ("target_type", "string"),
    ("target_type_v2", "string"),
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
    ("asset_type", "string"),   # subordinate to target_type = assets (§T13)
    ("acquirer_description", "string"),
    ("acquirer_sponsor_name", "string"),
    ("parent_seller_description", "string"),
    ("announced_date", "date"),
    ("announced_date_precision", "string"),
    ("closed_date", "date"),
    ("closed_date_precision", "string"),
    ("signing_date", "date"),
    ("signing_date_precision", "string"),
    ("rumor_date", "date"),
    ("value_amount", "number"),
    ("value_currency", "string"),
    ("value_type", "string"),
    ("per_share_price", "number"),
    ("pct_acquired", "number"),
    ("stake_transition_type", "string"),
    ("offer_mechanism", "string"),   # TENDER_OFFER | null (V3 §T12)
    ("is_platform_investment", "boolean"),   # legacy; no longer written by Stage 4 (V3 §T7)
    ("sponsor_transaction_role", "string"),  # PLATFORM | ADD_ON | null (§T7)
    ("is_secondary_buyout", "boolean"),
    ("is_merger_of_equals", "boolean"),
    # V3 take-private ownership outcome. 1 or absent -- never 0: Stage 4 normalizes a
    # model `false` to NULL and the observation writer skips NULL, so "not established"
    # is the ABSENCE of an observation, not an observed negative.
    ("is_going_private_outcome", "boolean"),
    ("target_revenue", "number"),
    ("target_revenue_period_type", "string"),
    ("target_revenue_period_type_v2", "string"),
    ("target_revenue_period_end", "date"),
    ("target_ebitda", "number"),
    ("target_ebitda_period_type", "string"),
    ("target_ebitda_period_type_v2", "string"),
    ("target_ebitda_period_end", "date"),
    ("financials_currency", "string"),
    # Point-in-time balance-sheet items. No period_type companion by design — these
    # are as-of figures, not periods, so there is no LTM/TTM or annual/quarterly
    # distinction to record.
    ("total_debt", "number"),
    ("total_debt_currency", "string"),
    ("cash_st", "number"),
    ("cash_st_currency", "string"),
    ("balance_sheet_as_of_date", "date"),
    ("financials_disclosure_status", "string"),
    ("transaction_terms_disclosure_status", "string"),
    ("consideration_components", "json"),
    ("hostile", "boolean"),          # legacy; no longer written by Stage 7 (V3 §T11)
    ("deal_attitude", "string"),     # FRIENDLY | HOSTILE | null
    ("approach_type", "string"),     # SOLICITED | UNSOLICITED | null
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
    ("round_currency", "string"),
    ("facility_size", "number"),
    ("total_raised_to_date", "number"),
    ("is_extension_round", "boolean"),
    ("round_price_direction", "string"),   # UP | DOWN | FLAT | null (V3 §A6.3)
    ("is_bridge_round", "boolean"),
    ("use_of_proceeds", "string"),
    ("has_board_seat", "boolean"),
    ("board_seat_notes", "string"),
    # Funding instrument/security classification (2026-09-03). M&A HC no longer
    # authors this field (retired 0.37); only Funding HC writes it now. Loaded here so
    # it reaches field_values -- the fallback that actually uses it lives at the
    # canonical-write call site below, since the M&A path's own derived value must
    # keep taking precedence when it exists.
    ("consideration_type", "string"),
]

_FIELD_NAMES = {f for f, _ in _FIELDS}
_FIELD_TYPE = {f: t for f, t in _FIELDS}
_CONTEXT_FIELDS = ("target_name", "acquirer_name", "deal_type", "announced_date")
# Buyer/structure types that satisfy the private-ownership condition of a take-private.
# Membership is expressed in the V2 lowercase vocabulary because that is what Stage 4
# stores; the comparison lowercases its input, so rows still carrying the legacy uppercase
# form match too.
#
# This set is deliberately NARROW and is one of three required conditions -- it is not on
# its own a take-private test. Every other acquirer type is out BY TYPE ALONE, which is a
# statement about what the type establishes, not a claim that such a buyer can never take a
# company private:
#
#   strategic_corporate  A private strategic buying a public company is an ordinary
#                        acquisition. The target stops being independent, not necessarily
#                        publicly traded, and nothing in the type says which.
#   consortium           A PE/sponsor consortium DOES qualify conceptually, but bare
#                        `consortium` means only "multiple buyers acting jointly" (the
#                        prompt's own words) and establishes no sponsor character. Nothing
#                        in the data model carries it: acquirer_type is one flat value with
#                        no per-member type; entity.entity_type is in the schema but is
#                        never written; consortium members are all written as the single
#                        undifferentiated role ACQUIRER; staging_investor.investor_type is
#                        funding-path-only and has no private_equity value; and
#                        acquirer_sponsor_name names a sponsor BEHIND the buyer, which in a
#                        direct PE consortium is null because the sponsors ARE the buyers.
#                        Recorded as a representation gap rather than proxied.
#   venture_capital, individual, family_office, hedge_fund, pension_fund,
#   sovereign_wealth_fund, growth_equity, spac, unknown
#                        None establishes a going-private structure by type.
#
# There is deliberately no acquirer-ticker guard. It was never a proxy for the buyer being
# private -- a listed sponsor (Blackstone, EQT, Apollo) taking a company private is a
# genuine take-private, and the guard returned 0 for every one of them.
_TAKE_PRIVATE_QUALIFYING_ACQUIRER_TYPES = frozenset({
    "private_equity",
    "pe_portfolio",
    "management",
    "employee_group",
    "other_financial_sponsor",
})


# ---------------------------------------------------------------------------
# Derived-field helpers
# ---------------------------------------------------------------------------

# Transaction TERMS, not offered consideration. Each describes how the deal is
# structured -- debt the buyer takes on, a contingent payment, equity a seller keeps --
# and none of them is what the buyer offered, so none of them decides the type. The
# `low_confidence_extraction` contract already says this in its own words: "a cash +
# earnout deal stays consideration_type=CASH". The subset ladder below used to
# contradict it, because a set test has no notion of which forms are structural: an
# all-stock combination that assumed the target's debt came out `OTHER`, and so did
# every cash deal carrying an earnout.
#
# `OTHER` is deliberately NOT in this set. It is a genuine offered form -- the LC
# vocabulary defines it as "preferred stock, exchangeable shares, notes" -- so it still
# decides the type, which is exactly what `OTHER` is for.
_NON_DETERMINING_CONSIDERATION_FORMS = frozenset({
    "EARNOUT", "CVR", "CONTINGENT_CONSIDERATION", "DEBT_ASSUMED", "RETAINED_EQUITY",
})


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
    # Decide on what was offered. Components that are only terms are set aside first.
    offered = forms - _NON_DETERMINING_CONSIDERATION_FORMS
    if not offered:
        # Terms with no offered form is not "the consideration was something else" --
        # it is a source that described the structure and never said what was paid.
        # Null is the honest answer; OTHER would assert a form nobody stated.
        return None
    stock_forms = {"ACQUIRER_STOCK", "TARGET_STOCK"}
    if offered <= {"CASH"}:
        return "CASH"
    if offered <= stock_forms:
        return "STOCK"
    if offered <= ({"CASH"} | stock_forms):
        return "CASH_AND_STOCK"
    return "OTHER"


def _lower(value: Any) -> str:
    """Case-fold one value for comparison. Local to the comparison, by design.

    `target_type` and `acquirer_type` are stored as the model emitted them -- Stage 3 and
    Stage 4 each write the raw value to the legacy-named column and the normalized value to
    the `_v2` column. Under the V2 vocabulary that raw value is lowercase, while stored rows
    from before the lowercasing carry the uppercase form. Folding here matches both without
    mutating the aggregated `field_values`, which other derivations and the canonical write
    read in their stored form.
    """
    return str(value or "").strip().lower()


def _derive_is_take_private(fields: dict) -> int:
    """Three required conditions, all of which must hold.

    1. The target was a public standalone company BEFORE the transaction.
    2. The buyer/structure satisfies the private-ownership condition.
    3. The source AFFIRMATIVELY establishes the ownership outcome -- that the target
       ceases to have publicly held/traded equity.

    Condition 3 is the one that cannot be inferred. Before it existed this derivation
    reached 1 on conditions 1 and 2 alone, which made every private-strategic acquisition
    of a public company a take-private and could not distinguish a sponsor's control
    investment in a still-listed company from a genuine privatization. No pre-existing
    primitive supplies it: pct_acquired is documented "Null if 100% or unstated" so its
    null is ambiguous by construction; the §2.6 resolver's assumed 100 fires on every
    silent control acquisition; stake_transition_type is populated only on explicit
    ownership evidence and is sparse; offer_mechanism is TENDER_OFFER|null and most
    take-privates are one-step mergers; target_status is pre-transaction only. Hence the
    extracted `is_going_private_outcome`.

    Absence of affirmative outcome evidence is 0, by decision. `is_going_private_outcome`
    is never persisted as 0, so `_explicit_flag` reads its absence, not a stored negative.
    """
    # Read the RESOLVED event type, never the raw `deal_type` column. `deal_type` is a
    # legacy alias that Stage 3 stopped asking the model to author at classifier 0.14; it is
    # still written, but from the resolved value. Reading it directly made this derivation
    # the only one in the file that depended on the alias surviving -- its sibling
    # _derive_is_secondary_buyout already used this helper. Same value today, and no longer
    # a hidden dependency on a field being retired.
    #
    # The event type and target_status are UPPERCASE in production and are compared as
    # stored. Only target_type and acquirer_type are case-folded -- those are the two the V2
    # vocabulary lowercased, and comparing them against uppercase literals returned 0 for
    # every transaction.
    if _event_type(fields) != "ACQUISITION":
        return 0
    if fields.get("target_status") != "PUBLIC":
        return 0
    if _lower(fields.get("target_type")) != "standalone_company":
        return 0
    if _lower(fields.get("acquirer_type")) not in _TAKE_PRIVATE_QUALIFYING_ACQUIRER_TYPES:
        return 0
    return _explicit_flag(fields.get("is_going_private_outcome"))


_MINORITY_STAKE_TRANSITIONS_WITHOUT_PCT = frozenset({
    "NEW_MINORITY_STAKE",
    "MINORITY_ACQUIRING_MAJORITY",
    "MAJORITY_ACQUIRE_REMAINING",
    "MAJORITY_INCREASING_STAKE",
    "MINORITY_INCREASING_STAKE",
})

_NON_MINORITY_STAKE_TRANSITIONS_WITHOUT_PCT = frozenset({
    "NEW_MAJORITY_STAKE",
    "FULL_ACQUISITION",
    "MINORITY_ACQUIRING_REMAINING",
})


def _derive_is_minority(fields: dict) -> int:
    """Derived minority-status flag.

    Minority means the current transaction involves a minority interest/stake
    characteristic, not that the buyer/investor has or lacks control after the
    transaction. Current pct_acquired is the strongest structured evidence when
    stated. Stake-transition labels are used only when pct is missing and the
    label itself implies a current minority-sized stake.
    """
    if _event_type(fields) == "MINORITY_INVESTMENT":
        return 1
    pct = fields.get("pct_acquired")
    if pct is not None:
        try:
            return int(float(pct) < 50.0)
        except (TypeError, ValueError):
            return 0
    transition = fields.get("stake_transition_type")
    if transition in _MINORITY_STAKE_TRANSITIONS_WITHOUT_PCT:
        return 1
    if transition in _NON_MINORITY_STAKE_TRANSITIONS_WITHOUT_PCT:
        return 0
    return 0


def _explicit_flag(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(float(value) != 0.0)
    except (TypeError, ValueError):
        return int(bool(value))


def _derive_is_secondary_buyout(fields: dict) -> int:
    if _explicit_flag(fields.get("is_secondary_buyout")):
        return 1
    deal_type = _event_type(fields)
    if deal_type not in _CONTROL_DEFAULT_TYPES:
        return 0
    return int(bool(fields.get("_has_buyer_sponsor_party") and fields.get("_has_seller_sponsor_party")))


def _derive_flags(fields: dict) -> dict:
    # The three locals that stood here were read only by the is_add_on derivation, which
    # V3 §T7 removes. Every surviving entry takes `fields` directly.
    return {
        "is_take_private": _derive_is_take_private(fields),
        "is_minority": _derive_is_minority(fields),
        "is_secondary_buyout": _derive_is_secondary_buyout(fields),
        "is_merger_of_equals": _explicit_flag(fields.get("is_merger_of_equals")),
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


def _derive_transaction_status(event_history_type: str | None, closed_date: str | None) -> str:
    """Derive deal status from the V2 event_history_type lifecycle + closed_date.

    event_history_type describes the release type (ANNOUNCED / CLOSED / AMENDED /
    TERMINATED). A deal is CLOSED when a close date is known or the release is a
    completion notice; TERMINATED when it was called off; otherwise it is PENDING.
    A deal that is not closed or terminated is, by definition, pending — so
    PENDING is the default rather than UNKNOWN.
    """
    if event_history_type == "TERMINATED":
        return "TERMINATED"
    if closed_date is not None or event_history_type == "CLOSED":
        return "CLOSED"
    return "PENDING"


_ROUND_RE = re.compile(r"^series[\s\-_]*([a-z])[\s\-_]*([0-9]*)$")
_PRE_SEED_RE = re.compile(r"^pre[\s\-_]*seed$")

# Series D and beyond. Compared as a parsed single letter, never as a substring: the V2
# derivation enumerated "series d".."series g" literally, so Series H and beyond returned
# null, and "series a" matched inside "Series AA".
_EARLY = "a"
_GROWTH = ("b", "c")


def _normalize_round(round_label: str | None) -> str | None:
    """`round_label` (verbatim source wording) -> canonical `round`, or None.

    Deterministic normalization, not model output: the prompt emits only the verbatim
    label, so there is no vocabulary for a prompt validator to enforce (V3 §T14).

    The shape is generative but bounded -- PRE_SEED, SEED, ANGEL, SERIES_<letter>, and
    SERIES_<letter><positive int>. Anything outside it returns None rather than a guess,
    and `round_label` keeps the original wording either way. `Series AA` is the case that
    matters: the V2 substring test mapped it to EARLY_STAGE by collision, and under V3 it
    is simply not a representable round.

    Bridge, extension, venture debt and convertible notes are NOT rounds. They describe
    instrument or event structure and have their own fields; returning None here is the
    correct answer, not a gap.
    """
    if not round_label:
        return None
    label = round_label.lower().strip()
    # Strip qualifiers that describe the round without changing which round it is.
    for suffix in (" extension", " round", " financing", " funding"):
        while label.endswith(suffix):
            label = label[: -len(suffix)].strip()
    if _PRE_SEED_RE.match(label):
        return "PRE_SEED"
    if label == "seed":
        return "SEED"
    if label == "angel":
        return "ANGEL"
    m = _ROUND_RE.match(label)
    if m:
        letter, number = m.group(1), m.group(2)
        if number:
            # A leading zero or a zero index is not a real round variant.
            if number.startswith("0"):
                return None
            return f"SERIES_{letter.upper()}{int(number)}"
        return f"SERIES_{letter.upper()}"
    return None


def _derive_vc_stage(canonical_round: str | None) -> str | None:
    """Canonical `round` -> broad `vc_stage` (V3 §T14).

    Derived from the normalized round, never from `round_label`. The series letter is
    parsed and compared as a letter, so there is no ceiling: Series H, I, J and beyond all
    resolve to LATE_STAGE, which the V2 literal enumeration could not do.
    """
    if not canonical_round:
        return None
    if canonical_round == "PRE_SEED":
        return "PRE_SEED"
    if canonical_round in ("SEED", "ANGEL"):
        return "SEED"
    if canonical_round.startswith("SERIES_"):
        letter = canonical_round[len("SERIES_"):][:1].lower()
        if letter == _EARLY:
            return "EARLY_STAGE"
        if letter in _GROWTH:
            return "GROWTH"
        if letter > "c":
            return "LATE_STAGE"
    return None


def _compute_multiples(
    implied_enterprise_value: float | None,
    value_currency: str | None,
    target_revenue: float | None,
    target_revenue_period_type: str | None,
    target_ebitda: float | None,
    target_ebitda_period_type: str | None,
    financials_currency: str | None,
    log: Any,
    cluster_id: str,
    v2_event_type: str | None = None,
    announced_date: str | None = None,
    target_revenue_period_end: str | None = None,
    target_ebitda_period_end: str | None = None,
) -> dict:
    """Compute EV/Revenue and EV/EBITDA multiples.

    Requires a whole-company implied_enterprise_value. TTM is treated as LTM
    (interchangeable industry usage). A recent source-reported ANNUAL actual may
    populate the LTM analytical slot when date-aligned, without relabeling the
    source financial period. Cross-currency pairs are flagged NM without conversion.
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

    if not implied_enterprise_value or implied_enterprise_value <= 0:
        return result

    currency_mismatch = bool(
        value_currency and financials_currency and value_currency != financials_currency
    )

    _RANGES = {"revenue": (0.1, 50.0), "ebitda": (1.0, 100.0)}

    def _parse_date(value: str | None, *, year_only_as_latest_day: bool = False) -> date | None:
        if not value:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) == 4 and text.isdigit():
            # Eligibility-only convention: year-only ANNUAL periods are tested
            # against Dec. 31 of that year, the latest possible full-year end.
            # The source period_end stored on the transaction remains unchanged.
            return date(int(text), 12, 31) if year_only_as_latest_day else None
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None

    announced_dt = _parse_date(announced_date)

    def _annual_actual_is_trailing_eligible(period_end: str | None, metric: str) -> bool:
        period_end_dt = _parse_date(period_end, year_only_as_latest_day=True)
        if announced_dt is None or period_end_dt is None:
            log.debug("cluster=%s %s ANNUAL missing date alignment inputs", cluster_id, metric)
            return False
        age_days = (announced_dt - period_end_dt).days
        if age_days < 0:
            log.debug("cluster=%s %s ANNUAL period_end=%r after announced_date=%r", cluster_id, metric, period_end, announced_date)
            return False
        if age_days > 455:
            log.debug("cluster=%s %s ANNUAL period_end=%r stale by %d days", cluster_id, metric, period_end, age_days)
            return False
        return True

    def _slot(period_type: str | None, period_end: str | None, metric: str) -> str | None:
        p = (period_type or "").upper()
        if p in ("LTM", "TTM"):
            return f"ev_to_{metric}_ltm"
        if p == "NTM":
            return f"ev_to_{metric}_ntm"
        if p == "ANNUAL" and _annual_actual_is_trailing_eligible(period_end, metric):
            return f"ev_to_{metric}_ltm"
        log.debug("cluster=%s %s period_type=%r not LTM/NTM — skipping multiple", cluster_id, metric, period_type)
        return None

    calculated_in_range = False
    calculated_out_of_range = False

    for metric, raw_value, period_type, period_end in (
        ("revenue", target_revenue, target_revenue_period_type, target_revenue_period_end),
        ("ebitda", target_ebitda, target_ebitda_period_type, target_ebitda_period_end),
    ):
        if not raw_value or raw_value <= 0:
            continue
        slot = _slot(period_type, period_end, metric)
        if slot is None:
            continue
        if currency_mismatch:
            calculated_out_of_range = True
            continue
        multiple = round(implied_enterprise_value / raw_value, 2)
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


def _period_end_precision(period_end: str | None) -> str | None:
    """Precision of a stated period end, read off its shape.

    Formatting, not inference: "2026" is a year because the source wrote a year, and it
    stays "2026". Expanding it to 2026-12-31 would assert a day the source never gave.

    One vocabulary across both normalized tables: exact | month | quarter | year, from
    the canonical metric row. `quarter` is not produced here -- a stored period end
    carries no quarter marking to read it off -- but it is part of the vocabulary and a
    caller may hold it.
    """
    if not period_end:
        return None
    text = str(period_end).strip()
    if len(text) == 4 and text.isdigit():
        return "year"
    if len(text) == 7:
        return "month"
    if len(text) >= 10:
        return "exact"
    return None


# Canonical metric_type per flat amount column. The canonical vocabulary renames
# ADJ_EBITDA to EBITDA; this implementation's column is target_ebitda and its prompt
# already captures "EBITDA or Adjusted EBITDA" into it, so EBITDA is the honest name.
_FINANCIAL_METRIC_TYPES: tuple[tuple[str, str, str, str], ...] = (
    # (metric_type, amount field, period-type field, period-end field)
    ("REVENUE", "target_revenue", "target_revenue_period_type_v2", "target_revenue_period_end"),
    ("EBITDA", "target_ebitda", "target_ebitda_period_type_v2", "target_ebitda_period_end"),
)


def _write_financial_metrics(
    conn,
    cluster_id: str,
    field_values: dict,
    metric_currencies: dict,
    bundle: dict,
    log: Any,
) -> int:
    """Write the resolved source-stated financial metrics as normalized rows.

    Canonical resolved facts, following the same rule the multiple rows follow: the
    value is the one reconciliation already chose, read from `field_values`, never from
    a staging row. A fact whose conflict went unresolved has no value in `field_values`
    and therefore produces NO row -- this table does not become a second place
    disagreements are stored.

    CURRENCY BELONGS TO THE ROW. Each metric carries the currency anchored to its own
    amount, not the shared `financials_currency`, which is null whenever the two metrics
    disagree and would strip a currency from both figures that each of them had.

    SOURCE-STATED ONLY. `is_calculated` is 0 on every row: revenue and EBITDA are
    collected, never computed here. Nothing is derived to fill this table, and no figure
    is ever recovered from a multiple -- an as-reported multiple and a stated financial
    are separate facts, and dividing one by the other would manufacture the third.

    FX STAYS NULL. `fx_rate` and `fx_rate_date` record a conversion that was performed.
    None is, so there is none to record.

    PERIOD TYPE IS CARRIED, NOT TRANSLATED. ANNUAL stays ANNUAL and INTERIM_YTD stays
    INTERIM_YTD; neither is folded into LTM or NTM. Precision is read off the stored
    period end's own shape, so a bare "2026" stays a year and is never expanded to a day.

    Re-aggregation replaces this transaction's rows.
    """
    conn.execute("DELETE FROM transaction_financial WHERE transaction_id = ?", (cluster_id,))
    source = (bundle.get("sources") or [{}])[0]
    written = 0
    for metric_type, amount_field, period_type_field, period_end_field in _FINANCIAL_METRIC_TYPES:
        amount = field_values.get(amount_field)
        if amount is None:
            continue
        period_end = field_values.get(period_end_field)
        conn.execute(
            """
            INSERT INTO transaction_financial (
                transaction_id, metric_type, value_captured, value_currency,
                period_type, period_end_date, period_end_date_precision,
                fx_rate, fx_rate_date, margin_pct, is_calculated,
                staging_extraction_id, source_raw_id, extraction_prompt_version
            ) VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,0,?,?,?)
            """,
            (
                cluster_id, metric_type, amount,
                metric_currencies.get(amount_field),
                field_values.get(period_type_field),
                period_end, _period_end_precision(period_end),
                source.get("staging_extraction_id"), source.get("source_raw_id"),
                _FULL_VERSION,
            ),
        )
        written += 1
    if written:
        log.info("cluster=%s wrote %d financial metric row(s)", cluster_id, written)
    return written


def _write_as_reported_multiples(
    conn,
    cluster_id: str,
    field_values: dict,
    flagged_keys: set,
    observations: dict,
    log: Any,
) -> int:
    """Write the RESOLVED multiple for each canonical key. Resolved facts only.

    Reads what reconciliation decided, not what any one source said. The value comes
    from `field_values` -- populated by the same _pick_value the scalar fields use --
    and the key itself supplies the dimensions, parsed back out of
    `multiple.{type}.{basis}.{end}`.

    AN UNRESOLVED KEY PRODUCES NO ROW. Product ruling: transaction_multiple carries
    resolved canonical facts only. Where two sources -- or one source twice -- claim
    different values for one key and the generic machinery cannot choose, the claims
    stay in `transaction_field_observation` and the disagreement stays in
    `aggregation_conflict_log` with flagged_for_review set. Nothing is silently chosen,
    and two indistinguishable canonical values are never surfaced.

    That is why Maverick produces no canonical multiple: its 11.5x and its tax-adjusted
    10.5x compose one key, differ, and share a source, a tier and a confidence, so
    _pick_value cannot separate them and neither can the aggregation prompt -- Product
    does not distinguish adjusted from unadjusted structurally, so there is nothing to
    separate them BY. Both observations survive; no canonical row is asserted.

    Three fields are NULL by rule on every row written here:

      quality                  -- CALCULATED, NM and NOT_CALCULABLE all report the
                                  outcome of a calculation, and none was performed.
      denominator_financial_id -- "Expected on calculated rows" (V3 6) is a scoped
                                  expectation whose scope excludes as-reported rows.
      the denominator itself   -- never reconstructed. Nothing is divided here.

    Re-aggregation replaces this transaction's as-reported rows. The delete is scoped to
    source_flag = 'as_reported' so a calculated row is never removed by a writer that
    does not own it.
    """
    conn.execute(
        "DELETE FROM transaction_multiple WHERE transaction_id = ? AND source_flag = 'as_reported'",
        (cluster_id,),
    )
    written = 0
    for key in sorted(k for k in field_values if k.startswith(_MULTIPLE_FIELD_PREFIX)):
        if key in flagged_keys:
            log.info("cluster=%s %s unresolved — no canonical multiple written; the "
                     "observations and the conflict record stand", cluster_id, key)
            continue
        value = field_values.get(key)
        if value is None:
            continue
        # multiple.{type}.{basis}.{end} — split from the left on the fixed number of
        # segments, so a period end containing a dot could not shift the others.
        parts = key[len(_MULTIPLE_FIELD_PREFIX):].split(".", 2)
        if len(parts) != 3:
            log.warning("cluster=%s unparseable multiple key %r — skipped", cluster_id, key)
            continue
        multiple_type, period_basis, period_end = (part or None for part in parts)
        # Provenance and the numerator family come from the observations behind the key.
        # Any of them will do for the family -- it is determined by multiple_type, which
        # is part of the key, so every observation here agrees on it.
        obs = observations.get(key) or []
        source_key = obs[0].get("source_key") if obs else None
        numerator = ("implied_enterprise_value"
                     if multiple_type in ("EV_REVENUE", "EV_EBITDA", "EV_EBIT", "EV_FCF")
                     else "implied_equity_value")
        conn.execute(
            """
            INSERT INTO transaction_multiple (
                transaction_id, multiple_type, multiple_value,
                period_basis, period_end_date, period_end_date_precision,
                numerator_value_type, denominator_financial_id,
                source_flag, quality, multiple_as_reported,
                staging_extraction_id, source_raw_id, extraction_prompt_version
            ) VALUES (?,?,?,?,?,?,?,NULL,'as_reported',NULL,?,?,?,?)
            """,
            (
                cluster_id, multiple_type, value,
                period_basis, period_end, _period_end_precision(period_end),
                numerator,
                _reported_multiple_text(conn, cluster_id, key, value),
                source_key[0] if source_key else None,
                source_key[1] if source_key else None,
                _FULL_VERSION,
            ),
        )
        written += 1
    if written:
        log.info("cluster=%s wrote %d resolved as-reported multiple row(s)", cluster_id, written)
    return written


def _reported_multiple_text(conn, cluster_id: str, key: str, value: Any) -> str | None:
    """The verbatim wording behind a resolved value, read from the preservation rows.

    Queried from the ledger rather than taken from the aggregation bundle, because the
    bundle deliberately does not contain these rows: `reported_multiple` is absent from
    _FIELDS, so _load_observation_input drops it. That exclusion is the point -- a
    preserved record is not a reconciliation candidate -- and it means the wording has
    to be fetched where it actually lives.

    A convenience for a reader of the canonical row, recovered rather than re-derived.
    The authoritative record of every stated multiple remains the observations
    themselves; this only reads them, and returns None when none carries this value.
    """
    rows = conn.execute(
        "SELECT field_value FROM transaction_field_observation "
        "WHERE transaction_id = ? AND field_name = 'reported_multiple' AND is_current = 1 "
        "ORDER BY observation_id",
        (cluster_id,),
    ).fetchall()
    for row in rows:
        try:
            item = json.loads(row["field_value"])
        except (ValueError, TypeError):
            continue
        if not isinstance(item, dict):
            continue
        if reported_multiple_field_name(item) != key:
            continue
        if item.get("multiple_value") == value:
            return item.get("as_reported_text")
    return None


def _event_type(fv: dict) -> str | None:
    return fv.get("v2_event_type") or fv.get("deal_type")


def _derive_investment_amount(fv: dict) -> float | None:
    """One named investor's check. Supplemental party-level detail, usually null.

    **Not the event's magnitude.** That is `round_size` for a financing event, and it
    reaches `transaction_size` from there. A $50M check into a $100M round leaves
    `round_size = 100M` and the check recorded against its investor; nothing is added,
    rolled up, or substituted in either direction.

    This previously derived `round_size or value_amount` for any non-control event,
    which broke the definition twice over: it copied a *round total* into a field that
    means *one investor's check*, and where no round size existed it fell back to a
    generic `value_amount` that names no investor at all. Both assert a party-level
    fact no source stated, and on the legacy funding rows the second one is how a
    misclassified raise acquired a canonical home.

    Per-investor checks are captured at the staging layer in
    `staging_investor.investment_amount`, keyed to the investor that wrote them. There
    is no transaction-level source for this field, so it derives to None until one
    exists — which is the documented expectation, not a gap: it is supplemental detail,
    null for most deals. Deriving anything else here would be manufacturing.
    """
    return None


def _derive_deal_value_currency(
    fv: dict, log: Any = None, cluster_id: str | None = None
) -> str | None:
    """Currency companion for the derived value fields (tag-and-defer; no USD
    conversion). Unanimity-or-null over every currency source present:
    `valuation_currency` (post-money-based funding values), `value_currency`
    (control-deal values), and `round_currency` (funding round amounts).

    Collect the distinct currencies actually present; if they agree, tag with that
    currency; if any two differ, return None rather than guess. The null is itself
    the queryable mismatch signal — a row with derived values populated but
    deal_value_currency null is detectable in SQL, so no flag column is needed. The
    log warning is a run-time convenience only.

    Generalizes the original two-source precedence-plus-mismatch-guard rule: with
    two sources the two are the same function; stating it as unanimity extends to the
    third (round) currency without inventing a tiebreak, and preserves the property
    the original relied on — the null is the signal. (decisions.md 2026-08-11,
    "Round Currency Enters the Derived-Value Currency Tag").
    """
    distinct = {
        c
        for c in (
            fv.get("valuation_currency"),
            fv.get("value_currency"),
            fv.get("round_currency"),
        )
        if c
    }
    if len(distinct) > 1:
        if log is not None:
            log.warning(
                "cluster_id=%s currency mismatch among %s — deal_value_currency set null",
                cluster_id, sorted(distinct),
            )
        return None
    return next(iter(distinct), None)


def _resolve_pct_acquired(fv: dict) -> tuple[float | None, str | None]:
    """§2.6 — the percentage, only when a source stated one.

    This used to default a silent control event to 100 ('assumed'). It no longer does.
    The assumption reached implied_equity_value, implied_enterprise_value and the
    calculated multiples indistinguishably from a stated fact, and it made a source
    that said the whole company changed hands produce the same row as a source that
    said nothing at all.

    Unstated is None, and None means "the source did not say". Nothing downstream may
    read it as 100: grossing a stake-level figure to a whole-company basis needs a real
    percentage, and inventing the denominator manufactures the numerator.

    No is_minority parameter any more. It existed only to stop the default from turning
    a minority stake into a whole-company buy; with no default there is nothing to stop.
    Returns (pct, source), where source is 'stated' or None -- never 'assumed', because
    no assumption is made.
    """
    pct = fv.get("pct_acquired")
    if pct is None:
        return None, None
    return float(pct), "stated"


def _derive_transaction_value(
    fv: dict,
    equity_value: float | None,
    total_debt: float | None,
    *,
    is_control: bool,
    is_below_control: bool = False,
    equity_currency: str | None = None,
    total_debt_currency: str | None = None,
) -> tuple[float | None, str | None]:
    """Tier-1 transaction value + basis (§2.1.1). As-reported wins; otherwise
    equity_value below control, equity_value + gross debt (total_debt) at control when
    debt is known, otherwise equity consideration only.
    Cash is never netted.

    TAKES CONTROL STATUS, NOT A PERCENTAGE. Keyword-only on purpose: these replaced a
    float in this position, and a caller still passing a percentage positionally would
    put a truthy number where a boolean belongs and take the control branch in silence.

    TWO FLAGS, NOT ONE, because the percentage carried THREE answers: control,
    below control, and unknown. Collapsing them into a single boolean would hand every
    unknown row the below-control branch, quietly giving a transaction value to deals
    that previously and correctly had none. `is_control` and `is_below_control` are both
    false when the source establishes neither, and that case still returns None. The 50 threshold was only ever a proxy
    for "is this a control deal", and reading it off a percentage meant a silent control
    deal needed a percentage invented for it. The control question is answered directly
    by the event type and the minority signal, which need no number, so the branch is
    now selected by what it was always actually asking.

      STATED                 — source stated a TRANSACTION_VALUE. Needs no percentage
                               and no control question: the source stated the whole deal.
      EQUITY_BELOW_CONTROL   — established below control; equity_value, no debt.
      (none)                 — neither established; no transaction value is claimed.
      EQUITY_PLUS_TOTAL_DEBT — control and total_debt known.
      EQUITY_VALUE_ONLY      — control and debt unknown; equity consideration only.

    Returns (None, None) when there is no equity to base on.
    EQUITY_VALUE_ONLY does not assume debt=0; it preserves the known purchase-price
    component for the stake actually acquired. The gross-debt branch
    is dormant until total_debt is populated (extraction is a later piece).

    **Funding events derive no transaction value at all.** A round is primary capital
    into the company; there is no purchase price, so the field is categorically
    inapplicable rather than merely usually absent. This used to be an assumption about
    upstream — funding rows reach the funding extractor, which never writes the M&A
    value fields — and the assumption does not hold for rows extracted before
    2026-08-07, which went through the M&A path when it had no `round_size` write. On
    those, a raise sits in `value_amount` typed `TRANSACTION_VALUE`, and without this
    gate every re-aggregation regenerates a canonical purchase price from it.
    `MINORITY_INVESTMENT` is deliberately outside the gate: a secondary purchase of a
    stake is an ordinary acquisition with a real consideration.
    """
    if _event_type(fv) in _FUNDING_EVENT_TYPES:
        return None, None
    value_amount = fv.get("value_amount")
    if fv.get("value_type") == "TRANSACTION_VALUE" and value_amount and value_amount > 0:
        return float(value_amount), "STATED"
    if equity_value is None:
        return None, None
    if is_below_control:
        return equity_value, "EQUITY_BELOW_CONTROL"
    if not is_control:
        return None, None
    # The debt-inclusive basis needs both currencies known and equal. When it is
    # refused, the known equity consideration is not discarded — it falls to
    # EQUITY_VALUE_ONLY, which has never implied debt is zero, only that debt could
    # not be added.
    if total_debt is not None and _currencies_usable(equity_currency, total_debt_currency):
        return round(equity_value + total_debt, 2), "EQUITY_PLUS_TOTAL_DEBT"
    return equity_value, "EQUITY_VALUE_ONLY"


# §2.4 — the rungs that may supply `transaction_size`. Every value names the SOURCE
# FIELD the magnitude came from, which is what keeps the enum one-dimensional.
#
# ONE value is reserved rather than live: `SPIN_SPLIT_CONSIDERATION_VALUE`, because no
# spin/split consideration field exists to read. Reserving it keeps the vocabulary stable
# so a later commit adds a branch rather than renaming stored data.
#
# `SOLE_INVESTOR_AMOUNT` is NOT reserved — it is removed outright, on semantics rather
# than availability; see the note under "Deliberately ABSENT" below, which supersedes the
# earlier framing that grouped it here as a second reserved-for-want-of-a-field value.
#
# Deliberately ABSENT, and not to be added without reopening the decision:
#   EQUITY_VALUE / EQUITY_CONSIDERATION — every case where a stake-level equity figure
#     can safely stand for the magnitude already produces `transaction_value`. The only
#     states where transaction_value is null while equity_value is known are those where
#     pct_acquired is unknown, i.e. the scope is unknown, so the figure could be the
#     whole company.
#   ENTERPRISE_VALUE / IMPLIED_ENTERPRISE_VALUE — below control this is the grossed-up
#     whole-company figure; it would report a 27%-for-$600M deal as $2.22B (spec §2.10
#     item 3, parked on that gross-up and not unparked by the currency/period work).
#   EQUITY_BELOW_CONTROL — a `transaction_value_basis` value. It names a derivation
#     condition rather than a source field, and the control status it records is already
#     carried by `transaction_value_basis`, `is_minority` and `pct_acquired`.
#   SOLE_INVESTOR_AMOUNT — removed 2026-08-17. An investor's check is not the event's
#     magnitude. Reporting a $50M check as a $100M round's size is wrong regardless of
#     how many investors disclosed, so this was never a disclosure-threshold problem
#     that a sole-investor restriction could solve. `investment_amount` is supplemental
#     party-level detail; when the round total is undisclosed the honest magnitude is
#     null. (It was previously reserved on the stated ground that no per-investor
#     column existed — that was wrong: `staging_investor.investment_amount` does. The
#     rung is removed on the semantics, not on availability.)
TRANSACTION_SIZE_BASES = frozenset({
    "TRANSACTION_VALUE",
    "ROUND_SIZE",
    "SPIN_SPLIT_CONSIDERATION_VALUE",  # reserved — no such source field exists
})

_MA_EVENT_TYPES = frozenset({"ACQUISITION", "MERGER", "REVERSE_MERGER"})
_SPIN_SPLIT_EVENT_TYPES = frozenset({"SPIN_OFF", "SPLIT_OFF", "SPIN_SPLIT"})


def _derive_transaction_size(
    fv: dict,
    transaction_value: float | None,
) -> tuple[float | None, str | None]:
    """The common transaction magnitude + the rung that supplied it (§2.4).

    Derived in aggregation, **never extracted** — no extractor decides what belongs in
    it. Keyed on event family, and the families are **disjoint**: a funding round never
    falls through to a purchase price, and an M&A deal never falls through to a round
    size. Ordering only has meaning within a family.

      M&A        -> transaction_value              -> TRANSACTION_VALUE
      Funding    -> round_size                     -> ROUND_SIZE
      Spin/Split -> (reserved, no live rung)       -> None
      otherwise  -> None

    `transaction_size_basis` is returned alongside and is non-null whenever the size is,
    because 600M via a purchase price and 600M via a round size are the same kind of
    number only if you know which rung produced them. The caller must write both or
    neither.

    **The funding family is exactly the classifier's three funding types.** A PIPE or
    other public-company primary raise is deliberately *not* forced into it
    (`prompts/deal_type_classifier.md`). Widening the family here would silently
    reclassify deals through the size field.

    An explicitly recognized PIPE no longer reaches this function at all: Stage 3 stamps
    it `RECOGNIZED_NOT_PROFILED` and the row stops at staging, so it is never clustered
    and never aggregated (`lib/pipe_recognition.py`). Other public-company primary
    raises still land in `UNKNOWN` and receive a null size, which remains an accepted
    coverage decision rather than an oversight.

    No equity rung and no EV rung — see `TRANSACTION_SIZE_BASES` for why each is absent.

    A funding round's `post_money_valuation` is never consulted. A valuation is not an
    as-transacted magnitude, and the two are one word apart in a review sheet.
    """
    event = _event_type(fv)

    if event in _FUNDING_EVENT_TYPES:
        round_size = fv.get("round_size")
        if round_size and round_size > 0:
            return float(round_size), "ROUND_SIZE"
        # No investor-check fallback. A check is one investor's contribution, not the
        # size of the event, so a known $50M check against an undisclosed total yields
        # a null magnitude — the round size is genuinely unknown. Summing checks is
        # doubly wrong: per-investor disclosure runs ~30% for leads and under 5% for
        # others, so a sum understates the round while presenting as one, and the
        # shortfall is invisible.
        return None, None

    if event in _SPIN_SPLIT_EVENT_TYPES:
        # SPIN_SPLIT_CONSIDERATION_VALUE would go here once the source field exists.
        # Note that a pure pro-rata spin has no consideration at all — nothing changes
        # hands for value — so null will remain correct for much of this family even
        # then, and a zero would assert a fact the event does not contain.
        return None, None

    if event in _MA_EVENT_TYPES:
        if transaction_value is not None and transaction_value > 0:
            return float(transaction_value), "TRANSACTION_VALUE"
        return None, None

    return None, None


def _derive_equity_value(fv: dict) -> tuple[float | None, str | None]:
    """Stake-level equity value + basis — the consideration for the stake actually
    acquired, never grossed up, uniform across control and non-control (§4.2).

      STATED             — source stated an equity figure (value_type=EQUITY_VALUE).

    Post-money is NOT equity value: it belongs in post_money_valuation (and the
    implied tier via _derive_implied_equity), never here.

    **Funding events derive no equity value**, enforced rather than assumed. A raise
    buys no existing equity, so the field is categorically inapplicable. This was
    previously left to upstream — "a primary-capital raise has null value fields, so it
    yields None here" — which is true only of rows extracted after the funding path
    split on 2026-08-07. Older rows carry the raise in `value_amount`, and without the
    gate a stale `EQUITY_VALUE` there would gross up through `_derive_implied_equity`
    into an implied tier and a multiple. The amount still reaches `investment_amount`
    (bug #8), so nothing is lost by refusing here.

    **Every writer here must be stake-level by construction**, because the field feeds
    `_derive_implied_equity`, which divides by pct. A whole-company amount arriving
    here is grossed up a second time: a 2.2B figure at pct 27 becomes 8.15B of implied
    equity, and any multiple struck off that is manufactured. Decision "FINDING:
    equity_value Conflates Stake-Level and 100%-Basis Scope" (2026-08-17).

    One guard enforces that: `MARKET_CAPITALIZATION` is its own value type as of HC
    prompt 0.18, so a market cap no longer arrives typed `EQUITY_VALUE`. The
    `== "EQUITY_VALUE"` test below is what excludes it; the fact itself is still
    retained in the observation ledger.

    A `PER_SHARE_X_SHARES` basis was defined here and never produced a value:
    `sec_shares` was hardcoded None, since SEC enrichment does not run in this
    implementation, so the branch could not fire. It is removed rather than left
    standing, because what it computed was not what its name claimed. It multiplied the
    per-share offer by TOTAL shares outstanding, which prices the whole company, and
    then required pct == 100 to make that coincide with the stake -- so the gate was
    compensating for the count rather than establishing a fact. The canonical model
    separates total shares outstanding, shares acquired in the transaction, and the
    percentage; this implementation holds only the first, and none of the three as a
    canonical field. Restoring any per-share derivation is a separate question that
    needs those distinctions settled first, and nothing here presumes its answer.
    """
    if _event_type(fv) in _FUNDING_EVENT_TYPES:
        return None, None
    value_amount = fv.get("value_amount")
    if fv.get("value_type") == "EQUITY_VALUE" and value_amount and value_amount > 0:
        return float(value_amount), "STATED"
    return None, None


def _derive_implied_equity(
    equity_value: float | None,
    pct: float | None,
) -> float | None:
    """Whole-company (100%) equity value: equity_value grossed to 100% by pct, or
    (future) source-stated.

    NEVER from transaction_value (value_amount) or post_money_valuation — decision
    "implied_equity_value Derives From equity_value Only" (2026-08-12). `pct` MUST be
    §2.6-resolved by the caller (decision "pct_acquired Must Be Resolved Before
    Threshold Evaluation"): NULL/non-positive pct yields None, not a 100% gross-up,
    which would reopen the manufactured-numerator defect through a pct-null door.
    Funding rounds vacate equity_value, so they produce no implied by construction.

    No fv param by design — a source-stated implied lookup (with Named Value Fields,
    later) will reintroduce one; until then there is deliberately no field-values
    surface to read pct off.
    """
    if equity_value is None or equity_value <= 0:
        return None
    if pct is None or pct <= 0:
        return None
    if pct < 100:
        return round(equity_value / (pct / 100.0), 2)
    return equity_value


def _currencies_usable(left: str | None, right: str | None) -> bool:
    """True only when both currencies are known and equal.

    The single gate for every calculation that mixes consideration with a
    balance-sheet figure. Unknown on either side does not calculate, and
    known-but-differing does not calculate; no FX conversion is attempted, because a
    conversion needs an FX date this pipeline does not carry. Decision
    "Debt and Cash Arithmetic Requires Known, Equal Currencies" (2026-08-17).
    """
    return bool(left and right and left == right)


def _derive_net_debt(
    reported_net_debt: float | None,
    reported_net_debt_currency: str | None,
    total_debt: float | None,
    total_debt_currency: str | None,
    total_debt_as_of: str | None,
    cash_st: float | None,
    cash_st_currency: str | None,
    cash_st_as_of: str | None,
) -> tuple[float | None, str | None, str | None, str | None]:
    """Net debt for the implied tier: reported if present, else the components.

    Returns (net_debt, currency, as_of_date, basis) where basis is one of
    ``REPORTED``, ``CALCULATED_TOTAL_DEBT_MINUS_CASH_ST``, or None.

    `total_debt` and `Cash_ST` are point-in-time balance-sheet items, so a derived
    net debt requires them to describe the *same* balance sheet: one shared as-of
    date and one shared currency, both known. Two figures from different dates are
    not a balance sheet, and an unknown date is insufficient evidence rather than an
    implied match. Missing cash is never treated as zero.

    Reported/manual net debt stays preferred (decision "Debt and Cash Inputs"); it
    is a single figure, so it carries no component-coherence requirement — only its
    own currency, checked by the caller against the deal currency.
    """
    if reported_net_debt is not None:
        return float(reported_net_debt), reported_net_debt_currency, None, "REPORTED"

    if total_debt is None or cash_st is None:
        return None, None, None, None
    if not _currencies_usable(total_debt_currency, cash_st_currency):
        return None, None, None, None
    if not (total_debt_as_of and cash_st_as_of and total_debt_as_of == cash_st_as_of):
        return None, None, None, None

    return (
        round(float(total_debt) - float(cash_st), 2),
        total_debt_currency,
        total_debt_as_of,
        "CALCULATED_TOTAL_DEBT_MINUS_CASH_ST",
    )


def _derive_implied_enterprise_value(
    value_amount: float | None,
    value_type: str | None,
    implied_equity_value: float | None,
    net_debt: float | None,
    *,
    implied_equity_currency: str | None = None,
    net_debt_currency: str | None = None,
    net_debt_basis: str | None = None,
) -> tuple[float | None, str | None]:
    """Canonical 100%-basis enterprise value + basis.

    STATED                                — source stated a whole-company EV.
    IMPLIED_EQUITY_PLUS_REPORTED_NET_DEBT — implied_equity_value + reported net_debt.
    IMPLIED_EQUITY_PLUS_CALCULATED_NET_DEBT — implied_equity_value + derived net_debt.

    `net_debt` arrives already resolved by _derive_net_debt, which owns the
    component-coherence rules; the basis suffix follows that resolution and is passed
    by the caller via `net_debt_basis`. Returns (None, None) when no path is
    satisfiable.

    §2.10 item 1 — the calculated bases add consideration in deal currency to a
    balance-sheet figure in the target's reporting currency. Both currencies must be
    known and equal or the sum is refused: an unknown currency is insufficient
    evidence, not a licence to assume agreement, and there is no plausible-range
    backstop on this field to catch a wrong-currency result. No conversion is
    attempted; that needs an FX date this pipeline does not carry.

    A STATED enterprise value is a single source-stated figure rather than a sum, so
    the guard does not apply to it.
    """
    if value_type == "ENTERPRISE_VALUE" and value_amount and value_amount > 0:
        return float(value_amount), "STATED"
    if implied_equity_value is None or net_debt is None:
        return None, None
    if not _currencies_usable(implied_equity_currency, net_debt_currency):
        return None, None

    suffix = (
        "CALCULATED_NET_DEBT"
        if net_debt_basis == "CALCULATED_TOTAL_DEBT_MINUS_CASH_ST"
        else "REPORTED_NET_DEBT"
    )
    return round(implied_equity_value + net_debt, 2), f"IMPLIED_EQUITY_PLUS_{suffix}"


def _source_key(obs: dict) -> tuple[Any, Any] | Any:
    return obs.get("source_key") or obs.get("observation_id")


def _pick_value_amount_for_type(field_observations: dict[str, list[dict]], target_value_type: str) -> float | None:
    """Pick a value_amount observation whose sibling value_type has the requested semantic type.

    The legacy aggregate has one value_amount/value_type winner, but Tier 1 transaction
    value and Tier 2 EV are independent canonical outputs. This helper lets each
    derivation consume the best observation of its own semantic type.
    """
    typed_source_keys = {
        _source_key(obs)
        for obs in field_observations.get("value_type", [])
        if obs.get("value") == target_value_type
    }
    if not typed_source_keys:
        return None

    amount_observations = [
        obs
        for obs in field_observations.get("value_amount", [])
        if _source_key(obs) in typed_source_keys and obs.get("value") is not None
    ]
    if not amount_observations:
        return None

    chosen, needs_llm, conflict_obs = _pick_value("value_amount", "number", amount_observations)
    if chosen is not None:
        return float(chosen)
    if needs_llm and conflict_obs:
        # Avoid a second aggregation prompt in the deterministic derivation path.
        # The canonical field still gets the best available typed amount by tier
        # and confidence; same-tier/same-confidence conflicts remain logged by the
        # legacy value_amount field if the raw amount itself conflicts.
        ranked = sorted(
            conflict_obs,
            key=lambda o: (
                TIER_ORDER.index(o.get("tier")) if o.get("tier") in TIER_ORDER else len(TIER_ORDER),
                _CONF_RANK.get(o.get("model_confidence") or "MEDIUM", 1),
            ),
        )
        return float(ranked[0]["value"])
    return None


# Financial metrics and the qualifiers that are only meaningful alongside them.
# `financials_currency` is deliberately absent: one column serves both metrics, so
# it is resolved separately by unanimity over the anchoring sources actually used.
# Balance-sheet amounts are as-of figures, not periods. This is the economic period
# type of the amount, and the only legal value for it — there is no LTM/TTM/NTM
# concept for a balance sheet, and filing frequency (annual/quarterly) is filing
# context rather than the period of the amount.
# The observation stage that marks a human correction of an extraction-layer
# fact. It is admitted by the observation read path and supersedes extraction
# observations for the same field.
MANUAL_REMEDIATION_STAGE = "MANUAL_REMEDIATION"

BALANCE_SHEET_PERIOD_TYPE = "POINT_IN_TIME"

_METRIC_COMPANION_FIELDS: dict[str, tuple[str, ...]] = {
    "target_revenue": (
        "target_revenue_period_type",
        "target_revenue_period_type_v2",
        "target_revenue_period_end",
    ),
    "target_ebitda": (
        "target_ebitda_period_type",
        "target_ebitda_period_type_v2",
        "target_ebitda_period_end",
    ),
}


def _values_match(left: Any, right: Any) -> bool:
    """Compare an observation value against a chosen canonical value."""
    if left is None or right is None:
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


def _anchor_source_keys(
    field_observations: dict[str, list[dict]], anchor_field: str, anchor_value: Any
) -> set:
    """Source keys of the observations that supplied the chosen value for a field."""
    return {
        _source_key(obs)
        for obs in field_observations.get(anchor_field, [])
        if _values_match(obs.get("value"), anchor_value)
    }


def _companion_from_sources(
    field_observations: dict[str, list[dict]], companion_field: str, source_keys: set
) -> Any:
    """Resolve a qualifier using only the sources that supplied the anchor amount.

    Returns None when none of those sources stated it. That null is deliberate: a
    qualifier borrowed from a different source silently re-labels an amount its own
    source never described that way, which is worse than an acknowledged unknown.
    """
    if not source_keys:
        return None
    observations = [
        obs
        for obs in field_observations.get(companion_field, [])
        if _source_key(obs) in source_keys and obs.get("value") is not None
    ]
    if not observations:
        return None

    field_type = _FIELD_TYPE.get(companion_field, "string")
    chosen, needs_llm, conflict_obs = _pick_value(companion_field, field_type, observations)
    if chosen is not None:
        return chosen
    if needs_llm and conflict_obs:
        # Stay on the deterministic path, as _pick_value_amount_for_type does: rank
        # by tier then confidence rather than opening a second aggregation prompt.
        ranked = sorted(
            conflict_obs,
            key=lambda o: (
                TIER_ORDER.index(o.get("tier")) if o.get("tier") in TIER_ORDER else len(TIER_ORDER),
                _CONF_RANK.get(o.get("model_confidence") or "MEDIUM", 1),
            ),
        )
        return ranked[0]["value"]
    return None


def _anchor_metric_qualifiers(
    field_values: dict, field_observations: dict[str, list[dict]]
) -> dict:
    """Re-resolve financial qualifiers against the source of their own amount (§2.10).

    Mutates `field_values` in place and returns each anchored amount's own currency,
    keyed by amount field, for callers that can hold a currency per value. Every canonical field is otherwise selected
    independently, so `target_revenue` can come from one source while
    `target_revenue_period_end` and `financials_currency` come from another. The
    period end is not cosmetic — the annual-as-trailing rule keys off it, so a
    borrowed date can decide whether a multiple is computed at all.

    `financials_currency` is shared by both metrics, so it cannot be anchored to one
    of them. It resolves by unanimity over the currencies the anchoring sources
    actually stated, and to null on disagreement — the same rule, and the same
    "null is the signal" posture, as _derive_deal_value_currency.
    """
    anchored_currencies: list[str] = []
    anchored_any_metric = False
    per_metric_currency: dict[str, str | None] = {}

    for amount_field, companions in _METRIC_COMPANION_FIELDS.items():
        amount = field_values.get(amount_field)
        if amount is None:
            continue
        anchored_any_metric = True
        source_keys = _anchor_source_keys(field_observations, amount_field, amount)
        for companion in companions:
            field_values[companion] = _companion_from_sources(
                field_observations, companion, source_keys
            )
        currency = _companion_from_sources(
            field_observations, "financials_currency", source_keys
        )
        # Kept, not just counted. This currency belongs to THIS metric's own amount --
        # the metric-row policy's first rule -- and the unanimity collapse below exists
        # only because transaction_record has one shared column to write into. A
        # normalized row has its own, so returning these lets each keep the currency its
        # source actually stated, including when the two disagree and the shared column
        # is therefore null.
        per_metric_currency[amount_field] = currency
        if currency:
            anchored_currencies.append(currency)

    if not anchored_any_metric:
        return per_metric_currency

    distinct = set(anchored_currencies)
    field_values["financials_currency"] = next(iter(distinct)) if len(distinct) == 1 else None
    return per_metric_currency


def _resolve_balance_sheet_inputs(
    field_values: dict,
    field_observations: dict[str, list[dict]],
    existing: Any,
) -> dict:
    """Resolve the balance-sheet inputs with each qualifier anchored to its own source.

    `total_debt` and `Cash_ST` are separate facts that may arrive from different
    sources, so each takes its currency and its as-of date from the source that
    supplied *it*. `balance_sheet_as_of_date` is one column serving both, so it is
    resolved twice — once per anchor — rather than read once and shared, which is
    exactly the cross-source borrowing this must prevent.

    Extracted values take precedence over the preserved manual columns. Manual entry
    was the interim mechanism and carries no qualifiers of its own beyond what a
    researcher fills in; an extracted figure arrives with its currency and as-of date
    attached, and must be able to update on re-aggregation rather than be pinned by
    the value it wrote last time.

    The persisted `balance_sheet_as_of_date` describes the figures actually stored:
    the shared date when both components agree, the single component's date when only
    one is present, and null when two present components disagree — in which case the
    derived net debt is refused anyway.
    """
    def _extracted_or_manual(field: str):
        value = field_values.get(field)
        if value is not None:
            return value
        return existing[field] if existing is not None else None

    total_debt = _extracted_or_manual("total_debt")
    cash_st = _extracted_or_manual("cash_st")

    total_debt_keys = _anchor_source_keys(field_observations, "total_debt", total_debt)
    cash_st_keys = _anchor_source_keys(field_observations, "cash_st", cash_st)

    total_debt_currency = _companion_from_sources(
        field_observations, "total_debt_currency", total_debt_keys
    ) or (existing["total_debt_currency"] if existing is not None else None)
    cash_st_currency = _companion_from_sources(
        field_observations, "cash_st_currency", cash_st_keys
    ) or (existing["cash_st_currency"] if existing is not None else None)

    total_debt_as_of = _companion_from_sources(
        field_observations, "balance_sheet_as_of_date", total_debt_keys
    )
    cash_st_as_of = _companion_from_sources(
        field_observations, "balance_sheet_as_of_date", cash_st_keys
    )
    manual_as_of = existing["balance_sheet_as_of_date"] if existing is not None else None
    total_debt_as_of = total_debt_as_of or (manual_as_of if total_debt is not None else None)
    cash_st_as_of = cash_st_as_of or (manual_as_of if cash_st is not None else None)

    if total_debt is not None and cash_st is not None:
        stored_as_of = total_debt_as_of if total_debt_as_of == cash_st_as_of else None
    else:
        stored_as_of = total_debt_as_of if total_debt is not None else cash_st_as_of

    # Balance-sheet amounts are point-in-time by definition, so the period type is
    # derived here rather than extracted — a constant the model never writes is a
    # constant the model cannot mislabel as LTM/TTM/NTM. Null when no balance-sheet
    # amount is present, so the marker never implies data that is not there.
    has_balance_sheet_amount = total_debt is not None or cash_st is not None
    period_type = BALANCE_SHEET_PERIOD_TYPE if has_balance_sheet_amount else None

    return {
        "total_debt": total_debt,
        "total_debt_currency": total_debt_currency,
        "total_debt_as_of": total_debt_as_of,
        "cash_st": cash_st,
        "cash_st_currency": cash_st_currency,
        "cash_st_as_of": cash_st_as_of,
        "balance_sheet_as_of_date": stored_as_of,
        "balance_sheet_period_type": period_type,
    }


def _derive_enterprise_value(
    value_amount: float | None,
    value_type: str | None,
    equity_value: float | None,
    net_debt: float | None,
) -> tuple[float | None, str | None]:
    """Legacy compatibility wrapper; canonical code should use
    _derive_implied_enterprise_value. The old stake-level equity + net debt path
    is intentionally disabled.
    """
    if value_type == "ENTERPRISE_VALUE" and value_amount and value_amount > 0:
        return float(value_amount), "STATED"
    return None, None


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
    # A human correction supersedes machine extraction for the same field.
    #
    # Without this, a remediation and the stale fact it corrects share a source and
    # therefore a tier, so the tier-based selection below treats them as a disagreement
    # between peers — resolved by confidence, or by asking the LLM. Both are wrong: the
    # correction is not one more opinion, it is the answer. Restricting to remediations
    # when any exist keeps the rest of the selection untouched for the ordinary case.
    remediated = [
        obs for obs in observations
        if obs.get("observation_source_stage") == MANUAL_REMEDIATION_STAGE
        and obs.get("value") is not None
    ]
    if remediated:
        # Latest correction wins if a field is remediated more than once. Observation
        # ids are monotonic, so the highest is the most recent.
        return max(remediated, key=lambda o: o.get("observation_id") or 0)["value"], False, []

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
                # Provenance is caller-owned. The model is never told which prompt version
                # ran -- no user template supplies it -- so its answer could only come from
                # a worked example, which is how this column came to record aggregation:0.4
                # while the prompt was at 0.5.
                _FULL_VERSION,
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
    # defaultdict, because the multiple.* keys are composed from the data rather than
    # declared in _FIELDS -- a plain dict would KeyError on the first one seen.
    observations: dict[str, list[dict]] = defaultdict(list)
    for field_name, _ in _FIELDS:
        observations[field_name] = []
    return {
        "field_observations": observations,
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
               se.asset_type,
               se.announced_date, se.closed_date, se.signing_date,
               se.value_amount, se.value_currency, se.value_type, se.per_share_price, se.pct_acquired,
               se.stake_transition_type, se.offer_mechanism,
               se.is_platform_investment, se.is_secondary_buyout, se.is_merger_of_equals,
               se.is_going_private_outcome,
               se.sponsor_transaction_role,
               se.target_revenue, se.target_revenue_period_type, se.target_revenue_period_end,
               se.target_ebitda, se.target_ebitda_period_type, se.target_ebitda_period_end,
               se.financials_currency,
               se.consideration_components,
               se.hostile, se.competing_bid,
               se.deal_attitude, se.approach_type,
               se.regulatory_approvals_required,
               se.has_go_shop, se.go_shop_period_days,
               se.target_fee_amount, se.target_fee_percentage,
               se.acquirer_fee_amount, se.acquirer_fee_percentage,
               se.model_confidence,
               -- V2 event/date/financials fields (were dropped: loader was stale vs _FIELDS)
               se.v2_event_type, se.event_history_type, se.recap_type, se.acquirer_type_v2,
               se.combination_structure,
               se.announced_date_precision, se.closed_date_precision, se.rumor_date,
               se.target_revenue_period_type_v2, se.target_ebitda_period_type_v2,
               se.financials_disclosure_status,
               -- Tier-3 wiring (2026-08-11): _v2 fields were written to transaction_record
               -- as perpetual NULL (absent from _FIELDS); signing_date_precision was never
               -- read. Now read like acquirer_type_v2 — transaction_record keeps both the
               -- legacy and _v2 column; downstream coalesces.
               se.target_type_v2, se.spin_split_type_v2, se.signing_date_precision,
               -- Funding fields (Stage 4b) — required so funding deal value/round data propagates
               se.round_label, se.round_size, se.pre_money_valuation, se.post_money_valuation,
               se.valuation_currency, se.round_currency, se.facility_size, se.total_raised_to_date,
               se.is_extension_round, se.round_price_direction, se.is_bridge_round,
               se.use_of_proceeds, se.has_board_seat, se.board_seat_notes,
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
                source_key = (member["extraction_id"], member["source_raw_id"])
                bundle["field_observations"][field_name].append({
                    "observation_id": i + 1,
                    "source_key": source_key,
                    "source_type": member["source_type"],
                    "tier": member["source_tier"],
                    "published_date": member["published_date"] or "",
                    "value": raw_val,
                    "model_confidence": member["model_confidence"] or "MEDIUM",
                    "source_text_excerpt": (member["clean_text"] or "")[:200],
                })
        clusters[cluster_id] = bundle
    return clusters


def _load_sponsor_participant_context(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "transaction_participant" not in tables:
        return {}
    rows = conn.execute(
        """
        SELECT
            transaction_id,
            MAX(CASE WHEN side='BUYER' AND participant_role='BUYER_SPONSOR' THEN 1 ELSE 0 END) AS has_buyer_sponsor,
            MAX(CASE WHEN side='SELLER' AND participant_role='SELLER_SPONSOR' THEN 1 ELSE 0 END) AS has_seller_sponsor
        FROM transaction_participant
        WHERE is_current = 1
          AND participant_role IN ('BUYER_SPONSOR', 'SELLER_SPONSOR')
        GROUP BY transaction_id
        """
    ).fetchall()
    return {
        row["transaction_id"]: {
            "_has_buyer_sponsor_party": int(row["has_buyer_sponsor"] or 0),
            "_has_seller_sponsor_party": int(row["has_seller_sponsor"] or 0),
        }
        for row in rows
    }


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
            tfo.observation_source_stage,
            tfo.staging_extraction_id,
            tfo.source_raw_id,
            tfo.observation_fact_key,
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
              'FUNDING_HC_EXTRACT',
              'LC_EXTRACT',
              'BACKFILL',
              -- A human correction of an extraction-layer fact. Omitting it meant a
              -- remediation could be written to the ledger and then silently ignored
              -- by the very derivation that exists to consume it. The allowlist is
              -- still an allowlist: it admits producers of Stage 9 INPUTS and excludes
              -- downstream stages whose observations Stage 9 does not own.
              'MANUAL_REMEDIATION'
          )
        ORDER BY tfo.transaction_id, tfo.staging_extraction_id, tfo.observation_id
        """
    ).fetchall()

    clusters: dict[str, dict] = {}
    seen_sources: dict[str, set[tuple[int | None, int | None]]] = defaultdict(set)
    for row in rows:
        cluster_id = row["transaction_id"]
        bundle = clusters.setdefault(cluster_id, _empty_cluster())
        source_identity = (row["staging_extraction_id"], row["source_raw_id"])
        source_key = (row["staging_extraction_id"], row["source_raw_id"], row["observation_fact_key"])
        if source_identity not in seen_sources[cluster_id]:
            seen_sources[cluster_id].add(source_identity)
            bundle["sources"].append({
                "source_raw_id": row["source_raw_id"],
                "source_tier": row["source_tier"],
                "staging_extraction_id": row["staging_extraction_id"],
            })

        field_name = row["field_name"]
        # A multiple's canonical fact key is composed from its own dimensions
        # (multiple.{type}.{basis}.{end}), so it cannot be declared in _FIELDS. It is
        # always numeric. The `reported_multiple` preservation rows are NOT admitted
        # here and never will be -- they are the record of what each source said, not
        # candidates to reconcile between.
        if field_name.startswith(_MULTIPLE_FIELD_PREFIX):
            field_type = "number"
        elif field_name not in _FIELD_TYPE:
            continue
        else:
            field_type = _FIELD_TYPE[field_name]
        value = _value_from_observation(row, field_type)
        if field_name in _CONTEXT_FIELDS and bundle["deal_context"].get(field_name) is None:
            bundle["deal_context"][field_name] = value

        bundle["field_observations"][field_name].append({
            "observation_id": row["observation_id"],
            "source_key": source_key,
            "observation_source_stage": row["observation_source_stage"],
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

# ---------------------------------------------------------------------------
# transaction_record write ownership
# ---------------------------------------------------------------------------
#
# The columns Stage 9 owns. Everything else on transaction_record belongs to a
# later stage (Stage 10 sec_documents, Stage 11 agreement_extract) or to the row
# itself (`notes`, `created_at`), and must survive re-aggregation untouched.
#
# This list is the single source of truth for the write: the placeholder count and
# the conflict-update clause are both derived from it, so they cannot drift apart.
# The params tuple at the call site must stay in this order.
_STAGE9_OWNED_COLUMNS: tuple[str, ...] = (
    "transaction_id",
    "deal_type",
    "v2_event_type",
    "event_history_type",
    "spin_split_type",
    "spin_split_type_v2",
    "distribution_mechanism",
    "recap_type",
    "combination_structure",
    "target_type",
    "target_type_v2",
    "event_type",
    "transaction_status",
    "target_status",
    "target_name",
    "target_domain",
    "target_ticker",
    "acquirer_name",
    "acquirer_domain",
    "acquirer_ticker",
    "acquirer_type",
    "acquirer_type_v2",
    "parent_seller_name",
    "parent_seller_ticker",
    "target_description",
    "asset_type",
    "acquirer_description",
    "acquirer_sponsor_name",
    "parent_seller_description",
    "announced_date",
    "announced_date_precision",
    "closed_date",
    "closed_date_precision",
    "signing_date",
    "signing_date_precision",
    "rumor_date",
    "value_amount",
    "value_currency",
    "value_type",
    "per_share_price",
    "pct_acquired",
    "stake_transition_type",
    "offer_mechanism",
    "target_revenue",
    "target_revenue_period_type",
    "target_revenue_period_type_v2",
    "target_revenue_period_end",
    "target_ebitda",
    "target_ebitda_period_type",
    "target_ebitda_period_type_v2",
    "target_ebitda_period_end",
    "financials_currency",
    "financials_disclosure_status",
    "transaction_terms_disclosure_status",
    "ev_to_revenue_ltm",
    "ev_to_revenue_ntm",
    "ev_to_ebitda_ltm",
    "ev_to_ebitda_ntm",
    "multiple_quality",
    "consideration_type",
    "consideration_components",
    "hostile",
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
    "is_take_private",
    "is_minority",
    "sponsor_transaction_role",
    "is_secondary_buyout",
    "is_merger_of_equals",
    "is_going_private_outcome",
    "has_earnout",
    "has_cvr",
    "round_label",
    "round",
    "vc_stage",
    "round_price_direction",
    "round_size",
    "pre_money_valuation",
    "post_money_valuation",
    "valuation_currency",
    "round_currency",
    "facility_size",
    "total_raised_to_date",
    "is_extension_round",
    "is_bridge_round",
    "use_of_proceeds",
    "has_board_seat",
    "board_seat_notes",
    "is_current",
    "aggregation_version",
    "updated_at",
    "net_debt",
    "equity_value",
    "equity_value_basis",
    "implied_equity_value",
    "implied_enterprise_value",
    "implied_enterprise_value_basis",
    "enterprise_value",
    "enterprise_value_basis",
    "investment_amount",
    "transaction_size",
    "transaction_size_basis",
    "deal_value_currency",
    "total_debt",
    "cash_st",
    "transaction_value",
    "transaction_value_basis",
    "pct_acquired_source",
    "total_debt_currency",
    "cash_st_currency",
    "balance_sheet_as_of_date",
    "balance_sheet_period_type",
    "net_debt_currency",
)

# Upsert rather than INSERT OR REPLACE. REPLACE deletes the row and inserts a new
# one, so every column absent from the list above was silently reset to NULL on each
# re-aggregation — including Stage 10/11 output.
#
# The update assigns `excluded.<col>` directly, NOT COALESCE. That is deliberate: a
# Stage-9-owned field whose newly aggregated evidence says NULL must actually become
# NULL. COALESCE would turn each canonical field into a high-water mark that could
# never be retracted, which is a worse defect than the one being fixed.
_TRANSACTION_RECORD_UPSERT_SQL = (
    "INSERT INTO transaction_record (\n    "
    + ",\n    ".join(_STAGE9_OWNED_COLUMNS)
    + "\n) VALUES (\n    "
    + ",".join("?" * len(_STAGE9_OWNED_COLUMNS))
    + "\n) ON CONFLICT(transaction_id) DO UPDATE SET\n    "
    + ",\n    ".join(
        f"{column}=excluded.{column}"
        for column in _STAGE9_OWNED_COLUMNS
        if column != "transaction_id"
    )
)


def run(conn: sqlite3.Connection, cfg: Config, run_id: str) -> dict:
    """Aggregate clustered extractions into canonical transaction records.

    Returns
    -------
    dict
        Keys: clusters_total, transactions_upserted, conflicts_resolved_by_llm,
              flagged_for_review, failed, transactions_created (run.py alias)
    """
    log = get_logger(_PROMPT_NAME, run_id, level=cfg.log_level)
    read_source = getattr(cfg, "aggregation_read_source", DEFAULT_AGGREGATION_READ_SOURCE)

    prompt = load_prompt_file(_PROMPT_NAME)
    register_prompt_version(conn, _PROMPT_NAME, _VERSION, prompt["file_hash"])
    log.info("Loaded %s  hash=%s read_source=%s", _FULL_VERSION, prompt["file_hash"][:12], read_source)

    clusters = _load_aggregation_input(conn, read_source)
    sponsor_participant_context = _load_sponsor_participant_context(conn)

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

            # Declared scalar fields, then whatever multiple keys this cluster's sources
            # actually produced. Both go through the SAME _pick_value / _call_agg_prompt /
            # _log_conflict path -- there is no multiples-specific reconciliation.
            multiple_keys = sorted(
                k for k in bundle["field_observations"]
                if k.startswith(_MULTIPLE_FIELD_PREFIX)
                and bundle["field_observations"][k]
            )
            flagged_multiple_keys: set[str] = set()
            for field_name, field_type in (
                list(_FIELDS) + [(k, "number") for k in multiple_keys]
            ):
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
                    # A conflicted multiple key yields NO canonical row. Product ruling:
                    # transaction_multiple carries resolved canonical facts only, and an
                    # unresolved key stays in the observation and conflict machinery
                    # rather than becoming a canonical value a reader cannot trust. The
                    # scalar fields keep their existing behaviour untouched.
                    if field_name.startswith(_MULTIPLE_FIELD_PREFIX) and (
                        result is None or result.get("flagged_for_review")
                    ):
                        flagged_multiple_keys.add(field_name)

                field_values[field_name] = chosen

            conflicts_llm += llm_count_this_cluster
            flagged += flag_count_this_cluster

            # Re-serialize any json-type fields that the LLM returned as Python objects
            for _fname, _ftype in _FIELDS:
                if _ftype == "json" and isinstance(field_values.get(_fname), (list, dict)):
                    field_values[_fname] = json.dumps(field_values[_fname])

            # §2.10 items 1-2 — re-anchor each financial qualifier to the source of
            # its own amount, before anything reads or persists them.
            metric_currencies = _anchor_metric_qualifiers(
                field_values, bundle["field_observations"]
            )

            # Derive additional fields
            field_values.update(sponsor_participant_context.get(cluster_id, {}))
            ctype = _derive_consideration_type(field_values.get("consideration_components"))
            if ctype is None:
                # Funding path only. No consideration_components vocabulary applies to
                # a funding round (SAFE / convertible_note / warrant have no component-
                # form equivalent), so the derivation above never fires for one. Fall
                # back to Funding HC's own collected instrument classification -- never
                # read on the M&A path, where the field is not authored at all (0.37)
                # and this key is therefore always absent from field_values there.
                ctype = field_values.get("consideration_type")
            derived = _derive_flags(field_values)
            txn_status = _derive_transaction_status(
                field_values.get("event_history_type"), field_values.get("closed_date")
            )
            # V3 §T14: two deterministic steps, not one substring test.
            # round_label (verbatim) -> canonical round -> broad vc_stage.
            # Both are DERIVED here, not extracted and not observed -- the same shape the
            # V2 round_stage_category used, so neither belongs in FUNDING_FIELDS.
            canonical_round = _normalize_round(field_values.get("round_label"))
            vc_stage = _derive_vc_stage(canonical_round)

            # Check for existing transaction_record to determine version and to
            # preserve the manual net_debt input across re-aggregation.
            existing = conn.execute(
                "SELECT aggregation_version, net_debt, total_debt, cash_st, "
                "net_debt_currency, total_debt_currency, cash_st_currency, "
                "balance_sheet_as_of_date FROM transaction_record WHERE transaction_id=?",
                (cluster_id,),
            ).fetchone()
            agg_version = (existing["aggregation_version"] + 1) if existing else 1

            # Derive valuations (the deterministic job — LLM captured primitives only).
            # net_debt, total_debt, and cash_st are manual collection inputs in
            # the interim; keep any stored value. Their currency and as-of anchors
            # are manual alongside them and preserved the same way.
            net_debt_reported = existing["net_debt"] if existing else None
            net_debt_currency = existing["net_debt_currency"] if existing else None
            balance_sheet = _resolve_balance_sheet_inputs(
                field_values, bundle["field_observations"], existing
            )
            total_debt = balance_sheet["total_debt"]
            total_debt_currency = balance_sheet["total_debt_currency"]
            cash_st = balance_sheet["cash_st"]
            cash_st_currency = balance_sheet["cash_st_currency"]
            balance_sheet_as_of_date = balance_sheet["balance_sheet_as_of_date"]
            balance_sheet_period_type = balance_sheet["balance_sheet_period_type"]
            net_debt, net_debt_resolved_currency, _net_debt_as_of, net_debt_basis = _derive_net_debt(
                net_debt_reported,
                net_debt_currency,
                total_debt,
                total_debt_currency,
                balance_sheet["total_debt_as_of"],
                cash_st,
                cash_st_currency,
                balance_sheet["cash_st_as_of"],
            )
            pct_resolved, pct_acquired_source = _resolve_pct_acquired(field_values)
            # The control question, answered without a percentage. This is exactly the
            # condition that used to produce the assumed 100 -- a control event type
            # with no minority signal -- so the transaction-value branch selection is
            # unchanged for every row; only the invented number is gone.
            is_control_deal = (
                _event_type(field_values) in _CONTROL_DEFAULT_TYPES
                and not derived["is_minority"]
            )
            # Each canonical value field consumes the best observation of its own
            # semantic type; none of them may depend on which type happens to win
            # the single legacy value_amount/value_type pair. Without this, a
            # cluster stating both an equity figure and a whole-company EV loses
            # whichever type loses that collapse.
            typed_equity_value_amount = _pick_value_amount_for_type(
                bundle["field_observations"], "EQUITY_VALUE"
            )
            equity_value_fields = dict(field_values)
            if typed_equity_value_amount is not None:
                equity_value_fields["value_amount"] = typed_equity_value_amount
                equity_value_fields["value_type"] = "EQUITY_VALUE"
            equity_value, equity_value_basis = _derive_equity_value(equity_value_fields)
            implied_equity_value = _derive_implied_equity(equity_value, pct_resolved)
            investment_amount = _derive_investment_amount(field_values)
            typed_transaction_value_amount = _pick_value_amount_for_type(
                bundle["field_observations"], "TRANSACTION_VALUE"
            )
            typed_enterprise_value_amount = _pick_value_amount_for_type(
                bundle["field_observations"], "ENTERPRISE_VALUE"
            )
            transaction_value_fields = dict(field_values)
            if typed_transaction_value_amount is not None:
                transaction_value_fields["value_amount"] = typed_transaction_value_amount
                transaction_value_fields["value_type"] = "TRANSACTION_VALUE"
            # Currency companion for the derived value fields; null on a genuine
            # currency mismatch (§4.7 — the null is itself the queryable signal).
            # Resolved before the debt-inclusive derivations, which need it to check
            # the deal currency against the balance-sheet currency.
            deal_value_currency = _derive_deal_value_currency(field_values, log, cluster_id)
            transaction_value, transaction_value_basis = _derive_transaction_value(
                transaction_value_fields, equity_value, total_debt,
                is_control=is_control_deal,
                is_below_control=bool(derived["is_minority"]),
                equity_currency=deal_value_currency,
                total_debt_currency=total_debt_currency,
            )
            transaction_size, transaction_size_basis = _derive_transaction_size(
                field_values, transaction_value
            )
            implied_enterprise_value_amount = (
                typed_enterprise_value_amount
                if typed_enterprise_value_amount is not None
                else field_values.get("value_amount")
            )
            implied_enterprise_value_type = (
                "ENTERPRISE_VALUE"
                if typed_enterprise_value_amount is not None
                else field_values.get("value_type")
            )
            implied_enterprise_value, implied_enterprise_value_basis = _derive_implied_enterprise_value(
                implied_enterprise_value_amount,
                implied_enterprise_value_type,
                implied_equity_value,
                net_debt,
                implied_equity_currency=deal_value_currency,
                net_debt_currency=net_debt_resolved_currency,
                net_debt_basis=net_debt_basis,
            )
            if (
                implied_equity_value is not None
                and net_debt is not None
                and implied_enterprise_value is None
            ):
                log.warning(
                    "cluster=%s implied EV not derived — deal currency %r vs net-debt "
                    "currency %r must both be known and equal (§2.10 item 1; no FX date "
                    "available)",
                    cluster_id, deal_value_currency, net_debt_resolved_currency,
                )
            # Legacy compatibility columns mirror the canonical Tier-2 field until
            # downstream readers are moved.
            enterprise_value = implied_enterprise_value
            enterprise_value_basis = implied_enterprise_value_basis
            multiples = _compute_multiples(
                implied_enterprise_value=implied_enterprise_value,
                value_currency=field_values.get("value_currency"),
                target_revenue=field_values.get("target_revenue"),
                target_revenue_period_type=(
                    field_values.get("target_revenue_period_type_v2")
                    or field_values.get("target_revenue_period_type")
                ),
                target_revenue_period_end=field_values.get("target_revenue_period_end"),
                target_ebitda=field_values.get("target_ebitda"),
                target_ebitda_period_type=(
                    field_values.get("target_ebitda_period_type_v2")
                    or field_values.get("target_ebitda_period_type")
                ),
                target_ebitda_period_end=field_values.get("target_ebitda_period_end"),
                financials_currency=field_values.get("financials_currency"),
                log=log,
                cluster_id=cluster_id,
                v2_event_type=field_values.get("v2_event_type") or field_values.get("deal_type"),
                announced_date=field_values.get("announced_date"),
            )

            # Upsert transaction_record
            conn.execute(
                _TRANSACTION_RECORD_UPSERT_SQL,
                (
                    cluster_id,
                    field_values.get("deal_type") or field_values.get("v2_event_type"),
                    field_values.get("v2_event_type"),
                    field_values.get("event_history_type"),
                    field_values.get("spin_split_type"),
                    field_values.get("spin_split_type_v2"),
                    field_values.get("distribution_mechanism"),
                    field_values.get("recap_type"),
                    field_values.get("combination_structure"),
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
                    field_values.get("asset_type"),
                    field_values.get("acquirer_description"),
                    field_values.get("acquirer_sponsor_name"),
                    field_values.get("parent_seller_description"),
                    field_values.get("announced_date"),
                    field_values.get("announced_date_precision"),
                    field_values.get("closed_date"),
                    field_values.get("closed_date_precision"),
                    field_values.get("signing_date"),
                    field_values.get("signing_date_precision"),
                    field_values.get("rumor_date"),
                    field_values.get("value_amount"),
                    field_values.get("value_currency"),
                    field_values.get("value_type"),
                    field_values.get("per_share_price"),
                    pct_resolved,  # §2.6: stated only. Never assumed.
                    field_values.get("stake_transition_type"),
                    field_values.get("offer_mechanism"),
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
                    field_values.get("transaction_terms_disclosure_status"),
                    multiples["ev_to_revenue_ltm"],
                    multiples["ev_to_revenue_ntm"],
                    multiples["ev_to_ebitda_ltm"],
                    multiples["ev_to_ebitda_ntm"],
                    multiples["multiple_quality"],
                    ctype,
                    field_values.get("consideration_components"),
                    field_values.get("hostile"),
                    field_values.get("deal_attitude"),
                    field_values.get("approach_type"),
                    field_values.get("competing_bid"),
                    field_values.get("regulatory_approvals_required"),
                    field_values.get("has_go_shop"),
                    field_values.get("go_shop_period_days"),
                    field_values.get("target_fee_amount"),
                    field_values.get("target_fee_percentage"),
                    field_values.get("acquirer_fee_amount"),
                    field_values.get("acquirer_fee_percentage"),
                    derived["is_take_private"],
                    derived["is_minority"],
                    field_values.get("sponsor_transaction_role"),
                    derived["is_secondary_buyout"],
                    derived["is_merger_of_equals"],
                    field_values.get("is_going_private_outcome"),
                    _derive_has_earnout(field_values.get("consideration_components")),
                    _derive_has_cvr(field_values.get("consideration_components")),
                    # Funding fields
                    field_values.get("round_label"),
                    canonical_round,
                    vc_stage,
                    field_values.get("round_price_direction"),
                    field_values.get("round_size"),
                    field_values.get("pre_money_valuation"),
                    field_values.get("post_money_valuation"),
                    field_values.get("valuation_currency"),
                    field_values.get("round_currency"),
                    field_values.get("facility_size"),
                    field_values.get("total_raised_to_date"),
                    field_values.get("is_extension_round"),
                    field_values.get("is_bridge_round"),
                    field_values.get("use_of_proceeds"),
                    field_values.get("has_board_seat"),
                    field_values.get("board_seat_notes"),
                    1,
                    agg_version,
                    now,
                    net_debt,
                    equity_value,
                    equity_value_basis,
                    implied_equity_value,
                    implied_enterprise_value,
                    implied_enterprise_value_basis,
                    enterprise_value,
                    enterprise_value_basis,
                    investment_amount,
                    transaction_size,
                    transaction_size_basis,
                    deal_value_currency,
                    total_debt,
                    cash_st,
                    transaction_value,
                    transaction_value_basis,
                    pct_acquired_source,
                    total_debt_currency,
                    cash_st_currency,
                    balance_sheet_as_of_date,
                    balance_sheet_period_type,
                    # Read from `existing` above and written back here: Stage 9 uses
                    # INSERT OR REPLACE, so a manual input that is read but not
                    # re-persisted survives exactly one pass and is then nulled.
                    net_debt_currency,
                ),
            )

            # Flush deferred conflict logs (must follow transaction_record INSERT for FK)
            for f_name, c_obs, c_result in pending_conflicts:
                _log_conflict(conn, cluster_id, f_name, c_obs, c_result)

            # Resolved financial metrics become canonical rows. Must follow the
            # transaction_record INSERT above for the FK.
            _write_financial_metrics(
                conn, cluster_id, field_values, metric_currencies, bundle, log,
            )

            # Resolved multiples become canonical rows now that a transaction_id
            # exists. Must follow the transaction_record INSERT above for the FK.
            _write_as_reported_multiples(
                conn, cluster_id, field_values, flagged_multiple_keys,
                bundle["field_observations"], log,
            )

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
