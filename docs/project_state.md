# M&A Collection MVP — Project State

**As of:** 2026-05-05
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

A single-operator, laptop-scale M&A data collection pipeline. Intent: demonstrate a pipeline that beats incumbent event-capture quality by combining authoritative source tiering, deterministic clustering, LLM-assisted aggregation, and structured extraction from primary deal documents.

- **Entry point:** `run.py` with 11 run modes: full, resume, scrape, extract, aggregate, sec-documents, agreement-extract, agreement-rerun, generate, export, rerun-prompt
- **Storage:** SQLite, WAL mode, 16 user tables (v1.0 schema)
- **Execution:** sequential, no concurrency, idempotent, resumable
- **Sources:** PR Newswire (scrape-only) + sec-api.io (API, $55/mo tier) for filing discovery; EDGAR direct for exhibit content retrieval
- **Models:** Claude Haiku 4.5 (relevancy filter only); Claude Opus 4.7 (all other LLM stages)
- **Error posture:** never halt on single-row failures; halt only on infrastructure (auth, DB, persistent rate limits)

### 2.2 Pipeline (14 Stages)

| # | Stage module | Engine | Source drop |
| :--- | :--- | :--- | :--- |
| 1 | `scrape_pr_newswire` | PR Newswire HTML scrape | initial |
| 2 | `relevancy_filter` | Haiku | initial |
| 3 | `deal_type_classify` | Opus | initial |
| 4 | `high_confidence_extract` | Opus | initial |
| 5 | `sec_trigger_detect` | Python regex | initial |
| 6 | `sec_enrich` | sec-api.io | initial |
| 7 | `low_confidence_extract` | Opus | initial |
| 8 | `entity_cluster` | Python fuzzy + union-find | initial |
| 9 | `aggregate` | Tier rules + Opus on conflicts | initial |
| 10 | `sec_documents` | sec-api.io + EDGAR direct | Drop 3.19 |
| 11 | `agreement_extract` | Opus (5 section prompts) | Drop 3.20a |
| 12 | `summarize` | Opus | initial |
| 13 | `rationale_tag` | Opus | initial |
| 14 | `export` | — | initial |

### 2.3 Schema (v1.0)

Sixteen user tables. Material additions since v0.2:

- **`transaction_document`** (Drop 3.19) — full text of SEC filings linked to transactions. UNIQUE on `(transaction_id, sec_accession_number, filing_type)`. Per-document extraction tracking via `agreement_extracted_at` column (Drop 3.22b).
- **`transaction_document_section`** (Drop 3.19) — heuristically tagged sections within stored documents. Section types tagged by `lib/section_tagger.py`: DEFINITIONS | RECITALS | CONSIDERATION | CAPITALIZATION | CONDITIONS_TO_CLOSING | TERMINATION_FEES | REPRESENTATIONS | BACKGROUND_OF_MERGER | FAIRNESS_OPINION | OTHER (10 types). Five of these types have associated extraction prompts.
- **`transaction_security`** (Drop 3.20a) — per-class/per-series capitalization extraction. Row-level current/soft-delete (no canonical roll-up; `_apply_capitalization` insert-only).
- **`transaction_field_observation`** (Drop 3.20a/3.20b) — every field observation captured with full source attribution. Multiple sources may disclose the same field; all preserved. `is_current=1` filter excludes soft-deleted rows (soft-delete is per-document on re-extraction).
- **`observation_changes_summary`** column on `transaction_record` (Drop 3.20b) — JSON array surfacing divergence across sources for the same field. Populated when sources disagree on a canonical value.

Other v0.2 → v1.0 changes:
- **`acquirer_sponsor_name`** (Drop 3.10) — comma-delimited PE sponsor capture on `staging_extraction` and `transaction_record`.
- **Earnouts and CVRs** (Drop 3.16) — captured in `consideration_components` JSON array; `has_earnout` / `has_cvr` flags.
- **Multi-transaction PR splitting** (Drop 3.18) — one PR produces multiple `staging_extraction` rows when distinct deals are bundled.
- **Valuation multiples** (Drop 3.12) — derived from extracted fields with NM display rule for out-of-range or period-mismatched values.
- **Transaction status, de-SPAC flag, refined date format** (Drops 3.14/3.15/3.17 bundle).
- **agreement_extracted_at** (Drop 3.22b) — `DATETIME` column on `transaction_document`; enables per-document extraction gate (vs. prior transaction-level gate).
- **12 agreement-sourced columns** on `transaction_record` (Drop 3.20a/3.20b): `acquirer_merger_sub_name`, `merger_structure`, `has_mac_clause`, `requires_target_shareholder_vote`, `target_vote_threshold`, `closing_conditions_summary`, `target_total_diluted_shares`, `fully_diluted_calc_quality`, `agreement_extraction_status`, `has_observation_changes`, `observation_changes_field_count`, `observation_changes_summary`.

