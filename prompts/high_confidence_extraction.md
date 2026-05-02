# High-Confidence Extraction Prompt

**Version:** 0.9
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

MULTI-TRANSACTION DETECTION

A press release usually describes ONE M&A transaction. Occasionally a single PR describes MULTIPLE distinct, separately-owned targets being acquired in coordinated but distinct transactions. When this is the case, return a "transactions" array with one element per distinct deal.

When to split — return MULTIPLE transactions:
- The PR explicitly enumerates multiple separately-owned targets: "Acme acquired three companies: A, B, and C, each independently owned and operated"
- Different sellers for different targets in the same PR
- List-style language for distinct deals: "These acquisitions include..." followed by company names with separate origins
- Each named target has its own descriptive text (separate "About" paragraphs, different geographies, different former owners)

When NOT to split — return a SINGLE transaction (array of length 1):
- One target that is itself a multi-component business unit (e.g., "U.S. Branded Business of Cumberland Pharma, including products A, B, C") — single with target_type=BUSINESS_UNIT
- An asset portfolio acquired together as a unit (e.g., "12 mitigation banks across 8 states") — single with target_type=ASSETS
- A target with multiple subsidiaries being acquired together — single transaction; subsidiaries are part of the parent
- Same deal reported in two PRs from different perspectives — handled by clustering, not splitting

Decision rule: if the PR describes 2+ targets where each would have its own deal_type, value, advisors, and rationale if announced separately, SPLIT. Otherwise, single transaction.

Default to NOT splitting when ambiguous. Splitting incorrectly creates two records where there should be one. When uncertain, use a single transaction and note the ambiguity.

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

ACQUIRER TYPE — PE_PORTFOLIO determination

Set acquirer_type = PE_PORTFOLIO when ANY of the following language signals are present in the source text describing the acquirer:
- "portfolio company of [X]"
- "[X] portfolio company"
- "[X]-backed [Acquirer]"
- "backed by [X]"
- "owned by [X Capital / X Partners / X Equity / X Holdings]"
- "[X], a [type] portfolio company"
- "platform company of [X]"
- "[X] platform"
- Direct statement that acquirer is operating under a private equity sponsor
- A stated parent-subsidiary relationship where the parent is identifiable as a PE firm

