# Low-Confidence Extraction Prompt

**Version:** 0.6 (V3 attitude/approach split)
**Repo path:** `prompts/low_confidence_extraction.md`

---

> ## ⚠️ QA NOTE — pending revision (NOT yet applied)
> From the 2026-08-01 MergerLinks QA — see `docs/qa_runbook_mergerlinks_2026_08_01.md`.
> - **#2 Advisors — capture *people* + *side*, not just firm.** Releases name the individual bankers and buy/sell side, e.g. *"Canaccord Genuity (sell-side), led by Sanjay Chadda and Lexia Schwartz… Juan Mejia at BrightTower, buy-side."* Advisor records should hold **firm + person(s) + side** (via the participant model). *Recall is also gated by one-URL ingest — multi-source ingest recovers most missed advisors; the prompt change is the people/side capture.*

---

## 1. Purpose

Extract fields that are frequently absent, inconsistently stated, or require nuanced judgment. These fields are lower-priority than the high-confidence set, but their signal matters for deal analytics when present.

Three field groups:
1. **Advisors** — financial and legal advisors on either side.
2. **Consideration components** — the composition of deal consideration (cash / stock / earnout / etc.) as an array of components. The orchestrator derives a single `consideration_type` classification downstream from these components.
3. **Deal characteristic flags** — deal features that are NOT derivable from the consideration array. Termination fees (split by party), go-shop, earnout presence, board posture and how the offer arrived, competing bids, regulatory approvals.

Runs on every row where high-confidence extraction completed.

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
  "clean_text": "Acme Corp (NASDAQ: ACME)...",
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "event_history_type": "ANNOUNCED",
  "target_type": "standalone_company",
  "value_amount": 500000000,
  "value_currency": "USD",
  "value_type": "TRANSACTION_VALUE"
}
```

**V2 note:** `v2_event_type` is the primary field (V2 EventType vocabulary). `deal_type`
is retained as a transitional alias. `event_history_type` replaces `event_type`
(`ANNOUNCED`, `CLOSED`, `AMENDED`, `TERMINATED`). `target_type` values are lowercase
V2 vocabulary (`standalone_company`, `subsidiary`, `business_unit`, `assets`, `spinco`).
Both legacy and V2 field names are accepted during the migration window.

```json
```

The `value_amount` and `value_type` from high-confidence extraction are passed so the model can sanity-check component sums and compute percentages against total deal value.

---

## 4. System Prompt

```
You are a financial data extraction model. Given the text of an M&A press release or SEC filing, extract the following fields. These fields are often absent — use null freely when a field is not stated.

ADVISORS:

Extract any financial and legal advisors mentioned in the text. For each advisor:
- advisor_name — the firm name as stated (e.g., "Goldman Sachs", "Wachtell, Lipton, Rosen & Katz")
- advisor_type — enum: FINANCIAL, LEGAL, OTHER
- advised_party — enum: TARGET, ACQUIRER, PARENT_SELLER, BOTH, UNKNOWN

Rules:
- "OTHER" covers fairness opinion providers, proxy solicitors, info agents, and accounting/tax advisors.
- Do not include internal advisors (in-house counsel, in-house finance teams) — only external firms.
- If multiple advisors are listed for the same party, capture each as a separate entry.
- If an advisor's role is stated but the advised party is ambiguous, use UNKNOWN.

CONSIDERATION COMPONENTS:

Extract the forms of consideration in the deal as an array of components. For each component:
- form — enum: CASH, ACQUIRER_STOCK, TARGET_STOCK, EARNOUT, CVR, DEBT_ASSUMED, RETAINED_EQUITY, OTHER
- amount — dollar amount of this component (null if not stated or not calculable)
- percentage — percentage of total deal value (null if not calculable)
- description — brief text describing the component (e.g., "$400M cash at closing," "contingent value right paying up to $5 per share")

