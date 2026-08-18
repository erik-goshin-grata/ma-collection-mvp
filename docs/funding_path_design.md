# Funding Path Design

**Version:** 0.1 (draft)
**Date:** 2026-07-28
**Status:** Design — not yet implemented
**Repo path:** `docs/funding_path_design.md`

---

> ## ⚠️ DATED DESIGN DRAFT — SUPERSEDED IN PART BY THE SHIPPED IMPLEMENTATION
>
> This is the **2026-07-28 design draft**, preserved as written. The header above still
> says *"Status: Design — not yet implemented"*; **that is no longer true.** The funding
> path shipped — `stages/funding_hc_extract.py`, `prompts/funding_hc_extraction.md`,
> `prompts/funding_lc_extraction.md`, and the `staging_investor` table
> (`schema/003_funding_path.sql`) all exist and run. Read the header status line as
> historical.
>
> Two further items in this document are superseded and are **not** open questions:
>
> | Where | What the draft says | Current state |
> |---|---|---|
> | §10 Open Question 1 | **`MINORITY_INVESTMENT` routing** — "keep on M&A path or move to funding path? … Defer until QA identifies systematic misclassification." | **Resolved differently — the question dissolved rather than being answered.** Classifier **0.7** removed `MINORITY_INVESTMENT` from the core output vocabulary entirely: minority is a derived flag (`is_minority`), not an event type, so a minority deal now routes to its underlying economic event. There is no `MINORITY_INVESTMENT` class left to assign a path to. Legacy rows already in the corpus keep the value and are still handled (`stages/aggregate.py` `_NON_CONTROL_TYPES`). |
> | Decisions table, row "`MINORITY_INVESTMENT` path" | "M&A path for now — Deferred" | Same as above: superseded by classifier 0.7, not still deferred. |
>
> Everything else here may also have drifted from the shipped code. Current sources of
> truth are the `_VALID_*` frozensets in `stages/`, the version tables inside `prompts/`,
> and `docs/decisions.md`. The rows above are what a consistency sweep positively
> identified — **not** a warranty about the rest of this draft.

---

## 1. Overview

The pipeline currently handles M&A events end-to-end. This document designs the
funding event path — VC rounds, growth equity investments, and venture debt
facilities — as a parallel extraction branch that shares the same relevancy
filter, classifier, clustering, aggregation, and summary stages, but uses a
dedicated extraction prompt shaped for funding-specific fields.

---

## 2. Scope

**In scope:**
- `VC_ROUND` — priced or unpriced venture rounds (Seed through Series N, angel,
  crowdfunding, convertible notes as primary instrument)
- `GROWTH_EQUITY` — growth equity minority investments by late-stage investors
- `VENTURE_DEBT` — debt facilities to venture-backed or growth-stage companies

**Deferred:**
- `MINORITY_INVESTMENT` — corporate strategic minority stakes. Currently routes
  to the M&A path (acquirer = strategic investor, `pct_acquired` populated).
  Shares characteristics of both paths — control provisions can be M&A-like,
  party shape is funding-like. Decision to fold into funding path or keep on
  M&A path deferred until QA surfaces systematic misclassification.

**Out of scope:**
- Structured PredictLeads Financing Events API — future adapter; will bypass
  extraction entirely and map structured fields directly to schema
- CBI funding coverage — handled by the warehouse reconciler, not this pipeline

---

## 3. Source Coverage

Funding announcements arrive via the same source types as M&A:

| Source | Coverage | Notes |
|---|---|---|
| PR Newswire RSS | Good for large rounds ($10M+) | Company-issued announcements |
| Business Wire RSS | Good for large rounds | Company-issued announcements |
| Global Newswire RSS | Moderate | Mix of quality |
| PredictLeads News Events | Pre-categorized hint available (`raises_funding` etc.) | Text still read via `source_body_lite` — cannot trust category alone |
| SEC filings | Limited — 8-K Item 1.01 for some structured rounds | PIPE transactions, convertible notes in public companies |

PredictLeads `category` is passed as `relevancy_reason_code` (advisory hint only).
The classifier reads text and may override. A PredictLeads `raises_funding`
category on an article that describes a PE buyout should classify as `ACQUISITION`,
not `VC_ROUND`.

---

## 4. Pipeline Architecture

No new stages. The orchestrator (`run.py`) branches on `v2_event_type` after
Stage 3 (classification):

