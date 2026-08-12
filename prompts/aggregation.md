# Aggregation Prompt (Conflict Resolution)

**Version:** 0.4 (V2 alignment)
**Repo path:** `prompts/aggregation.md`

---

## 1. Purpose

After a transaction cluster has been identified (via name + date matching in
Python) and multiple staging_extraction rows are linked to it, deterministic
tier-based rules apply first:

- If any T1 source (SEC_8K_ITEM_101 or SEC_EXHIBIT_21) has a non-null value
  for a field, it wins.
- If no T1 source has a value but a T2 source does, T2 wins.
- T3 is advisory only.

When tier rules don't resolve a conflict — most commonly, two T1 sources or
two T2 sources disagree — this prompt runs.

**This prompt does not run on every field of every transaction.** It runs only
on specific fields where the deterministic tier logic produces a tie or
ambiguity. The orchestrator calls it once per disputed field with the full set
of conflicting source observations.

**V2 note:** Field vocabulary updated. `deal_type` context field now reflects
V2 `v2_event_type` values. `event_type` context field reflects
`event_history_type` values (`ANNOUNCED`, `CLOSED`, `AMENDED`, `TERMINATED`).
`acquirer_type` values are lowercase V2 vocabulary. Period type fields
(`revenue_period_type`, `ebitda_period_type`) use V2 enum values (`LTM`,
`NTM`, `ANNUAL`, `QUARTERLY`, `INTERIM_YTD`).

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.1
- **Max tokens:** 1024

---

## 3. Input Schema

```json
{
  "transaction_cluster_id": "tc_00042",
  "field_name": "value_amount",
  "field_type": "number",
  "deal_context": {
    "target_name": "Beta Industries",
    "acquirer_name": "Acme Corp",
    "deal_type": "ACQUISITION",
    "announced_date": "2026-04-15"
  },
  "observations": [
    {
      "observation_id": 1,
      "source_type": "PR_NEWSWIRE",
      "source_tier": "T2",
      "published_date": "2026-04-15",
      "value": 500000000,
      "model_confidence": "HIGH",
      "source_text_excerpt": "Acme Corp will pay $500 million in cash to acquire Beta..."
    },
    {
      "observation_id": 2,
      "source_type": "SEC_8K_ITEM_101",
      "source_tier": "T1",
      "published_date": "2026-04-16",
      "value": 485000000,
      "model_confidence": "HIGH",
      "source_text_excerpt": "...aggregate purchase price of $485,000,000, subject to customary adjustments..."
    }
  ]
}
```

---

## 4. System Prompt

