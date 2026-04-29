# M&A Collection MVP — Project State

**As of:** 2026-04-23
**Repo:** `ma-collection-mvp` (private GitHub)
**Operator:** Erik (independent)
**Implementation:** Claude Code (Sonnet 4.6 in VS Code)
**Spec / review:** Claude Opus 4.7 (web chat)

---

## 1. Purpose of This Document

Preserves project state across chat sessions. Conversations grow long; this doc is the single source of truth for "what exists, what's next, why we made the decisions we made" when starting a fresh session.

Read this first. Then `README.md` for the repo overview. Then the spec files referenced inline.

---

## 2. What We've Built

### 2.1 Architecture

A single-operator, laptop-scale M&A data collection pipeline. Intent: demonstrate a pipeline that beats incumbent event-capture quality by combining authoritative source tiering, deterministic clustering, and LLM-assisted aggregation.

- **Entry point:** `run.py` with 8 run modes (full, resume, scrape, extract, aggregate, generate, export, rerun-prompt)
- **Storage:** SQLite, WAL mode, 12 tables (v0.2 schema)
- **Execution:** sequential, no concurrency, idempotent, resumable
- **Sources:** PR Newswire (scrape-only) + sec-api.io (API, $55/mo tier) for enrichment
- **Models:** Claude Haiku 4.5 (relevancy filter only); Claude Opus 4.7 (all other LLM stages)
- **Error posture:** never halt on single-row failures; halt only on infrastructure (auth, DB, persistent rate limits)

### 2.2 Pipeline (12 Stages)

| # | Stage | Input state | Output state | Engine |
| :--- | :--- | :--- | :--- | :--- |
| 1 | `scrape_pr_newswire` | — | `FETCHED` | PR Newswire HTML scrape |
| 2 | `relevancy_filter` | `FETCHED` | `RELEVANT` / `NOT_RELEVANT` / `RELEVANCY_FAILED` | Haiku |
| 3 | `deal_type_classify` | `RELEVANT` | `CLASSIFIED` | Opus |
| 4 | `high_confidence_extract` | `CLASSIFIED` | `HC_EXTRACTED` | Opus |
| 5 | `sec_trigger_detect` | `HC_EXTRACTED` | `SEC_TRIGGERED` / `SEC_NOT_TRIGGERED` | Python regex (no LLM) |
| 6 | `sec_enrich` | `SEC_TRIGGERED` | `SEC_ENRICHED` | sec-api.io (Filing Query, Extractor, Download) |
| 7 | `low_confidence_extract` | `HC_EXTRACTED` / `SEC_ENRICHED` / `SEC_NOT_TRIGGERED` | `LC_EXTRACTED` | Opus |
| 8 | `entity_cluster` | `LC_EXTRACTED` | `CLUSTERED` | Python fuzzy match + union-find |
| 9 | `aggregate` | `CLUSTERED` | `AGGREGATED` | Tier rules + Opus on conflicts |
| 10 | `summarize` | `AGGREGATED` | new `summary` row | Opus |
| 11 | `rationale_tag` | `AGGREGATED` | new `rationale_tag` row | Opus |
| 12 | `export` | `is_current = true` | CSV | — |

### 2.3 Schema (v0.2)

Twelve tables. Key decisions:

