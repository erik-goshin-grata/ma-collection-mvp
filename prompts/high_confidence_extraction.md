# High-Confidence Extraction Prompt

**Version:** 0.5 (revised)
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

For CARVE_OUT and ASSET_SALE contexts (target_type in BUSINESS_UNIT, SUBSIDIARY, or ASSETS), also extract:
- parent_seller_name — the Parent company divesting the target
- parent_seller_ticker — if the Parent is public

For SPIN_SPLIT transactions:
- parent_seller_name is the distributing Parent
- acquirer_name should be null (spin-offs have no acquirer)
- Still capture target_name (the SpinCo)

TARGET TYPE CONTEXT:
When target_type = ASSETS (a discrete set of assets, not a going-concern unit):
- target_name should describe the asset bundle specifically (e.g., "Diversified National Portfolio of Mitigation Banks", "KeyLift Expandable Interlaminar Stabilization System")
- If the assets are being sold from a parent company, populate parent_seller_name/ticker using the same rules as BUSINESS_UNIT/SUBSIDIARY
- pct_acquired is typically null for ASSETS deals (full asset transfer implied)

DATES:
- announced_date — the date the deal was first announced (ISO 8601, YYYY-MM-DD)
- closed_date — the date the deal closed. See rule below.
- signing_date — the date a definitive agreement was signed, if distinct from announced_date

announced_date rules:
- For ANNOUNCEMENT event_type, announced_date is typically the release's own date (published_date input). If the text explicitly states a different announcement date, use that.
- For CLOSE, AMENDMENT, or TERMINATION event_type, extract the ORIGINAL announcement date from the text if referenced (e.g., "previously announced on January 15, 2026"). If not referenced in the text, leave announced_date null.

closed_date rules:
- For ANNOUNCEMENT event_type, closed_date is null unless the release explicitly states a past close date (rare).
- For CLOSE event_type, the rule depends on whether the text states a specific close date:
  • If the text states an explicit close date (e.g., "closed on April 2, 2026", "the transaction was completed on April 2, 2026"), extract that date.
  • If the text describes the transaction as completed but does not state a separate close date (e.g., "XYZ Capital announced today it has acquired ABC Widgets", "today closed the acquisition of"), set closed_date equal to announced_date. This is the common private-to-private pattern where announce and close are simultaneous.
- For AMENDMENT or TERMINATION event_type, closed_date is null (the deal is not closed).
- Do NOT convert prospective phrases like "expected to close in Q3 2026" to a specific date. Leave closed_date null and note the quarter in notes.

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

