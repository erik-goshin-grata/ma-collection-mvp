# Low-Confidence Extraction Prompt

**Version:** 0.4
**Repo path:** `prompts/low_confidence_extraction.md`

---

## 1. Purpose

Extract fields that are frequently absent, inconsistently stated, or require nuanced judgment. These fields are lower-priority than the high-confidence set, but their signal matters for deal analytics when present.

Three field groups:
1. **Advisors** — financial and legal advisors on either side.
2. **Consideration components** — the composition of deal consideration (cash / stock / earnout / etc.) as an array of components. The orchestrator derives a single `consideration_type` classification downstream from these components.
3. **Deal characteristic flags** — deal features that are NOT derivable from the consideration array. Termination fees (split by party), go-shop, earnout presence, hostile nature, regulatory approvals.

Runs on every row where high-confidence extraction completed.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 2048

---

## 3. Input Schema

```json
{
  "source_raw_id": 12345,
  "source_type": "PR_NEWSWIRE",
  "source_tier": "T2",
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "Acme Corp (NASDAQ: ACME)...",
  "deal_type": "ACQUISITION",
  "event_type": "ANNOUNCEMENT",
  "target_type": "STANDALONE_COMPANY",
  "value_amount": 500000000,
  "value_currency": "USD",
  "value_type": "TRANSACTION_VALUE"
}
```

The `value_amount` and `value_type` from high-confidence extraction are passed so the model can sanity-check component sums and compute percentages against total deal value.

---

## 4. System Prompt

