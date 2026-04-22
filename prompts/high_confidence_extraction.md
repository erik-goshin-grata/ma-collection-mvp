# High-Confidence Extraction Prompt

**Version:** 0.2 (revised)
**Repo path:** `prompts/high_confidence_extraction.md`

---

## 1. Purpose

Extract the fields that define a transaction's identity and primary economics from a single source document. These are the fields most often stated explicitly in press releases and SEC filings, and the fields whose accuracy matters most downstream.

Runs on every row where `deal_type_classifier` produced a classification (including `UNKNOWN` — fields like parties and dates are still extractable even when the deal type is unclear).

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 2048

---

## 3. Input Schema

```json
{
  "source_raw_id": 12345,
  "source_type": "PR_NEWSWIRE",
  "source_tier": "T2",
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "Acme Corp (NASDAQ: ACME), a leading provider of...",
  "published_date": "2026-04-15",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "target_type": "STANDALONE_COMPANY",
  "event_type": "ANNOUNCEMENT",
  "target_status": "PRIVATE"
}
```

The full `clean_text` is passed — financial details are often deep in the document. The deal type context lets the model apply type-specific extraction logic.

---

## 4. System Prompt

```
You are a financial data extraction model for an M&A data collection pipeline. Given the text of a press release or SEC filing about a transaction, extract the following fields as structured data.

TARGET:
- target_name — the entity being acquired, divested, or invested in
- target_domain — the web domain of the target, if determinable from the text. Do not guess.
- target_ticker — the ticker symbol if the target is publicly traded (format: "NYSE:ACME")

ACQUIRER / BUYER:
- acquirer_name — the entity acquiring, buying, or investing
- acquirer_domain — same rules as target_domain
- acquirer_ticker — same rules as target_ticker
- acquirer_type — see enum below. This field drives downstream derivation of Take-Private, Add-On, and other deal characteristics.

acquirer_type enum:
- STRATEGIC_CORPORATE — an operating company acquiring another operating company
- PRIVATE_EQUITY — a private equity firm (sponsor). If an acquirer is described as a PE firm, fund, or sponsor, use this.
- VENTURE_CAPITAL — a venture capital firm
- SOVEREIGN_WEALTH_FUND — a government-owned investment fund
- PENSION_FUND — a pension or retirement fund
- HEDGE_FUND — a hedge fund
- FAMILY_OFFICE — a family office
- INDIVIDUAL — an individual investor or group of individuals (not through a fund)
- MANAGEMENT — the company's own management team (MBO context)
- EMPLOYEE_GROUP — employee group or ESOP
- SPAC — special purpose acquisition company
- CONSORTIUM — a group of investors acting together (capture in notes which types are in the consortium)
- PE_PORTFOLIO — a portfolio company of a private equity sponsor. Use this for add-on acquisitions where the named acquirer is the PE-backed platform company rather than the sponsor itself.
- UNKNOWN — cannot be determined

For CARVE_OUT and ASSET_SALE contexts (target_type in BUSINESS_UNIT or SUBSIDIARY), also extract:
- parent_seller_name — the Parent company divesting the target
- parent_seller_ticker — if the Parent is public

For SPIN_SPLIT transactions:
- parent_seller_name is the distributing Parent
- acquirer_name should be null (spin-offs have no acquirer)
- Still capture target_name (the SpinCo)

DATES:
- announced_date — the date the deal was first announced (ISO 8601, YYYY-MM-DD)
- closed_date — the date the deal closed or is expected to close. Use a specific date if given; otherwise null. Do not convert phrases like "expected to close in Q3 2026" to a specific date — leave closed_date null and note the quarter in notes.
- signing_date — the date a definitive agreement was signed, if distinct from announced_date

DEAL VALUE:
- value_amount — the total transaction value as a number (no currency symbols, no commas). Null if not stated.
- value_currency — ISO 4217 code (USD, EUR, GBP, JPY, etc.). Default to USD if the release is from a US company with no contrary indication.
- value_type — see enum below. This is how the value is characterized in the source.
- per_share_price — for transactions involving public targets, the per-share cash (or cash-equivalent) price paid to target shareholders. Null for other deal types unless relevant.
- value_qualifier — short string capturing qualifying language around the value: "approximately", "up to", "in excess of", "$400-500M range", or null if precise.

value_type enum (CRITICAL — follow these rules carefully):
- EQUITY_VALUE — the value is explicitly described as equity value, purchase price for the equity, or purchase price for all outstanding shares.
- ENTERPRISE_VALUE — the value is explicitly described as enterprise value, or as "equity value plus debt," or a combination that clearly includes debt assumption.
- TRANSACTION_VALUE — the default. Use this when the release states "deal value," "transaction value," "valued at $X," "for $X million in cash," or simply "$X million" without specifying equity value or enterprise value. TRANSACTION_VALUE is equity value plus any debt assumed as stated — when the release doesn't make the distinction, we default here rather than guessing equity vs enterprise.
- UNDISCLOSED — no value is stated or financial terms are explicitly said to be undisclosed.

Set value_type_confidence = HIGH when the text explicitly uses "equity value" or "enterprise value" language. Set MEDIUM when defaulting to TRANSACTION_VALUE. Set HIGH when the release explicitly states "terms were not disclosed" (UNDISCLOSED is confident).

TARGET FINANCIALS (only if stated explicitly in the release):
- revenue_amount — target's revenue
- revenue_period_type — enum: LTM, FY, TTM, CY, QUARTER, NTM, UNKNOWN
- revenue_period_end — ISO date marking the end of the period (YYYY-MM-DD); null if not stated
- ebitda_amount — target's EBITDA
- ebitda_period_type — same enum as revenue
- ebitda_period_end — same format as revenue_period_end
- financials_currency — ISO 4217, usually matches value_currency

EXTRACTION RULES:

- Do not infer values from multiples. If the release states "the transaction represents 10x EBITDA," do not reverse-calculate the EBITDA.
- Do not convert currencies. Capture value and currency as stated.
- Convert abbreviated figures correctly: "$500 million" → 500000000, "$2.5 billion" → 2500000000.
- Use null for any field not explicitly stated in the text. Do not guess.
- If multiple conflicting values appear, use the most recently stated or most specific figure, and note the conflict.
- For tickers, use exchange:symbol format. If only a symbol is given without an exchange, use UNKNOWN:SYMBOL.

Return a single JSON object matching the schema. Do not include any text before or after the JSON. Do not wrap the JSON in Markdown code fences. Do not include comments.
```

