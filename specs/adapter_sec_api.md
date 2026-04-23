# sec-api.io Adapter Spec

**Version:** 0.2 (revised)
**Repo path:** `specs/adapter_sec_api.md`

---

## 1. Purpose

Enrich PR-discovered transactions where a public party is detected. The adapter fetches the corresponding 8-K filing and retrieves:

- **Item text** (via the Extractor API) — the filer's own narrative of the deal, conditioned on the PR's event type
- **Exhibit 2.1** — the merger agreement, when present
- **Exhibit 99.x** — the press release(s) attached to the filing, when present

Produces one or more rows in `source_raw` per enriched transaction, each tagged `source_tier = T1`.

The adapter is **triggered per-transaction** after initial PR extraction identifies a public party. It is not a standalone discovery channel in MVP — all discovery flows through PR Newswire.

---

## 2. Inputs

### 2.1 API Base and Endpoints

Three sec-api.io endpoints are used:

| Endpoint | URL | Purpose |
| :--- | :--- | :--- |
| Filing Query API | `https://api.sec-api.io` (POST, JSON body) | Finds matching 8-K filings. Returns filing metadata including the list of items present and the list of exhibits. |
| Extractor API | `https://api.sec-api.io/extractor` (GET) | Returns clean text or HTML for a named item of a 10-K, 10-Q, or 8-K filing. Items only — not exhibits. |
| Filing & Exhibit Download API | `https://archive.sec-api.io/<accession_path>` (GET) | Retrieves raw bytes of any document or exhibit in a filing. Used for Exhibit 2.1 and Exhibit 99.x. |

### 2.2 Authentication

API key passed via `token` query parameter on all three endpoints. Key stored in `.env` as `SEC_API_KEY`.

### 2.3 Trigger Inputs (per transaction)

Provided by the orchestrator from the `staging_extraction` row that triggered enrichment:

- `target_name` and/or `acquirer_name`
- `target_domain` and/or `acquirer_domain` (if extracted)
- `announced_date` (ISO 8601)
- `event_type` — {ANNOUNCEMENT, CLOSE, AMENDMENT, TERMINATION}
- Public-party side: `TARGET`, `ACQUIRER`, or `BOTH`
- Any ticker(s) detected in the PR text

### 2.4 Configuration (from `.env`)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `SEC_API_KEY` | (required) | Authentication |
| `SEC_API_REQUEST_DELAY_SECONDS` | 0.2 | Throttle between outbound requests |
| `SEC_DATE_WINDOW_DAYS` | 7 | Filing search window around `announced_date` |
| `SEC_EXTRACTOR_MAX_RETRIES` | 3 | Retry count on "processing" status |
| `SEC_EXTRACTOR_RETRY_DELAY_MS` | 750 | Delay between retries on "processing" |

---

## 3. Public-Party Detection (Trigger Logic)

Detection runs on the PR's `clean_text` before the adapter is called. Signals that trip the trigger:

- **Exchange + ticker pattern:** regex `\b(NYSE|NASDAQ|NYSE American|OTCQB|OTCQX|AMEX)\s*[:]\s*[A-Z]{1,5}\b`
- **SEC filing language:** presence of "Form 8-K", "files with the SEC", "Securities and Exchange Commission", "Exchange Act", "Regulation FD"
- **Public company boilerplate:** "publicly traded", "common stock" — weaker signal, considered in combination with others

Trigger side attribution (which party is public):
- If the ticker mention falls within 200 characters of the target name in the text — `TARGET`
- If within 200 characters of the acquirer name — `ACQUIRER`
- If both, or ambiguous — `BOTH`

The adapter searches for filings by the public party (or both, when `BOTH`).

---

## 4. Behavior

### 4.1 Filing Query (Discovery)

Calls the Filing Query API to find the 8-K filing that corresponds to the announcement.

**Query construction (POST body):**

```json
{
  "query": "formType:\"8-K\" AND (items:\"1.01\" OR items:\"2.01\" OR items:\"8.01\" OR items:\"1.02\") AND ticker:<TICKER> AND filedAt:[<start_date> TO <end_date>]",
  "from": "0",
  "size": "10",
  "sort": [{"filedAt": {"order": "desc"}}]
}
```

**Item coverage rationale:**

- `1.01` — Entry into a Material Definitive Agreement. Primary hit for deal announcements.
- `2.01` — Completion of Acquisition or Disposition of Assets. Primary hit for deal closings.
- `8.01` — Other Events. Catch-all used for deal-related disclosures that don't fit 1.01 or 2.01.
- `1.02` — Termination of a Material Definitive Agreement. Primary hit for deal terminations.

**Party selection for `ticker:` clause:**

