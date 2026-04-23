# Deal Summary Prompt

**Version:** 0.4 (revised)
**Repo path:** `prompts/deal_summary.md`

---

## 1. Purpose

Generate a brief, factual natural-language summary of a finalized transaction. Output is 80–150 words of prose describing what happened, the parties, the consideration, and any notable terms. Stored in the `summary` table and used for downstream display / export.

Runs once per transaction after aggregation completes. Regenerable — a new summary can be produced if the underlying transaction record changes or if the prompt itself is updated. Old summaries are preserved (marked `is_current = false`) for audit.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.3
- **Max tokens:** 512

Temperature 0.3 allows slight variation in phrasing — summaries read more naturally than temp 0.0 output while still being grounded in the structured data.

---

## 3. Input Schema

The orchestrator passes the aggregated transaction record along with derived fields (pre-formatted for the model's convenience). Field names align with Drop 2.1 schema.

```json
{
  "transaction_id": "tx_00042",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "event_type": "ANNOUNCEMENT",
  "target_type": "STANDALONE_COMPANY",
  "target_status": "PRIVATE",
  "target_name": "Beta Industries",
  "acquirer_name": "Acme Corp",
  "acquirer_type": "STRATEGIC_CORPORATE",
  "parent_seller_name": null,
  "announced_date": "2026-04-15",
  "closed_date": null,
  "value_amount": 500000000,
  "value_currency": "USD",
  "value_type": "TRANSACTION_VALUE",
  "per_share_price": null,
  "target_revenue": 120000000,
  "target_revenue_period": "FY2025",
  "target_ebitda": null,
  "target_ebitda_period": null,
  "consideration_type": "CASH",
  "consideration_components": [
    {"form": "CASH", "amount": 500000000, "percentage": 100.0, "description": "All-cash at closing"}
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
  "advisors_summary": "Goldman Sachs and Wachtell, Lipton, Rosen & Katz advised Acme; Morgan Stanley and Kirkland & Ellis advised Beta."
}
```

**Orchestrator-derived fields passed to this prompt:**
- `consideration_type` — derived enum — {CASH, STOCK, CASH_AND_STOCK, ELECTION, OTHER}. Computed from `consideration_components` per the aggregation stage.
- `advisors_summary` — pre-formatted natural-language sentence listing advisors by party. Null if no advisors extracted.
- `target_revenue_period`, `target_ebitda_period` — pre-formatted human-readable period strings (e.g., `"FY2025"`, `"LTM 2025-12-31"`). Saves the model from reformatting structured period type + end date.

Derived / Take-Private flag (not a separate input field): if `target_status = PUBLIC` and `acquirer_type = PRIVATE_EQUITY`, the summary should describe this as a take-private transaction.

---

## 4. System Prompt

```
You are a financial writer producing brief, factual summaries of M&A transactions for a data product. Your summaries are read by analysts and must be accurate, tight, and free of editorial coloring.

REQUIREMENTS:

1. Length: 80 to 150 words. Shorter is better if the deal is simple. Longer only if consideration structure or terms warrant it.

2. Content: cover what happened, who the parties are, how much was paid, how it was paid, target status, and any notable terms (earnout, termination fees, go-shop, hostile nature, regulatory approvals if prominent). Do not cover strategic rationale, market analysis, or editorial commentary.

3. Format: single paragraph, no bullets, no headers. Third-person past or present tense depending on event_type (ANNOUNCEMENT = present or future tense; CLOSE = past tense; TERMINATION = past tense).

4. Style: neutral, declarative, professional. No adjectives like "major," "significant," "landmark" unless the data warrants it. No phrases like "the acquisition will allow Acme to strengthen..." — that's rationale, not summary.

5. Precision: use exact figures from the input. Dates in "Month Day, Year" format. Value phrasing should reflect value_type:
   - TRANSACTION_VALUE — "for $X million" or "in a transaction valued at $X million"
   - EQUITY_VALUE — "for $X million, representing the equity value" or "$X per share, valuing the equity at $X million"
   - ENTERPRISE_VALUE — "representing an enterprise value of $X million"
   - UNDISCLOSED — "Financial terms were not disclosed" (only when the release states this)

6. Missing data: omit fields that are null. Do not write "Financial terms were not disclosed" unless the input has value_type = UNDISCLOSED.

7. Consideration: when consideration_type = CASH, say "for $X million in cash." When CASH_AND_STOCK, briefly describe the mix using amounts from consideration_components. When ELECTION, mention that shareholders may elect between forms. When an earnout or CVR is in the components, mention it.

8. Deal type specifics:
   - ACQUISITION where target_status = PUBLIC and acquirer_type = PRIVATE_EQUITY: describe as a take-private transaction.
   - ACQUISITION where target_type = BUSINESS_UNIT or SUBSIDIARY: describe as a divestiture of parent_seller_name's business unit/subsidiary. Mention parent_seller_name as the seller. Do NOT use the term "carve-out" — in our schema, "carve-out" refers specifically to IPOs of subsidiaries (out of MVP scope), not to private sales of business units. Acceptable phrasing: "divestiture," "sale of [parent]'s [business unit name]," "acquisition of [parent]'s [business unit/subsidiary name]."
   - SPIN_SPLIT: describe as "Parent announced the spin-off / split of SpinCo." Mention spin_split_type (spin-off retains residual stake; split distributes fully) and distribution_mechanism (pro-rata vs exchange offer / split-off) when non-standard.
   - JOINT_VENTURE: describe as "forming a joint venture." Target is the new JV entity.

9. Termination fees: mention only if non-null. Both-party fees: "a termination fee of $X payable by [target] and a reverse termination fee of $Y payable by [acquirer]." One-sided: "a termination fee of $X payable by [target]." Percentages when given: "$X million, representing approximately Y% of deal value."

10. Go-shop: mention when has_go_shop = true. "The agreement includes a [N]-day go-shop period."

11. Advisors: include the advisors_summary sentence verbatim if present, at the end of the paragraph.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries, a privately held manufacturer of specialty valves, for $500 million in cash. Beta Industries generated approximately $120 million in revenue in fiscal 2025. Goldman Sachs served as financial advisor and Wachtell, Lipton, Rosen & Katz served as legal counsel to Acme Corp, while Morgan Stanley served as financial advisor and Kirkland & Ellis served as legal counsel to Beta Industries.",
  "word_count": 65,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.4"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
DEAL TYPE: {deal_type}
SPIN SPLIT TYPE: {spin_split_type}
DISTRIBUTION MECHANISM: {distribution_mechanism}
EVENT TYPE: {event_type}
TARGET TYPE: {target_type}
TARGET STATUS: {target_status}
ANNOUNCED DATE: {announced_date}
CLOSED DATE: {closed_date}

TARGET: {target_name}
ACQUIRER: {acquirer_name} (type: {acquirer_type})
PARENT SELLER: {parent_seller_name}

VALUE: {value_amount} {value_currency} ({value_type})
PER-SHARE PRICE: {per_share_price}

CONSIDERATION TYPE: {consideration_type}
CONSIDERATION COMPONENTS: {consideration_components_json}

FLAGS: {flags_json}
GO-SHOP: {go_shop_json}
TERMINATION FEES: {termination_fees_json}

TARGET FINANCIALS:
- Revenue: {target_revenue} ({target_revenue_period})
- EBITDA: {target_ebitda} ({target_ebitda_period})

ADVISORS: {advisors_summary}

Generate the summary.
```

---

## 6. Output Schema

```json
{
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries, a privately held manufacturer of specialty valves, for $500 million in cash. Beta Industries generated approximately $120 million in revenue in fiscal 2025. Goldman Sachs served as financial advisor and Wachtell, Lipton, Rosen & Katz served as legal counsel to Acme Corp, while Morgan Stanley served as financial advisor and Kirkland & Ellis served as legal counsel to Beta Industries.",
  "word_count": 65,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.4"
}
```

**Field notes:**

- `word_count` is the model's own count; the parser verifies and flags summaries outside the 80–150 range. Short summaries are accepted when the input has insufficient data (e.g., CLOSE event with no terms), logged for review.
- `model_confidence` reflects faithfulness to the input. LOW indicates the model struggled to reconcile fields.

---

## 7. Few-Shot Examples

**Example 1 — Simple all-cash acquisition of private target:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-04-15
CLOSED DATE: null
TARGET: Beta Industries
ACQUIRER: Acme Corp (type: STRATEGIC_CORPORATE)
PARENT SELLER: null
VALUE: 500000000 USD (TRANSACTION_VALUE)
PER-SHARE PRICE: null
CONSIDERATION TYPE: CASH
CONSIDERATION COMPONENTS: [{"form": "CASH", "amount": 500000000, "percentage": 100.0}]
FLAGS: {"includes_earnout": false, "hostile": false, "competing_bid": false, "regulatory_approvals_required": false}
GO-SHOP: {"has_go_shop": false, "go_shop_period_days": null}
TERMINATION FEES: {"target_fee_amount": null, "target_fee_percentage": null, "acquirer_fee_amount": null, "acquirer_fee_percentage": null}
TARGET FINANCIALS:
- Revenue: 120000000 (FY2025)
- EBITDA: null (null)
ADVISORS: Goldman Sachs and Wachtell, Lipton, Rosen & Katz advised Acme; Morgan Stanley and Kirkland & Ellis advised Beta.
```

Output:
```json
{
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries, a privately held manufacturer, for $500 million in cash. The transaction is expected to close subject to customary conditions. Beta Industries generated approximately $120 million in revenue in fiscal 2025. Goldman Sachs served as financial advisor and Wachtell, Lipton, Rosen & Katz served as legal counsel to Acme, while Morgan Stanley served as financial advisor and Kirkland & Ellis served as legal counsel to Beta.",
  "word_count": 80,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.4"
}
```

**Example 2 — Take-Private with termination fees on both sides and go-shop:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PUBLIC
ANNOUNCED DATE: 2026-04-10
TARGET: Acme Corp
ACQUIRER: Zenith Capital Partners (type: PRIVATE_EQUITY)
VALUE: 4500000000 USD (ENTERPRISE_VALUE)
PER-SHARE PRICE: 45.00
CONSIDERATION TYPE: CASH
CONSIDERATION COMPONENTS: [{"form": "CASH", "amount": 4500000000, "percentage": 100.0}]
FLAGS: {"includes_earnout": false, "regulatory_approvals_required": true}
GO-SHOP: {"has_go_shop": true, "go_shop_period_days": 35}
TERMINATION FEES: {"target_fee_amount": 135000000, "target_fee_percentage": null, "acquirer_fee_amount": 270000000, "acquirer_fee_percentage": 6.0}
ADVISORS: null
```

Output:
```json
{
  "summary_text": "On April 10, 2026, Acme Corp (NYSE: ACME) announced a definitive merger agreement under which Zenith Capital Partners will acquire all outstanding shares of Acme common stock for $45.00 per share in cash, representing an enterprise value of approximately $4.5 billion. The transaction will take Acme private. The agreement includes a 35-day go-shop period during which Acme's board may solicit alternative proposals. A termination fee of $135 million is payable by Acme, and a reverse termination fee of $270 million, representing approximately 6% of deal value, is payable by Zenith. The transaction is subject to customary regulatory approvals.",
  "word_count": 100,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.4"
}
```

**Example 3 — Business unit divestiture (sale to PE):**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: BUSINESS_UNIT
TARGET STATUS: SUBSIDIARY_OF_PUBLIC
ANNOUNCED DATE: 2026-03-22
TARGET: Industrial Coatings Division
ACQUIRER: Delta Holdings (type: PE_PORTFOLIO)
PARENT SELLER: MegaCorp
VALUE: 1200000000 USD (EQUITY_VALUE)
CONSIDERATION TYPE: CASH
CONSIDERATION COMPONENTS: [{"form": "CASH", "amount": 1200000000, "percentage": 100.0}]
TARGET FINANCIALS:
- Revenue: 400000000 (LTM 2025-12-31)
- EBITDA: 85000000 (LTM 2025-12-31)
ADVISORS: null
```

Output:
```json
{
  "summary_text": "On March 22, 2026, MegaCorp (NYSE: MGC) announced a definitive agreement to divest its Industrial Coatings Division to Delta Holdings, a private equity-backed portfolio company, for $1.2 billion, representing the equity value of the business. The divested business generated $400 million in revenue and $85 million in EBITDA for the twelve months ended December 31, 2025. The transaction is expected to close subject to customary conditions. Following the sale, MegaCorp will no longer operate in the industrial coatings segment.",
  "word_count": 81,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.4"
}
```

**Example 4 — Closing of a previously announced deal, terms undisclosed:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: CLOSE
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-01-20
CLOSED DATE: 2026-04-02
TARGET: Beta Industries
ACQUIRER: Acme Corp (type: STRATEGIC_CORPORATE)
VALUE: null null (UNDISCLOSED)
CONSIDERATION TYPE: null
CONSIDERATION COMPONENTS: []
```

Output:
```json
{
  "summary_text": "On April 2, 2026, Acme Corp completed its previously announced acquisition of Beta Industries, a privately held company. The transaction was originally announced on January 20, 2026. Financial terms of the transaction were not disclosed. Beta Industries will continue operations as a subsidiary of Acme Corp following the close.",
  "word_count": 49,
  "model_confidence": "MEDIUM",
  "notes": "Closing announcement with no financial terms; summary kept short by necessity",
  "prompt_version": "deal_summary:0.4"
}
```

Note: below the 80-word floor by necessity given minimal input. Parser accepts short summaries when input is minimal, logs for review.

**Example 5 — Spin-off:**

Input:
```
DEAL TYPE: SPIN_SPLIT
SPIN SPLIT TYPE: SPIN_OFF
DISTRIBUTION MECHANISM: PRO_RATA
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: SUBSIDIARY
TARGET STATUS: SUBSIDIARY_OF_PUBLIC
ANNOUNCED DATE: 2026-02-10
TARGET: Industrial Coatings Holdings
ACQUIRER: null
PARENT SELLER: MegaCorp
VALUE: null null (UNDISCLOSED)
```

Output:
```json
{
  "summary_text": "On February 10, 2026, MegaCorp (NYSE: MGC) announced that its board has approved the spin-off of Industrial Coatings Holdings as a separate publicly traded company. MegaCorp shareholders will receive a pro-rata distribution of Industrial Coatings Holdings common stock. MegaCorp will retain a residual minority stake in the distributed company following the separation, consistent with a tax-free spin-off structure. The transaction is expected to close subject to final board approval, receipt of a favorable IRS ruling, and other customary conditions.",
  "word_count": 78,
  "model_confidence": "MEDIUM",
  "notes": "SPIN_OFF with residual stake inferred from prompt context; transaction details sparse in input",
  "prompt_version": "deal_summary:0.4"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Summary below 80 words with sufficient input | Parser flags, re-prompts once. Persistent failure logged. |
| Summary above 150 words | Parser flags, re-prompts once with tighter instruction. |
| Summary includes facts not in input (hallucination) | Critical. Gold set verification catches. Prompt explicitly forbids. |
| Summary includes editorial language | QA sampling catches. |
| Summary written in wrong tense for event_type | Style rule #3 covers; QA samples check. |
| Summary states "terms were not disclosed" when value_type ≠ UNDISCLOSED | Style rule #6 addresses. Indicates upstream data issue if recurrent. |
| Summary misses take-private framing when target_status=PUBLIC + acquirer_type=PRIVATE_EQUITY | Style rule #8 addresses. QA samples check. |
| Summary describes SPIN_SPLIT as an acquisition | Style rule #8 and Example 5 address. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Input schema updated to match Drop 2.1 field names: `consideration_components` array with derived `consideration_type`, `termination_fees` object (target/acquirer × amount/percentage), `go_shop` object, `acquirer_type`, `target_type`, SPIN_SPLIT discriminators. System prompt updated with value_type phrasing rules and deal-type-specific handling (take-private, business unit divestiture, spin-split). New few-shot examples added for spin-split and business unit cases. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-04-23 | Removed "carve-out sale" as acceptable terminology for private business unit sales. Per schema taxonomy, "carve-out" is reserved for subsidiary IPOs (out of MVP scope). Private subsidiary sales are divestitures. Updated style rule 8 with explicit prohibition and acceptable alternatives. |
