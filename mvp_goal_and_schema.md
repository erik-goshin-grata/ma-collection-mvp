# M&A Collection MVP — Goal Doc & Schema

**Version:** 0.1 (draft)
**Scope:** End-to-end collection pipeline for 100 M&A transactions from PR Newswire + sec-api.io, with LLM-driven extraction, dedup, and AI-generated summary/rationale outputs.

---

## 1. Purpose

Build a working end-to-end M&A collection pipeline that ingests announcements from PR Newswire and public-party detail from sec-api.io, extracts structured data via prompted LLM calls, dedupes across sources, and produces reviewable transaction records with summaries and strategic rationale tags. MVP validates the architecture on 100 transactions before any scaling consideration.

Not production. Not a platform. A working proof loop that a single operator can run on a laptop, inspect end-to-end, and measure against a gold set.

---

## 2. Scope

### In Scope

- **Discovery**
  - PR Newswire M&A / Acquisitions category scrape (100 PRs, paginated)
  - sec-api.io 8-K Item 1.01 lookup for deals where a public party is detected in the PR
- **Pre-filtering**
  - Title-level relevancy (keyword + Haiku classifier)
  - Body-level relevancy on passing titles
- **Extraction**
  - High-confidence pass: parties, announced/closed dates, deal type, consideration (value + value_type), target financials (deal_disclosed only)
  - Low-confidence pass: advisors, characteristic flags, payment composition
- **Entity resolution**
  - Rules-based name + domain matching
  - No provisional entities; SpinCo/business-unit handling deferred
- **Aggregation**
  - Multi-source reconciliation, source-tier weighted (T1 > T2 > T3)
- **Generative outputs**
  - Deal summary (per transaction, regenerable)
  - Strategic rationale classification (single-label against 8-category taxonomy)
- **Dedup**
  - Fuzzy name match + date window clustering across sources
- **Storage**
  - SQLite-backed operational database
  - All raw HTML and clean text preserved for audit and re-runs
- **Evaluation**
  - Gold-set scoring script against defined accuracy targets
- **Prompt versioning**
  - Flat-file version tracking (git-based) with prompt hash in the DB

### Out of Scope (v2+)

- Prompt version registry tool (beyond flat-file + hash)
- Multi-tier human review routing
- Target financial hydration via dedicated SEC XBRL pass
- SpinCo / business-unit provisional entity handling
- Non-US sources, EU-specific extraction variants
- RSS feeds beyond the PR Newswire M&A category
- Production error monitoring, retry queues, worker pools, cloud infra
- Amendment (8-K/A) handling and record supersession
- Deal lifecycle updates (termination, completion, regulatory events)

---

## 3. Acceptance Criteria

MVP is considered validated when, on a 100-PR run:

| Metric | Target |
| :--- | :--- |
| Parties correctly identified (target + acquirer) | > 95% |
| Deal type correctly classified | > 90% |
| Announced date captured | > 98% |
| Value + value_type correct where disclosed | > 90% |
| Dedup: one record per real deal | > 95% |
| Strategic rationale tag reasonable (spot check) | Qualitative pass |
| End-to-end runtime (fresh run, 100 PRs, laptop) | < 2 hours |

Gold set for scoring = manually verified labels on the 100-PR cohort, built during or after the first full run.

---

## 4. Volume & Runtime

- **Initial run:** 100 PRs from PR Newswire M&A / Acquisitions category (`https://www.prnewswire.com/news-releases/financial-services-latest-news/acquisitions-mergers-and-takeovers-list/`)
- **Expected public-party subset triggering sec-api.io:** 15-20 deals
- **Runtime environment:** local laptop, Python 3.11+, SQLite
- **Model routing:**
  - Claude Opus 4.5 — extraction, deal type classification, summary, strategic rationale
  - Claude Haiku 4.5 — relevancy filter (title + body passes)
- **No containers, no cloud, no worker queue in MVP.**

---

## 5. Data Model

### 5.1 Design Principles

1. **Raw before derived.** Every extracted field traces to a stored source document.
2. **Source provenance on everything.** `source_id`, `source_tier`, `extracted_by_prompt_version` stamped on every extraction.
3. **Dedup is reversible.** Staging rows retain independent IDs; `transaction_id` assigned at cluster commit.
4. **Period-dated financials.** Every metric carries `period_type` + `period_end`. UNKNOWN is a valid value; guessing is not.
5. **Field search status.** Distinguishes searched-not-disclosed from searched-not-found from not-yet-searched from NULL.
6. **Idempotent pipeline.** Status columns on every row support resume without re-scraping or re-extracting.
7. **One question per field.** No multi-purpose fields. Deal classification and deal motivation live in separate tables.

