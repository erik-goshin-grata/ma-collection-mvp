# Aggregation Prompt (Conflict Resolution)

**Version:** 0.6 (provenance is caller-owned)
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

FIELD VOCABULARY

You are only ever asked to choose between two or more values that were actually
observed, and never to decide whether a field should be populated. Null
observations are removed upstream, so a value in front of you was extracted by
a stage that already applied its own evidence rules. Your job is which observed
value is canonical — not whether the fact is established.

<!-- AGG_VOCAB_START — machine-checked against the owning stages' frozensets by
     scripts/test_aggregation_vocabulary_parity.py. Every list below must match the
     enum the owning stage validates; add a field here when a slice adds one. -->
- v2_event_type: ACQUISITION | SPIN_OFF | SPLIT_OFF | JOINT_VENTURE | RECAPITALIZATION | VC_ROUND | GROWTH_EQUITY | VENTURE_DEBT | PIPE | UNKNOWN
- legacy_read_only: MERGER | REVERSE_MERGER | MINORITY_INVESTMENT
- event_history_type: ANNOUNCED | CLOSED | AMENDED | TERMINATED
- combination_structure: MERGER | REVERSE_MERGER | DE_SPAC
- target_type: standalone_company | subsidiary | business_unit | assets
- asset_type: REAL_ESTATE | INFRASTRUCTURE | ENERGY | NATURAL_RESOURCES | INTELLECTUAL_PROPERTY | DATA | FACILITY | EQUIPMENT | CONTRACTS_OR_RIGHTS | BRAND_OR_PRODUCT | OTHER
- offer_mechanism: TENDER_OFFER
- sponsor_transaction_role: PLATFORM | ADD_ON
- deal_attitude: FRIENDLY | HOSTILE
- approach_type: SOLICITED | UNSOLICITED
- round_price_direction: UP | DOWN | FLAT
- value_type: EQUITY_VALUE | TRANSACTION_VALUE | ENTERPRISE_VALUE | MARKET_CAPITALIZATION | UNDISCLOSED
- acquirer_type: strategic_corporate | private_equity | pe_portfolio | venture_capital | growth_equity | sovereign_wealth_fund | pension_fund | hedge_fund | family_office | individual | management | employee_group | spac | consortium | other_financial_sponsor | unknown
- consideration_components.form: CASH | ACQUIRER_STOCK | TARGET_STOCK | EARNOUT | CVR | CONTINGENT_CONSIDERATION | DEBT_ASSUMED | RETAINED_EQUITY | OTHER
<!-- AGG_VOCAB_END -->

`legacy_read_only` values appear on historical rows and may be observed. They are
NEVER valid new output: MERGER and REVERSE_MERGER are combination structures of an
ACQUISITION, not event types, and minority status is derived downstream rather than
typed. If observations disagree between a legacy value and a current one for the same
fact, prefer the current representation.

RESOLVING TYPED DIMENSIONS

Most fields resolve on tier, confidence and specificity. These do not, and treating
them as ordinary strings loses or invents a fact:

- combination_structure is HIERARCHICAL: DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER. Two
  sources saying DE_SPAC and MERGER are not in conflict — they state the same fact at
  different specificity. Choose the most specific value the sources support, resolve
  ambiguity UPWARD, and do not flag this as MATERIAL.
- asset_type is subordinate to target_type = assets. Never resolve the pair into a
  combination where asset_type is populated for any other target type.
- sponsor_transaction_role: PLATFORM is NOT a more specific ADD_ON. It carries a
  higher evidence bar, so a source asserting PLATFORM does not outrank one asserting
  ADD_ON merely by naming a stronger claim. Resolve on tier and confidence.
- deal_attitude and approach_type are INDEPENDENT dimensions. Resolving one must not
  influence the other; a transaction may be unsolicited and also board-recommended.
- round_price_direction: FLAT means the source stated the valuation was unchanged. It
  is not the "nothing established" answer, so never resolve a disagreement into FLAT
  as a compromise.
- offer_mechanism has one value plus null by decision. MANDATORY_OFFER,
  SCHEME_OF_ARRANGEMENT, ONE_STEP_MERGER and TWO_STEP_MERGER are excluded; never
  choose a value outside the list above.
- consideration_components.form: prefer the most specific form the sources support.
  CONTINGENT_CONSIDERATION is the fallback for contingency whose kind is not
  established — do not choose it over EARNOUT or CVR when a source establishes which.

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
   T2 wins. Do not prefer pe_portfolio on the strength of a sponsor name — whether
   a transaction is sponsor-backed is carried by sponsor_transaction_role, not by
   the acquirer's type.

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
  "notes": null
}

All fields are required. Use null for optional fields that have no value.
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
  "notes": null
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
  "notes": null
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
  "notes": "Orchestrator should preserve both value_amount (equity) and implied_enterprise_value (from T2/source-stated EV) rather than treating this as a simple numeric conflict."
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
  "notes": null
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
  "notes": "Sources reference different periods — both should be preserved as separate financial_metric rows when that table is available. For now, T1 NTM value is canonical for this field."
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
| 0.5 | 2026-08-21 | **V3 field vocabulary (§T2, §T3, §T7, §T11–T14).** This prompt reasons about every canonical field — Stage 9 calls it once per disputed field — and had not been opened by any V3 slice, so it still enumerated `MERGER` and `REVERSE_MERGER` as event types, lacked `MARKET_CAPITALIZATION`, and had no vocabulary at all for `combination_structure`, `asset_type`, `offer_mechanism`, `deal_attitude`, `approach_type`, `sponsor_transaction_role` or `round_price_direction`. Vocabularies are now in a marker-delimited block checked against the owning stages' frozensets by `scripts/test_aggregation_vocabulary_parity.py`. Retired event types move to a labelled `legacy_read_only` line: observable on stored rows, never valid new output. A typed-dimension section states the tie-breaks that plain string resolution gets wrong — `combination_structure` is hierarchical and DE_SPAC vs MERGER is not a conflict; `asset_type` is subordinate to `target_type = assets`; `PLATFORM` is not a more specific `ADD_ON`; attitude and approach are independent; `FLAT` is not a compromise between disagreeing price directions. The `pe_portfolio` specificity preference is removed — §T7 forbids acquirer type as a sponsor-status proxy and §T8 removes the value. Deliberately compact: no extraction rules, evidence bars or null policy, because `_pick_value` drops nulls before escalating and this prompt only ever chooses between observed non-null values. |
| 0.6 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
