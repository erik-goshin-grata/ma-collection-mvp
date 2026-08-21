# Strategic Rationale Prompt

**Version:** 0.6 (provenance is caller-owned)
**Repo path:** `prompts/strategic_rationale.md`

---

## 1. Purpose

Classify the primary strategic rationale for each transaction into exactly one
of 8 categories. The taxonomy is deliberately compact so that categories are
analytically useful and mutually distinguishable. If no single category fits
cleanly, the model returns OTHER with a note.

Runs once per transaction after aggregation completes. Regenerable — tags are
stored with `is_current` flag and can be recomputed if the taxonomy or prompt
changes.

**Principle:** rationale is about *why* this deal is happening, not *what* kind
of deal it is. Deal type (acquisition vs carve-out vs JV) is captured
separately; the rationale answers the strategic question.

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
  "v2_event_type": "ACQUISITION",
  "target_name": "Beta Industries",
  "acquirer_name": "Acme Corp",
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries...",
  "source_excerpts": [
    {
      "source_tier": "T1",
      "excerpt": "The acquisition is expected to accelerate Acme's expansion into the European industrial market..."
    },
    {
      "source_tier": "T2",
      "excerpt": "Together, we will be able to offer customers a broader product portfolio and deeper geographic reach."
    }
  ]
}
```

**V2 note:** `deal_type` replaced by `v2_event_type`. Values use V2 enum
(`ACQUISITION`, `MERGER`, `SPIN_OFF`, `SPLIT_OFF`, `REVERSE_MERGER`,
`JOINT_VENTURE`, `MINORITY_INVESTMENT`, `RECAPITALIZATION`).

The model sees the deal summary plus up to 3 short excerpts from original
source text containing rationale language. The orchestrator pre-selects these
by scanning for rationale-signaling keywords.

---

## 4. System Prompt

```
You are a classifier that labels M&A transactions with their primary strategic
rationale. You receive a brief deal summary and up to three excerpts from
source documents that describe the strategic reasoning for the deal.

Assign exactly one rationale from this taxonomy:

1. SCALE_CONSOLIDATION — Combines similar businesses for operational scale,
   cost synergies, or market share consolidation within an existing segment.
   Signals: "consolidate," "combined entity," "cost synergies," "scale,"
   "#1 or #2 player," "in-market roll-up."

2. GEOGRAPHIC_EXPANSION — Extends the acquirer's geographic footprint into a
   new region, country, or territory. Signals: "expand into," "enter
   [country/region]," "European presence," "new markets."

3. PRODUCT_OR_TECH_CAPABILITY — Adds a specific product, technology, or
   capability not previously held. Signals: "add [product/capability],"
   "broaden offering," "gain access to [technology]," "complementary product."

4. VERTICAL_INTEGRATION — Integrates the acquirer upstream (into supply) or
   downstream (into distribution/customer channels). Signals: "vertical,"
   "supply chain," "upstream," "downstream," "integrate," "end-to-end."

5. MARKET_DIVERSIFICATION — Moves the acquirer into a new end-market or
   customer segment meaningfully different from its existing business.
   Signals: "diversify," "new end-market," "adjacent segment," "reduce
   concentration."

6. TALENT_ACQUISITION — Primarily motivated by acquiring a team. Signals:
   "team of [engineers/researchers]," "acqui-hire," "talent," "founding team."

7. FINANCIAL_OR_ARBITRAGE — Return-driven (PE), balance sheet optimization,
   spin-off for value realization, shareholder-activism-driven separation.
   Signals: "unlock value," "financial returns," "balance sheet," "pure-play,"
   "separate businesses."

8. OTHER — Rationale is something else or cannot be determined.

CLASSIFICATION RULES:

- Pick exactly one rationale. If multiple are mentioned, pick the one given
  most prominence in the source excerpts.
- If v2_event_type = ACQUISITION and acquirer is a PE firm (acquirer_type =
  private_equity), default to FINANCIAL_OR_ARBITRAGE unless a specific
  strategic rationale is explicitly stated.
- If v2_event_type = SPIN_OFF or SPLIT_OFF, default to FINANCIAL_OR_ARBITRAGE
  (shareholder value creation through separation) unless stated otherwise.
- If v2_event_type = RECAPITALIZATION, default to FINANCIAL_OR_ARBITRAGE
  (balance sheet restructuring) unless strategic rationale is explicitly stated.
- If v2_event_type = JOINT_VENTURE, pick the rationale that best describes why
  the JV exists.
- If source excerpts contain only generic language without specifics, use OTHER
  with a note.
- Do not invent rationale based on industry context. Must see explicit rationale
  language in excerpts to classify.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown
code fences, no preamble.