form enum semantics:
- CASH — cash consideration paid at closing
- ACQUIRER_STOCK — shares of the acquirer issued to target shareholders
- TARGET_STOCK — exchange of target stock (rare in MVP scope)
- EARNOUT — contingent payment based on post-close performance
- CVR — contingent value right
- DEBT_ASSUMED — target debt assumed by the acquirer
- RETAINED_EQUITY — equity rolled over by target shareholders (common in PE deals)
- OTHER — any other form (preferred stock, exchangeable shares, notes). Use description to specify.

EARNOUT components:

An earnout is a contingent payment to target shareholders (or selling principals) tied to post-close performance milestones — typically revenue, EBITDA, or business-specific operational targets over a measurement period (1–5 years post-close).

Source language signals:
- "earnout of up to $X million"
- "$X million in earnouts"
- "performance-based payment of up to $X"
- "contingent on achieving [metric] targets"
- "additional consideration of up to $X based on performance"
- "earn-out" / "earnouts" / "earn out"

When an earnout is present, add a component with form=EARNOUT:
- amount: the maximum if "up to $X" is stated, or the stated amount; null when amount not disclosed
- percentage: percentage of total deal value if calculable; null otherwise
- description: brief summary of what triggers the payment and measurement period if stated

When earnout amount is not stated: set amount=null, description="earnout present but amount not disclosed".

CVR components:

A Contingent Value Right (CVR) is a security issued to target shareholders that pays additional consideration upon specified milestones — most common in pharma/biotech (regulatory approval, sales thresholds), occasionally in legal liability outcomes or post-close events.

Source language signals:
- "Contingent Value Right" / "CVR"
- "non-tradeable / tradeable contingent value rights"
- "additional payment of $X per share upon [milestone]"
- "milestone payment tied to [trigger]"

When a CVR is present, add a component with form=CVR:
- amount: total aggregate CVR value if stated; null if only per-share stated and share count not inferable
- percentage: null in most cases (CVR value often not computable as % of deal)
- description: what milestone unlocks the CVR payment, expiration if stated, tradeable status if stated

LEAVE OUT EARNOUT and CVR entries entirely when:
- Source text does not mention an earnout or CVR
- Mentions "performance" generically without tying it to additional payment ("expected to drive performance" is not an earnout)

Notes:
- Earnouts and CVRs are ADDITIVE to primary consideration. A deal can be cash + earnout, stock + CVR, cash + stock + earnout + CVR. Capture all components.
- The consideration_type field (orchestrator-derived: CASH / STOCK / CASH_AND_STOCK / ELECTION / OTHER) reflects the primary structure. Earnouts and CVRs do NOT change consideration_type — a cash + earnout deal stays consideration_type=CASH. The has_earnout and has_cvr flags are derived downstream from the components array.

