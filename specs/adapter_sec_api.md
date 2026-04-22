# sec-api.io Adapter Spec

**Version:** 0.1 (draft)
**Repo path:** `specs/adapter_sec_api.md`

---

## 1. Purpose

Enrich PR-discovered transactions where a public party is detected. The adapter fetches the corresponding 8-K Item 1.01 filing and, where available, Exhibit 2.1 (the merger agreement itself). Produces additional rows in `source_raw` tagged as T1 sources.

The adapter is **triggered per-transaction** after initial PR extraction identifies a public party. It is not a standalone discovery channel in MVP — all discovery flows through PR Newswire.

---

## 2. Inputs

### 2.1 API Base
```
https://api.sec-api.io
```

### 2.2 Authentication
API key passed via `Authorization` header on every request. Key stored in `.env` as `SEC_API_KEY`.

### 2.3 Trigger Inputs (per transaction)
Provided by the orchestrator from the staging_extraction row that triggered enrichment:
- `target_name` and/or `acquirer_name`
- `target_domain` and/or `acquirer_domain` (if extracted)
- `announced_date` (ISO 8601)
- Public-party side: `TARGET`, `ACQUIRER`, or `BOTH`
- Any ticker(s) detected in the PR text

### 2.4 Configuration (from `.env`)
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `SEC_API_KEY` | (required) | Authentication |
| `SEC_API_REQUEST_DELAY_SECONDS` | 0.2 | Throttle between outbound requests |
| `SEC_DATE_WINDOW_DAYS` | 7 | Filing search window around `announced_date` |

---

## 3. Public-Party Detection (Trigger Logic)

Detection runs on the PR's `clean_text` before the adapter is called. Any of the following signals trip the trigger:

- **Exchange + ticker pattern:** regex `\b(NYSE|NASDAQ|NYSE American|OTCQB|OTCQX)\s*[:]\s*[A-Z]{1,5}\b`
- **SEC filing language:** presence of "Form 8-K", "files with the SEC", "Securities and Exchange Commission", "Exchange Act", "Regulation FD"
- **Public company boilerplate:** "publicly traded", "common stock"

Trigger side attribution (which party is public):
- If the ticker mention falls within 200 characters of the target name in the text — `TARGET`
- If within 200 characters of the acquirer name — `ACQUIRER`
- If both, or if ambiguous — `BOTH`

The adapter searches for filings by the public party (or both, when `BOTH`).

---

## 4. Behavior

### 4.1 Filing Query (Discovery)
Calls the Filing Query API to find the 8-K Item 1.01 filing that corresponds to the announcement.

Query construction:
- `formType:"8-K"` required
- `items:"1.01"` required (8-K Item 1.01 — Entry into Material Definitive Agreement)
- `ticker:<TICKER>` when a ticker is known
- `companyName:<NAME>` as fallback when only the name is known (adapter uses the name of the public party)
- `filedAt:[<announced_date - SEC_DATE_WINDOW_DAYS> TO <announced_date + SEC_DATE_WINDOW_DAYS>]`

Sort: `filedAt` descending. Page size: 10.

Response handling:
- Zero filings returned — mark transaction `sec_lookup_status = NO_MATCH`, log the query, exit adapter. Not an error.
- One or more filings — take the filing whose `filedAt` is closest to `announced_date`. Capture:
  - `accessionNo`
  - `filedAt`
  - `linkToFilingDetails`
  - exhibit list (for §4.3)
  - filer CIK and name

### 4.2 Item 1.01 Text Extraction
Calls the Item Extractor API to pull clean text of Item 1.01 from the identified filing.