```
source_raw (FETCHED)
    ↓
Stage 2: relevancy_filter          — unchanged; VC_ROUND_OR_FUNDING now in scope
    ↓
Stage 3: deal_type_classify        — unchanged; outputs VC_ROUND / GROWTH_EQUITY /
    ↓                                VENTURE_DEBT for funding events
    ├── v2_event_type ∈ {VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT}
    │       ↓
    │   Stage 4b: funding_hc_extract   — NEW
    │       ↓
    └── v2_event_type ∈ MA types
            ↓
        Stage 4a: high_confidence_extract  — existing

    [shared from Stage 5 onward]
    ↓
Stage 5: sec_trigger               — unchanged (mostly N/A for funding)
Stage 6: sec_enrich                — unchanged (mostly N/A for funding)
Stage 7: lc_extract                — unchanged; deal-type-agnostic
Stage 8: cluster                   — unchanged
Stage 9: aggregate                 — minor extension for funding fields
Stage 10: summarize                — unchanged; deal-type-agnostic
Stage 11: rationale_tag            — unchanged; deal-type-agnostic
```

**Stage 4b** (`funding_hc_extract.py`) is a new stage file that mirrors the
structure of `high_confidence_extract.py` but calls `funding_hc_extraction.md`
and writes funding-specific fields to `staging_extraction` and `staging_investor`.

---

## 5. Schema Changes

### 5a. New columns on `staging_extraction`

Funding scalar fields — nullable on all existing rows.

```sql
-- Round classification
ALTER TABLE staging_extraction ADD COLUMN round_label TEXT;
-- "Series B", "Seed Extension", "Bridge Round", etc. — as stated in source

ALTER TABLE staging_extraction ADD COLUMN round_stage_category TEXT;
-- PRE_SEED | SEED | EARLY_STAGE | GROWTH | LATE_STAGE
-- Derived by pipeline from round_label; not extracted by prompt

-- Round size and valuation
ALTER TABLE staging_extraction ADD COLUMN round_size REAL;
-- Total amount raised in this round

ALTER TABLE staging_extraction ADD COLUMN pre_money_valuation REAL;
ALTER TABLE staging_extraction ADD COLUMN post_money_valuation REAL;
ALTER TABLE staging_extraction ADD COLUMN valuation_currency TEXT;
-- ISO 4217; usually USD

-- Venture debt specific
ALTER TABLE staging_extraction ADD COLUMN facility_size REAL;
-- For VENTURE_DEBT: total debt facility size

-- Round metadata
ALTER TABLE staging_extraction ADD COLUMN total_raised_to_date REAL;
-- Cumulative raised including this round, when stated

ALTER TABLE staging_extraction ADD COLUMN is_extension_round INTEGER;
-- 0/1: extension or continuation of a prior round

ALTER TABLE staging_extraction ADD COLUMN is_down_round INTEGER;
-- 0/1: valuation below prior round

ALTER TABLE staging_extraction ADD COLUMN is_up_round INTEGER;
-- 0/1: valuation above prior round (usually implicit; flag when source confirms)

ALTER TABLE staging_extraction ADD COLUMN is_bridge_round INTEGER;
-- 0/1: explicitly described as bridge financing
```

### 5b. New `staging_investor` table

Mirrors the existing `advisor` table pattern — one row per investor per
extraction, FK to `staging_extraction`. Investor arrays are normalized here
rather than serialized as JSON on `staging_extraction`.

```sql
CREATE TABLE IF NOT EXISTS staging_investor (
    investor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id INTEGER NOT NULL REFERENCES staging_extraction(extraction_id),
    name TEXT NOT NULL,
    domain TEXT,
    investor_type TEXT,
    -- vc_firm | growth_equity | corporate_vc | family_office | hedge_fund |
    -- sovereign_wealth_fund | angel | accelerator | unknown
    is_lead INTEGER DEFAULT 0,        -- 0/1
    lead_investor_rank INTEGER,       -- 1 = lead, 2 = second, etc.; null if not stated
    investment_amount REAL,           -- individual investor's amount when stated
    investment_currency TEXT,         -- ISO 4217
    is_new_investor INTEGER,          -- 0/1: first-time investor in this company
    is_existing_investor INTEGER,     -- 0/1: follow-on from prior round
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_staging_investor_extraction
    ON staging_investor(extraction_id);
```