- When a ticker is known, use `ticker:<TICKER>`.
- When only a name is known, fall back to `companyName:<name>`.
- The name used is the public party's name (target if side=TARGET, acquirer if side=ACQUIRER).
- When side=BOTH, try the target's ticker/name first; if zero matches, retry with the acquirer's.

**Date window:**

- `filedAt:[<announced_date - SEC_DATE_WINDOW_DAYS> TO <announced_date + SEC_DATE_WINDOW_DAYS>]`
- 8-K filings must occur within 4 business days of the triggering event, so a 7-day window is conservative. Widen only if a lookup misses a known filing.

**Response handling:**

- Zero filings returned — set `staging_extraction.sec_lookup_status = NO_MATCH`, log the query, exit adapter. Not an error — common for private-to-private deals that still mention tickers in boilerplate.
- One or more filings — take the filing whose `filedAt` is closest to `announced_date`. Capture from the response:
  - `accessionNo`
  - `filedAt`
  - `linkToFilingDetails`
  - `items` array — **critical for §4.2 pre-check**
  - Exhibit list (for §4.3 and §4.4)
  - Filer CIK and company name

### 4.2 Item Text Extraction (Conditional on event_type)

The Extractor API FAQ notes that 8-Ks do not always include every item, and recommends verifying section existence via Query API metadata before calling Extractor. The adapter follows this guidance: before any Extractor call, it inspects the `items` array from §4.1 and only calls Extractor for items actually present in the filing.

This avoids:
- Unnecessary Extractor calls on items that don't exist
- The "processing" retry loop the FAQ describes for missing sections
- Burning through the tier's call budget

**Item selection by event_type:**

| event_type | Primary item | Fallback if primary absent |
| :--- | :--- | :--- |
| `ANNOUNCEMENT` | 1.01 | 8.01 |
| `CLOSE` | 2.01 | 8.01 |
| `TERMINATION` | 1.02 | 8.01 |
| `AMENDMENT` | 1.01 | 2.01, then 8.01 |

The adapter extracts the primary item if present; falls back to the secondary only if primary is absent. It does NOT fan out and extract all possible items — that's noise.

If neither primary nor fallback is present in the filing's `items` array, skip Item extraction entirely. Exhibit 2.1 and Exhibit 99.x retrieval still proceed (§4.3, §4.4).

**Extractor API call:**

```
GET https://api.sec-api.io/extractor
  ?url=<linkToFilingDetails>
  &item=<item_code>
  &type=text
  &token=<SEC_API_KEY>
```

**Item code mapping** (Extractor uses dash syntax; Query API uses dot syntax):

| Item (Query / human) | Extractor code |
| :--- | :--- |
| 1.01 | `1-1` |
| 1.02 | `1-2` |
| 2.01 | `2-1` |
| 8.01 | `8-1` |

**"Processing" response handling:**

If the Extractor returns status "processing", retry up to `SEC_EXTRACTOR_MAX_RETRIES` times with `SEC_EXTRACTOR_RETRY_DELAY_MS` delay between attempts. If still processing after the retry budget, log and treat as unavailable — do not insert a `source_raw` row. This condition is rare because the pre-check in the prior step avoids calling Extractor on non-existent items.

**Write to `source_raw`:**

| Field | Value |
| :--- | :--- |
| `source_type` | `SEC_8K_ITEM_101`, `SEC_8K_ITEM_201`, `SEC_8K_ITEM_801`, or `SEC_8K_ITEM_102` (one per item code) |
| `source_tier` | `T1` |
| `url` | `linkToFilingDetails` with `#item=<code>` appended for disambiguation when multiple items extracted from same filing |
| `title` | `"8-K Item <N.NN> - <filer_name> - <filedAt>"` |
| `published_date` | `filedAt` |
| `raw_html` | NULL (Extractor output is already clean text) |
| `clean_text` | Extractor API response body |
| `content_hash` | SHA-256 via `utils.content_hash()` (shared util) |
| `fetched_at` | Current UTC timestamp |
| `source_status` | `FETCHED` |

Duplicate guard via `content_hash` before insert.

### 4.3 Exhibit 2.1 Retrieval

Exhibit 2.1 is the merger agreement itself — the most authoritative T1 source for consideration structure, target capitalization, and deal terms.

**Matching logic:**

Scan the filing's exhibit list for any exhibit whose filename or label matches (case-insensitive prefix match) any of:
- `2.1`
- `ex-2.1`, `ex2-1`, `ex2_1`, `ex2.1`
- `exhibit 2.1`

Label variations are common across filers; case-insensitive prefix matching on `2.1` is the robust approach.

**Retrieval:**

Call the Filing & Exhibit Download API with the exhibit URL.

**Response handling:**

- HTML response: run trafilatura to produce `clean_text`, preserve `raw_html`.
- Text response: use response directly as `clean_text`.
- PDF response: MVP-deferred. Log the exhibit with `source_status = UNREADABLE` and `clean_text = null`. Downstream skips these rows. v2 adds PDF text extraction.

