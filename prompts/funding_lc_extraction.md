# Funding Low-Confidence Extraction Prompt

**Version:** 0.1
**Repo path:** `prompts/funding_lc_extraction.md`

---

## 1. Purpose

Extract lower-priority fields from a funding event source that are frequently
absent, inconsistently stated, or require judgment. Covers advisors, use of
proceeds, board seat information, ownership percentage, and regulatory flags.

Runs on every `staging_extraction` row where funding HC extraction completed
(`status = HC_EXTRACTED` and `v2_event_type IN (VC_ROUND, GROWTH_EQUITY,
VENTURE_DEBT)`).

Three field groups:
1. **Advisors** — placement agents and legal counsel
2. **Deal context** — use of proceeds, board seats, pct_acquired
3. **Flags** — regulatory approvals

Sources vary in verbosity — press releases may state all of these; portfolio
pages and sparse sources may state none. Use null freely.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-7`
- **Temperature:** 0.0
- **Max tokens:** 1024

---

## 3. Input Schema

```json
{
  "source_raw_id": 12345,
  "source_type": "PR_NEWSWIRE",
  "source_tier": "T2",
  "title": "TechCo Raises $50 Million Series B",
  "clean_text": "TechCo today announced...",
  "v2_event_type": "VC_ROUND",
  "event_history_type": "ANNOUNCED",
  "round_size": 50000000,
  "round_label": "Series B"
}
```

`round_size` and `round_label` from HC extraction are passed so the model can
sanity-check advisor context and proceed amounts.

---

## 4. System Prompt

```
You are a financial data extraction model. Given the text of a funding
announcement — which may be a press release, portfolio page, fund announcement,
or document excerpt — extract the following fields. These fields are often
absent. Use null freely when a field is not stated.

ADVISORS

Extract any advisors mentioned in the text. For each advisor:
- name — firm name as stated
- type — enum: FINANCIAL, LEGAL, OTHER
  - FINANCIAL: investment banks, placement agents, financial advisors
  - LEGAL: law firms providing legal counsel
  - OTHER: accounting, tax, or other advisory roles
- advised_party — enum: COMPANY, INVESTOR, BOTH, UNKNOWN
  - COMPANY: advisor to the company raising capital
  - INVESTOR: advisor to one or more investors
  - BOTH: explicitly advising both sides
  - UNKNOWN: role stated but party unclear

Rules:
- Capture placement agents explicitly — they are common in growth equity and
  venture debt and often named as "exclusive placement agent" or "financial
  advisor to the company"
- Do not include investors as advisors — they are captured in HC extraction
- Do not include internal advisors (in-house counsel, internal finance teams)
- If multiple advisors are listed for the same party, capture each separately

USE OF PROCEEDS

use_of_proceeds: Brief text description of how the company plans to use the
funding, when stated. Use the source's own language. Keep to 1-2 sentences.
Null if not stated.

Common signals:
- "proceeds will be used to..."
- "funding will support..."
- "capital will be deployed to..."
- "will use the funds to..."

BOARD SEATS

has_board_seat: true when an investor is explicitly stated to be taking a
board seat or board observer seat. false when source explicitly says no board
representation. null when not mentioned.

board_seat_notes: Brief description of board seat arrangement when has_board_seat
is true (e.g., "TA Associates will take two board seats", "Venture Partners
will have a board observer right"). Null otherwise.

Common signals for board seats:
- "will join the board of directors"
- "board seat", "board observer"
- "will appoint [name] to the board"
- "board representation"

PCT ACQUIRED

pct_acquired: Ownership percentage acquired by the investor(s), when stated
and not captured in HC extraction. Number only (e.g., 35.0 for 35%). Null if
not stated. Do NOT compute from valuation and round size.

REGULATORY FLAGS

regulatory_approvals_required: true when specific regulatory approvals are
called out (antitrust/HSR, CFIUS, foreign competition approvals). Common for
large growth equity rounds above $500M or rounds involving strategic investors
in sensitive sectors. false/null for most VC rounds.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown
code fences, no preamble.

{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "COMPANY"},
    {"name": "Kirkland & Ellis", "type": "LEGAL", "advised_party": "COMPANY"}
  ],
  "use_of_proceeds": "to expand the company's sales team and accelerate product development in Europe",
  "has_board_seat": true,
  "board_seat_notes": "Venture Partners will join the board of directors",
  "pct_acquired": null,
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "funding_lc_extraction:0.1"
}

All fields are required. Use null for optional fields that have no value.
"prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
SOURCE TYPE: {source_type}
SOURCE TIER: {source_tier}
V2 EVENT TYPE: {v2_event_type}
ROUND: {round_label} / {round_size}

TITLE: {title}

BODY:
{clean_text}

