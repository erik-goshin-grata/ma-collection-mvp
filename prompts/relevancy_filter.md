# Relevancy Filter Prompt

**Version:** 0.3 (revised)
**Repo path:** `prompts/relevancy_filter.md`

---

## 1. Purpose

Binary gate at the top of the extraction pipeline. Given a press release title and body, decide whether the release is about an M&A transaction or capital event in the MVP scope. Drops noise cheaply before expensive downstream extraction runs.

Runs on every row in `source_raw` with `source_status = FETCHED` and `source_type = PR_NEWSWIRE`. SEC-sourced rows (`source_type IN (SEC_8K_ITEM_101, SEC_EXHIBIT_21)`) are not filtered — they are assumed relevant because they were retrieved under a triggered lookup.

---

## 2. Model & Parameters

- **Model:** `claude-haiku-4-5`
- **Temperature:** 0.0
- **Max tokens:** 256
- **Expected latency:** <1s per call

---

## 3. Input Schema

Passed by the orchestrator:

```json
{
  "source_raw_id": 12345,
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "Acme Corp (NASDAQ: ACME), a leading provider of..."
}
```

The orchestrator truncates `clean_text` to the first 4000 characters before passing. Full-text analysis is a downstream concern; relevancy only needs the top of the release.

---

## 4. System Prompt

```
You are a relevancy classifier for a financial data collection pipeline. You receive the title and body excerpt of a press release. Your job is to decide whether the release is about an in-scope transaction event.

IN SCOPE (classify as RELEVANT):
- Acquisitions (one company buying another)
- Mergers (two companies combining)
- Carve-outs, divestitures, and asset sales
- Spin-offs and split-offs
- Take-private transactions
- Reverse mergers
- Joint ventures (new entity formed between parties)
- Minority investments or growth equity rounds in private companies
- Definitive agreements for any of the above
- Closing or completion of any of the above

OUT OF SCOPE (classify as NOT_RELEVANT):
- Product launches, partnerships without equity, commercial agreements
- Customer announcements or contract wins
- Executive appointments, hires, departures
- Earnings releases, guidance updates, dividend announcements
- Share buybacks by a company of its own stock (unless part of a take-private)
- Debt financings and bond issuances (unless tied to a specific acquisition)
- Regulatory filings that do not announce a transaction
- Marketing content, whitepapers, industry commentary
- Stock split or reverse stock split announcements
- Name changes or rebrand announcements

EDGE CASES:
- If a release announces both a product and an acquisition, classify as RELEVANT (the acquisition is the higher-priority signal).
- If a release is about a previously announced deal being amended, terminated, or extended, classify as RELEVANT.
- If a release is about a rumored deal without a definitive agreement, classify as NOT_RELEVANT (rumor coverage is out of MVP scope).
- If a release is about a company being added to an index, going IPO, or completing a direct listing, classify as NOT_RELEVANT (IPOs are not in MVP scope).

CRITICAL — reason_code MUST be chosen from the enum list

The reason_code field must be exactly one of the 21 values listed in the in-scope and out-of-scope enumerations above. Do not invent new values. Do not adapt existing values with suffixes or prefixes. Do not substitute synonyms or more descriptive labels.

If no listed value fits perfectly:
- For RELEVANT classifications, use AMBIGUOUS_BUT_LIKELY_DEAL
- For NOT_RELEVANT classifications, use OTHER_NOT_RELEVANT

Examples of invented values that must NOT be produced:
- SHARE_BUYBACK → use BUYBACK_OR_DIVIDEND
- DEBT_RESTRUCTURING_AMENDMENT → use DEBT_OR_NON_DEAL_FINANCING
- ACQUISITION_COMPLETION → use DEAL_CLOSE_OR_COMPLETION
- ACQUISITION_CLOSING → use DEAL_CLOSE_OR_COMPLETION
- MINORITY_INVESTMENT_CLOSING → use DEAL_CLOSE_OR_COMPLETION
- ASSET_SALE → use CARVE_OUT_OR_DIVESTITURE
- PRODUCT_PARTNERSHIP → use PRODUCT_OR_COMMERCIAL
- ADVISORY_ENGAGEMENT_NO_DEFINITIVE_TRANSACTION → use OTHER_NOT_RELEVANT
- MERGER_REGULATORY_APPROVAL → use DEAL_AMENDMENT_OR_TERMINATION

Precision is not the goal — enum discipline is. The reason_code is for categorical filtering, not descriptive tagging.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble. The reason_code field must be one of the 21 enum values listed above — no exceptions.

{
  "classification": "RELEVANT",
  "reason_code": "ACQUISITION_ANNOUNCEMENT",
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "relevancy_filter:0.3"
}

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```

---

## 5. User Prompt Template

```
TITLE: {title}

BODY:
{clean_text}

Classify this release.
```

---

## 6. Output Schema

