# Agreement Recitals Extraction Prompt

**Version:** 0.2
**Repo path:** `prompts/agreement_recitals.md`

---

## 1. Purpose

Extract structured party information from the RECITALS or preamble section of a deal document. Identifies all named parties, distinguishes the ultimate parent acquirer from any Merger Sub / acquisition vehicle, and determines the merger structure.

Runs in Stage 11 (agreement_extract) for each RECITALS section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 1024

---

## 3. Input Schema

```json
{
  "section_text": "...",
  "prompt_version": "agreement_recitals:0.1"
}
```

---

## 4. System Prompt

```
You are extracting structured party information from the recitals or preamble section of a merger agreement, proxy statement, or tender offer document.

PARTY IDENTIFICATION

The recitals name all parties. Common patterns:

Three-party structure (most common in modern public M&A):
- "Parent" or "Acquirer" — the ultimate parent company (the real buyer)
- "Merger Sub" or "Acquisition Sub" — wholly-owned shell formed solely for the merger
- "Company" or "Target" — the target company

Language that identifies a Merger Sub:
- "wholly-owned subsidiary of [Parent]"
- "newly formed for the purpose of the [Merger/Acquisition]"
- "formed solely to effectuate the transactions contemplated"
- Names like "[X] Acquisition Corp.", "[X] Merger Sub, Inc.", "Project [Codename] Merger Sub"

DEMOTE the Merger Sub from the acquirer field:
- parent_acquirer_name = the ultimate Parent (the real acquirer)
- merger_sub_name = the shell entity (when present; null otherwise)
- target_name = the target company

Two-party structure (direct merger, no Merger Sub):
- acquirer and target only
- merger_sub_name = null
- merger_structure = DIRECT

MERGER STRUCTURE DETERMINATION

Identify from the recitals language:
- DIRECT: "Target shall merge with and into Acquirer" (no Merger Sub; Acquirer survives)
- FORWARD_TRIANGULAR: "Target shall merge with and into Merger Sub" (Merger Sub survives, Target disappears)
- REVERSE_TRIANGULAR: "Merger Sub shall merge with and into Target" (Target survives — most common in modern public M&A)
- TENDER_OFFER: tender offer mechanics described; often combined with a subsequent second-step merger

If the merger mechanism cannot be determined from this section, return null for merger_structure. Do not return "UNKNOWN" — null means no observation, "UNKNOWN" is not a valid value.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "parent_acquirer_name": "Acme Corporation",
  "merger_sub_name": "Project Alpha Merger Sub, Inc.",
  "target_name": "Beta Industries, Inc.",
  "merger_structure": "REVERSE_TRIANGULAR",
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "agreement_recitals:0.1"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
Extract party and structure information from the following deal document section.

SECTION TEXT:
{section_text}

prompt_version: {prompt_version}
```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `parent_acquirer_name` | string | Ultimate parent acquirer (not Merger Sub) |
| `merger_sub_name` | string\|null | Acquisition vehicle / shell entity when present |
| `target_name` | string | The company being acquired |
| `merger_structure` | enum\|null | DIRECT \| FORWARD_TRIANGULAR \| REVERSE_TRIANGULAR \| TENDER_OFFER \| null (not determinable) |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Ambiguities, caveats (≤200 chars) |
| `prompt_version` | string | Echoed from input |

---

## 7. Few-Shot Examples

### Example 1 — Reverse triangular (most common)

**Input section text:**
```
AGREEMENT AND PLAN OF MERGER

dated as of April 10, 2026

among

GLOBALTECH CORPORATION, a Delaware corporation ("Parent"),

GLOBALTECH ACQUISITION CORP., a Delaware corporation and a wholly-owned subsidiary of Parent ("Merger Sub"),

and

DELTA SYSTEMS, INC., a Delaware corporation (the "Company").

WHEREAS, the parties hereto desire to effect a business combination through a merger of Merger Sub with and into the Company upon the terms and subject to the conditions of this Agreement (the "Merger"), with the Company surviving the Merger as a wholly-owned subsidiary of Parent;
```

**Expected output:**
```json
{
  "parent_acquirer_name": "GlobalTech Corporation",
  "merger_sub_name": "GlobalTech Acquisition Corp.",
  "target_name": "Delta Systems, Inc.",
  "merger_structure": "REVERSE_TRIANGULAR",
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "agreement_recitals:0.1"
}
```

### Example 2 — Tender offer structure

**Input section text:**
```
THIS AGREEMENT AND PLAN OF MERGER (this "Agreement") is entered into as of March 1, 2026, by and among:

NORTHSTAR CAPITAL PARTNERS LP ("Parent"), a Delaware limited partnership,

NSC ACQUISITION INC., a Delaware corporation and a wholly-owned subsidiary of Parent ("Purchaser"), and

REDWOOD FINANCIAL GROUP, INC. (the "Company"), a Delaware corporation.

WHEREAS, upon the terms and conditions of this Agreement, Purchaser will commence a tender offer (the "Offer") to purchase all of the outstanding shares of Company Common Stock at a price of $34.00 per share in cash, followed by a Merger in which Purchaser shall merge with and into the Company.
```

**Expected output:**
```json
{
  "parent_acquirer_name": "NorthStar Capital Partners LP",
  "merger_sub_name": "NSC Acquisition Inc.",
  "target_name": "Redwood Financial Group, Inc.",
  "merger_structure": "TENDER_OFFER",
  "model_confidence": "HIGH",
  "notes": "Tender offer followed by back-end merger; Purchaser merges into Company (reverse triangular back-end).",
  "prompt_version": "agreement_recitals:0.1"
}
```

### Example 3 — Two-party direct merger (no Merger Sub)

**Input section text:**
```
AGREEMENT AND PLAN OF MERGER

This Agreement and Plan of Merger ("Agreement") is entered into as of February 15, 2026, between APEX HOLDINGS, INC., a Nevada corporation ("Acquirer"), and SUMMIT CORP., a Nevada corporation (the "Company").

WHEREAS, the Board of Directors of each of Acquirer and the Company has approved the merger of the Company with and into Acquirer, with Acquirer surviving the merger as the surviving entity.
```

**Expected output:**
```json
{
  "parent_acquirer_name": "Apex Holdings, Inc.",
  "merger_sub_name": null,
  "target_name": "Summit Corp.",
  "merger_structure": "DIRECT",
  "model_confidence": "HIGH",
  "notes": "Two-party structure; Company merges into Acquirer, Acquirer survives.",
  "prompt_version": "agreement_recitals:0.1"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section contains only boilerplate (page header, no party recitals) | Return all party fields and merger_structure null; model_confidence = NONE |
| Merger Sub and Parent names are similar (easy to confuse) | Look for "wholly-owned subsidiary of" language to identify the Sub |
| Multiple Merger Subs named (step-merger structures) | Use the first / primary Merger Sub; note others in `notes` |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — party identification + merger structure |
| 0.2 | 2026-05-05 | Remove UNKNOWN from merger_structure; null = not determinable |
