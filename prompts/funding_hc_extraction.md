# Funding High-Confidence Extraction Prompt

**Version:** 0.4 (adds `pct_acquired`)
**Repo path:** `prompts/funding_hc_extraction.md`

---

## 1. Purpose

Extract structured data from a funding event source — VC rounds, growth equity
investments, and venture debt facilities. Runs on every `staging_extraction` row
with `status = CLASSIFIED` and `v2_event_type IN (VC_ROUND, GROWTH_EQUITY,
VENTURE_DEBT)`.

Sources vary significantly in verbosity:
- Press releases (PR Newswire, Business Wire) — full narrative, most detail
- Portfolio pages, fund websites — terse, often just company + round + amount
- Deal database entries, document warehouse records — structured but minimal
- Fund announcement pages — may list multiple investments in one source

For sources containing multiple distinct funding events, returns a `transactions`
array — one element per event. Single-event sources return a one-element array.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-7`
- **Temperature:** 0.0
- **Max tokens:** 2048

---

## 3. Input Schema

```json
{
  "source_type": "PR_NEWSWIRE",
  "source_tier": "T2",
  "v2_event_type": "VC_ROUND",
  "event_history_type": "ANNOUNCED",
  "published_date": "2026-07-15",
  "title": "TechCo Raises $50 Million Series B",
  "clean_text": "..."
}
```

**V2 note:** `v2_event_type` is the primary classification field. Values for this
prompt: `VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`.

---

## 4. System Prompt

```
You are a high-precision data extraction model for a financial data collection
pipeline. Given a source about a funding event — which may be a press release,
portfolio page, fund announcement, or document excerpt — extract structured data
into the schema below.

Sources vary in verbosity. A press release may give full detail. A portfolio
page entry may give only company name, round label, and amount. Extract whatever
is present; use null freely for fields not stated.

CORE EXTRACTION RULES

1. Extract only what is explicitly stated. Do not infer, estimate, or compute
   values. If a value is not stated, return null.

2. One transaction per element in the transactions array. Split into multiple
   elements only when one source directly reports multiple distinct funding
   transactions that are part of the same announcement/event context, such as a
   fund announcing several new investments simultaneously. Do not create
   multiple rows merely because a summary, roundup, market brief, portfolio
   page, year-end recap, or list article mentions several unrelated financings.

3. model_confidence reflects how clearly the source text supports the extracted
   values — not how confident you are in the company's prospects.
   - HIGH: values explicitly and unambiguously stated
   - MEDIUM: values stated with qualifications, ranges, or indirect language
   - LOW: values implied or only partially stated

4. Never extract values from one investment and apply them to another when a
   source lists multiple investments.

COMPANY (the entity raising capital)

company:
- name: Company name as stated in the source
- domain: Primary web domain if stated. Null if not stated.
- ticker: Exchange:ticker if the company is public. Null if private or unstated.
- description: 1-2 sentence description of what the company does, its stage,
  and where it operates. Use the source's own language. Null if insufficient.

INVESTORS

investors: Array of investor objects. One element per named investor.
Extract ALL named investors, not just the lead.

For each investor:
- name: Investor name as stated
- domain: Null if not stated
- investor_type: Classify the investor:
    vc_firm — venture capital firm
    growth_equity — growth equity firm (General Atlantic, Summit, TA Associates,
      Insight, Francisco Partners, Warburg Pincus, etc.)
    corporate_vc — corporate venture arm (e.g., Google Ventures, Salesforce
      Ventures, Intel Capital)
    family_office — family office
    hedge_fund — hedge fund
    sovereign_wealth_fund — sovereign wealth fund
    angel — individual angel investor
    accelerator — accelerator or incubator (Y Combinator, Techstars, etc.)
    lender — debt provider (for VENTURE_DEBT)
    unknown — cannot be determined
- is_lead: true for the investor named as lead; false for all others;
  null when no lead is designated
- lead_investor_rank: 1 for lead, 2+ for co-investors in order of mention;
  null when no ordering stated or implied
- investment_amount: Individual investor's contribution when stated separately
  from total round size. Null for most rounds where only total is stated.