Rules:
- Do not derive cash/stock percentages if the release doesn't provide them — leave percentage null.
- Amounts should sum approximately to the deal value; do not force reconciliation.
- For all-cash deals, record a single CASH entry with amount equal to the deal value.
- Empty array is valid (terms not disclosed, or release doesn't detail components).

Do NOT output any "all_cash" or "includes_stock" boolean flags. The orchestrator derives a consideration_type categorization (CASH / STOCK / CASH_AND_STOCK / ELECTION / OTHER) from the components downstream.

DEAL CHARACTERISTIC FLAGS:

Extract features of the deal that are not directly derivable from the consideration array.

Earnout presence:
- includes_earnout — boolean: true if any earnout or CVR component is present. (Yes, this is derivable from the components array, but it's a prominent enough feature that we capture it explicitly for easy filtering.)

Attitude / approach / competitive signals:

These are three independent facts. Do not let one imply another — a transaction may be
unsolicited and also friendly or board-recommended.

- deal_attitude — enum or null: FRIENDLY | HOSTILE | null. The target board's posture
  toward the approach.
    FRIENDLY requires positive evidence that the target or target board supports,
      recommends, approves, or has agreed to the transaction — e.g. "the Board unanimously
      recommends", "entered into a definitive agreement", "the Board approved".
      Do NOT infer FRIENDLY merely because discussions or negotiations occurred.
    HOSTILE when the target or target board rejects the bid or proposal and the bidder
      continues with a new or improved unsolicited bid, or when the source explicitly
      characterises the bid as hostile.
    null otherwise. Absence of hostile evidence is NOT FRIENDLY.
- approach_type — enum or null: SOLICITED | UNSOLICITED | null. How the approach arose.
    UNSOLICITED when the source clearly states or establishes an unsolicited bid or
      proposal.
    SOLICITED when the source clearly states or establishes a solicited process — a sale
      process, auction, strategic review or outreach, or an invitation to bid.
    null otherwise. null is a first-class outcome and will be the most common one.
    Do not infer either value from the absence of the other.
    approach_type is independent of deal_attitude.
- competing_bid — boolean: true if a competing or "topping" bid is referenced

Regulatory:
- regulatory_approvals_required — boolean: true if specific antitrust, CFIUS, or other regulatory approvals are called out

Go-Shop:
- has_go_shop — boolean: true if a go-shop period is mentioned
- go_shop_period_days — integer: duration in days. Null if has_go_shop is false or duration not stated.

Termination Fees (schema splits these by party):
- target_fee_amount — dollar amount payable by target if it terminates. Null if not stated.
- target_fee_percentage — percentage of deal value payable by target. Null if only amount stated, or not stated at all.
- acquirer_fee_amount — dollar amount payable by acquirer (reverse termination fee) if it terminates. Null if not stated.
- acquirer_fee_percentage — percentage of deal value payable by acquirer. Null if only amount stated, or not stated at all.

Termination fee rules:
- If the release states "$135 million termination fee payable by [target]" — target_fee_amount populated, target_fee_percentage null.
- If stated as "approximately 3% of deal value payable by [target]" — target_fee_percentage populated, target_fee_amount null.
- If both are stated ("$135 million, representing approximately 3% of deal value") — populate both.
- "Reverse termination fee" or "payable by [acquirer]" — acquirer side.
- Default all four fields to null. Termination fees are rare in private-party deals and common only in public-target M&A.

All booleans default false unless the text supports them. Do not set any flag true based on deal type alone.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"}
  ],
  "consideration_components": [
    {
      "form": "CASH",
      "amount": 50000000,
      "percentage": 92.6,
      "description": "$50M cash at closing"
    },
    {
      "form": "EARNOUT",
      "amount": 4000000,
      "percentage": 7.4,
      "description": "up to $4M tied to revenue milestones in years 1-2 post-close"
    }
  ],
  "flags": {
    "includes_earnout": true,
    "deal_attitude": null,
    "approach_type": null,
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
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
SOURCE TYPE: {source_type}
V2 EVENT TYPE: {v2_event_type}
TARGET TYPE: {target_type}
EVENT HISTORY TYPE: {event_history_type}
DEAL VALUE: {value_amount} {value_currency} ({value_type})

TITLE: {title}

BODY:
{clean_text}

Extract advisors, consideration components, and deal characteristic flags.
```

---

## 6. Output Schema

```json
{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"}
  ],
  "consideration_components": [
    {
      "form": "CASH",
      "amount": 500000000,
      "percentage": 100.0,
      "description": "All-cash consideration paid at closing"
    }
  ],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": null,
    "approach_type": null,
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
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Field notes:**

- `advisors` and `consideration_components` are always arrays (possibly empty). Empty array = no mentions in source, distinct from unknown.
- `flags` contains only features not derivable from `consideration_components`. Payment mix flags (`all_cash`, `includes_stock`) removed in v0.2 — derived by orchestrator from the components array.
- `go_shop` is now an object: `has_go_shop` + `go_shop_period_days`.
- `termination_fees` is now an object with target/acquirer × amount/percentage, per schema.

---

## 7. Few-Shot Examples

**Example 1 — All-cash deal with advisors, no termination fees:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: 500000000 USD (TRANSACTION_VALUE)

TITLE: Acme Corp to Acquire Beta Industries for $500 Million
BODY: Acme Corp (NASDAQ: ACME) today announced a definitive agreement to acquire Beta Industries for $500 million in cash. Goldman Sachs is serving as exclusive financial advisor and Wachtell, Lipton, Rosen & Katz is serving as legal counsel to Acme. Morgan Stanley is serving as financial advisor to Beta, with Kirkland & Ellis as its legal counsel.
```

Output:
```json
{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Wachtell, Lipton, Rosen & Katz", "type": "LEGAL", "advised_party": "ACQUIRER"},
    {"name": "Morgan Stanley", "type": "FINANCIAL", "advised_party": "TARGET"},
    {"name": "Kirkland & Ellis", "type": "LEGAL", "advised_party": "TARGET"}
  ],
  "consideration_components": [
    {"form": "CASH", "amount": 500000000, "percentage": 100.0, "description": "All-cash at closing"}
  ],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": "FRIENDLY",
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 2 — Mixed consideration with earnout:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: 800000000 USD (TRANSACTION_VALUE)

TITLE: Acme Corp Acquires Beta Industries for Up to $800 Million
BODY: Acme Corp today announced the acquisition of Beta Industries for upfront consideration of $600 million, comprised of $400 million in cash and $200 million in Acme common stock. Additionally, Beta shareholders will be entitled to earnout payments of up to $200 million based on Beta's revenue performance over the three years following closing.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 400000000, "percentage": 50.0, "description": "$400M cash at closing"},
    {"form": "ACQUIRER_STOCK", "amount": 200000000, "percentage": 25.0, "description": "$200M in Acme common stock at closing"},
    {"form": "EARNOUT", "amount": 200000000, "percentage": 25.0, "description": "Up to $200M earnout over 3 years based on revenue"}
  ],
  "flags": {
    "includes_earnout": true,
    "deal_attitude": null,
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Percentages against $800M max deal value",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 3 — Take-Private with termination fees (both sides), go-shop, regulatory:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: 4500000000 USD (EQUITY_VALUE)

TITLE: Acme Corp to Be Acquired by Zenith Capital in $4.5 Billion Transaction
BODY: Acme Corp (NYSE: ACME) entered into a definitive merger agreement with Zenith Capital Partners for $45.00 per share in cash. The agreement includes a 35-day "go-shop" period during which Acme's board may solicit alternative proposals. A termination fee of $135 million is payable by Acme if the agreement is terminated under specified circumstances. Zenith will pay a reverse termination fee of $270 million, representing approximately 6% of the equity value, if the transaction fails to close due to regulatory reasons. The transaction is subject to customary regulatory approvals, including HSR clearance.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 4500000000, "percentage": 100.0, "description": "$45.00 per share cash aggregate equity consideration"}
  ],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": "FRIENDLY",
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": true
  },
  "go_shop": {"has_go_shop": true, "go_shop_period_days": 35},
  "termination_fees": {
    "target_fee_amount": 135000000,
    "target_fee_percentage": null,
    "acquirer_fee_amount": 270000000,
    "acquirer_fee_percentage": 6.0
  },
  "model_confidence": "HIGH",
  "notes": "HSR explicit. Target fee amount-only; acquirer fee both amount and percentage stated.",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 4 — Cash plus earnout:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: 54000000 USD (TRANSACTION_VALUE)

