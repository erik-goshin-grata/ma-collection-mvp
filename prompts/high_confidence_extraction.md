# High Confidence Extraction Prompt

**Version:** 0.22 (input note describes the values the stage actually sends)
**Repo path:** `prompts/high_confidence_extraction.md`

---

> ## QA NOTES — from the 2026-08-01 MergerLinks review
> See `docs/qa_runbook_mergerlinks_2026_08_01.md` for detail + manual-validation steps.
>
> **✅ APPLIED in this revision** (active in the rules below):
> - **#1 Close date (rule b):** an announcement with **no forward/pending-close language** ⇒ closed on announcement (`closed_date = announced_date`); funding / minority close on announcement; **guard** against flipping deals with "subject to..." language. See `closed_date`.
> - **#4 Currency:** `value.currency = null` when `value.amount` is null (no orphan currency). See `value.currency`.
> - **#5 Financials:** capture stated revenue/EBITDA even when in running prose; capture a **stated aggregate value even when a per-share is present**. See `value.amount` and TARGET FINANCIALS *capture discipline*.
> - **#6 Periods:** anchor `period_end` to a stated year (else null, keep period_type); **ARR is not revenue** — don't record ARR in `revenue_amount`. See TARGET FINANCIALS.
>
> **⏳ STILL PENDING** (notes only — not yet implemented):
> - **#3 SPLIT:** do **not** split when multiple targets share one consideration / combine into a single platform (e.g. Apax/Centor+PPP). Only split when each target would stand alone.
> - **#7 Value type / disclosure** *(needs schema — eng note):* drop `UNDISCLOSED` from `value.type`; move disclosure to **two axes** — `deal_value_disclosure` + `target_financials_disclosure`.
> - **#11 Exchange ratio** *(needs schema — eng note):* capture the stock-deal `exchange_ratio` into a **structured field** (today it lands in `consideration_components.description` free-text; Olin/Huntsman `0.5476`).
> - **ARR field** *(needs schema — eng note):* dedicated ARR capture, distinct from GAAP revenue.
>
> **Derivation JOB (#8) — BUILT** in `stages/aggregate.py`: equity / implied-equity / EV / multiples and `is_take_private`. `is_divestiture` (§T4) and `is_add_on` (§T7) are **no longer authored** — the first is removed from V3, the second is replaced by the extracted `deal.sponsor_transaction_role`; both columns are retained but unwritten. The model **captures primitives only** — it must not compute or infer any of these (see rule 1).

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

**Input note:** the field LABELS above are legacy names, retained for stability. The
VALUES are current: the stage supplies `v2_event_type` under the `deal_type` label and
`event_history_type` under the `event_type` label, falling back to the legacy column only
when the current one is absent. Classify the content, not the label.

`target_type` is supplied in its normalized current representation — the stage passes
`target_type_v2` when present — so it arrives lowercase: `standalone_company`,
`subsidiary`, `business_unit`, `assets`.

Lowercase is specific to `target_type` and `acquirer.type`. Other vocabularies here are
uppercase by design — `target_status`, `value.type`, `asset_type`, `offer_mechanism`,
`deal.sponsor_transaction_role` — so do not lowercase a value because this note does.

---

## 4. System Prompt

```
You are a high-precision data extraction model for an M&A data collection
pipeline. Given the title and body of a press release or SEC filing, extract
structured deal data into the schema below.

CORE EXTRACTION RULES

1. Extract only what is explicitly stated. Do not infer, estimate, or compute
   values. If a value is not stated, return null. This is absolute: never
   multiply a per-share price by a share count to produce an aggregate equity
   value. Capture the primitives instead — `per_share_price`, plus any
   aggregate value the source states in its own words — and leave equity /
   implied-equity / EV to the deterministic derivation job (finding #8), which
   uses authoritative (SEC) share counts. Populate `value.amount` only from a
   figure the source itself states.

2. One transaction per element in the transactions array. Split into multiple
   elements only when one source directly reports multiple distinct transactions
   that are part of the same announcement/event context, such as one buyer
   acquiring two separate targets or asset groups. Do not create multiple rows
   merely because a summary, roundup, market brief, or list article mentions
   several unrelated deals. Each element must be independently complete — do not
   reference "the above" or carry fields between elements.

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
- asset_type: What KIND of asset is being transacted. Populate this ONLY when the
  TARGET TYPE supplied in the user prompt is `assets`. For every other target type it
  MUST be null — it is a sub-classification of an asset purchase, not a description of
  the target's industry or sector.

  Values:
    REAL_ESTATE           — property acquired principally as real estate
    INFRASTRUCTURE        — infrastructure assets (pipelines, towers, terminals, grids)
    ENERGY                — energy assets (generation, storage, energy production)
    NATURAL_RESOURCES     — mineral, timber, water or similar resource assets
    INTELLECTUAL_PROPERTY — patents, licences, technology rights
    DATA                  — data assets or datasets
    FACILITY              — an operating plant, mill, yard or similar facility
    EQUIPMENT             — machinery, fleet, or equipment portfolios
    CONTRACTS_OR_RIGHTS   — contracts, customer agreements, operating rights
    BRAND_OR_PRODUCT      — a brand, product line, or product portfolio
    OTHER                 — a discrete asset set none of the above describes
    null                  — target type is not `assets`, or the kind is not established

  FACILITY is deliberately distinct from REAL_ESTATE: an operating plant or mill is a
  different transaction object from property acquired principally as real estate.

  Asset type is NOT sector. It describes the thing transacted; the target's industry
  remains a separate classification. A pipeline bought by an energy company is
  INFRASTRUCTURE because a pipeline is the asset, not because the buyer is in energy.

  Single-valued. A portfolio of several assets of the same kind is one asset_type. If a
  transaction genuinely mixes asset classes such that one value loses material
  information, choose the predominant one and say so in notes.

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
- sponsor_name: The PE / private-capital sponsor associated with the acquirer,
  when the source establishes it. Not gated on any acquirer.type value. Null when
  the source does not establish a sponsor — do NOT infer an identity merely
  because the acquirer appears to be sponsor-backed, and do not guess a fund name.
  If multiple co-sponsors, comma-delimit.

parent_seller:
- name: Parent company divesting the target (when target_type is subsidiary,
  business_unit, or assets). Null for standalone company acquisitions.
- ticker: Exchange:ticker if parent is public. Null if private or unstated.
- description: 1-2 sentence description of the parent seller. Null if sparse.

deal:
- pct_acquired: Percentage of target being acquired. Null if 100% or unstated.
  Extract for minority investments and partial acquisitions only. Do not
  extract 100 — leave null for full acquisitions.
  Current transaction only: distinguish prior ownership, the stake acquired in
  this transaction, and post-transaction ownership. When a source says the
  buyer acquires the "remaining X%," extract pct_acquired = X. Do not substitute
  resulting ownership for the percentage acquired in the current transaction.
  Example: if the buyer previously acquired/owned an 80% controlling stake and
  this announcement says it acquired the remaining 20%, pct_acquired = 20, even
  if the target becomes a 100% wholly owned subsidiary after the transaction.
- stake_transition_type: Nullable explicit ownership-transition classification.
  Populate ONLY when the source explicitly states enough ownership evidence to
  distinguish prior ownership, current stake acquired, and/or resulting
  ownership/control. Otherwise null. Do not infer from pct_acquired alone.
  Do not return UNKNOWN for this field; null is the no-observation state.
- offer_mechanism: Whether the acquisition is being effected through an offer
  made directly to target securityholders. Enum or null: TENDER_OFFER | null.
    TENDER_OFFER — established by tender-offer, exchange-offer, or equivalent
      direct offer-to-securityholders language: "commence a tender offer",
      "exchange offer", "offer to purchase all outstanding shares".
    null — that mechanism is not established. This is the common case.
    Do NOT infer TENDER_OFFER because the target is public. Most public-company
      acquisitions are not effected by an offer to securityholders.
    Do NOT infer it from a merger agreement alone.
    A two-step deal — a tender offer followed by a back-end merger — sets BOTH
      offer_mechanism = TENDER_OFFER and combination_structure = MERGER. They
      answer different questions and do not compete.
  Values:
    NEW_MINORITY_STAKE — buyer/investor owned 0% or no prior stake is stated,
      acquires less than 50%, and remains below control.
    NEW_MAJORITY_STAKE — buyer/investor owned 0% or no prior stake is stated,
      acquires 50% or more but less than 100%, resulting in control. Fort
      Tech/Logia pattern: no prior ownership stated, current pct_acquired 50.1%,
      resulting stake/control is majority. TPG/Lotte Rental pattern: no prior
      ownership stated, current pct_acquired 61.17%.
    FULL_ACQUISITION — buyer/investor owned 0% or no prior stake is stated, and
      acquires 100% or the full company.
    MINORITY_ACQUIRING_MAJORITY — buyer was below 50% before and crosses to 50%
      or more, but does not acquire all remaining shares.
    MAJORITY_ACQUIRE_REMAINING — buyer was already at 50% or more and acquires
      all remaining shares, resulting in 100% ownership. Lumina/TNQTech pattern:
      prior ownership 80%, current pct_acquired 20%, resulting ownership 100%.
    MINORITY_ACQUIRING_REMAINING — buyer was below 50% and acquires all
      remaining shares, resulting in 100% ownership.
    MAJORITY_INCREASING_STAKE — buyer was already at 50% or more and increases
      ownership, but does not acquire all remaining shares.
    MINORITY_INCREASING_STAKE — buyer was below 50% and increases ownership but
      remains below 50%.

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
    (a) the source explicitly states the deal has closed/completed, OR
    (b) the source announces the deal with NO forward/pending-close language —
        no "expected to close," "will acquire," "subject to," "pending,"
        "upon completion," or other future-tense closing. Then the deal is
        closed on announcement: set closed_date = announced_date.
  Funding rounds and minority investments are closed on announcement unless the
  source says otherwise.
  GUARD — do NOT flip a deal to closed when it carries ANY forward/conditional
  closing language ("subject to regulatory approval," "expected to close in
  Q_ 20__," "pending shareholder vote," "customary closing conditions"), even
  when most of the release reads past-tense. When in doubt, leave closed_date
  null.
  Examples:
    "X today completed its acquisition of Y" / "X has acquired Y" (no future
      terms) → closed_date = announced_date.
    "X today announced it will acquire Y; the transaction is expected to close
      in Q4 2026, subject to regulatory approval" → closed_date = null.
  Null if the deal is announced but not yet closed.
- closed_date_precision: same precision values as announced_date_precision.
- signing_date: Date a definitive agreement was signed, if stated and distinct
  from announced_date. Usually null.
- signing_date_precision: same precision values.
- rumor_date: Date of first media report if the deal was rumored before
  official announcement. Signals: "previously reported," "as reported
  earlier," "according to sources." Extract the rumor date if stated. Null
  if not a rumored deal or date not stated.

CAPITAL RAISED — precondition (apply before the VALUE fields below)

First determine whether the stated amount is capital being raised by, or invested
INTO, the company — a funding round, growth investment, PIPE, or subscription for
newly issued shares. Signals: "raised," "$X funding round," "investment of $X in
<company>," "led a round," "to fund expansion / growth / R&D."

If PRIMARY CAPITAL: the amount is not a value of any kind. Record it in round_size
(below), set value.amount = null and value.type = null, and note the reason in notes
as PRIMARY_CAPITAL. An amount invested as new capital is never the company's equity
value, enterprise value, or transaction value — never gross it up.

Otherwise continue to the VALUE fields and record normally. Buying shares from an
existing holder — including a minority stake ("acquired the X% held by," "purchased
the stake held by") — is an ordinary acquisition; its consideration is a real
EQUITY_VALUE and must be recorded as one, not diverted to round_size.

If the source does not permit the distinction, set value.type_confidence = LOW and
note the ambiguity.

Balance-sheet items: extract total_debt and cash_st when the source states them
explicitly, as point-in-time figures inside target_financials:

- total_debt: TOTAL debt, NOT net of cash. If the source states only a net debt
  figure, leave total_debt null and mention the net figure in notes — a net figure
  entered here would be silently wrong downstream.
- cash_st: cash and cash equivalents plus short-term / marketable investments, as
  one combined figure. Do not split it into components.
- total_debt_currency / cash_st_currency: ISO 4217 code for each figure, taken from
  how that figure is stated.
- balance_sheet_as_of_date: the exact date the balance sheet is stated as of.
  These are POINT_IN_TIME figures. Never label them LTM, TTM, or NTM — a balance
  sheet covers no period, it is a position on one date. Do not record annual or
  quarterly either: that describes the filing the figure came from, not the economic
  period of the amount. Give the exact date, not the filing's period label.

Do not compute net_debt. Do not compute enterprise value. The deterministic
aggregation layer calculates net_debt from total_debt - cash_st only when both
figures share one currency and one balance_sheet_as_of_date, and derives
implied_enterprise_value from implied_equity_value + net_debt. Never assume missing
debt or cash/ST is zero, and never derive whole-company EV from stake-level
equity_value plus debt.

If the source does not state a figure, or states it without a currency or an as-of
date, leave the corresponding field null. A null is correct; a guess is not.

Currency of monetary figures: when the source explicitly states the same figure in
both a local currency and USD — for example "3.14 trillion won ($2.2 billion)" —
prefer the stated USD figure and set the currency to USD. Never convert a currency
yourself to produce a USD number; only use a USD figure the source itself states.
This applies to deal values and to balance-sheet figures alike.

round_size: Amount of primary capital raised by / invested into the company, as a
number (no currency symbol). Populate ONLY for the primary-capital case above; null
for ordinary acquisitions and secondary purchases.

VALUE

value:
- amount: Dollar (or local currency) value as stated. Return as a number
  (e.g., 500000000 for $500 million). Do not include currency symbol. If the
  source states BOTH a per-share price and an aggregate/total deal value,
  capture the aggregate here (and the per-share in per_share_price below) — do
  not drop the total just because a per-share figure is present.
- currency: ISO 4217 code (e.g., USD, GBP, EUR). Infer from context when
  obvious ($ = USD unless non-US context). Null if unstated. Also null whenever
  amount is null — never return a currency for a value that was not stated (an
  undisclosed deal has null amount AND null currency).
- type: What the stated value represents — use V2 MetricType vocabulary:
    EQUITY_VALUE — the equity purchase price for the stake actually acquired,
      or a per-share × shares aggregate the source itself states (do not
      compute the product yourself — see rule 1). This is consideration that
      changed hands, NOT a valuation of the whole company. A market
      capitalization is not an EQUITY_VALUE — see MARKET_CAPITALIZATION below.
    TRANSACTION_VALUE — total consideration including assumed debt; often
      labelled "transaction value" or "total consideration"
    ENTERPRISE_VALUE — source-stated whole-company EV; often labelled
      "enterprise value" or "including net debt." Do not compute EV from
      equity value, debt, or cash in this extraction prompt.
    MARKET_CAPITALIZATION — the target's or acquirer's public market value of
      equity, as stated by the source. This is a whole-company figure and a
      property of the company, not of the transaction: nothing was bought at
      this price. Capture it when the source states it, so the fact is kept,
      but never use it as the deal's equity purchase price or consideration.
      A minority stake bought for $600 million in a company with a $2.2
      billion market cap is a $600 million transaction — the $2.2 billion is
      context, and recording it as the equity value overstates the deal by
      nearly 4x.
    UNDISCLOSED — source explicitly states terms are not disclosed
  Null if no value is stated and source does not say undisclosed.
- type_confidence: HIGH / MEDIUM / LOW — how confident you are in the type
  classification. LOW when the source labels the value ambiguously.
- qualifier: Any qualifier on the value — e.g., "approximately," "up to,"
  "subject to adjustments." Null if stated as an exact figure.
- per_share_price: Per-share offer price if stated (for public targets). Number
  in same currency as value.amount. Null if not stated.

value_observations:
Use this array to preserve every independently typed deal-value fact stated in
the source. This is separate from target financial metrics such as revenue or
EBITDA.

- The `value_observations` key is required on every transaction element.
  Return an empty array (`[]`) when the source has no explicitly supported,
  qualified deal-value fact. Do not infer a value merely to populate the array.
- Return one item per distinct deal-value fact, even when two facts have the
  same numeric amount or come from the same sentence/source.
- Do not collapse facts merely because amount, currency, or source are
  identical. A "$210 million enterprise value" and "$210 million 2025 net
  sales" are different facts; the first belongs here and the second belongs in
  target_financials.revenue_amount.
- ENTERPRISE_VALUE is an extraction/observation type for a source-stated
  enterprise value. The canonical output downstream remains
  implied_enterprise_value; do not create or imply a separate canonical
  enterprise_value field.
- Keep the legacy value object populated with the primary/most transaction-
  specific value for compatibility, usually the equity purchase price or total
  consideration. Also include that primary fact as the first item in
  value_observations. Downstream treats the first typed value observation as the
  compatibility/primary value rather than a second independent source of truth.
- Include at minimum amount, currency, type, basis or qualifier if stated, and
  a short evidence phrase.
- Valid type values are the same as value.type: EQUITY_VALUE,
  TRANSACTION_VALUE, ENTERPRISE_VALUE, MARKET_CAPITALIZATION, UNDISCLOSED.
- A MARKET_CAPITALIZATION observation is never the primary value. The primary
  fact is the most transaction-specific one — the equity purchase price or the
  total consideration. A market cap describes the company, not the deal, so it
  belongs later in the array and never in the legacy value object unless the
  source states no transaction value at all.
- Use basis="STATED" for source-stated values when no more specific basis is
  needed. Use qualifier for words like "approximately", "up to", or "subject to
  adjustment".

- sponsor_transaction_role: How this transaction relates to a financial sponsor's
  platform. Enum or null: PLATFORM | ADD_ON | null.
    PLATFORM — the source affirmatively establishes that THIS transaction creates
      or acquires the company as a NEW sponsor platform. Explicit wording ("new
      platform", "platform investment") or transaction context that establishes
      it. A PE firm being the buyer does NOT establish this on its own. This is
      deliberately a higher evidence bar than ADD_ON.
    ADD_ON — a company that is ALREADY sponsor/PE-backed — a portfolio company or
      an existing platform — is making this acquisition. Literal "add-on",
      "bolt-on" or "tuck-in" wording is NOT required, and the sponsor does not
      have to be named. Establishable from the transaction language, from the
      source establishing the acquirer's portfolio/platform status, or from the
      company description supplying that context alongside this acquisition.
      Ordinary wording qualifies: "X, a portfolio company of Y Capital, acquired
      Z", or "X, a private-equity-backed company, acquired Z" where current
      sponsor backing is genuinely established.
    null — neither is established. Sponsor or PE involvement ALONE is not enough,
      and null is expected to be common.
    Generic VC backing is not ADD_ON: the relevant fact is PE/sponsor ownership of
      an existing operating company, not that the acquirer once raised venture
      capital. The generic word "platform" describing a company, product or
      technology is not PLATFORM — it must refer to the sponsor relationship.
    There is no precedence rule to apply mechanically. PLATFORM requires
      affirmative new-platform evidence; otherwise an acquisition by an already
      sponsor-backed portfolio company is ADD_ON.
    This is a transaction classification, independent of acquirer.type. Do not
      derive it from any acquirer-type value.

features:
Use this object for explicit, qualified transaction feature evidence. Return
null when the source does not provide explicit evidence for a feature. Do not
infer these flags merely from buyer type, merger structure, company size,
ownership percentages, board composition, or management roles.

- is_secondary_buyout: true when the source explicitly says a sponsor-backed
  company/business is acquired from another financial sponsor, or otherwise
  explicitly provides that sponsor-to-sponsor ownership context. Do not infer
  from PE buyer alone.
- is_merger_of_equals: true only with explicit/qualified source evidence such as
  "merger of equals", "combination of equals", or clearly equivalent language.
  Do not infer merely from merger structure, similar company size, ownership
  percentages, board composition, or management roles.

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

CAPTURE DISCIPLINE: Extract a stated revenue/EBITDA figure for the TARGET even
when it appears in running prose, a quote, or otherwise non-financial framing
(e.g. "the Norwegian unit, which generated NOK 2.1bn in revenue," "Roku's
platform revenue of $..."). A figure attached to the target counts even when
the release is not primarily about financials. Only leave null when no figure
is stated.

PERIOD ANCHOR: When the source gives an end date or fiscal year for a figure,
populate revenue_period_end / ebitda_period_end with it (YYYY or YYYY-MM-DD).
If a figure has a period basis but no stated year, keep the period_type and
leave period_end null — do not invent a year.

ARR IS NOT REVENUE: Do not record ARR (annual recurring revenue) in
revenue_amount. If the source states only ARR, leave revenue_amount null so ARR
is not mistagged as GAAP revenue (which would produce a bogus EV/revenue
multiple). A dedicated ARR field is pending schema — see the QA notes header.

MULTI-TRANSACTION SOURCES

When a single source directly announces multiple transactions in the same
announcement/event context, return one transactions array element per
transaction. Valid signals include:
- One buyer acquiring two separate targets or asset groups in the same release
- Platform company announcing multiple add-on acquisitions in one release
- PE firm announcing several portfolio exits simultaneously as one firm event

Do not split merely because a summary, roundup, market brief, tombstone list, or
article digest mentions several unrelated deals. For those sources, extract only
the supported transaction represented by the current classified story when clear;
otherwise return a conservative one-element result with null/UNKNOWN fields and
notes explaining the ambiguity.

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
        "description": "a privately-held manufacturer of specialty valves for the oil and gas industry, headquartered in Dallas, Texas",
        "asset_type": null
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
        "pct_acquired": null,
        "offer_mechanism": null,
        "sponsor_transaction_role": null
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
      "value_observations": [
        {
          "amount": 500000000,
          "currency": "USD",
          "type": "TRANSACTION_VALUE",
          "basis": "STATED",
          "qualifier": null,
          "evidence": "for $500 million in cash"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": "string | null",
        "asset_type": "enum | null — only when TARGET TYPE is `assets`; null otherwise"
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
        "pct_acquired": "number | null",
        "stake_transition_type": "NEW_MINORITY_STAKE | NEW_MAJORITY_STAKE | FULL_ACQUISITION | MINORITY_ACQUIRING_MAJORITY | MAJORITY_ACQUIRE_REMAINING | MINORITY_ACQUIRING_REMAINING | MAJORITY_INCREASING_STAKE | MINORITY_INCREASING_STAKE | null",
        "offer_mechanism": "TENDER_OFFER | null",
        "sponsor_transaction_role": "PLATFORM | ADD_ON | null"
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
      "value_observations": [
        {
          "amount": "number | null",
          "currency": "string | null",
          "type": "EQUITY_VALUE | TRANSACTION_VALUE | ENTERPRISE_VALUE | UNDISCLOSED",
          "basis": "string | null",
          "qualifier": "string | null",
          "evidence": "string | null"
        }
      ],
      "features": {
        "is_secondary_buyout": "boolean | null",
        "is_merger_of_equals": "boolean | null"
      },
      "round_size": "number | null",
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": "a privately held manufacturer of specialty valves for the oil and gas industry headquartered in Dallas, Texas",
        "asset_type": null
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
        "pct_acquired": null,
        "offer_mechanism": null,
        "sponsor_transaction_role": null
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
      "value_observations": [
        {
          "amount": 500000000,
          "currency": "USD",
          "type": "TRANSACTION_VALUE",
          "basis": "STATED",
          "qualifier": null,
          "evidence": "for $500 million in cash"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": null,
        "asset_type": null
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
        "pct_acquired": null,
        "offer_mechanism": null,
        "sponsor_transaction_role": null
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
      "value_observations": [
        {
          "amount": 2100000000,
          "currency": "USD",
          "type": "ENTERPRISE_VALUE",
          "basis": "STATED",
          "qualifier": "approximately; including assumed net debt",
          "evidence": "values PublicCo at approximately $2.1 billion enterprise value"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": "a privately held HVAC and mechanical services contractor headquartered in New Jersey",
        "asset_type": null
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
        "pct_acquired": null,
        "offer_mechanism": null,
        "sponsor_transaction_role": null
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
      "value_observations": [],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": "the industrial coatings division of MegaCorp",
        "asset_type": null
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
        "pct_acquired": null,
        "offer_mechanism": null,
        "sponsor_transaction_role": null
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
      "value_observations": [
        {
          "amount": 1200000000,
          "currency": "USD",
          "type": "TRANSACTION_VALUE",
          "basis": "STATED",
          "qualifier": null,
          "evidence": "to sell its Industrial Coatings Division ... for $1.2 billion in cash"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
        "description": null,
        "asset_type": null
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
      "deal": {"pct_acquired": null, "offer_mechanism": null, "sponsor_transaction_role": null},
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
      "value_observations": [],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
    },
    {
      "target": {
        "name": "Omega Systems",
        "domain": null,
        "ticker": null,
        "description": null,
        "asset_type": null
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
      "deal": {"pct_acquired": null, "offer_mechanism": null, "sponsor_transaction_role": null},
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
      "value_observations": [
        {
          "amount": 250000000,
          "currency": "USD",
          "type": "TRANSACTION_VALUE",
          "basis": "STATED",
          "qualifier": null,
          "evidence": "acquisition of Omega Systems for $250 million in cash"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
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
      "target": {"name": null, "domain": null, "ticker": null, "description": null, "asset_type": null},
      "acquirer": {"name": null, "domain": null, "ticker": null, "type": "unknown", "description": null, "sponsor_name": null},
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": null, "offer_mechanism": null, "sponsor_transaction_role": null},
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
      "value_observations": [],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
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
      "prompt_version": "high_confidence_extraction:0.16"
    }
  ]
}
```

**Example 7 — Partial acquisition with separate equity value, enterprise value, and revenue:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
DEAL TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT TYPE: ANNOUNCED
TARGET STATUS: PRIVATE
PUBLISHED DATE: 2026-08-13
TITLE: Samsonite Group S.A. to Acquire BÉIS
BODY: Samsonite Group S.A. today announced that it has entered into a definitive
agreement to acquire BÉIS, LLC. The transaction represents a total enterprise
value of approximately $210 million on a cash-free, debt-free basis. Samsonite
will acquire 85% of the equity of BÉIS for $178.5 million. BÉIS generated net
sales of approximately $210 million in 2025.
```

Output:
```json
{
  "transactions": [
    {
      "target": {"name": "BÉIS, LLC", "domain": null, "ticker": null, "description": null, "asset_type": null},
      "acquirer": {"name": "Samsonite Group S.A.", "domain": null, "ticker": null, "type": "strategic_corporate", "description": null, "sponsor_name": null},
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": 85.0, "stake_transition_type": "NEW_MAJORITY_STAKE", "offer_mechanism": null, "sponsor_transaction_role": null},
      "dates": {
        "announced_date": "2026-08-13",
        "announced_date_precision": "exact",
        "closed_date": null,
        "closed_date_precision": null,
        "signing_date": null,
        "signing_date_precision": null,
        "rumor_date": null
      },
      "value": {
        "amount": 178500000,
        "currency": "USD",
        "type": "EQUITY_VALUE",
        "type_confidence": "HIGH",
        "qualifier": null,
        "per_share_price": null
      },
      "value_observations": [
        {
          "amount": 178500000,
          "currency": "USD",
          "type": "EQUITY_VALUE",
          "basis": "STATED",
          "qualifier": null,
          "evidence": "acquire 85% of the equity of BÉIS for $178.5 million"
        },
        {
          "amount": 210000000,
          "currency": "USD",
          "type": "ENTERPRISE_VALUE",
          "basis": "STATED",
          "qualifier": "approximately; cash-free, debt-free",
          "evidence": "total enterprise value of approximately $210 million on a cash-free, debt-free basis"
        }
      ],
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
      "financials_disclosure_status": "DISCLOSED",
      "consideration_type": "cash",
      "target_financials": {
        "revenue_amount": 210000000,
        "revenue_period_type": "ANNUAL",
        "revenue_period_end": "2025",
        "ebitda_amount": null,
        "ebitda_period_type": null,
        "ebitda_period_end": null,
        "currency": "USD"
      },
      "model_confidence": "HIGH",
      "notes": "The $210M revenue fact remains in target_financials; only the separate $210M enterprise value is included in value_observations.",
      "prompt_version": "high_confidence_extraction:0.16"
    }
  ]
}
```

---

**Example 8 — Asset purchase: asset_type populated, and it is not the sector:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: assets
EVENT HISTORY TYPE: ANNOUNCED

TITLE: Cascade Midstream Acquires Gulf Coast Pipeline System from Meridian Energy
BODY: Cascade Midstream Partners today announced it has agreed to acquire a 240-mile
refined products pipeline system and two associated storage terminals on the Gulf Coast
from Meridian Energy Corp for $310 million in cash. The transaction does not include
Meridian's marketing business or personnel.
```

Output:
```json
{
  "transactions": [
    {
      "target": {
        "name": "Gulf Coast pipeline system and associated storage terminals",
        "domain": null,
        "ticker": null,
        "description": "a 240-mile refined products pipeline system with two associated storage terminals on the Gulf Coast",
        "asset_type": "INFRASTRUCTURE"
      },
      "acquirer": {"name": "Cascade Midstream Partners", "domain": null, "ticker": null, "type": "strategic_corporate", "description": null, "sponsor_name": null},
      "parent_seller": {"name": "Meridian Energy Corp", "ticker": null, "description": null},
      "deal": {"pct_acquired": null, "stake_transition_type": null, "offer_mechanism": null, "sponsor_transaction_role": null},
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
      "notes": "TARGET TYPE is assets, so asset_type is populated. A pipeline system is INFRASTRUCTURE because that is the thing transacted, not ENERGY, which would describe the parties' sector. No employees or going-concern unit transfer, consistent with an asset purchase rather than a business unit."
    }
  ]
}
```

**Why asset_type is null in the other examples.** In Examples 1-7 the TARGET TYPE supplied
is `standalone_company`, so asset_type is null in each. It is subordinate to the target
type, not an independent judgement about what the target does.

**Example 9 — Two-step tender offer: offer_mechanism and combination_structure coexist:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED

TITLE: Halden Therapeutics to Acquire Verity Biosciences for $18.50 Per Share
BODY: Halden Therapeutics announced a definitive agreement to acquire Verity Biosciences
(NASDAQ: VRTY) for $18.50 per share in cash. Under the agreement, a wholly owned
subsidiary of Halden will commence a tender offer to purchase all outstanding shares of
Verity common stock. Following completion of the tender offer, the subsidiary will merge
into Verity, which will become a wholly owned subsidiary of Halden.
```

Output:
```json
{
  "transactions": [
    {
      "target": {"name": "Verity Biosciences", "domain": null, "ticker": "NASDAQ: VRTY", "description": null, "asset_type": null},
      "acquirer": {"name": "Halden Therapeutics", "domain": null, "ticker": null, "type": "strategic_corporate", "description": null, "sponsor_name": null},
      "parent_seller": {"name": null, "ticker": null, "description": null},
      "deal": {"pct_acquired": null, "stake_transition_type": null, "offer_mechanism": "TENDER_OFFER"},
      "features": {"is_secondary_buyout": null, "is_merger_of_equals": null},
      "notes": "\"commence a tender offer to purchase all outstanding shares\" is a direct offer to securityholders. The back-end merger is a separate fact recorded as combination_structure by the classifier; it does not compete with offer_mechanism and does not replace it."
    }
  ]
}
```

**Why offer_mechanism is null in Examples 1-8.** None of them describes an offer made to
securityholders. Example 2 is a public take-private effected by merger agreement, which is
the case most likely to be mistyped: a public target is not evidence of a tender offer.

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
| Model infers platform investment, secondary buyout, or merger of equals without explicit evidence | Critical — features require qualified source evidence; aggregation handles only the narrow side-qualified sponsor derivation for secondary buyout |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1–0.9 | 2026-04-22 – 2026-05-XX | Initial drafts, multi-transaction support, observation dual-write, various field additions |
| 0.10 | 2026-07-22 | Announcement vs Close semantics — CLOSE reserved for separate later releases |
| 0.11 | 2026-07-22 | Take-private note updated; sponsor_name handling clarified |
| 0.12 | 2026-07-28 | V2 alignment. acquirer.type values lowercased and expanded (pe_portfolio, growth_equity, hedge_fund, consortium, management, employee_group, other_financial_sponsor added). revenue_period_type and ebitda_period_type values aligned to V2 period_type enum (LTM, NTM, ANNUAL, QUARTERLY, INTERIM_YTD); null explicitly required when period not stated. date_precision fields added for all dates. rumor_date added. financials_disclosure_status added as required field. consideration_type added as interim field (pending consideration_component table). ANNOUNCED/CLOSED replace ANNOUNCEMENT/CLOSE in event_type references. Example 6 added for NTM financials. |
| 0.13 | 2026-08-04 | Capital-raised precondition added ahead of the VALUE fields (`006f817`). Primary capital raised by or invested **into** the company is not a value of any kind: it is recorded in `round_size` with `value.amount` and `value.type` null and the reason noted as `PRIMARY_CAPITAL`, and is never grossed up into an equity, enterprise or transaction value. Buying shares from an existing holder — including a minority stake — stays an ordinary acquisition whose consideration is a real `EQUITY_VALUE` and must not be diverted to `round_size`. Where the source does not permit the distinction, `value.type_confidence = LOW` and the ambiguity is noted. |
| 0.14 | 2026-08-12 | Added nullable `deal.stake_transition_type` for explicit ownership-transition cases. |
| 0.15 | 2026-08-14 | Added required `value_observations` array for independently typed deal-value facts. |
| 0.16 | 2026-08-14 | Added explicit-evidence `features` object for platform investment, secondary buyout, and merger-of-equals flags. |
| 0.17 | 2026-08-16 | Version bump covering the balance-sheet capture landed in the two preceding commits (`611d173`, `c8154bf`): `total_debt` and `cash_st` with per-figure currency, `balance_sheet_as_of_date`, and POINT_IN_TIME framing — a balance sheet covers no period, so it is never labelled LTM/TTM/NTM, annual or quarterly. The bump itself (`baae0f3`) changed no prompt text; it accompanied `net_debt_currency` persistence in the stage. Recorded this way because the prompt body and the version number moved in different commits. |
| 0.18 | 2026-08-17 | **Equity scope narrowed; `MARKET_CAPITALIZATION` split out** (`454d6d3`). `EQUITY_VALUE` is the equity purchase price for the stake actually acquired — consideration that changed hands, not a valuation of the whole company. A market capitalization is a property of the company rather than of the transaction and becomes its own `value.type`, captured when stated but never used as the deal's consideration. A minority stake bought for $600 million in a company with a $2.2 billion market cap is a $600 million transaction; recording the market cap as the equity value overstates it nearly fourfold. |
| 0.19 | 2026-08-20 | **`asset_type` added (V3 §T13), subordinate to `target_type = assets`.** Eleven values plus null, answering *what kind of asset is being transacted* — **not** the target's sector or industry, which remains a separate classification. `FACILITY` is deliberately distinct from `REAL_ESTATE`: an operating plant is a different transaction object from property held principally as real estate. Single-valued; a portfolio of like assets is one value. **Null for every target type other than `assets`**, enforced in the stage validator — it is a sub-classification of an asset purchase, not an independent judgement. Example 8 added; all ten existing example target blocks carry `asset_type: null`. |
| 0.20 | 2026-08-20 | **`offer_mechanism` added (V3 §T12).** `TENDER_OFFER` | null, in the `deal` block. Describes whether the acquisition is effected through an offer made directly to target securityholders; established by tender-offer, exchange-offer or equivalent language. Vocabulary deliberately not expanded — `MANDATORY_OFFER`, `SCHEME_OF_ARRANGEMENT`, `ONE_STEP_MERGER` and `TWO_STEP_MERGER` are excluded by §T12. Two anti-inference rules: a public target is not evidence of a tender offer, and a merger agreement alone is not either. Example 9 added for the two-step case, where `offer_mechanism = TENDER_OFFER` and `combination_structure = MERGER` coexist. Previously this fact existed only as `merger_structure = TENDER_OFFER` on the SEC/agreement path, unreachable for any transaction without a filing; that path is retained as corroborating evidence, not replaced. |
| 0.21 | 2026-08-21 | **`sponsor_transaction_role` added (V3 §T7); `is_platform_investment` retired.** `PLATFORM` / `ADD_ON` / null in the `deal` block, replacing the v0.4 `is_platform_investment` + `is_add_on` pair. `PLATFORM` needs affirmative evidence that **this** transaction creates a new sponsor platform — a PE buyer alone does not establish it. `ADD_ON` is an acquisition **by** an already sponsor/PE-backed portfolio or platform company; literal add-on/bolt-on/tuck-in wording is **not** required, the sponsor need not be named, and a company description may supply the context. Null is expected to be common: sponsor involvement alone is insufficient and generic VC backing is not `ADD_ON`. **No mechanical precedence rule** — `PLATFORM` requires new-platform evidence, otherwise an acquisition by an already-backed portfolio company is `ADD_ON`. Independent of `acquirer.type`, and never derived from it. `is_secondary_buyout` stays orthogonal. `is_platform_investment` leaves the `features` contract entirely — it accepted only explicit platform wording, which is the narrower half of what §T7 asks. **`sponsor_name` is no longer gated on `pe_portfolio`** (a value §T8 removes): populate it whenever the source establishes the sponsor associated with the acquirer, never inferred from apparent sponsor backing. |
| 0.22 | 2026-08-21 | **Input note corrected to describe the values the stage actually sends; stale vocabulary and derivation claims removed.** The note said `deal_type` and `event_type` "reflect legacy classifier output (v0.5 and earlier)" and promised they would become `v2_event_type` / `event_history_type` "when classifier is updated to v0.6+". The classifier is at 0.12 and that rename never happened: the template keeps the legacy **labels** while `stages/high_confidence_extract.py` supplies the **current values** under them. `target_type` in particular arrives normalized — the stage passes `target_type_v2` when present — which is why it is lowercase. That is stated as specific to `target_type` and `acquirer.type`, with a reminder that `target_status`, `value.type`, `asset_type`, `offer_mechanism` and `sponsor_transaction_role` are uppercase by design, so the note cannot be read as a general lowercasing rule. `spinco` is dropped from the vocabulary (§T3 removed it, so it cannot arrive). The derivation note no longer presents `is_divestiture` and `is_add_on` as built: §T4 removed one and §T7 replaced the other with `sponsor_transaction_role`, and both columns are retained but unwritten. |
