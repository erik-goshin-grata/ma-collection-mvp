# Agreement Conditions to Closing Extraction Prompt

**Version:** 0.3
**Repo path:** `prompts/agreement_conditions.md`

---

## 1. Purpose

Extract whether the closing conditions call out a specific regulatory approval
requirement, from the CONDITIONS_TO_CLOSING section of a deal document.

Runs in Stage 11 (agreement_extract) for each CONDITIONS_TO_CLOSING section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 512

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
You are extracting whether a deal's closing conditions call out a specific regulatory approval requirement, from the Conditions to Closing section of a merger agreement or proxy statement.

REGULATORY APPROVALS

regulatory_approvals_required: true when the closing conditions call out a specific regulatory approval or clearance by naming the regime or process — e.g., antitrust/HSR Act, CFIUS, foreign competition/foreign-investment approvals, or a sector-specific regulator (FINRA, FCA, insurance-law consents, banking, etc.). false when the section states closing conditions and no such specific regime is named — including when the condition refers only generically to "required governmental consents/approvals" without naming which regime, or defers to an unincluded schedule for the list. null when the section does not state enough about closing conditions to determine either way.

Stock-exchange listing approval (NYSE/Nasdaq) alone does not qualify — that is a listing requirement, not a regulatory approval regime.

A cross-reference to an unincluded Disclosure Schedule for "required governmental consents" or "required regulatory approvals" is not, by itself, a named regime — do not infer HSR, CFIUS, or any other specific regime from a generic or schedule-deferred reference.

Named approvals may be summarized in notes. Do not introduce a separate approval list or taxonomy.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "regulatory_approvals_required": true,
  "model_confidence": "HIGH",
  "notes": "HSR clearance and CFIUS approval required."
}

All fields are required. Use null for optional fields that have no value.
```

---

## 5. User Prompt Template

```
Extract the regulatory-approval flag from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `regulatory_approvals_required` | boolean\|null | True only when a specific regime/regulator is named; null when not determinable from this section |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Named regimes/regulators, caveats (≤200 chars) |

---

## 7. Few-Shot Examples

### Example 1 — Specific regimes named (HSR + sector regulator)

**Input section text:**
```
Section 7.1 Conditions to Obligations of Each Party.
(a) the Stockholder Approval shall have been obtained.
(b) any waiting period (and any extension thereof) applicable to the consummation of the transactions contemplated by this Agreement under the HSR Act shall have terminated or expired.
(d) all consents required under the Insurance Laws set forth on Section 7.1(d) of the Company Disclosure Schedule shall have been obtained; and
(e) FINRA shall have delivered to the Broker-Dealer Subsidiary its written approval of the Continuing Membership Application, or the parties shall have proceeded pursuant to FINRA Rule 1017 without such approval.
```

**Expected output:**
```json
{
  "regulatory_approvals_required": true,
  "model_confidence": "HIGH",
  "notes": "HSR Act clearance, consents under the Insurance Laws, and FINRA approval of the Continuing Membership Application."
}
```

### Example 2 — Generic, schedule-deferred governmental consents (no regime named)

**Input section text:**
```
(b) Required Governmental Consents. The consents or approvals of Governmental Authorities as set forth on Section 9.01(b) of the Company Disclosure Schedule shall have been obtained and shall remain in full force and effect.
(c) Registration Statement. The Registration Statement shall have become effective under the Securities Act and no stop order shall have been issued.
```

**Expected output:**
```json
{
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": "Only a schedule-deferred reference to 'Required Governmental Consents'; no specific regime is named in this excerpt."
}
```

### Example 3 — No approval-related condition at all

**Input section text:**
```
(a) No injunction or order of any court of competent jurisdiction enjoining, prohibiting, or rendering illegal the consummation of the Closing shall be in force.
(b) The representations and warranties of the Company shall be true and correct in all material respects as of the Closing Date.
(c) The Company shall have performed in all material respects its covenants under this Agreement.
```

**Expected output:**
```json
{
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": null
}
```

### Example 4 — Exchange listing approval only (does not qualify)

**Input section text:**
```
(c) NYSE Listing. The New York Stock Exchange shall have approved the application of the Contributor to be substituted as the listed company in place of Legacy Parent, and such substitution listing shall have become effective, subject only to official notice of issuance.
```

**Expected output:**
```json
{
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": "Only NYSE listing/substitution approval is stated; that is an exchange-listing requirement, not a regulatory approval regime."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section refers only to a Disclosure Schedule for "required consents/approvals" with no regime named | `regulatory_approvals_required = false` |
| Section names only an exchange-listing approval (NYSE/Nasdaq) | `regulatory_approvals_required = false` |
| Section is a preamble list without substantive condition text | Return model_confidence MEDIUM or LOW; `regulatory_approvals_required = null` if not determinable |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — MAC clause, shareholder vote, conditions summary |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.3 | 2026-09-02 | **V3 alignment** (see `logs/agreement_baseline_20260901/conditions_v3_alignment_review.md`). Dropped `has_mac_clause`, `requires_target_shareholder_vote`, `target_vote_threshold`, and `closing_conditions_summary` — none has a V3 concept, and V3's one adjacent concept (the derived transaction summary, §10) explicitly rules out a source-authored narrative being the first place a fact appears. Added `regulatory_approvals_required`, aligned to `docs/v3_data_dictionary.md` §1's existing FLAG (also collected, with identical semantics, by the historical funding LC path — same field, same reading, two collection points). True only when a specific regime/regulator is named; a generic or schedule-deferred "required governmental consents/approvals" reference, or exchange-listing approval alone, is not sufficient. `has_go_shop` stays with `agreement_termination`; `competing_bid` is not added here. |