- **`deal_type` enum (7 values):** `ACQUISITION`, `MERGER`, `SPIN_SPLIT`, `REVERSE_MERGER`, `JOINT_VENTURE`, `MINORITY_INVESTMENT`, `UNKNOWN`. Take-Private, Add-On, Carve-Out-as-IPO, Reverse-Merger are **feature flags on base types**, not separate enum values. Rationale: `target_status = PUBLIC + acquirer_type = PRIVATE_EQUITY` captures Take-Private without a dedicated enum value.
- **`value_type` enum (4 values):** `EQUITY_VALUE`, `TRANSACTION_VALUE`, `ENTERPRISE_VALUE`, `UNDISCLOSED`. Default: `TRANSACTION_VALUE`.
- **`target_type` enum:** `STANDALONE_COMPANY`, `BUSINESS_UNIT`, `SUBSIDIARY`. Drives `is_divestiture` derivation.
- **`acquirer_type` enum (15 values):** includes `PE_PORTFOLIO` for add-ons; enables `is_add_on` derivation.
- **`consideration_components`:** JSON array on `staging_extraction` and `transaction_record`. `consideration_type` is orchestrator-derived (CASH / STOCK / CASH_AND_STOCK / ELECTION / OTHER) per deterministic rules, not LLM-extracted.
- **`termination_fees`:** split by party (target_fee_amount, target_fee_percentage, acquirer_fee_amount, acquirer_fee_percentage).
- **`go_shop`:** `has_go_shop` (bool) + `go_shop_period_days` (int).
- **Derived flags on `transaction_record`:** `is_take_private`, `is_add_on`, `is_divestiture` — computed from source fields in aggregate.py.
- **Provenance:** `dt_prompt_version`, `hc_prompt_version`, `lc_prompt_version` on `staging_extraction`. Single `prompt_version` on `summary`, `rationale_tag`, `aggregation_conflict_log`.
- **Enums enforced at application layer**, not via SQLite CHECK constraints — lets prompts iterate without DDL migrations.

DDL: `schema/001_initial.sql`. Spec: `mvp_goal_and_schema.md`.

### 2.4 Prompts (8 files)

| File | Model | Purpose | Current version |
| :--- | :--- | :--- | :--- |
| `relevancy_filter` | Haiku | In-scope / out-of-scope classification | 0.4 |
| `deal_type_classifier` | Opus | 7-type taxonomy classification | 0.2 |
| `high_confidence_extraction` | Opus | Parties, dates, value, target financials | 0.4 |
| `low_confidence_extraction` | Opus | Advisors, consideration, flags, termination fees | 0.2 |
| `aggregation` | Opus | Conflict resolution on tier ties | 0.1 |
| `deal_summary` | Opus | 80–150 word narrative | 0.4 |
| `strategic_rationale` | Opus | 8-category rationale classification | 0.3 |
| `prompt_conventions` | — | Shared conventions (not a prompt itself) | — |

All prompt files follow a common structure. Sections 4 (System Prompt) and 5 (User Template) are loaded at runtime via `prompts/base.py`'s `load_prompt_file()`. Section 6 (Output Schema) is human-facing documentation. A `RESPONSE FORMAT` block inlined in section 4 gives the model the concrete JSON shape (Drop 3.2).

### 2.5 Evaluation Infrastructure

- **Gold set:** `eval/gold_set_template.csv` (empty template). Operator labels rows with 23 graded fields.
- **Scoring:** `eval/score.py` produces a markdown scorecard. Per-field precision, dedup metrics, failure counts.
- **Tiered coverage:** full coverage on parties / deal_type / value; 20/100 spot-check on dates, financials, rationale; 10/100 spot-check for false-negative irrelevants.
- **Operator grades, not Claude.** Avoids LLM self-agreement.

Spec: `specs/evaluation.md`.

---

## 3. Current State

### 3.1 Commits (most recent first)