PCT_ACQUIRED:
- pct_acquired — the percentage of the target being acquired, when explicitly stated.
- Extract as a number (e.g., 51.0 for "51%", 24.9 for "24.9%").
- LEAVE NULL when:
  - The PR does not state a percentage (most full acquisitions don't say "100%")
  - The deal is described as "all of" or "the entire company" (treat as 100% implicit; leave NULL)
- POPULATE when:
  - The PR states a specific percentage being acquired
  - The deal is partial: minority investment, partial stake, increased ownership to a stated %
- For minority investments, capture the percentage even when small (e.g., "5% stake")

TARGET FINANCIALS (only if stated explicitly in the release):
- revenue_amount — target's revenue
- revenue_period_type — enum: LTM, FY, TTM, CY, QUARTER, NTM, UNKNOWN
- revenue_period_end — ISO date marking the end of the period (YYYY-MM-DD); null if not stated
- ebitda_amount — target's EBITDA
- ebitda_period_type — same enum as revenue
- ebitda_period_end — same format as revenue_period_end
- financials_currency — ISO 4217, usually matches value_currency

PARTY DESCRIPTIONS:
Extract concise descriptions for each party. Source: the "About [Company]" boilerplate at the bottom of nearly every PR, plus opening-paragraph framing ("Acme Corp, a publicly traded SaaS platform for landscaping business management, today announced...").

- target_description — what the target company does. 5-30 words. Include industry, primary business, geographic scope if stated, and ownership status (publicly traded, privately held, PE-backed) when relevant.
- acquirer_description — what the acquirer does. Same length and content guidance.
- parent_seller_description — for divestiture or carve-out transactions, what the parent company does. NULL when there is no parent_seller.

Examples of good descriptions:
- "a publicly-traded SaaS platform providing business management tools to landscaping companies"
- "a privately-held ERP provider serving the construction industry"
- "an Audax Group portfolio company operating a multi-state DSO platform with 65 dental clinics"
- "a Fortune 100 industrial conglomerate spanning aerospace, building technologies, and performance materials"

Examples of weak descriptions to AVOID:
- "a privately held company" (too thin — extract the industry/business at minimum)
- "Acme Corp, a Delaware corporation" (legal form is not a description)
- A literal copy of the entire "About" paragraph (too long — distill to one sentence)

LEAVE NULL when:
- The PR genuinely contains no descriptive content about the party (rare)
- The party is mentioned only by name with no context

DO NOT INVENT descriptions. If the source text says "Acme Corp acquired Beta Industries" with no further context about what either does, set both descriptions to NULL.

EXTRACTION RULES:

- Do not infer values from multiples. If the release states "the transaction represents 10x EBITDA," do not reverse-calculate the EBITDA.
- Do not convert currencies. Capture value and currency as stated.
- Convert abbreviated figures correctly: "$500 million" → 500000000, "$2.5 billion" → 2500000000.
- Use null for any field not explicitly stated in the text. Do not guess.
- If multiple conflicting values appear, use the most recently stated or most specific figure, and note the conflict.
- For tickers, use exchange:symbol format. If only a symbol is given without an exchange, use UNKNOWN:SYMBOL.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "target": {
    "name": "Beta Industries",
    "domain": null,
    "ticker": null,
    "description": "a privately-held ERP provider serving the construction industry"
  },
  "acquirer": {
    "name": "Acme Corp",
    "domain": "acme.com",
    "ticker": "NASDAQ:ACME",
    "type": "STRATEGIC_CORPORATE",
    "description": "a publicly-traded SaaS platform providing business management tools to landscaping companies"
  },
  "parent_seller": {
    "name": null,
    "ticker": null,
    "description": null
  },
  "deal": {
    "target_type": "STANDALONE_COMPANY",
    "pct_acquired": null
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
  "prompt_version": "high_confidence_extraction:0.5"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
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
    "domain": null,
    "ticker": null,
    "description": "a privately-held manufacturer of specialty valves headquartered in Dallas, Texas"
  },
  "acquirer": {
    "name": "Acme Corp",
    "domain": "acme.com",
    "ticker": "NASDAQ:ACME",
    "type": "STRATEGIC_CORPORATE",
    "description": "a leading provider of industrial automation solutions"
  },
  "parent_seller": {
    "name": null,
    "ticker": null,
    "description": null
  },
  "deal": {
    "target_type": "STANDALONE_COMPANY",
    "pct_acquired": null
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
  "prompt_version": "high_confidence_extraction:0.5"
}
```

**Key field notes:**

- `acquirer.type` is now extracted (new in v0.2). Critical for downstream derivation of Take-Private, Add-On, and Platform flags.
- `value.type` now follows the schema-aligned enum: EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED. The v0.1 values STATED_UNQUALIFIED, IMPLIED, and RANGE_OR_APPROXIMATE are removed.
- `value.type_confidence` replaces per-field confidence for value_type. HIGH when explicit, MEDIUM when defaulting to TRANSACTION_VALUE.
- `value.qualifier` captures range/approximation language separately.
- `parent_seller` populated for target_type in {BUSINESS_UNIT, SUBSIDIARY, ASSETS} and for SPIN_SPLIT transactions.
- `deal.pct_acquired` captures partial stake percentages when explicitly stated (new in v0.5).
- `target.description`, `acquirer.description`, `parent_seller.description` — concise party descriptions from "About" boilerplate (new in v0.5).

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
  "target": {"name": "Beta Industries", "domain": null, "ticker": null, "description": "a privately held manufacturer of specialty valves headquartered in Dallas, Texas"},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": "NASDAQ:ACME", "type": "STRATEGIC_CORPORATE", "description": "a leading provider of industrial automation solutions"},
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
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
  "prompt_version": "high_confidence_extraction:0.5"
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
  "target": {"name": "Acme Corp", "domain": null, "ticker": "NYSE:ACME", "description": null},
  "acquirer": {"name": "Zenith Capital Partners", "domain": null, "ticker": null, "type": "PRIVATE_EQUITY", "description": null},
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
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
  "notes": "Enterprise value explicitly stated; PUBLIC target + PE acquirer — downstream derives Take-Private flag. No About sections in source; descriptions null.",
  "prompt_version": "high_confidence_extraction:0.5"
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
  "target": {"name": "Industrial Coatings Division", "domain": null, "ticker": null, "description": null},
  "acquirer": {"name": "Delta Holdings", "domain": null, "ticker": null, "type": "PE_PORTFOLIO", "description": null},
  "parent_seller": {"name": "MegaCorp", "ticker": "NYSE:MGC", "description": null},
  "deal": {"target_type": "BUSINESS_UNIT", "pct_acquired": null},
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
  "notes": "EQUITY_VALUE explicit ('representing the equity value'); debt assumption mentioned separately ($200M) not included in $1.2B. Delta Holdings characterized as 'PE portfolio company' in release. LTM from 'twelve months ended 12/31/2025'. No About sections; descriptions null.",
  "prompt_version": "high_confidence_extraction:0.5"
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
  "target": {"name": "Beta Industries", "domain": null, "ticker": null, "description": null},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": null, "type": "STRATEGIC_CORPORATE", "description": null},
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
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
  "prompt_version": "high_confidence_extraction:0.5"
}
```

**Example 5 — Simultaneous announce-and-close, private-to-private:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: CLOSE
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-15

TITLE: XYZ Capital Acquires ABC Widgets
BODY: XYZ Capital, a private equity firm based in Chicago, announced today it has acquired ABC Widgets, a privately held manufacturer of industrial fasteners headquartered in Ohio. Terms of the transaction were not disclosed. ABC Widgets will continue to operate under its existing management team.
```

Output:
```json
{
  "target": {"name": "ABC Widgets", "domain": null, "ticker": null, "description": "a privately held manufacturer of industrial fasteners headquartered in Ohio"},
  "acquirer": {"name": "XYZ Capital", "domain": null, "ticker": null, "type": "PRIVATE_EQUITY", "description": "a private equity firm based in Chicago"},
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
  "dates": {
    "announced_date": "2026-04-15",
    "closed_date": "2026-04-15",
    "signing_date": null
  },
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
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Past-tense completive language ('has acquired'); no separate close date stated. Simultaneous announce-and-close: closed_date = announced_date.",
  "prompt_version": "high_confidence_extraction:0.5"
}
```

**Example 6 — ASSETS target type (asset portfolio acquisition):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: ASSETS
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: SUBSIDIARY_OF_PRIVATE
PUBLISHED DATE: 2026-04-23

TITLE: Conservation Equity Management Acquires Diversified National Portfolio of Mitigation Banks
BODY: Conservation Equity Management ("CEM"), a privately-held investment firm focused on environmental markets, today announced the acquisition of a diversified national portfolio of mitigation banks from Diversified Mitigation Banks LLC. The portfolio comprises 12 wetland and stream mitigation banks across 8 states, generating recurring credit sales to developers and infrastructure projects.

About Conservation Equity Management
CEM is a private investment firm specializing in environmental markets, including wetland and stream mitigation banking, conservation easements, and carbon credit projects.
```

Output:
```json
{
  "target": {
    "name": "Diversified National Portfolio of Mitigation Banks",
    "domain": null,
    "ticker": null,
    "description": "a 12-asset portfolio of wetland and stream mitigation banks across 8 states generating recurring credit sales to developers and infrastructure projects"
  },
  "acquirer": {
    "name": "Conservation Equity Management",
    "domain": null,
    "ticker": null,
    "type": "PRIVATE_EQUITY",
    "description": "a private investment firm specializing in environmental markets including wetland and stream mitigation banking, conservation easements, and carbon credit projects"
  },
  "parent_seller": {"name": "Diversified Mitigation Banks LLC", "ticker": null, "description": null},
  "deal": {"target_type": "ASSETS", "pct_acquired": null},
  "dates": {"announced_date": "2026-04-23", "closed_date": null, "signing_date": null},
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
    "acquirer_type": "MEDIUM",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Asset portfolio acquisition (12 mitigation banks); not a going-concern subsidiary. ASSETS target_type appropriate. Terms not disclosed.",
  "prompt_version": "high_confidence_extraction:0.5"
}
```

**Example 7 — Partial acquisition with pct_acquired:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-23

TITLE: Foresight Autonomous Holdings Announces Agreement to Acquire 51% of VisionWave Holdings
BODY: Foresight Autonomous Holdings Ltd. (NASDAQ: FRSX), a publicly-traded company specializing in vision systems for the automotive and autonomous driving industries, today announced an agreement to acquire 51% of VisionWave Holdings, Inc. for $17.5 million in Foresight common stock.

About VisionWave
VisionWave Holdings is a private developer of LiDAR perception software for autonomous vehicles.
```

Output:
```json
{
  "target": {
    "name": "VisionWave Holdings, Inc.",
    "domain": null,
    "ticker": null,
    "description": "a private developer of LiDAR perception software for autonomous vehicles"
  },
  "acquirer": {
    "name": "Foresight Autonomous Holdings Ltd.",
    "domain": null,
    "ticker": "NASDAQ:FRSX",
    "type": "STRATEGIC_CORPORATE",
    "description": "a publicly-traded company specializing in vision systems for the automotive and autonomous driving industries"
  },
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": 51.0},
  "dates": {"announced_date": "2026-04-23", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 17500000,
    "currency": "USD",
    "type": "EQUITY_VALUE",
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
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Partial acquisition (51%) for stock consideration; $17.5M is purchase price for 51% stake (EQUITY_VALUE for the acquired portion). pct_acquired = 51.0.",
  "prompt_version": "high_confidence_extraction:0.5"
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
| Model invents description when source has none | Prompt forbids. Gold set catches hallucinated descriptions. |
| Model uses BUSINESS_UNIT for discrete asset sales (KeyLift, mitigation banks) | ASSETS guidance + Example 6 addresses. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Revised. value_type enum aligned with schema (EQUITY_VALUE, TRANSACTION_VALUE, ENTERPRISE_VALUE, UNDISCLOSED) with TRANSACTION_VALUE as default. value_type_confidence added. acquirer_type added as output (drives Take-Private and Add-On derivation downstream). PE_PORTFOLIO added to acquirer_type enum to support Add-On recognition. SPIN_SPLIT handling added (acquirer_name = null, parent_seller populated). |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-04-23 | Added simultaneous announce-and-close date rule for CLOSE event_type when no separate close date is stated. Applies to the common private-to-private pattern where past-tense completive language is used without a distinct close date. Added Example 5 to illustrate. |
| 0.5 | 2026-04-23 | Added ASSETS to target_type handling (TARGET TYPE CONTEXT section; parent_seller extraction rule extended to ASSETS). Added pct_acquired numeric field for partial acquisitions (PCT_ACQUIRED section). Added party description extraction for target, acquirer, parent_seller from "About" boilerplate (PARTY DESCRIPTIONS section). Updated RESPONSE FORMAT block and output schema (§6) to include description fields on all party objects and new "deal" object with target_type context and pct_acquired. Updated all five existing examples with description fields and deal section. Added Example 6 (ASSETS portfolio acquisition) and Example 7 (partial acquisition with pct_acquired). |
