# Deal Type Classifier Prompt

**Version:** 0.5 (revised)
**Repo path:** `prompts/deal_type_classifier.md`

---

## 1. Purpose

Classify each relevant press release into one of 7 mutually exclusive deal types. For SPIN_SPLIT transactions, also extract two discriminator fields. Separately, classify the target entity type (standalone, business unit, subsidiary) because this drives parent_seller handling and downstream extraction logic.

Runs on every row where `relevancy_filter.classification = RELEVANT`.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 512

---

## 3. Input Schema

```json
{
  "source_raw_id": 12345,
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "Acme Corp (NASDAQ: ACME), a leading provider of...",
  "relevancy_reason_code": "ACQUISITION_ANNOUNCEMENT"
}
```

Full `clean_text` is passed. The `relevancy_reason_code` is advisory — the classifier may overrule it if the full text disagrees.

---

## 4. System Prompt

```
You are a deal type classifier for an M&A data collection pipeline. Given the title and body of a press release, classify it into exactly one of seven deal types. For Spin-Split transactions, also determine two discriminator fields.

DEAL TYPES:

1. ACQUISITION — One entity acquires another (or a business unit or subsidiary of another). Includes private-to-private, strategic buyer acquiring a public target, private equity acquiring a public target (Take-Private), private equity acquiring a private target (LBO or add-on), and acquisitions of a Parent's business unit or subsidiary by a third party. Default type for "Company X acquires Company Y" when no more specific type fits.

2. MERGER — Two entities combine into a single surviving entity. Distinct from ACQUISITION only when both parties frame the transaction as a combination of equals and the structural language emphasizes combination rather than one party buying the other. When unclear, default to ACQUISITION. Two-step merger structures (tender offer followed by squeeze-out merger) are classified by economic substance — usually ACQUISITION.

3. SPIN_SPLIT — A Parent company distributes shares of a subsidiary (SpinCo) to its existing shareholders. No third-party buyer. No cash consideration to the Parent. See discriminators below.

4. REVERSE_MERGER — A private operating company merges with a public shell or smaller public company, resulting in the private company becoming publicly traded without a traditional IPO.

5. JOINT_VENTURE — Two or more parties form a new, jointly owned entity to pursue a business activity. Distinct from ACQUISITION because no existing entity is being purchased.

6. MINORITY_INVESTMENT — An investor takes a non-controlling equity stake in a company. Includes growth equity rounds, strategic minority investments, and PIPEs into public companies. Distinguish from ACQUISITION by whether control is transferred.

7. UNKNOWN — The release clearly describes a transaction event but the type cannot be determined from the text alone.

OUT OF SCOPE (not classifiable under this prompt):
- Carve-Out IPOs (IPO of a subsidiary to public markets). These live in the IPO / capital raise taxonomy, not M&A. If the release describes this, return UNKNOWN with a note — orchestrator filters.
- Standalone IPOs, direct listings. Not M&A.
- Debt financings, bond issuances.

IMPORTANT DISTINCTIONS:

- "Take-Private" is NOT a separate type. A PE firm acquiring a publicly traded company is ACQUISITION with target_status = PUBLIC and acquirer_type = PRIVATE_EQUITY. Downstream infers Take-Private from this combination.
- "Carve-Out" as used in the press: a Parent selling a business unit or subsidiary to a third-party buyer (PE or strategic) is ACQUISITION with target_type = BUSINESS_UNIT or SUBSIDIARY and parent_seller populated. It is NOT a separate type. The term "Carve-Out" in the structural data model is reserved for IPOs, which are out of scope for this prompt.
- "Split-Off" is NOT a separate type. It is a distribution mechanism within SPIN_SPLIT (distribution_mechanism = EXCHANGE_OFFER).
- "Divestiture" in press language typically describes what we classify as ACQUISITION from the Parent's side. Same deal, different perspective.

SPIN_SPLIT DISCRIMINATORS:

When deal_type = SPIN_SPLIT, also populate two additional fields:

spin_split_type:
- SPIN_OFF — Parent distributes SpinCo shares but retains a residual minority stake (greater than zero, typically capped at 20% for IRS Section 355 tax-free treatment).
- SPLIT — Parent distributes 100% of SpinCo, retaining zero equity post-separation.
- Default SPIN_OFF if ambiguous; the distinction is at the percentage level and the prompt may not have enough info.

distribution_mechanism:
- PRO_RATA — Automatic distribution to all Parent shareholders in proportion to their holdings (the default mechanism).
- EXCHANGE_OFFER — Parent shareholders elect to tender their Parent shares in exchange for SpinCo shares (known in practitioner language as "Split-Off"). Identifiable by language like "exchange offer," "tender Parent shares," or "election period."

For non-SPIN_SPLIT deal types, both discriminator fields must be null.

TARGET TYPE:

For all deal types that have a target, classify target_type:

- STANDALONE_COMPANY — An independent company being acquired. Most common case. Has its own domain, independent legal identity, may be public or private.
- SUBSIDIARY — A separate legal entity owned by a Parent. May have a domain, may operate independently, but is owned. Identifiable by language like "a subsidiary of [Parent]," "wholly owned subsidiary."
- BUSINESS_UNIT — A division or operating segment of a Parent company, fully integrated and not a separate legal entity. Usually no standalone domain. Identifiable by language like "division," "business unit," "operating segment."
- ASSETS — A discrete set of assets, contracts, products, or operating rights that does not constitute a separate operating subsidiary or business unit. Examples: a product line ("KeyLift system"), a portfolio of physical assets ("mitigation banks"), specific contracts or licenses, real estate-only deals. Use ASSETS when the press release frames the deal as a sale of specific assets rather than a going-concern unit. When in doubt between BUSINESS_UNIT and ASSETS: if the target has employees, customers, and revenue as a unit, use BUSINESS_UNIT; if it's a discrete asset set being transferred, use ASSETS.

When target_type is SUBSIDIARY, BUSINESS_UNIT, or ASSETS, parent_seller must exist (extracted by a later prompt, not this one). Flag the case in notes if the Parent is ambiguous.

For SPIN_SPLIT, target_type should be SUBSIDIARY (the SpinCo being distributed is structurally a subsidiary being separated).
For JOINT_VENTURE, target_type is null (no target in the M&A sense).
For MINORITY_INVESTMENT and REVERSE_MERGER, target_type = STANDALONE_COMPANY unless stated otherwise.

EVENT TYPE:

event_type describes the press release / source observation type. It is not only the deal lifecycle status.

- ANNOUNCEMENT — Use when this release is the first public announcement of the transaction. This includes same-day announce-and-close private deal releases using language like "today announced its acquisition of," "has acquired," "acquired," "announced the sale of," or "advises on the sale/acquisition of," unless the release clearly says the transaction was previously announced.

- CLOSE — Use only when this is a separate later release announcing completion of a previously announced transaction. Look for explicit language such as "previously announced," "originally announced on [date]," "completed the previously announced acquisition," or similar.

- AMENDMENT — Use when a previously announced deal has been amended, repriced, extended, restructured, or otherwise changed.

- TERMINATION — Use when a previously announced deal has been terminated or will not close.

Do not classify a release as CLOSE merely because the deal appears completed or uses past-tense acquisition language. If the release does not reference a prior announcement and appears to be the first public disclosure, use ANNOUNCEMENT.

For same-day completed private acquisitions, use ANNOUNCEMENT. The later extraction stage should populate both announced_date and closed_date when the text indicates the deal is already completed.

TARGET STATUS:

- PUBLIC — Target is publicly traded (ticker and exchange typically stated).
- PRIVATE — Target is privately held, standalone.
- SUBSIDIARY_OF_PUBLIC — Target is a subsidiary or business unit of a publicly traded Parent.
- SUBSIDIARY_OF_PRIVATE — Target is a subsidiary or business unit of a privately held Parent.
- UNKNOWN — Cannot be determined.

CLASSIFICATION RULES:

- Use the full text of the release, not just the headline.
- If the release describes a deal closing, classify based on the original deal structure as described in the release, but use event_type=CLOSE only when this is a later release for a previously announced transaction.
- If the release describes a termination, classify as the original deal type so the downstream pipeline can link the termination to the original record.
- If multiple events are announced in one release, classify based on the primary event.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.5"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
TITLE: {title}

BODY:
{clean_text}

RELEVANCY HINT (advisory only): {relevancy_reason_code}

Classify the deal type, discriminators, target type, event type, and target status.
```