### 5c. New columns on `transaction_record`

Funding scalars aggregated from `staging_extraction` — nullable on all existing
M&A rows.

```sql
ALTER TABLE transaction_record ADD COLUMN round_label TEXT;
ALTER TABLE transaction_record ADD COLUMN round_stage_category TEXT;
ALTER TABLE transaction_record ADD COLUMN round_size REAL;
ALTER TABLE transaction_record ADD COLUMN pre_money_valuation REAL;
ALTER TABLE transaction_record ADD COLUMN post_money_valuation REAL;
ALTER TABLE transaction_record ADD COLUMN valuation_currency TEXT;
ALTER TABLE transaction_record ADD COLUMN facility_size REAL;
ALTER TABLE transaction_record ADD COLUMN total_raised_to_date REAL;
ALTER TABLE transaction_record ADD COLUMN is_extension_round INTEGER DEFAULT 0;
ALTER TABLE transaction_record ADD COLUMN is_down_round INTEGER DEFAULT 0;
ALTER TABLE transaction_record ADD COLUMN is_bridge_round INTEGER DEFAULT 0;
```

### 5d. V2 alignment note

At V2 promotion:
- `staging_investor` rows → `transaction_party` rows with `role = INVESTOR`,
  `is_lead`, `lead_investor_rank` carried through
- `round_size`, `pre_money_valuation`, `post_money_valuation` → `financial_metric`
  rows with `metric_type = ROUND_SIZE / PRE_MONEY_VALUATION / POST_MONEY_VALUATION`
- `facility_size` → `financial_metric` row with `metric_type = FACILITY_SIZE`
  (pending `MetricType` enum addition)

---

## 6. New Prompt: `funding_hc_extraction.md`

### Purpose

Extract structured data from a funding announcement. Distinct from M&A
high-confidence extraction — the party shape, financial fields, and round
metadata are funding-specific.

### Key differences from `high_confidence_extraction.md`

| Dimension | M&A HC | Funding HC |
|---|---|---|
| Primary party | acquirer + target | company raising + investors (array) |
| Value field | deal value (EV/TV/equity) | round size + pre/post-money valuation |
| Financial metrics | target revenue/EBITDA | funding-specific only |
| Dates | announced + close + signing | announced + close |
| Flags | hostile, competing bid, regulatory | extension, down round, bridge |
| Consideration | cash/stock/earnout/CVR | round size per investor (when stated) |

### Output schema (key fields)

```json
{
  "transactions": [
    {
      "company": {
        "name": "TechCo",
        "domain": "techco.com",
        "ticker": null,
        "description": "a Series B-stage provider of logistics automation software"
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
        "facility_size": null,
        "total_raised_to_date": null,
        "is_extension_round": false,
        "is_down_round": false,
        "is_bridge_round": false
      },
      "dates": {
        "announced_date": "2026-07-15",
        "announced_date_precision": "exact",
        "closed_date": "2026-07-15",
        "closed_date_precision": "exact"
      },
      "financials_disclosure_status": "DISCLOSED",
      "model_confidence": "HIGH",
      "notes": null,
      "prompt_version": "funding_hc_extraction:0.1"
    }
  ]
}
```

### Extraction rules (summary)

- `company` is the entity raising capital — maps to `target` in `staging_extraction`
- `investors` array — one element per named investor; extract all named investors,
  not just the lead
- `is_lead` — true for the investor named as lead; false for all others; null if
  no lead designated
- `lead_investor_rank` — 1 for lead, 2+ for named co-investors in order of mention;
  null when no ordering stated
- `is_new_investor` / `is_existing_investor` — from source language ("new investor",
  "existing investor", "follow-on", "returning backer")
- `round.size` — total round amount; not individual investor amounts unless only
  one investor
- `round.label` — as stated in source ("Series B", "Seed Extension", "Bridge Round")
- `round.pre_money_valuation` / `post_money_valuation` — only when explicitly stated;
  do NOT compute from round size and ownership percentage
- Period type not needed on funding financials — no LTM/NTM distinction for
  pre/post-money valuations

---

## 7. Stage 9 Aggregation Extensions

`aggregate.py` needs minor extension for funding fields:

**Add to `_FIELDS`:**
```python
("round_label", "string"),
("round_size", "number"),
("pre_money_valuation", "number"),
("post_money_valuation", "number"),
("valuation_currency", "string"),
("facility_size", "number"),
("total_raised_to_date", "number"),
("is_extension_round", "boolean"),
("is_down_round", "boolean"),
("is_bridge_round", "boolean"),
```

