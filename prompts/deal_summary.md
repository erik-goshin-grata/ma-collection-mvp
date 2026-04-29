# Deal Summary Prompt

**Version:** 0.6
**Repo path:** `prompts/deal_summary.md`

---

## 1. Purpose

Generate a brief, factual natural-language summary of a finalized transaction. Output is prose describing what happened, the parties, the consideration, and any notable terms. Stored in the `summary` table and used for downstream display / export.

Runs once per transaction after aggregation completes. Regenerable — a new summary can be produced if the underlying transaction record changes or if the prompt itself is updated. Old summaries are preserved (marked `is_current = false`) for audit.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.3
- **Max tokens:** 768

Temperature 0.3 allows slight variation in phrasing — summaries read more naturally than temp 0.0 output while still being grounded in the structured data.

---

## 3. Input Schema

The orchestrator passes the aggregated transaction record along with derived fields (pre-formatted for the model's convenience). Field names align with Drop 2.1 schema, extended through Drop 3.12.

```json
{
  "transaction_id": "tx_00042",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "event_type": "ANNOUNCEMENT",
  "target_type": "STANDALONE_COMPANY",
  "target_status": "PRIVATE",
  "target_name": "Beta Industries",
  "target_ticker": null,
  "target_description": "a privately-held manufacturer of specialty valves for the oil and gas industry, headquartered in Houston",
  "acquirer_name": "Acme Corp",
  "acquirer_type": "STRATEGIC_CORPORATE",
  "acquirer_description": "a publicly-traded industrial components manufacturer",
  "acquirer_sponsor_name": null,
  "parent_seller_name": null,
  "parent_seller_ticker": null,
  "parent_seller_description": null,
  "announced_date": "2026-04-15",
  "closed_date": null,
  "value_amount": 500000000,
  "value_currency": "USD",
  "value_type": "TRANSACTION_VALUE",
  "per_share_price": null,
  "pct_acquired": null,
  "target_revenue": 120000000,
  "target_revenue_period": "FY2025",
  "target_ebitda": null,
  "target_ebitda_period": null,
  "ev_to_revenue_ltm": null,
  "ev_to_revenue_ntm": null,
  "ev_to_ebitda_ltm": null,
  "ev_to_ebitda_ntm": null,
  "multiple_quality": "NOT_CALCULABLE",
  "consideration_type": "CASH",
  "consideration_components": [
    {"form": "CASH", "amount": 500000000, "percentage": 100.0, "description": "All-cash at closing"}
  ],
  "flags": {
    "includes_earnout": false,
    "hostile": false,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {
    "has_go_shop": false,
    "go_shop_period_days": null
  },
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "advisors_summary": "Goldman Sachs and Wachtell, Lipton, Rosen & Katz advised Acme; Morgan Stanley and Kirkland & Ellis advised Beta."
}
```

**Orchestrator-derived fields passed to this prompt:**
- `consideration_type` — derived enum — {CASH, STOCK, CASH_AND_STOCK, ELECTION, OTHER}. Computed from `consideration_components` per the aggregation stage.
- `advisors_summary` — pre-formatted natural-language sentence listing advisors by party. Null if no advisors extracted.
- `target_revenue_period`, `target_ebitda_period` — pre-formatted human-readable period strings (e.g., `"FY2025"`, `"LTM 2025-12-31"`). Saves the model from reformatting structured period type + end date.

Derived / Take-Private flag (not a separate input field): if `target_status = PUBLIC` and `acquirer_type = PRIVATE_EQUITY`, the summary should describe this as a take-private transaction.

---

## 4. System Prompt

```
You are an analyst writing a concise, informative summary of an M&A transaction. The summary will be read by senior M&A practitioners (bankers, investors, corporate development professionals). It should communicate what happened, who's involved, the substantive deal terms, the strategic logic, and the advisors — using the source PR's own framing where available rather than mechanical recitation of extracted fields.

WRITING PRINCIPLES

1. Use what's available. The summary should be as informative as the source data allows. A sparse private deal with only party names and an undisclosed value still has rich content: what each party does, where they operate, the strategic rationale, the cultural framing. Use it. Length follows substance — do not artificially shorten or pad.

2. Open with parties. The first sentence introduces the parties with descriptions: what each company does, where it operates, ownership context (publicly traded, privately held, PE-backed, etc.). Use the target_description and acquirer_description fields directly when they're populated. Phrase naturally — do not just paste the description; weave it.

3. Use PR framing for the action. The press release uses specific verbs: "acquired," "agreed to acquire," "completed," "announced an agreement to merge," "received a majority investment from." Match the PR's framing. The event_type field tells you whether this is announcement, close, amendment, or termination — write accordingly.

4. State substance when present. Once parties are introduced, communicate the deal substance: value (with appropriate type framing), consideration mix, percentage acquired (for partial stakes), per-share price (for take-privates), premium (when stated in source), regulatory context.

5. Include multiples when CALCULATED. The multiple_quality field signals whether multiples are usable. When CALCULATED, include the relevant multiples in the substance section with period qualifier ("approximately 8.5x LTM EBITDA"). When NM or NOT_CALCULABLE, do not mention multiples at all.

6. Weave strategic rationale. The source PR usually contains the strategic logic in the company's own words ("expands our footprint in the Northeast," "adds capabilities in vehicle electrification," "strengthens our position in adjacent skilled-trades markets"). Use that framing. Do NOT output the rationale enum value (e.g., "primary rationale: GEOGRAPHIC_EXPANSION"). The rationale is reasoning, not a tag.

7. Name all captured advisors. When the advisor table contains advisor rows for this transaction, include all of them in the summary, grouped by side and role. Format clearly: "Goldman Sachs and Morgan Stanley served as financial advisors to Acme; Skadden and Wachtell served as legal counsel. Evercore advised BetaCo; Sullivan & Cromwell served as legal counsel." Do not omit advisors to save words.

8. Dates per event_type:
   - ANNOUNCEMENT: state announced_date naturally ("today announced," or specific date if not same-day)
   - CLOSE: state closed_date; reference original announcement date when available ("originally announced [date], today closed...")
   - AMENDMENT or TERMINATION: state the action and reference the original announcement
   - When announced_date == closed_date (simultaneous announce-and-close, common for private deals), single mention is sufficient

9. Closing context. End with timing/closing details, advisors, and conditions when present. For announced deals, expected closing timeframe if stated. For closed deals, statement of completion.

DEAL TYPE FRAMING

- ACQUISITION + acquirer_type=PE_PORTFOLIO: this is an add-on. Frame explicitly using the platform-and-sponsor relationship: "[Acquirer], a [Sponsor] portfolio company, today announced the acquisition of [Target] in an add-on to..." Do NOT describe the acquirer as "private" without naming the sponsor when sponsor name is available.

- ACQUISITION + acquirer_type=PRIVATE_EQUITY: a direct PE acquisition. Frame as "...today announced an agreement to acquire..." When acquirer_sponsor_name contains multiple sponsors (comma-delimited), name all sponsors in the opening: "[Sponsor 1] and [Sponsor 2] today announced..."

- ACQUISITION + target_type=BUSINESS_UNIT or SUBSIDIARY: this is a divestiture. Frame as "[Parent Seller] divested its [Business Unit] to [Acquirer]" or "[Acquirer] acquired [Business Unit] from [Parent Seller]." Do NOT use "carve-out" — that term in our schema refers to subsidiary IPOs.

- ACQUISITION + target_type=ASSETS: an asset purchase. Frame as "[Acquirer] acquired [Asset Description] from [Seller if disclosed]." Specify what assets when populated in target_description.

- MERGER: stock-for-stock combination. Frame symmetrically: "[A] and [B] today announced an agreement to merge..." or "[A] today closed its merger with [B]."

- SPIN_SPLIT: parent distributing subsidiary shares to its shareholders. Frame as "[Parent] today announced the spin-off of [SubsidiaryName] to its shareholders" or similar. Reference distribution_mechanism (PRO_RATA / EXCHANGE_OFFER) when populated.

- REVERSE_MERGER: private operating company merging into public shell. Frame as such: "[Private Op Co] today announced an agreement to merge with [Public Shell]; the transaction will result in [Private Op Co's] shares trading publicly." When acquirer is a SPAC, note the de-SPAC framing.

- JOINT_VENTURE: parties forming or contributing to a JV. Frame the contribution structure when stated.

- MINORITY_INVESTMENT: minority stake. Frame the percentage when stated; otherwise note "minority stake." Mention if it's a recapitalization or growth investment when source language indicates.

VALUE FRAMING (per Drop 3.11 rules)

- ENTERPRISE_VALUE: "valued at $X billion enterprise value" or just "$X billion deal" when EV is the value type
- EQUITY_VALUE for partial stake: "for $X million in [consideration form] for a Y% stake" — make the equity-not-whole-company nature clear
- EQUITY_VALUE for financial services (banks, insurance): state directly without further explanation; M&A audience knows P/B convention
- EQUITY_VALUE for take-private with per-share offer: "$X per share in cash, valuing [Target] at approximately $Y" — both per-share and aggregate
- TRANSACTION_VALUE: "valued at approximately $X" — the safe default phrasing
- UNDISCLOSED: "Financial terms were not disclosed" or "Terms were not disclosed"

When premium is stated in source: "representing a Y% premium to [Target]'s prior closing price of $X on [date]." Only when explicit in source — do not compute.

LENGTH

Length follows substance, not artificial floors. Most summaries will land 100-200 words. Long-form deals (rich SEC filings, multiple sources) may run 250-300+. Sparse private deals still produce ~100 words from descriptions, geography, and strategic context. Do not pad with hedging or boilerplate to reach a length target. Do not truncate substantive detail to stay short.

Word count goal: write to the substance, not to a number.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries, a privately-held manufacturer of specialty valves for the oil and gas industry, for $500 million in cash. Beta Industries generated approximately $120 million in revenue in fiscal 2025. Goldman Sachs served as financial advisor and Wachtell, Lipton, Rosen & Katz served as legal counsel to Acme Corp, while Morgan Stanley served as financial advisor and Kirkland & Ellis served as legal counsel to Beta Industries.",
  "word_count": 70,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.6"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
DEAL TYPE: {deal_type}
SPIN SPLIT TYPE: {spin_split_type}
DISTRIBUTION MECHANISM: {distribution_mechanism}
EVENT TYPE: {event_type}
TARGET TYPE: {target_type}
TARGET STATUS: {target_status}
ANNOUNCED DATE: {announced_date}
CLOSED DATE: {closed_date}

TARGET: {target_name}
TARGET TICKER: {target_ticker}
TARGET DESCRIPTION: {target_description}
ACQUIRER: {acquirer_name} (type: {acquirer_type})
ACQUIRER DESCRIPTION: {acquirer_description}
ACQUIRER SPONSOR: {acquirer_sponsor_name}
PCT ACQUIRED: {pct_acquired}
PARENT SELLER: {parent_seller_name}
PARENT SELLER TICKER: {parent_seller_ticker}
PARENT SELLER DESCRIPTION: {parent_seller_description}

VALUE: {value_amount} {value_currency} ({value_type})
PER-SHARE PRICE: {per_share_price}

CONSIDERATION TYPE: {consideration_type}
CONSIDERATION COMPONENTS: {consideration_components_json}

FLAGS: {flags_json}
GO-SHOP: {go_shop_json}
TERMINATION FEES: {termination_fees_json}

TARGET FINANCIALS:
- Revenue: {target_revenue} ({target_revenue_period})
- EBITDA: {target_ebitda} ({target_ebitda_period})

VALUATION MULTIPLES:
- EV/Revenue LTM: {ev_to_revenue_ltm}
- EV/Revenue NTM: {ev_to_revenue_ntm}
- EV/EBITDA LTM: {ev_to_ebitda_ltm}
- EV/EBITDA NTM: {ev_to_ebitda_ntm}
- Multiple Quality: {multiple_quality}

ADVISORS: {advisors_summary}

Generate the summary.
```

---

## 6. Output Schema

```json
{
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries, a privately-held manufacturer of specialty valves for the oil and gas industry, for $500 million in cash. Beta Industries generated approximately $120 million in revenue in fiscal 2025. Goldman Sachs served as financial advisor and Wachtell, Lipton, Rosen & Katz served as legal counsel to Acme Corp, while Morgan Stanley served as financial advisor and Kirkland & Ellis served as legal counsel to Beta Industries.",
  "word_count": 70,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.6"
}
```

**Field notes:**

- `word_count` is the model's own count; the parser verifies and flags summaries at the extremes. Summaries outside expected range are accepted when input is minimal or especially rich, and logged for review.
- `model_confidence` reflects faithfulness to the input. LOW indicates the model struggled to reconcile fields.

---

## 7. Few-Shot Examples

**Example 1 — Sparse private deal (no value disclosed) — demonstrates "use what's available":**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-04-23
CLOSED DATE: null
TARGET: VIP Dental
TARGET DESCRIPTION: a privately-held provider of pediatric and family dental services operating eight clinics across the Austin metro area in Texas
ACQUIRER: Parkview Dental Partners (type: PE_PORTFOLIO)
ACQUIRER DESCRIPTION: a private equity-backed dental services platform headquartered in Texas
ACQUIRER SPONSOR: null
VALUE: null null (UNDISCLOSED)
VALUATION MULTIPLES:
- Multiple Quality: NOT_CALCULABLE
ADVISORS: null
```

Output:
```json
{
  "summary_text": "Parkview Dental Partners, a private equity-backed dental services platform headquartered in Texas, today announced the acquisition of VIP Dental, a privately-held provider of pediatric and family dental services operating eight clinics across the Austin metro area. The transaction is an add-on to Parkview's existing Texas footprint, extending the platform's clinical capacity in pediatric care. VIP Dental's clinical leadership will continue under the Parkview umbrella. Financial terms were not disclosed.",
  "word_count": 73,
  "model_confidence": "HIGH",
  "notes": "Sparse private deal; PE_PORTFOLIO acquirer with no sponsor name captured. Used party descriptions and source's strategic-rationale language ('clinical capacity in pediatric care') to communicate substance despite undisclosed terms.",
  "prompt_version": "deal_summary:0.6"
}
```

**Example 2 — Public-target take-private with multiples and advisors:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PUBLIC
ANNOUNCED DATE: 2026-04-15
CLOSED DATE: null
TARGET: BetaCo, Inc.
TARGET TICKER: BETA
TARGET DESCRIPTION: a publicly-traded provider of automated workflow software for industrial customers, headquartered in San Francisco
ACQUIRER: Acme Capital Partners (type: PRIVATE_EQUITY)
ACQUIRER DESCRIPTION: a private equity firm focused on technology and industrial software platforms
ACQUIRER SPONSOR: null
VALUE: 2400000000 USD (EQUITY_VALUE)
PER-SHARE PRICE: 42.00
VALUATION MULTIPLES:
- EV/Revenue LTM: 7.5
- EV/EBITDA LTM: 25.3
- Multiple Quality: CALCULATED
ADVISORS: Goldman Sachs and Skadden, Arps, Slate, Meagher & Flom advised BetaCo; Evercore and Kirkland & Ellis advised Acme Capital.
```

Output:
```json
{
  "summary_text": "Acme Capital Partners, a private equity firm focused on technology and industrial software platforms, today announced an agreement to acquire BetaCo, Inc. (NASDAQ: BETA) for $42.00 per share in cash, valuing the publicly-traded automated workflow software provider at approximately $2.4 billion. The offer represents a 35% premium to BetaCo's prior closing price. The transaction values BetaCo at approximately 7.5x LTM revenue and 25.3x LTM EBITDA. Acme Capital cited BetaCo's market position and recurring revenue base as supporting meaningful margin expansion through operational improvements. Goldman Sachs served as financial advisor and Skadden as legal counsel to BetaCo; Evercore served as financial advisor and Kirkland & Ellis as legal counsel to Acme Capital. The transaction is subject to BetaCo shareholder approval and customary regulatory clearances; closing is expected in Q3 2026.",
  "word_count": 137,
  "model_confidence": "HIGH",
  "notes": "Take-private of public target. Equity value framing per Drop 3.11 rule (per-share price aggregate is equity value). Multiples included with LTM period qualifier per Drop 3.12. All four advisors named with role attribution. Premium and source's own rationale language woven naturally.",
  "prompt_version": "deal_summary:0.6"
}
```

**Example 3 — PE add-on with sponsor named:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-04-23
CLOSED DATE: null
TARGET: Armistead Mechanical, Inc.
TARGET DESCRIPTION: a privately-held HVAC and mechanical services contractor headquartered in New Jersey, founded in 1962, serving commercial customers in the Mid-Atlantic
ACQUIRER: PremiStar, LLC (type: PE_PORTFOLIO)
ACQUIRER DESCRIPTION: a national platform of HVAC and mechanical services companies, backed by Audax Private Equity
ACQUIRER SPONSOR: Audax Private Equity
VALUE: null null (UNDISCLOSED)
VALUATION MULTIPLES:
- Multiple Quality: NOT_CALCULABLE
ADVISORS: null
```

Output:
```json
{
  "summary_text": "PremiStar, LLC, a national platform of HVAC and mechanical services companies backed by Audax Private Equity, today announced the acquisition of Armistead Mechanical, Inc., a privately-held commercial HVAC and mechanical services contractor headquartered in New Jersey. Armistead, founded in 1962, serves commercial customers across the Mid-Atlantic. The transaction is an add-on to PremiStar's existing platform, expanding the company's geographic presence in the Mid-Atlantic and adding skilled service technicians and Armistead's established customer relationships. Financial terms were not disclosed.",
  "word_count": 81,
  "model_confidence": "HIGH",
  "notes": "PE add-on; Audax Private Equity sponsor named in opening per add-on framing rule. Add-on framing explicit. Source's strategic-rationale language ('skilled service technicians,' 'customer relationships') woven without literal enum tag.",
  "prompt_version": "deal_summary:0.6"
}
```

**Example 4 — Multi-sponsor co-investor recap:**

Input:
```
DEAL TYPE: ACQUISITION
EVENT TYPE: ANNOUNCEMENT
TARGET TYPE: STANDALONE_COMPANY
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-01-21
CLOSED DATE: null
TARGET: Harrell-Fish Inc.
TARGET DESCRIPTION: a privately-held mechanical and electrical services contractor with operations across the Midwest
ACQUIRER: New State Capital Partners and Amethyst Capital Group (type: PRIVATE_EQUITY)
ACQUIRER DESCRIPTION: two co-investing private equity firms; New State focused on lower-middle-market business services and industrials, Amethyst on growth-stage industrial and services platforms
ACQUIRER SPONSOR: New State Capital Partners, Amethyst Capital Group
VALUE: null null (UNDISCLOSED)
VALUATION MULTIPLES:
- Multiple Quality: NOT_CALCULABLE
ADVISORS: GLC Advisors & Co. advised TARGET.
```

Output:
```json
{
  "summary_text": "Harrell-Fish Inc., a privately-held mechanical and electrical services contractor with operations across the Midwest, today announced a majority investment from New State Capital Partners and Amethyst Capital Group. New State, focused on lower-middle-market business services and industrials, and Amethyst, focused on growth-stage industrial and services platforms, will partner with Harrell-Fish to support continued organic growth, expand operating resources, and pursue future M&A opportunities. GLC Advisors & Co. served as exclusive financial advisor to Harrell-Fish. Financial terms were not disclosed.",
  "word_count": 81,
  "model_confidence": "HIGH",
  "notes": "Co-investor structure (PRIVATE_EQUITY acquirer with both sponsors named in acquirer_sponsor_name). Source PR's strategic framing ('continued organic growth,' 'deep operating resources') used directly. Single advisor named with role attribution. Majority investment language ('majority investment from') matches source PR framing.",
  "prompt_version": "deal_summary:0.6"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Summary far below expected length with sufficient input | Parser flags, re-prompts once. Persistent failure logged. |
| Summary includes facts not in input (hallucination) | Critical. Gold set verification catches. Prompt explicitly forbids. |
| Summary includes editorial language | QA sampling catches. |
| Summary written in wrong tense for event_type | Style rule #3 covers; QA samples check. |
| Summary states "terms were not disclosed" when value_type ≠ UNDISCLOSED | Style rule under VALUE FRAMING addresses. Indicates upstream data issue if recurrent. |
| Summary misses take-private framing when target_status=PUBLIC + acquirer_type=PRIVATE_EQUITY | DEAL TYPE FRAMING section addresses. QA samples check. |
| Summary describes SPIN_SPLIT as an acquisition | DEAL TYPE FRAMING and examples address. |
| Summary omits multiples when multiple_quality=CALCULATED | Principle #5 addresses. |
| Summary includes multiples when multiple_quality=NM or NOT_CALCULABLE | Principle #5 explicitly prohibits. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Input schema updated to match Drop 2.1 field names: `consideration_components` array with derived `consideration_type`, `termination_fees` object (target/acquirer × amount/percentage), `go_shop` object, `acquirer_type`, `target_type`, SPIN_SPLIT discriminators. System prompt updated with value_type phrasing rules and deal-type-specific handling (take-private, business unit divestiture, spin-split). New few-shot examples added for spin-split and business unit cases. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-04-23 | Removed "carve-out sale" as acceptable terminology for private business unit sales. Per schema taxonomy, "carve-out" is reserved for subsidiary IPOs (out of MVP scope). Private subsidiary sales are divestitures. Updated style rule 8 with explicit prohibition and acceptable alternatives. |
| 0.5 | 2026-04-23 | Added ASSETS to target_type divestiture handling rule (style rule 8). ASSETS targets follow the same summary framing as BUSINESS_UNIT/SUBSIDIARY divestitures. |
| 0.6 | 2026-04-29 | Major rewrite. Replaced template-feeling field-recitation summaries with narrative summaries that weave structured fields into prose. Required: party descriptions in opening (Drop 3.9), PE sponsor names in add-on framing (Drop 3.10), value type framing (Drop 3.11), multiples with period qualifier when CALCULATED (Drop 3.12), all captured advisors named with side and role attribution. Strategic rationale woven from source language rather than enum-tagged. Length follows substance, not artificial floor. Replaced existing few-shot examples with four examples covering: sparse private deal, public take-private with multiples and advisors, PE add-on with sponsor, multi-sponsor co-investor recap. User template extended with target_description, acquirer_description, acquirer_sponsor_name, pct_acquired, multiples columns, parent_seller_description, parent_seller_ticker, target_ticker. |