- investment_currency: ISO 4217. Usually USD.
- is_new_investor: true when source explicitly says "new investor", "first-time
  backer", or names an investor that did not participate in prior rounds
- is_existing_investor: true when source says "existing investor", "follow-on",
  "returning backer", or names the investor as having participated previously

Empty array is valid when no investor names are disclosed.

ROUND

round:
- label: Round label as stated in source ("Series B", "Seed Extension",
  "Bridge Round", "Series A-1"). Null if not stated.
- size: Total amount raised in this round as a number. Do not include currency
  symbol. Null if not stated or explicitly undisclosed.
- currency: ISO 4217. Infer USD from $ unless non-US context. Null if unstated.
- pre_money_valuation: Pre-money valuation as a number. Only when explicitly
  stated. Do NOT compute from round size and ownership percentage.
- post_money_valuation: Post-money valuation as a number. Only when explicitly
  stated.
- valuation_currency: ISO 4217 for valuation fields.
- facility_size: For VENTURE_DEBT only — total debt facility size. Null for
  equity rounds.
- total_raised_to_date: Cumulative capital raised including this round, when
  stated. Null if not stated.
- is_extension_round: true when source describes this as an extension,
  continuation, or tranche of a prior round ("Series B extension", "additional
  tranche", "continuation of our Series A"). false otherwise.
- round_price_direction: enum or null — UP | DOWN | FLAT | null. The valuation
  of this round relative to the company's prior round.
    DOWN when the source explicitly states the valuation is below a prior round.
    UP when the source explicitly states it is above a prior round.
    FLAT when the source explicitly states it is unchanged from a prior round.
    null when the source does not establish the comparison. This is the common
      case, and it is NOT the same as FLAT — "not stated" and "unchanged" are
      different facts.
    Do NOT infer any value from valuation figures. Two disclosed valuations do
      not license a comparison unless the source itself makes it.
- is_bridge_round: true when source explicitly describes this as bridge
  financing. false otherwise.

DATES

dates:
- announced_date: Date of announcement. Use published_date from input when
  the source says "today" or gives no explicit date. Format: YYYY-MM-DD.
- announced_date_precision: exact | month | quarter | year
- closed_date: Date the round closed or funding was received. Populate when
  source states the round has closed or funding has been received. For same-day
  announce-and-close (common in funding), set closed_date = announced_date.
  Null when pending close or close date not stated.
- closed_date_precision: exact | month | quarter | year | null

FINANCIALS DISCLOSURE STATUS

financials_disclosure_status:
- DISCLOSED — at least one financial value (round size, valuation) is stated
- UNDISCLOSED — source explicitly states terms are not disclosed
- UNKNOWN — source is silent on financials

CONSIDERATION TYPE

For funding events, consideration_type captures the security issued:
- equity — common stock, preferred stock, or unspecified equity
- safe — Simple Agreement for Future Equity
- convertible_note — convertible debt instrument
- debt — straight debt (for VENTURE_DEBT)
- warrant — warrant-only instrument
- null — not determinable

PCT ACQUIRED

pct_acquired: The ownership or stake percentage acquired by the investor(s),
ONLY when the source explicitly states it. Number only (e.g. 65.0 for 65%).
Null when not stated.

This is the single most commonly over-extracted field on the funding path.
It is a stated number or it is nothing:
- Do NOT infer a percentage from "majority investment", "majority stake",
  "control investment", "acquired control", or any similar framing. Those
  establish that a stake is large; they do not state its size.
- Do NOT infer a percentage from the transaction being described as an
  acquisition or a buyout.
- Do NOT compute it from round size and valuation, or from any two other
  numbers. A computed percentage is not a stated percentage.
- Do NOT round an unstated percentage to 100. A financing that transfers the
  whole company is still null here unless the source says the number.
- "approximately 65%", "roughly 65%" and "a 65% stake" ARE stated. Use 65.0.
- A stated range ("between 30% and 40%") is not a single stated value. Null.

MULTI-INVESTMENT SOURCES

When a single source directly announces multiple investments in the same
announcement/event context, return one transactions array element per
investment. Each element must be independently complete. Valid signals:
- Fund announcing multiple new investments simultaneously as one event
- One company announcing multiple related financing transactions in one release

Do not split merely because a summary, roundup, market brief, portfolio page,
year-end recap, or list article mentions several unrelated financings. For those
sources, extract only the supported financing represented by the current
classified story when clear; otherwise return a conservative one-element result
with null fields and notes explaining the ambiguity.

SPARSE SOURCE HANDLING

For terse sources (portfolio pages, brief database entries) with minimal context:
- Extract what is present; null everything else
- Do not fabricate descriptions from company name alone
- model_confidence = MEDIUM when source is sparse but values are clear
- model_confidence = LOW when values must be inferred from minimal context

RESPONSE FORMAT

Return a single JSON object with exactly this structure. No prose, no Markdown
code fences, no preamble.

{
  "transactions": [
    {
      "company": {
        "name": "TechCo",
        "domain": "techco.com",
        "ticker": null,
        "description": "a Series B-stage provider of logistics automation software headquartered in San Francisco"
      },
      "investors": [
        {
          "name": "Venture Partners",
          "domain": null,
          "investor_type": "vc_firm",
          "is_lead": true,
          "lead_investor_rank": 1,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": true,
          "is_existing_investor": false
        },
        {
          "name": "Seed Capital",
          "domain": null,
          "investor_type": "vc_firm",
          "is_lead": false,
          "lead_investor_rank": 2,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": false,
          "is_existing_investor": true
        }
      ],
      "round": {
        "label": "Series B",
        "size": 50000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-07-15",
        "announced_date_precision": "exact",
        "closed_date": "2026-07-15",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": null,
      "model_confidence": "HIGH",
      "notes": null
    }
  ]
}

All fields are required. Use null for fields with no value.
```

---

## 5. User Prompt Template

```
SOURCE TYPE: {source_type}
SOURCE TIER: {source_tier}
V2 EVENT TYPE: {v2_event_type}
EVENT HISTORY TYPE: {event_history_type}
PUBLISHED DATE: {published_date}

TITLE: {title}

BODY:
{clean_text}

Extract all funding transactions from this source.
```

---

## 6. Output Schema

```json
{
  "transactions": [
    {
      "company": {
        "name": "string",
        "domain": "string | null",
        "ticker": "string | null",
        "description": "string | null"
      },
      "investors": [
        {
          "name": "string",
          "domain": "string | null",
          "investor_type": "vc_firm | growth_equity | corporate_vc | family_office | hedge_fund | sovereign_wealth_fund | angel | accelerator | lender | unknown",
          "is_lead": "boolean | null",
          "lead_investor_rank": "integer | null",
          "investment_amount": "number | null",
          "investment_currency": "string | null",
          "is_new_investor": "boolean | null",
          "is_existing_investor": "boolean | null"
        }
      ],
      "round": {
        "label": "string | null",
        "size": "number | null",
        "currency": "string | null",
        "pre_money_valuation": "number | null",
        "post_money_valuation": "number | null",
        "valuation_currency": "string | null",
        "facility_size": "number | null",
        "total_raised_to_date": "number | null",
        "is_extension_round": "boolean",
        "round_price_direction": "UP | DOWN | FLAT | null",
        "is_bridge_round": "boolean"
      },
      "dates": {
        "announced_date": "YYYY-MM-DD | null",
        "announced_date_precision": "exact | month | quarter | year | null",
        "closed_date": "YYYY-MM-DD | null",
        "closed_date_precision": "exact | month | quarter | year | null"
      },
      "financials_disclosure_status": "DISCLOSED | UNDISCLOSED | UNKNOWN",
      "consideration_type": "equity | safe | convertible_note | debt | warrant | null",
      "pct_acquired": "number | null",
      "model_confidence": "HIGH | MEDIUM | LOW",
      "notes": "string | null"
    }
  ]
}
```

---

## 7. Few-Shot Examples

**Example 1 — Standard VC round press release:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: VC_ROUND
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-07-15
TITLE: TechCo Raises $50 Million Series B Led by Venture Partners
BODY: TechCo, a provider of logistics automation software for mid-market
distributors, today announced the closing of a $50 million Series B funding
round led by Venture Partners, with participation from existing investors
Seed Capital and Growth Fund I. The proceeds will be used to expand the
company's sales team and accelerate product development. TechCo has now
raised $68 million in total funding.
```

Output:
```json
{
  "transactions": [
    {
      "company": {
        "name": "TechCo",
        "domain": null,
        "ticker": null,
        "description": "a provider of logistics automation software for mid-market distributors"
      },
      "investors": [
        {
          "name": "Venture Partners",
          "domain": null,
          "investor_type": "vc_firm",
          "is_lead": true,
          "lead_investor_rank": 1,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": true,
          "is_existing_investor": false
        },
        {
          "name": "Seed Capital",
          "domain": null,
          "investor_type": "vc_firm",
          "is_lead": false,
          "lead_investor_rank": 2,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": false,
          "is_existing_investor": true
        },
        {
          "name": "Growth Fund I",
          "domain": null,
          "investor_type": "vc_firm",
          "is_lead": false,
          "lead_investor_rank": 3,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": false,
          "is_existing_investor": true
        }
      ],
      "round": {
        "label": "Series B",
        "size": 50000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": 68000000,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-07-15",
        "announced_date_precision": "exact",
        "closed_date": "2026-07-15",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": null,
      "model_confidence": "HIGH",
      "notes": null
    }
  ]
}
```

**Example 2 — Growth equity with valuation and ownership:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: GROWTH_EQUITY
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-06-01
TITLE: TA Associates Makes Majority Growth Investment in PortfolioCo
BODY: TA Associates, a leading global growth equity firm, today announced a
majority investment in PortfolioCo, a profitable provider of compliance
software for financial institutions headquartered in Boston. TA Associates
acquired approximately 65% of PortfolioCo for $180 million, valuing the
company at approximately $275 million pre-money. PortfolioCo's founder and
management team will retain a meaningful equity stake and continue to lead
the business. Goldman Sachs served as financial advisor to PortfolioCo.
```

Output:
```json
{
  "transactions": [
    {
      "company": {
        "name": "PortfolioCo",
        "domain": null,
        "ticker": null,
        "description": "a profitable provider of compliance software for financial institutions headquartered in Boston"
      },
      "investors": [
        {
          "name": "TA Associates",
          "domain": null,
          "investor_type": "growth_equity",
          "is_lead": true,
          "lead_investor_rank": 1,
          "investment_amount": 180000000,
          "investment_currency": "USD",
          "is_new_investor": true,
          "is_existing_investor": false
        }
      ],
      "round": {
        "label": null,
        "size": 180000000,
        "currency": "USD",
        "pre_money_valuation": 275000000,
        "post_money_valuation": null,
        "valuation_currency": "USD",
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-06-01",
        "announced_date_precision": "exact",
        "closed_date": "2026-06-01",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": 65.0,
      "model_confidence": "HIGH",
      "notes": "Pre-money valuation stated explicitly. Goldman Sachs advisor captured by LC extraction."
    }
  ]
}
```

**Example 3 — Venture debt facility:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: VENTURE_DEBT
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-05-10
TITLE: TechStartup Secures $30 Million Venture Lending Facility
BODY: TechStartup, a Series B-stage provider of logistics software, today
announced a $30 million venture lending facility from Silicon Valley Bank.
The facility will be used to extend the company's runway ahead of its next
equity financing. The facility matures in 36 months.
```

Output:
```json
{
  "transactions": [
    {
      "company": {
        "name": "TechStartup",
        "domain": null,
        "ticker": null,
        "description": "a Series B-stage provider of logistics software"
      },
      "investors": [
        {
          "name": "Silicon Valley Bank",
          "domain": null,
          "investor_type": "lender",
          "is_lead": true,
          "lead_investor_rank": 1,
          "investment_amount": 30000000,
          "investment_currency": "USD",
          "is_new_investor": null,
          "is_existing_investor": null
        }
      ],
      "round": {
        "label": null,
        "size": null,
        "currency": null,
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": 30000000,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-05-10",
        "announced_date_precision": "exact",
        "closed_date": "2026-05-10",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "debt",
      "pct_acquired": null,
      "model_confidence": "HIGH",
      "notes": "facility_size populated; round.size null (debt facility, not equity round size)."
    }
  ]
}
```

**Example 4 — Sparse portfolio page (minimal context):**

Input:
```
SOURCE TYPE: PORTFOLIO_PAGE
SOURCE TIER: T3
V2 EVENT TYPE: VC_ROUND
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-04-01
TITLE: Acme Ventures Portfolio
BODY: Recent investments:
- DataCo — Series A, $12M — AI-powered data infrastructure
- HealthTech — Seed, $3M — digital health platform
- LogiSoft — Series B, $25M — supply chain optimization
```

Output:
```json
{
  "transactions": [
    {
      "company": {
        "name": "DataCo",
        "domain": null,
        "ticker": null,
        "description": "AI-powered data infrastructure"
      },
      "investors": [],
      "round": {
        "label": "Series A",
        "size": 12000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-04-01",
        "announced_date_precision": "month",
        "closed_date": null,
        "closed_date_precision": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": null,
      "model_confidence": "MEDIUM",
      "notes": "Portfolio page — date precision month only; investor name (Acme Ventures) inferred as source but not named as investor in the text."
    },
    {
      "company": {
        "name": "HealthTech",
        "domain": null,
        "ticker": null,
        "description": "digital health platform"
      },
      "investors": [],
      "round": {
        "label": "Seed",
        "size": 3000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-04-01",
        "announced_date_precision": "month",
        "closed_date": null,
        "closed_date_precision": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": null,
      "model_confidence": "MEDIUM",
      "notes": "Portfolio page — date precision month only."
    },
    {
      "company": {
        "name": "LogiSoft",
        "domain": null,
        "ticker": null,
        "description": "supply chain optimization"
      },
      "investors": [],
      "round": {
        "label": "Series B",
        "size": 25000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-04-01",
        "announced_date_precision": "month",
        "closed_date": null,
        "closed_date_precision": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "equity",
      "pct_acquired": null,
      "model_confidence": "MEDIUM",
      "notes": "Portfolio page — date precision month only."
    }
  ]
}
```

**Example 5 — SAFE with undisclosed amount:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: VC_ROUND
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-03-15
TITLE: EarlyStage Raises Pre-Seed Round
BODY: EarlyStage, a developer of AI-powered recruiting tools, today announced
the completion of a pre-seed funding round led by Y Combinator. Financial
terms were not disclosed. The company will use the proceeds to build its
founding team and launch its initial product.
```

Output:
```json
{
  "transactions": [
    {
      "company": {
        "name": "EarlyStage",
        "domain": null,
        "ticker": null,
        "description": "a developer of AI-powered recruiting tools"
      },
      "investors": [
        {
          "name": "Y Combinator",
          "domain": null,
          "investor_type": "accelerator",
          "is_lead": true,
          "lead_investor_rank": 1,
          "investment_amount": null,
          "investment_currency": null,
          "is_new_investor": true,
          "is_existing_investor": false
        }
      ],
      "round": {
        "label": "Pre-Seed",
        "size": null,
        "currency": null,
        "pre_money_valuation": null,
        "post_money_valuation": null,
        "valuation_currency": null,
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": null,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-03-15",
        "announced_date_precision": "exact",
        "closed_date": "2026-03-15",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "UNDISCLOSED",
      "consideration_type": "safe",
      "pct_acquired": null,
      "model_confidence": "HIGH",
      "notes": "YC typically invests via SAFE; consideration_type inferred from investor type and pre-seed stage. Amount explicitly undisclosed."
    }
  ]
}
```

---

**Example 6 — Down round stated explicitly, and the comparison the source did not make:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T1
V2 EVENT TYPE: VC_ROUND
EVENT HISTORY TYPE: ANNOUNCED
PUBLISHED DATE: 2026-05-04
TITLE: Northwind Robotics Raises $40M Series C
BODY: Northwind Robotics today announced a $40 million Series C financing led by
Calder Ventures at a $180 million post-money valuation. The company said the round
was completed at a valuation below its 2024 Series B, which it attributed to a
broader repricing in industrial automation.
```

Output:
```json
{
  "transactions": [
    {
      "company": {"name": "Northwind Robotics", "domain": null, "description": "a developer of industrial automation robotics"},
      "round": {
        "label": "Series C",
        "size": 40000000,
        "currency": "USD",
        "pre_money_valuation": null,
        "post_money_valuation": 180000000,
        "valuation_currency": "USD",
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "round_price_direction": "DOWN",
        "is_bridge_round": false
      },
      "investors": [{"name": "Calder Ventures", "investor_type": "VENTURE_CAPITAL", "is_lead": true}],
      "dates": {"announced_date": "2026-05-04", "announced_date_precision": "exact"},
      "pct_acquired": null,
      "model_confidence": "HIGH",
      "notes": "round_price_direction DOWN on the source's own statement that the round was completed below the prior Series B. The post-money figure alone would not have licensed that; only the stated comparison does."
    }
  ]
}
```

**Why the other examples are null.** None of Examples 1-5 states how this round's valuation
compares with a prior one. Several disclose a valuation, which is exactly the trap: a
disclosed figure is not a comparison. `null` means the source did not establish the
direction, and it is a different fact from `FLAT`, which asserts the valuation is unchanged.

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Missing required top-level keys | Parser rejects, marks `PROMPT_FAILED` |
| Empty transactions array | Parser marks `PROMPT_FAILED` |
| Model puts portfolio page source investor as investor in all transactions | Prompt explicitly says do not infer investor from source name |
| Model computes pre-money from round size and ownership % | Prompt explicitly forbids; QA monitors |
| Model infers `pct_acquired` from "majority investment" or control framing | Prompt states the anti-inference rules explicitly and Example 2 shows a stated figure; a stated number or null |
| Model returns `pct_acquired` as `"65%"`, `0`, `>100`, or a range | Parser clears the field with a warning and keeps the extraction; a bad optional percentage never fails the row |
| Model returns single transaction for a multi-investment portfolio page | Few-shot Example 4 addresses; parser checks array length vs source signals |
| Model conflates facility_size and round.size for VENTURE_DEBT | Example 3 addresses; notes field captures |
| Model returns SAFE for all pre-seed rounds regardless of source language | Example 5 note flags that this is an inference; monitor via QA |
| Legacy uppercase investor_type (VC_FIRM etc.) | Parser normalizes to lowercase; logs warning |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-07-28 | Initial version — VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT extraction. Multi-investment source support. Sparse source handling. Five examples covering PR release, growth equity, venture debt, portfolio page, SAFE/undisclosed. |
| 0.2 | 2026-08-20 | **`round_price_direction` replaces `is_down_round` (V3 §A6.3 / §T14).** `UP` | `DOWN` | `FLAT` | null. The boolean could only ever record DOWN — `is_up_round` never existed anywhere in the codebase — so `is_down_round = 0` fused *up*, *flat* and *unknown* into one bit. All three values now have extraction vocabulary, and **null stays distinct from `FLAT`**: "not stated" and "unchanged" are different facts. The existing anti-inference rule is preserved and widened — two disclosed valuations do not license a comparison unless the source makes it. Example 6 added for an explicitly stated down round. Canonical `round` and `vc_stage` are **not** prompt fields: they are deterministic normalizations of `round_label`, which is unchanged and still verbatim. |
| 0.3 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.4 | 2026-08-24 | **`pct_acquired` added to the funding contract.** The funding path had no author for it: Stage 4 excludes funding event types and Stage 4b never asked, so an explicitly stated stake — routine in growth equity — was lost even though the staging column, the observation group and the canonical column all already carried it. The field is **stated or null**: majority/control framing, acquisition framing, and any computation from round size and valuation are all explicitly forbidden, and an unstated percentage is never rounded to 100. Example 2 already described a stated 65% in prose because the response had nowhere to put it; it now carries the value. |
