# Relevancy Filter Prompt

**Version:** 0.9 (a sale process is not a transaction)
**Repo path:** `prompts/relevancy_filter.md`

---

> ## ✅ QA NOTE — fix already applied (code)
> The stage now **normalizes off-enum `reason_code`** rather than dropping a correctly-classified row (Haiku sometimes returns `ACQUISITION` instead of `ACQUISITION_ANNOUNCEMENT`, or `ASSET_PURCHASE`). See `stages/relevancy_filter.py::_normalize_reason_code` and the 2026-08-01 QA runbook. Prompt enum discipline is unchanged; the safety net is in code so a valid deal is never lost on a metadata label.

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
- VC funding rounds (Seed, Series A through Series N, angel, crowdfunding)
- Growth equity investments by growth equity or late-stage investors
- Venture debt or venture lending facilities to venture-backed companies
- Recapitalizations (dividend recap, equity recap, leveraged recap, sponsor recap)
- PIPEs — private investment in public equity — where the source explicitly uses the term "PIPE" or the phrase "private investment in public equity". A private placement, convertible note, preferred issuance or registered direct offering is NOT a PIPE unless the source names it one.
- Definitive agreements for any of the above
- Closing or completion of any of the above

OUT OF SCOPE (classify as NOT_RELEVANT):
- Product launches, partnerships without equity, commercial agreements
- Customer announcements or contract wins
- Executive appointments, hires, departures
- Earnings releases, guidance updates, dividend announcements
- Share buybacks by a company of its own stock (unless part of a take-private)
- Corporate debt financings and bond issuances by mature public companies (venture debt to venture-backed companies IS in scope — see above)
- Regulatory filings that do not announce a transaction
- Marketing content, whitepapers, industry commentary
- Stock split or reverse stock split announcements
- Name changes or rebrand announcements

EDGE CASES:
- If a release announces both a product and an acquisition, classify as RELEVANT (the acquisition is the higher-priority signal).
- If a release is about a previously announced deal being amended, terminated, or extended, classify as RELEVANT.
- If a release is about a rumored deal without a definitive agreement, classify as NOT_RELEVANT (rumor coverage is out of MVP scope).
- If a release describes only a process for seeking a buyer — a formal sale process, a
  court-supervised or bankruptcy auction, a strategic-alternatives review, a stated
  intention to sell, a solicitation of bids, or the engagement of advisors to pursue a
  sale — and no counterparty to a specific acquisition or divestiture has been announced
  or agreed, classify as NOT_RELEVANT with OTHER_NOT_RELEVANT. Seeking a buyer is not a
  transaction. This turns on whether a counterparty is established, not on how formal or
  how likely the sale is: a court-supervised auction with a filed motion, an engaged
  banker and a target completion date is still out of scope while the buyer is
  unidentified. A later release naming a stalking-horse bidder, a winning bidder or
  acquirer, a definitive sale agreement, or a completed sale is a separate source and may
  be RELEVANT on its own terms.
- If a release is about a company being added to an index, going IPO, or completing a direct listing, classify as NOT_RELEVANT (IPOs are not in MVP scope).

REASON CODES — RELEVANT side:
- ACQUISITION_ANNOUNCEMENT
- MERGER_ANNOUNCEMENT
- CARVE_OUT_OR_DIVESTITURE
- SPIN_OFF_OR_SPLIT
- TAKE_PRIVATE
- REVERSE_MERGER
- JOINT_VENTURE
- MINORITY_INVESTMENT
- VC_ROUND_OR_FUNDING — VC round, growth equity investment, or venture debt facility
- RECAPITALIZATION — dividend recap, equity recap, leveraged recap, or sponsor recap
- PIPE — private investment in public equity, where the source explicitly names the structure. RELEVANT so the event is recorded and recognized downstream; the deal-type classifier marks it as recognized-but-not-profiled.
- DEAL_CLOSE_OR_COMPLETION
- DEAL_AMENDMENT_OR_TERMINATION
- AMBIGUOUS_BUT_LIKELY_DEAL — use when release references an in-scope event but framing is unclear

REASON CODES — NOT_RELEVANT side:
- PRODUCT_OR_COMMERCIAL
- PERSONNEL
- EARNINGS_OR_FINANCIAL_REPORTING
- BUYBACK_OR_DIVIDEND
- DEBT_OR_NON_DEAL_FINANCING
- REGULATORY_OR_COMPLIANCE
- MARKETING_OR_COMMENTARY
- RUMOR_OR_SPECULATION
- IPO_OR_DIRECT_LISTING
- OTHER_NOT_RELEVANT

CRITICAL — reason_code MUST be chosen from the enum list

The reason_code field must be exactly one of the 24 values in the REASON CODES lists above. Do not invent new values. Do not adapt existing values with suffixes or prefixes. Do not substitute synonyms or more descriptive labels.

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
- TAKE_PRIVATE_ANNOUNCEMENT → use TAKE_PRIVATE
- MINORITY_INVESTMENT_ANNOUNCEMENT → use MINORITY_INVESTMENT
- VC_FUNDING_ROUND → use VC_ROUND_OR_FUNDING
- SERIES_B_FUNDING → use VC_ROUND_OR_FUNDING
- GROWTH_EQUITY_INVESTMENT → use VC_ROUND_OR_FUNDING
- VENTURE_DEBT_FACILITY → use VC_ROUND_OR_FUNDING
- DIVIDEND_RECAPITALIZATION → use RECAPITALIZATION
- PIPE_FINANCING, PIPE_TRANSACTION, PIPE_OFFERING, PRIVATE_INVESTMENT_IN_PUBLIC_EQUITY → use PIPE