```
You are a financial data extraction model. Given the text of an M&A press release or SEC filing, extract the following fields. These fields are often absent — use null freely when a field is not stated.

ADVISORS:

Extract any financial and legal advisors mentioned in the text. For each advisor:
- advisor_name — the firm name as stated (e.g., "Goldman Sachs", "Wachtell, Lipton, Rosen & Katz")
- advisor_type — enum: FINANCIAL, LEGAL, OTHER
- advised_party — enum: TARGET, ACQUIRER, PARENT_SELLER, BOTH, UNKNOWN

Rules:
- "OTHER" covers fairness opinion providers, proxy solicitors, info agents, and accounting/tax advisors.
- Do not include internal advisors (in-house counsel, in-house finance teams) — only external firms.
- If multiple advisors are listed for the same party, capture each as a separate entry.
- If an advisor's role is stated but the advised party is ambiguous, use UNKNOWN.

CONSIDERATION COMPONENTS:

Extract the forms of consideration in the deal as an array of components. For each component:
- form — enum: CASH, ACQUIRER_STOCK, TARGET_STOCK, EARNOUT, CVR, DEBT_ASSUMED, RETAINED_EQUITY, OTHER
- amount — dollar amount of this component (null if not stated or not calculable)
- percentage — percentage of total deal value (null if not calculable)
- description — brief text describing the component (e.g., "$400M cash at closing," "contingent value right paying up to $5 per share")

form enum semantics:
- CASH — cash consideration paid at closing
- ACQUIRER_STOCK — shares of the acquirer issued to target shareholders
- TARGET_STOCK — exchange of target stock (rare in MVP scope)
- EARNOUT — contingent payment based on post-close performance
- CVR — contingent value right
- DEBT_ASSUMED — target debt assumed by the acquirer
- RETAINED_EQUITY — equity rolled over by target shareholders (common in PE deals)
- OTHER — any other form (preferred stock, exchangeable shares, notes). Use description to specify.

EARNOUT components:

An earnout is a contingent payment to target shareholders (or selling principals) tied to post-close performance milestones — typically revenue, EBITDA, or business-specific operational targets over a measurement period (1–5 years post-close).

Source language signals:
- "earnout of up to $X million"
- "$X million in earnouts"
- "performance-based payment of up to $X"
- "contingent on achieving [metric] targets"
- "additional consideration of up to $X based on performance"
- "earn-out" / "earnouts" / "earn out"

When an earnout is present, add a component with form=EARNOUT:
- amount: the maximum if "up to $X" is stated, or the stated amount; null when amount not disclosed
- percentage: percentage of total deal value if calculable; null otherwise
- description: brief summary of what triggers the payment and measurement period if stated

When earnout amount is not stated: set amount=null, description="earnout present but amount not disclosed".

CVR components:

A Contingent Value Right (CVR) is a security issued to target shareholders that pays additional consideration upon specified milestones — most common in pharma/biotech (regulatory approval, sales thresholds), occasionally in legal liability outcomes or post-close events.

Source language signals:
- "Contingent Value Right" / "CVR"
- "non-tradeable / tradeable contingent value rights"
- "additional payment of $X per share upon [milestone]"
- "milestone payment tied to [trigger]"

When a CVR is present, add a component with form=CVR:
- amount: total aggregate CVR value if stated; null if only per-share stated and share count not inferable
- percentage: null in most cases (CVR value often not computable as % of deal)
- description: what milestone unlocks the CVR payment, expiration if stated, tradeable status if stated

LEAVE OUT EARNOUT and CVR entries entirely when:
- Source text does not mention an earnout or CVR
- Mentions "performance" generically without tying it to additional payment ("expected to drive performance" is not an earnout)

Notes:
- Earnouts and CVRs are ADDITIVE to primary consideration. A deal can be cash + earnout, stock + CVR, cash + stock + earnout + CVR. Capture all components.
- The consideration_type field (orchestrator-derived: CASH / STOCK / CASH_AND_STOCK / ELECTION / OTHER) reflects the primary structure. Earnouts and CVRs do NOT change consideration_type — a cash + earnout deal stays consideration_type=CASH. The has_earnout and has_cvr flags are derived downstream from the components array.

Rules:
- Do not derive cash/stock percentages if the release doesn't provide them — leave percentage null.
- Amounts should sum approximately to the deal value; do not force reconciliation.
- For all-cash deals, record a single CASH entry with amount equal to the deal value.
- Empty array is valid (terms not disclosed, or release doesn't detail components).

Do NOT output any "all_cash" or "includes_stock" boolean flags. The orchestrator derives a consideration_type categorization (CASH / STOCK / CASH_AND_STOCK / ELECTION / OTHER) from the components downstream.

DEAL CHARACTERISTIC FLAGS:

Extract features of the deal that are not directly derivable from the consideration array.

Earnout presence:
- includes_earnout — boolean: true if any earnout or CVR component is present. (Yes, this is derivable from the components array, but it's a prominent enough feature that we capture it explicitly for easy filtering.)

Hostile / competitive signals:
- hostile — boolean: true if the deal is described as hostile, unsolicited, or subject to a proxy contest
- competing_bid — boolean: true if a competing or "topping" bid is referenced

Regulatory:
- regulatory_approvals_required — boolean: true if specific antitrust, CFIUS, or other regulatory approvals are called out

Go-Shop:
- has_go_shop — boolean: true if a go-shop period is mentioned
- go_shop_period_days — integer: duration in days. Null if has_go_shop is false or duration not stated.

Termination Fees (schema splits these by party):
- target_fee_amount — dollar amount payable by target if it terminates. Null if not stated.
- target_fee_percentage — percentage of deal value payable by target. Null if only amount stated, or not stated at all.
- acquirer_fee_amount — dollar amount payable by acquirer (reverse termination fee) if it terminates. Null if not stated.
- acquirer_fee_percentage — percentage of deal value payable by acquirer. Null if only amount stated, or not stated at all.

Termination fee rules:
- If the release states "$135 million termination fee payable by [target]" — target_fee_amount populated, target_fee_percentage null.
- If stated as "approximately 3% of deal value payable by [target]" — target_fee_percentage populated, target_fee_amount null.
- If both are stated ("$135 million, representing approximately 3% of deal value") — populate both.
- "Reverse termination fee" or "payable by [acquirer]" — acquirer side.
- Default all four fields to null. Termination fees are rare in private-party deals and common only in public-target M&A.

All booleans default false unless the text supports them. Do not set any flag true based on deal type alone.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"}
  ],
  "consideration_components": [
    {
      "form": "CASH",
      "amount": 50000000,
      "percentage": 92.6,
      "description": "$50M cash at closing"
    },
    {
      "form": "EARNOUT",
      "amount": 4000000,
      "percentage": 7.4,
      "description": "up to $4M tied to revenue milestones in years 1-2 post-close"
    }
  ],
  "flags": {
    "includes_earnout": true,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {
    "has_go_shop": false,
    "go_shop_period_days": null
  },
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
SOURCE TYPE: {source_type}
DEAL TYPE: {deal_type}
TARGET TYPE: {target_type}
EVENT TYPE: {event_type}
DEAL VALUE: {value_amount} {value_currency} ({value_type})

TITLE: {title}

BODY:
{clean_text}

Extract advisors, consideration components, and deal characteristic flags.
```

