# Strategic Rationale Prompt

**Version:** 0.2 (draft)
**Repo path:** `prompts/strategic_rationale.md`

---

## 1. Purpose

Classify the primary strategic rationale for each transaction into exactly one of 8 categories. The taxonomy is deliberately compact so that categories are analytically useful and mutually distinguishable. If no single category fits cleanly, the model returns OTHER with a note.

Runs once per transaction after aggregation completes. Regenerable — tags are stored with `is_current` flag and can be recomputed if the taxonomy or prompt changes.

**Principle:** rationale is about *why* this deal is happening, not *what* kind of deal it is. Deal type (acquisition vs carve-out vs JV) is captured separately; the rationale answers the strategic question.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 512

---

## 3. Input Schema

```json
{
  "transaction_id": "tx_00042",
  "deal_type": "ACQUISITION",
  "target_name": "Beta Industries",
  "acquirer_name": "Acme Corp",
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries...",
  "source_excerpts": [
    {
      "source_tier": "T1",
      "excerpt": "The acquisition is expected to accelerate Acme's expansion into the European industrial market, complementing its existing North American footprint..."
    },
    {
      "source_tier": "T2",
      "excerpt": "Together, we will be able to offer customers a broader product portfolio and deeper geographic reach."
    }
  ]
}
```

The model sees the deal summary (already processed) plus up to 3 short excerpts from the original source text that contain rationale language. The orchestrator pre-selects these by scanning for rationale-signaling keywords (e.g., "accelerate," "expand," "complement," "strengthen," "enter," "diversify"). Total input is kept compact — this prompt doesn't need the full release body.

---

## 4. System Prompt

```
You are a classifier that labels M&A transactions with their primary strategic rationale. You receive a brief deal summary and up to three excerpts from source documents that describe the strategic reasoning for the deal.

Assign exactly one rationale from this taxonomy:

1. SCALE_CONSOLIDATION — The deal combines similar businesses to achieve operational scale, cost synergies, or market share consolidation within an existing segment. Signals: "consolidate," "combined entity," "cost synergies," "scale," "#1 or #2 player," "in-market roll-up."

2. GEOGRAPHIC_EXPANSION — The deal extends the acquirer's geographic footprint into a new region, country, or territory. Signals: "expand into," "enter [country/region]," "European presence," "new markets."

3. PRODUCT_OR_TECH_CAPABILITY — The deal adds a specific product, technology, or capability to the acquirer's portfolio that it did not previously have. Signals: "add [product/capability]," "broaden offering," "gain access to [technology]," "complementary product."

4. VERTICAL_INTEGRATION — The deal integrates the acquirer upstream (into supply) or downstream (into distribution/customer channels) within its own value chain. Signals: "vertical," "supply chain," "upstream," "downstream," "integrate," "end-to-end."

5. MARKET_DIVERSIFICATION — The deal moves the acquirer into a new end-market or customer segment meaningfully different from its existing business. Signals: "diversify," "new end-market," "adjacent segment," "reduce concentration."

6. TALENT_ACQUISITION — The deal is primarily motivated by acquiring a team rather than a product or revenue stream. Signals: "team of [engineers/researchers]," "acqui-hire," "talent," "founding team."

7. FINANCIAL_OR_ARBITRAGE — The deal's primary rationale is financial: return-driven (PE), balance sheet optimization, spin-off for value realization, shareholder-activism-driven separation. Signals: "unlock value," "financial returns," "balance sheet," "pure-play," "separate businesses."

8. OTHER — The rationale is something else or cannot be determined.

CLASSIFICATION RULES:

- Pick exactly one rationale. If multiple rationales are mentioned, pick the one given the most prominence in the source excerpts.
- If the deal is a TAKE_PRIVATE and the acquirer is a PE firm, default to FINANCIAL_OR_ARBITRAGE unless a specific strategic rationale is explicitly stated.
- If the deal is a SPIN_OFF, default to FINANCIAL_OR_ARBITRAGE (shareholder value creation through separation) unless stated otherwise.
- If the deal is a JOINT_VENTURE, pick the rationale that best describes why the JV exists (usually GEOGRAPHIC_EXPANSION, PRODUCT_OR_TECH_CAPABILITY, or MARKET_DIVERSIFICATION).
- If the source excerpts contain only generic language ("strengthen our business," "create value for shareholders") without specifics, use OTHER with a note.
- Do not invent rationale based on industry context. The model must see explicit rationale language in the excerpts to classify.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "rationale": "GEOGRAPHIC_EXPANSION",
  "secondary_rationales": ["PRODUCT_OR_TECH_CAPABILITY"],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "strategic_rationale:0.2"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
DEAL TYPE: {deal_type}
TARGET: {target_name}
ACQUIRER: {acquirer_name}

SUMMARY:
{summary_text}

SOURCE EXCERPTS:
{source_excerpts_formatted}

Classify the primary strategic rationale.
```

---

## 6. Output Schema