Note on suffixes: Do NOT append _ANNOUNCEMENT, _CLOSING, _COMPLETION, _AMENDMENT, or _TERMINATION suffixes to any enum value. Event-type distinctions belong in the deal_type_classifier output, not in reason_code. Use the base enum value only.

Precision is not the goal — enum discipline is. The reason_code is for categorical filtering, not descriptive tagging.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble. The reason_code field must be one of the 24 enum values listed above — no exceptions.

{
  "classification": "RELEVANT",
  "reason_code": "ACQUISITION_ANNOUNCEMENT",
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for optional fields that have no value.
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
  "notes": null
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `classification` | enum | `RELEVANT`, `NOT_RELEVANT` |
| `reason_code` | enum | See the REASON CODES lists in §4 |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Brief explanation if notable |

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
  "notes": null
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
  "notes": "Commercial partnership, no equity or M&A component mentioned"
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
  "notes": "No definitive agreement; rumor coverage is out of scope"
}
```

**Example 4 — VC funding round:**

Input:
```
TITLE: TechCo Raises $50 Million Series B Led by Venture Partners
BODY: TechCo today announced the closing of a $50 million Series B funding
round led by Venture Partners, with participation from existing investors
Seed Capital and Growth Fund I. The proceeds will be used to accelerate
product development and geographic expansion.
```

Output:
```json
{
  "classification": "RELEVANT",
  "reason_code": "VC_ROUND_OR_FUNDING",
  "model_confidence": "HIGH",
  "notes": null
}
```

**Example 5 — Edge case, deal termination:**

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
  "notes": "Termination of a previously announced deal — in scope for completeness"
}
```

**Example 6 — Edge case, sale process with no counterparty:**

Input:
```
TITLE: BFG Supply files Chapter 11 bankruptcy, seeks asset sale
BODY: BFG Supply, a distributor of horticultural and agricultural supplies, filed for Chapter 11 bankruptcy protection on August 18 in Delaware, seeking to sell all of its assets through a court-supervised auction process. SSG Capital Advisors is running a going-concern sale process for some or all of the company's businesses and assets, with a target completion date within roughly 60 days. Potential buyers have already expressed interest, and the Debtors are in active negotiations with parties that could serve as a stalking horse bidder...
```

Output:
```json
{
  "classification": "NOT_RELEVANT",
  "reason_code": "OTHER_NOT_RELEVANT",
  "model_confidence": "HIGH",
  "notes": "Court-supervised sale process with advisors engaged and interested parties, but no established transaction counterparty — seeking a buyer is not a transaction"
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
| 0.4 | 2026-04-23 | Added suffix-pattern warning and two concrete examples. Addresses residual 13% failure rate from v0.3. |
| 0.5 | 2026-07-28 | V2 alignment. Added VC/funding events to IN SCOPE: VC funding rounds, growth equity investments, venture debt, recapitalizations. Added `VC_ROUND_OR_FUNDING` and `RECAPITALIZATION` to reason_code enum (23 total, up from 21). Updated OUT OF SCOPE debt note to distinguish venture debt (in scope) from corporate bond issuances (out of scope). Added Example 4 (VC round). Updated CRITICAL block with invented-value examples for funding types. |
| 0.6 | 2026-08-18 | Added `PIPE` to the RELEVANT reason_code enum (24 total, up from 23) and to IN SCOPE, gated on the source explicitly naming the structure. RELEVANT rather than NOT_RELEVANT on purpose: marking it not-relevant would drop the row before deal-type classification and lose the recognized-exclusion record. Added invented-value mappings for PIPE_FINANCING / PIPE_TRANSACTION / PIPE_OFFERING / PRIVATE_INVESTMENT_IN_PUBLIC_EQUITY. |
| 0.7 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.8 | 2026-08-21 | **The reason_code vocabulary is now delivered to the model.** The authoritative 24-code list moved from §6 into the §4 system prompt; §4 and §5 are the only sections `load_prompt_file` sends, so the list previously reached nothing. The prompt asserted the codes were "listed in the in-scope and out-of-scope enumerations above" — prose category descriptions containing no enum values — so the model saw codes only incidentally, as the right-hand side of the invented-value correction list. Ten codes, including the in-scope `MERGER_ANNOUNCEMENT`, `SPIN_OFF_OR_SPLIT`, `REVERSE_MERGER` and `JOINT_VENTURE`, were never delivered at all; eight of those have no alias path and folded into the catch-alls. Taxonomy, side assignments, glosses, alias table and relevancy semantics are unchanged — this delivers the existing vocabulary, it does not redefine it. |
| 0.9 | 2026-08-25 | **A sale process is not a transaction.** One EDGE CASES bullet: where a source describes only a process for seeking a buyer — formal sale process, court-supervised or bankruptcy auction, strategic-alternatives review, stated intention to sell, solicitation of bids, or advisors engaged to pursue a sale — and no counterparty to a specific acquisition or divestiture has been announced or agreed, classify NOT_RELEVANT with OTHER_NOT_RELEVANT. The counterparty test is scoped to this boundary and is not a general transaction requirement. A later source naming a stalking-horse bidder, winning bidder, definitive sale agreement or completed sale may be RELEVANT on its own terms. No reason code added (24 unchanged); the in-scope asset-sale/divestiture line and the rumor bullet are untouched. §7 Example 6 added as documentation (outside the delivered fence). |
