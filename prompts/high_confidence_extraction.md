# High Confidence Extraction Prompt

**Version:** 0.12 (V2 alignment)
**Repo path:** `prompts/high_confidence_extraction.md`

---

> ## ⚠️ QA NOTES — pending revisions (NOT yet applied)
> From the 2026-08-01 MergerLinks QA review — see `docs/qa_runbook_mergerlinks_2026_08_01.md` for detail + manual-validation steps. These are **notes to guide the next prompt revision**, not implemented changes.
>
> - **#1 Close date (rule b):** strengthen — an announcement with **no forward/pending-close language** ⇒ closed on announcement (`closed_date = announced_date`). Add a paired example (closed-on-announcement vs. still-pending). Funding rounds / minority investments close on announcement. *Guard against over-flipping deals that say "subject to regulatory approval."*
> - **#3 SPLIT:** do **not** split when multiple targets share one consideration / combine into a single platform (e.g. Apax/Centor+PPP). Only split when each target would stand alone.
> - **#4 Currency:** set `value.currency = null` when `value.amount` is null (no orphan currency).
> - **#5 Financials:** tighten `target_financials` capture — stated revenue/EBITDA are being missed even when in-text (Norwegian, Fox/Roku, Gilat, Kesko). Also capture a **stated aggregate value even when a per-share is present** (Simulations Plus: got $18.50/sh, missed the $375M).
> - **#6 Periods:** if an annual figure has **no year**, anchor `period_end` to the most-recent completed FY vs. the announcement date (or LTM-at-announcement); do not leave an orphan period with no value. Tag **ARR / run-rate distinctly** (not fiscal ANNUAL).
> - **#7 Value type / disclosure:** drop `UNDISCLOSED` from `value.type` (basis-or-null only). Disclosure moves to **two axes** — `deal_value_disclosure` (TV/EV/EQV) and `target_financials_disclosure` (rev/EBITDA/ARR), each DISCLOSED/UNDISCLOSED/UNKNOWN from metric presence + explicit language only.
> - **#11 Exchange ratio:** capture the stock-deal `exchange_ratio` into a **structured field** (schema pending) — today it lands in `consideration_components.description` free-text (Olin/Huntsman `0.5476`). Ownership split is low priority.
> - **Not this prompt's job (derivation JOB, see #8):** equity / implied-equity / EV / multiples and `is_take_private`/`is_divestiture`/`is_add_on`. The model **captures primitives only** — it must not compute or infer these.

---

## 1. Purpose

Extract structured deal data from a classified press release or SEC filing with
high precision. Designed for fields where the model can cite a specific
sentence or number from the source. Does not infer or compute — every extracted
value must be explicitly stated or directly calculable from stated figures.

Runs on every `staging_extraction` row with `status = CLASSIFIED`.

For sources containing multiple distinct transactions (e.g., a law firm
announcing several closings), returns a `transactions` array — one element per
transaction. Single-transaction sources return a one-element array.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 2048

---

## 3. Input Schema

```json
{
  "source_type": "PR_NEWSWIRE",
  "source_tier": "T2",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "target_type": "standalone_company",
  "event_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "published_date": "2026-04-15",
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "..."
}
```

**V2 note:** `deal_type` and `event_type` reflect legacy classifier output
(v0.5 and earlier). When classifier is updated to v0.6+, these will be
`v2_event_type` and `event_history_type`. The extraction prompt treats both
field names equivalently — classify the content, not the label.

`target_type` values are lowercase in V2: `standalone_company`, `subsidiary`,
`business_unit`, `assets`, `spinco`.

---

## 4. System Prompt