**Investor aggregation:**
After field resolution, Stage 9 reads `staging_investor` rows for all cluster
members and deduplicates investors by normalized name. The lead investor from
the highest-tier source wins. Output goes to a new `transaction_investor` table
(mirrors `advisor` table → `transaction_record` relationship).

**`round_stage_category` derivation:**
Derived from `round_label` in Stage 9, not extracted by prompt:

```python
_ROUND_STAGE_MAP = {
    ("pre-seed", "pre_seed", "preseed"): "PRE_SEED",
    ("seed", "angel", "founder"): "SEED",
    ("series a", "series-a"): "EARLY_STAGE",
    ("series b", "series-b", "series c", "series-c"): "GROWTH",
    ("series d", "series e", "series f", "growth equity",
     "growth", "late stage", "late-stage"): "LATE_STAGE",
}
```

---

## 8. Multiples for Funding Events

EV/Revenue and EV/EBITDA multiples are not applicable for funding events.
`_compute_multiples()` in Stage 9 skips calculation when `v2_event_type` is
`VC_ROUND`, `GROWTH_EQUITY`, or `VENTURE_DEBT`.

Revenue multiples on post-money valuation (`PMV / Revenue`) are meaningful for
growth equity but require NTM revenue — not extractable from press releases
reliably. Deferred to a later workstream.

---

## 9. Deal Summary and Rationale

Both `deal_summary.md` and `strategic_rationale.md` are deal-type-agnostic and
handle funding events without changes. The deal summary prompt already has
`MINORITY_INVESTMENT` framing; it needs a funding-specific framing block added
for `VC_ROUND`, `GROWTH_EQUITY`, and `VENTURE_DEBT` in the next version.

**`deal_summary.md` addition needed (v0.10):**

```
VC_ROUND / GROWTH_EQUITY: Frame as "[Investor(s)] led a $X [round label] in
[Company]." Name lead investor first. Name all captured investors. Include
pre/post-money valuation when stated. Note round extension or down-round
status when flagged. Use "participated in" for non-lead investors.

VENTURE_DEBT: Frame as "[Lender] provided a $X venture lending facility to
[Company]." Note interest rate or maturity if stated. Context on what the
facility finances when stated.
```

**`strategic_rationale.md`:** Funding events default to `FINANCIAL_OR_ARBITRAGE`
unless source explicitly states strategic rationale — already handled by the
PE/sponsor rule. No change needed.

---

## 10. Open Questions

1. **`MINORITY_INVESTMENT` routing** — keep on M&A path or move to funding path?
   Corporate strategic minorities have control provisions; pure financial minorities
   look like funding. Defer until QA identifies systematic misclassification.

2. **`staging_investor` → `transaction_investor` aggregation table** — needed to
   surface investors on `transaction_record` for summary/export. Design mirrors
   `advisor` table. Add in same migration as funding schema changes.

3. **`round_stage_category` enum** — needs eng confirmation (open item in
   `enum_schema_gaps.md`). Pipeline derives it internally; V2 warehouse needs
   the same enum for conformance.

4. **PredictLeads Financing Events adapter** — when built, bypasses Stages 2-4b
   entirely. Maps structured API fields directly to `staging_extraction` +
   `staging_investor`. Design separately.

6. **Form D adapter extension** — SEC adapter needs Form D trigger for funding
   events. Filing lag (up to 15 days post first sale) means the lookback window
   needs to be wider than for 8-K enrichment. Coordinate with `sec_api.py`
   adapter work.

---

## 11. Implementation Sequence

1. Schema migration (`003_funding_path.sql`) — new columns + `staging_investor` table
2. `prompts/funding_hc_extraction.md` v0.1 — write and validate on test corpus
3. `stages/funding_hc_extract.py` — mirrors `high_confidence_extract.py` structure
4. `run.py` — add branch on `v2_event_type` to route to Stage 4b
5. `stages/aggregate.py` — extend `_FIELDS` and investor aggregation
6. `adapters/sec_api.py` — extend to trigger on Form D for funding events
7. `prompts/deal_summary.md` v0.10 — add funding framing block
8. `docs/prompt_versions.md` — update with new prompt

---

## 13. SEC Enrichment for Funding Events — Form D