```json
{
  "rationale": "GEOGRAPHIC_EXPANSION",
  "secondary_rationales": ["PRODUCT_OR_TECH_CAPABILITY"],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "strategic_rationale:0.2"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `rationale` | enum | One of the 8 values above |
| `secondary_rationales` | array of enum | 0–2 other rationales the deal also supports, ranked by prominence |
| `supporting_excerpt_index` | integer or null | 0-indexed position of the source excerpt that most strongly supports the primary rationale; null if based on summary alone |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Explanation, required when rationale is OTHER |

**Secondary rationales:**
- Stored for analytical flexibility (users may want to filter on any tagged rationale, not only primary).
- Max 2 secondary rationales. If more would apply, the deal is probably too complex to pigeonhole — use OTHER for primary with a note.

---

## 7. Few-Shot Examples

**Example 1 — Clear geographic expansion:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET: Beta Industries
ACQUIRER: Acme Corp

SUMMARY:
On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries for $500 million in cash.

SOURCE EXCERPTS:
[0] T1: "The acquisition is expected to accelerate Acme's expansion into the European industrial market, complementing its existing North American footprint."
[1] T2: "Beta's strong presence in Germany, France, and the UK positions Acme to serve European customers with local support and logistics."
```

Output:
```json
{
  "rationale": "GEOGRAPHIC_EXPANSION",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "strategic_rationale:0.2"
}
```

**Example 2 — Tech capability with secondary scale signal:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET: Omega AI
ACQUIRER: Acme Corp

SUMMARY:
Acme Corp announced the acquisition of Omega AI, a machine learning platform company, for $1.2 billion.

SOURCE EXCERPTS:
[0] T1: "The acquisition will add advanced machine learning capabilities to Acme's existing industrial automation platform, enabling predictive maintenance and quality control features not previously available in our product suite."
[1] T2: "Omega's 150-person engineering team will join Acme's R&D organization, expanding our AI engineering capacity."
```

Output:
```json
{
  "rationale": "PRODUCT_OR_TECH_CAPABILITY",
  "secondary_rationales": ["TALENT_ACQUISITION"],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "Primary rationale is capability addition (ML to industrial automation); team scale is a secondary signal",
  "prompt_version": "strategic_rationale:0.2"
}
```

**Example 3 — Take-private with PE acquirer, default to financial:**

Note: deal_type is ACQUISITION. Take-Private context is inferred downstream from target_status = PUBLIC + acquirer_type = PRIVATE_EQUITY; TAKE_PRIVATE was removed as a top-level type in v0.2.

Input:
```
DEAL TYPE: ACQUISITION
TARGET: Acme Corp
ACQUIRER: Zenith Capital Partners

SUMMARY:
On April 10, 2026, Acme Corp announced a merger agreement under which Zenith Capital Partners will acquire all outstanding shares for $45.00 per share in cash, taking the company private.

SOURCE EXCERPTS:
[0] T2: "This transaction delivers immediate and certain value to our shareholders at a significant premium to recent trading prices."
[1] T2: "As a private company, Acme will have the flexibility to execute its long-term strategy without the pressures of quarterly reporting."
```

Output:
```json
{
  "rationale": "FINANCIAL_OR_ARBITRAGE",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "PE take-private with standard shareholder-value framing; no specific strategic rationale beyond the financial thesis",
  "prompt_version": "strategic_rationale:0.2"
}
```

**Example 4 — Generic language, use OTHER:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET: Beta Industries
ACQUIRER: Acme Corp

SUMMARY:
Acme Corp announced the acquisition of Beta Industries for an undisclosed amount.

SOURCE EXCERPTS:
[0] T2: "This acquisition will strengthen our position in the market and create value for our shareholders."
[1] T2: "We are excited to welcome the Beta team to the Acme family."
```

Output:
```json
{
  "rationale": "OTHER",
  "secondary_rationales": [],
  "supporting_excerpt_index": null,
  "model_confidence": "LOW",
  "notes": "Excerpts contain only generic value-creation language; no specific strategic rationale can be identified",
  "prompt_version": "strategic_rationale:0.2"
}
```

**Example 5 — Vertical integration:**

Input:
```
DEAL TYPE: ACQUISITION
TARGET: Delta Logistics
ACQUIRER: Acme Corp

SUMMARY:
Acme Corp announced the acquisition of Delta Logistics, a regional warehousing and distribution provider, for $350 million.

SOURCE EXCERPTS:
[0] T1: "The acquisition of Delta Logistics will give Acme direct control of the distribution network that currently serves approximately 40% of its North American customer shipments, reducing reliance on third-party logistics providers."
[1] T2: "End-to-end ownership of our supply chain is a strategic priority for Acme."
```

Output:
```json
{
  "rationale": "VERTICAL_INTEGRATION",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "Downstream integration into distribution",
  "prompt_version": "strategic_rationale:0.2"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model returns rationale not in enum | Parser rejects, marks `PROMPT_FAILED` |
| Model over-uses OTHER on classifiable deals | QA monitors OTHER rate. If above 20% on clearly-framed deals, prompt revision needed. |
| Model misclassifies take-privates as strategic instead of financial | Few-shot Example 3 addresses. Monitor via QA. |
| Model returns secondary_rationales including the primary | Parser dedups, logs warning |
| Model returns more than 2 secondary_rationales | Parser truncates to first 2, logs warning |
| Model invents rationale not supported by excerpts | Critical — gold set verification catches. Prompt explicitly forbids. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
