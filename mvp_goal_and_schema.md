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

**deal_status**
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

## 8. Open Items Before Prompt Design

1. **PR Newswire robots.txt posture.** Confirm acceptable scrape rate and that the M&A category page is not disallowed. Adjust throttle accordingly.
2. **sec-api.io rate limit on $55 tier.** Needed to set parallelism ceiling on the SEC adapter. Pull from account dashboard.
3. **Gold set labeling.** Who labels the 100-deal gold set — you, or a second pass by Claude with manual review? Matters for eval methodology.
4. **Currency handling.** MVP assumes USD default. Any non-US deals in the 100-PR sample get flagged but not converted. Confirm acceptable.
5. **Closed date discovery.** MVP captures closed_date only if stated in the announcement. No lifecycle monitoring to update it later. Confirm acceptable.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft for review |