Form D is the SEC filing for exempt securities offerings under Regulation D.
Companies file it when raising private capital without SEC registration. It is
the T1 source for funding events — the equivalent of 8-K Item 1.01 for M&A.

**sec-api.io support:** Full coverage via `Form D - Private Placements & Exempt
Offerings` API (`/docs/form-d-xml-json-api`). Same real-time query pattern as
the existing 8-K adapter — query by CIK + date range, different form type.
No new adapter needed; extend `adapters/sec_api.py`.

**Coverage:** All Form D and D/A filings since September 2008. Updated daily.
~13,000–18,000 records per month currently.

**What Form D provides (from `primary_doc.xml`):**

| XML Field | Pipeline Field | Notes |
|---|---|---|
| `<dateOfFirstSale>` | `closed_date` | T1 — authoritative close date; may be "yet to occur" |
| `<totalOfferingAmount>` | `round_size` | T1 — total round; may be "Indefinite" for open funds |
| `<totalAmountSold>` | `round_size` | Use when < totalOfferingAmount (partial close) |
| `<isEquityType>` | Confirms `VC_ROUND` / `GROWTH_EQUITY` | Boolean |
| `<isDebtType>` | Confirms `VENTURE_DEBT` | Boolean |
| `<isOtherType>` + `<descriptionOfOtherType>` | SAFE, convertible note detection | "Simple Agreement for Future Equity (SAFE)" |
| `<revenueRange>` | Company size context | Buckets: no revenues / $1-$1M / $1M-$5M etc. |
| `<industryGroupType>` | Sector context | "Technology", "Commercial", "Pooled Investment Fund" etc. |
| `<totalNumberAlreadyInvested>` | Investor count | Integer count only — no names |
| `<federalExemptionsExclusions>` | `06b` / `06c` / `04` | 06c = general solicitation permitted |

**What Form D does NOT provide:**
- Investor names (`relatedPersonsList` has officers/directors, not investors)
- Pre/post-money valuation
- Round label (Series A, Seed, etc.)

Press release remains essential for investor identification and round context.
Form D corroborates on amount, date, and security type.

**SAFE detection:** When `<isOtherType>` is true and `<descriptionOfOtherType>`
contains "SAFE" or "Simple Agreement for Future Equity", classify as `VC_ROUND`
(SAFEs are a common seed-stage equity instrument, not debt).

**Form D/A (amendments):** D/A filings are complete restatements — the entire
form is re-filed with updated values. The `<previousAccessionNumber>` element
links each amendment to the prior filing. Pipeline should check for D/A
amendments in the same date window and prefer the most recent.

**Adapter extension needed:**

The existing SEC adapter triggers on 8-K Item 1.01 for M&A events. For funding
events it should additionally trigger on Form D filings:

```python
# In adapters/sec_api.py — add to trigger conditions
FORM_D_TRIGGER_EVENT_TYPES = frozenset({
    "VC_ROUND", "GROWTH_EQUITY", "VENTURE_DEBT",
})

# Query pattern (same as 8-K but different form type)
# form_type: "D" or "D" OR "D/A"
# cik: company CIK
# date_range: announced_date - SEC_LOOKBACK_DAYS to announced_date + SEC_LOOKAHEAD_DAYS
```

**Timing:** Form D must be filed within 15 calendar days of first sale. May lag
the press release. Set `SEC_LOOKBACK_DAYS=30` (already the current default)
for funding events — same setting as M&A lookback.

**Tier:** Form D is T1 — wins over T2 press release values in aggregation
conflicts on `closed_date` and `round_size`.


| Decision | Choice | Rationale |
|---|---|---|
| Router vs extended classifier | Extended classifier (Option B) | Classifier already outputs VC_ROUND/GROWTH_EQUITY/VENTURE_DEBT; orchestrator branches on output |
| Shared vs separate extraction prompt | Separate `funding_hc_extraction.md` | Party shape, financial fields, and round metadata too different for shared prompt |
| Investor storage | `staging_investor` table (Option C) | Mirrors `advisor` table pattern; normalizes array naturally; maps cleanly to V2 `transaction_party` |
| `MINORITY_INVESTMENT` path | M&A path for now | Deferred — corporate rounds have M&A-like control provisions |
| Multiples | Skip for funding events | EV multiples not applicable; PMV/Revenue multiples require NTM data not reliably extractable |
