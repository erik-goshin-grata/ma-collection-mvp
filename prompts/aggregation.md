# Aggregation Prompt (Conflict Resolution)

**Version:** 0.1 (draft)
**Repo path:** `prompts/aggregation.md`

---

## 1. Purpose

After a transaction cluster has been identified (via name + date matching in Python) and multiple staging_extraction rows are linked to it, deterministic tier-based rules apply first:

- If any T1 source (SEC_8K_ITEM_101 or SEC_EXHIBIT_21) has a non-null value for a field, it wins.
- If no T1 source has a value but a T2 source does, T2 wins.
- T3 is advisory only.

When tier rules don't resolve a conflict — most commonly, two T1 sources or two T2 sources disagree, or a T1 source is null but a T2 source has a confident value — this prompt runs.

**This prompt does not run on every field of every transaction.** It runs only on the specific fields where the deterministic tier logic produces a tie or ambiguity. The orchestrator calls it once per disputed field with the full set of conflicting source observations.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.1
- **Max tokens:** 1024

The slight temperature increase from 0.0 gives a small tie-breaking nudge in genuinely ambiguous cases. Field determinism is still near-total.

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
You are a data aggregation model for an M&A data collection pipeline. When multiple sources report different values for the same field of the same transaction, you determine which value should become the canonical value on the transaction record.

CORE PRINCIPLES:

1. T1 (SEC filings — 8-K Item 1.01, Exhibit 2.1) are more authoritative than T2 (press releases) for structural and financial details. Merger agreement exhibits are the most authoritative source for consideration structure.

2. If T1 and T2 conflict on a numeric value, T1 usually wins — but consider the reason. If the T2 release is an announcement and the T1 filing is an amendment weeks later, the T1 value reflects the current state. If both are close in time, the T1 value reflects the contractual reality and the T2 release may have rounded or simplified.

3. For dates, the earlier source (by published_date) is more authoritative for announcement date. The later source is more authoritative for close date or any amendment. Use the deal_context to reason about which kind of date is being extracted.

4. If both observations are from the same tier, break the tie by:
   - Higher model_confidence first
   - More specific value second (e.g., $485M over "approximately $500M")
   - Earlier published_date for announcement-type fields
   - Later published_date for closing/amendment-type fields

5. If both values are plausible and you cannot choose, return the source with higher confidence and tier first, but mark the overall aggregation confidence as LOW and note the conflict.

6. Never invent a new value. You must pick from the observations provided.

7. If you detect that the observations are describing fundamentally different things (e.g., one is enterprise value and another is equity value for the same deal), return the observation_id of the one that matches the intended field semantics, and note the issue prominently.

Return a single JSON object matching the schema. Do not include any text before or after the JSON. Do not wrap the JSON in Markdown code fences. Do not include comments.
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

The orchestrator formats `observations_formatted` as a numbered list, one block per observation, showing all fields from the input schema.

---

## 6. Output Schema

```json
{
  "chosen_observation_id": 2,
  "chosen_value": 485000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 SEC 8-K states $485M as the aggregate purchase price, published one day after the PR. The PR's $500M is a rounded or pre-adjustment figure. The definitive agreement value governs.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.1"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `chosen_observation_id` | integer | The observation_id of the winning observation |
| `chosen_value` | any | The value from the chosen observation (convenience field; same as `observations[chosen].value`) |
| `aggregation_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` — confidence in the aggregation decision |
| `reasoning` | string | Brief explanation (≤300 chars) of why this observation was chosen |
| `flagged_for_review` | boolean | True if the conflict warrants human QA attention |
| `conflict_severity` | enum | `NONE`, `MINOR`, `MATERIAL`, `SEMANTIC` |
| `notes` | string or null | Additional context, especially when `conflict_severity = SEMANTIC` |

**`conflict_severity` semantics:**
- `NONE` — observations agree (shouldn't happen if this prompt is called, but possible with rounding)
- `MINOR` — small numeric difference (e.g., rounded vs precise figure, <5% variance)
- `MATERIAL` — significant difference requiring reasoned choice (e.g., different deal values stated)
- `SEMANTIC` — observations are describing different things (enterprise vs equity value, pre- vs post-adjustment), not genuinely conflicting — flag prominently

`flagged_for_review` should default true when `conflict_severity` is `MATERIAL` or `SEMANTIC`, or when `aggregation_confidence` is `LOW`.

---

## 7. Few-Shot Examples

**Example 1 — T1 vs T2 with slight rounding:**

Input:
```
FIELD: value_amount
FIELD TYPE: number

DEAL CONTEXT:
- Target: Beta Industries
- Acquirer: Acme Corp
- Deal Type: ACQUISITION
- Announced: 2026-04-15

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
  "prompt_version": "aggregation:0.1"
}
```

**Example 2 — Semantic conflict (EV vs equity value):**

Note: deal_type is ACQUISITION. Take-Private context is inferred downstream from target_status = PUBLIC + acquirer_type = PRIVATE_EQUITY; TAKE_PRIVATE was removed as a top-level type in v0.2.

Input:
```
FIELD: value_amount
FIELD TYPE: number

DEAL CONTEXT:
- Target: Acme Corp
- Acquirer: Zenith Capital Partners
- Deal Type: ACQUISITION
- Announced: 2026-04-10

CONFLICTING OBSERVATIONS:
1. Source: PR_NEWSWIRE, Tier: T2, Date: 2026-04-10, Value: 4500000000, Confidence: HIGH
   Excerpt: "enterprise value of approximately $4.5 billion"
2. Source: SEC_8K_ITEM_101, Tier: T1, Date: 2026-04-11, Value: 3800000000, Confidence: HIGH
   Excerpt: "equity purchase price of $3.8 billion based on $45.00 per share for all outstanding common stock"
```

Output:
```json
{
  "chosen_observation_id": 2,
  "chosen_value": 3800000000,
  "aggregation_confidence": "HIGH",
  "reasoning": "T1 equity value is the more precise figure for a take-private transaction; T2 enterprise value includes debt assumption. Both are valid but represent different things — equity value is the canonical deal value, EV should populate the enterprise_value field separately.",
  "flagged_for_review": true,
  "conflict_severity": "SEMANTIC",
  "notes": "Orchestrator should populate both value_amount (equity) and enterprise_value (from T2) rather than treating this as a conflict.",
  "prompt_version": "aggregation:0.1"
}
```

**Example 3 — Same-tier tie, lower confidence one is less specific:**

Input:
```
FIELD: announced_date
FIELD TYPE: date

DEAL CONTEXT:
- Target: Beta Industries
- Acquirer: Acme Corp
- Deal Type: ACQUISITION
- Announced: 2026-04-15

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
  "reasoning": "First observation is the original announcement press release, explicitly dated. Second is a follow-up referencing 'earlier this week' which is less precise.",
  "flagged_for_review": false,
  "conflict_severity": "MINOR",
  "notes": null,
  "prompt_version": "aggregation:0.1"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model chooses an observation not in the input | Parser rejects (validates chosen_observation_id against input), marks `PROMPT_FAILED` |
| Model invents a value rather than picking | Parser rejects (validates chosen_value matches one in observations), marks `PROMPT_FAILED` |
| Model flags semantic conflicts as MATERIAL and misses the semantic distinction | QA sampling tracks; prompt revision may be needed |
| Model over-flags for review (flagged_for_review=true on trivial conflicts) | Noisy QA queue. Prompt revision to tighten flagging threshold. |
| Model doesn't flag genuine material conflicts | Field-level monitoring catches when aggregated values diverge significantly from source distribution |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