---

## 6. Output Schema

```json
{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"}
  ],
  "consideration_components": [
    {
      "form": "CASH",
      "amount": 500000000,
      "percentage": 100.0,
      "description": "All-cash consideration paid at closing"
    }
  ],
  "flags": {
    "includes_earnout": false,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {
    "has_go_shop": false,
    "go_shop_period_days": null
  },
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Field notes:**

- `advisors` and `consideration_components` are always arrays (possibly empty). Empty array = no mentions in source, distinct from unknown.
- `flags` contains only features not derivable from `consideration_components`. Payment mix flags (`all_cash`, `includes_stock`) removed in v0.2 — derived by orchestrator from the components array.
- `go_shop` is now an object: `has_go_shop` + `go_shop_period_days`.
- `termination_fees` is now an object with target/acquirer × amount/percentage, per schema.

---

## 7. Few-Shot Examples

**Example 1 — All-cash deal with advisors, no termination fees:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
DEAL VALUE: 500000000 USD (TRANSACTION_VALUE)

TITLE: Acme Corp to Acquire Beta Industries for $500 Million
BODY: Acme Corp (NASDAQ: ACME) today announced a definitive agreement to acquire Beta Industries for $500 million in cash. Goldman Sachs is serving as exclusive financial advisor and Wachtell, Lipton, Rosen & Katz is serving as legal counsel to Acme. Morgan Stanley is serving as financial advisor to Beta, with Kirkland & Ellis as its legal counsel.
```

Output:
```json
{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"},
    {"name": "Morgan Stanley", "type": "FINANCIAL", "advised_party": "TARGET"},
    {"name": "Kirkland & Ellis", "type": "LEGAL", "advised_party": "TARGET"}
  ],
  "consideration_components": [
    {"form": "CASH", "amount": 500000000, "percentage": 100.0, "description": "All-cash at closing"}
  ],
  "flags": {
    "includes_earnout": false,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 2 — Mixed consideration with earnout:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
DEAL VALUE: 800000000 USD (TRANSACTION_VALUE)

TITLE: Acme Corp Acquires Beta Industries for Up to $800 Million
BODY: Acme Corp today announced the acquisition of Beta Industries for upfront consideration of $600 million, comprised of $400 million in cash and $200 million in Acme common stock. Additionally, Beta shareholders will be entitled to earnout payments of up to $200 million based on Beta's revenue performance over the three years following closing.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 400000000, "percentage": 50.0, "description": "$400M cash at closing"},
    {"form": "ACQUIRER_STOCK", "amount": 200000000, "percentage": 25.0, "description": "$200M in Acme common stock at closing"},
    {"form": "EARNOUT", "amount": 200000000, "percentage": 25.0, "description": "Up to $200M earnout over 3 years based on revenue"}
  ],
  "flags": {
    "includes_earnout": true,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Percentages against $800M max deal value",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 3 — Take-Private with termination fees (both sides), go-shop, regulatory:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
DEAL VALUE: 4500000000 USD (ENTERPRISE_VALUE)

TITLE: Acme Corp to Be Acquired by Zenith Capital in $4.5 Billion Transaction
BODY: Acme Corp (NYSE: ACME) entered into a definitive merger agreement with Zenith Capital Partners for $45.00 per share in cash. The agreement includes a 35-day "go-shop" period during which Acme's board may solicit alternative proposals. A termination fee of $135 million is payable by Acme if the agreement is terminated under specified circumstances. Zenith will pay a reverse termination fee of $270 million, representing approximately 6% of the equity value, if the transaction fails to close due to regulatory reasons. The transaction is subject to customary regulatory approvals, including HSR clearance.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 4500000000, "percentage": 100.0, "description": "$45.00 per share cash"}
  ],
  "flags": {
    "includes_earnout": false,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": true
  },
  "go_shop": {"has_go_shop": true, "go_shop_period_days": 35},
  "termination_fees": {
    "target_fee_amount": 135000000,
    "target_fee_percentage": null,
    "acquirer_fee_amount": 270000000,
    "acquirer_fee_percentage": 6.0
  },
  "model_confidence": "HIGH",
  "notes": "HSR explicit. Target fee amount-only; acquirer fee both amount and percentage stated.",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 4 — Cash plus earnout:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
DEAL VALUE: 54000000 USD (TRANSACTION_VALUE)

TITLE: SkyCore Technologies Acquired for $54 Million
BODY: SkyCore Technologies today announced it has been acquired for $50 million in cash at close plus up to $4 million in earnouts tied to revenue performance over the two years following close. No advisors were disclosed.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 50000000, "percentage": 92.6, "description": "$50M cash at closing"},
    {"form": "EARNOUT", "amount": 4000000, "percentage": 7.4, "description": "up to $4M tied to revenue performance years 1-2 post-close"}
  ],
  "flags": {
    "includes_earnout": true,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Percentages against $54M max deal value",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 5 — Cash per share plus CVR:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
DEAL VALUE: null null (UNDISCLOSED)

TITLE: PharmaCo to Acquire BioTarget for $25.00 Per Share Plus CVR
BODY: PharmaCo today announced a definitive agreement to acquire BioTarget Inc. (NASDAQ: BTGT) for $25.00 per share in cash at close, plus a non-tradeable Contingent Value Right of up to $5.00 per share tied to FDA approval of ABC-123 by December 31, 2027. Lazard is serving as financial advisor to PharmaCo; Centerview Partners is advising BioTarget.
```

