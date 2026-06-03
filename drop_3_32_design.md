# Drop 3.32 Design - Entity Participant Model

Date: 2026-06-03

Status: 3.32a implemented in the current working tree and copied-real-DB
validation passed. Broader participant-model ideas remain design-only until
separately approved.

## Purpose

Design an additive participant model that captures all disclosed organization
participants in multi-party transactions while preserving the current flat
`transaction_record` fields.

The model should prepare the pipeline for broader M&A, Growth Equity, and
Venture Capital coverage by separating:

- the legal or natural entity being named,
- the entity's intrinsic type,
- the role the entity plays in a specific transaction,
- the transaction side or group the participant belongs to,
- source provenance and review metadata on canonical participant records.

This drop should not remove or reinterpret existing `transaction_record` fields.
The flat fields remain the compatibility surface for current aggregation,
summaries, rationale tagging, export, and downstream review.

The first implementation slice is narrower than the full long-term design. The
actual first problem to solve is multi-party organization support:

- multiple buyers;
- multiple sponsors;
- multiple investors;
- investor groups;
- consortiums;
- parent / merger-sub structures.

The first slice should answer one question:

Can we accurately represent all disclosed organizations participating in a
transaction?

Important correction:

The participant model is not the only user-facing shape. Researchers and
product users should not be forced to enter, filter, or export abstract
side/role combinations for familiar deal attributes. Normalization is useful
for capture, audit, provenance, linking, and deduplication; familiar deal
attributes remain useful for manual collection, product filtering, and export.

Design principle:

- Normalize for capture, audit, and intelligence.
- Preserve familiar deal attributes for researcher workflow, product filtering,
  and export.
- Make derived deal attributes available for fast querying, even when the
  underlying participant model is normalized.

## Guiding Principles

1. Capture disclosed parties. Do not collapse subsidiaries, portfolio companies,
   funds, corporate venture arms, or acquisition vehicles into their parents.
2. Prefer transaction-context participant roles over abstract relationship
   types in 3.32a.
3. Consortiums are transaction-side groupings, not synthetic entities.
4. Investor lead status is an attribute of a participant in a transaction, not a
   separate entity.
5. Entity type is different from transaction role. A venture capital firm can be
   an investor in one deal, sponsor in another, and seller in another.
6. Researchers should audit, correct, and enrich. The system should extract
   first, preserve provenance, and mark ambiguous records for review rather than
   silently normalizing away complexity.
7. Keep familiar deal attributes for collection and product use. The normalized
   participant model may ingest, mirror, or derive from those fields, but it
   should not make basic collection or querying harder.

## Collection Model != Storage Model != Product/Export Model

Drop 3.32 should explicitly separate three models that serve different users and
query patterns. They should interoperate, but they should not collapse into one
shape.

This distinction remains important for future product design, but 3.32a does
not implement new collection fields, export fields, filtering surfaces, or
advisor/accountant redesign.

### 1. Collection Model: Researcher / Manual Fields

These are the fields a researcher expects to see and edit directly. They should
use familiar deal-language labels, not ontology terms.

Examples:

- `target_financial_advisor`
- `acquirer_financial_advisor`
- `seller_financial_advisor`
- `target_legal_advisor`
- `acquirer_legal_advisor`
- `seller_legal_advisor`
- `target_accountant`
- `acquirer_accountant`
- `seller_accountant`
- `target_fairness_opinion_provider`
- `proxy_solicitor`
- `information_agent`
- `debt_financing_sources`
- `equity_financing_sources`
- `lead_investor`
- `co_investors`

Collection rules:

- A researcher can enter "Goldman Sachs" in `target_financial_advisor` without
  selecting `side=TARGET`, `participant_role=FINANCIAL_ADVISOR`, and
  `entity_type=INVESTMENT_BANK`.
- The system can mirror that field into normalized participant rows
  deterministically.
- If a field contains multiple firms, preserve the collection value and create
  one normalized participant candidate per firm where parsing is safe.
- Ambiguous splits should be reviewable, not silently over-normalized.

Implementation can store these as explicit columns, a field-value table, or a
researcher-facing view. The important requirement is the user-facing contract:
collection fields remain familiar deal attributes.

### 2. Product/Export Model: Fast Filterable Deal Attributes

These are fields exposed to downstream users, exports, dashboards, and filters.
They can be backed by flat columns, materialized views, or deterministic pivots
from normalized participants, but they should remain easy to query.