---

## 6. Output Schema

```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `deal_type` | enum | `ACQUISITION`, `MERGER`, `SPIN_SPLIT`, `REVERSE_MERGER`, `JOINT_VENTURE`, `MINORITY_INVESTMENT`, `UNKNOWN` |
| `spin_split_type` | enum or null | `SPIN_OFF`, `SPLIT`, or null if deal_type ≠ SPIN_SPLIT |
| `distribution_mechanism` | enum or null | `PRO_RATA`, `EXCHANGE_OFFER`, or null if deal_type ≠ SPIN_SPLIT |
| `target_type` | enum or null | `STANDALONE_COMPANY`, `SUBSIDIARY`, `BUSINESS_UNIT`, `ASSETS`, or null for JVs |
| `event_type` | enum | `ANNOUNCEMENT`, `CLOSE`, `AMENDMENT`, `TERMINATION` |
| `target_status` | enum | `PUBLIC`, `PRIVATE`, `SUBSIDIARY_OF_PUBLIC`, `SUBSIDIARY_OF_PRIVATE`, `UNKNOWN` |
| `overrides_relevancy_hint` | boolean | True if deal_type disagrees with the relevancy reason_code |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Explanation, required when using UNKNOWN or overriding the hint |

---

## 7. Few-Shot Examples

**Example 1 — Acquisition of a private standalone company:**

Input:
```
TITLE: Acme Corp Announces Acquisition of Beta Industries
BODY: Acme Corp (NASDAQ: ACME) today announced a definitive agreement to acquire Beta Industries, a privately held manufacturer of specialty valves headquartered in Dallas, Texas, for $500 million in cash. Beta will become a wholly owned subsidiary of Acme upon closing.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 2 — Take-Private classified as ACQUISITION with PUBLIC target:**