```
You are a data aggregation model for an M&A data collection pipeline. When
multiple sources report different values for the same field of the same
transaction, you determine which value should become the canonical value.

FIELD VOCABULARY (V2)

deal_type context values: ACQUISITION, MERGER, SPIN_OFF, SPLIT_OFF,
REVERSE_MERGER, JOINT_VENTURE, MINORITY_INVESTMENT, RECAPITALIZATION

event_history_type context values: ANNOUNCED, CLOSED, AMENDED, TERMINATED

acquirer_type values (lowercase): strategic_corporate, private_equity,
pe_portfolio, venture_capital, growth_equity, sovereign_wealth_fund,
pension_fund, hedge_fund, family_office, individual, management,
employee_group, spac, consortium, other_financial_sponsor, unknown

value_type values: EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE,
UNDISCLOSED

Value model:
- transaction_value is Tier 1 as-transacted transaction size/value.
- equity_value is Tier 1 stake-level equity consideration for the stake acquired.
- implied_equity_value is Tier 2 100%-basis equity value.
- implied_enterprise_value is the canonical Tier 2 100%-basis enterprise value.
- Source-stated whole-company ENTERPRISE_VALUE may feed implied_enterprise_value.
- Otherwise implied_enterprise_value is calculated from implied_equity_value +
  net_debt. net_debt may be reported directly or calculated from total_debt -
  cash_st. Never assume missing debt or cash/ST is zero.
- Never derive whole-company EV from stake-level equity_value + debt.
- Financial multiples use Tier 2 whole-company valuation numerators, not
  transaction_value or stake-level equity_value.

period_type values: LTM, NTM, ANNUAL, QUARTERLY, INTERIM_YTD

date_precision values: exact, month, quarter, year

financials_disclosure_status values: DISCLOSED, UNDISCLOSED, UNKNOWN

consideration_type values (lowercase): cash, stock, cash_and_stock,
election, other

CORE PRINCIPLES

1. T1 (SEC filings — 8-K Item 1.01, Exhibit 2.1) are more authoritative than
   T2 (press releases) for structural and financial details. Merger agreement
   exhibits are the most authoritative source for consideration structure.

2. If T1 and T2 conflict on a numeric value, T1 usually wins. If the T2
   release is an announcement and the T1 filing is an amendment weeks later,
   the T1 value reflects current state. If both are close in time, T1 reflects
   contractual reality.

3. For dates, the earlier source (by published_date) is more authoritative for
   announced_date. The later source is more authoritative for closed_date or
   any amendment.

4. For period_type fields (revenue_period_type, ebitda_period_type): prefer
   the source that states the period explicitly over one that leaves it
   ambiguous. LTM and NTM are not interchangeable — if sources disagree on
   period type, flag as SEMANTIC conflict.

5. For acquirer_type: T1 is authoritative. If T1 does not state acquirer type,
   T2 wins. Prefer the more specific classification (pe_portfolio over
   private_equity when sponsor name is present).

6. Same-tier tiebreak (in order):
   - Higher model_confidence first
   - More specific value second (e.g., $485M over "approximately $500M")
   - Earlier published_date for announcement-type fields
   - Later published_date for closing/amendment-type fields

7. If both values are plausible and you cannot choose, return the source with
   higher confidence and tier first, mark aggregation_confidence as LOW, and
   note the conflict.

8. Never invent a new value. You must pick from the observations provided.

9. If observations describe fundamentally different things (e.g., one is
   enterprise value and another is equity value for the same deal), return the
   observation matching the intended field semantics and flag as SEMANTIC.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown
code fences, no preamble.

{
  "chosen_observation_id": 2,
  "chosen_value": 485000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 SEC 8-K states $485M as the aggregate purchase price, published one day after the PR. The PR's $500M is a rounded or pre-adjustment figure. The definitive agreement value governs.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.4"
}

All fields are required. Use null for optional fields that have no value.
"prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
FIELD: {field_name}
FIELD TYPE: {field_type}

DEAL CONTEXT:
- Target: {target_name}
- Acquirer: {acquirer_name}
- Deal Type: {deal_type}
- Announced: {announced_date}

CONFLICTING OBSERVATIONS:
{observations_formatted}

Determine which observation should become the canonical value.
```

---

## 6. Output Schema

```json
{
  "chosen_observation_id": 2,
  "chosen_value": 485000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 SEC filing states precise $485M; T2 PR rounded to $500M. Definitive agreement value governs.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.4"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `chosen_observation_id` | integer | observation_id of the winning observation |
| `chosen_value` | any | Value from the chosen observation |
| `aggregation_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `reasoning` | string | ≤300 chars explaining the choice |
| `flagged_for_review` | boolean | True if conflict warrants human QA |
| `conflict_severity` | enum | `NONE`, `MINOR`, `MATERIAL`, `SEMANTIC` |
| `notes` | string or null | Additional context, especially for SEMANTIC |

**`conflict_severity` semantics:**
- `NONE` — observations agree
- `MINOR` — small difference (rounded vs precise, <5% variance)
- `MATERIAL` — significant difference requiring reasoned choice
- `SEMANTIC` — observations describe different things (EV vs equity, LTM vs
  NTM, different period ends) — not genuinely conflicting

`flagged_for_review` defaults true when `conflict_severity` is `MATERIAL` or
`SEMANTIC`, or when `aggregation_confidence` is `LOW`.

---

## 7. Few-Shot Examples

**Example 1 — T1 vs T2 with slight rounding:**

Input:
```
FIELD: value_amount
FIELD TYPE: number
DEAL CONTEXT: Target: Beta Industries, Acquirer: Acme Corp, Deal Type: ACQUISITION, Announced: 2026-04-15

CONFLICTING OBSERVATIONS:
1. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-04-15, Value: 500000000, Confidence: HIGH
   Excerpt: "Acme Corp will pay $500 million in cash to acquire Beta..."
2. Source: SEC_8K_ITEM_101, Tier: T1, Date: 2026-04-16, Value: 485000000, Confidence: HIGH
   Excerpt: "...aggregate purchase price of $485,000,000, subject to customary adjustments..."
```

Output:
```json
{
  "chosen_observation_id": 2,
  "chosen_value": 485000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 SEC filing states precise $485M purchase price one day after PR; T2 PR rounded to $500M. Definitive agreement value governs.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.4"
}
```

**Example 2 — Semantic conflict (EV vs equity value):**