### 5.2 Source Tier Mapping

| Source Type | Tier | Rationale |
| :--- | :--- | :--- |
| `SEC_EXHIBIT_21` (merger agreement) | T1 | Definitive legal document |
| `SEC_8K_ITEM_101` | T1 | Filed regulatory disclosure |
| `PR_NEWSWIRE` | T2 | Company-issued release |
| `COMPANY_IR` (v2) | T2 | Company-issued release |
| `NEWS_ARTICLE` (v2) | T3 | Third-party reporting |

MVP only writes T1 and T2 rows. T3 is structural placeholder.

### 5.3 Enumerations

**deal_type**
`ACQUISITION`, `MERGER`, `CARVE_OUT`, `ASSET_SALE`, `SPIN_OFF`, `TAKE_PRIVATE`, `REVERSE_MERGER`, `JV`, `MINORITY_INVESTMENT`, `UNKNOWN`

**target_type** (schema v0.3+)
`STANDALONE_COMPANY`, `BUSINESS_UNIT`, `SUBSIDIARY`, `ASSETS`

Note: `ASSETS` added in Drop 3.9 for discrete asset purchases (product lines, physical asset portfolios, contracts) that are not going-concern units. `is_divestiture` derivation includes `ASSETS` alongside `BUSINESS_UNIT` and `SUBSIDIARY`.

**event_type** (source observation kind — what type of PR is this?)
`ANNOUNCEMENT` (first public announcement), `CLOSE` (separate later completion release), `AMENDMENT` (changes to previously-announced deal), `TERMINATION` (deal will not close), `RUMOR` (pre-announcement).

Note: event_type does NOT describe deal lifecycle status. A same-day announce-and-close PR has event_type=ANNOUNCEMENT, not CLOSE. CLOSE is reserved for a separate later release explicitly referencing a previously-announced deal.

**transaction_status** (deal lifecycle state — derived, not extracted)
`PENDING` (announced, not yet closed), `CLOSED` (deal completed), `TERMINATED` (deal will not close), `RUMORED` (informal pre-announcement signal), `UNKNOWN`. Derived in aggregate.py from event_type + closed_date.

Same-day announce-and-close pattern (most common in private M&A): event_type=ANNOUNCEMENT, closed_date=announced_date, transaction_status=CLOSED. The PR is announcement-type but the deal is closed at the same time.

**deal_status** (legacy; superseded by transaction_status in schema v0.5+)
`ANNOUNCED`, `PENDING`, `COMPLETED`, `TERMINATED`, `WITHDRAWN`, `UNKNOWN`

**value_type**
`ENTERPRISE_VALUE`, `EQUITY_VALUE`, `TOTAL_TRANSACTION_VALUE`, `UNKNOWN`

**period_type**
`LTM`, `FY`, `CQ`, `NTM`, `UNKNOWN`

**security_type** (consideration)
`CASH`, `ACQUIRER_STOCK`, `TARGET_STOCK`, `EARNOUT`, `CVR`, `DEBT_ASSUMED`, `RETAINED_EQUITY`, `OTHER`

**target_status** / **acquirer_status**
`PUBLIC`, `PRIVATE`, `SUBSIDIARY`, `PE_PORTFOLIO`, `GOVERNMENT`, `NON_PROFIT`, `UNKNOWN`

**field_search_status**
`FOUND`, `SEARCHED_NOT_DISCLOSED`, `SEARCHED_NOT_FOUND`, `NOT_SEARCHED`

**metric** (target_financials)
`REVENUE`, `EBITDA`, `NET_INCOME`, `GROSS_PROFIT`, `OPERATING_INCOME`, `TOTAL_ASSETS`, `OTHER`

**source_status**
`FETCHED`, `FILTERED_TITLE`, `FILTERED_BODY`, `PASSED`, `EXTRACTED`, `AGGREGATED`, `FAILED`

**strategic_rationale_category**
`SCALE_CONSOLIDATION`, `GEOGRAPHIC_EXPANSION`, `PRODUCT_OR_TECH_CAPABILITY`, `VERTICAL_INTEGRATION`, `MARKET_DIVERSIFICATION`, `TALENT_ACQUISITION`, `FINANCIAL_OR_ARBITRAGE`, `OTHER`

---

## 6. Schema DDL (SQLite)