```json
{
  "classification": "RELEVANT",
  "reason_code": "ACQUISITION_ANNOUNCEMENT",
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "relevancy_filter:0.3"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `classification` | enum | `RELEVANT`, `NOT_RELEVANT` |
| `reason_code` | enum | See below |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Brief explanation if notable |
| `prompt_version` | string | Set by caller, returned unchanged |

**`reason_code` values (RELEVANT side):**
- `ACQUISITION_ANNOUNCEMENT`
- `MERGER_ANNOUNCEMENT`
- `CARVE_OUT_OR_DIVESTITURE`
- `SPIN_OFF_OR_SPLIT`
- `TAKE_PRIVATE`
- `REVERSE_MERGER`
- `JOINT_VENTURE`
- `MINORITY_INVESTMENT`
- `DEAL_CLOSE_OR_COMPLETION`
- `DEAL_AMENDMENT_OR_TERMINATION`
- `AMBIGUOUS_BUT_LIKELY_DEAL` — use when release references an in-scope event but framing is unclear

**`reason_code` values (NOT_RELEVANT side):**
- `PRODUCT_OR_COMMERCIAL`
- `PERSONNEL`
- `EARNINGS_OR_FINANCIAL_REPORTING`
- `BUYBACK_OR_DIVIDEND`
- `DEBT_OR_NON_DEAL_FINANCING`
- `REGULATORY_OR_COMPLIANCE`
- `MARKETING_OR_COMMENTARY`
- `RUMOR_OR_SPECULATION`
- `IPO_OR_DIRECT_LISTING`
- `OTHER_NOT_RELEVANT`

---

## 7. Few-Shot Examples

**Example 1 — Clear acquisition:**

Input:
```
TITLE: Acme Corp to Acquire Beta Industries for $500 Million
BODY: Acme Corp (NASDAQ: ACME) today announced it has entered into a definitive agreement to acquire Beta Industries, a privately held manufacturer of specialty components, for $500 million in cash. The transaction is expected to close in Q3 2026...
```

Output:
```json
{
  "classification": "RELEVANT",
  "reason_code": "ACQUISITION_ANNOUNCEMENT",
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "relevancy_filter:0.3"
}
```

**Example 2 — Partnership, not a deal:**

Input:
```
TITLE: Acme Corp and Beta Industries Announce Strategic Partnership
BODY: Acme Corp and Beta Industries today announced a multi-year strategic partnership to jointly develop and market next-generation analytics solutions. Under the agreement, the companies will collaborate on product development and go-to-market activities...
```

Output:
```json
{
  "classification": "NOT_RELEVANT",
  "reason_code": "PRODUCT_OR_COMMERCIAL",
  "model_confidence": "HIGH",
  "notes": "Commercial partnership, no equity or M&A component mentioned",
  "prompt_version": "relevancy_filter:0.3"
}
```

**Example 3 — Edge case, rumor coverage:**

Input:
```
TITLE: Acme Corp Reportedly in Talks to Acquire Beta Industries
BODY: According to sources familiar with the matter, Acme Corp is in preliminary discussions to acquire Beta Industries in a deal that could be valued at over $1 billion. Neither company has confirmed the discussions, and no definitive agreement has been reached...
```

Output:
```json
{
  "classification": "NOT_RELEVANT",
  "reason_code": "RUMOR_OR_SPECULATION",
  "model_confidence": "HIGH",
  "notes": "No definitive agreement; rumor coverage is out of scope",
  "prompt_version": "relevancy_filter:0.3"
}
```

**Example 4 — Edge case, deal termination:**

Input:
```
TITLE: Acme Corp and Beta Industries Terminate Merger Agreement
BODY: Acme Corp and Beta Industries today announced that they have mutually agreed to terminate their previously announced merger agreement dated January 15, 2026. The companies cited regulatory challenges and evolving market conditions...
```

Output:
```json
{
  "classification": "RELEVANT",
  "reason_code": "DEAL_AMENDMENT_OR_TERMINATION",
  "model_confidence": "HIGH",
  "notes": "Termination of a previously announced deal — in scope for completeness",
  "prompt_version": "relevancy_filter:0.3"
}
```

---

## 8. Failure Modes

| Failure | Observed behavior | Handling |
| :--- | :--- | :--- |
| Model returns explanation + JSON | Rare at temp 0.0, but possible | Parser extracts the first valid JSON object and logs the preamble |
| Model returns invalid enum value | Possible if prompt updates expand enums | Parser rejects, marks row `PROMPT_FAILED`, logs for review |
| Model refuses ("I cannot classify this") | Very rare; possible on releases with sensitive content | Parser treats refusal as `PROMPT_FAILED`, logs |
| Empty response | API timeout or overload | Orchestrator retries once with 5s backoff, then marks `PROMPT_FAILED` |
| Borderline cases (LOW confidence on either side) | Model returns `LOW` confidence | Orchestrator accepts classification but flags for QA sampling at a higher rate than HIGH/MEDIUM rows |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
| 0.2 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt section to ensure model receives schema definition at load time. |
| 0.3 | 2026-04-23 | Tightened enum discipline: added explicit CRITICAL block before RESPONSE FORMAT listing invented values observed in validation runs and mapping each to the correct enum value. Strengthened RESPONSE FORMAT preamble with no-exceptions language. Addresses 30-47% failure rate from model inventing reason_codes like SHARE_BUYBACK, ACQUISITION_COMPLETION, etc. instead of using listed enum values. |