| Commit | Drop | Content |
| :--- | :--- | :--- |
| `d3e4720` | Stage 8 fix | Name-only matching and hash stabilization for null-date rows (resolves Servier hash-collision false-singleton and 78/76 transaction count gap) |
| `c4f5077` | Layer 5 | eval/score.py: gold set scoring and scorecard generation |
| (most recent before) | — | 100-PR production run export `transactions_run_20260423_153828.csv` |
| `a7a3f60` | Layer 4d | Stages 10/11/12: summary, rationale, export |
| (several) | Layer 4c | Stages 8/9: clustering, aggregation |
| (several) | Layer 4b | Stages 5/6/7: SEC trigger, SEC enrich, LC extract |
| `8cf20b1` | Drop 3.5 | `relevancy_filter` v0.3: CRITICAL enum discipline block |
| (subsequent) | Drop 3.6 | `relevancy_filter` v0.4: suffix-pattern guard |
| (subsequent) | Drop 3.7 | Removed "carve-out" terminology leak from summary + rationale prompts |
| `e02269d` | Drop 3.3 | `high_confidence_extraction` v0.4: simultaneous announce-and-close date rule |
| `29502eb` | Drop 3.2 | Inline RESPONSE FORMAT blocks in all 7 prompt files |
| `3277ad8` | Layer 2.1 | sec-api.io adapter v0.2 (expanded items, Exhibit 99) |
| `5c66958` | Layer 1 | config, db, logger, run.py + 12 stage stubs |
| `a00806c` | Cleanup | `sec_lookup_status` column, 3 prompt_version columns, model env vars |
| `7c6034a` | Drop 3 | Pipeline, entity resolution, evaluation, DDL, README, env, gitignore |

### 3.2 100-PR Production Run Results (run_20260423_153828)

**Runtime:** 49m 07s (PASS < 2h target)
**API cost:** ~$3-4

| Metric | Value |
| :--- | :--- |
| Fetched | 100 |
| Relevant | 88 |
| Not relevant | 9 |
| Relevancy failed (schema violation) | 3 |
| Extracted (HC) | 87 |
| Clusters | 76 (after Stage 8 patch; originally reported as 78 due to null-date hash collision, corrected) |
| Transactions | 76 |
| Summaries | 76 |
| Rationales | 76 |
| Failures logged | 4 |

**Deal type distribution (76 transactions):** ACQUISITION 68, REVERSE_MERGER 3, MINORITY_INVESTMENT 2, MERGER 2, JOINT_VENTURE 1.

**Primary rationale distribution:** PRODUCT_OR_TECH_CAPABILITY 25, GEOGRAPHIC_EXPANSION 19, SCALE_CONSOLIDATION 15, FINANCIAL_OR_ARBITRAGE 7, VERTICAL_INTEGRATION 4, OTHER 4, MARKET_DIVERSIFICATION 2, TALENT_ACQUISITION 0.

**Consideration type:** 59 null (78% — expected for private deals), 7 CASH, 7 OTHER, 2 STOCK, 1 CASH_AND_STOCK.

**Flags:** is_take_private 1, is_add_on 19 (18 singletons + 1 with divestiture), is_divestiture 12 (10 standalone + 2 with add-on).