### 6.1 `source_raw` — every fetched document

```sql
CREATE TABLE source_raw (
    source_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT NOT NULL,              -- enum: PR_NEWSWIRE, SEC_8K_ITEM_101, SEC_EXHIBIT_21, COMPANY_IR, NEWS_ARTICLE
    source_tier     TEXT NOT NULL,              -- T1, T2, T3 (derived from source_type)
    url             TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,              -- ISO 8601
    raw_html        TEXT,                       -- nullable; stored for audit
    clean_text      TEXT,                       -- trafilatura output or Extractor API output
    content_hash    TEXT NOT NULL,              -- SHA256 over clean_text; dedup key at source level
    published_date  TEXT,                       -- from listing metadata (PR Newswire date or filedAt)
    title           TEXT,
    source_status   TEXT NOT NULL DEFAULT 'FETCHED',
    filter_reason   TEXT,                       -- why it was filtered, if applicable
    notes           TEXT,
    UNIQUE (content_hash)
);

CREATE INDEX idx_source_raw_status ON source_raw(source_status);
CREATE INDEX idx_source_raw_type ON source_raw(source_type);
CREATE INDEX idx_source_raw_date ON source_raw(published_date);
```

### 6.2 `staging_extraction` — one row per LLM extraction pass

```sql
CREATE TABLE staging_extraction (
    extraction_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id               INTEGER NOT NULL REFERENCES source_raw(source_id),
    prompt_name             TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,      -- hash or semver tag; joins to prompt_versions
    model_id                TEXT NOT NULL,      -- e.g., claude-opus-4-5
    extracted_at            TEXT NOT NULL,
    raw_output_json         TEXT NOT NULL,      -- full LLM response as JSON string
    parsed_target_name      TEXT,
    parsed_target_domain    TEXT,
    parsed_acquirer_name    TEXT,
    parsed_acquirer_domain  TEXT,
    parsed_announced_date   TEXT,
    parsed_deal_type        TEXT,
    cluster_id              TEXT,               -- assigned during dedup; nullable until then
    transaction_id          INTEGER,            -- FK to transaction; nullable until cluster commit
    status                  TEXT NOT NULL DEFAULT 'PARSED',  -- PARSED, CLUSTERED, COMMITTED, ORPHANED, FAILED
    notes                   TEXT
);

CREATE INDEX idx_stg_ext_source ON staging_extraction(source_id);
CREATE INDEX idx_stg_ext_txid ON staging_extraction(transaction_id);
CREATE INDEX idx_stg_ext_cluster ON staging_extraction(cluster_id);
```

### 6.3 `transaction` — one row per deduped deal

```sql
CREATE TABLE transaction (
    transaction_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_target    TEXT NOT NULL,
    target_domain       TEXT,
    canonical_acquirer  TEXT NOT NULL,
    acquirer_domain     TEXT,
    announced_date      TEXT NOT NULL,
    closed_date         TEXT,
    deal_type           TEXT NOT NULL,          -- enum deal_type
    deal_status         TEXT NOT NULL DEFAULT 'ANNOUNCED',
    target_status       TEXT,                   -- enum target_status
    acquirer_status     TEXT,                   -- enum acquirer_status
    created_at          TEXT NOT NULL,
    last_updated_at     TEXT NOT NULL
);

CREATE INDEX idx_tx_announced ON transaction(announced_date);
CREATE INDEX idx_tx_target ON transaction(canonical_target);
CREATE INDEX idx_tx_acquirer ON transaction(canonical_acquirer);
```

### 6.4 `transaction_source` — many-to-many transaction ↔ raw source

```sql
CREATE TABLE transaction_source (
    transaction_id  INTEGER NOT NULL REFERENCES transaction(transaction_id),
    source_id       INTEGER NOT NULL REFERENCES source_raw(source_id),
    extraction_id   INTEGER NOT NULL REFERENCES staging_extraction(extraction_id),
    linked_at       TEXT NOT NULL,
    PRIMARY KEY (transaction_id, source_id)
);
```

### 6.5 `consideration` — securities-level rows

```sql
CREATE TABLE consideration (
    consideration_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      INTEGER NOT NULL REFERENCES transaction(transaction_id),
    security_type       TEXT NOT NULL,          -- enum security_type
    amount              REAL,                   -- in currency units (not per share)
    currency            TEXT NOT NULL DEFAULT 'USD',
    per_share           REAL,
    exchange_ratio      REAL,
    note                TEXT,
    source_id           INTEGER NOT NULL REFERENCES source_raw(source_id),
    field_search_status TEXT NOT NULL DEFAULT 'FOUND'
);

CREATE INDEX idx_cons_tx ON consideration(transaction_id);
```