Examples:

- filter for deals where `target_financial_advisor` includes "Goldman Sachs";
- export `acquirer_legal_advisor` as a readable column;
- count deals with any `seller_financial_advisor`;
- filter venture rounds with `lead_investor`;
- export `co_investors` without requiring a consumer to understand participant
  groups.

Product rules:

- Do not require product consumers to reconstruct normal M&A attributes from
  participant rows unless the product surface intentionally exposes a graph.
- Keep participant-derived product fields deterministic and testable.
- Store or materialize derived deal attributes for fast querying where filters
  are expected to be common.
- Prefer plural/export-friendly values when a field naturally contains multiple
  parties, even if the field name remains singular for continuity.

### 3. Storage Model: Normalized Participant Storage

This is the audit/linking/provenance layer. It stores entities, participants,
groups, and aliases.

Normalized storage should:

- preserve source provenance;
- support entity linking across transactions;
- represent consortiums and co-investor groups without synthetic entities;
- preserve sponsors, platforms, parents, merger subs, investors, and issuers as
  transaction-context participant roles;
- support researcher review and correction.

Normalized storage should not:

- replace familiar collection fields;
- make common product filters harder;
- force researchers to manually model every field as side plus role plus entity
  type.

Practical rule:

Use normalized participant storage to understand who participated and how. Use
collection/product attributes to make common research and product workflows
fast, familiar, and low-friction.

## Current State After 3.31c

Relevant current structures:

- `transaction_record` stores flat canonical party fields:
  - `target_name`, `target_domain`, `target_ticker`
  - `acquirer_name`, `acquirer_domain`, `acquirer_ticker`,
    `acquirer_type`
  - `parent_seller_name`, `parent_seller_ticker`
  - `acquirer_sponsor_name`
  - `acquirer_merger_sub_name`
- `advisor` stores per-extraction advisor firm rows with
  `name`, `type`, and `advised_party`.
- `transaction_field_observation` now has source-row and agreement observation
  provenance after 3.31b/3.31c.
- Stage 9 can read either `staging_extraction` or
  `transaction_field_observation`, but defaults to
  `AGGREGATION_READ_SOURCE=staging`.

Limitations:

- Flat party fields cannot represent multiple buyers, investors, sponsors,
  investor groups, consortiums, parent-acquirer structures, seller groups, or
  merger-sub structures.
- `acquirer_type` mixes entity type and transaction role.
- `acquirer_sponsor_name` is comma-delimited and cannot express which entity is
  the acquirer, sponsor, parent, fund, or platform company.
- Agreement recitals can disclose Parent, Merger Sub, Company, guarantors, and
  affiliates, but only a subset lands in canonical flat fields.
- Advisors are firm-level only; named bankers, lawyers, partners, and deal leads
  are not modeled.

## Proposed Tables and Columns

The tables below are proposed schema additions. They are illustrative DDL, not
implementation.

The 3.32a patch includes only these schema components:

- `entity`
- `entity_alias`
- `transaction_participant`
- `transaction_participant_group`

Do not add a generic `transaction_deal_participant_attribute` table in 3.32a.
Advisor, accountant, export, and researcher-collection surfaces remain
unchanged in this first slice.

### 1. `entity`

Global organization record. This is not a transaction role table.

