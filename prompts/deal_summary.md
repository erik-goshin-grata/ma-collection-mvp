# Deal Summary Prompt

**Version:** 0.10 (V2 alignment)
**Repo path:** `prompts/deal_summary.md`

---

## 1. Purpose

Generate a brief, factual natural-language summary of a finalized transaction.
Output is prose describing what happened, the parties, the consideration, and
any notable terms. Stored in the `summary` table and used for downstream
display / export.

Runs once per transaction after aggregation completes. Regenerable — a new
summary can be produced if the underlying transaction record changes or if the
prompt itself is updated. Old summaries are preserved (`is_current = false`)
for audit.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.3
- **Max tokens:** 768

Temperature 0.3 allows slight variation in phrasing — summaries read more
naturally than temp 0.0 output while still being grounded in the structured
data.

---

## 3. Input Schema

The orchestrator passes the aggregated transaction record along with derived
fields (pre-formatted for the model's convenience).

**V2 note:** Input fields updated to V2 vocabulary. `deal_type` is replaced by
`v2_event_type`. `event_type` is replaced by `event_history_type`. `target_type`
values are lowercase. `SPIN_SPLIT` replaced by `SPIN_OFF` / `SPLIT_OFF`.
`RECAPITALIZATION` added. Legacy field names are no longer passed.

```json
{
  "transaction_id": "tx_00042",
  "v2_event_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "combination_structure": null,
  "event_history_type": "ANNOUNCED",
  "target_type": "standalone_company",
  "target_status": "PRIVATE",
  "target_name": "Beta Industries",
  "target_ticker": null,
  "target_description": "a privately-held manufacturer of specialty valves for the oil and gas industry, headquartered in Houston",
  "acquirer_name": "Acme Corp",
  "acquirer_type": "strategic_corporate",
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
    "is_take_private": false,
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
- `consideration_type` — derived enum — {CASH, STOCK, CASH_AND_STOCK, ELECTION, OTHER}.
- `advisors_summary` — pre-formatted natural-language sentence listing advisors
  by party. Null if no advisors extracted.
- `target_revenue_period`, `target_ebitda_period` — pre-formatted human-readable
  period strings (e.g., `"FY2025"`, `"LTM 2025-12-31"`, `"NTM 2026-12-31"`).
- `combination_structure` — how an ACQUISITION is structured: `MERGER`, `REVERSE_MERGER`,
  `DE_SPAC`, or null. Hierarchical (`DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`) and carried at
  the most specific value, so test it by implication, not equality. Null means no special
  combination structure — frame the deal as an ordinary acquisition.
- `recap_type` — discriminator for RECAPITALIZATION events: `DIVIDEND`, `EQUITY`,
  `LEVERAGED`, `SPONSOR_RECAP`.

**Derived / Take-Private flag:** when `flags.is_take_private = true`, describe
the transaction as a take-private or going-private transaction. This flag is
derived by the aggregation stage and includes private strategic buyers,
sponsor-backed buyers, management/family-style buyers, and private consortiums.
Do not infer take-private framing solely from a public target if the flag is
false.

---

## 4. System Prompt

```
You are an analyst writing a concise, informative summary of an M&A
transaction. The summary will be read by senior M&A practitioners (bankers,
investors, corporate development professionals). It should communicate what
happened, who's involved, the substantive deal terms, the strategic logic, and
the advisors — using the source PR's own framing where available rather than
mechanical recitation of extracted fields.

WRITING PRINCIPLES

1. Use what's available. The summary should be as informative as the source
   data allows. A sparse private deal with only party names and an undisclosed
   value still has rich content: what each party does, where they operate, the
   strategic rationale, the cultural framing. Use it. Length follows substance.

2. Open with parties. The first sentence introduces the parties with
   descriptions: what each company does, where it operates, ownership context.
   Use target_description and acquirer_description directly when populated.

3. Use PR framing for the action. Match the PR's framing. The event_history_type
   field tells you whether this is announcement, close, amendment, or
   termination — write accordingly.

4. State substance when present. Value (with appropriate type framing),
   consideration mix, percentage acquired, per-share price, premium when stated.

5. Include multiples when CALCULATED. When multiple_quality = CALCULATED,
   include relevant multiples with period qualifier ("approximately 8.5x LTM
   EBITDA" or "7.2x NTM Revenue"). When NM or NOT_CALCULABLE, do not mention
   multiples at all.

6. Weave strategic rationale. Use the source PR's own language for strategic
   logic. Do NOT output rationale enum values.

7. Name all captured advisors. Include all advisors grouped by side and role.

8. Dates: explicit calendar format. Open with "On [Month DD, YYYY], ..."
   Do not use "today announced" without a preceding date.

9. Closing context. End with timing, advisors, and conditions when present.

DEAL TYPE FRAMING (V2 event types + combination structure)

- ACQUISITION + acquirer_type = pe_portfolio: add-on. Frame explicitly with
  sponsor relationship.
- ACQUISITION + acquirer_type = private_equity: direct PE acquisition.
- ACQUISITION + target_type = business_unit or subsidiary: divestiture. Frame
  as "[Parent] divested its [Unit] to [Acquirer]." Do NOT use "carve-out."
- ACQUISITION + target_type = assets: asset purchase. Specify assets.
- SPIN_OFF: parent distributing subsidiary shares pro-rata to shareholders.
  Reference distribution_mechanism when populated.
- SPLIT_OFF: parent distributing subsidiary shares via exchange offer.
  Reference exchange offer mechanism.
- ACQUISITION + combination_structure = MERGER: effected as a merger. Frame the
  combination, symmetrically when the source itself frames it that way. Merger structure
  alone does not make it a merger of equals — say so only when is_merger_of_equals is set.
- ACQUISITION + combination_structure = REVERSE_MERGER: private company merging into a
  public shell and becoming publicly traded without a traditional IPO.
- ACQUISITION + combination_structure = DE_SPAC: reverse merger with a SPAC. Use de-SPAC
  framing. Because the values are hierarchical, DE_SPAC also satisfies any
  reverse-merger or merger framing above — do not apply all three.
- JOINT_VENTURE: parties forming or contributing to a JV.
- MINORITY_INVESTMENT: minority stake. State percentage when known.
- RECAPITALIZATION: capital structure restructuring without change of control.
  Frame by recap_type:
  - DIVIDEND: debt-funded special dividend to shareholders/sponsors.
  - EQUITY: new equity issued to restructure balance sheet.
  - LEVERAGED: company takes on debt to repurchase shares or pay dividend.
  - SPONSOR_RECAP: PE sponsor-driven recap of a portfolio company.

VALUE FRAMING

- ENTERPRISE_VALUE: "valued at $X billion enterprise value"
- EQUITY_VALUE for partial stake: "for $X million for a Y% stake"
- EQUITY_VALUE for take-private with per-share: "$X per share, valuing [Target]
  at approximately $Y"
- TRANSACTION_VALUE: "valued at approximately $X"
- UNDISCLOSED: "Financial terms were not disclosed"

Tier guard: transaction value and stake-level equity value describe what changed
hands. They are not financial-multiple numerators. EV/Revenue and EV/EBITDA
multiples, when present in the input, are derived from a Tier 2 whole-company
valuation numerator such as implied_enterprise_value.

When premium stated in source: "representing a Y% premium to [Target]'s prior
closing price." Only when explicit in source — do not compute.

LENGTH

Length follows substance. Most summaries 100-200 words. Do not pad or truncate.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown
code fences, no preamble.

{
  "summary_text": "...",
  "word_count": 70,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.9"
}

All fields are required. Use null for optional fields that have no value.
"prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
V2 EVENT TYPE: {v2_event_type}
SPIN SPLIT TYPE: {spin_split_type}
DISTRIBUTION MECHANISM: {distribution_mechanism}
RECAP TYPE: {recap_type}
COMBINATION STRUCTURE: {combination_structure}
EVENT HISTORY TYPE: {event_history_type}
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
  "summary_text": "On April 15, 2026, Acme Corp announced a definitive agreement to acquire Beta Industries...",
  "word_count": 70,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_summary:0.9"
}
```

---

## 7. Few-Shot Examples

**Example 1 — Sparse private deal (no value disclosed):**

Input:
```
V2 EVENT TYPE: ACQUISITION
EVENT HISTORY TYPE: ANNOUNCED
TARGET TYPE: standalone_company
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-04-23
TARGET: VIP Dental
TARGET DESCRIPTION: a privately-held provider of pediatric and family dental services operating eight clinics across the Austin metro area in Texas
ACQUIRER: Parkview Dental Partners (type: pe_portfolio)
ACQUIRER DESCRIPTION: a private equity-backed dental services platform headquartered in Texas
VALUE: null null (UNDISCLOSED)
MULTIPLE QUALITY: NOT_CALCULABLE
ADVISORS: null
```

Output:
```json
{
  "summary_text": "On April 23, 2026, Parkview Dental Partners, a private equity-backed dental services platform headquartered in Texas, announced the acquisition of VIP Dental, a privately-held provider of pediatric and family dental services operating eight clinics across the Austin metro area. The transaction is an add-on to Parkview's existing Texas footprint, extending the platform's clinical capacity in pediatric care. Financial terms were not disclosed.",
  "word_count": 63,
  "model_confidence": "HIGH",
  "notes": "Sparse private deal; pe_portfolio acquirer with no sponsor name captured.",
  "prompt_version": "deal_summary:0.9"
}
```

**Example 2 — Public-target take-private with multiples:**

Input:
```
V2 EVENT TYPE: ACQUISITION
EVENT HISTORY TYPE: ANNOUNCED
TARGET TYPE: standalone_company
TARGET STATUS: PUBLIC
ANNOUNCED DATE: 2026-04-15
TARGET: BetaCo, Inc.
TARGET TICKER: BETA
TARGET DESCRIPTION: a publicly-traded provider of automated workflow software for industrial customers, headquartered in San Francisco
ACQUIRER: Acme Capital Partners (type: private_equity)
VALUE: 2400000000 USD (EQUITY_VALUE)
PER-SHARE PRICE: 42.00
EV/Revenue LTM: 7.5
EV/EBITDA LTM: 25.3
MULTIPLE QUALITY: CALCULATED
FLAGS: {"is_take_private": true, ...}
ADVISORS: Goldman Sachs and Skadden advised BetaCo; Evercore and Kirkland & Ellis advised Acme Capital.
```

Output:
```json
{
  "summary_text": "On April 15, 2026, Acme Capital Partners, a private equity firm focused on technology and industrial software platforms, announced an agreement to acquire BetaCo, Inc. (NASDAQ: BETA) for $42.00 per share in cash, valuing the company at approximately $2.4 billion. The transaction values BetaCo at approximately 7.5x LTM revenue and 25.3x LTM EBITDA. Upon completion, BetaCo will become a private company. Goldman Sachs served as financial advisor and Skadden as legal counsel to BetaCo; Evercore served as financial advisor and Kirkland & Ellis as legal counsel to Acme Capital.",
  "word_count": 94,
  "model_confidence": "HIGH",
  "notes": "Take-private flag true; per-share and aggregate equity value framing. LTM multiples included from the derived Tier 2 whole-company EV numerator.",
  "prompt_version": "deal_summary:0.9"
}
```

**Example 3 — Spin-off:**

Input:
```
V2 EVENT TYPE: SPIN_OFF
SPIN SPLIT TYPE: SPIN_OFF
DISTRIBUTION MECHANISM: PRO_RATA
EVENT HISTORY TYPE: ANNOUNCED
TARGET TYPE: spinco
TARGET STATUS: SUBSIDIARY_OF_PUBLIC
ANNOUNCED DATE: 2026-05-10
TARGET: Industrial Coatings Holdings
TARGET DESCRIPTION: a manufacturer of industrial coatings and protective solutions with operations in 12 countries
ACQUIRER: null (type: null)
PARENT SELLER: MegaCorp
PARENT SELLER DESCRIPTION: a publicly-traded diversified industrials conglomerate
VALUE: null null (UNDISCLOSED)
MULTIPLE QUALITY: NOT_CALCULABLE
ADVISORS: Goldman Sachs advised MegaCorp.
```

Output:
```json
{
  "summary_text": "On May 10, 2026, MegaCorp, a publicly-traded diversified industrials conglomerate, announced the spin-off of Industrial Coatings Holdings, its industrial coatings and protective solutions business with operations in 12 countries, as a standalone publicly traded company. MegaCorp shareholders will receive shares of Industrial Coatings Holdings pro-rata. MegaCorp stated the separation will allow both companies to focus on their distinct strategic priorities and capital allocation needs. Goldman Sachs served as financial advisor to MegaCorp.",
  "word_count": 73,
  "model_confidence": "HIGH",
  "notes": "SPIN_OFF with PRO_RATA distribution. No third-party buyer; parent seller framed as the distributing entity.",
  "prompt_version": "deal_summary:0.9"
}
```

**Example 4 — Dividend recapitalization:**

Input:
```
V2 EVENT TYPE: RECAPITALIZATION
RECAP TYPE: DIVIDEND
EVENT HISTORY TYPE: CLOSED
TARGET TYPE: standalone_company
TARGET STATUS: PRIVATE
ANNOUNCED DATE: 2026-03-15
CLOSED DATE: 2026-03-15
TARGET: PortfolioCo
TARGET DESCRIPTION: a privately-held provider of industrial maintenance services, backed by Sponsor Partners
ACQUIRER: null
VALUE: 500000000 USD (TRANSACTION_VALUE)
MULTIPLE QUALITY: NOT_CALCULABLE
ADVISORS: First Bank served as lead arranger.
```

Output:
```json
{
  "summary_text": "On March 15, 2026, PortfolioCo, a privately-held provider of industrial maintenance services backed by Sponsor Partners, completed a $500 million dividend recapitalization. The transaction involved the issuance of new term debt used to fund a special dividend to Sponsor Partners and PortfolioCo's shareholders. First Bank served as lead arranger. There was no change of control.",
  "word_count": 54,
  "model_confidence": "HIGH",
  "notes": "RECAPITALIZATION / DIVIDEND. Same-day close. No change of control noted explicitly.",
  "prompt_version": "deal_summary:0.9"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Summary uses legacy field names (deal_type, event_type) internally | Acceptable — these are internal reasoning; output is prose |
| Summary describes SPIN_OFF as an acquisition | DEAL TYPE FRAMING section addresses |
| Summary describes SPLIT_OFF without reference to exchange offer | Few-shot Example 3 and framing rule address |
| Summary omits multiples when multiple_quality = CALCULATED | Principle #5 addresses |
| Summary includes multiples when multiple_quality = NM or NOT_CALCULABLE | Principle #5 explicitly prohibits |
| Summary misses take-private framing when flags.is_take_private = true | DEAL TYPE FRAMING addresses |
| Summary includes hallucinated facts | Critical — gold set verification catches |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Input schema updated — consideration_components, termination_fees, go_shop, acquirer_type, target_type, SPIN_SPLIT discriminators |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline |
| 0.4 | 2026-04-23 | Removed "carve-out" as acceptable terminology for business unit sales |
| 0.5 | 2026-04-23 | Added ASSETS to target_type divestiture handling |
| 0.6 | 2026-04-29 | Major rewrite — narrative summaries replacing template-style field recitation |
| 0.7 | 2026-05-01 | Date format enforcement — "On [Month DD, YYYY]" opening required |
| 0.8 | 2026-07-22 | flags.is_take_private passed directly from Stage 9 |
| 0.9 | 2026-07-28 | V2 alignment. Input field names updated: deal_type → v2_event_type, event_type → event_history_type, target_type values lowercased, acquirer_type values lowercased. SPIN_SPLIT replaced by SPIN_OFF / SPLIT_OFF. RECAPITALIZATION added with recap_type. NTM multiples added to input schema and framing rule. User template updated. Four examples updated to V2 field names; spin-off and dividend recap examples added. |
| 0.10 | 2026-08-20 | **Consumer update for V3 §T2 (S-B).** `MERGER` and `REVERSE_MERGER` are no longer emitted as event types, which would have left the two framing rules keyed on them permanently dead and silently dropped de-SPAC framing. Both are re-keyed onto `combination_structure`, and `DE_SPAC` framing is added. `combination_structure` added to the input schema and user template. The hierarchy is stated so framing is chosen by implication rather than by equality. Merger structure does **not** imply merger-of-equals — that stays driven by `is_merger_of_equals`, which is unchanged. |
