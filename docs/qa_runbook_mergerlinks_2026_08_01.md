# QA Runbook — LLM Extraction Review (MergerLinks batch)

**Date:** 2026-08-01
**Purpose:** Record the QA loop (test → improve → run → manual review) for the blind
extraction test against a MergerLinks reference set, the findings, the decisions
locked, what remains to be built, and what must be confirmed by manual validation.

> **Framing:** production ingestion will be RSS-fed. The CSV/URL ingest, the
> Businesswire same-origin fetch bridge, and the scorecard builders used here are
> **QA scaffolding** (kept in the scratchpad, deliberately *not* committed). This
> runbook captures the *product* conclusions — prompt/code/schema changes — that
> the QA loop produced.

---

## 1. What was run

- **Set:** MergerLinks export `ML_LLM_20260731.csv`, 91 deals with verified
  reference values (deal value, EV, net debt, equity value, revenue, EBITDA,
  EV/EBITDA, premium, advisors, seller, dates) and 1+ `source_urls` per deal.
- **Method:** *blind* extraction — only the scraped source text is fed to the LLM;
  the MergerLinks values are stashed as `source_raw.notes.csv_reference` (the
  answer key) and used only for scoring.
- **Ingest:** one best source per deal (Businesswire-preferred — usually the
  official release). 89/91 auto; 2 recovered manually (Caverion, Saint-Gobain).