Extract advisors, use of proceeds, board seats, pct_acquired, and regulatory flags.
```

---

## 6. Output Schema

```json
{
  "advisors": [
    {
      "name": "string",
      "type": "FINANCIAL | LEGAL | OTHER",
      "advised_party": "COMPANY | INVESTOR | BOTH | UNKNOWN"
    }
  ],
  "use_of_proceeds": "string | null",
  "has_board_seat": "boolean | null",
  "board_seat_notes": "string | null",
  "pct_acquired": "number | null",
  "regulatory_approvals_required": "boolean | null",
  "model_confidence": "HIGH | MEDIUM | LOW",
  "notes": "string | null",
  "prompt_version": "string"
}
```

**Field routing to schema:**

| Field | Destination table | Column |
|---|---|---|
| `advisors` | `advisor` | One row per advisor per extraction (existing table) |
| `use_of_proceeds` | `staging_extraction` | `use_of_proceeds TEXT` (new column) |
| `has_board_seat` | `staging_extraction` | `has_board_seat INTEGER` (new column) |
| `board_seat_notes` | `staging_extraction` | `board_seat_notes TEXT` (new column) |
| `pct_acquired` | `staging_extraction` | `pct_acquired REAL` (existing — overwrite if HC null) |
| `regulatory_approvals_required` | `staging_extraction` | `regulatory_approvals_required INTEGER` (existing) |

All three new columns (`use_of_proceeds`, `has_board_seat`, `board_seat_notes`)
are also added to `transaction_record` and included in Stage 9 `_FIELDS` for
aggregation.

---

## 7. Few-Shot Examples

**Example 1 — Press release with placement agent and use of proceeds:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: VC_ROUND
ROUND: Series B / 50000000
TITLE: TechCo Raises $50 Million Series B Led by Venture Partners
BODY: TechCo today announced the closing of a $50 million Series B led by
Venture Partners. The proceeds will be used to expand TechCo's sales team and
accelerate product development in Europe. Evercore served as exclusive placement
agent to TechCo. Fenwick & West provided legal counsel to TechCo. Cooley LLP
served as legal counsel to Venture Partners.
```

Output:
```json
{
  "advisors": [
    {"name": "Evercore", "type": "FINANCIAL", "advised_party": "COMPANY"},
    {"name": "Fenwick & West", "type": "LEGAL", "advised_party": "COMPANY"},
    {"name": "Cooley LLP", "type": "LEGAL", "advised_party": "INVESTOR"}
  ],
  "use_of_proceeds": "to expand the company's sales team and accelerate product development in Europe",
  "has_board_seat": null,
  "board_seat_notes": null,
  "pct_acquired": null,
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "funding_lc_extraction:0.1"
}
```

**Example 2 — Growth equity with board seat and pct_acquired:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: GROWTH_EQUITY
ROUND: null / 180000000
TITLE: TA Associates Makes Majority Growth Investment in PortfolioCo
BODY: TA Associates acquired approximately 65% of PortfolioCo for $180 million.
TA Associates Partner Jane Smith will join PortfolioCo's board of directors
alongside a board observer from TA Associates' operating team. Goldman Sachs
served as financial advisor to PortfolioCo. Ropes & Gray provided legal counsel
to TA Associates. The proceeds will be used to fund organic growth initiatives
and pursue strategic acquisitions.
```

Output:
```json
{
  "advisors": [
    {"name": "Goldman Sachs", "type": "FINANCIAL", "advised_party": "COMPANY"},
    {"name": "Ropes & Gray", "type": "LEGAL", "advised_party": "INVESTOR"}
  ],
  "use_of_proceeds": "to fund organic growth initiatives and pursue strategic acquisitions",
  "has_board_seat": true,
  "board_seat_notes": "TA Associates Partner Jane Smith will join the board; TA Associates operating team will have a board observer seat",
  "pct_acquired": 65.0,
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "funding_lc_extraction:0.1"
}
```

**Example 3 — Venture debt, lender counsel only:**

Input:
```
SOURCE TYPE: PR_NEWSWIRE
SOURCE TIER: T2
V2 EVENT TYPE: VENTURE_DEBT
ROUND: null / null
TITLE: TechStartup Secures $30 Million Venture Lending Facility
BODY: TechStartup announced a $30 million venture lending facility from Silicon
Valley Bank. The facility will extend TechStartup's runway ahead of its next
equity financing round. Latham & Watkins served as counsel to Silicon Valley
Bank.
```

Output:
```json
{
  "advisors": [
    {"name": "Latham & Watkins", "type": "LEGAL", "advised_party": "INVESTOR"}
  ],
  "use_of_proceeds": "to extend the company's runway ahead of its next equity financing round",
  "has_board_seat": false,
  "board_seat_notes": null,
  "pct_acquired": null,
  "regulatory_approvals_required": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "funding_lc_extraction:0.1"
}
```

**Example 4 — Sparse source, nothing to extract:**

Input:
```
SOURCE TYPE: PORTFOLIO_PAGE
SOURCE TIER: T3
V2 EVENT TYPE: VC_ROUND
ROUND: Series A / 12000000
TITLE: Acme Ventures Portfolio
BODY: DataCo — Series A, $12M — AI-powered data infrastructure
```

Output:
```json
{
  "advisors": [],
  "use_of_proceeds": null,
  "has_board_seat": null,
  "board_seat_notes": null,
  "pct_acquired": null,
  "regulatory_approvals_required": null,
  "model_confidence": "HIGH",
  "notes": "Sparse portfolio page — no LC fields available.",
  "prompt_version": "funding_lc_extraction:0.1"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model captures investor as advisor | Prompt explicitly excludes investors; parser cross-checks against HC investor names |
| Model populates use_of_proceeds with generic boilerplate ("to grow the business") | Acceptable — use source language rule means this is what was stated |
| Model sets has_board_seat true when source only mentions existing board members | Prompt requires explicit new board appointment language |
| Model computes pct_acquired from valuation and round size | Prompt explicitly forbids; QA monitors |
| Model returns COMPANY for investor-side advisor | Few-shot examples clarify; parser flags mismatches |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-07-28 | Initial version — advisors, use of proceeds, board seats, pct_acquired, regulatory flags for funding events. |