DDL: `schema/` migration files. Spec: `mvp_goal_and_schema.md` and `ma_transaction_schema.md`.

### 2.4 Prompts (13 files)

| File | Version | Model | Purpose |
| :--- | :--- | :--- | :--- |
| `relevancy_filter` | 0.4 (revised) | Haiku | In-scope / out-of-scope classification |
| `deal_type_classifier` | 0.4 (revised) | Opus | 7-type taxonomy classification |
| `high_confidence_extraction` | 0.9 | Opus | Parties, dates, value, target financials |
| `low_confidence_extraction` | 0.4 | Opus | Advisors, consideration, flags, termination fees |
| `aggregation` | 0.2 (draft) | Opus | Conflict resolution on tier ties |
| `deal_summary` | 0.7 | Opus | 80–150 word narrative |
| `strategic_rationale` | 0.3 (revised) | Opus | 8-category rationale classification |
| `agreement_recitals` | 0.2 | Opus | Party identification, Merger Sub demotion, merger structure |
| `agreement_consideration` | 0.1 | Opus | Cash per share, exchange ratio, CVR/earnout components |
| `agreement_capitalization` | 0.1 | Opus | Per-security-class share counts → `transaction_security` |
| `agreement_termination` | 0.1 | Opus | Termination fees, go-shop provisions |
| `agreement_conditions` | 0.1 | Opus | MAC clause, shareholder vote threshold, conditions summary |
| `prompt_conventions` | — | — | Shared response-format conventions (not a callable prompt) |

Prompt-output contract (Drop 3.22b): closed-vocabulary fields return `null` when a section is silent; `UNKNOWN` is not a valid output for any field. Prompt version `agreement_recitals:0.2` removed UNKNOWN from the merger_structure enum.

### 2.5 Evaluation Infrastructure

Unchanged in design. Gold-set labeling not yet executed against the cycle-close run.

Spec: `specs/evaluation.md`.

---

## 3. Current State

### 3.1 Commits (most recent first)

| Commit | Drop | Content |
| :--- | :--- | :--- |
| `77d0097` | 3.22c | Applier functions clear canonical on empty current-observation set; `_clear_stale_canonical_fields()` + 8 new test cases |
| `49cb6a4` | 3.22b | Agreement-extract data quality fixes: UNKNOWN→null, defined-term filter, document_title watermark, per-doc extraction gate, RECITALS heuristics |
| `ead8f5f` | 3.22a | `fetch_exhibit()` SGML wrapper stripping (parallel of 3.21 fix on second fetch path) |
| `6a22e04` | 3.22 | `_find_exhibits()` regex fix + `query_8k_signing_filings()` + Stage 10 dual-filer rewrite; `scripts/backfill_8k_exhibit21.py` |
| `86af826` | state | Project state update through Drop 3.21 |
| `7c8a816` | 3.21 | SEC adapter cleaned-text fix + document_title heuristic fix + reprocess script |
| `f730349` | hotfix | `db.py` migrations completeness — Drops 3.16/3.18/3.19 column additions back-ported to existing-DB upgrade path |
| `1772289` | 3.20b | Cross-source observation tracking + diff surfacing |
| `4852935` | 3.20a | Agreement extraction (5 section prompts, `transaction_security`, party resolution, capitalization, termination, conditions) + SEC window tightening + `document_title` capture |
| `dd3aeda` | 3.19 | SEC filing expansion: DEFM14A / SC TO-T / S-4 / 8-K Exhibit 2.1, document storage, section tagging |
| `f37ad41` | 3.18 | Multi-transaction PR splitting at HC extraction |
| `69dc23a` | 3.16 | Earnout and CVR capture in `consideration_components`, `has_earnout` / `has_cvr` flags |
| `169c063` | 3.14/3.15/3.17 | Date format in summaries, `transaction_status` derivation, `is_de_spac` flag (bundle) |
| `59f4f9a` | 3.13 | `deal_summary` v0.6: narrative rewrite weaving descriptions / sponsors / multiples / advisors |
| `db2569f` | 3.12 | Valuation multiples derivation (EV/Revenue, EV/EBITDA, LTM+NTM) with NM display rule |
| `1e9f1d0` | 3.11 | `value_type` determination rules (HC v0.7): equity vs transaction value |
| `d91916e` | 3.10 | `acquirer_sponsor_name` field + add-on detection improvements (HC v0.6) |

