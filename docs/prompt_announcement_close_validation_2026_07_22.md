# Prompt Announcement/Close Validation - 2026-07-22

## Purpose

Validate the prompt update that clarifies `ANNOUNCEMENT` vs `CLOSE` handling
for first-observed completed private deals, advisor tombstones, and pending
public-company transactions.

## Scope

- Provider: Anthropic, using the locally configured `.env`.
- Local DB only: `data/prompt_validation_20260722_r2.db`.
- Run ID: `prompt_validation_20260722_r2_run_20260722_154146`.
- Mode: `extract`.
- Production DB: untouched.
- Schema changes: none.
- Stage 9 changes: none.
- Prompt versions under validation:
  - `deal_type_classifier:0.5`
  - `high_confidence_extraction:0.10`

The first validation pass found a local seed-date issue for the syndicated Utz
BusinessWire copy. The row was corrected from `2026-07-20` to the actual
publication date, `2026-07-21`, and the validation was rerun on a fresh local
DB.

## Seed Sources

Six source rows were imported into `source_raw` through the TSV importer so
they would pass through the normal relevancy, deal-type classification, and
high-confidence extraction stages.

| Source | Input handling |
| :--- | :--- |
| Summit Professional Education / Kids Bowel & Bladder | Direct PRNewswire fetch |
| TKO Miller / CD Energy Services / ConTeras | Direct PRNewswire fetch |
| Warburg Pincus and Kayne Anderson / WildFire / Magnolia | Direct PRNewswire fetch |
| TrueLink Capital / Lyons Magnus | Direct PRNewswire fetch |
| Greenberg Traurig / The Gores Group / Imagine / Lumine | Corrected PRNewswire URL (`Gores` / `Imagine`) |
| Utz / Intersnack | Syndicated BusinessWire copy because direct BusinessWire automated fetch returned 403 |

## Results

| Source | Classifier event_type | announced_date | closed_date | Result |
| :--- | :--- | :--- | :--- | :--- |
| Summit / Kids Bowel & Bladder | `ANNOUNCEMENT` | `2026-07-16` | `2026-07-16` | Pass |
| TKO Miller / CD Energy / ConTeras | `ANNOUNCEMENT` | `2026-07-21` | `2026-07-21` | Pass |
| Warburg / WildFire / Magnolia | `ANNOUNCEMENT` | `2026-07-21` | null | Pass |
| TrueLink / Lyons Magnus | `ANNOUNCEMENT` | `2026-07-20` | `2026-07-20` | Pass |
| Greenberg Traurig / Gores / Imagine / Lumine | `ANNOUNCEMENT` | `2026-07-16` | `2026-07-16` | Pass |
| Utz / Intersnack | `ANNOUNCEMENT` | `2026-07-21` | null | Pass |

Run summary:

- Source rows imported: 6.
- Relevant rows: 6.
- Deal-type classifications: 6.
- High-confidence PR/source extractions: 6.
- SEC attached source extractions: 2.
- Low-confidence extractions: 8.
- Prompt failures: 0.
- Advisor rows inserted: 22.

The two public-company examples, Warburg/WildFire/Magnolia and
Utz/Intersnack, triggered SEC enrichment on the local DB and queued two
`SEC_EXHIBIT_99` attached sources. Those rows also extracted without prompt
failure, but the acceptance check above is based on the six original seed
sources.

## Observations

- The classifier no longer turns first-observed completed private deal language
  into `CLOSE`.
- Pending-close language correctly kept Warburg/WildFire/Magnolia and
  Utz/Intersnack without `closed_date`.
- Advisor tombstone patterns correctly remained `ANNOUNCEMENT` while still
  allowing same-day `closed_date`.
- No production data was touched.

## Deferred

- The test used Anthropic because that is the current locally configured live
  provider. OpenAI live validation remains deferred until the local runtime has
  an enterprise OpenAI API key.
- The direct BusinessWire URL returned 403 to automated fetch; this validation
  used a syndicated copy for source text.
- SEC enrichment behavior was observed but not changed in this prompt patch.
