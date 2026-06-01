"""Shared field-priority constants for aggregation logic.

Imported by stages/agreement_extract.py and stages/aggregate.py.
Both stages reference the same priority tables to prevent drift.
"""

# Source-tier ordering. Index 0 = highest priority.
TIER_ORDER: tuple[str, ...] = ("T1", "T2", "T3")

# Per-field filing-type priority for fields where agreement text is authoritative.
# Index 0 = highest priority. Fields absent from this dict default to
# most-recent-filing-date wins within Stage 11.
FIELD_FILING_TYPE_PRIORITY: dict[str, list[str]] = {
    # Termination / go-shop: legal source-of-truth is original agreement
    "target_fee_amount":                ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "target_fee_percentage":            ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "acquirer_fee_amount":              ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "acquirer_fee_percentage":          ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A", "S4"],
    "has_go_shop":                      ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A"],
    "go_shop_period_days":              ["8K_EXHIBIT_21", "DEFM14A", "DEFA14A"],
    # Closing conditions: agreement is legal source
    "has_mac_clause":                   ["8K_EXHIBIT_21", "DEFM14A", "S4"],
    "requires_target_shareholder_vote": ["8K_EXHIBIT_21", "DEFM14A"],
    "target_vote_threshold":            ["8K_EXHIBIT_21", "DEFM14A"],
    # Merger structure: agreement establishes
    "merger_structure":                 ["8K_EXHIBIT_21", "DEFM14A", "S4"],
    # per_share_price / consideration_components: most-recent wins (DEFA14A captures bumps)
    # no explicit rule = default behaviour
}


def filing_type_rank(field_name: str, filing_type: str | None) -> int:
    """Filing-type rank for a given field. Lower is higher priority.

    Returns the index of filing_type in the field's priority list.
    If field_name has no rule, or filing_type is not in the rule,
    returns len(rule) -- lowest priority, sorts to the end.
    """
    rule = FIELD_FILING_TYPE_PRIORITY.get(field_name, [])
    if filing_type and filing_type in rule:
        return rule.index(filing_type)
    return len(rule)
