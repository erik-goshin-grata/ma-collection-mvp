# Agreement Consideration Extraction Prompt

**Version:** 0.2 (provenance is caller-owned)
**Repo path:** `prompts/agreement_consideration.md`

---

## 1. Purpose

Extract the structured consideration details from the CONSIDERATION section of a deal document (merger agreement, proxy, tender offer). Produces greater precision than PR-only extraction because agreement text states consideration terms definitively.

Runs in Stage 11 (agreement_extract) for each CONSIDERATION section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 1024

---

## 3. Input Schema

```json
{
  "section_text": "..."
}
```

---

## 4. System Prompt

```
You are extracting structured consideration data from the Consideration or Per Share Merger Consideration section of a deal document.

CONSIDERATION COMPONENTS

Return a "consideration_components" array. Each element describes one component of the deal consideration. Use these form values:
- CASH: fixed cash payment per share
- ACQUIRER_STOCK: acquirer stock issued per target share (use exchange_ratio for the ratio)
- TARGET_STOCK: target stock retained (unusual; for stub equity)
- CVR: contingent value right with specified trigger and per-share value
- EARNOUT: contingent earn-out payment (post-close milestone-based)
- DEBT_ASSUMED: acquirer assumes target debt as part of consideration
- RETAINED_EQUITY: seller retains equity stake in combined entity

PER-SHARE FIELDS
- per_share_amount: the cash amount per share (for CASH), or the CVR/earnout value per share when stated
- exchange_ratio: for ACQUIRER_STOCK, shares of acquirer stock per share of target stock (e.g., 0.4552)
- currency: ISO 4217 currency code (USD most common)
- trigger_description: for CVR/earnout, brief description of the trigger condition (≤200 chars)

per_share_price_total: the aggregate per-share value summing all cash components. For stock consideration, include the implied value only if the exchange ratio and a stated value are both present; otherwise omit.

ELECTION MECHANICS
When target shareholders have an election (cash or stock or mixed):
- Include each electable form as a separate component
- Set "election": true on each electable component
- Note any proration mechanics in the notes field

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": 42.00, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false},
    {"form": "CVR", "per_share_amount": 5.00, "currency": "USD", "exchange_ratio": null, "trigger_description": "FDA approval of XB-100 by December 31, 2027", "election": false}
  ],
  "per_share_price_total": 47.00,
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for optional sub-fields that have no value.
```

---

## 5. User Prompt Template

```
Extract consideration structure from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `consideration_components` | array | One element per consideration form |
| `consideration_components[].form` | enum | CASH \| ACQUIRER_STOCK \| CVR \| EARNOUT \| DEBT_ASSUMED \| RETAINED_EQUITY \| OTHER |
| `consideration_components[].per_share_amount` | number\|null | Per-share cash value |
| `consideration_components[].currency` | string\|null | ISO 4217 |
| `consideration_components[].exchange_ratio` | number\|null | For ACQUIRER_STOCK: acquirer shares per target share |
| `consideration_components[].trigger_description` | string\|null | For CVR/EARNOUT |
| `consideration_components[].election` | boolean | True when shareholder can elect this form |
| `per_share_price_total` | number\|null | Sum of cash-equivalent per-share consideration |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Proration mechanics, caveats (≤200 chars) |

---

## 7. Few-Shot Examples

### Example 1 — Pure cash merger

**Input section text:**
```
ARTICLE III
MERGER CONSIDERATION

Section 3.1 Conversion of Company Common Stock. At the Effective Time, by virtue of the Merger and without any action on the part of the holders thereof, each share of Company Common Stock issued and outstanding immediately prior to the Effective Time (other than Excluded Shares and Dissenting Shares) shall be converted into the right to receive $52.00 in cash, without interest (the "Merger Consideration").
```

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": 52.00, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false}
  ],
  "per_share_price_total": 52.00,
  "model_confidence": "HIGH",
  "notes": null
}
```

### Example 2 — Cash plus CVR

**Input section text:**
```
Section 2.2 Conversion of Shares. At the Effective Time, each share of Company Common Stock issued and outstanding shall be converted into the right to receive: (a) $28.00 in cash, without interest (the "Cash Consideration"), plus (b) one contingent value right (a "CVR") entitling the holder to receive up to an additional $4.50 per share upon achievement of the Net Sales Milestone (as defined in the CVR Agreement) by December 31, 2028. The aggregate per-share consideration assuming full CVR payment would be $32.50.
```

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": 28.00, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false},
    {"form": "CVR", "per_share_amount": 4.50, "currency": "USD", "exchange_ratio": null, "trigger_description": "Achievement of Net Sales Milestone by December 31, 2028", "election": false}
  ],
  "per_share_price_total": 28.00,
  "model_confidence": "HIGH",
  "notes": "CVR amount ($4.50) is contingent maximum; total if full CVR is $32.50 per agreement."
}
```

### Example 3 — Stock-for-stock with exchange ratio

**Input section text:**
```
Section 2.2 Conversion of Shares.

(a) Each share of Company Common Stock issued and outstanding immediately prior to the Effective Time shall be converted into the right to receive 0.4235 shares (the "Exchange Ratio") of Parent Common Stock (the "Stock Consideration").

(b) No fractional shares of Parent Common Stock shall be issued; any fractional entitlement shall be paid in cash based on the volume-weighted average price of Parent Common Stock for the five trading days preceding the Closing Date.
```

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "ACQUIRER_STOCK", "per_share_amount": null, "currency": null, "exchange_ratio": 0.4235, "trigger_description": null, "election": false}
  ],
  "per_share_price_total": null,
  "model_confidence": "HIGH",
  "notes": "Cash paid in lieu of fractional shares at VWAP; no fixed per-share cash amount stated."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| No per-share amount found (aggregate only) | Set per_share_amount null; note aggregate in notes |
| Section describes treatment of options/RSUs without stating common stock consideration | Return empty array; model_confidence NONE |
| Election mechanics with proration pools | Include each electable form; describe proration in notes |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
