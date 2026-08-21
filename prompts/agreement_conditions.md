# Agreement Conditions to Closing Extraction Prompt

**Version:** 0.2 (provenance is caller-owned)
**Repo path:** `prompts/agreement_conditions.md`

---

## 1. Purpose

Extract structured closing conditions from the CONDITIONS_TO_CLOSING section of a deal document. Captures MAC clause presence, shareholder vote requirements, and a brief summary of the top conditions.

Runs in Stage 11 (agreement_extract) for each CONDITIONS_TO_CLOSING section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

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
You are extracting closing conditions from the Conditions to Closing section of a merger agreement or proxy statement.

MAC CLAUSE

A Material Adverse Change (MAC) or Material Adverse Effect (MAE) clause conditions closing on the absence of a material adverse change to the target's business. Identify:
- has_mac_clause: true when any closing condition references a MAC, MAE, or Material Adverse Change/Effect

Common indicators:
- "no Material Adverse Effect shall have occurred"
- "there shall not have occurred or exist any Material Adverse Change"
- "the representations and warranties ... shall be true and correct in all material respects" (this alone does not create a MAC condition; look for explicit MAC language)

SHAREHOLDER VOTE

- requires_target_shareholder_vote: true when target shareholder approval is a closing condition
- target_vote_threshold: the required approval level:
  - MAJORITY_OUTSTANDING: majority of all outstanding shares (e.g., "majority of the outstanding shares entitled to vote")
  - TWO_THIRDS: two-thirds supermajority of votes cast or outstanding
  - MAJORITY_VOTING: majority of votes actually cast at the meeting (simple majority of quorum)
  - OTHER: any other threshold (describe in notes)
  - null: shareholder vote not required or threshold not stated

CLOSING CONDITIONS SUMMARY

Provide a brief 2-4 sentence summary of the top closing conditions. Focus on substantive conditions only — omit purely procedural ones (e.g., "certificates shall have been executed"). Common top conditions:
- Target shareholder approval
- Required regulatory approvals (HSR, CFIUS, foreign competition filings)
- No MAC condition
- Material accuracy of representations and warranties at closing
- Compliance with covenants
- No injunction or legal prohibition

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "has_mac_clause": true,
  "requires_target_shareholder_vote": true,
  "target_vote_threshold": "MAJORITY_OUTSTANDING",
  "closing_conditions_summary": "Target shareholder approval (majority of outstanding shares). HSR clearance and CFIUS approval required. No material adverse effect on target at closing. Accuracy of representations and warranties in all material respects.",
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for optional fields that have no value.
```

---

## 5. User Prompt Template

```
Extract closing conditions from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `has_mac_clause` | boolean | True when closing conditioned on absence of MAC/MAE |
| `requires_target_shareholder_vote` | boolean\|null | null when not determinable from this section |
| `target_vote_threshold` | enum\|null | MAJORITY_OUTSTANDING \| TWO_THIRDS \| MAJORITY_VOTING \| OTHER \| null |
| `closing_conditions_summary` | string\|null | 2-4 sentence plain-text summary of top conditions |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Caveats, unusual conditions (≤200 chars) |

---

## 7. Few-Shot Examples

### Example 1 — Standard public company merger conditions

**Input section text:**
```
ARTICLE VI
CONDITIONS TO CLOSING

Section 6.1 Conditions to Obligations of Each Party. The obligations of each of the parties to effect the Merger are subject to the satisfaction or waiver of the following conditions:

(a) Stockholder Approval. The Company Stockholder Approval shall have been obtained.

(b) Regulatory Approvals. Any waiting period applicable to the consummation of the Merger under the HSR Act shall have expired or been earlier terminated, and all required clearances from foreign competition authorities shall have been obtained.

(c) No Injunction. No governmental entity shall have issued any order, injunction, judgment, or decree that is in effect and that prevents the consummation of the Merger.

Section 6.2 Additional Conditions to Parent's and Merger Sub's Obligations.

(d) Representations True. The representations and warranties of the Company shall be true and correct as of the Closing Date as though made on and as of such date.

(e) No Material Adverse Effect. No Material Adverse Effect shall have occurred with respect to the Company since the date of this Agreement.
```

**Expected output:**
```json
{
  "has_mac_clause": true,
  "requires_target_shareholder_vote": true,
  "target_vote_threshold": "MAJORITY_OUTSTANDING",
  "closing_conditions_summary": "Target stockholder approval required. HSR clearance and foreign competition regulatory approvals required. No Material Adverse Effect on target at closing. Accuracy of target representations and warranties at closing date.",
  "model_confidence": "HIGH",
  "notes": null
}
```

### Example 2 — Tender offer structure with CFIUS

**Input section text:**
```
Section 7.1 Conditions to the Offer. Notwithstanding any other provision of this Agreement, Purchaser shall not be obligated to accept for payment, or pay for, any shares of Common Stock, and may delay the acceptance for payment of, or payment for, any tendered shares, if at the Acceptance Time:

(i) the Minimum Condition is not satisfied — a majority of the then outstanding shares of Common Stock entitled to vote on a merger having been validly tendered and not validly withdrawn;

(ii) any governmental authority of competent jurisdiction has enacted any applicable law that makes the Offer or the Merger illegal;

(iii) the CFIUS Approval has not been obtained;

(iv) a Material Adverse Effect has occurred with respect to the Company since the date of this Agreement and is continuing.
```

**Expected output:**
```json
{
  "has_mac_clause": true,
  "requires_target_shareholder_vote": true,
  "target_vote_threshold": "MAJORITY_OUTSTANDING",
  "closing_conditions_summary": "Minimum tender condition: majority of outstanding shares tendered. CFIUS approval required. No Material Adverse Effect on target. No legal prohibition on consummation.",
  "model_confidence": "HIGH",
  "notes": "Tender offer structure; minimum condition serves as shareholder approval mechanism."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section is a preamble list without substantive condition text | Return model_confidence MEDIUM or LOW; summary based on condition headings |
| Vote threshold stated in a different section of the document | Return target_vote_threshold null; note that threshold may be elsewhere |
| No explicit MAC language but representations condition present | has_mac_clause = false; pure R&W accuracy condition is not a MAC |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — MAC clause, shareholder vote, conditions summary |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