### 6.6 `valuation` — EV / equity / TTV with type stamped

```sql
CREATE TABLE valuation (
    valuation_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      INTEGER NOT NULL REFERENCES transaction(transaction_id),
    value_type          TEXT NOT NULL,          -- enum value_type
    amount              REAL,
    currency            TEXT NOT NULL DEFAULT 'USD',
    per_share           REAL,
    is_disclosed        INTEGER NOT NULL DEFAULT 0,  -- bool
    field_search_status TEXT NOT NULL,          -- enum field_search_status
    source_id           INTEGER REFERENCES source_raw(source_id)
);

CREATE INDEX idx_val_tx ON valuation(transaction_id);
```

### 6.7 `target_financials` — metrics with period dating

```sql
CREATE TABLE target_financials (
    financial_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id      INTEGER NOT NULL REFERENCES transaction(transaction_id),
    metric              TEXT NOT NULL,          -- enum metric
    amount              REAL,
    currency            TEXT NOT NULL DEFAULT 'USD',
    period_type         TEXT NOT NULL,          -- enum period_type
    period_end          TEXT,                   -- ISO date; nullable when source is imprecise
    provenance          TEXT NOT NULL,          -- DEAL_DISCLOSED in v1; TARGET_REPORTED in v2
    field_search_status TEXT NOT NULL,
    source_id           INTEGER REFERENCES source_raw(source_id)
);

CREATE INDEX idx_fin_tx ON target_financials(transaction_id);
CREATE INDEX idx_fin_metric ON target_financials(transaction_id, metric);
```

### 6.8 `advisor` — deal advisors

```sql
CREATE TABLE advisor (
    advisor_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transaction(transaction_id),
    advisor_name    TEXT NOT NULL,
    advisor_type    TEXT NOT NULL,              -- FINANCIAL, LEGAL, OTHER
    represents      TEXT NOT NULL,              -- TARGET, ACQUIRER, BOTH, UNKNOWN
    source_id       INTEGER REFERENCES source_raw(source_id)
);

CREATE INDEX idx_adv_tx ON advisor(transaction_id);
```

### 6.9 `deal_characteristic` — feature flags on the deal

```sql
CREATE TABLE deal_characteristic (
    transaction_id          INTEGER PRIMARY KEY REFERENCES transaction(transaction_id),
    is_take_private         INTEGER,            -- bool, nullable
    is_carve_out            INTEGER,
    is_spin_off             INTEGER,
    is_reverse_merger       INTEGER,
    is_hostile              INTEGER,
    is_all_cash             INTEGER,
    is_all_stock            INTEGER,
    is_mixed_consideration  INTEGER,
    has_earnout             INTEGER,
    has_retained_equity     INTEGER,
    notes                   TEXT
);
```

### 6.10 `summary` — AI-generated deal summary

```sql
CREATE TABLE summary (
    summary_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transaction(transaction_id),
    summary_text    TEXT NOT NULL,
    word_count      INTEGER NOT NULL,
    model_id        TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    is_current      INTEGER NOT NULL DEFAULT 1  -- 0 when regenerated
);

CREATE INDEX idx_sum_tx ON summary(transaction_id);
CREATE INDEX idx_sum_current ON summary(transaction_id, is_current);
```

### 6.11 `rationale_tag` — strategic rationale

```sql
CREATE TABLE rationale_tag (
    rationale_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL REFERENCES transaction(transaction_id),
    category        TEXT NOT NULL,              -- enum strategic_rationale_category
    confidence      REAL,                       -- 0.0 - 1.0
    model_id        TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    is_current      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX idx_rat_tx ON rationale_tag(transaction_id);
```

### 6.12 `prompt_version` — lightweight registry

```sql
CREATE TABLE prompt_version (
    prompt_version_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_name         TEXT NOT NULL,          -- e.g., 'high_confidence_extraction'
    version             TEXT NOT NULL,          -- semver or iso timestamp tag
    text_hash           TEXT NOT NULL,          -- SHA256 over prompt text
    file_path           TEXT,                   -- relative path in repo
    created_at          TEXT NOT NULL,
    notes               TEXT,
    UNIQUE (prompt_name, version)
);
```

---

## 7. Pipeline Flow (Reference)