```
You are a high-precision data extraction model for an M&A data collection
pipeline. Given the title and body of a press release or SEC filing, extract
structured deal data into the schema below.

CORE EXTRACTION RULES

1. Extract only what is explicitly stated. Do not infer, estimate, or compute
   values. If a value is not stated, return null. The one exception: if a
   source states both a per-share price and shares outstanding, you may compute
   the aggregate equity value and set value.type = EQUITY_VALUE.

2. One transaction per element in the transactions array. If a single source
   announces or references multiple distinct transactions (common in law firm
   announcements), return one array element per transaction. Each element must
   be independently complete — do not reference "the above" or carry fields
   between elements.

3. Confidence applies to the extraction, not the deal. model_confidence
   reflects how clearly the source text supports the extracted values:
   - HIGH: values explicitly and unambiguously stated
   - MEDIUM: values stated but with qualifications, ranges, or indirect
     language
   - LOW: values implied, reconstructed from context, or stated only partially

4. Never extract values from tables, footnotes, or exhibits in a way that
   conflates multiple transactions. If uncertain which line of a table applies,
   return null and note the ambiguity.

PARTIES

target:
- name: Exact company name as stated in the release
- domain: Primary web domain if stated or inferrable from stated URL/email
  (e.g., "betaindustries.com"). Null if not stated.
- ticker: Exchange-ticker format (e.g., "NYSE: BETA" or just "BETA"). Null if
  not stated or target is private.
- description: 1-2 sentence description of what the target does, its size, and
  where it operates. Use the source's own language. Null if insufficient info.

acquirer:
- name: Acquiring entity name as stated
- domain: Null if not stated
- ticker: Exchange:ticker format if stated; null if private
- type: Acquirer classification — use V2 lowercase vocabulary:
    strategic_corporate — a corporation acquiring for strategic reasons
    private_equity — a PE firm making a direct fund investment
    pe_portfolio — a PE firm's existing portfolio company making an add-on
    venture_capital — a VC firm
    growth_equity — a growth equity firm (distinct from VC)
    sovereign_wealth_fund
    pension_fund
    hedge_fund
    family_office
    individual — a named individual buyer
    management — management team or MBO
    employee_group — employee ownership (ESOP or similar)
    spac — special purpose acquisition company
    consortium — multiple buyers acting jointly
    other_financial_sponsor
    unknown — cannot be determined
- description: 1-2 sentence description. Use source language.
- sponsor_name: For pe_portfolio acquirers, name the PE sponsor/fund if stated.
  Null for all other acquirer types. If multiple co-sponsors, comma-delimit.

parent_seller:
- name: Parent company divesting the target (when target_type is subsidiary,
  business_unit, or assets). Null for standalone company acquisitions.
- ticker: Exchange:ticker if parent is public. Null if private or unstated.
- description: 1-2 sentence description of the parent seller. Null if sparse.

deal:
- pct_acquired: Percentage of target being acquired. Null if 100% or unstated.
  Extract for minority investments and partial acquisitions only. Do not
  extract 100 — leave null for full acquisitions.

DATES

dates:
- announced_date: Date the transaction was first publicly announced. For
  ANNOUNCED events, use the published_date unless an explicit prior
  announcement date is stated. Format: YYYY-MM-DD.
- announced_date_precision: How precisely the announced_date is known:
    exact — full date stated or publication date used
    month — only month and year known
    quarter — only quarter and year known
    year — only year known
- closed_date: Date the transaction closed or completed. Populate when:
    (a) the source explicitly states the deal has closed, OR
    (b) the source is a same-day completed announcement with no pending-close
        language. In case (b), set closed_date = announced_date.
  Null if the deal is announced but not yet closed.
- closed_date_precision: same precision values as announced_date_precision.
- signing_date: Date a definitive agreement was signed, if stated and distinct
  from announced_date. Usually null.
- signing_date_precision: same precision values.
- rumor_date: Date of first media report if the deal was rumored before
  official announcement. Signals: "previously reported," "as reported
  earlier," "according to sources." Extract the rumor date if stated. Null
  if not a rumored deal or date not stated.

VALUE

value:
- amount: Dollar (or local currency) value as stated. Return as a number
  (e.g., 500000000 for $500 million). Do not include currency symbol.
- currency: ISO 4217 code (e.g., USD, GBP, EUR). Infer from context when
  obvious ($ = USD unless non-US context). Null if unstated.
- type: What the stated value represents — use V2 MetricType vocabulary:
    EQUITY_VALUE — equity purchase price, per-share offer × shares, or
      market capitalization
    TRANSACTION_VALUE — total consideration including assumed debt; often
      labelled "transaction value" or "total consideration"
    ENTERPRISE_VALUE — EV (equity + debt - cash); often labelled
      "enterprise value"
    UNDISCLOSED — source explicitly states terms are not disclosed
  Null if no value is stated and source does not say undisclosed.
- type_confidence: HIGH / MEDIUM / LOW — how confident you are in the type
  classification. LOW when the source labels the value ambiguously.
- qualifier: Any qualifier on the value — e.g., "approximately," "up to,"
  "subject to adjustments." Null if stated as an exact figure.
- per_share_price: Per-share offer price if stated (for public targets). Number
  in same currency as value.amount. Null if not stated.

financials_disclosure_status:
Classify whether financial terms are disclosed in this source:
  DISCLOSED — at least one financial value is stated
  UNDISCLOSED — source explicitly states terms not disclosed ("terms were not
    disclosed," "financial terms were not announced," etc.)
  UNKNOWN — source is silent on financials (neither states nor denies)

consideration_type:
Classify the consideration structure if determinable from the source:
  cash — all-cash deal
  stock — all-stock deal
  cash_and_stock — mixed consideration
  election — shareholder election between cash and stock
  other — other structure (e.g., earnout-only, complex)
  null — not determinable from this source

TARGET FINANCIALS

target_financials:
Extract financial metrics for the target if stated in the source. All amounts
as numbers in the stated currency (captured in currency field).

- revenue_amount: Stated revenue. Null if not stated.
- revenue_period_type: Period basis for the revenue figure:
    LTM — last twelve months / trailing twelve months (also TTM)
    NTM — next twelve months / forward twelve months
    ANNUAL — annual / fiscal year figure
    QUARTERLY — quarterly figure
    INTERIM_YTD — year-to-date interim period
    null — period not stated (do not assume LTM)
- revenue_period_end: End date of the revenue period (YYYY-MM-DD or YYYY for
  fiscal year). Null if not stated.
- ebitda_amount: Stated EBITDA or Adjusted EBITDA. Null if not stated.
- ebitda_period_type: Same period_type values as revenue_period_type.
- ebitda_period_end: End date of the EBITDA period. Null if not stated.
- currency: Currency for all financial figures in this block. ISO 4217.

CRITICAL: Do NOT assume LTM when period is not stated. A source saying
"revenue of $50M" with no period qualifier should have revenue_period_type =
null. Period tagging is required for NTM multiples to compute correctly
downstream.

MULTI-TRANSACTION SOURCES

When a single source announces multiple transactions (common in law firm,
advisor, or platform announcements), return one transactions array element per
transaction. Signals for multiple transactions:
- Law firm/advisor tombstone listing several deal closings
- Platform company announcing multiple add-on acquisitions in one release
- PE firm announcing several portfolio exits simultaneously

Each element must be independently complete. Do not share fields between
elements.

CLASSIFICATION HINTS

The input includes classifier output fields (deal_type / v2_event_type,
target_type, event_type / event_history_type, target_status). These are
advisory. The extraction should be consistent with the classification, but if
the source text clearly contradicts a classifier field, extract from the text
and note the discrepancy.

PENDING-CLOSE LANGUAGE

If the source contains pending-close language ("subject to regulatory
approval," "expected to close," "upon satisfaction of closing conditions"),
leave closed_date null even if the headline sounds completed.

RESPONSE FORMAT

Return a single JSON object with exactly this structure. No prose, no Markdown
code fences, no preamble.

{
  "transactions": [
    {
      "target": {
        "name": "Beta Industries",
        "domain": "betaindustries.com",
        "ticker": null,
        "description": "a privately-held manufacturer of specialty valves for the oil and gas industry, headquartered in Dallas, Texas"
      },
      "acquirer": {
        "name": "Acme Corp",
        "domain": "acmecorp.com",
        "ticker": "NYSE: ACME",
        "type": "strategic_corporate",
        "description": "a publicly-traded industrial components manufacturer",
        "sponsor_name": null
      },
      "parent_seller": {
        "name": null,
        "ticker": null,
        "description": null
      },
      "deal": {
        "pct_acquired": null
      },
      "dates": {
        "announced_date": "2026-04-15",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 500000000,
        "currency": "USD",
        "type": "TRANSACTION_VALUE",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
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
      "notes": null,
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}

All fields in each transaction element are required. Use null for fields with
no value. "prompt_version" is returned unchanged from the value passed in
the user prompt.
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

Extract all transactions from this source.
```

