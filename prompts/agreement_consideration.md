# Agreement Consideration Extraction Prompt

**Version:** 0.3
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
- EARNOUT: contingent earn-out payment (post-close milestone-based)
- CVR: contingent value right with specified trigger and per-share value
- CONTINGENT_CONSIDERATION: a contingent purchase-price adjustment that is neither an earnout nor a CVR — e.g., a holdback released or forfeited based on a post-closing true-up, not a milestone and not a rights instrument
- DEBT_ASSUMED: use only when the source states a specific dollar amount of the target's financial indebtedness (notes, loans, bonds, credit facilities) that the acquirer assumes as part of the deal consideration. Do NOT use this for a generic reference to "assumed liabilities" or ordinary assumed obligations with no stated amount — describe that in notes instead, not as a component. Never calculate or infer this amount.
- RETAINED_EQUITY: seller retains equity stake in combined entity
- OTHER: a stated consideration element that does not fit any form above

PER-SHARE FIELDS
- per_share_amount: the cash amount per share (for CASH), or the CVR/earnout value per share when stated
- exchange_ratio: for ACQUIRER_STOCK, shares of acquirer stock per share of target stock (e.g., 0.4552)
- currency: ISO 4217 currency code (USD most common)
- trigger_description: for CVR/earnout, brief description of the trigger condition (≤200 chars)

AGGREGATE AMOUNT
- amount: the aggregate/total dollar amount of this component, when the source states one directly — e.g. a stated "Base Purchase Price," "Closing Consideration," or aggregate cash figure. Distinct from per_share_amount. Only capture what the source states directly as a headline/aggregate/base figure. Never calculate this by multiplying a per-share amount by a share count, and never derive a final number from an adjustment or true-up formula (e.g., working capital, indebtedness, or net-asset adjustments) — if the source states only an adjustment mechanism and not a base figure, leave amount null and describe the mechanism in notes.

CONSERVATIVE COMPONENT RULE
Only create a component when the source states a specific form and at least one of: a per-share amount, an aggregate amount, an exchange ratio, or (for CVR/EARNOUT/CONTINGENT_CONSIDERATION) a defined trigger or mechanism. Do not create a component for a vague or incidental mention with no stated terms — e.g., an earn-out or milestone payment whose terms are not specified in the excerpt, or a generic reference to assumed liabilities with no stated amount. Describe such mentions in notes instead of manufacturing a component for them.

NO INVENTED VALUES
Never invent a dollar amount, per-share price, or exchange ratio. If the source states only a share count and/or an ownership percentage — with no dollar value, no per-share price, and no exchange ratio — leave per_share_amount, amount, and exchange_ratio null on that component and describe the share count/ownership fact in notes.

Do not infer, calculate, or restate transaction value or enterprise value. Your job is limited to the consideration components as the source states them.

ELECTION MECHANICS
When target shareholders have an election (cash or stock or mixed):
- Include each electable form as a separate component
- Set "election": true on each electable component
- Note any proration mechanics in the notes field

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": 42.00, "amount": null, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false},
    {"form": "CVR", "per_share_amount": 5.00, "amount": null, "currency": "USD", "exchange_ratio": null, "trigger_description": "FDA approval of XB-100 by December 31, 2027", "election": false}
  ],
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
| `consideration_components[].form` | enum | CASH \| ACQUIRER_STOCK \| TARGET_STOCK \| EARNOUT \| CVR \| CONTINGENT_CONSIDERATION \| DEBT_ASSUMED \| RETAINED_EQUITY \| OTHER — matches `docs/v3_data_dictionary.md` §4 exactly |
| `consideration_components[].per_share_amount` | number\|null | Per-share cash value |
| `consideration_components[].amount` | number\|null | Aggregate/total dollar amount for this component, only when explicitly stated by the source (e.g. a stated Base Purchase Price or Closing Consideration) — never calculated |
| `consideration_components[].currency` | string\|null | ISO 4217 |
| `consideration_components[].exchange_ratio` | number\|null | For ACQUIRER_STOCK: acquirer shares per target share |
| `consideration_components[].trigger_description` | string\|null | For CVR/EARNOUT/CONTINGENT_CONSIDERATION |
| `consideration_components[].election` | boolean | True when shareholder can elect this form |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Proration mechanics, adjustment/true-up qualifications, vague/unspecified mentions not captured as a component, caveats (≤200 chars) |

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
    {"form": "CASH", "per_share_amount": 52.00, "amount": null, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false}
  ],
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
    {"form": "CASH", "per_share_amount": 28.00, "amount": null, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false},
    {"form": "CVR", "per_share_amount": 4.50, "amount": null, "currency": "USD", "exchange_ratio": null, "trigger_description": "Achievement of Net Sales Milestone by December 31, 2028", "election": false}
  ],
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
    {"form": "ACQUIRER_STOCK", "per_share_amount": null, "amount": null, "currency": null, "exchange_ratio": 0.4235, "trigger_description": null, "election": false}
  ],
  "model_confidence": "HIGH",
  "notes": "Cash paid in lieu of fractional shares at VWAP; no fixed per-share cash amount stated."
}
```

### Example 4 — Stated aggregate base price, subject to adjustment

**Input section text:**
```
Section 2.5 Purchase Price. The aggregate purchase price for the Company shall be the Base Purchase Price of $62,000,000 in cash, plus or minus the amount by which the Estimated Tangible Book Value as of the Closing Date is greater or less than the Target Tangible Book Value, in each case as determined pursuant to the post-Closing adjustment procedures set forth in Section 2.6.
```

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": null, "amount": 62000000, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false}
  ],
  "model_confidence": "HIGH",
  "notes": "Base Purchase Price, subject to post-closing adjustment based on Estimated vs. Target Tangible Book Value per Section 2.6; adjusted figure not calculated here."
}
```