```
                  ┌─────────────────────┐
                  │  PR Newswire scrape │
                  │   (100 listings)    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  source_raw INSERT  │ ← PR Newswire
                  │  status=FETCHED     │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Title relevancy    │  Haiku
                  │  (filter or pass)   │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Body relevancy     │  Haiku
                  │  (filter or pass)   │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Deal type classify │  Opus
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  High-conf extract  │  Opus
                  │  Low-conf extract   │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Public party?      │───YES──► sec-api.io
                  │                     │          (Query + Extractor
                  └──────────┬──────────┘           + Exhibit 2.1)
                             │ NO                   │
                             │                      │
                             ▼                      ▼
                  ┌────────────────────────────────────┐
                  │  Dedup / Cluster                   │
                  │  (name+date window, fuzzy match)   │
                  └──────────┬─────────────────────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Aggregation        │  source-tier weighted
                  │  (commit transaction)│
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Summary            │  Opus
                  │  Rationale          │  Opus
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  Eval against gold  │
                  └─────────────────────┘
```

---

## 8. Post-First-Run Checkpoint: Raw T1 Source Review

Before any v2 scope decision on securities-level consideration extraction (per-security capture from Exhibit 2.1, acquirer share price at announcement, calculated equity value), the operator performs a manual review of the raw SEC source texts captured during the first 100-PR run.

**Purpose.** Ground the v2 decision in evidence rather than design assumptions. The questions to answer: for the subset of MVP deals that triggered SEC enrichment, what fraction have extractable securities-level detail in the Exhibit 2.1 text? Is the per-security model actually achievable from these texts, or do most exhibits lack the structured tables the schema assumes?

**Mechanics.**

1. After first run completes, query the database:
   ```sql
   SELECT source_raw_id, source_type, title, length(clean_text) AS char_count
   FROM source_raw
   WHERE source_type IN ('SEC_8K_ITEM_101', 'SEC_EXHIBIT_21')
   ORDER BY source_type, length(clean_text) DESC;
   ```

2. Dump 5–10 Exhibit 2.1 texts to individual files for review:
   ```sql
   SELECT clean_text FROM source_raw WHERE source_raw_id = <id>;
   ```