### 3.2 Cycle-close production run (run_20260505_112348)

**Runtime:** 1h 50m  **API cost:** ~$25  **Mode:** full (MAX_FETCHES=100)

| Metric | Value |
| :--- | :--- |
| Sources fetched | 100 |
| Relevant | 85 |
| Staging extractions (includes multi-PR splits) | 167 |
| Clusters formed | 154 |
| Merged duplicates | 18 |
| Transactions created | 154 |
| Summaries generated | 153 |
| Rationales tagged | 153 |
| Failures | 5 |
| Transactions exported (all is_current=1 records) | 226 |

**Validation findings by drop:**

- **Drops 3.10–3.17:** Confirmed working. Deal type distribution, consideration derivation, flags, summaries, rationales all producing expected output.
- **Drop 3.18 (multi-transaction PR splitting):** 5 of 168 `source_raw` rows produced multi-row splits on real PRs.
- **Drop 3.19 (SEC filing fetch):** 45 staging_extraction rows TRIGGERED → 18 unique transactions in Stage 10 → 4 documents fetched (2× S-4, 1× DEFM14A, 1× 8K_ITEM_201). Architectural defect confirmed: `fetch_filing_full_text()` stored raw SGML submission package instead of cleaned plain text, producing `sections=0` on three documents. Fixed by Drop 3.21.
- **Drop 3.21 (SEC cleaned-text fix):** 3 documents reprocessed post-fix. Two S-4s yielded 937K and 1.2M chars. Agreement-extract ran; 2 transactions EXTRACTED, 7 observations written (all from `agreement_conditions` — CONDITIONS_TO_CLOSING only; CONSIDERATION/TERMINATION_FEE not yet tagged from S-4 shell structure).
- **Drop 3.22 (exhibit fix):** `_find_exhibits()` regex corrected + dual-filer 8K path for public-public deals. Backfill recovered 10 exhibits across 8 transactions.
- **Drops 3.22a–3.22c:** adapter + reconciler bugs surfaced by Drop 3.22 extraction output. Closed sequentially; no regressions.

**SEC enrichment / agreement extraction state (post-Drop 3.22c):**

| Metric | Value |
| :--- | :--- |
| SEC-triggered transactions | 18 |
| Documents stored (`transaction_document`) | 14 current (10× 8K_EXHIBIT_21, 2× S4, 1× DEFM14A, 1× 8K_ITEM_201) |
| Transactions with `agreement_extraction_status='EXTRACTED'` | 8 |
| `transaction_document_section` rows | 32 |
| `transaction_field_observation` rows (current / total) | 26 / 50 |
| `transaction_security` rows | 0 (CAPITALIZATION sections not yet tagged — Drop 3.23/3.24 scope) |
| `merger_structure` distribution | REVERSE_TRIANGULAR (1), FORWARD_TRIANGULAR (1), DIRECT (1) |

Note on 8K_EXHIBIT_21 dual-filer: two transactions (tc_8a6144513ebf, tc_56312904df23) have 2 documents each — one per SEC filer in a public-public deal. Same merger agreement, distinct accession numbers.

### 3.3 Acceptance Criteria — status

Most criteria pending gold-set labeling against cycle-close run. The 100-PR acceptance targets from prior runs remain the reference; no new scoring run completed against run_20260505_112348.

---

## 4. What Needs to Be Addressed

Ordered by priority for a fresh session.

### 4.1 Immediate

**A. Drop 3.23 — S-4 / DEFM14A parent-section navigation.** Current section tagger doesn't scope CONSIDERATION/CAPITALIZATION/TERMINATION_FEE patterns to the embedded "THE MERGER AGREEMENT" parent section in S-4 / DEFM14A documents. Brings transaction_security extraction and termination-fee extraction online for stock-deal documents. Validates against Stellar/Prosperity and Essential/AWWC S-4s already in the production DB — no re-fetch needed. ~1 day.