{
  "rationale": "GEOGRAPHIC_EXPANSION",
  "secondary_rationales": ["PRODUCT_OR_TECH_CAPABILITY"],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for optional fields that have no value.
```

---

## 5. User Prompt Template

```
V2 EVENT TYPE: {v2_event_type}
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
  "notes": null
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `rationale` | enum | One of the 8 values above |
| `secondary_rationales` | array of enum | 0–2 other rationales the deal also supports |
| `supporting_excerpt_index` | integer or null | 0-indexed position of excerpt most supporting primary rationale |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Required when rationale is OTHER |

---

## 7. Few-Shot Examples

**Example 1 — Clear geographic expansion:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET: Beta Industries
ACQUIRER: Acme Corp

SUMMARY: On April 15, 2026, Acme Corp announced a definitive agreement to
acquire Beta Industries for $500 million in cash.

SOURCE EXCERPTS:
[0] T1: "The acquisition is expected to accelerate Acme's expansion into the
European industrial market, complementing its existing North American footprint."
[1] T2: "Beta's strong presence in Germany, France, and the UK positions Acme
to serve European customers with local support and logistics."
```

Output:
```json
{
  "rationale": "GEOGRAPHIC_EXPANSION",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null
}
```

**Example 2 — Tech capability with secondary talent signal:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET: Omega AI
ACQUIRER: Acme Corp

SUMMARY: Acme Corp announced the acquisition of Omega AI for $1.2 billion.

SOURCE EXCERPTS:
[0] T1: "The acquisition will add advanced machine learning capabilities to
Acme's existing industrial automation platform."
[1] T2: "Omega's 150-person engineering team will join Acme's R&D organization."
```

Output:
```json
{
  "rationale": "PRODUCT_OR_TECH_CAPABILITY",
  "secondary_rationales": ["TALENT_ACQUISITION"],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": null
}
```

**Example 3 — Take-private with PE acquirer, default financial:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET: Acme Corp
ACQUIRER: Zenith Capital Partners

SUMMARY: On April 10, 2026, Zenith Capital Partners announced an agreement to
acquire Acme Corp for $45.00 per share, taking the company private.

SOURCE EXCERPTS:
[0] T2: "This transaction delivers immediate and certain value to our
shareholders at a significant premium to recent trading prices."
[1] T2: "As a private company, Acme will have the flexibility to execute its
long-term strategy without the pressures of quarterly reporting."
```

Output:
```json
{
  "rationale": "FINANCIAL_OR_ARBITRAGE",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "PE take-private with standard shareholder-value framing; no specific strategic rationale beyond the financial thesis."
}
```

**Example 4 — Generic language, use OTHER:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET: Beta Industries
ACQUIRER: Acme Corp

SUMMARY: Acme Corp announced the acquisition of Beta Industries for an
undisclosed amount.

SOURCE EXCERPTS:
[0] T2: "This acquisition will strengthen our position in the market and create
value for our shareholders."
```

Output:
```json
{
  "rationale": "OTHER",
  "secondary_rationales": [],
  "supporting_excerpt_index": null,
  "model_confidence": "LOW",
  "notes": "Excerpts contain only generic value-creation language; no specific strategic rationale can be identified."
}
```

**Example 5 — Vertical integration:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET: Delta Logistics
ACQUIRER: Acme Corp

SUMMARY: Acme Corp announced the acquisition of Delta Logistics for $350
million.

SOURCE EXCERPTS:
[0] T1: "The acquisition will give Acme direct control of the distribution
network serving approximately 40% of its North American customer shipments,
reducing reliance on third-party logistics providers."
[1] T2: "End-to-end ownership of our supply chain is a strategic priority."
```

Output:
```json
{
  "rationale": "VERTICAL_INTEGRATION",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "Downstream integration into distribution."
}
```

**Example 6 — Recapitalization defaults to financial:**

Input:
```
V2 EVENT TYPE: RECAPITALIZATION
TARGET: PortfolioCo
ACQUIRER: null

SUMMARY: On March 15, 2026, PortfolioCo completed a $500 million dividend
recapitalization funded by new term debt.

SOURCE EXCERPTS:
[0] T2: "The recapitalization reflects PortfolioCo's strong cash flow
generation and provides liquidity to our investors."
```

Output:
```json
{
  "rationale": "FINANCIAL_OR_ARBITRAGE",
  "secondary_rationales": [],
  "supporting_excerpt_index": 0,
  "model_confidence": "HIGH",
  "notes": "Recapitalization defaults to FINANCIAL_OR_ARBITRAGE per classification rule; source confirms balance-sheet and investor-liquidity framing."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model returns rationale not in enum | Parser rejects, marks `PROMPT_FAILED` |
| Model over-uses OTHER on classifiable deals | QA monitors OTHER rate. Above 20% warrants prompt revision. |
| Model misclassifies take-privates as strategic | Example 3 addresses. Monitor via QA. |
| Model returns secondary_rationales including the primary | Parser dedups, logs warning |
| Model returns more than 2 secondary_rationales | Parser truncates to first 2, logs warning |
| Model invents rationale not in excerpts | Critical — gold set verification catches |
| Model uses legacy `deal_type` label in notes | Acceptable — internal reasoning only |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-23 | Added RESPONSE FORMAT block inline |
| 0.3 | 2026-04-23 | Audited and removed incorrect use of "carve-out" for private business unit sales |
| 0.4 | 2026-07-22 | Updated take-private note to derived flag reference |
| 0.5 | 2026-07-28 | V2 alignment. `deal_type` → `v2_event_type` in input schema and user template. V2 event type enum referenced in classification rules. RECAPITALIZATION default rule added (FINANCIAL_OR_ARBITRAGE). SPIN_OFF / SPLIT_OFF explicitly referenced in spin-off default rule. Example 6 added for recapitalization. |
| 0.6 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