**SEC enrichment:** 6 of 76 (7.9%): Honeywell/WWS, Einride/Legato, Essential Utilities, Stellar Bancorp, Citizens National, Spire Mississippi. All large, publicly-visible deals. 14 NO_MATCH (likely mix of private-to-private with boilerplate ticker mentions + public deals whose 8-Ks haven't been filed yet).

**Cluster analysis:** 7 multi-member clusters. Three are multilingual dedup (Woolpert DE/FR/ES/EN, Servier French+English, NX Group ES/FR/DE) — cross-language fuzzy matching worked. Servier/Day One now clusters explicitly via the name-only pass (Stage 8 patch); previously merged by hash accident. One flagged for operator review: `tc_182e231b4059` — Allcryo deal with two acquirer-side framings (Bridge Industries vs TransTech). Confirmed as correct merge (same deal; PE sponsor vs platform framing) but surfaced a schema gap (see §5).

**Snapshots preserved:**
- `data/ma_mvp_validation_20260423.db` (15-PR validation run, 3.5MB)
- `exports/archive/validation_20260423.csv` (13 transactions, 8.3KB)

### 3.3 Acceptance Criteria (from `specs/pipeline.md` §10) — status

| Metric | Target | 100-PR run | Status |
| :--- | :--- | :--- | :--- |
| Runtime | < 2h | 49m | PASS |
| Relevancy accuracy | > 95% | pending gold label | TBD |
| Parties extracted correctly | > 95% | pending gold label | TBD |
| Deal type classified correctly | > 90% | pending gold label | TBD |
| Announced date | > 98% | pending gold label | TBD |
| Value + value_type | > 90% | pending gold label | TBD |
| Dedup precision | > 95% | 1 suspect flagged, likely clean | PASS (hash-collision risk resolved by Stage 8 patch; pending gold label for false-merge confirmation) |
| Prompt failure rate | < 2% | 4/88 = 4.5% | Close / minor FAIL |

The 4.5% prompt failure rate is driven by the `reason_code` enum discipline tail (3 of 4 failures). Two of the three were RELEVANT deals the pipeline dropped. Prompt revision to close this tail is a known deferred item (§4.2).

---

## 4. What Needs to Be Addressed

Ordered by priority for a fresh session.

### 4.1 Immediate (before or during next session)

**A. Gold set labeling.** Operator task. Plan 3 focused hours. Label all 76 transactions on full-coverage fields; 20/76 on spot-check fields; 9/9 NOT_RELEVANT rows for false-negative check; cluster `tc_182e231b4059` explicitly.

Suggested labels for `tc_182e231b4059`:
- target: Allcryo
- acquirer: Cryogenic Technology Resources
- acquirer_type: PE_PORTFOLIO
- parent_seller_name: null
- deal_type: ACQUISITION
- target_type: STANDALONE_COMPANY
- announced_date: 2026-04-21
- closed_date: 2026-04-21
- event_type: CLOSE
- consideration_type: null
- primary_rationale: GEOGRAPHIC_EXPANSION

Note in the gold set CSV's notes column: "acquirer is PE portfolio co; sponsor is Bridge Industries; intermediate platform is TransTech Group; schema collapses to single acquirer field."

**B. Run the scorecard.** `python eval/score.py --gold eval/gold_set_<date>.csv --run-id run_20260423_153828`. Review against acceptance criteria.

### 4.2 Prompt revision candidates (batch for Drop 3.8)

Deferred, to be batched after scorecard review:

- **Drop 3.8 — relevancy_filter v0.5: close the enum discipline tail.** Add to CRITICAL block:
  - `MERGER_OR_CONSOLIDATION → use MERGER`
  - `MERGER_OR_COMBINATION → use MERGER`
  - `EXECUTIVE_APPOINTMENT → use PERSONNEL`
  - General note: "Do NOT combine enum values with OR."
- **Prompt-side enum tightening** (if scorecard surfaces similar patterns in other prompts).
- **Summary word-floor conditional relaxation** for sparse private deals — accept sub-80 words when value_type = UNDISCLOSED AND no advisors AND no financials.

### 4.3 Known deferred — MVP-acceptable limitations

- **Sec-retry mode.** When PRs are processed same-day they're issued, the corresponding 8-K often hasn't been filed yet. Implement `--mode=sec-retry` that re-queries sec-api.io for `sec_lookup_status = NO_MATCH` rows whose announced_date is 5+ business days old. Small adapter extension.
- **Item 2.02 fallback for earnings-release-disclosed deals.** Confirmed pattern via Honeywell AIP divestiture. Fallback to Item 2.02 only after other items NO_MATCH; gate by Haiku content filter confirming the 2.02 text references the specific target transaction. Estimated 1-2 days.
- **PDF exhibit text extraction.** Currently marked UNREADABLE when Exhibit 2.1 or 99.x returns as PDF. Downstream skips. Implement in v2.
- **Close-announcement linking (lifecycle management).** Today a CLOSE PR arriving weeks after an announcement creates an orphan CLOSE transaction. The right behavior is to link it to the existing announcement record and update closed_date/status. See §5.

### 4.4 Not yet done — final MVP steps

After §4.1 lands:
- Document known limitations and deferred items in the repo (possibly this file, checked in as `docs/project_state.md`).
- Commit the 100-PR CSV export to a `runs/` folder (or equivalent) for reference.
- Write a short post-run analysis markdown summarizing scorecard findings.

---

## 5. Strategic Directions (V2)

These are known opportunities, logged but not scoped. Review when planning post-MVP work.

### 5.1 Transaction Lifecycle Management

Today's automated pipeline treats each PR as a discrete event. The Grata manual collection process already handles deal lifecycle — close announcements, regulatory milestones, amendments, terminations — as supplemental observations linked to the original announcement record. Lifecycle management for the automated pipeline is automation parity with that manual workflow, not a new architectural problem. Where DataOps has documented matching criteria for linking follow-up PRs to existing deals, the automated path should inherit those rules. The escape-valve pattern (flag low-confidence matches for human review) extends from the existing aggregation_conflict_log convention via a new transaction_link_log table.

V2 architecture adds a linking stage:

1. Extract phase (unchanged)
2. Intra-run cluster (current Stage 8)
3. **Cross-run linking (new):** for each cluster with `event_type IN (CLOSE, AMENDMENT, TERMINATION)`, query the DB for existing `transaction_record` rows matching target/acquirer (fuzzy ≥90) AND `announced_date` within last 365 days. Link if found.
4. Aggregate (updated): if linked, merge new sources with existing transaction's source history; regenerate summary and rationale against combined pool; update affected fields. If unlinked, create new transaction (today's behavior).