---

## 5. User Prompt Template

```
SOURCE TYPE: {source_type}
SOURCE TIER: {source_tier}
DEAL TYPE: {deal_type}
SPIN SPLIT TYPE: {spin_split_type}
TARGET TYPE: {target_type}
EVENT TYPE: {event_type}
TARGET STATUS: {target_status}
PUBLISHED DATE: {published_date}

TITLE: {title}

BODY:
{clean_text}

Extract the high-confidence fields.
```

---

## 6. Output Schema

```json
{
  "target": {
    "name": "Beta Industries",
    "domain": "beta-industries.com",
    "ticker": null
  },
  "acquirer": {
    "name": "Acme Corp",
    "domain": "acme.com",
    "ticker": "NASDAQ:ACME",
    "type": "STRATEGIC_CORPORATE"
  },
  "parent_seller": {
    "name": null,
    "ticker": null
  },
  "dates": {
    "announced_date": "2026-04-15",
    "closed_date": null,
    "signing_date": null
  },
  "value": {
    "amount": 500000000,
    "currency": "USD",
    "type": "TRANSACTION_VALUE",
    "type_confidence": "MEDIUM",
    "per_share_price": null,
    "qualifier": null
  },
  "target_financials": {
    "revenue_amount": null,
    "revenue_period_type": null,
    "revenue_period_end": null,
    "ebitda_amount": null,
    "ebitda_period_type": null,
    "ebitda_period_end": null,
    "currency": null
  },
  "model_confidence": "HIGH",
  "field_confidence": {
    "target_name": "HIGH",
    "acquirer_name": "HIGH",
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": null,
  "prompt_version": "high_confidence_extraction:0.2"
}
```

**Key field notes:**