### Example 5 — Vague/unspecified references do not become components

**Input section text:**
```
Section 2.1 Closing Consideration. At the Closing, Buyer shall pay to Seller aggregate consideration of $111,000,000 in cash (the "Closing Consideration"). In addition, Buyer shall assume the Assumed Liabilities and shall make the Milestone Payments described in Section 2.09 upon achievement of the applicable milestones.
```
*(Section 2.09 and the definition of Assumed Liabilities are not included in this excerpt — no amounts are stated for either.)*

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "CASH", "per_share_amount": null, "amount": 111000000, "currency": "USD", "exchange_ratio": null, "trigger_description": null, "election": false}
  ],
  "model_confidence": "HIGH",
  "notes": "Aggregate Closing Consideration of $111,000,000 cash. Also references Milestone Payments (Section 2.09) and assumption of Assumed Liabilities; neither is stated with a specific amount or trigger in this excerpt, so neither is captured as a separate component."
}
```

### Example 6 — Contingent post-closing adjustment (holdback), not an earnout or CVR

**Input section text:**
```
Section 2.3 Closing Payments. At the Closing, Acquirer shall pay the Estimated Closing Cash Consideration, less the Purchase Price Adjustment Holdback of $10,000,000. Following the final determination of the Closing Statement pursuant to Section 2.4, (a) if the Final Adjustment Amount is positive, Acquirer shall release the Purchase Price Adjustment Holdback to Seller, or (b) if the Final Adjustment Amount is negative, the Purchase Price Adjustment Holdback shall be applied against amounts owed to Acquirer.
```

**Expected output:**
```json
{
  "consideration_components": [
    {"form": "CONTINGENT_CONSIDERATION", "per_share_amount": null, "amount": 10000000, "currency": "USD", "exchange_ratio": null, "trigger_description": "Release or application of the Purchase Price Adjustment Holdback based on the Final Adjustment Amount determined under Section 2.4", "election": false}
  ],
  "model_confidence": "HIGH",
  "notes": "Holdback is a post-closing true-up mechanism, not a milestone-based earnout or a rights instrument (CVR)."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| No per-share amount found, but a stated aggregate/base/headline figure is present | Set per_share_amount null; capture the stated figure in `amount`. Never calculate one from an adjustment formula |
| No per-share amount and no stated aggregate figure found | Set both per_share_amount and amount null; note context in notes |
| Section describes treatment of options/RSUs without stating common stock consideration | Return empty array; model_confidence NONE |
| Election mechanics with proration pools | Include each electable form; describe proration in notes |
| Vague/unspecified consideration reference (undefined milestone terms, generic assumed liabilities with no stated amount) | Do not create a component; describe in notes |
| Debt assumption referenced without a specific stated amount | Do not use DEBT_ASSUMED; describe in notes |
| Contingent purchase-price adjustment that is not an earnout or CVR (e.g., a holdback/true-up) | Use CONTINGENT_CONSIDERATION |
| Consideration stated only as a share count and/or ownership percentage, with no dollar value, per-share price, or exchange ratio | Leave per_share_amount, amount, and exchange_ratio null; describe the share count/ownership fact in notes. Never invent a dollar value |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.3 | 2026-09-02 | **V3 alignment** (see `logs/agreement_baseline_20260901/consideration_v3_alignment_review.md`). (1) Added `amount` — an aggregate/total dollar figure per component, distinct from `per_share_amount`; named explicitly in `docs/v3_data_dictionary.md` §4's consideration-component definition. The MVP write path already supported this key in `stages/agreement_extract.py`'s compound-observation loop before this change; only the prompt was not asking for it, so it had never fired. Resolves Velocity Financial's and Sangamo's real regression cases, where a stated aggregate ("Base Purchase Price," "Closing Consideration") had nowhere to go but `notes`. (2) Removed `per_share_price_total` — redundant with what is derivable from the components, contradicts the "derive, don't dual-author" principle in V3's Consideration section, has no live downstream consumer (`_apply_consideration` never read it), and was not reliably populated even under 0.2. (3) Aligned the `form` enum exactly to `docs/v3_data_dictionary.md` §4 (adds `CONTINGENT_CONSIDERATION`, restores `TARGET_STOCK`/`OTHER` consistently) and resolved the prior internal mismatch between this section and §6, which arose because only §4 is ever sent to the model (`prompts/base.py`'s `load_prompt_file()`) — §6 had silently drifted since 0.1. (4) Tightened `DEBT_ASSUMED`: requires an explicit stated dollar amount of financial indebtedness; a generic "Assumed Liabilities" reference is no longer eligible (found misapplied on Sangamo's real regression output). De-emphasized as one value among nine rather than a default landing spot. No inference or calculation of TV/EV. (5) Added the conservative-component rule: a vague or unspecified mention (undefined milestone terms, generic assumed liabilities with no amount) stays in `notes` rather than becoming a manufactured component — resolves the Sangamo case fully alongside (4). (6) Made explicit the existing no-invention behavior for share-only/ownership-only consideration (Volato, Black Spade) and the stated-aggregate-never-calculated rule (Victory Capital). No MVP stage/script changes were required for any of the above — the write path (`_write_observations`'s compound consideration block, `_apply_consideration`) already handled every field involved. |
