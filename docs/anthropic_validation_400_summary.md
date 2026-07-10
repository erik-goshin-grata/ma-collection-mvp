# 400-Story Anthropic Validation Summary

Date: 2026-07-09

## Purpose

This validation run tested the M&A collection pipeline against a larger, stored set of news stories from a TSV file. The goal was to validate the pipeline in POC mode before using a future news firehose API.

This run used Anthropic only. No prompts, schemas, extraction semantics, aggregation logic, or data model changes were made for the validation itself.

## What We Added

Two implementation pieces were added for this exercise:

- `scripts/import_tsv_sources.py`
  - Imports TSV rows into `source_raw`.
  - Uses the story title, full story body, source URL, and effective date.
  - Preserves TSV source metadata in `source_raw.notes`, including category, party 1, party 2, party 1 domain, and party 2 domain.
  - Supports limiting imports for controlled validation batches.

- `stages/export.py` updates
  - Adds advisor fields to the CSV export only.
  - Adds source review fields to the CSV export, including source URL, source title, source category, source parties, and source party domains.
  - Does not add advisors or source-party metadata to the main transaction table.

The primary export from this run is:

- `exports/transactions_anthropic_validation_400_run_20260709_090917.csv`

Additional audit files were created under:

- `exports/validation_review_400/`

## Input Data

The input file represented roughly one month of news stories:

- Input file: `data/input_stories/news_events_full_review_2026-06-08_to_2026-07-08.tsv`
- Input rows: 3,089
- Relevant fields used:
  - `source_title`
  - `source_body_lite`
  - `source_url`
  - effective date
  - party 1
  - party 2
  - party 1 domain
  - party 2 domain
  - source category

This was not a completely raw news firehose. It was already an M&A-flavored source set from upstream story selection.

## Dedupe and Sampling

Before sampling, duplicate stories were removed using the story body text. In practice, this means rows with the same body hash were treated as duplicates.

Results:

- Original rows: 3,089
- Duplicate body rows removed: 179
- Unique rows after body dedupe: 2,910

A deterministic 400-story sample was created from the deduped set using seed:

- `ma-collection-month-sample-400-v1`

Sample allocation:

| Source category | Sample rows |
| --- | ---: |
| acquires | 224 |
| sells_assets_to | 133 |
| merges_with | 29 |
| spins_off_company | 8 |
| spins_off_division | 6 |

Sample file:

- `data/input_stories/news_events_full_review_2026-06-08_to_2026-07-08_sample_400.tsv`

Manifest:

- `data/input_stories/news_events_full_review_2026-06-08_to_2026-07-08_sample_400_manifest.json`

## Pipeline Results

The 400-story sample completed the full pipeline: import, extract, aggregate, summarize, rationale tag, and export.

Headline results:

| Stage | Result |
| --- | ---: |
| TSV stories imported | 400 |
| Relevant stories | 286 |
| Not relevant stories | 86 |
| Relevancy failures | 28 |
| Current transaction records | 277 |
| Summaries generated | 277 |
| Rationale tags generated | 277 |
| Advisor records extracted | 106 |
| Export rows | 277 |

Aggregation:

| Metric | Result |
| --- | ---: |
| Final transaction records | 277 |
| Duplicate clusters merged | 5 |
| Aggregation conflicts resolved by LLM | 20 |
| Conflicts flagged for review | 3 |

SEC enrichment:

| SEC outcome | Count |
| --- | ---: |
| Not triggered | 269 |
| Triggered and matched | 5 |
| Triggered but no match | 10 |
| Runtime/API errors | 0 |

## Export Coverage

The export includes the new review fields:

- Advisor columns:
  - target financial advisors
  - target legal advisors
  - acquirer financial advisors
  - acquirer legal advisors
  - parent/seller financial advisors
  - parent/seller legal advisors
  - both/other/unknown advisor buckets
  - `advisors_json`

- Source review columns:
  - source URLs
  - source titles
  - source categories
  - source party 1 names and domains
  - source party 2 names and domains
  - `source_metadata_json`

Export validation:

- 277 exported rows.
- `advisors_json` populated on 47 transaction rows.
- Source URL/title/category fields populated on all 277 rows.
- Party 1 and party 2 domain fields populated on all 277 rows.

## What We Saw

### Extraction Was Mostly Straightforward

The core extraction machinery held up well. The system extracted structured deal data, advisors, summaries, rationales, and SEC enrichment artifacts across a much larger sample than the first 10-story validation.

The harder issues were not basic extraction. They were judgment, taxonomy, and workflow policy issues.

### Judgment and Classification Need Review

Several important cases surfaced:

- Buyer-side acquisition vs seller-side divestiture/carve-out:
  - Example pattern: a company sells a business unit or asset. The buyer-side view is an acquisition, but the seller-side view may be a carve-out or divestiture.
  - We likely need clearer policy for when to represent this as acquisition, divestiture, or both.

- Follow-up events:
  - Merger approvals, deal closes, amendments, and regulatory milestones should not be discarded.
  - If a transaction already exists, these should attach as new sources and trigger review/update logic.
  - If a transaction does not exist and enough dates/parties are present, the system may need to create a new transaction record even if the story is not the initial announcement.

