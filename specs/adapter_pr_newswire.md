# PR Newswire Adapter Spec

**Version:** 0.1 (draft)
**Repo path:** `specs/adapter_pr_newswire.md`

---

## 1. Purpose

Scrape the PR Newswire M&A / Acquisitions category listing and retrieve the body of each press release. Produces rows in `source_raw` that feed downstream filtering and extraction.

The adapter is a **fetcher only**. No LLM calls, no filtering, no classification. Its job is to get press release text into the database cleanly and politely.

---

## 2. Inputs

### 2.1 Listing URL
```
https://www.prnewswire.com/news-releases/financial-services-latest-news/acquisitions-mergers-and-takeovers-list/
```

### 2.2 Pagination
Pages are traversed via `?page=N` appended to the listing URL. Adapter starts at page 1 and increments until a stop condition is met (see §4.4).

### 2.3 Configuration (from `.env`)
| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `USER_AGENT_STRING` | `"MA-Collection-MVP/0.1 (contact: <email>)"` | Identifies the scraper to the site |
| `MAX_FETCHES` | 100 | Stop condition — MVP target |
| `REQUEST_DELAY_SECONDS` | 1.5 | Minimum sleep between any two outbound requests |

---

## 3. Pre-Flight

Before any listing fetch, the adapter:

1. Fetches `https://www.prnewswire.com/robots.txt`.
2. Writes the contents to `logs/robots_<timestamp>.txt`.
3. Checks for any `Disallow` directive affecting the listing or press release paths.
4. Checks for a `Crawl-delay` directive. If present and higher than `REQUEST_DELAY_SECONDS`, raises the delay to match.
5. If the listing path is explicitly disallowed, the adapter aborts with a clear error message and does not proceed. (This is unexpected but the check belongs in the flow.)

---

## 4. Behavior

### 4.1 Listing Scrape
For each page 1..N:
1. GET the listing URL with `?page=N`.
2. Parse HTML to extract one record per press release entry. Required fields per record:
   - `title` — article headline
   - `url` — absolute URL to the release body
   - `published_date` — parsed to ISO 8601 where the listing provides it
3. For each record, check `source_raw` by `url`. If a row already exists, skip (idempotent). Otherwise queue the URL for body fetch.

### 4.2 Body Fetch
For each queued URL:
1. GET the release URL.
2. On HTTP 200: store `raw_html`.
3. Run trafilatura on `raw_html` to produce `clean_text`.
4. Compute `content_hash` as SHA-256 over `clean_text` (whitespace normalized, lowercased).
5. Check `source_raw` by `content_hash`. If already present, skip (catches re-published releases under different URLs).
6. Insert a row into `source_raw`:
   - `source_type` = `PR_NEWSWIRE`
   - `source_tier` = `T2`
   - `url`, `title`, `published_date` from listing
   - `raw_html`, `clean_text`, `content_hash` from fetch
   - `fetched_at` = current UTC timestamp (ISO 8601)
   - `source_status` = `FETCHED`

### 4.3 Rate Limiting
Between any two outbound HTTP requests (listing or body), sleep `REQUEST_DELAY_SECONDS`. No parallel fetching in MVP. Politeness over throughput.

### 4.4 Stop Conditions
Adapter halts pagination when any of these is true:
- Rows inserted this run equals `MAX_FETCHES`.
- A listing page returns zero press release entries (end of available content).
- Consecutive error threshold: 10 non-200 responses in a row, or 5 consecutive 429s.

---

## 5. Outputs

### 5.1 `source_raw` Rows
One row per successfully fetched release. Field mapping in §4.2.

### 5.2 Run Log
Written to `logs/pr_newswire_<timestamp>.log`:
- Pre-flight robots.txt contents
- Per-page: page number, records seen, records new, records skipped
- Per-URL: URL, HTTP status, duplicate hash or inserted row ID
- Run totals: runtime seconds, inserts, errors

### 5.3 Return Summary
Adapter returns a dictionary for the orchestrator:
```json
{
  "pages_fetched": 0,
  "listings_seen": 0,
  "bodies_fetched": 0,
  "rows_inserted": 0,
  "skipped_duplicates": 0,
  "errors": [],
  "runtime_seconds": 0.0
}
```

---

## 6. Error Handling

| Condition | Behavior |
| :--- | :--- |
| 404 on listing page | Log and stop (treated as end of pagination) |
| 404 on body URL | Log error, continue to next URL |
| 429 (rate limited) | Sleep 60s, retry once. A second 429 increments the consecutive-429 counter. 5 consecutive 429s = stop run with clear error. |
| 403 (forbidden) | Stop run immediately. Possible bot block — needs human investigation before resuming. |
| 5xx | Retry once after 10s backoff. Persistent failure: log and continue. |
| Network / DNS failure | Retry once after 5s. If still failing, log and continue. |
| Trafilatura returns empty or <100 chars | Insert the row with `clean_text` = NULL and note the reason in `source_raw.notes`. Downstream filter will drop it. |
| Expected HTML element missing (listing parse fails) | Log warning with page number. If two consecutive pages fail to parse, stop run — listing structure may have changed. |

---

## 7. Out of Scope for MVP

- JavaScript-rendered content handling (the listing is static HTML as of this writing).
- Image or attachment downloads.
- Following cross-links to wire service parent articles.
- Historical backfill beyond current listing pages.
- Secondary PR Newswire categories (IPOs, financings, partnerships).
- Multi-language handling.
- Browser automation (Playwright / Selenium).

---

## 8. Open Items

- **`Crawl-delay` and terms of service:** Discovered at runtime during pre-flight. No action needed from reviewer before build; flagged if anything surprising surfaces.
- **Listing HTML structure:** CSS selectors for title/URL/date will be determined at implementation time from the live page. Adapter must log a warning (not fail silently) if expected elements are absent, so stale selectors are caught fast.

---

## 9. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