Schema additions:
- `transaction_record.status_history` JSON array tracking ANNOUNCEMENT → PENDING_REGULATORY → CLOSED progressions
- New `transaction_link_log` table for linking-decision audit trail

Rationale: this is the difference between event-capture quality and platform-quality data. Competitors (S&P Capital IQ, PitchBook) do this; Grata's current data structure does not. This is the single highest-impact v2 improvement.

### 5.2 Source Comprehensiveness for Public-Party Deals

For any deal involving a public party, gather ALL same-date or proximate-date SEC filings + IR-page material, not just the 8-K Items we currently target.

Candidate sources:
- 8-K Items beyond current coverage: 2.02 (deferred — see §4.3), 7.01 (Reg FD), 9.01 (exhibits index)
- DEFM14A, DEFR14A (merger proxy)
- S-4 (registration statement for stock-for-stock)
- SC TO-T (tender offer)
- Form 425 (merger communications)
- 6-K (foreign private issuers)
- Company IR page scrape (announcement-date snapshot)
- Earnings call transcripts (deal discussion portions)

Purpose:
- Manual DataOps review — reviewer sees all source documents in one place
- Consumer source transparency with provenance
- Richer input for extraction (see §5.3)

### 5.3 Pool-Extraction Architecture

Current architecture: extract per-source, aggregate across sources.
Future architecture: gather full source pool per deal, extract once against the pool.

Enables:
- Securities-level consideration capture (per-class share counts, exchange ratios, elections) — detail lives in DEFM14A/S-4
- Higher fill rate on target financial periodicity (LTM/FY, period end) — investor presentations routinely disclose
- Richer strategic rationale — earnings call transcripts contain management's own framing
- Synergy targets, integration timelines, cost-savings estimates — disclosure categories absent from PRs

Tradeoff: per-source extraction is more auditable (field → source provenance); pool extraction is more accurate on complex fields but harder to debug when wrong.

Gated on §5.2 (sources must be collected before pool extraction becomes relevant).

### 5.4 PE Deal Structure Classification

Current schema cannot distinguish between a sponsor's first investment in a target (often described as a platform investment), a sponsor-to-sponsor sale (secondary buyout), an add-on acquisition by a portfolio company, a take-private, a carve-out buyout, a management buyout, or various PE exit structures. For a PE-heavy private-deals dataset, this is a material gap.

The proposed v2 taxonomy is descriptive — based on observable deal structure — rather than strategic. We deliberately avoid using "platform" as a schema value because whether a deal functions as a platform depends on the sponsor's post-close strategy, not on the announcement-time structure. Platform status is a derived analytical concept built on top of the schema, not a field within it.