Output:
```json
{
  "advisors": [
    {"name": "Lazard", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Centerview Partners", "type": "FINANCIAL", "advised_party": "TARGET"}
  ],
  "consideration_components": [
    {"form": "CASH", "amount": null, "percentage": null, "description": "$25.00 per share cash at closing"},
    {"form": "CVR", "amount": null, "percentage": null, "description": "non-tradeable CVR of up to $5.00 per share; FDA approval of ABC-123 by 2027-12-31"}
  ],
  "flags": {
    "includes_earnout": true,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Total deal value not computable without share count; amounts left null",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 6 — Closing release, no terms disclosed:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: CLOSE
DEAL VALUE: null null (UNDISCLOSED)

TITLE: Acme Corp Completes Acquisition of Beta Industries
BODY: Acme Corp today announced that it has completed its previously announced acquisition of Beta Industries. Terms of the transaction were not disclosed.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [],
  "flags": {
    "includes_earnout": false,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Closing release with no financial terms disclosed",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model outputs v0.1 fields (`all_cash`, `includes_stock`) | Parser rejects — these are derived downstream, not extracted |
| Model uses v0.1 `break_fee_amount` / `break_fee_disclosed` fields | Parser rejects — replaced with `termination_fees` object in v0.2 |
| Model confuses target and acquirer fees | Prompt explicitly addresses. QA samples catch. |
| Model populates both amount and percentage when only one is stated | Accept (valid), but flag in notes if inferred |
| Model invents advisors not in text | Gold set catches; prompt forbids |
| Model attributes advisor to wrong party | Common when advisors are listed at the end. advised_party=UNKNOWN is provided for ambiguous cases. |
| Percentage fields don't sum to 100 | Aggregation stage flags; release may have unaccounted components |
| `go_shop_period_days` populated but `has_go_shop` = false | Parser rejects (logical contradiction) |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Revised. Removed `all_cash` and `includes_stock` flags (derived by orchestrator from consideration array). Split `break_fee_*` fields into `termination_fees` object with target/acquirer × amount/percentage per schema. Formalized `go_shop` as object with `has_go_shop` + `go_shop_period_days`. Renamed `consideration` → `consideration_components` for clarity. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-05-02 | Added EARNOUT and CVR component-type guidance to consideration_components extraction. Components are additive to primary consideration; do not change consideration_type. Added few-shot examples for both (Examples 4, 5). Updated RESPONSE FORMAT to show earnout component. |