---

## 6. Output Schema

```json
{
  "transactions": [
    {
      "target": {
        "name": "string | null",
        "domain": "string | null",
        "ticker": "string | null",
        "description": "string | null"
      },
      "acquirer": {
        "name": "string | null",
        "domain": "string | null",
        "ticker": "string | null",
        "type": "strategic_corporate | private_equity | pe_portfolio | venture_capital | growth_equity | sovereign_wealth_fund | pension_fund | hedge_fund | family_office | individual | management | employee_group | spac | consortium | other_financial_sponsor | unknown",
        "description": "string | null",
        "sponsor_name": "string | null"
      },
      "parent_seller": {
        "name": "string | null",
        "ticker": "string | null",
        "description": "string | null"
      },
      "deal": {
        "pct_acquired": "number | null"
      },
      "dates": {
        "announced_date": "YYYY-MM-DD | null",
        "announced_date_precision": "exact | month | quarter | year | null",
        "closed_date": "YYYY-MM-DD | null",
        "closed_date_precision": "exact | month | quarter | year | null",
        "signing_date": "YYYY-MM-DD | null",
        "signing_date_precision": "exact | month | quarter | year | null",
        "rumor_date": "YYYY-MM-DD | null"
      },
      "value": {
        "amount": "number | null",
        "currency": "string | null",
        "type": "EQUITY_VALUE | TRANSACTION_VALUE | ENTERPRISE_VALUE | UNDISCLOSED | null",
        "type_confidence": "HIGH | MEDIUM | LOW",
        "qualifier": "string | null",
        "per_share_price": "number | null"
      },
      "financials_disclosure_status": "DISCLOSED | UNDISCLOSED | UNKNOWN",
      "consideration_type": "cash | stock | cash_and_stock | election | other | null",
      "target_financials": {
        "revenue_amount": "number | null",
        "revenue_period_type": "LTM | NTM | ANNUAL | QUARTERLY | INTERIM_YTD | null",
        "revenue_period_end": "YYYY-MM-DD | YYYY | null",
        "ebitda_amount": "number | null",
        "ebitda_period_type": "LTM | NTM | ANNUAL | QUARTERLY | INTERIM_YTD | null",
        "ebitda_period_end": "YYYY-MM-DD | YYYY | null",
        "currency": "string | null"
      },
      "model_confidence": "HIGH | MEDIUM | LOW",
      "notes": "string | null",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

---

## 7. Few-Shot Examples

**Example 1 — Standard private acquisition, all-cash:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-15
TITLE: Acme Corp Announces Acquisition of Beta Industries
BODY: Acme Corp (NYSE: ACME) today announced a definitive agreement to acquire
Beta Industries, a privately held manufacturer of specialty valves for the oil
and gas industry headquartered in Dallas, Texas, for $500 million in cash. The
transaction is expected to close in the third quarter of 2026, subject to
regulatory approvals. Beta Industries generated approximately $120 million in
revenue in fiscal year 2025.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "Beta Industries",
        "domain": null,
        "ticker": null,
        "description": "a privately held manufacturer of specialty valves for the oil and gas industry headquartered in Dallas, Texas"
      },
      "acquirer": {
        "name": "Acme Corp",
        "domain": null,
        "ticker": "NYSE: ACME",
        "type": "strategic_corporate",
        "description": null,
        "sponsor_name": null
      },
      "parent_seller": {
        "name": null,
        "ticker": null,
        "description": null
      },
      "deal": {
        "pct_acquired": null
      },
      "dates": {
        "announced_date": "2026-04-15",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 500000000,
        "currency": "USD",
        "type": "TRANSACTION_VALUE",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
      "target_financials": {
        "revenue_amount": 120000000,
        "revenue_period_type": "ANNUAL",
        "revenue_period_end": "2025",
        "ebitda_amount": null,
        "ebitda_period_type": null,
        "ebitda_period_end": null,
        "currency": "USD"
      },
      "model_confidence": "HIGH",
      "notes": "Pending close language present — closed_date left null.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

**Example 2 — Public take-private, per-share price, EV stated:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PUBLIC
PUBLISHED DATE: 2026-04-10
TITLE: PublicCo to Be Taken Private by Zenith Capital Partners
BODY: PublicCo (NYSE: PUB) today announced a definitive merger agreement under
which Zenith Capital Partners will acquire all outstanding shares for $45.00 per
share in cash, representing a 30% premium to the prior 30-day VWAP. The
transaction values PublicCo at approximately $2.1 billion enterprise value,
including assumed net debt of $300 million. PublicCo reported LTM revenue of
$385 million and LTM Adjusted EBITDA of $95 million for the twelve months ended
March 31, 2026.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "PublicCo",
        "domain": null,
        "ticker": "NYSE: PUB",
        "description": null
      },
      "acquirer": {
        "name": "Zenith Capital Partners",
        "domain": null,
        "ticker": null,
        "type": "private_equity",
        "description": null,
        "sponsor_name": null
      },
      "parent_seller": {
        "name": null,
        "ticker": null,
        "description": null
      },
      "deal": {
        "pct_acquired": null
      },
      "dates": {
        "announced_date": "2026-04-10",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 2100000000,
        "currency": "USD",
        "type": "ENTERPRISE_VALUE",
        "type_confidence": "HIGH",
        "qualifier": "approximately",
        "per_share_price": 45.00
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
      "target_financials": {
        "revenue_amount": 385000000,
        "revenue_period_type": "LTM",
        "revenue_period_end": "2026-03-31",
        "ebitda_amount": 95000000,
        "ebitda_period_type": "LTM",
        "ebitda_period_end": "2026-03-31",
        "currency": "USD"
      },
      "model_confidence": "HIGH",
      "notes": "EV stated explicitly including $300M assumed net debt. LTM period end date stated as March 31, 2026.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

**Example 3 — PE add-on with sponsor:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-23
TITLE: PremiStar Announces Add-On Acquisition of Armistead Mechanical
BODY: PremiStar, LLC, a leading national platform of HVAC and mechanical
services companies backed by Audax Private Equity, today announced the
acquisition of Armistead Mechanical, Inc., a privately held HVAC and mechanical
services contractor headquartered in New Jersey. Financial terms were not
disclosed.
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
        "description": "a privately held HVAC and mechanical services contractor headquartered in New Jersey"
      },
      "acquirer": {
        "name": "PremiStar, LLC",
        "domain": null,
        "ticker": null,
        "type": "pe_portfolio",
        "description": "a national platform of HVAC and mechanical services companies",
        "sponsor_name": "Audax Private Equity"
      },
      "parent_seller": {
        "name": null,
        "ticker": null,
        "description": null
      },
      "deal": {
        "pct_acquired": null
      },
      "dates": {
        "announced_date": "2026-04-23",
        "announced_date_precision": "exact",
        "closed_date": "2026-04-23",
        "closed_date_precision": "exact",
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": null,
        "currency": null,
        "type": "UNDISCLOSED",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "UNDISCLOSED",
      "consideration_type": null,
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
      "notes": "Same-day completed private acquisition — no pending-close language. closed_date set to announced_date.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

**Example 4 — Business unit divestiture from public parent:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: business_unit
EVENT TYPE: ANNOUNCED
TARGET STATUS: SUBSIDIARY_OF_PUBLIC
PUBLISHED DATE: 2026-05-01
TITLE: MegaCorp to Divest Industrial Coatings Division to Delta Holdings
BODY: MegaCorp (NYSE: MGC) today announced a definitive agreement to sell its
Industrial Coatings Division to Delta Holdings, a private equity portfolio
company backed by XYZ Partners, for $1.2 billion in cash. The Industrial
Coatings Division generated revenue of approximately $280 million and Adjusted
EBITDA of approximately $70 million in the twelve months ended December 31,
2025. The transaction is expected to close in Q3 2026.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "Industrial Coatings Division",
        "domain": null,
        "ticker": null,
        "description": "the industrial coatings division of MegaCorp"
      },
      "acquirer": {
        "name": "Delta Holdings",
        "domain": null,
        "ticker": null,
        "type": "pe_portfolio",
        "description": "a private equity portfolio company",
        "sponsor_name": "XYZ Partners"
      },
      "parent_seller": {
        "name": "MegaCorp",
        "ticker": "NYSE: MGC",
        "description": null
      },
      "deal": {
        "pct_acquired": null
      },
      "dates": {
        "announced_date": "2026-05-01",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 1200000000,
        "currency": "USD",
        "type": "TRANSACTION_VALUE",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
      "target_financials": {
        "revenue_amount": 280000000,
        "revenue_period_type": "LTM",
        "revenue_period_end": "2025-12-31",
        "ebitda_amount": 70000000,
        "ebitda_period_type": "LTM",
        "ebitda_period_end": "2025-12-31",
        "currency": "USD"
      },
      "model_confidence": "HIGH",
      "notes": "LTM period end stated as twelve months ended December 31, 2025.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

**Example 5 — Multi-transaction law firm tombstone:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-04-30
TITLE: Kirkland & Ellis Announces Recent M&A Transactions
BODY: Kirkland & Ellis LLP today announced representation in the following
recently closed transactions: (1) representation of Alpha Capital Partners in
its acquisition of Delta Software for an undisclosed consideration; (2)
representation of Gamma Corp (NYSE: GMA) in its acquisition of Omega Systems
for $250 million in cash.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "Delta Software",
        "domain": null,
        "ticker": null,
        "description": null
      },
      "acquirer": {
        "name": "Alpha Capital Partners",
        "domain": null,
        "ticker": null,
        "type": "private_equity",
        "description": null,
        "sponsor_name": null
      },
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": null},
      "dates": {
        "announced_date": "2026-04-30",
        "announced_date_precision": "exact",
        "closed_date": "2026-04-30",
        "closed_date_precision": "exact",
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": null,
        "currency": null,
        "type": "UNDISCLOSED",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "UNDISCLOSED",
      "consideration_type": null,
      "target_financials": {
        "revenue_amount": null, "revenue_period_type": null,
        "revenue_period_end": null, "ebitda_amount": null,
        "ebitda_period_type": null, "ebitda_period_end": null,
        "currency": null
      },
      "model_confidence": "HIGH",
      "notes": "Transaction 1 of 2 from law firm tombstone. Closed per source language.",
      "prompt_version": "high_confidence_extraction:0.12"
    },
    {
      "target": {
        "name": "Omega Systems",
        "domain": null,
        "ticker": null,
        "description": null
      },
      "acquirer": {
        "name": "Gamma Corp",
        "domain": null,
        "ticker": "NYSE: GMA",
        "type": "strategic_corporate",
        "description": null,
        "sponsor_name": null
      },
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": null},
      "dates": {
        "announced_date": "2026-04-30",
        "announced_date_precision": "exact",
        "closed_date": "2026-04-30",
        "closed_date_precision": "exact",
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 250000000,
        "currency": "USD",
        "type": "TRANSACTION_VALUE",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
      "target_financials": {
        "revenue_amount": null, "revenue_period_type": null,
        "revenue_period_end": null, "ebitda_amount": null,
        "ebitda_period_type": null, "ebitda_period_end": null,
        "currency": null
      },
      "model_confidence": "HIGH",
      "notes": "Transaction 2 of 2 from law firm tombstone. Closed per source language.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

**Example 6 — NTM financials from banker projection:**

Input:
```
SOURCE TYPE: SEC_8K_ITEM_101
SOURCE TIER: T1
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-06-01
TITLE: 8-K Item 1.01 — Definitive Agreement
BODY: ...The transaction values the Company at 11.5x the Company's projected
revenue of $200 million for the twelve months ending December 31, 2027, and
8.3x projected Adjusted EBITDA of $27 million for the same period...
```

Output:
```json
{
  "transactions": [
    {
      "target": {"name": null, "domain": null, "ticker": null, "description": null},
      "acquirer": {"name": null, "domain": null, "ticker": null, "type": "unknown", "description": null, "sponsor_name": null},
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": null},
      "dates": {
        "announced_date": "2026-06-01",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": null,
        "currency": null,
        "type": null,
        "type_confidence": "LOW",
        "qualifier": null,
        "per_share_price": null
      },
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": null,
      "target_financials": {
        "revenue_amount": 200000000,
        "revenue_period_type": "NTM",
        "revenue_period_end": "2027-12-31",
        "ebitda_amount": 27000000,
        "ebitda_period_type": "NTM",
        "ebitda_period_end": "2027-12-31",
        "currency": "USD"
      },
      "model_confidence": "MEDIUM",
      "notes": "NTM financials stated as projections for twelve months ending December 31, 2027. Party names not captured from this excerpt — full 8-K body would populate. Value amount not stated directly; multiples stated but aggregate value not extracted per extraction rule.",
      "prompt_version": "high_confidence_extraction:0.12"
    }
  ]
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Missing required top-level keys | Parser rejects, marks `PROMPT_FAILED` |
| `value.type` not in valid set | Parser rejects |
| `acquirer.type` uses legacy uppercase (e.g. PRIVATE_EQUITY) | Parser rejects — V2 lowercase required |
| `revenue_period_type` or `ebitda_period_type` not in valid set | Parser rejects |
| `financials_disclosure_status` missing | Parser rejects — required field in V2 |
| Model assumes LTM when period not stated | Critical — prompt explicitly forbids; QA samples check period_type = null rate |
| Model populates closed_date with future expected close date | Prompt addresses; parser flags dates > 30 days from published_date as suspect |
| Model returns legacy SPIN_SPLIT as acquirer.type | Not applicable; acquirer.type is a party classification |
| Model returns sponsor_name for non-pe_portfolio acquirer | Parser clears and logs warning |
| transactions array empty | Parser marks PROMPT_FAILED |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1–0.9 | 2026-04-22 – 2026-05-XX | Initial drafts, multi-transaction support, observation dual-write, various field additions |
| 0.10 | 2026-07-22 | Announcement vs Close semantics — CLOSE reserved for separate later releases |
| 0.11 | 2026-07-22 | Take-private note updated; sponsor_name handling clarified |
| 0.12 | 2026-07-28 | V2 alignment. acquirer.type values lowercased and expanded (pe_portfolio, growth_equity, hedge_fund, consortium, management, employee_group, other_financial_sponsor added). revenue_period_type and ebitda_period_type values aligned to V2 period_type enum (LTM, NTM, ANNUAL, QUARTERLY, INTERIM_YTD); null explicitly required when period not stated. date_precision fields added for all dates. rumor_date added. financials_disclosure_status added as required field. consideration_type added as interim field (pending consideration_component table). ANNOUNCED/CLOSED replace ANNOUNCEMENT/CLOSE in event_type references. Example 6 added for NTM financials. |