- Source-party interpretation:
  - The TSV provides party 1 and party 2, but those parties may represent buyer/seller/parent rather than target/acquirer.
  - Many apparent mismatches are explainable when the parsed target is an asset, property, portfolio, or business unit.

### Relevancy Failures Were Mostly Schema Brittleness

There were 28 relevancy failures. These were not generally stories deemed irrelevant. The model often returned `classification = RELEVANT`, but with a reason code outside the allowed enum.

Invalid reason-code counts:

| Invalid reason code | Count |
| --- | ---: |
| MERGER | 10 |
| SPIN_OFF | 8 |
| EXECUTIVE_APPOINTMENT | 4 |
| ASSET_PURCHASE | 1 |
| SPIN_OFF_OR_SPLIT_OFF | 1 |
| ASSET_SALE_OR_PURCHASE | 1 |
| EARNINGS_OR_GUIDANCE | 1 |
| ASSET_SALE | 1 |
| REBRAND_OR_NAME_CHANGE | 1 |

Interpretation:

- `MERGER` should likely normalize to `MERGER_ANNOUNCEMENT`.
- `SPIN_OFF` should likely normalize to `SPIN_OFF_OR_SPLIT`.
- Asset-sale and asset-purchase labels may map to carve-out/divestiture or acquisition depending on story framing.

This is a schema normalization issue more than a relevance-judgment failure.

Review file:

- `exports/validation_review_400/relevancy_failures.csv`

### Irrelevant Stories Had Reasons

The 86 not-relevant stories did receive reason codes and notes.

Reason-code counts:

| Reason | Count |
| --- | ---: |
| OTHER_NOT_RELEVANT | 60 |
| PRODUCT_OR_COMMERCIAL | 19 |
| DEBT_OR_NON_DEAL_FINANCING | 3 |
| RUMOR_OR_SPECULATION | 2 |
| IPO_OR_DIRECT_LISTING | 2 |

Interpretation:

- The notes are useful, but `OTHER_NOT_RELEVANT` is broad.
- For production review, we may want more specific exclusion labels for real estate, sponsorship, industry commentary, policy/regulatory stories, historical retrospectives, and multi-deal newsletters.

Review file:

- `exports/validation_review_400/irrelevant_reasons.csv`

### Announcement Dates Need Attention

85 exported transactions were missing `announced_date`.

Breakdown:

| Event type | Count |
| --- | ---: |
| CLOSE | 78 |
| ANNOUNCEMENT | 7 |

77 of the 85 had a `closed_date`.

Interpretation:

- Some stories are legitimately close-only or completion-focused.
- However, 85 missing announcement dates out of 277 exported transactions is high for this sample size.
- The system may be overusing `closed_date`, failing to use the story date as a fallback announcement/effective date, or classifying update/announcement stories as closes.

Review file:

- `exports/validation_review_400/missing_announcement_dates.csv`

### SEC Enrichment Worked, But Match Rate Was Limited

15 rows triggered SEC lookup.

Results:

- 5 matched SEC filings and inserted SEC source rows.
- 10 had no match.
- 0 had SEC runtime/API errors.

Matched examples included:

- TTM Technologies / Swiss Technology Group
- EXL / iMerit
- MacroGenics / Bora Pharmaceuticals
- Standex / Narayan Powertech

Important nuance:

- SEC enrichment attached 8-K item text and press-release exhibits where found.
- It did not necessarily find or attach a filed transaction agreement.
- For example, the EXL/iMerit filing is an Item 8.01 narrative disclosure. It states that a securities purchase agreement was entered into and provides useful structure details, but the purchase agreement itself was not filed as an exhibit in this run.

Official-source trigger gap:

- Olin/Huntsman is a useful example. The redistributed story contained enough information for consideration extraction to identify a stock-for-stock merger structure, but it did not trigger SEC enrichment.
- The issue is not primarily that the article extraction missed consideration. The issue is that a redistributed or secondary source is a weak official-record signal.
- In production, once a transaction involves likely public-company parties, SEC feed monitoring or party-based CIK/ticker resolution should become the primary official-source trigger. The article can still seed the party/date search, but SEC filings should carry the official-source confirmation and detail enrichment burden.
- This matters especially for older redistributed stories, public-company merger announcements, and cases where the story text does not contain explicit `NYSE`, `NASDAQ`, `Form 8-K`, or SEC-language cues.

Review file:

- `exports/validation_review_400/sec_attempts.csv`

### Classification and High-Confidence Failures Were Concentrated

There were 10 combined classification/high-confidence failures:

| Failure type | Count |
| --- | ---: |
| Classification failures | 5 |
| High-confidence extraction parse failures | 5 |

Classification failures:

- All 5 were invalid `event_type = None/blank`.
- These tended to be ambiguous or off-policy stories, such as roundups, sports transfer stories, and non-core M&A items.

High-confidence failures:

- All 5 were parse errors: response was not parseable JSON.
- They clustered around difficult inputs:
  - historical retrospective
  - speculative sale process
  - bundled multi-target acquisition
  - SPAC/reverse merger
  - multi-deal roundup

Review file:

- `exports/validation_review_400/classification_and_high_confidence_failures.csv`

## Production Implications

### Batch the Work

The 400-story run completed, but it is not ideal to treat hundreds of stories as one long operational unit.

Recommended production pattern:

1. Ingest and dedupe the full story set.
2. Run relevancy/classification/extraction in controlled batches, such as 100 to 150 stories.
3. Run clustering and aggregation globally after all extraction batches complete.
4. Run summaries, rationale tagging, and export globally.

Reasoning:

- Easier restart if one model call stalls.
- Clearer cost and progress reporting.
- Earlier visibility into failure patterns.
- Lower risk that one slow story holds up the entire run.
- Global aggregation still catches duplicates and follow-up stories across batches.

### Treat Judgment as a First-Class Layer

The POC suggests the major production work is not just extracting fields. It is defining how stories should update the transaction graph.

Areas needing explicit policy:

- Initial announcement vs close vs approval vs amendment.
- Follow-up source attachment to an existing transaction.
- Buyer-side acquisition vs seller-side divestiture/carve-out.
- Asset/property/business-unit deals vs whole-company transactions.
- Multi-deal articles and roundups.
- Source-party roles, especially buyer/seller/parent/asset/portfolio.

### Add Enum Normalization

Near-miss labels should not necessarily fail the row. Examples:

- `MERGER` -> `MERGER_ANNOUNCEMENT`
- `SPIN_OFF` -> `SPIN_OFF_OR_SPLIT`
- `ASSET_SALE` or `ASSET_PURCHASE` -> review or normalize based on buyer/seller framing

This should reduce false failures without changing the core extraction task.

## Review Artifacts

The following audit files were created for follow-up review:

- `exports/validation_review_400/missing_announcement_dates.csv`
- `exports/validation_review_400/classification_event_type_failures.csv`
- `exports/validation_review_400/relevancy_failures.csv`
- `exports/validation_review_400/irrelevant_reasons.csv`
- `exports/validation_review_400/sec_attempts.csv`
- `exports/validation_review_400/source_party_mismatch_review.csv`
- `exports/validation_review_400/classification_and_high_confidence_failures.csv`

## Bottom Line

The larger validation run was successful as a POC. The pipeline handled a 400-story sample, produced 277 transaction exports, preserved advisor and source-domain review data, and completed summaries/rationale tagging.

The main lesson is that production quality will depend on judgment policy and workflow design:

- better relevancy reason normalization
- clearer event-type/date handling
- better source-party role interpretation
- batch-oriented processing
- global aggregation after batch extraction
- review/update handling for follow-up stories

The extraction layer is usable. The classification and transaction-graph decision layer is where the next tuning effort should focus.

## Follow-Up TODO

Near-term review:

- Review the validation exception CSVs before making prompt or pipeline changes.
- Pay particular attention to:
  - missing announcement dates
  - relevancy failures caused by near-valid labels
  - close/update stories that should attach to existing transactions
  - buyer-side acquisition vs seller-side divestiture/carve-out framing
  - source-party mismatches where source parties represent buyer/seller/parent rather than target/acquirer
  - high-confidence parse failures on historical, speculative, bundled, SPAC, or roundup stories

Prompt and classification improvements to consider after review:

- Normalize near-miss relevancy labels instead of failing rows outright.
- Clarify event-type guidance for announcement, close, approval, amendment, termination, and follow-up updates.
- Clarify when seller-side business-unit sales should be treated as divestitures/carve-outs versus buyer-side acquisitions.
- Add better handling for merger approvals and other follow-up sources that should update an existing transaction or trigger review.
- Add clearer guidance for multi-deal roundups: split, skip, or route to manual review.

Pipeline improvements to consider after review:

- Process large source sets in smaller extraction batches, then run global clustering and aggregation after all batches complete.
- Add better progress/retry management for slow or stalled model calls.
- Preserve a batch/run identifier for operational reporting and restartability.
- Add a source-update path for follow-up stories that attach to existing transactions.
- Distinguish transaction-identified SEC enrichment from proactive SEC monitoring.

SEC enrichment improvements to consider:

- Add proactive SEC feed monitoring or post-identification CIK/ticker lookup for likely public-company transactions, even when the source article does not contain explicit SEC/ticker language.
- Capture and display document labels clearly, such as `8-K Item 8.01 Other Events`, `EX-99.1 Press Release`, `EX-99.2 Investor Presentation`, and `EX-2.1 Purchase Agreement`.
- Classify `EX-99.x` subtypes: press release, investor presentation, conference call script, transaction fact sheet, supplemental financial information, or other.
- Add a dedicated extraction path for `8-K Item 8.01` narrative disclosures.
- Use investor presentations as researcher sources; extract text by default and reserve OCR/vision parsing for priority transactions, weak text extraction, or researcher request.
- Treat earnings releases that mention transactions as lower-confidence transaction sources unless they contain enough concrete deal terms to create or update a record.