**Write to `source_raw`:**

| Field | Value |
| :--- | :--- |
| `source_type` | `SEC_EXHIBIT_21` |
| `source_tier` | `T1` |
| `url` | Exhibit URL |
| `title` | `"Exhibit 2.1 - <filer_name> - <filedAt>"` |
| `published_date` | `filedAt` |
| `raw_html` | HTML response if applicable |
| `clean_text` | Extracted / response text (null if UNREADABLE) |
| `content_hash` | SHA-256 via `utils.content_hash()` |
| `fetched_at` | Current UTC timestamp |
| `source_status` | `FETCHED` or `UNREADABLE` |

Duplicate guard via `content_hash`.

### 4.4 Exhibit 99.x Retrieval

Exhibit 99.x is typically the company-issued press release attached to the 8-K. It is materially useful because:

- It captures the PR text even when the PR didn't hit the wire services we scrape — direct-to-IR-page announcements still show up here.
- It is more authoritative than the wire-service version of the same text (T1 vs T2).
- Cross-source dedup via `content_hash` catches exact duplicates against PR Newswire captures; partial matches (different HTML wrapping, added SEC cover-page boilerplate) flow as separate observations and reconcile in aggregation.

**Matching logic:**

Scan the filing's exhibit list for any exhibit whose filename or label matches (case-insensitive prefix match) any of:
- `99.1`, `99.2`, `99.3`, ... (explicit enumeration up through `99.9` covers all observed variants)
- `ex-99.1`, `ex-99.2`, ...
- `ex99.1`, `ex99-1`, `ex99_1`, ...
- `exhibit 99.1`, ...

Retrieve EACH matching exhibit. A single 8-K may include multiple 99.x exhibits (press release, investor call script, supplementary announcement).

**Retrieval and response handling:** same as Exhibit 2.1.

**Write to `source_raw` (one row per exhibit retrieved):**

| Field | Value |
| :--- | :--- |
| `source_type` | `SEC_EXHIBIT_99` |
| `source_tier` | `T1` |
| `url` | Exhibit URL |
| `title` | `"Exhibit 99.<N> - <filer_name> - <filedAt>"` |
| `published_date` | `filedAt` |
| `raw_html` | HTML response if applicable |
| `clean_text` | Extracted / response text (null if UNREADABLE) |
| `content_hash` | SHA-256 via `utils.content_hash()` |
| `fetched_at` | Current UTC timestamp |
| `source_status` | `FETCHED` or `UNREADABLE` |

Duplicate guard via `content_hash`. If an Exhibit 99.1 matches the `content_hash` of an existing PR Newswire row, skip the insert — the existing row's provenance will be upgraded to T1 at aggregation (see §4.5).

### 4.5 Linking to Transaction

The adapter does NOT write `transaction_source` rows. Linking happens at the aggregation stage, where all SEC-sourced rows (Item text and exhibits) are joined to the transaction cluster created from PR-sourced extractions.

Transaction context needed for aggregation linking is preserved in each inserted row's `source_raw.notes` as a JSON blob:

```json
{
  "triggered_by_extraction_id": 1234,
  "trigger_signal": "NASDAQ:ACME mentioned within 47 chars of target name",
  "filing_accession": "0001234567-26-000001",
  "filer_cik": "0001234567",
  "filer_name": "Acme Corp",
  "filing_url": "https://www.sec.gov/...",
  "item_extracted": "1.01",
  "exhibit_label": null
}
```

For exhibit rows, `item_extracted` is null and `exhibit_label` carries the matched label (e.g., `"2.1"`, `"99.1"`).

All rows from the same 8-K share the same `filing_accession` value in this blob. Aggregation groups them on that key when reconciling.

### 4.6 Rate Limiting

Between any two outbound requests (any of the three endpoints), sleep `SEC_API_REQUEST_DELAY_SECONDS`. No parallelism in MVP.

---

## 5. Outputs

### 5.1 `source_raw` Rows

Per successfully enriched transaction, up to N rows where N is:

- 0 or 1 for the Item text (depends on event_type and what items the filing actually contains)
- 0 or 1 for Exhibit 2.1
- 0, 1, or more for Exhibit 99.x (multiple 99.x exhibits common on larger filings)

Typical yields:
- Simple announcement: 1 Item 1.01 row + 1 Exhibit 99.1 row (the PR text as-filed)
- Announcement with merger agreement attached: 1 Item 1.01 + 1 Exhibit 2.1 + 1 Exhibit 99.1
- Closing with no merger-agreement re-attachment: 1 Item 2.01 + possibly 1 Exhibit 99.1

### 5.2 Run Log

Written to `logs/sec_api_<run_id>.log`:

- Per-transaction: trigger signal, query payload, filings returned, filing selected, items present, exhibits present
- Per-Extractor call: item code, processing-retry count, response size
- Per-Exhibit call: exhibit URL, content-type, fetched / skipped / UNREADABLE
- API errors with full response bodies

### 5.3 Return Summary (per transaction)

```json
{
  "triggered": true,
  "trigger_signal": "NASDAQ:ACME near target name",
  "filings_found": 1,
  "filing_accession": "0001234567-26-000001",
  "items_available": ["1.01", "9.01"],
  "item_extracted": "1.01",
  "exhibit_21_fetched": true,
  "exhibit_99_fetched_count": 1,
  "exhibits_unreadable_count": 0,
  "rows_inserted": 3,
  "errors": []
}
```

---

## 6. Error Handling

| Condition | Behavior |
| :--- | :--- |
| 401 / 403 | Stop adapter. Invalid or expired API key. Raised to orchestrator; halts pipeline. |
| 429 (rate limited) | Sleep 30s, retry once. Second 429 logged; continue to next transaction. Persistent 429s indicate tier budget exceeded — revisit throttle config. |
| No filings matched the query | Set `staging_extraction.sec_lookup_status = NO_MATCH`, log, continue. |
| Filing Query returns 200 but unexpected schema | Log raw response, skip transaction, continue. |
| Extractor "processing" status persists past retry budget | Log, skip Item extraction for this filing, continue with exhibits. |
| Extractor returns empty text | Insert row with `clean_text = NULL` and note reason in `source_raw.notes`, continue. |
| Exhibit 2.1 URL 404s | Log, skip exhibit. Not fatal — Item text alone is still valuable. |
| Exhibit 99.x URL 404s | Log, skip that exhibit. Continue with other 99.x exhibits if present. |
| PDF exhibit (2.1 or 99.x) | Log as UNREADABLE, insert row with `clean_text = NULL`. Downstream skips. v2 adds PDF extraction. |
| Ticker maps to multiple entities | Take top-ranked filing from Query response. Log ambiguity in `source_raw.notes`. |
| 5xx from any endpoint | Retry once after 10s backoff. Persistent: log, skip transaction, continue. |

---

## 7. Out of Scope for MVP

- 8-K/A amendments. Treated as regular filings in MVP. Supersession logic is v2.
- Enrichment forms: DEFM14A, S-4, SC TO-T. These add depth but not discovery for MVP.
- Form 6-K (foreign private issuers). Cross-border deals are MVP-deferred.
- Full-text search across all filings for a company. Only targeted 8-K lookup in MVP.
- XBRL-to-JSON target financial hydration. Reserved for v2.
- Company Subsidiaries API. Reserved for v2 entity resolution work.
- Outstanding Shares & Public Float. Reserved for v2 valuation enrichment.
- Form 13D/13G beneficial ownership. Reserved for v2.
- PDF exhibit text extraction. Reserved for v2.
- WebSocket streaming for real-time discovery. Polling / PR-triggered lookup sufficient for MVP.
- Fanning out to every item present in a filing. Adapter extracts only the event-type-relevant primary or fallback.

---

## 8. Open Items

- **Tier rate limit verification.** The $55 Personal & Startups tier's exact rate limit is not documented numerically. Default throttle (0.2s delay — 5 req/sec) is likely well within. Worth confirming from the account dashboard; adjust if lower.
- **Ticker-to-CIK resolution.** Filing Query supports `ticker:` directly. To be confirmed during sandbox validation with a real filing.
- **Exhibit labeling variation.** Case-insensitive prefix match on `2.1` and `99.x` should catch the vast majority; rare edge cases may need follow-up after first production run.
- **Cross-source content_hash behavior.** When an Exhibit 99.1 matches the `content_hash` of an existing PR Newswire row exactly, MVP skips the Exhibit 99.1 insert. This preserves the unique-hash invariant but means the `source_tier` on the existing row stays at T2. Aggregation is responsible for upgrading tier on matched-hash pairs. Whether the upgrade happens at aggregation or at the adapter is a judgment call — MVP handles at aggregation to keep adapters pure I/O.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft. Query scoped to Item 1.01 only. Exhibit 2.1 retrieval supported. Exhibit 99.x not addressed. |
| 0.2 | 2026-04-23 | Expanded Item coverage to include 1.01, 2.01, 8.01, 1.02. Event-type-conditional Item selection (primary + fallback) with pre-check against filing's `items` array. Added Exhibit 99.x retrieval with new `source_type = SEC_EXHIBIT_99`. Documented "processing" response handling per Extractor API FAQ. Clarified endpoint URLs (Filing Query API base vs Extractor API vs Filing & Exhibit Download API). Added PDF exhibit handling via `source_status = UNREADABLE`. Added `staging_extraction.sec_lookup_status = NO_MATCH` state. |