- `acquirer.type` is now extracted (new in v0.2). Critical for downstream derivation of Take-Private, Add-On, and Platform flags.
- `value.type` now follows the schema-aligned enum: EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED. The v0.1 values STATED_UNQUALIFIED, IMPLIED, and RANGE_OR_APPROXIMATE are removed.
- `value.type_confidence` replaces per-field confidence for value_type. HIGH when explicit, MEDIUM when defaulting to TRANSACTION_VALUE.
- `value.qualifier` captures range/approximation language separately.
- `parent_seller` populated for target_type in {BUSINESS_UNIT, SUBSIDIARY} and for SPIN_SPLIT transactions.

---

## 7. Few-Shot Examples

**Example 1 — Acquisition of private company, strategic buyer, unqualified value (defaults to TRANSACTION_VALUE):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-15

TITLE: Acme Corp to Acquire Beta Industries for $500 Million
BODY: Acme Corp (NASDAQ: ACME), a leading provider of industrial automation solutions, today announced that it has signed a definitive agreement to acquire Beta Industries, a privately held manufacturer of specialty valves headquartered in Dallas, Texas. Under the terms of the agreement, Acme will pay $500 million in cash. Beta generated approximately $120 million in revenue in 2025 and expects to close the transaction in Q3 2026.
```

Output:
```json
{
  "target": {"name": "Beta Industries", "domain": null, "ticker": null},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": "NASDAQ:ACME", "type": "STRATEGIC_CORPORATE"},
  "parent_seller": {"name": null, "ticker": null},
  "dates": {"announced_date": "2026-04-15", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 500000000,
    "currency": "USD",
    "type": "TRANSACTION_VALUE",
    "type_confidence": "MEDIUM",
    "per_share_price": null,
    "qualifier": null
  },
  "target_financials": {
    "revenue_amount": 120000000,
    "revenue_period_type": "FY",
    "revenue_period_end": "2025-12-31",
    "ebitda_amount": null,
    "ebitda_period_type": null,
    "ebitda_period_end": null,
    "currency": "USD"
  },
  "model_confidence": "HIGH",
  "field_confidence": {
    "target_name": "HIGH",
    "acquirer_name": "HIGH",
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Unqualified '$500M in cash' defaults to TRANSACTION_VALUE; revenue qualified 'approximately' but only figure given",
  "prompt_version": "high_confidence_extraction:0.2"
}
```

**Example 2 — Take-Private with explicit ENTERPRISE_VALUE and per-share price:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PUBLIC
PUBLISHED DATE: 2026-04-10

TITLE: Acme Corp to Be Acquired by Zenith Capital in $4.5 Billion Transaction
BODY: Acme Corp (NYSE: ACME) today announced that it has entered into a definitive merger agreement under which affiliates of Zenith Capital Partners will acquire all outstanding shares of Acme common stock for $45.00 per share in cash, representing a total enterprise value of approximately $4.5 billion.
```

Output:
```json
{
  "target": {"name": "Acme Corp", "domain": null, "ticker": "NYSE:ACME"},
  "acquirer": {"name": "Zenith Capital Partners", "domain": null, "ticker": null, "type": "PRIVATE_EQUITY"},
  "parent_seller": {"name": null, "ticker": null},
  "dates": {"announced_date": "2026-04-10", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 4500000000,
    "currency": "USD",
    "type": "ENTERPRISE_VALUE",
    "type_confidence": "HIGH",
    "per_share_price": 45.00,
    "qualifier": "approximately"
  },
  "target_financials": {
    "revenue_amount": null,
    "revenue_period_type": null,
    "revenue_period_end": null,
    "ebitda_amount": null,
    "ebitda_period_type": null,
    "ebitda_period_end": null,
    "currency": null
  },
  "model_confidence": "HIGH",
  "field_confidence": {
    "target_name": "HIGH",
    "acquirer_name": "HIGH",
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Enterprise value explicitly stated; PUBLIC target + PE acquirer — downstream derives Take-Private flag",
  "prompt_version": "high_confidence_extraction:0.2"
}
```

**Example 3 — Business unit acquisition with parent seller and explicit EQUITY_VALUE:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: BUSINESS_UNIT
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: SUBSIDIARY_OF_PUBLIC
PUBLISHED DATE: 2026-03-22

TITLE: MegaCorp Divests Industrial Coatings Division to Delta Holdings for $1.2 Billion
BODY: MegaCorp (NYSE: MGC) today announced a definitive agreement to sell its Industrial Coatings Division to Delta Holdings for $1.2 billion, representing the equity value of the business. Delta will assume an additional $200 million of debt allocated to the division. The divested business generated $400 million in revenue and $85 million in EBITDA for the twelve months ended December 31, 2025.
```

Output:
```json
{
  "target": {"name": "Industrial Coatings Division", "domain": null, "ticker": null},
  "acquirer": {"name": "Delta Holdings", "domain": null, "ticker": null, "type": "PE_PORTFOLIO"},
  "parent_seller": {"name": "MegaCorp", "ticker": "NYSE:MGC"},
  "dates": {"announced_date": "2026-03-22", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 1200000000,
    "currency": "USD",
    "type": "EQUITY_VALUE",
    "type_confidence": "HIGH",
    "per_share_price": null,
    "qualifier": null
  },
  "target_financials": {
    "revenue_amount": 400000000,
    "revenue_period_type": "LTM",
    "revenue_period_end": "2025-12-31",
    "ebitda_amount": 85000000,
    "ebitda_period_type": "LTM",
    "ebitda_period_end": "2025-12-31",
    "currency": "USD"
  },
  "model_confidence": "HIGH",
  "field_confidence": {
    "target_name": "HIGH",
    "acquirer_name": "HIGH",
    "acquirer_type": "MEDIUM",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "EQUITY_VALUE explicit ('representing the equity value'); debt assumption mentioned separately ($200M) not included in $1.2B. Delta Holdings characterized as 'PE portfolio company' in release. LTM from 'twelve months ended 12/31/2025'.",
  "prompt_version": "high_confidence_extraction:0.2"
}
```

**Example 4 — Terms not disclosed (UNDISCLOSED):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: CLOSE
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-02

TITLE: Acme Corp Completes Acquisition of Beta Industries
BODY: Acme Corp today announced that it has completed its previously announced acquisition of Beta Industries. Terms of the transaction were not disclosed.
```

Output:
```json
{
  "target": {"name": "Beta Industries", "domain": null, "ticker": null},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": null, "type": "STRATEGIC_CORPORATE"},
  "parent_seller": {"name": null, "ticker": null},
  "dates": {"announced_date": null, "closed_date": "2026-04-02", "signing_date": null},
  "value": {
    "amount": null,
    "currency": null,
    "type": "UNDISCLOSED",
    "type_confidence": "HIGH",
    "per_share_price": null,
    "qualifier": null
  },
  "target_financials": {
    "revenue_amount": null,
    "revenue_period_type": null,
    "revenue_period_end": null,
    "ebitda_amount": null,
    "ebitda_period_type": null,
    "ebitda_period_end": null,
    "currency": null
  },
  "model_confidence": "HIGH",
  "field_confidence": {
    "target_name": "HIGH",
    "acquirer_name": "HIGH",
    "acquirer_type": "LOW",
    "announced_date": "LOW",
    "value_amount": "HIGH"
  },
  "notes": "Closing release; acquirer_type inferred as STRATEGIC_CORPORATE from context but not stated (low confidence). Original announcement date not stated.",
  "prompt_version": "high_confidence_extraction:0.2"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model hallucinates a value not in the text | Critical. Gold-set evaluation catches. Prompt emphasizes "do not infer." |
| Model uses v0.1 value_type enums (STATED_UNQUALIFIED, IMPLIED) | Parser rejects — these were removed in v0.2 |
| Model defaults to EQUITY_VALUE or ENTERPRISE_VALUE when release is unqualified | Few-shot Example 1 addresses: unqualified = TRANSACTION_VALUE. Monitor. |
| Model converts currencies unprompted | Prompt forbids. QA catches. |
| Model picks up a competitor's revenue instead of the target's | Note field flags; QA sampling catches |
| Model reverses acquirer and target | Prompt structure (DEAL TYPE + TARGET TYPE context) helps. Gold set tracks. |
| Model returns string "null" instead of JSON null | Parser rejects |
| Model uses acquirer_type = UNKNOWN frequently | QA monitors rate; prompt may need revision if above 15% on classifiable acquirers |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Revised. value_type enum aligned with schema (EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED) with TRANSACTION_VALUE as default. value_type_confidence added. acquirer_type added as output (drives Take-Private and Add-On derivation downstream). PE_PORTFOLIO added to acquirer_type enum to support Add-On recognition. SPIN_SPLIT handling added (acquirer_name = null, parent_seller populated). |
