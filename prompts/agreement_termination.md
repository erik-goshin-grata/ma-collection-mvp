# Agreement Termination Fees Extraction Prompt

**Version:** 0.3
**Repo path:** `prompts/agreement_termination.md`

---

## 1. Purpose

Extract termination fee structure from the TERMINATION_FEES section of a deal document. Captures target and acquirer (reverse) termination fees, their amounts, percentages, and go-shop provisions.

Runs in Stage 11 (agreement_extract) for each TERMINATION_FEES section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

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
You are extracting termination fee and go-shop data from the Termination or Termination Fees section of a merger agreement.

TERMINATION FEES

Two fees are typically present:

1. Target/Company Termination Fee (also called "Company Termination Fee", "Break-Up Fee", or "Fiduciary Out Fee"):
   - Paid BY the target TO the acquirer
   - Triggered when the target's board changes its recommendation or the target accepts a superior proposal
   - Typically 2-4% of equity value in public M&A

2. Acquirer/Parent Termination Fee (also called "Reverse Termination Fee", "Parent Termination Fee"):
   - Paid BY the acquirer TO the target
   - Triggered when the acquirer cannot obtain financing or fails to close for regulatory reasons
   - Typically equal to or larger than the company fee (often 2-4x)

Extract amounts in the currency stated (usually USD). Extract percentages when explicitly stated as a % of equity value, transaction value, or deal value.

GO-SHOP PROVISIONS

A go-shop provision allows the target to actively solicit competing bids for a specified period after signing. Identify:
- has_go_shop: true if there is a go-shop period described in this section
- go_shop_period_days: the number of days of the go-shop period (e.g., 30, 45)

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "target_termination_fee": 50000000,
  "target_termination_fee_currency": "USD",
  "target_termination_fee_pct": 2.5,
  "acquirer_termination_fee": 100000000,
  "acquirer_termination_fee_currency": "USD",
  "acquirer_termination_fee_pct": null,
  "has_go_shop": false,
  "go_shop_period_days": null,
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for amounts/percentages when not stated.
```

---

## 5. User Prompt Template

```
Extract termination fee and go-shop information from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `target_termination_fee` | number\|null | Amount paid by target to acquirer on fiduciary out |
| `target_termination_fee_currency` | string\|null | ISO 4217 |
| `target_termination_fee_pct` | number\|null | As % of equity value when stated |
| `acquirer_termination_fee` | number\|null | Reverse termination fee paid by acquirer to target |
| `acquirer_termination_fee_currency` | string\|null | ISO 4217 |
| `acquirer_termination_fee_pct` | number\|null | As % of equity value when stated |
| `has_go_shop` | boolean | True when a go-shop period is described |
| `go_shop_period_days` | integer\|null | Days in go-shop period |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Trigger conditions, caveats (≤200 chars) |

---

## 7. Few-Shot Examples

### Example 1 — Standard break-up and reverse termination fee

**Input section text:**
```
Section 7.3 Company Termination Fee. In the event this Agreement is terminated by Parent pursuant to Section 7.1(e) (Company Board Recommendation Change) or by the Company pursuant to Section 7.1(f) (Superior Proposal), then the Company shall pay to Parent a fee equal to $85,000,000 (the "Company Termination Fee") within two (2) Business Days of such termination.

Section 7.4 Parent Termination Fee. In the event this Agreement is terminated by the Company pursuant to Section 7.1(g) (Financing Failure) or by either party due to failure to obtain Regulatory Approval, then Parent shall pay to the Company a fee equal to $170,000,000 (the "Parent Termination Fee") within two (2) Business Days of such termination. The parties agree that the Company Termination Fee represents approximately 2.3% of the implied equity value of the transaction.
```

**Expected output:**
```json
{
  "target_termination_fee": 85000000,
  "target_termination_fee_currency": "USD",
  "target_termination_fee_pct": 2.3,
  "acquirer_termination_fee": 170000000,
  "acquirer_termination_fee_currency": "USD",
  "acquirer_termination_fee_pct": null,
  "has_go_shop": false,
  "go_shop_period_days": null,
  "model_confidence": "HIGH",
  "notes": "Company fee (2.3% of equity value) stated explicitly. Parent fee is 2x company fee; no pct stated."
}
```

### Example 2 — Go-shop with reduced fiduciary out fee

**Input section text:**
```
Section 8.3 Termination Fees.

(a) Go-Shop Period. During the period beginning on the date of this Agreement and ending at 11:59 p.m. Eastern time on the date that is 35 days after the date of this Agreement (the "Go-Shop Period"), the Company and its subsidiaries and their respective Representatives may initiate, solicit and encourage any inquiry or proposal that could lead to an Acquisition Proposal.

(b) Company Termination Fee. In connection with a termination pursuant to Section 8.1(d)(i) (Fiduciary Out) resulting from an Excluded Party, the Company shall pay a reduced termination fee equal to $22,000,000 (the "Go-Shop Termination Fee"). For all other terminations under Section 8.1(d)(i), the Company shall pay $44,000,000 (the "Standard Termination Fee").

(c) Parent Termination Fee. Parent shall pay the Company a fee of $60,000,000 in connection with a termination by the Company pursuant to Section 8.1(e) (Financing Failure).
```

**Expected output:**
```json
{
  "target_termination_fee": 44000000,
  "target_termination_fee_currency": "USD",
  "target_termination_fee_pct": null,
  "acquirer_termination_fee": 60000000,
  "acquirer_termination_fee_currency": "USD",
  "acquirer_termination_fee_pct": null,
  "has_go_shop": true,
  "go_shop_period_days": 35,
  "model_confidence": "HIGH",
  "notes": "Reduced go-shop fee ($22M) applies for Excluded Parties; standard fee ($44M) used as primary. Parent fee for financing failure."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section describes only triggers (§7.1) without fee amounts | Return fee amounts null; model_confidence MEDIUM or LOW |
| Fee stated as multiple of another fee ("2x the Company Termination Fee") without also stating that fee's own dollar amount | Return the multiple-based fee amount null and note the stated multiple; never compute a dollar figure from it |
| Non-USD currency | Extract as stated; populate currency field appropriately |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — termination fees + go-shop |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.3 | 2026-09-02 | **Documentation-only correction** (V3 alignment review — see `logs/agreement_baseline_20260901/termination_v3_alignment_review.md`; the six named fields, the write path, and both currency fields are otherwise unchanged and confirmed already aligned). §8's "Fee stated as multiple of another fee" row previously instructed computing an implied dollar amount from a stated multiple — this line was never actually sent to the model (`load_prompt_file()` sends only §4/§5), so it never changed live behavior, but it contradicted `docs/v3_data_dictionary.md`'s treatment of both fee amount and percentage as Collected, not Derived. Corrected to return null and note the stated multiple instead, matching the row above it. No change to §4, §5, §6, §7, or `stages/agreement_extract.py`. |