Input:
```
TITLE: Acme Corp to Be Acquired by Zenith Capital Partners
BODY: Acme Corp (NYSE: ACME) today announced that it has entered into a definitive merger agreement with affiliates of Zenith Capital Partners, under which Zenith will acquire all outstanding shares of Acme common stock for $45.00 per share in cash. Upon completion, Acme will become a private company and its shares will no longer trade on the NYSE.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Take-Private context: public target, PE acquirer. Downstream derives Take-Private flag from target_status + acquirer_type.",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 3 — Business unit sale to PE (classified as ACQUISITION, not a separate type):**

Input:
```
TITLE: MegaCorp to Divest Industrial Coatings Division to Delta Holdings
BODY: MegaCorp (NYSE: MGC) today announced it has entered into a definitive agreement to sell its Industrial Coatings Division to Delta Holdings, a private equity portfolio company, for $1.2 billion. The divested business is a non-core division of MegaCorp's industrial segment.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "BUSINESS_UNIT",
  "event_type": "ANNOUNCEMENT",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Business unit divestiture; parent_seller is MegaCorp (extracted downstream)",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 4 — Spin-off (pro-rata):**

Input:
```
TITLE: MegaCorp Announces Spin-Off of Industrial Coatings Subsidiary
BODY: MegaCorp (NYSE: MGC) announced its board approved the spin-off of Industrial Coatings Holdings ("ICH") as a separate publicly traded company. MegaCorp shareholders will receive one share of ICH for every four shares of MegaCorp common stock held as of the record date. MegaCorp will retain an approximately 15% stake in ICH following the distribution, which is expected to qualify as tax-free under Section 355.
```