**B. Drop 3.24 — Exhibit 2.1 structural navigation.** Replace classification-based section tagging with structure-based navigation for 8K_EXHIBIT_21 documents. Article-heading anchors (`Article [Roman]`, `ARTICLE [N]`, ToC links) → canonical section lookup. More reliable than per-clause classification; eliminates the ERISA / Form-of false positives that motivated the 3.22b RECITALS heuristics. ~1–1.5 days.

**C. Gold set labeling against the cycle-close run.** Operator task. Acceptance-criteria measurement blocked without it.

**D. Strategic rationale enhancement.** Two-layer rationale (standardized tags + narrative drivers + themes + risks). Highest-value design gap independent of SEC workstream. ~3–5 days. Useful for private deals where rationale lives in PR text.

**E. Cost optimization A/B.** Sonnet substitution on summarize / rationale_tag / aggregate stages. Produces real operating-economics data. ~$25 + ½ day.

### 4.2 Known deferred — MVP-acceptable limitations

- **Sec-retry mode.** When PRs are processed same-day they're issued, the corresponding 8-K often hasn't been filed yet. Implement `--mode=sec-retry` that re-queries sec-api.io for `sec_lookup_status = NO_MATCH` rows whose `announced_date` is 5+ business days old. Not yet implemented.
- **Item 2.02 fallback for earnings-release-disclosed deals.** Confirmed pattern via Honeywell AIP divestiture. Fallback to Item 2.02 after other items return NO_MATCH; gate by Haiku content filter confirming the 2.02 text references the specific target transaction. Not yet implemented. Estimated 1–2 days.
- **PDF exhibit text extraction.** Exhibit 2.1 or 99.x returning as PDF logs UNREADABLE and skips. Defer to V2.
- **Close-announcement linking (lifecycle management).** See §5.1.
- **Pre-execution draft documents.** Surfaced in Drop 3.22b validation (Campbell Lutyens `[PURCHASER]` placeholder in unsigned draft). Decide whether to filter ingestion of pre-execution drafts or accept and flag. Deferred.
- **Adapter deal-matching filter + backward window for close-PR deals.** Needed to correctly link signing-era filings when the pipeline first sees a deal at close announcement. Backward window without deal-matching risks mis-attribution for active acquirers. Two-step fix: (1) deal-matching filter before filing linkage; (2) backward window gated on step 1.

### 4.3 Open patches awaiting decision

None pending — Drop 3.22 → 3.22a → 3.22b → 3.22c sequence closed.

---

## 5. Strategic Directions (V2)

### 5.1 Transaction Lifecycle Management

Today the pipeline treats each PR as a discrete event. A CLOSE PR arriving weeks after an ANNOUNCEMENT creates a new orphan CLOSE record. V2 architecture adds a cross-run linking stage. Highest-impact V2 improvement.

### 5.2 Source Comprehensiveness for Public-Party Deals

Drop 3.19 made meaningful progress (S-4, DEFM14A, SC TO-T, Form 425, Exhibit 2.1 now in scope). Remaining gaps: 8-K Items 2.02, 7.01, 9.01; Form 6-K (foreign private issuers); company IR page scrape; earnings call transcript extraction.

### 5.3 Pool-Extraction Architecture

Future architecture: gather full source pool per deal, extract once against the pool. Enables securities-level consideration capture, higher fill rate on target financial periodicity, richer strategic rationale from earnings calls. Gated on §5.2.

### 5.4 PE Deal Hierarchy Schema [SUBSUMED BY §5.8]

Original observation (Bridge → TransTech → CTR → Allcryo) and proposed `acquirer_parent_platform_name` + `acquirer_ultimate_sponsor_name` columns. The deeper conversation in §5.8 supersedes this with a normalized entity model.

### 5.5 GlobeNewswire Adapter

Second wire service. The pipeline is source-agnostic (`source_type` enum accepts new values). RSS + body scrape, no API needed. Validated post-first-run; defer to after production run.

### 5.6 Cross-Lingual Entity Resolution

Observed multilingual dedup worked in the 100-PR run (DE/FR/ES/EN variants merged correctly via token-based fuzzy match). Current approach has no unicode normalization. V2: NFD unicode normalization + diacritic strip.

### 5.7 Domain-Based Entity Resolution

Current clustering uses name + date only. Schema already captures `target_domain` and `acquirer_domain`; clustering logic doesn't use them. V2: add domain match as tiebreaker when names are ambiguous.

### 5.8 Transaction Entity Model (V2 architectural)