Do NOT set PE_PORTFOLIO when:
- The acquirer is a publicly-traded company that happens to have a PE investor (that's STRATEGIC_CORPORATE)
- A PE firm is mentioned in the PR but not as the backer of the acquiring entity (e.g., advisor or co-investor)
- The acquirer is itself a PE firm directly making the acquisition (that's PRIVATE_EQUITY, not PE_PORTFOLIO)

Distinguishing PRIVATE_EQUITY vs PE_PORTFOLIO:
- PRIVATE_EQUITY: the PE firm is the named acquirer ("Bain Capital acquired X")
- PE_PORTFOLIO: a portfolio company of the PE firm is the named acquirer ("Acme Holdings, a Bain Capital portfolio company, acquired X")

When acquirer_type = PE_PORTFOLIO is set, you MUST also populate acquirer_sponsor_name.

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

EVENT_TYPE — what kind of PR is this?

event_type reports the source observation type — what kind of press release this is. It is NOT the deal's lifecycle status. The deal's lifecycle status is derived from closed_date by downstream logic.

Rules for assignment:

ANNOUNCEMENT: this is the first public release announcing the deal. The PR uses framing like "today announced an agreement to acquire," "today announced the acquisition of," or similar. Use ANNOUNCEMENT even when the deal is also closed at announcement (common for same-day private deals). Set closed_date when the PR text indicates the deal is already done; leave null when the deal is pending.

CLOSE: this is a separate release reporting that a previously-announced deal has closed. The PR explicitly references that the deal was previously announced ("originally announced on [date]," "previously announced," "today completed the previously announced..."). Closed_date is the date of completion; announced_date is the original date if recoverable from the text.

AMENDMENT: a release reporting changes to a previously-announced deal (price change, structure change, regulatory milestones, extended deadlines).

TERMINATION: a release reporting that a previously-announced deal will not close (mutual termination, regulatory rejection, financing failure).

RUMOR: pre-announcement reporting on potential deals; not formal press releases by parties.

Same-day announce-and-close handling:
A common private-deal pattern is "[Acquirer] today announced the acquisition of [Target]" using past-tense action language. The deal is announced today AND closed today. Set:
- event_type = ANNOUNCEMENT (it IS an announcement-type PR, even though the deal is also closed)
- announced_date = release date
- closed_date = release date (same-day close indicated by past-tense action verb)

Do NOT set event_type=CLOSE for these. CLOSE is reserved for separate, later releases announcing completion of a previously-announced deal.

When in doubt: if the PR is the first time this deal is being publicly announced, event_type=ANNOUNCEMENT regardless of close status.

DATES:
- announced_date — the date the deal was first announced (ISO 8601, YYYY-MM-DD)
- closed_date — the date the deal closed. See rule below.
- signing_date — the date a definitive agreement was signed, if distinct from announced_date

announced_date rules:
- For ANNOUNCEMENT event_type, announced_date is typically the release's own date (published_date input). If the text explicitly states a different announcement date, use that.
- For CLOSE, AMENDMENT, or TERMINATION event_type, extract the ORIGINAL announcement date from the text if referenced (e.g., "previously announced on January 15, 2026"). If not referenced in the text, leave announced_date null.

closed_date rules:
- For ANNOUNCEMENT event_type: null when the deal is pending (future close). Populate closed_date when the PR uses past-tense completive action language indicating the deal is already done ("has acquired," "today acquired," "announced the acquisition of" with past-tense verb) — this is the same-day announce-and-close pattern. Set closed_date = announced_date.
- For CLOSE event_type: this is a separate later release reporting completion. Extract the explicit close date if stated. If no separate date is stated, closed_date = published_date.
- For AMENDMENT or TERMINATION event_type, closed_date is null (the deal is not closed).
- Do NOT convert prospective phrases like "expected to close in Q3 2026" to a specific date. Leave closed_date null and note the quarter in notes.

DEAL VALUE:
- value_amount — the total transaction value as a number (no currency symbols, no commas). Null if not stated.
- value_currency — ISO 4217 code (USD, EUR, GBP, JPY, etc.). Default to USD if the release is from a US company with no contrary indication.
- value_type — see enum below. This is how the value is characterized in the source.
- per_share_price — for transactions involving public targets, the per-share cash (or cash-equivalent) price paid to target shareholders. Null for other deal types unless relevant.
- value_qualifier — short string capturing qualifying language around the value: "approximately", "up to", "in excess of", "$400-500M range", or null if precise.

VALUE TYPE — determination

Pick exactly one value_type per transaction. Apply the rules in priority order:

1. UNDISCLOSED — when the source text states no monetary value at all, OR explicitly says terms were not disclosed.
   - Signals: "terms were not disclosed", "financial terms were not disclosed", "undisclosed terms", "for an undisclosed amount"
   - When UNDISCLOSED, value_amount must be NULL.

2. ENTERPRISE_VALUE — when the source text uses "enterprise value" language directly, OR when value clearly includes debt assumed.
   - Signals: "enterprise value of $X", "EV of $X", "total enterprise value", "deal valued at $X including assumed debt", "X times EBITDA" (multiple of EBITDA implies EV)

3. EQUITY_VALUE — apply when ANY of the following hold:
   a. Source text uses "equity value" language directly: "equity value of $X", "stockholders' equity", "for the equity"
   b. Per-share offer price language: "$X per share", "offer of $X per share to shareholders"
   c. Partial-stake acquisition where pct_acquired is stated and less than 100%: the value reflects payment for the acquired equity stake, not whole-company. Override TRANSACTION_VALUE default in this case.
   d. Financial services target (banks, insurance, asset managers, broker-dealers): industry convention is equity value, not enterprise value. Banks are valued on P/B and P/TBV; insurance on book value or embedded value. Use EQUITY_VALUE when the target's primary business is regulated financial services unless source text explicitly states ENTERPRISE_VALUE.
   e. Take-private of public company where consideration is described per-share: aggregate value is implicit equity value (offer price × shares outstanding).
   f. Stock-for-stock deals where consideration is shares of the acquirer: equity-for-equity exchange, EQUITY_VALUE.

4. TRANSACTION_VALUE — default when value is stated but type is not explicit AND none of the EQUITY_VALUE conditions above apply.
   - Common signals: "transaction valued at $X", "deal worth $X", "$X transaction"
   - This is the safe default for strategic acquisitions of standalone companies where consideration is cash and no per-share or partial-stake framing is present.

Decision priority: when multiple rules could apply, prioritize:
- Explicit value-type language (rule 2 or 3a) overrides defaults
- Partial-stake (3c) overrides TRANSACTION_VALUE default
- Financial services convention (3d) overrides TRANSACTION_VALUE default
- TRANSACTION_VALUE applies only when nothing more specific fits

Set value_type_confidence to:
- HIGH when an explicit value-type signal is present in source text
- MEDIUM when applying a convention rule (3c, 3d, 3e, 3f) without explicit text confirmation
- LOW when the source text is ambiguous and the model is making a best-guess assignment

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

ACQUIRER_SPONSOR_NAME:
- The PE sponsor(s) backing the acquirer entity.
- Populate when acquirer_type = PE_PORTFOLIO (always) or acquirer_type = PRIVATE_EQUITY (when the named acquirer's sponsor parent is also identified).
- Single sponsor: store the sponsor name as a string ("Bain Capital").
- Multiple sponsors (co-investor structures, common in PE recaps and minority deals): comma-delimited list ("New State Capital Partners, Amethyst Capital Group").

Source signals for sponsor name extraction:
- "[Acquirer], a portfolio company of [SPONSOR]" → SPONSOR
- "[Acquirer], a [SPONSOR]-backed company" → SPONSOR
- "[Acquirer], owned by [SPONSOR]" → SPONSOR
- "majority investment from [SPONSOR-1] and [SPONSOR-2]" → "SPONSOR-1, SPONSOR-2"
- "co-led by [SPONSOR-1] and [SPONSOR-2]" → "SPONSOR-1, SPONSOR-2"
- About-section text: "[X Capital] is a private investment firm... [X Capital] portfolio companies include..." → confirms sponsor identity when ambiguous

LEAVE NULL when:
- acquirer_type is STRATEGIC_CORPORATE, INDIVIDUAL, MANAGEMENT, EMPLOYEE_GROUP, or other non-PE categories
- acquirer_type is PRIVATE_EQUITY and the named acquirer IS the sponsor (no additional parent to capture)
- acquirer_type is PE_PORTFOLIO but the source does not name the sponsor (rare; flag in notes)

Strip legal suffixes from sponsor names: "Bain Capital, LP" → "Bain Capital". "Audax Group, LLC" → "Audax Group". This matches our normalization for other entity names and improves forward-compat with entity resolution.

EXTRACTION RULES:

- Do not infer values from multiples. If the release states "the transaction represents 10x EBITDA," do not reverse-calculate the EBITDA.
- Do not convert currencies. Capture value and currency as stated.
- Convert abbreviated figures correctly: "$500 million" → 500000000, "$2.5 billion" → 2500000000.
- Use null for any field not explicitly stated in the text. Do not guess.
- If multiple conflicting values appear, use the most recently stated or most specific figure, and note the conflict.
- For tickers, use exchange:symbol format. If only a symbol is given without an exchange, use UNKNOWN:SYMBOL.

RESPONSE FORMAT

Return a JSON object with a top-level "transactions" array. No prose, no Markdown code fences, no preamble. For single-transaction PRs, return an array of length 1. For multi-transaction PRs, return one element per distinct deal.

{
  "transactions": [
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
        "description": "a publicly-traded SaaS platform providing business management tools to landscaping companies",
        "sponsor_name": null
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
      "notes": null
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}

All fields within each transaction element are required. Use null for optional fields that have no value. "prompt_version" is at the top level (single value for the entire response); it is returned unchanged from the value passed in the user prompt.
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
    "description": "a leading provider of industrial automation solutions",
    "sponsor_name": null
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
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
    {
  "target": {"name": "Beta Industries", "domain": null, "ticker": null, "description": "a privately held manufacturer of specialty valves headquartered in Dallas, Texas"},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": "NASDAQ:ACME", "type": "STRATEGIC_CORPORATE", "description": "a leading provider of industrial automation solutions", "sponsor_name": null},
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
  "notes": "Unqualified '$500M in cash' defaults to TRANSACTION_VALUE; revenue qualified 'approximately' but only figure given"
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
    {
  "target": {"name": "Acme Corp", "domain": null, "ticker": "NYSE:ACME", "description": null},
  "acquirer": {"name": "Zenith Capital Partners", "domain": null, "ticker": null, "type": "PRIVATE_EQUITY", "description": null, "sponsor_name": null},
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
  "notes": "Enterprise value explicitly stated; PUBLIC target + PE acquirer — downstream derives Take-Private flag. No About sections in source; descriptions null."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
    {
  "target": {"name": "Industrial Coatings Division", "domain": null, "ticker": null, "description": null},
  "acquirer": {"name": "Delta Holdings", "domain": null, "ticker": null, "type": "PE_PORTFOLIO", "description": null, "sponsor_name": null},
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
  "notes": "EQUITY_VALUE explicit ('representing the equity value'); debt assumption mentioned separately ($200M) not included in $1.2B. Delta Holdings characterized as 'PE portfolio company' in release. LTM from 'twelve months ended 12/31/2025'. No About sections; descriptions null."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
    {
  "target": {"name": "Beta Industries", "domain": null, "ticker": null, "description": null},
  "acquirer": {"name": "Acme Corp", "domain": null, "ticker": null, "type": "STRATEGIC_CORPORATE", "description": null, "sponsor_name": null},
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
  "notes": "Closing release; acquirer_type inferred as STRATEGIC_CORPORATE from context but not stated (low confidence). Original announcement date not stated."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 5 — Simultaneous announce-and-close, private-to-private:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: STANDALONE_COMPANY
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-15

TITLE: XYZ Capital Acquires ABC Widgets
BODY: XYZ Capital, a private equity firm based in Chicago, announced today it has acquired ABC Widgets, a privately held manufacturer of industrial fasteners headquartered in Ohio. Terms of the transaction were not disclosed. ABC Widgets will continue to operate under its existing management team.
```

Output:
```json
{
  "transactions": [
    {
  "target": {"name": "ABC Widgets", "domain": null, "ticker": null, "description": "a privately held manufacturer of industrial fasteners headquartered in Ohio"},
  "acquirer": {"name": "XYZ Capital", "domain": null, "ticker": null, "type": "PRIVATE_EQUITY", "description": "a private equity firm based in Chicago", "sponsor_name": null},
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
  "notes": "Past-tense completive language ('has acquired'); no separate close date stated. Simultaneous announce-and-close: closed_date = announced_date."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
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
    "description": "a private investment firm specializing in environmental markets including wetland and stream mitigation banking, conservation easements, and carbon credit projects",
    "sponsor_name": null
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
  "notes": "Asset portfolio acquisition (12 mitigation banks); not a going-concern subsidiary. ASSETS target_type appropriate. Terms not disclosed."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
  "transactions": [
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
    "description": "a publicly-traded company specializing in vision systems for the automotive and autonomous driving industries",
    "sponsor_name": null
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
  "notes": "Partial acquisition (51%) for stock consideration; $17.5M is purchase price for 51% stake (EQUITY_VALUE for the acquired portion). pct_acquired = 51.0."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 8 — Single-sponsor PE add-on (PE_PORTFOLIO acquirer with acquirer_sponsor_name):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-23

TITLE: PremiStar Acquires Armistead Mechanical
BODY: PremiStar, LLC ("PremiStar"), a portfolio company of Audax Private Equity, today announced the acquisition of Armistead Mechanical, Inc., a privately-held provider of HVAC and mechanical services to commercial customers in the Mid-Atlantic. Terms were not disclosed.

About PremiStar
PremiStar is a national platform of HVAC and mechanical services companies, backed by Audax Private Equity.

About Armistead Mechanical
Armistead Mechanical, founded in 1962, is a leading commercial HVAC and mechanical services contractor headquartered in New Jersey.
```

Output:
```json
{
  "transactions": [
    {
  "target": {
    "name": "Armistead Mechanical, Inc.",
    "domain": null,
    "ticker": null,
    "description": "a privately-held HVAC and mechanical services contractor headquartered in New Jersey, founded in 1962, serving commercial customers in the Mid-Atlantic"
  },
  "acquirer": {
    "name": "PremiStar, LLC",
    "domain": null,
    "ticker": null,
    "type": "PE_PORTFOLIO",
    "description": "a national platform of HVAC and mechanical services companies, backed by Audax Private Equity",
    "sponsor_name": "Audax Private Equity"
  },
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
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
    "acquirer_type": "HIGH",
    "announced_date": "HIGH",
    "value_amount": "HIGH"
  },
  "notes": "Add-on acquisition: PE_PORTFOLIO acquirer (PremiStar) backed by Audax Private Equity acquires standalone target."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 9 — Multi-sponsor co-investor recap (comma-delimited acquirer_sponsor_name):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-01-21

TITLE: Harrell-Fish Receives Majority Investment from New State Capital Partners and Amethyst Capital Group
BODY: GLC Advisors & Co. ("GLC"), a leading independent investment banking advisory firm, is pleased to announce that it served as the exclusive financial advisor to Harrell-Fish Inc. ("HFI") in its majority investment from New State Capital Partners ("New State") and Amethyst Capital Group, LLC. The transaction supports HFI's continued organic growth, adds deep operating resources, and enables future M&A expansion opportunities.

About New State Capital Partners
New State Capital Partners is a private equity firm focused on lower-middle-market investments across business services, industrials, and consumer sectors.

About Amethyst Capital Group
Amethyst Capital Group is a private investment firm specializing in growth-stage industrial and services platforms.
```

Output:
```json
{
  "transactions": [
    {
  "target": {
    "name": "Harrell-Fish Inc.",
    "domain": null,
    "ticker": null,
    "description": null
  },
  "acquirer": {
    "name": "New State Capital Partners and Amethyst Capital Group",
    "domain": null,
    "ticker": null,
    "type": "PRIVATE_EQUITY",
    "description": "two co-investing private equity firms; New State Capital is focused on lower-middle-market business services, industrials, and consumer; Amethyst Capital specializes in growth-stage industrial and services platforms",
    "sponsor_name": "New State Capital Partners, Amethyst Capital Group"
  },
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
  "dates": {"announced_date": "2026-01-21", "closed_date": null, "signing_date": null},
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
  "notes": "Majority recapitalization with co-investor structure. Both sponsors are direct PE acquirers (PRIVATE_EQUITY, not PE_PORTFOLIO) — neither is a portfolio-company shell. acquirer_sponsor_name captures both sponsors comma-delimited. Founders/management likely retain minority equity in this recap structure."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 10 — Partial acquisition: EQUITY_VALUE per partial-stake rule (rule 3c):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-23

TITLE: Foresight Autonomous Holdings Announces Agreement to Acquire 51% of VisionWave Holdings
BODY: Foresight Autonomous Holdings Ltd. (NASDAQ: FRSX) today announced an agreement to acquire 51% of VisionWave Holdings, Inc. for $17.5 million in Foresight common stock.
```

Output:
```json
{
  "transactions": [
    {
  "target": {"name": "VisionWave Holdings, Inc.", "domain": null, "ticker": null, "description": null},
  "acquirer": {
    "name": "Foresight Autonomous Holdings Ltd.",
    "domain": null,
    "ticker": "NASDAQ:FRSX",
    "type": "STRATEGIC_CORPORATE",
    "description": null,
    "sponsor_name": null
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
    "qualifier": "for 51% of equity"
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
  "notes": "Partial acquisition (51%); $17.5M consideration is for the acquired equity stake, not whole-company. value_type = EQUITY_VALUE per partial-stake rule (3c). Implies $34.3M total equity value of target."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 11 — Financial services target: EQUITY_VALUE per industry convention (rule 3d):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PUBLIC
PUBLISHED DATE: 2026-04-22

TITLE: Peoples Bancorp Inc. to Acquire Citizens National Corporation
BODY: Peoples Bancorp Inc. (NASDAQ: PEBO) and Citizens National Corporation announced today a definitive agreement under which Peoples will acquire Citizens National in a cash and stock transaction valued at approximately $148 million. Citizens National is the holding company for Citizens National Bank, a community bank serving West Virginia and Kentucky with $1.2 billion in assets.
```

Output:
```json
{
  "transactions": [
    {
  "target": {
    "name": "Citizens National Corporation",
    "domain": null,
    "ticker": null,
    "description": "the holding company for Citizens National Bank, a community bank serving West Virginia and Kentucky with $1.2 billion in assets"
  },
  "acquirer": {
    "name": "Peoples Bancorp Inc.",
    "domain": null,
    "ticker": "NASDAQ:PEBO",
    "type": "STRATEGIC_CORPORATE",
    "description": null,
    "sponsor_name": null
  },
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
  "dates": {"announced_date": "2026-04-22", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 148000000,
    "currency": "USD",
    "type": "EQUITY_VALUE",
    "type_confidence": "MEDIUM",
    "per_share_price": null,
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
  "notes": "Bank holding company acquisition. Financial services convention: banks valued on equity value (P/B, P/TBV), not enterprise value. value_type = EQUITY_VALUE per industry convention rule (3d). Confidence MEDIUM because source text does not explicitly say 'equity value' but industry convention is unambiguous for community banks."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 12 — Take-private with per-share offer: EQUITY_VALUE (rules 3b + 3e):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PUBLIC
PUBLISHED DATE: 2026-04-15

TITLE: Acme Capital to Acquire BetaCo for $42 Per Share in Cash
BODY: Acme Capital Partners today announced an agreement to acquire BetaCo, Inc. (NASDAQ: BETA) for $42.00 per share in cash, representing a 35% premium to BetaCo's closing price of $31.10 on April 14. The transaction values BetaCo at approximately $2.4 billion, with closing expected in Q3 2026 subject to BetaCo shareholder approval and regulatory clearances.
```

Output:
```json
{
  "transactions": [
    {
  "target": {"name": "BetaCo, Inc.", "domain": null, "ticker": "NASDAQ:BETA", "description": null},
  "acquirer": {
    "name": "Acme Capital Partners",
    "domain": null,
    "ticker": null,
    "type": "PRIVATE_EQUITY",
    "description": null,
    "sponsor_name": null
  },
  "parent_seller": {"name": null, "ticker": null, "description": null},
  "deal": {"target_type": "STANDALONE_COMPANY", "pct_acquired": null},
  "dates": {"announced_date": "2026-04-15", "closed_date": null, "signing_date": null},
  "value": {
    "amount": 2400000000,
    "currency": "USD",
    "type": "EQUITY_VALUE",
    "type_confidence": "HIGH",
    "per_share_price": 42.00,
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
  "notes": "Take-private at $42/share cash, 35% premium. $2.4B aggregate value reflects equity acquired (per-share price × shares outstanding); value_type = EQUITY_VALUE per per-share rule (3b) and take-private rule (3e). Acquirer is PE; PUBLIC target + PE acquirer → is_take_private derived downstream."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
}
```

**Example 13 — Multi-transaction PR (two distinct targets, two deals):**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
DEAL TYPE: ACQUISITION
SPIN SPLIT TYPE: null
TARGET TYPE: ASSETS
EVENT TYPE: ANNOUNCEMENT
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-30

TITLE: Action Environmental Services Acquires NYC Assets of ADM Environmental Group and M&M Sanitation Services
BODY: Action Environmental Services today announced the acquisition of select New York City assets of two independent operators: ADM Environmental Group and M&M Sanitation Services. Each transaction was negotiated separately and closes today, expanding Action's NYC service capacity. Financial terms of each transaction were not disclosed.

About Action Environmental Services
Action Environmental Services is a full-service environmental and waste management company serving commercial and industrial customers in the greater New York metropolitan area.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "Select New York City assets of ADM Environmental Group",
        "domain": null,
        "ticker": null,
        "description": "waste collection assets in New York City operated by ADM Environmental Group"
      },
      "acquirer": {
        "name": "Action Environmental Services",
        "domain": null,
        "ticker": null,
        "type": "STRATEGIC_CORPORATE",
        "description": "a full-service environmental and waste management company serving commercial and industrial customers in the greater New York metropolitan area",
        "sponsor_name": null
      },
      "parent_seller": {"name": "ADM Environmental Group", "ticker": null, "description": null},
      "deal": {"target_type": "ASSETS", "pct_acquired": null},
      "dates": {"announced_date": "2026-04-30", "closed_date": "2026-04-30", "signing_date": null},
      "value": {"amount": null, "currency": null, "type": "UNDISCLOSED", "type_confidence": "HIGH", "per_share_price": null, "qualifier": null},
      "target_financials": {"revenue_amount": null, "revenue_period_type": null, "revenue_period_end": null, "ebitda_amount": null, "ebitda_period_type": null, "ebitda_period_end": null, "currency": null},
      "model_confidence": "HIGH",
      "field_confidence": {"target_name": "HIGH", "acquirer_name": "HIGH", "acquirer_type": "HIGH", "announced_date": "HIGH", "value_amount": "HIGH"},
      "notes": "Multi-transaction PR; deal 1 of 2. ADM Environmental NYC assets negotiated and closed separately from M&M Sanitation deal in same announcement. Same-day announce-and-close."
    },
    {
      "target": {
        "name": "Select New York City assets of M&M Sanitation Services",
        "domain": null,
        "ticker": null,
        "description": "waste collection assets in New York City operated by M&M Sanitation Services"
      },
      "acquirer": {
        "name": "Action Environmental Services",
        "domain": null,
        "ticker": null,
        "type": "STRATEGIC_CORPORATE",
        "description": "a full-service environmental and waste management company serving commercial and industrial customers in the greater New York metropolitan area",
        "sponsor_name": null
      },
      "parent_seller": {"name": "M&M Sanitation Services", "ticker": null, "description": null},
      "deal": {"target_type": "ASSETS", "pct_acquired": null},
      "dates": {"announced_date": "2026-04-30", "closed_date": "2026-04-30", "signing_date": null},
      "value": {"amount": null, "currency": null, "type": "UNDISCLOSED", "type_confidence": "HIGH", "per_share_price": null, "qualifier": null},
      "target_financials": {"revenue_amount": null, "revenue_period_type": null, "revenue_period_end": null, "ebitda_amount": null, "ebitda_period_type": null, "ebitda_period_end": null, "currency": null},
      "model_confidence": "HIGH",
      "field_confidence": {"target_name": "HIGH", "acquirer_name": "HIGH", "acquirer_type": "HIGH", "announced_date": "HIGH", "value_amount": "HIGH"},
      "notes": "Multi-transaction PR; deal 2 of 2. M&M Sanitation NYC assets negotiated and closed separately from ADM Environmental deal in same announcement. Same-day announce-and-close."
    }
  ],
  "prompt_version": "high_confidence_extraction:0.9"
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
| 0.6 | 2026-04-29 | Tightened acquirer_type=PE_PORTFOLIO detection with concrete language signals (10+ phrases including "portfolio company of," "X-backed," "X platform"). Added acquirer_sponsor_name extraction with single-sponsor and multi-sponsor (comma-delimited) handling. Updated RESPONSE FORMAT and §6 output schema to include sponsor_name on acquirer object. Updated all seven existing examples with sponsor_name field. Added Example 8 (single-sponsor PE add-on: PremiStar/Armistead Mechanical) and Example 9 (multi-sponsor co-investor recap: Harrell-Fish). Addresses 14 missed add-ons from 100-PR review. |
| 0.8 | 2026-05-01 | Revised event_type semantics: event_type reports the kind of PR (source observation type), not deal lifecycle status. Same-day announce-and-close PRs are now event_type=ANNOUNCEMENT with closed_date populated (was CLOSE in v0.4–0.7). transaction_status is derived downstream from closed_date. Added EVENT_TYPE section with explicit ANNOUNCEMENT/CLOSE/AMENDMENT/TERMINATION/RUMOR rules and same-day handling. Updated closed_date rules to handle ANNOUNCEMENT + past-tense completive language. Updated Example 5 (simultaneous announce-and-close): input EVENT TYPE changed from CLOSE to ANNOUNCEMENT; output dates unchanged (closed_date = announced_date). |
| 0.7 | 2026-04-29 | Tightened value_type determination with priority-ordered rules (UNDISCLOSED → ENTERPRISE_VALUE → EQUITY_VALUE → TRANSACTION_VALUE). Added explicit handling for partial-stake acquisitions (rule 3c: EQUITY_VALUE when pct_acquired < 100%), financial services convention (rule 3d: banks/insurance use equity value), per-share take-privates (rule 3e: aggregate is equity value), and stock-for-stock deals (rule 3f). Refined value_type_confidence guidance: HIGH for explicit text signals, MEDIUM for convention rules (3c–3f), LOW for ambiguous. Added Examples 10 (partial-stake Foresight/VisionWave), 11 (community bank Peoples/Citizens National), 12 (per-share take-private BetaCo). Addresses 3 patterns from 100-PR review where TRANSACTION_VALUE was assigned when EQUITY_VALUE was correct. |
| 0.9 | 2026-05-02 | Multi-transaction PR splitting: HC extraction now returns {"transactions": [...], "prompt_version": "..."} at the top level. Single-transaction PRs return arrays of length 1 (unified response shape). Multi-transaction PRs (multiple distinct targets in one PR) return one element per transaction. Added MULTI-TRANSACTION DETECTION section with decision rules. Added Example 13 (Action Environmental two-asset NYC acquisition). Wrapped all existing Examples 1–12 in transactions arrays. |