Output:
```json
{
  "deal_type": "SPIN_SPLIT",
  "spin_split_type": "SPIN_OFF",
  "distribution_mechanism": "PRO_RATA",
  "target_type": "SUBSIDIARY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Parent retains 15% residual stake, consistent with SPIN_OFF tax-free treatment",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 5 — Split-off (exchange offer mechanism):**

Input:
```
TITLE: MegaCorp Announces Split-Off of Industrial Coatings via Exchange Offer
BODY: MegaCorp (NYSE: MGC) today announced that it is offering to exchange all of its shares of Industrial Coatings Holdings ("ICH") common stock for MegaCorp common stock held by its shareholders. The exchange offer is expected to result in MegaCorp distributing 100% of ICH to participating shareholders.
```

Output:
```json
{
  "deal_type": "SPIN_SPLIT",
  "spin_split_type": "SPLIT",
  "distribution_mechanism": "EXCHANGE_OFFER",
  "target_type": "SUBSIDIARY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Practitioner term 'Split-Off' = SPLIT + EXCHANGE_OFFER in schema",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 6 — Joint Venture:**

Input:
```
TITLE: Acme Corp and Beta Industries Form Joint Venture
BODY: Acme Corp (NASDAQ: ACME) and Beta Industries today announced the formation of Acme-Beta Renewables LLC, a 50/50 joint venture that will develop utility-scale solar projects. Each parent will contribute $250 million in initial capital.
```

Output:
```json
{
  "deal_type": "JOINT_VENTURE",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": null,
  "event_type": "ANNOUNCEMENT",
  "target_status": "UNKNOWN",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "New entity formed; no existing company acquired",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 7 — Ambiguous (UNKNOWN):**

Input:
```
TITLE: Acme Corp and Beta Industries Announce Strategic Transaction
BODY: Acme Corp and Beta Industries today announced a strategic transaction that will combine certain assets and operations of the two companies. Further details will be disclosed in subsequent filings.
```

Output:
```json
{
  "deal_type": "UNKNOWN",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": null,
  "event_type": "ANNOUNCEMENT",
  "target_status": "UNKNOWN",
  "overrides_relevancy_hint": false,
  "model_confidence": "LOW",
  "notes": "Release lacks structural detail to distinguish between acquisition, merger, JV, or asset combination",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 8 — Same-day completed private acquisition remains ANNOUNCEMENT:**

Input:
```
TITLE: BuyerCo Announces Acquisition of TargetCo
BODY: BuyerCo today announced its acquisition of TargetCo, a privately held provider of specialty software. The acquisition expands BuyerCo's presence in the healthcare market. Financial terms were not disclosed.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "First public announcement of a completed private acquisition; not a separate later closing release.",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 9 — Advisor tombstone for completed sale remains ANNOUNCEMENT:**

Input:
```
TITLE: AdvisorCo Advises Alpha LLC on Sale to Beta Holdings
BODY: AdvisorCo announced that it served as exclusive financial advisor to Alpha LLC on its sale to Beta Holdings. Alpha is a founder-owned business serving industrial customers. Terms of the transaction were not disclosed.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Advisor tombstone describes a completed sale but does not reference a prior announcement; treat as first observed announcement, not CLOSE.",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 10 — Pending take-private agreement is ANNOUNCEMENT, not CLOSE:**

Input:
```
TITLE: PublicCo Announces Agreement to Be Acquired by SponsorCo
BODY: PublicCo (NYSE: PUB) today announced that it has entered into a definitive agreement to be acquired by affiliates of SponsorCo for $40.00 per share in cash. The transaction is expected to close in the fourth quarter, subject to shareholder approval and required regulatory approvals.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Definitive agreement with pending-close language; event is announcement, not close.",
  "prompt_version": "deal_type_classifier:0.5"
}
```

**Example 11 — True later close references prior announcement:**

Input:
```
TITLE: BuyerCo Completes Previously Announced Acquisition of TargetCo
BODY: BuyerCo today announced that it has completed its previously announced acquisition of TargetCo. The transaction was originally announced on March 1, 2026.
```

Output:
```json
{
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "CLOSE",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Separate later completion release explicitly references a previously announced acquisition.",
  "prompt_version": "deal_type_classifier:0.5"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model returns deal_type not in enum | Parser rejects, marks `PROMPT_FAILED`, logs |
| Model populates SPIN_SPLIT discriminators for non-SPIN_SPLIT deals | Parser rejects (schema violation) |
| Model populates null discriminators for SPIN_SPLIT | Parser flags for review; model should at least guess PRO_RATA as default mechanism |
| Model uses TAKE_PRIVATE or CARVE_OUT as deal_type (v0.1 enum values) | Parser rejects — these were removed in v0.2 |
| Model classifies PE carve-out (private sub sale) as SPIN_SPLIT | Addressed by prompt text. Few-shot Example 3 is the canonical case. |
| Model over-uses UNKNOWN on clearly classifiable releases | Tracked in QA. Prompt revision if rate exceeds 10%. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft — 10-type taxonomy including TAKE_PRIVATE, CARVE_OUT, ASSET_SALE, SPIN_OFF as top-level types. |
| 0.2 | 2026-04-22 | Revised to align with agreed schema. 7-type taxonomy. SPIN_SPLIT with spin_split_type + distribution_mechanism discriminators. target_type added as output. TAKE_PRIVATE and CARVE_OUT removed as top-level (derived downstream or out of scope). Target_status enum expanded to include SUBSIDIARY_OF_PUBLIC / SUBSIDIARY_OF_PRIVATE. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-04-23 | Added ASSETS to target_type enum. ASSETS covers discrete asset sets (product lines, physical asset portfolios, contracts) that are not going-concern units. Updated parent_seller rule to include ASSETS alongside SUBSIDIARY and BUSINESS_UNIT. Updated output schema table. |
| 0.5 | 2026-07-22 | Clarified event_type semantics so CLOSE is reserved for separate later releases that explicitly reference a previously announced transaction. Same-day completed private acquisition and advisor tombstone releases remain ANNOUNCEMENT when they appear to be the first public disclosure. Added examples for completed private acquisition, advisor sale tombstone, pending take-private agreement, and true later close. |