TITLE: SkyCore Technologies Acquired for $54 Million
BODY: SkyCore Technologies today announced it has been acquired for $50 million in cash at close plus up to $4 million in earnouts tied to revenue performance over the two years following close. No advisors were disclosed.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 50000000, "percentage": 92.6, "description": "$50M cash at closing"},
    {"form": "EARNOUT", "amount": 4000000, "percentage": 7.4, "description": "up to $4M tied to revenue performance years 1-2 post-close"}
  ],
  "flags": {
    "includes_earnout": true,
    "deal_attitude": null,
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Percentages against $54M max deal value",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 5 — Cash per share plus CVR:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: null null (UNDISCLOSED)

TITLE: PharmaCo to Acquire BioTarget for $25.00 Per Share Plus CVR
BODY: PharmaCo today announced a definitive agreement to acquire BioTarget Inc. (NASDAQ: BTGT) for $25.00 per share in cash at close, plus a non-tradeable Contingent Value Right of up to $5.00 per share tied to FDA approval of ABC-123 by December 31, 2027. Lazard is serving as financial advisor to PharmaCo; Centerview Partners is advising BioTarget.
```

Output:
```json
{
  "advisors": [
    {"name": "Lazard", "type": "FINANCIAL", "advised_party": "ACQUIRER"},
    {"name": "Centerview Partners", "type": "FINANCIAL", "advised_party": "TARGET"}
  ],
  "consideration_components": [
    {"form": "CASH", "amount": null, "percentage": null, "description": "$25.00 per share cash at closing"},
    {"form": "CVR", "amount": null, "percentage": null, "description": "non-tradeable CVR of up to $5.00 per share; FDA approval of ABC-123 by 2027-12-31"}
  ],
  "flags": {
    "includes_earnout": true,
    "deal_attitude": "FRIENDLY",
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Total deal value not computable without share count; amounts left null",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

**Example 6 — Closing release, no terms disclosed:**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: CLOSED
DEAL VALUE: null null (UNDISCLOSED)

TITLE: Acme Corp Completes Acquisition of Beta Industries
BODY: Acme Corp today announced that it has completed its previously announced acquisition of Beta Industries. Terms of the transaction were not disclosed.
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": null,
    "approach_type": null,
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "Closing release with no financial terms disclosed",
  "prompt_version": "low_confidence_extraction:0.4"
}
```

---

**Example 7 — Unsolicited approach, board evaluating (real source):**

*Source: Business News Australia, 18 August 2026 — "TPG swoops on EQT Holdings with $658m
takeover bid after First Guardian fallout hammers shares."
`businessnewsaustralia.com/articles/tpg-swoops-on-eqt-holdings-with-658m-takeover-bid-after-first-guardian-fallout-hammers-shares.html`
Body text below is **verbatim** from the source, including its own typo ("First Guarrdian")
and grammar — real releases are not clean, and the extraction must not depend on their being so.*

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: 657700000 AUD (TRANSACTION_VALUE)

TITLE: TPG swoops on EQT Holdings with $658m takeover bid after First Guardian fallout hammers shares.
BODY: Private equity group TPG Global has swooped on the depressed share price of EQT Holdings (ASX: EQT) in the wake of the First Guarrdian collapse to launch a takeover $657.7 million takeover bid for the Melbourne-based financial services group.

The unsolicited offer for 100 per cent of the company is priced a $24.55 cash per share, a healthy premium to the company's closing price of $17.31 yesterday.

The non-binding bid lands after a torrid stretch for EQT's share price, which plunged from $34.50 a year ago to a low of $14.70 earlier this year as the company grappled with the fallout from the First Guardian Master Fund collapse.

TPG's proposal is conditional on completion of due diligence as well as clearances from the Foreign Investment Review Board, the Australian Competition and Consumer Commission and the Australian Prudential Regulation Authority.

"TPG has requested a period of exclusivity to conduct due diligence and negotiate transaction documentation," says EQT.

"The board of EQT, together with its advisers, will evaluate the proposal and will update shareholders in due course."
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [
    {"form": "CASH", "amount": 657700000, "percentage": 100.0, "description": "$24.55 cash per share for 100% of the company"}
  ],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": null,
    "approach_type": "UNSOLICITED",
    "competing_bid": false,
    "regulatory_approvals_required": true
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "approach_type UNSOLICITED on the source's own word. deal_attitude null: the board will evaluate the proposal and has not supported, recommended, approved or agreed to it."
}
```

**Why this example matters — the two dimensions are independent.**

`approach_type = UNSOLICITED` and `deal_attitude = null` on the same transaction.

- **Unsolicited is not hostile.** Nothing here is rejected or opposed. The target has not taken
  a posture at all — it says it "will evaluate the proposal". Setting `deal_attitude = HOSTILE`
  because the offer was unsolicited would be reading one dimension off the other, which is
  exactly the conflation this version removes.
- **Nor is it friendly.** Requesting exclusivity to "negotiate transaction documentation" is a
  negotiation, not agreement. Per the rule above, discussions and negotiations do **not**
  support `FRIENDLY`; only support, recommendation, approval or agreement does.
- **`advisors` is empty** even though the source says the board is acting "together with its
  advisers". No firm is named, so there is nothing to extract — an unnamed adviser is not an
  advisor record.
- **`regulatory_approvals_required` is true** because FIRB, the ACCC and APRA are called out
  by name.

---

**Examples 8 and 9 — the same bidder, the same target, two weeks apart (real source).**

*Read these as a pair. The approach does not change between them; the board's posture does.
Body text is the excerpt as supplied, ellipses included.*

**Example 8 — Unsolicited proposal, board will review (Seer, 13 April 2026):**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: ANNOUNCED
DEAL VALUE: null null (UNDISCLOSED)

TITLE: Seer Confirms Receipt of Unsolicited Proposal
BODY: Seer... confirmed that it has received a highly contingent, non-binding and unsolicited proposal from Bradley L. Radoff and Michael Torok... Consistent with its fiduciary duties... the Seer Board of Directors will carefully review and consider the Proposal...
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": null,
    "approach_type": "UNSOLICITED",
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "approach_type UNSOLICITED on the source's own word. deal_attitude null: a board that will review and consider a proposal has taken no posture — reviewing is neither support nor opposition."
}
```

**Example 9 — The same proposal, revised and rejected (Seer, 27 April 2026):**

Input:
```
V2 EVENT TYPE: ACQUISITION
TARGET TYPE: standalone_company
EVENT HISTORY TYPE: AMENDED
DEAL VALUE: null null (UNDISCLOSED)

TITLE: Seer Board Rejects Revised Unsolicited Proposal
BODY: Seer... announced that its Board of Directors has thoroughly reviewed and unanimously rejected the revised unsolicited, non-binding proposal...
```

Output:
```json
{
  "advisors": [],
  "consideration_components": [],
  "flags": {
    "includes_earnout": false,
    "deal_attitude": "HOSTILE",
    "approach_type": "UNSOLICITED",
    "competing_bid": false,
    "regulatory_approvals_required": false
  },
  "go_shop": {"has_go_shop": false, "go_shop_period_days": null},
  "termination_fees": {
    "target_fee_amount": null,
    "target_fee_percentage": null,
    "acquirer_fee_amount": null,
    "acquirer_fee_percentage": null
  },
  "model_confidence": "HIGH",
  "notes": "deal_attitude HOSTILE: the board unanimously rejected the proposal and the bidder continued with a further improved unsolicited proposal. approach_type stays UNSOLICITED — a revised proposal from the same unsolicited bidder is still unsolicited."
}
```

**What the pair teaches.**

`approach_type` is **identical** in both — `UNSOLICITED`. `deal_attitude` moves from `null` to
`HOSTILE`. Same bidder, same target, two weeks apart.

- **`UNSOLICITED` does not imply `HOSTILE`.** On 13 April the proposal was already unsolicited
  and the board had taken no posture. Had `deal_attitude` been read off the approach, Example 8
  would have been labelled hostile two weeks before the board decided anything. V2's single
  `hostile` boolean did exactly that: its definition made "unsolicited" sufficient to set the bit.
- **A board reviewing an offer is not a posture.** "Will carefully review and consider",
  offered "consistent with its fiduciary duties", is what a board is obliged to do on receipt of
  any proposal. It is not support, and it is not opposition.
- **Rejection plus a continuing bidder establishes `HOSTILE`** — "thoroughly reviewed and
  unanimously rejected", with the bidder returning with a further improved unsolicited proposal.
- **The approach label does not move.** A revised proposal from the same unsolicited bidder is
  still unsolicited. Do not relabel `approach_type` because the posture resolved.

**Still missing: a worked `SOLICITED` example.** No source establishing a target-initiated
process is available yet. Note that "strategic review" alone does not qualify — a review of
operations is not a solicitation of buyers; the language must establish that the **target**
initiated a process to find one.

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model outputs v0.1 fields (`all_cash`, `includes_stock`) | Parser rejects — these are derived downstream, not extracted |
| Model uses v0.1 `break_fee_amount` / `break_fee_disclosed` fields | Parser rejects — replaced with `termination_fees` object in v0.2 |
| Model confuses target and acquirer fees | Prompt explicitly addresses. QA samples catch. |
| Model populates both amount and percentage when only one is stated | Accept (valid), but flag in notes if inferred |
| Model invents advisors not in text | Gold set catches; prompt forbids |
| Model attributes advisor to wrong party | Common when advisors are listed at the end. advised_party=UNKNOWN is provided for ambiguous cases. |
| Percentage fields don't sum to 100 | Aggregation stage flags; release may have unaccounted components |
| `go_shop_period_days` populated but `has_go_shop` = false | Parser rejects (logical contradiction) |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-22 | Revised. Removed `all_cash` and `includes_stock` flags (derived by orchestrator from consideration array). Split `break_fee_*` fields into `termination_fees` object with target/acquirer × amount/percentage per schema. Formalized `go_shop` as object with `has_go_shop` + `go_shop_period_days`. Renamed `consideration` → `consideration_components` for clarity. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.4 | 2026-05-02 | Added EARNOUT and CVR component-type guidance to consideration_components extraction. Components are additive to primary consideration; do not change consideration_type. Added few-shot examples for both (Examples 4, 5). Updated RESPONSE FORMAT to show earnout component. |
| 0.5 | 2026-07-28 | V2 alignment. Input schema updated: `deal_type` → `v2_event_type` (deal_type retained as alias); `event_type` → `event_history_type` (ANNOUNCED/CLOSED/AMENDED/TERMINATED); `target_type` values lowercased (V2 vocabulary). User template updated. All examples updated to V2 field names. Note: LC extraction logic is deal-type-agnostic — advisors, consideration components, and flags are extracted regardless of whether the event is M&A or funding. No taxonomy changes required. |
| 0.6 | 2026-08-20 | **V3 attitude/approach split (§T11).** The fused `hostile` boolean is removed and replaced by two independent nullable dimensions: `deal_attitude` (`FRIENDLY`/`HOSTILE`/null) and `approach_type` (`SOLICITED`/`UNSOLICITED`/null). `hostile` conflated three facts — posture, approach and proxy contest — and its false-by-default handling made "unstated" indistinguishable from "friendly"; both new fields are null when the source does not establish the fact, and **absence of hostile evidence is not `FRIENDLY`**. `FRIENDLY` requires positive support/recommendation/agreement evidence, not merely that negotiations occurred. **Proxy contest is deliberately not carried forward** — §T11.1 does not promote it to V3, and `hostile`'s third clause was its only capture. `competing_bid` is unchanged and remains a boolean. Examples set `deal_attitude` only where the example's own body establishes posture. **Example 7 added** from real source text (Business News Australia, TPG/EQT Holdings, 2026-08-18), demonstrating `approach_type = UNSOLICITED` with `deal_attitude = null` — unsolicited is neither hostile nor friendly, and a board that "will evaluate" has not agreed. **Examples 8 and 9 added** from real source text (Seer, 13 and 27 April 2026) as a deliberate pair: the same unsolicited bidder, `deal_attitude` moving from null to `HOSTILE` while `approach_type` stays `UNSOLICITED` — the counterexample that separates approach from posture. `approach_type` is set only when the source clearly states or establishes an unsolicited bid or a solicited process; **neither value is inferred from the absence of the other**, and null is a first-class outcome and the most common one. `HOSTILE` is rejection with a continuing bidder, or an explicitly hostile characterisation. **A worked `SOLICITED` example is still pending** — no source establishing a target-initiated process is available yet. |