**Proposed additions:**

`pe_deal_structure` ENUM on `transaction_record`:

| Value | Meaning |
| :--- | :--- |
| `SPONSOR_TO_SPONSOR` | PE-to-PE sale (secondary buyout) |
| `SPONSOR_ACQUIRES_PRIVATE` | Sponsor buys privately-held target, no exiting sponsor |
| `SPONSOR_ACQUIRES_PUBLIC` | Take-private |
| `SPONSOR_ACQUIRES_CARVE_OUT` | Sponsor buys business unit from corporate parent |
| `PORTFOLIO_ACQUIRES` | Existing portfolio company acquires (add-on) |
| `MANAGEMENT_BUYOUT` | Management-led acquisition, may include PE backing |
| `PE_EXIT_TO_STRATEGIC` | PE-owned target sold to strategic acquirer |
| `PE_EXIT_TO_PUBLIC` | PE-owned target goes public (IPO exit) |
| `GP_LED_SECONDARY` | Same GP, new fund vehicle (continuation fund, strip sale) |
| `NOT_PE` | Deal has no PE involvement |

Supporting fields:
- `exiting_sponsor_name TEXT` — populated when target was previously PE-owned
- `acquiring_sponsor_name TEXT` — the sponsor acquiring; may equal `acquirer_name` when `acquirer_type = PRIVATE_EQUITY`, or the sponsor behind a portfolio company when `acquirer_type = PE_PORTFOLIO`
- `acquirer_parent_platform_name TEXT` — for `PORTFOLIO_ACQUIRES`, the intermediate platform company

Derivation runs in `aggregate.py`. Most values derive from existing fields (`acquirer_type`, `target_status`, `target_type`, `parent_seller_name`) plus the new `exiting_sponsor_name`. The LC extraction prompt needs a new parse target: `exiting_sponsor_name` from text signals like "portfolio company of X since 2019" or "X Capital, which acquired the company in 2020, today announced the sale...".

MBO handling: when a management team is the visible acquirer but a PE sponsor provides equity, set `acquirer_type = PRIVATE_EQUITY` (capital source) and `pe_deal_structure = MANAGEMENT_BUYOUT` (structure flag). Management team names captured in `notes`. This keeps "show me all PE deals" queries simple while preserving the MBO distinction.

**User Filter Derivability**

The descriptive taxonomy supports common user filters without additional schema fields:

| Filter | Query |
| :--- | :--- |
| Add-On | `pe_deal_structure = PORTFOLIO_ACQUIRES` |
| Platform | `pe_deal_structure IN (SPONSOR_ACQUIRES_PRIVATE, SPONSOR_ACQUIRES_CARVE_OUT)` |
| Buyout (broad) | `pe_deal_structure IN (SPONSOR_ACQUIRES_PRIVATE, SPONSOR_ACQUIRES_PUBLIC, SPONSOR_ACQUIRES_CARVE_OUT, MANAGEMENT_BUYOUT, SPONSOR_TO_SPONSOR)` |
| Take-Private | `pe_deal_structure = SPONSOR_ACQUIRES_PUBLIC` (equivalent to existing `is_take_private = 1`) |
| Secondary Buyout | `pe_deal_structure = SPONSOR_TO_SPONSOR` |
| Carve-Out Buyout | `pe_deal_structure = SPONSOR_ACQUIRES_CARVE_OUT` |
| Management Buyout | `pe_deal_structure = MANAGEMENT_BUYOUT` |
| Sponsor Exit | `exiting_sponsor_name IS NOT NULL OR pe_deal_structure IN (PE_EXIT_TO_STRATEGIC, PE_EXIT_TO_PUBLIC, SPONSOR_TO_SPONSOR)` |
| GP-Led Secondary | `pe_deal_structure = GP_LED_SECONDARY` |