```sql
CREATE TABLE entity (
    entity_id              TEXT PRIMARY KEY,
    entity_kind            TEXT NOT NULL DEFAULT 'ORGANIZATION',
    canonical_name         TEXT NOT NULL,
    normalized_name        TEXT NOT NULL,
    legal_name             TEXT,
    display_name           TEXT,

    -- Intrinsic classification, not transaction role.
    entity_type            TEXT,
    entity_subtype         TEXT,

    -- Organization linking attributes.
    domain                 TEXT,
    ticker                 TEXT,
    cik                    TEXT,
    lei                    TEXT,
    country                TEXT,
    region                 TEXT,
    website_url            TEXT,

    review_status          TEXT DEFAULT 'UNREVIEWED',
    reviewed_by            TEXT,
    reviewed_at            TEXT,
    researcher_notes       TEXT,

    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Recommended `entity_kind` values:

- `ORGANIZATION`

Recommended organization `entity_type` values:

- `OPERATING_COMPANY`
- `BUSINESS_UNIT`
- `SUBSIDIARY`
- `ACQUISITION_VEHICLE`
- `CORPORATE_VENTURE_ARM`
- `PRIVATE_EQUITY_FIRM`
- `VENTURE_CAPITAL_FIRM`
- `GROWTH_EQUITY_FIRM`
- `PE_PORTFOLIO_COMPANY`
- `FUND`
- `SPAC`
- `SOVEREIGN_WEALTH_FUND`
- `PENSION_FUND`
- `HEDGE_FUND`
- `FAMILY_OFFICE`
- `GOVERNMENT_ENTITY`
- `OTHER_ORGANIZATION`
- `UNKNOWN`

Notes:

- A corporate venture arm should be its own `entity` with
  `entity_type=CORPORATE_VENTURE_ARM`; do not collapse it into the corporate
  parent in 3.32a.
- A PE-backed platform company should be its own `entity` with
  `entity_type=PE_PORTFOLIO_COMPANY`; the platform and sponsor are represented
  as transaction-context participant roles in 3.32a.
- A fund and its manager may both be entities if both are disclosed.

### 2. `entity_alias`

Source-supported name variants and identifiers.

```sql
CREATE TABLE entity_alias (
    alias_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id              TEXT NOT NULL REFERENCES entity(entity_id),
    alias_name             TEXT NOT NULL,
    normalized_alias       TEXT NOT NULL,
    alias_type             TEXT, -- LEGAL_NAME | DBA | ABBREVIATION | FORMER_NAME | SOURCE_NAME
    source_raw_id          INTEGER,
    source_document_id     INTEGER,
    source_section_id      INTEGER,
    first_observed_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_current             INTEGER DEFAULT 1
);
```

### 3. `transaction_participant_group`

Transaction-side grouping. This is where consortiums live.

```sql
CREATE TABLE transaction_participant_group (
    group_id               TEXT PRIMARY KEY,
    transaction_id         TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    side                   TEXT NOT NULL,
    group_type             TEXT NOT NULL,
    group_label            TEXT,
    disclosed_group_name   TEXT,
    source_raw_id          INTEGER,
    source_document_id     INTEGER,
    source_section_id      INTEGER,
    model_confidence       TEXT,
    review_status          TEXT DEFAULT 'UNREVIEWED',
    notes                  TEXT,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Recommended `side` values:

- `TARGET`
- `BUYER`
- `SELLER`
- `INVESTOR`
- `ISSUER`

Recommended `group_type` values:

- `CONSORTIUM`
- `INVESTOR_GROUP`
- `SELLER_GROUP`

Rules:

- A consortium group is not an `entity`.
- The group can have `disclosed_group_name`, such as "the Investor Group", but
  that label must not create a synthetic entity unless the source discloses a
  legal entity with that name.
- A group contains organization participants in 3.32a.

### 4. `transaction_participant`

Canonical participant membership for a transaction.

```sql
CREATE TABLE transaction_participant (
    participant_id         TEXT PRIMARY KEY,
    transaction_id         TEXT NOT NULL REFERENCES transaction_record(transaction_id),
    entity_id              TEXT NOT NULL REFERENCES entity(entity_id),
    group_id               TEXT REFERENCES transaction_participant_group(group_id),

    side                   TEXT NOT NULL,
    participant_role       TEXT NOT NULL,
    role_detail            TEXT,

    -- Transaction-specific attributes.
    is_primary             INTEGER,
    is_lead                INTEGER,
    is_existing_investor   INTEGER,
    is_new_investor        INTEGER,

    source_raw_id          INTEGER,
    source_document_id     INTEGER,
    source_section_id      INTEGER,
    source_stage           TEXT,
    model_confidence       TEXT,
    evidence_text          TEXT,

    review_status          TEXT DEFAULT 'UNREVIEWED',
    reviewed_by            TEXT,
    reviewed_at            TEXT,
    researcher_notes       TEXT,

    is_current             INTEGER DEFAULT 1,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

Recommended `participant_role` values:

Deal party roles:

- `TARGET`
- `ACQUIRER`
- `BUYER_SPONSOR`
- `SELLER_SPONSOR`
- `BUYER_PLATFORM`
- `SELLER_PLATFORM`
- `INVESTOR`
- `ISSUER`
- `PARENT_SELLER`
- `PARENT_ACQUIRER`
- `MERGER_SUB`

Rules:

- Do not use `LEAD_INVESTOR` as a role. Use
  `participant_role=INVESTOR` with `is_lead=1`.
- `participant_role` is transaction-specific. It does not determine
  `entity.entity_type`.

### Deferred: `entity_relationship`

`entity_relationship` is removed from the active 3.32a scope.

Do not write relationship rows for:

- `PORTFOLIO_COMPANY_OF`
- `SPONSORED_BY`
- `CORPORATE_VC_ARM_OF`
- `MANAGED_BY`
- `SUBSIDIARY_OF`

Those concepts may be revisited later, but 3.32a represents disclosed
organizations through transaction-context participant roles instead.

## Participant Roles

The 3.32a participant model should support organization roles needed for
multi-party transaction support:

- target;
- acquirer;
- buyer sponsor;
- seller sponsor;
- buyer platform;
- seller platform;
- investor;
- issuer;
- parent seller;
- parent acquirer;
- merger sub.

Lead investor status is represented as `participant_role=INVESTOR` with
`is_lead=1`, not as a separate role or entity.

## Participant Side and Grouping

`side` answers "which side of the transaction is this participant on?"
`group` answers "who acted together?"
`participant_role` answers "what did this participant do?"
`entity_type` answers "what kind of entity is this intrinsically?"

These concepts are normalized storage concepts. They are not a replacement for
existing flat transaction fields.

Examples:

| Disclosure | Entity | Entity type | Side | Group | Role / attributes |
|---|---|---|---|---|---|
| "Acme Ventures led the Series B" | Acme Ventures | CORPORATE_VENTURE_ARM | INVESTOR | Investor group | INVESTOR, `is_lead=1` |
| "Acme Ventures, the venture arm of Acme Corp" | Acme Ventures | CORPORATE_VENTURE_ARM | INVESTOR | Investor group | INVESTOR |
| "Buyer Group led by Silver Lake and CPP Investments" | Buyer Group | none | BUYER | CONSORTIUM | group label only |
| "Silver Lake" | Silver Lake | PRIVATE_EQUITY_FIRM | BUYER or INVESTOR | Buyer Group | ACQUIRER, BUYER_SPONSOR, or INVESTOR as disclosed |
| "Merger Sub, a wholly owned subsidiary of Parent" | Merger Sub | SUBSIDIARY | BUYER | none | MERGER_SUB |
| "Parent" | Parent | OPERATING_COMPANY | BUYER | none | PARENT_ACQUIRER or ACQUIRER |
| "Company will issue Series B shares to Acme Ventures" | Company | OPERATING_COMPANY | ISSUER | none | ISSUER |

Grouping rules:

- Every participant should have a side.
- A group can exist with one participant if the source discloses an explicit
  group construct, but default single participants do not need artificial
  groups.
- A consortium group may have zero or one current participant when the existing
  flat field contains only a generic group label; those cases remain reviewable
  and should not create synthetic entities.
- Group labels should be source text, not researcher-invented rollups.

## Entity Attributes Useful for Linking

Store these when disclosed or available from current source data:

Organization linking attributes:

- Exact source name.
- Legal name.
- Display name.
- Normalized name.
- Domain or website.
- Public ticker.
- SEC CIK.
- LEI, if present.
- Country, state, or headquarters location when disclosed.
- Entity type and subtype.
- Transaction-context participant role, side, group, and source-specific
  aliases.
- Source-specific aliases.

Linking posture:

- Strong identifiers such as domain, ticker, and CIK can link across
  transactions.
- Name-only global linking should be conservative and reviewable.
- Researcher overrides should be first-class, not hidden edits.

## 3.32a Extraction and Derivation Boundary

3.32a should not add a new LLM extraction prompt. It should derive normalized
organization participants from existing flat fields and existing pipeline
outputs.

Derive in 3.32a:

- organization entities from existing target, acquirer, parent seller, sponsor,
  merger-sub, and agreement parent-acquirer fields;
- participant rows from those same existing fields;
- participant groups only where existing flat fields indicate a consortium,
  investor group, or seller group;
- aliases from source names already present in flat fields;
- platform, sponsor, parent, and merger-sub concepts as transaction-context
  participant roles;
- `is_primary` from existing canonical target/acquirer fields;
- `is_lead`, `is_existing_investor`, and `is_new_investor` only when already
  explicit in existing fields or reviewable source text available to the
  backfill.

Do not derive in 3.32a:

- people;
- advisors;
- accountants;
- lenders;
- ownership history;
- lead investor status from brand prominence or ordering alone;
- parent or ultimate parent relationships from brand recognition alone;
- corporate venture arm collapse into corporate parent;
- sponsor/platform/parent relationships from firm-name pattern alone.

## Revised 3.32 Implementation Plan

3.32a should implement organization-only multi-party transaction support. It
should not attempt to solve people, advisors, manual collection, export, or
graph exploration.

The implementation should:

1. Add the approved participant tables.
2. Backfill organization participants from existing flat fields.
3. Represent groups for consortiums, investor groups, and seller groups.
4. Represent sponsors, platforms, parents, merger subs, investors, and issuers
   as transaction-context participant roles.
5. Validate that canonical transaction behavior is unchanged.

## Proposed 3.32a Patch Scope

Included:

- `entity`
- `entity_alias`
- `transaction_participant`
- `transaction_participant_group`
- organization-only backfill from existing flat transaction fields
- copied-real-DB validation

Supported participant roles:

- `TARGET`
- `ACQUIRER`
- `BUYER_SPONSOR`
- `SELLER_SPONSOR`
- `BUYER_PLATFORM`
- `SELLER_PLATFORM`
- `INVESTOR`
- `ISSUER`
- `PARENT_SELLER`
- `PARENT_ACQUIRER`
- `MERGER_SUB`

Supported participant attributes:

- `is_lead`
- `is_primary`
- `is_existing_investor`
- `is_new_investor`

Supported group types:

- `CONSORTIUM`
- `INVESTOR_GROUP`
- `SELLER_GROUP`

## Backfill Plan

Backfill should be additive, idempotent, and run on copied real DBs before any
production use.

For each current `transaction_record`:

- `target_name` -> `entity`; participant side `TARGET`, role `TARGET`,
  `is_primary=1`.
- `acquirer_name` -> `entity`; participant side `BUYER`, role `ACQUIRER` or
  `BUYER_PLATFORM` when existing `acquirer_type=PE_PORTFOLIO`; `is_primary=1`.
- `parent_seller_name` -> `entity`; participant side `SELLER`, role
  `PARENT_SELLER`.
- `acquirer_sponsor_name` -> one or more sponsor entities when parsing is safe;
  participant side `BUYER`, role `BUYER_SPONSOR`; mark review status when parsing
  is ambiguous.
- `acquirer_merger_sub_name` -> `entity`; participant side `BUYER`, role
  `MERGER_SUB`.
- Agreement-derived parent acquirer fields, when available, -> participant side
  `BUYER`, role `PARENT_ACQUIRER`.

Group backfill:

- If `acquirer_type=CONSORTIUM`, create a `CONSORTIUM` group and attach buyer,
  investor, sponsor, and parent-acquirer participants where available.
- If multiple investors are disclosed in existing flat fields, create an
  `INVESTOR_GROUP`.
- If multiple parent sellers are disclosed in existing flat fields, create a
  `SELLER_GROUP`.
- Do not create an `entity` for "Buyer Group", "Investor Group", or similar
  labels unless the source discloses a legal entity with that name.

Relationship backfill:

- None in active 3.32a.
- Do not write `PORTFOLIO_COMPANY_OF`, `SPONSORED_BY`,
  `CORPORATE_VC_ARM_OF`, `MANAGED_BY`, or `SUBSIDIARY_OF`.

Entity linking order:

1. Exact ticker, CIK, domain, or LEI when available.
2. Exact normalized name within the same transaction.
3. Name-only participant entities remain transaction-scoped in 3.32a to avoid
   unsafe global merges.
4. Name-only global matching can be introduced later only as a reviewable
   entity-linking workflow.

Ambiguity should create `review_status=NEEDS_REVIEW`, not silent merges.

## Validation Plan

Schema and integrity:

- Every current `transaction_participant` row has valid `transaction_id`,
  `entity_id`, `side`, and `participant_role`.
- Every participant group belongs to a valid transaction.
- Every non-null `group_id` on `transaction_participant` exists.
- `entity_kind`, `entity_type`, `side`, `group_type`, and
  `participant_role` are within 3.32a vocabularies.

Coverage:

- Every non-null `transaction_record.target_name` has a current `TARGET`
  participant.
- Every non-null `transaction_record.acquirer_name` has a current `ACQUIRER` or
  `BUYER_PLATFORM` participant, except generic consortium labels that are stored
  as group labels only.
- Every non-null `transaction_record.parent_seller_name` has a current
  `PARENT_SELLER` participant.
- Every non-null `transaction_record.acquirer_sponsor_name` has one or more
  `BUYER_SPONSOR` participants or an explicit review flag.
- Every non-null `transaction_record.acquirer_merger_sub_name` has a current
  `MERGER_SUB` participant.
- Every `acquirer_type=CONSORTIUM` transaction has a participant group or an
  explicit review flag.

Safety:

- `transaction_record` row content is unchanged.
- Existing `advisor` rows are unchanged.
- Stage 9 staging-read output is unchanged.
- Stage 9 observation-read parity remains unchanged.
- Backfill is idempotent.
- No synthetic consortium/investor/seller group entities are created.
- `entity_relationship` is absent from the active schema and no relationship
  rows are written.

Review samples:

- All transactions where `acquirer_type=CONSORTIUM`.
- All transactions with comma-delimited `acquirer_sponsor_name`.
- All transactions with merger-sub fields.
- All transactions with multiple buyer/investor/sponsor participants.

## Explicit Non-Goals

- Do not remove or rename existing `transaction_record` fields.
- Do not change Stage 9 aggregation behavior.
- Do not switch default `AGGREGATION_READ_SOURCE`.
- Do not implement agreement supersession through Stage 9.
- Do not implement people.
- Do not implement advisor redesign.
- Do not implement a generic `transaction_deal_participant_attribute` table.
- Do not replace or mirror existing `advisor` rows.
- Do not replace existing `transaction_record` fields.
- Do not redesign export or filtering.
- Do not create a researcher UI.
- Do not perform external web, LinkedIn, SEC, or firmographic enrichment in
  this drop.
- Do not build graph-style relationship modeling.
- Do not build ownership-history modeling.
- Do not build a complete enterprise master-data system.
- Do not infer ultimate parents or corporate families from brand recognition.
- Do not collapse corporate venture arms, subsidiaries, funds, acquisition
  vehicles, or portfolio companies into parents.
- Do not make lead investor a separate entity or role.
- Do not require live OpenAI or Anthropic API validation for the design.

## Acceptance Criteria for 3.32a

Before implementation can close, validation should show:

- `transaction_record` is unchanged.
- Existing Stage 9 parity remains unchanged.
- Backfill is idempotent.
- Every flat target/acquirer/parent-seller/sponsor/merger-sub field has
  participant coverage or an explicit review flag.
- Groups represent consortiums, investor groups, and seller groups without
  creating synthetic entities.
- Lead investor status appears only as an attribute.
- Parent, sponsor, platform, investor, issuer, and merger-sub concepts are
  preserved as participant roles when disclosed by existing data.
- Ambiguous entity linking is reviewable.
- Corpus-level samples confirm subsidiaries, corporate venture arms, and PE
  platform companies are preserved as disclosed.

## 3.32a Validation Result

Validation passed on a copied real DB. Production was not touched and no live
API calls were made.

Inputs and artifacts:

- Source DB: `/private/tmp/ma_331c_parity_staging_674ab04.db`
- Validation DB: `/private/tmp/ma_332a_patch3_validation.db`
- Full JSON report: `/private/tmp/ma_332a_patch3_validation.json`

Results:

- Overall result: `PASS`
- `entity` rows inserted: `802`
- `entity_alias` rows inserted: `802`
- `transaction_participant` rows inserted: `803`
- `transaction_participant_group` rows inserted: `20`
- Duplicate current participants: `0`
- Duplicate groups: `0`
- Synthetic group entities: `0`
- Foreign key issues: `0`
- `transaction_record` unchanged: `335` rows, digest matched source
- `advisor` unchanged: `340` rows, digest matched source
- Idempotency second run inserted: `0`

Participant role counts:

- `TARGET`: `335`
- `ACQUIRER`: `278`
- `BUYER_PLATFORM`: `54`
- `BUYER_SPONSOR`: `66`
- `PARENT_SELLER`: `70`

Group counts:

- `CONSORTIUM`: `12`
- `INVESTOR_GROUP`: `5`
- `SELLER_GROUP`: `3`

Coverage note:

- Strict acquirer misses: `3`
- All three are generic consortium-label exceptions stored as group labels, not
  synthetic entities.