Parameters:
- Filing URL or accession from §4.1
- Item code: `1-1` (sec-api.io's convention for Item 1.01 — note: the Query API uses `1.01`, the Extractor API uses `1-1`)
- Type: `text`

Write to `source_raw`:
| Field | Value |
| :--- | :--- |
| `source_type` | `SEC_8K_ITEM_101` |
| `source_tier` | `T1` |
| `url` | Filing detail URL |
| `title` | `"8-K Item 1.01 - <filer_name> - <filedAt>"` |
| `published_date` | `filedAt` |
| `raw_html` | NULL (Extractor output is already clean) |
| `clean_text` | Extractor API response |
| `content_hash` | SHA-256 over `clean_text` |
| `fetched_at` | Current UTC timestamp |
| `source_status` | `FETCHED` |

Duplicate guard: check `source_raw` by `content_hash` before insert.

### 4.3 Exhibit 2.1 Retrieval
If the filing's exhibit list contains an exhibit labeled `2.1`, `EX-2.1`, `ex2-1`, or `ex2_1` (case-insensitive):

Calls the Filing & Exhibit Download API with the exhibit URL.

Response handling:
- If HTML: run trafilatura to produce `clean_text`, preserve `raw_html`.
- If text: use response directly as `clean_text`.

Write to `source_raw`:
| Field | Value |
| :--- | :--- |
| `source_type` | `SEC_EXHIBIT_21` |
| `source_tier` | `T1` |
| `url` | Exhibit URL |
| `title` | `"Exhibit 2.1 - <filer_name> - <filedAt>"` |
| `published_date` | `filedAt` |
| `raw_html` | HTML response if applicable |
| `clean_text` | Extracted / response text |
| `content_hash` | SHA-256 over `clean_text` |
| `fetched_at` | Current UTC timestamp |
| `source_status` | `FETCHED` |

Duplicate guard via `content_hash`.

### 4.4 Linking to Transaction
The adapter does not write `transaction_source` rows. Linking happens at the aggregation stage, where SEC-sourced rows are joined to the transaction cluster created from PR-sourced extractions. This preserves the separation between fetching and entity resolution.

Transaction context needed for aggregation linking is preserved in `source_raw.notes` as a JSON blob:
```json
{
  "triggered_by_extraction_id": 1234,
  "trigger_signal": "NASDAQ:ACME mentioned within 47 chars of target name",
  "filing_accession": "0001234567-26-000001",
  "filer_cik": "0001234567"
}
```

### 4.5 Rate Limiting
Between any two outbound requests (any endpoint), sleep `SEC_API_REQUEST_DELAY_SECONDS`. No parallelism in MVP.

---

## 5. Outputs

### 5.1 `source_raw` Rows
Up to 2 rows per successfully enriched transaction:
- 1 for the 8-K Item 1.01 text.
- 1 for Exhibit 2.1 if present and retrievable.

### 5.2 Run Log
Written to `logs/sec_api_<timestamp>.log`:
- Per-transaction: trigger signal, query payload, filings returned, filing selected
- Per-filing: Item Extractor result, exhibit list
- Per-exhibit: fetched / skipped / failed
- API errors with full response bodies

### 5.3 Return Summary (per transaction)
```json
{
  "triggered": true,
  "trigger_signal": "NASDAQ:ACME near target name",
  "filings_found": 1,
  "filing_accession": "0001234567-26-000001",
  "item_101_fetched": true,
  "exhibit_21_fetched": true,
  "rows_inserted": 2,
  "errors": []
}
```

---

## 6. Error Handling

| Condition | Behavior |
| :--- | :--- |
| 401 / 403 | Stop adapter. Invalid or expired API key. |
| 429 (rate limited) | Sleep 30s, retry once. A second 429 is logged; continue to next transaction. Persistent 429s mean we're exceeding tier limits and need to revisit throttle. |
| No filings matched the query | Log, mark transaction as no SEC enrichment, continue. Not an error — common for private-to-private deals that still mention tickers. |
| Filing Query returns 200 but unexpected schema | Log raw response, skip transaction, continue. |
| Item Extractor returns empty text | Log, insert row with `clean_text` = NULL and note in `source_raw.notes`, continue. |
| Exhibit 2.1 URL 404s | Log, skip exhibit. Not fatal — Item 1.01 text alone is still valuable. |
| Ticker maps to multiple entities | Take the top-ranked filing from the query response. Log ambiguity in `source_raw.notes`. |
| 5xx from any endpoint | Retry once after 10s backoff. Persistent: log, skip transaction, continue. |

---

## 7. Out of Scope for MVP

- 8-K/A amendments. If an amendment is the only filing returned, it's treated as a regular filing for MVP purposes. Supersession logic is v2.
- Enrichment forms: DEFM14A, S-4, SC TO-T. These add depth but not discovery for MVP.
- Form 6-K (foreign private issuers). Cross-border deals are MVP-deferred.
- Full-text search across all filings for a company. Only targeted Item 1.01 lookup in MVP.
- XBRL-to-JSON target financial hydration. Reserved for v2.
- Company Subsidiaries API. Reserved for v2 entity resolution work.
- Outstanding Shares & Public Float. Reserved for v2 valuation enrichment.
- Form 13D/13G beneficial ownership. Reserved for v2.
- WebSocket streaming for real-time discovery. Polling / PR-triggered lookup is sufficient for MVP scale.

---

## 8. Open Items

- **Tier rate limit verification.** The $55 Personal & Startups tier's exact rate limit is not confirmed numerically. Default throttle (0.2s delay = ~5 req/sec) is likely well within. Worth confirming from the account dashboard; adjust if lower.
- **Ticker-to-CIK resolution.** Filing Query supports `ticker:` directly, so no separate resolution call is required. To be confirmed during sandbox validation with a real filing.
- **Exhibit labeling variation.** Exhibit 2.1 labels are not uniform across filings (observed variants: `EX-2.1`, `ex2-1`, `Exhibit 2.1`, `2.1*`, `Exhibit No. 2.1`). Case-insensitive matching on label start with `2.1` should catch the vast majority; rare edge cases may need follow-up.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