**Roll-Up / Consolidation** is not a schema field. It is a strategy pattern observable only across multiple deals over time (e.g., same platform making 3+ add-ons in a rolling 12-month window). Derive at query time or via a periodic job that counts add-ons per platform. This keeps the schema focused on per-deal structural observations; downstream applications can define their own roll-up criteria without schema changes.

**Recapitalization:** minority recaps captured via `deal_type = MINORITY_INVESTMENT`. Majority recaps (existing-sponsor dividend recaps or partial secondaries to LPs) are rare in PR-sourced data and not covered by MVP or v2 taxonomy; add a dedicated value only if coverage becomes needed.

**Growth Equity:** treated as `MINORITY_INVESTMENT` with `acquirer_type = PRIVATE_EQUITY` or `VENTURE_CAPITAL`. If MVP expands to growth equity as a first-class tracking concern, growth-specific taxonomy belongs in the VC / growth equity schema (see `vc_transaction_schema.md` in project files), not in the M&A schema.

Priority: high. Subsumes the previously-flagged PE hierarchy work. Ships in approximately 1-2 days of spec + implementation after MVP lands.

### 5.5 GlobeNewswire Adapter

Second wire service. The pipeline is source-agnostic (`source_type` enum accepts new values). RSS + body scrape, no API needed. Validated post-first-run; defer to after production run.

### 5.6 Cross-Lingual Entity Resolution

Observed multilingual dedup worked in the 100-PR run (DE/FR/ES/EN variants merged correctly via token-based fuzzy match). Current approach has no unicode normalization; names like "Müller" vs "Muller" may not cluster.

V2: NFD unicode normalization + diacritic strip. Small change; captures edge cases.

### 5.7 Domain-Based Entity Resolution

Current clustering uses name + date only. Domain (when present) is a strong signal. Schema already captures target_domain and acquirer_domain; clustering logic doesn't use them.

V2 addition: add domain match as a tiebreaker when names are ambiguous. High-confidence match on domain bypasses the fuzzy-name-score threshold.

---

## 6. Known Patterns and Non-Issues

Things that look like bugs but aren't. Flagged so future sessions don't chase them.

- **Sub-80 word summaries.** When a deal's source data is sparse (UNDISCLOSED value, no advisors, no financials, no termination fees), the summary prompt correctly produces short summaries. Word-floor warnings are log noise, not data loss.
- **`consideration_type` null rate at 78%.** Expected for middle-market private deals. Terms are almost never disclosed.
- **No TALENT_ACQUISITION rationale tags.** PR Newswire M&A coverage skews product/capability + geographic. Acqui-hires are rarely wire-distributed.
- **SEC NO_MATCH rate.** Of 14 NO_MATCH rows in the 100-PR run, some are legitimately private-to-private deals where the public-party detector tripped on boilerplate ticker mentions but no 8-K will ever exist. Normal.
- **`OTHER` consideration_type (7/76).** Catches complex structures (earnout-only, retained equity, CVR-only) that don't map cleanly to the 4-item derived enum. Intentional fallback.

---

## 7. Development Style & Conventions

### 7.1 Repo structure

```
ma-collection-mvp/
├── README.md
├── mvp_goal_and_schema.md
├── specs/ (adapter_pr_newswire, adapter_sec_api, pipeline, entity_resolution, evaluation)
├── prompts/ (7 prompt files + prompt_conventions.md + base.py)
├── schema/001_initial.sql
├── eval/ (gold_set_template.csv, score.py)
├── stages/ (12 stage modules)
├── adapters/ (pr_newswire.py, sec_api.py)
├── scripts/ (validate_adapters.py, validate_prompt_base.py)
├── config.py, db.py, logger.py, run.py
├── .env (gitignored), .env.example
└── .gitignore
```

Runtime-generated (gitignored): `data/`, `exports/`, `logs/`, `notes/`.

### 7.2 Drop numbering