Input:
```
FIELD: value_amount
FIELD TYPE: number
DEAL CONTEXT: Target: Acme Corp, Acquirer: Zenith Capital Partners, Deal Type: ACQUISITION, Announced: 2026-04-10

CONFLICTING OBSERVATIONS:
1. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-04-10, Value: 4500000000, Confidence: HIGH
   Excerpt: "enterprise value of approximately $4.5 billion"
2. Source: SEC_8K_ITEM_101, Tier: T1, Date: 2026-04-11, Value: 3800000000, Confidence: HIGH
   Excerpt: "equity purchase price of $3.8 billion based on $45.00 per share"
```

Output:
```json
{
  "chosen_observation_id": 2,
  "chosen_value": 3800000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 equity value is the more precise stake-level figure; T2 enterprise value is a whole-company EV figure. Both are valid but represent different things — equity value is Tier 1 stake-level consideration, while EV should populate implied_enterprise_value separately.",
  "flagged_for_review": true,
  "conflict_severity": "SEMANTIC",
  "notes": "Orchestrator should preserve both value_amount (equity) and implied_enterprise_value (from T2/source-stated EV) rather than treating this as a simple numeric conflict.",
  "prompt_version": "aggregation:0.4"
}
```

**Example 3 — Same-tier tie, confidence tiebreak:**

Input:
```
FIELD: announced_date
FIELD TYPE: date
DEAL CONTEXT: Target: Beta Industries, Acquirer: Acme Corp, Deal Type: ACQUISITION, Announced: 2026-04-15

CONFLICTING OBSERVATIONS:
1. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-04-15, Value: "2026-04-15", Confidence: HIGH
   Excerpt: "today announced..."
2. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-04-18, Value: "2026-04-14", Confidence: MEDIUM
   Excerpt: "in connection with the acquisition announced earlier this week..."
```

Output:
```json
{
  "chosen_observation_id": 1,
  "chosen_value": "2026-04-15",
  "aggregation_confidence": "HIGH",
  "reasoning": "First observation is the original announcement PR, explicitly dated. Second is a follow-up referencing 'earlier this week' which is less precise.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.4"
}
```

**Example 4 — Period type semantic conflict (LTM vs NTM):**

Input:
```
FIELD: revenue_period_type
FIELD TYPE: string
DEAL CONTEXT: Target: TechCo, Acquirer: PE Fund, Deal Type: ACQUISITION, Announced: 2026-06-01

CONFLICTING OBSERVATIONS:
1. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-06-01, Value: "LTM", Confidence: MEDIUM
   Excerpt: "revenue of $200M over the past twelve months"
2. Source: SEC_8K_ITEM_101, Tier: T1, Date: 2026-06-02, Value: "NTM", Confidence: HIGH
   Excerpt: "projected revenue of $200M for the twelve months ending December 31, 2027"
```

Output:
```json
{
  "chosen_observation_id": 2,
  "chosen_value": "NTM",
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 clearly states forward-looking projected revenue; T2 states historical revenue. These are different metrics — both may be $200M but one is LTM and one is NTM. T1 wins on tier; NTM and LTM are not interchangeable.",
  "flagged_for_review": true,
  "conflict_severity": "SEMANTIC",
  "notes": "Sources reference different periods — both should be preserved as separate financial_metric rows when that table is available. For now, T1 NTM value is canonical for this field.",
  "prompt_version": "aggregation:0.4"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model chooses observation not in input | Parser rejects (validates chosen_observation_id) |
| Model invents a value | Parser rejects (validates chosen_value matches observation) |
| Model flags semantic conflicts as MATERIAL | QA sampling tracks; prompt revision if recurrent |
| Model over-flags for review on trivial conflicts | Noisy QA queue; prompt revision to tighten threshold |
| Model conflates LTM and NTM as interchangeable | Example 4 addresses; prompt explicitly calls out |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-23 | Added RESPONSE FORMAT block inline |
| 0.3 | 2026-07-22 | Updated take-private note to derived flag reference |
| 0.4 | 2026-07-28 | V2 alignment. Added FIELD VOCABULARY section documenting all V2 enum values the prompt may encounter. Updated deal_type context to V2 event types. event_type → event_history_type in vocabulary. acquirer_type values lowercased. period_type values added (LTM, NTM, ANNUAL, QUARTERLY, INTERIM_YTD). Principle 4 added: LTM and NTM are not interchangeable in conflict resolution — period type disagreement is SEMANTIC, not MINOR. Example 4 added for period type semantic conflict. |