**Surfaced by:** EA / Oak-Eagle take-private (cycle-close run). Three sponsors at the consortium layer (PIF + Silver Lake + Affinity), Parent vehicle (Oak-Eagle AcquireCo, Inc.), Merger Sub (Oak-Eagle MergerCo, Inc.) — five named entities in one transaction's preamble.

**Current schema:** each entity role is a string column on `transaction_record` (`target_name`, `acquirer_name`, `parent_acquirer_name`, `acquirer_sponsor_name` comma-delimited per Drop 3.10). Adds a column per role; breaks on cardinality > 1; mixes legal-structural entities (Merger Sub, AcquireCo) with display entities (sponsor, target, strategic acquirer).

**Proposed:** `transaction_entity` table with `(transaction_id, entity_id, role, role_layer, display_contexts)`. Each entity has a legal role (PARENT, MERGER_SUB, SPONSOR, ACQUIRER, TARGET, SELLER, etc.) and display-context tags indicating which downstream views the entity is relevant to: HEADLINE, REGULATORY, FINANCING, OWNERSHIP, LITIGATION.

**Why this matters beyond display:**
- Merger Subs and intermediate AcquireCos are the named parties on HSR filings, debt issuances, state regulatory approvals, 13D/G filings, litigation captions. Capturing them is structurally necessary, not optional.
- Private credit borrowers are typically the same Merger Sub / AcquireCo entity — shared `entity_id` enables cross-product joins (M&A transaction → credit facility) without entity re-resolution.
- Sponsor-level rollups, regulatory entity-of-record tracking, and financing-counterparty tracking are distinct query patterns that single-string columns can't serve.
- Cardinality at the consortium layer (PIF + Silver Lake + Affinity) and at the seller layer (multi-seller divestitures) is structurally common in private markets, not edge cases.

**Subsumes §5.4 PE Deal Hierarchy.** Replaces several string columns on `transaction_record` rather than supplementing them.

**Effort:** high. Migration of existing string-column data into entity rows. Extraction-layer rewrite — extraction captures all named entities, not one per role. Display-layer queries by context tags rather than reading flat columns.

**Priority:** high. Foundational for private-markets coverage and for cross-product entity joins the broader engagement requires.

---

## 6. Known Patterns and Non-Issues

- **Sub-80 word summaries.** When a deal's source data is sparse (UNDISCLOSED value, no advisors, no financials), the summary prompt correctly produces short summaries. Word-floor warnings are log noise, not data loss.
- **`consideration_type` null rate ~78%.** Expected for middle-market private deals. Terms are almost never disclosed.
- **No TALENT_ACQUISITION rationale tags.** PR Newswire M&A coverage skews product/capability + geographic. Acqui-hires are rarely wire-distributed.
- **SEC NO_MATCH rate.** Some NO_MATCH rows are legitimately private-to-private deals where the public-party detector tripped on boilerplate ticker mentions. Normal.
- **Canonical field flips on agreement-extract reruns.** When 8K_EXHIBIT_21 data arrives via reprocessing, priority rules re-derive canonical values from the broader observation set. Mechanical re-derivation (same value, different source) is routing — no concern. Observation divergence populates `observation_changes_summary` and is a data-quality signal worth review.
- **Soft-deleted observations clearing canonical fields.** Drop 3.22c made this explicit: when filtered observations leave a field with no current observations, `_clear_stale_canonical_fields()` NULLs the corresponding `transaction_record` column. Prior behavior was silent stale-value persistence.
- **Dual-filer 8K_EXHIBIT_21 rows.** For public-public deals, both filers' submission packages produce `transaction_document` rows — same merger agreement, distinct accession numbers, possibly redacted differently. Drop 3.20b reconciliation handles cross-filer divergence in the same way as cross-source-type divergence.
- **`has_mac_clause` schema default 0.** For transactions never processed by Stage 11, `has_mac_clause=0` is the DB default, not an extracted false observation. After a Stage 11 run that finds no CONDITIONS section, `_clear_stale_canonical_fields()` NULLs this to "not observed." The 0 → NULL transition is correct behavior on first extraction.

---

## 7. Development Style & Conventions

### 7.1 Repo structure

```
ma-collection-mvp/
├── README.md
├── mvp_goal_and_schema.md
├── specs/  (adapter_pr_newswire, adapter_sec_api, pipeline, entity_resolution, evaluation)
├── prompts/  (13 prompt files + base.py)
├── schema/  (migration files per drop)
├── lib/  (section_tagger.py)
├── eval/  (gold_set_template.csv, score.py)
├── stages/  (14 stage modules)
├── adapters/  (pr_newswire.py, sec_api.py)
├── scripts/  (validate_adapters.py, test_agreement_extract_filters.py, backfill_8k_exhibit21.py, ...)
├── docs/  (project_state.md)
├── config.py, db.py, logger.py, run.py
├── .env (gitignored), .env.example
└── .gitignore
```