Drops are spec/prompt revision bundles, labeled sequentially. Each lands as a single commit. Patch notes (`drop_X_Y_patch.md` in `/mnt/user-data/outputs/`) are instructions to Claude Code; they are NOT committed to the repo.

### 7.3 Validation discipline

Every layer has a live-source validation step before committing. Dry-run validation (no LLM / API calls) catches integration errors cheaply; live validation confirms end-to-end. Scoring is always operator-graded, never LLM-graded.

### 7.4 Communication style

Terse, directive. No hedging. Erik edits Code's output into his own voice. Cross-document consistency checks are routine. Erik flags issues with minimal context and expects analysis; Claude should provide analysis and recommendation, not validation-seeking questions.

### 7.5 Environment

- `.env` populated with `ANTHROPIC_API_KEY`, `SEC_API_KEY`, `OPERATOR_CONTACT_EMAIL`
- `OPUS_MODEL=claude-opus-4-7`
- `HAIKU_MODEL=claude-haiku-4-5-20251001`
- Python 3.11+, 5 dependencies (`anthropic`, `requests`, `trafilatura`, `rapidfuzz`, `python-dotenv`)

### 7.6 Re-run resets

When re-running the pipeline at a specific stage (e.g., `--mode=aggregate` after a Stage 8 patch), the operator clears downstream tables manually. The correct reset depends on the entry stage:

| Re-run from | Tables to clear | Tables to preserve |
| :--- | :--- | :--- |
| `--mode=full` (fresh DB) | All tables | None |
| `--mode=aggregate` (Stages 8–9) | `transaction_record`, `transaction_source`, `aggregation_conflict_log`. Roll `staging_extraction.status` back to `LC_EXTRACTED`. | `advisor`, `consideration_components` (on `staging_extraction`), `summary`, `rationale_tag`, `source_raw` |
| `--mode=generate` (Stages 10–11) | `summary` (or flip `is_current=0`), `rationale_tag` (or flip `is_current=0`) | All else |
| `--mode=rerun-prompt` | None — new outputs flip prior `is_current` to 0 | All |

Operator: tailor the reset to the stages that will actually re-run. The `advisor` table is written by Stage 7 and should NOT be cleared on aggregate-mode reruns.

**Validation runs use a separate DB path.** Validation runs (small-N test runs after a prompt revision, schema change, or new feature) MUST use `data/ma_mvp_test.db` or any path other than the production `data/ma_mvp.db`. Never run `rm -f data/ma_mvp.db` followed by schema re-init as part of a validation step — this destroys production state. The validation pattern is: set `DB_PATH=data/ma_mvp_test.db` in env or shell, init that path's schema, run the test, inspect, then leave it alone or remove only that test path. Production runs use the default `data/ma_mvp.db` path. This separation is a hard convention, not a guideline. Failure to maintain it cost us the 100-PR DB state during Drop 3.9 validation; the canonical CSV survived but the queryable DB did not.

---

## 8. Next Session Kickoff

Recommended first message to a fresh session:

> I'm Erik. I'm working on an M&A data collection MVP. Please read `docs/project_state.md` first — it's the handoff document for the project. Then read `README.md` and the `specs/` folder.
>
> The pipeline completed a 100-PR production run (`run_20260423_153828`). Stage 8 clustering patch is done. Immediate remaining work:
> 1. Gold set labeling (operator task — see §4.1A)
> 2. Scorecard review (see §4.1B)
> 3. Drop 3.8 — relevancy_filter v0.5 enum discipline tail (see §4.2)
>
> After you've read the docs, give me a summary of what you understood and propose the order for the immediate work.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.2 | 2026-04-23 | Updated post-Stage 8 patch: resolved null-date hash collision, 78/76 cluster gap closed. Added Layer 5 (eval/score.py) and Stage 8 fix to commit log. Updated §3.2 cluster count, §3.3 dedup status, §4.1 immediate items. |
| 0.1 | 2026-04-23 | Initial handoff document. Snapshot after 100-PR production run. |