- **Pipeline:** V2 (`high_confidence_extraction` v0.12 + the fixes committed in
  `csv-url-harness-and-v2-fixes` / PR #1).

## 2. Cost baseline (corrected)

- **91-deal run ≈ $12.85 → ~$0.141 / txn** at the actual Anthropic prices
  (Opus 4.7 ≈ $5/$25 per M in/out; Haiku 4.5 ≈ $1/$5). In line with the prior
  ~$0.135/txn benchmark. (An earlier estimate of $0.42/txn used wrong $15/$75
  Opus pricing — disregard it.)
- **SEC agreement extraction was a mistake for this test** and was killed mid-run.
  It pulled long 8-Ks / merger agreements and ran Opus over them. It should never
  run for a blind press-release test, and even in production the correct pattern
  is **query the sec-api structured fields — never LLM the filing.**
  Use `--mode=llm-only` for URL-only QA runs.

## 3. Scorecard (blind extraction vs MergerLinks answer key)

- **Deal value:** 28/30 match where both present (currency-adjusted USD→GBP;
  MergerLinks normalizes to GBP). We also captured EV on 15 deals ML left blank.
- **Announced date:** 85/92 match.
- **Closed date:** 32 match, 22 missed (see finding #1).
- **Advisors:** ~67% recall where ML has them — but confounded by one-URL ingest
  and ML's non-URL advisor feeds (finding #2).
- **Revenue/EBITDA/multiples:** sparse — mostly a source limitation, not an
  extraction failure (findings #5, #8).

## 4. Findings → improve

| # | Finding | Root cause | Fix | Type |
|---|---------|-----------|-----|------|
| 1 | Close dates missed (21/22 are same-day completions) | Rule (b) doesn't fire on announcements lacking explicit "completed" language | Strengthen rule (b): "no forward/pending-close language ⇒ closed on announcement, `closed_date = announced_date`" + paired example (closed-on-announcement vs. pending). Funding rounds / minority investments close on announcement by nature. | Prompt |
| 2 | Advisor recall ~67% | One-URL ingest under-reads multi-source deals (1-URL 24% miss vs 2+-URL 42% miss); ML also has non-URL advisor feeds. Confirmed: Montagu/BMC (Jefferies) & Bertram/Bluebird (Canaccord Genuity) advisors were in the *prnewswire* URLs we didn't read. | Multi-source ingest + cluster (recovers most misses); **also capture advisor *people* + firm + side** — releases name individual bankers (e.g. Canaccord Genuity/Sanjay Chadda/Lexia Schwartz sell-side; BrightTower/Juan Mejia buy-side) — via the participant model. Accept that some ML advisors come from feeds not in any URL. | Ingest / data / schema |
| 3 | Centor over-split (Apax/Centor+PPP → 2 txns) | SPLIT rule fired on a combine-into-one-platform deal | Tighten SPLIT: don't split when targets share one consideration / combine into one platform | Prompt |
| 4 | Currency present, value null | Currency defaults when value not extracted | Null the currency when value is null | Prompt |
| 5 | Stated revenue/EBITDA missed (~3–4) | `target_financials` capture too weak | Tighten `target_financials` extraction (Norwegian, Fox/Roku, Gilat, Kesko were in-text but missed) | Prompt |
| 6 | Periods: ANNUAL with no year; ARR mis-tagged | Model won't hallucinate a year (correct), and ARR conflated with fiscal annual | If annual & no year ⇒ anchor `period_end` to most-recent completed FY vs. announcement (or LTM-at-announcement). Add a distinct RUN_RATE tag for ARR. | Prompt |
| 7 | `value_type = UNDISCLOSED`; disclosure flag overblown | `value_type` doubles as a disclosure state; single `financials_disclosure_status` conflates independent axes | Drop UNDISCLOSED from `value_type` (basis or null only). Replace `financials_disclosure_status` with **two axes**: `deal_value_disclosure` (TV/EV/EQV) + `target_financials_disclosure` (rev/EBITDA/ARR), each DISCLOSED / UNDISCLOSED / UNKNOWN, derived from **metric presence + explicit language only** — never inferred across axes. | Prompt + schema |
| 8 | Derived valuations not computed (equity, implied equity, EV, multiples all ~0) | No derivation step; share counts (SEC) not fetched; minority stakes not grossed up | **LLM captures primitives; a deterministic job computes derived values** — never the LLM. See §5. | Code + schema + integration |
| 9 | Presentation shows "Unknown" | Data-layer nuance leaks to the surface | Platform rollup per axis: **Disclosed / Not Disclosed** only; keep UNKNOWN/UNDISCLOSED nuance internal | Presentation |
| 10 | **Aggregation nulls clobber real values** — Olin/Huntsman: PR extraction captured `consideration_type=stock`, but the higher-tier SEC exhibit's NULL overrode it, so the final record shows `None` | Best-of aggregation prioritizes by source tier and lets a higher-tier **null** win over a lower-tier **value** | A null must never beat a populated value — aggregation should coalesce to the highest-tier *non-null*. **Fix before scaling multi-source**, or more sources degrade fields. | Code (aggregate) |
| 11 | Exchange ratio / ownership split not captured (Olin/Huntsman: `0.5476`, `54.5%/45.5%` were **in the PR**) | No `exchange_ratio` field; consideration capture incomplete for stock deals | Add `exchange_ratio` (+ ownership split) as captured primitives; feeds the stock-deal equity derivation (finding #8). Was in the press release — not a SEC gap. | Prompt + schema |

## 5. Decision locked — value model architecture

**The LLM captures primitives. A separate deterministic job calculates everything derived. The LLM never does arithmetic.**

| Capture (LLM — from the release) | Compute (job — deterministic, free) |
|---|---|
| per-share price | `equity = per_share × shares` (shares from sec-api) |
| stated aggregate value | (used directly) |
| stake value + `pct_acquired` | `implied_equity = stake ÷ pct` (minority gross-up) |
| net debt | `EV = equity + net_debt` |
| revenue, EBITDA, ARR | `EV/revenue`, `EV/EBITDA` on derived/implied EV |

Rationale: accuracy (the prompt already forbids the model reverse-calculating),
zero token cost, and consistency. Structured inputs (share counts, and net debt
where in filings) come from the **sec-api** — not by LLM-ing documents.

**Two-axis disclosure** (replaces the single flag; the axes are nearly disjoint —
37 deals disclose a value, 10 a financial, only 5 both, 57 neither):
- `deal_value_disclosure` — DISCLOSED if any of TV/EV/EQV present; UNDISCLOSED if
  source explicitly says so; UNKNOWN if silent.
- `target_financials_disclosure` — same, over revenue/EBITDA/ARR.
- Each axis driven by metric presence + explicit statement only.

### Worked examples (why the derivation job matters)

- **Tracsis / Mistral Data** — captured EV £48M + revenue £13M (LTM 2026-03-31); `EV/revenue = 3.7×` is computable but never computed. The £4M EBITDA was in the **official LSE RNS** (a source URL we skipped) → would give `EV/EBITDA = 12×`. Also carried an orphan EBITDA period (LTM/2026-03-31) with no value.
- **Altaris / Simulations Plus** — captured `per_share = $18.50`; the **$375M aggregate was in the headline** and missed; `$18.50 × shares ≈ $375M` never computed. Equity value should be both captured and derivable.
- **Sixth Street / Pinnacle Gas** — 27% stake for $600M; `implied equity = $600M ÷ 0.27 ≈ $2.22B` never computed (we store only the stake).
- **Olin / Huntsman (stock-for-stock)** — captured `consideration = stock` and nothing quantitative. Valuation needs: exchange ratio (extract as a primitive from the 8-K / merger-agreement section — not LLM the whole doc), target shares (sec-api **structured** company-facts), acquirer price → `implied equity = exchange_ratio × acquirer_price × target_shares`. A SEC Exhibit 99 (press-release version) *was* pulled but lacks the ratio/share counts; the structured pull was never wired.

## 6. To be addressed (prioritized)

**A. Prompt (capture) — `high_confidence_extraction.md`**
1. Close-date rule (b) + paired example (#1).
2. SPLIT tightening for combine-into-one deals (#3).
3. `target_financials` capture; net-debt capture; stated-aggregate capture when a per-share is present (#5, #8, #4).
4. Period anchoring + RUN_RATE tag (#6).
5. Remove UNDISCLOSED from `value_type` (#7).

**B. Schema**
6. `deal_value_disclosure` + `target_financials_disclosure` (replace `financials_disclosure_status`).
7. Derived fields: `equity_value` (computed), `implied_equity_value`, computed `enterprise_value`, computed multiples.

**C. Code (derive) — new valuation-derivation job**
8. Equity / implied-equity / EV / multiples from captured primitives + sec-api shares. Deterministic, no LLM.

**D. Integration**
9. sec-api **structured** fetch for shares outstanding (and net debt where present) — replacing the killed LLM-over-document SEC path.

**E. Presentation**
10. Disclosed / Not-Disclosed rollup per axis; hide UNKNOWN.

## 7. Manual validation checklist (confirm before shipping each change)

- [ ] **Close-date rule (#1):** undated same-day completions flip to CLOSED
      **without** over-flipping genuinely-pending deals — verify Mutares/Free2move
      and Yum/Pizza Hut ("subject to regulatory approvals") stay PENDING.
- [ ] **Aggregation null-clobber (#10):** on a multi-source deal, confirm a populated field from one source is NOT overwritten by a null from a higher-tier source (re-check Olin/Huntsman `consideration_type=stock` survives).
- [ ] **SPLIT tightening (#3):** Apax/Centor+PPP collapses to ONE transaction,
      while a genuine multi-deal (Colt → NorthC + DWS) still correctly splits.
- [ ] **Two-axis disclosure (#7):** Clear Water reads deal-value=Not Disclosed,
      financials=Disclosed (revenue £3M). Bluebird/4E read Not Disclosed on both,
      and are UNKNOWN (silent), not UNDISCLOSED.
- [ ] **Derived valuations (#8):** for deals with both sides, recomputed
      equity/implied-equity/EV/multiples match MergerLinks — spot-check Simulations
      Plus ($18.50 × shares ≈ $375M), Sixth Street/Pinnacle (27% → implied ≈ $2.2B).
- [ ] **Financials capture (#5):** re-check the in-text-but-missed figures
      (Norwegian Air/Nordic, Fox/Roku, Gilat, Kesko).
- [ ] **Net debt:** confirm whether it is captured at all today; validate
      `EV = equity + net_debt` on a sample.
- [ ] **Periods (#6):** annual-without-year deals now anchored; ARR tagged RUN_RATE
      (Camber/Respond.io $35M ARR).
- [ ] **Cost:** a clean `--mode=llm-only` re-run lands ≈ $0.135–0.14/txn (no SEC).

## 8. Related artifacts

- Fixes already committed: branch `csv-url-harness-and-v2-fixes` (`06b2dfd`),
  PR #1 (run.py NameError, migration ledger, stage/prompt sync, status derivation,
  relevancy normalize).
- Scorecard/worksheet for this run: `exports/ML_worksheet.csv`,
  `exports/ML_scorecard.csv`, `exports/transactions_run_20260801_110339.csv`
  (git-ignored; regenerate from the run DB).