Runtime-generated (gitignored): `data/`, `exports/`, `logs/`, `notes/`.

### 7.2 Drop numbering

Drops are spec/prompt revision bundles, labeled sequentially. Each lands as a single commit. Patch notes (`drop_X_Y_patch.md`) are instructions to Claude Code; they are NOT committed to the repo.

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

When re-running the pipeline at a specific stage, the operator clears downstream tables manually:

| Re-run from | Tables to clear | Tables to preserve |
| :--- | :--- | :--- |
| `--mode=full` (fresh DB) | All tables | None |
| `--mode=aggregate` (Stages 8–9) | `transaction_record`, `transaction_source`, `aggregation_conflict_log`. Roll `staging_extraction.status` back to `LC_EXTRACTED`. | `advisor`, `staging_extraction.consideration_components`, `summary`, `rationale_tag`, `source_raw` |
| `--mode=generate` (Stages 12–13) | Flip `summary.is_current=0`, `rationale_tag.is_current=0` | All else |
| `--mode=rerun-prompt` | None — new outputs flip prior `is_current` to 0 | All |
| `--mode=agreement-rerun` | Set `agreement_extracted_at=NULL` on target documents | All observations (soft-delete handles per-doc cleanup) |

**Validation runs use a separate DB path.** Never use `data/ma_mvp.db` for test runs. Use `data/ma_mvp_test.db` or any other path. This is a hard convention; failure to maintain it cost the 100-PR DB state during Drop 3.9 validation.

---

## 8. Next Session Kickoff

Recommended first message to a fresh session:

> I'm Erik. I'm working on an M&A data collection MVP. Please read `docs/project_state.md` first — it's the handoff document for the project. Then read `README.md` and the `specs/` folder.
>
> The pipeline just completed a cycle-close production run (`run_20260505_112348`, 226 transactions, ~$25, 1h 50m) with the Drop 3.19/3.20a SEC document extraction stack online. Drops 3.21 → 3.22 → 3.22a → 3.22b → 3.22c closed out adapter and reconciler bugs surfaced by that run.
>
> Queued work:
> 1. Drop 3.23 — S-4 / DEFM14A parent-section navigation (CONSIDERATION/CAPITALIZATION sections)
> 2. Drop 3.24 — Exhibit 2.1 structural navigation (article-anchor lookup, replaces classification for Exhibit 2.1)
> 3. Strategic rationale enhancement (two-layer rationale + narrative drivers)
> 4. Cost optimization A/B (Sonnet substitution on summarize/rationale/aggregate)
> 5. Gold set labeling against the cycle-close run (operator task)
>
> §5.8 entity-model V2 is logged but not in immediate scope.
>
> After you've read the docs, give me a summary of what you understood and propose the order for the immediate work.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-23 | Initial handoff document. Snapshot after 100-PR production run. |
| 0.2 | 2026-04-23 | Post-Stage 8 patch: resolved null-date hash collision, 78/76 cluster gap closed. Layer 5 (eval/score.py) and Stage 8 fix added to commit log. |
| 0.3 | 2026-05-02 | Drop 3.19: sec_documents stage (Stage 10), pipeline expanded to 13 stages. |
| 0.4 | 2026-05-04 | Drop 3.20a: pipeline expanded to 14 stages, agreement_extract stage, transaction_security. |
| 0.5 | 2026-05-04 | Drop 3.20b: transaction_field_observation table, observation diff columns. |
| 0.6 | 2026-05-05 | Iteration cycle closed through Drop 3.21. Cycle-close run results. Schema v1.0. |
| 0.7 | 2026-05-05 | Rewrite through Drop 3.22c. All VERIFY flags resolved from git log / DB. SHAs 3.10–3.22c filled. Prompt table updated to 13 files with current versions. Stage module names confirmed from run.py. Run modes updated to 11. 8K_EXHIBIT_21 corrected to 10 docs / 8 transactions. Section types corrected (OTHER not MISCELLANEOUS; full 10-type list). Cycle-close metrics from run_log. Sec-retry and Item 2.02 confirmed still deferred. §5.8 entity model added. §6 updated with 3.22c patterns. |