3. Review each exhibit for:
   - Presence of a clear consideration table (security type × shares × price × exchange ratio).
   - Presence of acquirer stock pricing language (if stock consideration is involved).
   - Structural consistency across exhibits (are the fields we'd want to extract in similar places across filings?).

4. Document findings in a brief markdown note (`/notes/t1_source_review_<date>.md`) with:
   - Total exhibits reviewed
   - Count with extractable securities tables
   - Count with acquirer pricing language
   - Examples of ambiguous or non-standard formats
   - Recommendation on v2 scope feasibility

**Outcome.** The review informs whether v2 securities extraction is:
- **Feasible as designed** — proceed with schema-aligned per-security extraction.
- **Feasible with modifications** — adjust schema or prompt approach based on what real exhibits look like.
- **Deferred** — T1 source quality doesn't support the per-security model; continue with as-reported deal value as the canonical extraction.

This review is a prerequisite for any v2 securities extraction scoping conversation.

---

## 9. Open Items Before Prompt Design

1. **PR Newswire robots.txt posture.** Confirm acceptable scrape rate and that the M&A category page is not disallowed. Adjust throttle accordingly.
2. **sec-api.io rate limit on $55 tier.** Needed to set parallelism ceiling on the SEC adapter. Pull from account dashboard.
3. **Gold set labeling.** Who labels the 100-deal gold set — you, or a second pass by Claude with manual review? Matters for eval methodology.
4. **Currency handling.** MVP assumes USD default. Any non-US deals in the 100-PR sample get flagged but not converted. Confirm acceptable.
5. **Closed date discovery.** MVP captures closed_date only if stated in the announcement. No lifecycle monitoring to update it later. Confirm acceptable.

---

## 10. Document Control

**New fields (Drop 3.9 / schema v0.3):**
- `pct_acquired REAL` — percentage of target acquired when explicitly stated; NULL for implicit 100% (on `staging_extraction` and `transaction_record`)
- `target_description TEXT`, `acquirer_description TEXT`, `parent_seller_description TEXT` — concise 1-sentence party descriptions from "About" boilerplate (on both tables)

**New fields (Drop 3.10 / schema v0.4):**
- `acquirer_sponsor_name TEXT` — PE sponsor(s) backing the acquirer; comma-delimited when multiple (e.g. "New State Capital Partners, Amethyst Capital Group"). NULL when acquirer is not PE-backed or sponsor not stated. Written by HC extraction and passed through aggregate. (on `staging_extraction` and `transaction_record`)

**Valuation multiples (Drop 3.12, derived at aggregation — `transaction_record` only):**
- `ev_to_revenue_ltm`, `ev_to_revenue_ntm`, `ev_to_ebitda_ltm`, `ev_to_ebitda_ntm` — EV/Revenue and EV/EBITDA multiples for LTM/NTM periods. TTM is treated as LTM.
- `multiple_quality` — `CALCULATED` | `NM` | `NOT_CALCULABLE`
- Computed only when `value_type = ENTERPRISE_VALUE` and the corresponding financial metric is present and positive. Plausible ranges: EV/Revenue 0.1x–50x, EV/EBITDA 1x–100x.
- NM display rule: out-of-range multiples display as `NM` in CSV export, but the computed value is preserved in the DB for inspection.
- Currency mismatch (e.g. USD EV vs EUR EBITDA) flags NM without conversion; FX conversion is a v2 enhancement.
- Equity multiples (P/E, P/B, P/TBV) deferred — require additional extraction fields (net_income, book_value).

**New fields (Drop 3.14/3.15/3.17 / schema v0.5):**
- `transaction_status TEXT` — deal lifecycle state (`PENDING | CLOSED | TERMINATED | RUMORED | UNKNOWN`). Derived at aggregation from event_type + closed_date. Distinct from event_type (which is the source PR kind). (`transaction_record` only)
- `is_de_spac INTEGER` — 1 when deal_type=REVERSE_MERGER AND acquirer_type=SPAC. (`transaction_record` only)
- event_type semantics revised (HC extraction v0.8): same-day announce-and-close PRs now correctly produce event_type=ANNOUNCEMENT + closed_date populated (previously mis-tagged as event_type=CLOSE in v0.4–0.7).

**Earnout and CVR consideration (Drop 3.16 / schema v0.6):**
- `has_earnout INTEGER` — 1 when consideration_components contains a EARNOUT-form entry. Derived at aggregation. (`transaction_record` only)
- `has_cvr INTEGER` — 1 when consideration_components contains a CVR-form entry. Derived at aggregation. (`transaction_record` only)
- These flags are derived from consideration_components JSON at aggregation time; they are NOT extracted by the LC prompt directly.
- Earnouts and CVRs are ADDITIVE to primary consideration; they do not change `consideration_type`. A cash + earnout deal stays `consideration_type=CASH` with `has_earnout=1`.
- Earnout component shape: `form=EARNOUT`, `amount`, `percentage`, `description`
- CVR component shape: `form=CVR`, `amount`, `percentage`, `description`

**Multi-transaction PR splitting (Drop 3.18 / schema v0.7):**
- `multi_transaction_index INTEGER` — 0-indexed position within the set of transactions extracted from a single source PR. 0 for single-transaction PRs (the common case). (`staging_extraction` only)
- `multi_transaction_total INTEGER` — total number of transactions extracted from a given source PR. 1 for single-transaction PRs. (`staging_extraction` only)
- HC extraction prompt v0.9: response shape changed from a single top-level object to `{"transactions": [...], "prompt_version": "..."}`. The `transactions` array always contains at least one element. Single-transaction PRs produce a 1-element array.
- Pipeline behavior: for a multi-transaction response, `transactions[0]` UPDATEs the original `staging_extraction` row (sets `multi_transaction_index=0`); `transactions[1+]` each INSERT a new row carrying Stage 3 classification fields (`deal_type`, `spin_split_type`, `distribution_mechanism`, `target_type`, `event_type`, `target_status`, `dt_prompt_version`) from the original row. All resulting rows share the same `source_raw_id`.
- Downstream stages (LC extraction, entity clustering, aggregation, summarize, rationale) are unchanged — they operate on `HC_EXTRACTED` rows regardless of `multi_transaction_index`.
- Splitting criterion: only when the PR explicitly announces distinct deals (e.g. simultaneous acquisition of two separate named companies). Do NOT split for a single target described in multiple paragraphs or with multiple consideration tranches.

**SEC filing expansion — transaction_document and section tagging (Drop 3.19 / schema v0.8):**
- `transaction_document` — new table storing full-text SEC filings linked to a transaction by `transaction_id`. Filing types: `8K_ITEM_201`, `8K_EXHIBIT_21`, `DEFM14A`, `SC_TOT`, `S4`, `OTHER`. Includes `raw_text`, `sec_accession_number`, `filer_cik`, `filer_name`, `filing_date`, `raw_text_length`, `fetch_timestamp`, `is_current`. UNIQUE constraint on `(transaction_id, sec_accession_number, filing_type)`.
- `transaction_document_section` — new table storing heuristic section excerpts from documents in `transaction_document`. `section_type` enum: `DEFINITIONS`, `RECITALS`, `CONSIDERATION`, `CAPITALIZATION`, `CONDITIONS_TO_CLOSING`, `TERMINATION_FEES`, `REPRESENTATIONS`, `BACKGROUND_OF_MERGER`, `FAIRNESS_OPINION`. Stores bounded `excerpt_text`, char offsets, and `confidence` (HIGH | MEDIUM | LOW).
- `linked_filings_count INTEGER DEFAULT 0` — new column on `transaction_record`. Count of linked `transaction_document` rows. Set by `sec_documents` stage after processing; reset to 0 on re-aggregation, then re-set by sec_documents stage on next run.
- New Stage 10 (`sec_documents`): runs after aggregate (Stage 9). For transactions with public-party SEC trigger signals, fetches 8-K Item 2.01, 8-K Exhibit 2.1, DEFM14A, SC TO-T, and S-4 filings. Stores full text in `transaction_document`. Runs heuristic section tagger (`lib/section_tagger.py`) and stores results in `transaction_document_section`. Updates `linked_filings_count` on `transaction_record`. Idempotent: skips `(transaction_id, filing_type)` pairs already present.
- Existing stages 10-12 renumbered to 11-13 (summarize, rationale_tag, export). New mode: `--mode=sec-documents` runs Stage 10 only.
- `pdfminer.six` added to requirements.txt for PDF exhibit extraction. Graceful degradation if not installed: PDFs marked UNREADABLE.
- Section detection: pure Python regex, no LLM. Headings detected by all-caps lines, ARTICLE [Roman], Section X.Y, numbered prefixes. HIGH confidence = pattern matches heading text; MEDIUM = pattern matches first 200 chars of section body.

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft for review |
| 0.6 | 2026-05-02 | Drop 3.16: has_earnout, has_cvr flags; EARNOUT/CVR guidance in LC extraction prompt v0.4. |
| 0.7 | 2026-05-02 | Drop 3.18: multi_transaction_index/total columns in staging_extraction; HC extraction v0.9 returns transactions array. |
| 0.8 | 2026-05-02 | Drop 3.19: transaction_document and transaction_document_section tables; linked_filings_count on transaction_record; sec_documents stage (Stage 10); pdfminer.six dependency. |
| 0.9 | 2026-05-04 | Drop 3.20a: transaction_security table; 9 new columns on transaction_record (agreement extraction fields); document_title on transaction_document; agreement_extract stage (Stage 11); 5 agreement extraction prompts; SEC window tightened to 0 to +180 days. |
| 1.0 | 2026-05-04 | Drop 3.20b: transaction_field_observation table; 3 new columns on transaction_record (has_observation_changes, observation_changes_field_count, observation_changes_summary); observation writing and diff surfacing in agreement_extract stage; per-field source-type priority rules for canonical value selection. |

**Agreement extraction fields — Drop 3.20a / schema v0.9 (transaction_record):**
- `acquirer_merger_sub_name TEXT` — name of the Merger Sub / acquisition vehicle in 3-party structures; null for direct mergers. Set by agreement_recitals extraction.
- `merger_structure TEXT` — DIRECT | FORWARD_TRIANGULAR | REVERSE_TRIANGULAR | TENDER_OFFER | UNKNOWN. Set by agreement_recitals extraction.
- `has_mac_clause INTEGER DEFAULT 0` — 1 when closing conditions section contains a Material Adverse Change/Effect condition.
- `requires_target_shareholder_vote INTEGER` — 0 | 1 | null (null until agreement_extract runs). Whether target shareholder approval is a closing condition.
- `target_vote_threshold TEXT` — MAJORITY_OUTSTANDING | TWO_THIRDS | MAJORITY_VOTING | OTHER | null. The required shareholder approval threshold.
- `closing_conditions_summary TEXT` — brief plain-text summary of top closing conditions. Not exported to CSV (long text); accessible via DB.
- `target_total_diluted_shares INTEGER` — sum of all security types from transaction_security rows for this transaction. Computed by agreement_extract.
- `fully_diluted_calc_quality TEXT` — COMPLETE (common + dilutive securities populated) | PARTIAL (common only) | NOT_AVAILABLE (no securities data).
- `agreement_extraction_status TEXT` — NOT_TRIGGERED | EXTRACTED | NO_AGREEMENT_LINKED | EXTRACTION_FAILED. Set by agreement_extract stage.

**transaction_security table — Drop 3.20a / schema v0.9:**
One row per (transaction, security_type, security_class) per source document. Multiple rows per security type expected when multiple source documents disclose the same security (e.g., both 8K_EXHIBIT_21 and DEFM14A). All rows preserved; most-recent-source-wins logic applied at query time or by agreement_extract rollup.

Key columns: `transaction_id`, `security_type` (COMMON_STOCK | PREFERRED_STOCK | OPTIONS | RSU | PSU | DSU | SAR | WARRANT | CONVERTIBLE_NOTE | OTHER), `security_type_as_reported` (verbatim from source), `security_class` (Class A / Series C / null), `shares_outstanding`, `shares_outstanding_as_of` (YYYY-MM-DD), `weighted_avg_strike_price`, `consideration_treatment` (CASH_OUT | CONVERSION | ASSUMED | CANCELLED | ROLLOVER | OTHER), `consideration_per_share`, `consideration_currency`, `extraction_source_section_id` (FK to transaction_document_section), `extraction_source_document_id` (FK to transaction_document).

**document_title — Drop 3.20a / schema v0.9 (transaction_document):**
- `document_title TEXT` — document's self-declared title extracted from first ~1500 chars by `extract_document_title()` in adapters/sec_api.py. Uses all-caps test and Title-Case + keyword test. Null when not identifiable.

**SEC window tightening — Drop 3.20a:**
- `query_filings_by_formtype()` date window changed from `announced_date ± 90 days` to `announced_date` through `announced_date + 180 days`. Pre-announcement filings are noise; 180-day post-announcement window covers DEFM14A (+30-60d), S-4 (+45-90d), and 8-K Item 2.01 at typical close timing.

**Cross-source observation tracking and diff surfacing — Drop 3.20b / schema v1.0:**

`transaction_field_observation` table — one row per (transaction, field, source document). Every scalar value extracted by any of the 5 agreement-section prompts is written here with full source attribution (`source_document_id`, `source_section_id`, `filing_date`, `extraction_prompt_version`). Arrays (consideration_components, securities) are written as compound field names:
- Share counts: `shares_outstanding.{security_type}[.{security_class}]` (e.g., `shares_outstanding.COMMON_STOCK.Class A`)
- Consideration: `consideration.{form}.{attr}` (e.g., `consideration.CASH.per_share_amount`)

New columns on `transaction_record`:
- `has_observation_changes INTEGER DEFAULT 0` — 1 when any tracked field has >1 distinct value across all source documents for this transaction.
- `observation_changes_field_count INTEGER DEFAULT 0` — count of fields with diffs; useful for sorting/filtering.
- `observation_changes_summary TEXT` — JSON array describing each diffed field: values in chronological order with source attribution, change_type (INCREASE | DECREASE | IDENTICAL | DIFFERENT), delta (numeric fields only), delta_pct. Excluded from CSV export (long, structured); accessible via DB query.

**Observation JSON shape example:**
```json
[
  {
    "field": "per_share_price",
    "values": [
      {"value": "42.00", "filing_date": "2026-04-15", "filing_type": "8K_EXHIBIT_21", "document_title": "Agreement and Plan of Merger"},
      {"value": "44.50", "filing_date": "2026-05-20", "filing_type": "DEFA14A", "document_title": "Definitive Additional Materials"}
    ],
    "change_type": "INCREASE",
    "delta": 2.5,
    "delta_pct": 5.95
  }
]
```

**Per-field source-type priority rules (Drop 3.20b):**
For fields where the legal source-of-truth is the original agreement rather than the most-recently-filed document, `agreement_extract` applies priority rules when selecting which observation populates the canonical `transaction_record` column:
- Termination fees, go-shop: `8K_EXHIBIT_21` > `DEFM14A` > `DEFA14A` > `S4`
- MAC clause, shareholder vote: `8K_EXHIBIT_21` > `DEFM14A`
- Merger structure: `8K_EXHIBIT_21` > `DEFM14A` > `S4`
- Per-share price and consideration components: no rule — most-recent-filing-date wins (DEFA14As capture bumps)

For non-priority fields, sections are processed in ASC filing_date order so the most recently filed source wins by last-write semantics.
