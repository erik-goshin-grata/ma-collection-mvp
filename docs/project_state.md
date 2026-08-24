# Project State
**Updated:** 2026-08-14
**Last commit:** `52c4e94` Add Grata V2 inventory and data dictionary
**Branch:** `main`

> ## ⏳ **DATED SNAPSHOT — 2026-08-14. Do not read as current state.**
> Everything below is the project as it stood on that date and has not been refreshed
> since. The prompt versions listed here in particular are superseded; the live table is
> `docs/prompt_versions.md`. Individual statements corrected in place are marked inline.

---

## Current Checkpoint: Transaction Value Model + Minority Cleanup

V2 prompt vocabulary and the funding event path are landed and operational (prior
drop). The active workstream is the **two-tier transaction value model** — Tier 1
as-transacted (`equity_value` stake-level, `transaction_value`, `transaction_size`)
vs Tier 2 100%-basis (`implied_equity_value`, `implied_enterprise_value`, the only
legal multiple numerators). Design is landed in `docs/decisions.md` (2026-08-10
entries) and `docs/spec_transaction_value_model.md`; code landings §4.1/4.2/4.7 are
in, and the implied-enterprise-value rewire is now implemented in the harness.
Canonical `implied_enterprise_value` is populated from source-stated whole-company
EV or from `implied_equity_value + net_debt`; reported/manual `net_debt` is
preferred, otherwise `net_debt = total_debt - Cash_ST` only when both components
exist. Missing debt or cash/ST remains null; no zero assumptions. Legacy
`enterprise_value` remains only as a compatibility mirror pending later inventory
and reorganization. The first §4.2 re-aggregation is discharged on the two live
DBs; the second re-aggregation remains owed after broader `total_debt` + `Cash_ST`
collection/extraction. All 14 stages + the funding branch remain wired and running.

Minority cleanup is now accepted and implemented in this validation harness:
`MINORITY_INVESTMENT` is no longer a validated core classifier output, minority
status is derived as `is_minority`, and explicit ownership step-ups are captured
with nullable `stake_transition_type` (`NULL`, not `UNKNOWN`, means insufficient
explicit evidence). `is_minority` is a transaction feature/flag, not a proxy for
post-transaction control state; current `pct_acquired` takes precedence over
ownership-history labels embedded in `stake_transition_type`. This does not
change Grata production schema/enums.

Validation checkpoint: `NEW_MAJORITY_STAKE` is implemented. The 2026-08-12
50-ish story PredictLeads validation corpus completed with no aggregation
execution failures and no core `MINORITY_INVESTMENT` outputs. MediaWorks/SEG
exposed and validated independent preservation of source-supported
`TRANSACTION_VALUE` and `ENTERPRISE_VALUE` semantics in aggregation. Erebor's
`VC_ROUND` classification passed source review with a source-quality caveat.

Newest Grata reconciliation/design documents:
- `docs/grata_v2_reconciliation_2026_08_17.md` — **read this first.** The harness delta
  against the two below: what is implemented and validated, what is recommended for Grata
  ENG, what is already adequate in Grata, and what is deferred or not yet live-validated.
  Carries the Adopt / Keep / Defer list and the remaining open schema questions.
- `docs/grata_v2_inventory_and_recommendations.md` — v0.3, redlined inline 2026-08-17
- `docs/grata_v2_data_dictionary.md` — v0.3, redlined inline 2026-08-17

Treat the latter two as current inventory/recommendation inputs, not as proof that their
recommendations are implemented in this harness or in Grata production. The reconciliation
is where that distinction is actually drawn — and its largest caveat is that the
balance-sheet half of the value model (`total_debt`, `cash_st`, both calculated EV bases)
is **fixture-validated only**, with zero live corpus rows.

---

## Schema

Schema of record is `schema/*.sql` (001+002+003) **plus** the additive ALTERs in
`db.py _apply_migrations`, which run on every `init_db`. `test_schema_convergence.py`
asserts all paths reach one canonical column set (verified across 10 historical DBs).

| Source | Applied | Contents |
|---|---|---|
| `001_initial.sql` | Yes | Base schema — 16 tables incl. `staging_extraction`, `transaction_record`, `transaction_field_observation`, `staging_investor` |
| `002_v2_prompt_alignment.sql` | Yes | 12 new nullable V2 columns on `staging_extraction` and `transaction_record` |
| `003_funding_path.sql` | Yes | `staging_investor` table + funding scalar columns |
| `db.py _apply_migrations` ALTERs | Yes | Value-model / harness columns not in historical DBs: `investment_amount`, `deal_value_currency`, `total_debt`, `transaction_value`, `transaction_value_basis`, `pct_acquired_source`, `is_minority`, `stake_transition_type`, precision/round observation columns |

---

## Prompt Versions

See `docs/prompt_versions.md` for full cross-prompt version table.

Current versions:
- `relevancy_filter` 0.5
- `deal_type_classifier` 0.7 (minority-as-flag routing; no core `MINORITY_INVESTMENT`)
- `high_confidence_extraction` 0.14 (0.14: nullable explicit `stake_transition_type`)
- `funding_hc_extraction` 0.1 (NEW)
- `funding_lc_extraction` 0.1 (NEW — **never became an executable contract; see the correction below**)
- `low_confidence_extraction` 0.5
- `aggregation` 0.4
- `deal_summary` 0.9
- `strategic_rationale` 0.5
- `prompt_conventions` 0.3
- Agreement prompts (5): unchanged at current versions

---

## Stage Status

| Stage | Module | Notes |
|---|---|---|
| 1 | `scrape_pr_newswire` | Running |
| 2 | `relevancy_filter` | v0.5 — funding events in scope |
| 3 | `deal_type_classify` | v0.7 — V2 event types, funding types classifiable; `MINORITY_INVESTMENT` rejected as core output |
| 4a | `high_confidence_extract` | v0.14 — V2 vocabulary; §4.1 capital-raised precondition + `round_size`; nullable `stake_transition_type` when explicit |
| 4b | `funding_hc_extract` | v0.1 — NEW; routes VC_ROUND/GROWTH_EQUITY/VENTURE_DEBT |
| 5 | `sec_trigger_detect` | Running |
| 6 | `sec_enrich` | Extended lookback/lookahead window |
| 7 | `low_confidence_extract` | v0.5 |
| 8 | `entity_cluster` | Running |
| 9 | `aggregate` | V2 + funding fields; multiples gated off funding; value-model §4.1/4.2 landed; derives transaction-feature `is_minority` from current pct/explicit minority evidence before transition fallback |
| 10 | `sec_documents` | Running |
| 11 | `agreement_extract` | Running |
| 12 | `summarize` | v0.9 — V2 input fields; M&A framing only (funding framing v0.10 pending) |
| 13 | `rationale_tag` | v0.5 |
| 14 | `export` | Running |

---

## Configuration

Key config flags:
- `AGGREGATION_READ_SOURCE=observation` — **default as of 2026-08-17.** Stage 9 reads
  `transaction_field_observation`. `staging` remains explicitly selectable as the
  rollback/debug path. The default is defined once in
  `config.DEFAULT_AGGREGATION_READ_SOURCE` and imported by `stages/aggregate.py`.
  See decisions.md "Stage 9 Reads the Observation Ledger by Default".
- `LLM_PROVIDER=anthropic` — default. OpenAI provider available via `LLM_PROVIDER=openai`.
- `opus_model=claude-opus-4-7`
- `sonnet_model=claude-sonnet-4-6` — Sonnet tier (classify/HC/funding-HC/summary), wired 2026-08-03
- `haiku_model=claude-haiku-4-5-20251001`
- `SEC_LOOKBACK_DAYS=30` / `SEC_LOOKAHEAD_DAYS=7`

---

## Known Gaps / Deferred Work

**Equity scope conflation — FIXED 2026-08-17 (forward-looking):**
`equity_value` is stake-level only. `EQUITY_VALUE` no longer admits market
capitalization, which became its own `MARKET_CAPITALIZATION` type (HC prompt 0.18) —
retained as a fact, never canonical consideration. `PER_SHARE_X_SHARES` is gated to
`pct_acquired == 100` and is never scaled below it. Guarded by
`scripts/test_equity_value_scope.py`; see decisions.md, "equity_value Is Stake-Level
Only; Market Cap Is Its Own Type".
- **Live diagnostic was clean:** 92 records, 7 with `equity_value`, 1 exposed at
  `pct < 100`, 0 `PER_SHARE_X_SHARES`, 0 confirmed market-cap candidates. Text
  matching is heuristic, so that is *no evidence found*, **not proof of absence**.
- **What is still open:** the taxonomy half applies only to rows extracted at 0.18+.
  It does not retroactively clean the corpus, and no re-extraction is scheduled —
  Path B remains deferred. Existing rows keep whatever scope semantics produced them.

**Legacy funding value-mapping remediation — CLOSED 2026-08-17. Both batches applied.**
Batch 1 (nine rows) and batch 2 (Aston Power $20M, AttoTude $52M, Cellares $327M) are
applied and re-aggregated on `read_source=observation`. **Model-integrity assertion
passed — 0 funding-family rows carry `transaction_size_basis = TRANSACTION_VALUE`.**

**Cellares — live verified, closed.** The divergent case, and the one that exercised the
whole chain end to end:

| field | live value |
|---|---|
| `round_size` | 327,000,000 |
| `transaction_size` | 327,000,000 |
| `transaction_size_basis` | `ROUND_SIZE` |
| `transaction_value` | NULL |
| `investment_amount` | NULL |

Its staged $50M is Prime Radiant's *check* inside a $327M Series D. The check survives as
provenance in `staging_extraction.value_amount` and the observation ledger, and is
correctly absent from every canonical magnitude field. Deriving it required the
`MANUAL_REMEDIATION` read-path fix (admission **and** precedence) — see decisions.md,
"Manual Remediations Are First-Class Observations".

**Chronograph — RESOLVED 2026-08-18 by convention, not by infrastructure.**
"over $140 million" is recorded at its stated anchor: `round_size` = `transaction_size` =
140,000,000, basis `ROUND_SIZE`, with the original wording preserved in
`source_raw.clean_text` and quoted in the remediation note. This is a
researcher-normalization convention, **not** a claim the source stated exactly $140M —
which is why the wording has to survive in provenance. It covers a **single stated
anchor** only; ranges, "up to" ceilings, approximations and rumoured figures are still
deferred and still leave `round_size` NULL, each to be decided from a real example. No
qualifier schema work was done or is planned. `batch3_qualified_anchor` in the planner is
prepared and **not applied**; `UNRESOLVED` is now empty. See decisions.md, "Qualified
Anchors: A Researcher-Normalization Convention, Not Qualifier Infrastructure".

**Still open from this workstream (deliberately separate, not blocking):**
- **PIPE** — resolved 2026-08-18 as a recognized-but-not-profiled exclusion at
  classification time, not as a `transaction_size` rung. Recorded below.
- **Coverage review found four false positives** from numeric proximity — investor AUM,
  cumulative firm capital, and a post-money valuation read as round sizes. The
  classifier now requires the amount to be *bound* to the target's financing event and
  judges each figure on its own span. See decisions.md, "Funding Coverage Review:
  Binding, Not Proximity".
- `VENTURE_DEBT` remains out of scope.

**(superseded) Original 10-row finding:**
Ten `VC_ROUND`/`GROWTH_EQUITY` rows carry a raise in `transaction_value` with
`round_size` NULL. **All ten are at HC prompt 0.12; none at 0.13+**, so this is legacy
data, not a defect in the current funding extraction path — Stage 4a processed funding
rows until 2026-08-07 and had no `round_size` write or capital-raised precondition until
0.13. 8/10 have heuristic round language; 2 (Cellares, Rejoni) need a source read.
- **Guard landed:** Stage 9 now family-gates `transaction_value` and `equity_value`
  (decisions.md, "Funding Events Derive No transaction_value or equity_value"), so
  re-aggregation can no longer regenerate M&A values from these rows. The guard refuses;
  it never moves an amount into `round_size`.
- **Remediation is per-row and human-approved.** No bulk copy. The amount stays visible
  in `investment_amount`, `staging_extraction.value_amount`, and the observation ledger.
- **`VENTURE_DEBT` is out of scope** — `round_size` vs `facility_size` is a separate
  decision, not to be settled while fixing historical VC/Growth rows.
- The retired XLSX shadow masked this by falling through to `transaction_value`; the
  family-keyed `transaction_size` rule exposing null is what surfaced it.

**Funding HC baseline — 8/8 on real text (2026-08-17):**
`funding_hc_extraction` 0.1 separates round size, valuation, cumulative funding and
investor check correctly on the eight verbatim-article fixtures
(`scripts/funding_hc_baseline_fixtures.py`, run via
`scripts/run_funding_hc_baseline.py`). **No prompt or schema change.** The legacy corpus
defects traced to HC 0.12, which predated the funding path — not to this prompt.
- Computomic `UNKNOWN` was **correct**; the fixture expectation was wrong. Silence is
  `UNKNOWN`; only an explicit "terms were not disclosed" is `UNDISCLOSED`. Both prompts
  agree, so there is no taxonomy inconsistency to fix.
- Chronograph's "over $140M" was recognised economically and carried unscored. The
  qualified-anchor convention (2026-08-18) resolved it to 140,000,000. **That expectation
  has never been run against the live model** — the 8/8 result predates it, so the next
  baseline run may report 8/8 or 7/8. Either is information, not a regression.
- **Investor/fund AUM is not a structural gap** (earlier framing retracted). Discarding
  an investor's own size is correct for magnitude extraction and needs no field.
- The eight fixtures are permanent and must stay verbatim, never paraphrased.

**PIPE: recognized, not profiled (2026-08-18).**
The Funding family is `{VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT}`, **unchanged**, and the
`transaction_size` waterfall is untouched. What changed is upstream, at classification.

An explicitly recognized PIPE now gets its own seat: Stage 3 stamps
`v2_event_type = 'PIPE'` and the terminal status `RECOGNIZED_NOT_PROFILED`. Both
extraction gates select `status = 'CLASSIFIED'`, so the row is skipped by Stage 4 and
Stage 4a **without either gate naming PIPE**, and never reaches clustering or
aggregation — no `round_size`, no `transaction_size`, no valuation, no
`transaction_record`.

This closes a real leak rather than a theoretical one. Declining to call a PIPE a
funding round used to route it into M&A instead: Stage 4's gate is
`NOT IN ('VC_ROUND','GROWTH_EQUITY','VENTURE_DEBT')`, and `UNKNOWN` satisfies it, so an
`UNKNOWN` PIPE fell into M&A high-confidence extraction and emerged as a
transaction_record with M&A semantics.

- **Recognition reads the transaction language, never the provider.** The same PIPE from
  PredictLeads, PR Newswire or an SEC filing is treated identically; the functions take
  no source parameter and the regression asserts that structurally.
- **Narrow by construction.** Only the acronym bound to a financing construction, or the
  phrase spelled out. A private placement, convertible note, preferred issuance or
  registered direct offering is not a PIPE unless the source says so.
- **Structural types are never displaced.** `ACQUISITION`, `MERGER`, `REVERSE_MERGER`,
  `SPIN_OFF`, `SPLIT_OFF`, `JOINT_VENTURE`, `RECAPITALIZATION` are left alone —
  "$150 million PIPE" is standard de-SPAC language and that deal is in scope. Only
  `UNKNOWN` and the funding family can be overridden.
- **No schema change.** Enums are application-layer in this repo (`schema/001_initial.sql`
  §7), so `PIPE` and `RECOGNIZED_NOT_PROFILED` are new values, not new columns.
- **Promotion is one line.** Stop stamping the terminal status and the row flows as
  `CLASSIFIED`; the only open question then is which extractor owns it.

**PIPE is now first-class in the prompt vocabulary too (second commit, same day).**
`deal_type_classifier` 0.8 offers `PIPE` as a twelfth type, gated on the source
explicitly naming the structure and carrying a negative list (private placement,
convertible notes or preferred, registered direct, ATM/underwritten offering) so the new
bucket does not become a catch-all. `relevancy_filter` 0.6 adds a `PIPE` reason code on
the **RELEVANT** side — deliberately not NOT_RELEVANT, which would drop the row before
Stage 3 and destroy the recognized-exclusion record it exists to create.

A classifier-emitted `PIPE` is excluded on the same terminal status as a
recognizer-driven one. That branch is not cosmetic: `PIPE` is not in the funding family,
so leaving a self-declared PIPE at `CLASSIFIED` would satisfy Stage 4's `NOT IN` gate and
send it into M&A extraction — the original leak, reopened by the change meant to close
it. Provenance records `recognition_form` (`CLASSIFIER` vs `ACRONYM`/`EXPANDED`) and
`corroborated`, i.e. whether the deterministic recognizer independently found explicit
PIPE language. An uncorroborated classifier verdict is still honoured — the exclusion
deletes nothing and is reversible — but it is flagged, so an over-classifying prompt is
findable rather than quietly dropping deals.

**No backfill.** Rows classified before this change keep whatever they were given; the
corpus is test/validation data and cleaning it was explicitly declined.

**Grata backlog: five Product semantics decided 2026-08-19; no Product decisions left open.**
Recorded in `grata_v2_inventory_and_recommendations.md` §R and decisions.md, "Five Product
Semantics Decided". Reconciliation is precedence-based with fact identity established first
and lifecycle events explicitly not fact updates (R1); USD normalization optional and
derived (R2); three currency concepts with **no inheritance** of transaction currency by a
metric of unknown native currency (R3); period coherence stays exact (R4); source ≠
transaction (R5). Nine invariants (R6) replace the Silver/Gold architecture question, which
is now ENG's and **no longer gates §E4**.

**Seven items remain, none of them a Product decision.** Separated by owner because the
groups unblock differently: ENG items are schedulable now, evidence-blocked items cannot be
scheduled at all, and external asks wait on another team.

*ENG implementation — 3*

| item | note |
| --- | --- |
| Reconciliation / supersession **key** + implementation | Semantics settled by R1; the key is unlikely to be single-valued (immutable filings vs mutable web sources) |
| Silver/Gold placement against the R6 invariants | Product does not prescribe the layer; no longer gates §E4 |
| `PER_SHARE_X_SHARES` wiring | `aggregate.py` hard-codes `sec_shares = None` while `agreement_extract.py` already writes `transaction_security.shares_outstanding` with a diluted total and quality marker. Live population unverified; coverage limited to agreement-bearing deals |

*Evidence-blocked — 2*

| item | what would unblock it |
| --- | --- |
| Multi-event value contamination: live or theoretical? | the Ensysce source text |
| Live debt/cash path validation | a real sample; corpus has zero rows and none will be manufactured |

*External-system asks — 2*

| item | asked of |
| --- | --- |
| 14 ML definition/example items (§Q7) | MergerLinks |
| `value_usd_basis` semantics | Grata |

Closed as stale: the two transaction-size Grata asks and the eleven Adopt recommendations
are acknowledgement items, not open questions.

**PIPE workstream CLOSED 2026-08-18 — recognition is lexical only, and that is known to
be incomplete.** Three real examples (Silvaco/Micron, MySize, Ensysce/Cy Biopharma)
showed that a structural PIPE need not use the word, that the ELOC boundary is undecided,
and that a single source can carry an acquisition *and* a concurrent placement. The
findings are recorded in decisions.md, "PIPE: Unresolved Architecture and Product
Findings"; no further PIPE code is planned.

Backlog left behind, none of it started:

| item | blocked on |
| --- | --- |
| Structural recognition (public issuer + primary private issuance to limited/named investors + equity-linked), with 144A/QIB, registered, private-issuer and secondary carve-outs | reading the three sources — all outbound web including `sec.gov` is egress-blocked from the container, and the corpus DB is not present |
| MySize ELOC / committed-equity boundary | the full MySize source. **Do not decide either way without it.** |
| Value contamination on a compound source (acquisition + concurrent placement) | the Ensysce text — determines whether the risk is live or theoretical |
| Event/component-level exclusion rather than source-level | **not a PIPE patch.** Belongs to the inventory assessment / Grata reconciliation as an architecture question |

Verified at `79a93f9`: the shipped recognizer returns nothing for all three headlines, so
Silvaco seated at `UNKNOWN` still proceeds into M&A HC. The lexical path closed the leak
for releases that name the structure; it did not close it for releases that only have the
structure.

**Funding path (partial):**
- ~~`stages/funding_lc_extract.py` — not written; prompt exists~~ — **corrected 2026-08-24.**
  This was never a pending stage. `docs/funding_path_design.md` §4 routes funding events
  through the existing Stage 7 LC (*"unchanged; deal-type-agnostic"*) and introduces Funding
  HC as the only funding-specific stage. The draft prompt is retained at
  `docs/historical_funding_lc_extraction_prompt.md`; no Funding LC stage is required.
- `adapters/sec_api.py` Form D extension — deferred
- `deal_summary` funding framing — deferred to v0.10

**V2 schema alignment (deferred):**
- `transaction_participant` → `transaction_party` rename (3.32a tables)
- `financial_metric` as deal value write target
- `transaction_event_history` as date write target
- Agreement observation supersession

**Open-deal monitoring (placeholder — later):**
- Capability we do not have: keep watching a deal in `ANNOUNCED` status for
  follow-on SEC filings until it closes or dies, and feed them back in. Today
  the pipeline is one-shot per discovery.
- Stop conditions are the lifecycle filings we already fetch: **2.01 (close)**
  and **1.02 (termination)** — keep these grouped with 1.01/8.01 when designing.
- Half-built already: the observation ledger's diff-surfacing (a value moving
  across filings over time) and the `event_history` ANNOUNCED→AMENDED→CLOSED/
  TERMINATED concept. Missing piece is the scheduler that re-checks EDGAR for
  new filings on still-open deals. Not designed; do not start without a decision.

**Internal consistency guardrails:**
- Field inventory + parity test — method decided (generate by origin);
  `docs/spec_field_parity_test.md` is landed and its ON HOLD gate is lifted.
  Checks 2–4 test this repo's internal consistency and do not depend on Grata
  schema/enum locations. See value-model handoff §6 and decisions.md
  "Field Inventory Method and Naming Conventions".
- _(The former `docs/enum_schema_gaps.md` reference was removed — that file does
  not exist in the repo. Funding-valuation scope since resolved:
  `post_money_valuation` only, no implied EV/multiples — decisions.md
  "Funding Valuation Scope".)_

**Validation:**
- Minority cleanup validated on a four-story live set:
  Lumina/TNQTech now yields `ACQUISITION`, `pct_acquired=20`,
  `stake_transition_type=MAJORITY_ACQUIRE_REMAINING`, `is_minority=1`;
  LMPG/Platinum remains evidence-limited and stable; Lydian remains `VC_ROUND`;
  Paradium/InfoSentience remains full `ACQUISITION`.
- Funding path test corpus **built** — `data/pl_funding.db` (68 stranded VC/venture-debt
  rounds + the KG / 10x `MINORITY_INVESTMENT` cases); funding run surfaced bugs #5–#9,
  all fixed and committed.
- Observation coverage and round-currency work is landed: the observation write
  path covers every field Stage 9 reads, Stage 4b dual-writes funding observations,
  and `round_currency` feeds the three-source unanimity rule for `deal_value_currency`.
- The implied-equity violation found during §4.2 verification is fixed:
  `implied_equity_value` derives from `equity_value` only, never from an
  unqualified `transaction_value` or `post_money_valuation`.
- Currency + period anchoring is implemented and **validated on the live corpus** by
  the Path A re-aggregation (2026-08-17): qualifiers anchor to the source of their own
  amount, and the run produced no borrowed-qualifier losses because this corpus
  contains no cross-source qualifier dependency. The fixture still reproduces the
  defect. What remains unvalidated is the *balance-sheet* half — zero live debt/cash
  cases exist, so the debt-inclusive bases have never fired on real data.
  SEC enrichment remains limited to transaction/merger documents, relevant
  exhibits such as 99s, and manually collected values, not general financial-
  statement mining.
- First §4.2 re-aggregation discharged on `pl_funding.db` and `ma_mvp.db`; the
  second re-aggregation remains owed after `total_debt` + `Cash_ST`.

---

## Next Steps

Current queue (source: `docs/session_handoff_2026_08_10_value_model.md`):

1. ~~**Currency + period anchoring** (§2.10 items 1–2)~~ — **landed 2026-08-17.**
   Financial qualifiers now anchor to the source of their own amount; an unstated
   qualifier is null rather than borrowed. `implied_enterprise_value` refuses a
   calculated basis across two known, differing currencies. Anchor columns
   `net_debt_currency` / `total_debt_currency` / `cash_st_currency` /
   `balance_sheet_as_of_date` added to `transaction_record`. See decisions.md
   "Financial Qualifiers Anchor to the Source of Their Own Amount" and
   `scripts/test_currency_period_anchoring.py`.
   **Discharged:** HC prompt 0.17 populates the currency anchors, and Stage 4a
   persists them (2026-08-17). The cross-currency guard is live rather than dormant.
   **Still deliberately undecided:** the period-coherence tolerance between
   `balance_sheet_as_of_date` and the multiple denominator's period. No tolerance was
   invented; the as-of date is preserved so corpus behaviour can be evaluated once a
   real debt/cash case exists.
2. ~~**`total_debt` + `Cash_ST` as `target_financials` metrics**~~ — **landed 2026-08-17.**
   Extracted as point-in-time items with per-source currency and
   `balance_sheet_as_of_date` (no period type — they are as-of figures). Derived
   `net_debt` requires one shared currency and one shared as-of date. Debt-inclusive
   arithmetic (`EQUITY_PLUS_TOTAL_DEBT`, calculated implied EV) requires currencies
   known and equal; no FX conversion. See decisions.md "total_debt / Cash_ST
   Extraction and Debt-Inclusive Arithmetic" and
   `scripts/test_debt_cash_extraction.py`.
   **The second owed re-aggregation (Path A) is DISCHARGED (2026-08-17)** on
   `data/ma_mvp.db`: 92 → 92 rows, 2 additional `transaction_value` (both
   `EQUITY_VALUE_ONLY`), 1 additional `ev_to_revenue_ltm`, no losses. The
   currency-gap sizing gate cleared at zero at-risk rows. See decisions.md
   "Path A Re-aggregation: Accepted".
   **Still owed: Path B** — re-extraction is what actually populates debt/cash; the
   corpus still has zero `net_debt` and zero debt-inclusive bases. See
   `docs/runbook_path_b_reextraction.md` (plan only, not executed).
3. **Review export value-model surface** — expose the current value-model fields
   (`equity_value`, `implied_equity_value`, `transaction_value`, `investment_amount`,
   `deal_value_currency`, and funding round fields) without treating `_v2` shadow
   columns as reviewer-facing Grata enum fields.
4. ~~**`transaction_size` + export column**~~ — **landed 2026-08-17.** Family-keyed
   waterfall: M&A takes `transaction_value`, Funding takes `round_size`, Spin/Split and
   everything else are null. The shipped basis vocabulary is exactly
   `{TRANSACTION_VALUE, ROUND_SIZE, SPIN_SPLIT_CONSIDERATION_VALUE}`
   (`stages/aggregate.py`). `SPIN_SPLIT_CONSIDERATION_VALUE` is reserved with no live
   rung, because no such source field exists yet. `SOLE_INVESTOR_AMOUNT` is **not
   reserved — it was removed outright**, on semantics rather than sequencing: an
   investor's check is never the event's magnitude, at any disclosure level.
   *(An earlier revision of this line read "`SOLE_INVESTOR_AMOUNT` and
   `SPIN_SPLIT_CONSIDERATION_VALUE` are reserved… because neither source field exists".
   Superseded on both counts: the removal was a semantic decision, and per-investor
   amounts are in fact storable — `staging_investor.investment_amount` — so availability
   was never the reason.)* No equity rung and no EV rung. The review export's shadow waterfall is retired, so the sheet now
   shows blank where canonical rules find the magnitude unsupported; the 67-column shape
   is unchanged. Guarded by `scripts/test_transaction_size.py`.
5. **Legacy value-field inventory/reorganization** — `enterprise_value` is now a
   compatibility mirror of `implied_enterprise_value`; decide later whether to
   remove, alias, or formally deprecate it after downstream consumers are known.
6. **Grata V2 recommendation triage** — **done 2026-08-17**, in
   `docs/grata_v2_reconciliation_2026_08_17.md`: 14 items sorted into implemented+validated
   / recommended for ENG / already adequate in Grata / deferred, plus Adopt-Keep-Defer and
   six open schema questions. The v0.3 inventory and dictionary are redlined inline. Still
   true, and the reason the triage exists: do not treat unimplemented proposals there as
   accepted harness behavior.

Owed operational: **none.** The second re-aggregation was executed and accepted as Path A
on 2026-08-17 (`docs/runbook_second_reaggregation.md` §8). Path B re-extraction is
deliberately deferred until a naturally occurring or manually collected debt/cash case
exists.

Still open, lower priority: `deal_summary` v0.10 funding framing. (~~write
`stages/funding_lc_extract.py`~~ — **corrected 2026-08-24**: not a pending stage, and not
required. See the funding-path correction above.)

_(The former "apply `AGGREGATION_READ_SOURCE=observation` after a validation run" item
is discharged — the default switched on 2026-08-17. Note this changed the default only;
aggregation is still incremental, so existing `AGGREGATED` rows keep their prior
semantics until a deliberate AGGREGATED→CLUSTERED reset re-derives them.)_

## Pending re-aggregation — §4.2 (2026-08-10)

### DISCHARGE (2026-08-12): first §4.2 re-aggregation done on the two live DBs

The first owed re-aggregation (item 1 below) is **discharged on `pl_funding.db` and `ma_mvp.db`** —
the two live targets. Both routed through `init_db` (columns asserted), full AGGREGATED→CLUSTERED
reset with row-count assertions, real Stage 9, diff classified by decision lineage. `ma_valu8.db`
/ `ma_grata.db` remain deferred (control-heavy fixtures, not read from). **The second re-aggregation
is discharged as of 2026-08-17** — executed and accepted as Path A on `ma_mvp.db`
(`docs/runbook_second_reaggregation.md` §8): 92 → 92 transactions, 98 re-derived, observation read
path corpus-wide. What it could *not* discharge is the dormancy finding: §4.2's
`EQUITY_PLUS_TOTAL_DEBT` branch has still produced zero values on real data, because no staging row
carries debt or cash. Only a re-extraction (Path B, deliberately deferred) can change that.

A mid-run verification surfaced a decision violation in `_derive_implied_equity` (below), which was
**fixed before `ma_mvp` ran** (`065a87d`). A corrective re-aggregation of `pl_funding.db` nulled its
manufactured implied values; `ma_mvp` re-aggregated with the fix in place, so it corrected by never
creating. Net implied-equity outcome:
- `pl_funding.db`: 9 violation values nulled (2 tv-fallback + **7 live `post_money`-branch** funding
  violations, incl. Base Power 13B, DeepX 3.14T); 3 legitimate equity-grossed values preserved.
- `ma_mvp.db`: 1 tv-fallback (Genesis) prevented; 3 equity-grossed preserved; 0 post-money.

**Run-note worth keeping:** the "exactly two value-model fields changed" assertion on the corrective
`pl_funding` pass *earned its place by failing* — it tripped at 9 rows, forcing the investigation
that found the 7 live `post_money` violations. A looser check would have passed and nobody would have
looked. And: any claim about how many rows hold a value must be measured against **state**, never
inferred from a NULL→val **diff** (the error that put a false "latent" claim into `decisions.md`,
since corrected).

§4.2 landed as CODE first (equity_value now stake-level; transaction_value threshold;
+ total_debt / transaction_value / transaction_value_basis / pct_acquired_source columns),
then was applied to the two live DBs by deliberate re-aggregation. Aggregation remains
**incremental** (only CLUSTERED rows are derived; existing AGGREGATED rows keep old
semantics), so any future semantic change still needs an explicit reset-and-run, not a normal
pipeline pass. **The re-aggregation that was owed here is now discharged:**

1. ~~**After total_debt + Cash_ST extraction (the next piece):** re-aggregate again to populate
   the transaction_value total-debt branch (dormant until then) and derive net_debt from
   `total_debt − Cash_ST`~~ — **run as Path A, 2026-08-17.** The extraction landed in code
   (prompt 0.17 + Stage 4a persistence), but the existing corpus was extracted under 0.16 and
   earlier, so re-aggregation alone could not activate the debt branch — the runbook's §0
   finding. Path A therefore delivered the read-path, typed-equity and anchoring changes across
   the corpus; the debt branch and derived `net_debt` remain dormant pending Path B. Cash is
   still defined as `Cash_ST` (decisions.md "Debt and Cash Inputs").

Expect **unattributable diffs** from re-aggregation: the DB holds several historical
derivation semantics (aggregation has always been incremental), not just a single expected
creates. Diffs that don't trace to §4.2 are expected, not regressions.

### Finding (2026-08-12): §4.2's transaction-value/debt branch has never run on real data

`total_debt` exists on **no** database — not `pl_funding.db`, `ma_mvp.db`, `ma_valu8.db`, or
`ma_grata.db`. It is a manual column, never populated. So §4.2's control-path branch
(`transaction_value = equity_value + total_debt` at `pct_acquired ≥ 50`) **cannot fire against
any existing data** — every control deal with qualified equity consideration takes the
debt-unknown `EQUITY_VALUE_ONLY` fallback. The **second owed re-aggregation is therefore substantive, not a
formality**: the total-debt branch will run against real data for the *first time* only after
`total_debt` + `Cash_ST` extraction lands. The first re-aggregation (below) can only exercise the
stake-level `equity_value` change and the TV=equity fallback.

Target choice for re-aggregation follows *live data*, not interesting data: re-aggregation is
remediation of a mixed column someone reads, not a test of a code path (that's a unit test).
Live targets: `pl_funding.db` (75/91 rows on the changed funding/minority path) and `ma_mvp.db`
(config-live per `.env`; all 92 rows non-control = the changed `equity_value` path).
`ma_valu8.db` / `ma_grata.db` (control-heavy) are deferred unless someone reads them.

### Precondition (was missing from the work order): AGGREGATED→CLUSTERED reset

Aggregation derives `WHERE status='CLUSTERED'` then moves rows to `AGGREGATED`. A DB whose rows
are all `AGGREGATED` (which `pl_funding.db` and `ma_mvp.db` both are — 0 CLUSTERED) has nothing to
re-derive; step 7 would run clean against zero rows and report success — a silent no-op. **The
reset is a required, deliberate step; assert row counts before the reset, after it, and after the
run.**

### Phase-1 progress (2026-08-11/12): observation coverage + round currency landed

Steps 1–6 of `docs/workorder_code_2026_08_11.md` are complete and committed. The observation
write path now covers every field aggregation reads (funding group + tier-2/tier-3 wiring);
`round_currency` is captured and `deal_value_currency` is unanimity-or-null over three sources.
Step-6 parity on `pl_funding.db`: **zero canonical transaction diffs**. A residual +10 LLM-conflict
delta on six HC *descriptive* fields (target/acquirer name+description, ticker, per_share_price) is
**pre-existing** — verified identical on the pristine pre-backfill snapshot — and reflects the
observation model's finer per-source granularity, not this work. It is a latent-divergence item to
resolve before any switch to `AGGREGATION_READ_SOURCE=observation`, tracked separately from §4.2.

### FINDING (2026-08-12): implied_equity_value grossed up from an unqualified transaction_value — DECISION VIOLATION [RESOLVED `065a87d`, decision "implied_equity_value Derives From equity_value Only"]

Step-7 verification on `pl_funding.db` surfaced a live bug that violates a recorded decision.
`decisions.md` — **"Transaction Size as Universal Magnitude"** — states an unqualified source
figure "must never populate equity value or either implied value, because grossing up an
unqualified number and striking a multiple off it manufactures a figure no source ever qualified."
Aggregation does exactly that: on rows where the source states only an unqualified value (lands as
`transaction_value` with `transaction_value_basis='STATED'`) and no separate equity figure,
`_derive_*` grosses that value up into `implied_equity_value` via `transaction_value / pct_acquired`.

Confirmed instances (all `equity_value` NULL, `implied` = `transaction_value / pct` exactly):
- `pl_funding.db`: **TC Skyward** (pct 50, tv 1.946B → implied **3.892B**); **KG Mobility tc_616c**
  (pct 10, tv 75M → implied **750M**).
- `ma_mvp.db` (dry-run on copy, not yet live): **1 row** — Genesis Digital Assets (pct 38.3, tv
  500M → implied **1.305B**).

Severity: `implied_equity_value` is one of the two *legal multiple numerators*. These are
manufactured numerators one step from a manufactured multiple — the multiples gate held only
because no revenue/EBITDA denominator computed, not because the input was sound. **This is the
phase-3 evidence** (a manufactured numerator from our own data), in a different shape than the
predicted "multiple struck off a stake-level value."

Resolved in `065a87d`: the derivation no longer grosses an unqualified/`STATED`
`transaction_value` into `implied_equity_value`. Corrective re-aggregation nulled the affected
`pl_funding.db` values, and `ma_mvp.db` re-aggregated with the fix in place.

### FINDING (2026-08-12): KG Mobility double-counted — Stage 8 clustering miss

Two `transaction_record`s exist for the same KG Mobility ← Chery 10% deal (`tc_616c` from source 9,
`tc_9731` from source 151) — two source articles that did not cluster together. Separate from the
value model; a Stage 8 (`entity_cluster`) dedup gap affecting counts/aggregates. It also acted as an
accidental controlled experiment: the same figure routed as `equity_value` in one record (coherent
two-tier: 75M/750M, ratio exactly 100/pct) and as `transaction_value` in the other (the violation
above) — the first real-data instance of the classify-or-lose problem the **"Named Value Fields
Replace the Single Value Slot"** decision (accepted, unmigrated) was written to fix.

### Phase-3 framing note

Grata's silver header carries **named scalars** (`deal_value`, `reported_ev`, `amount_raised`,
`post_evaluation`) pivoting into `financial_metric` rows in gold — no single classify-or-lose slot,
so it does not have this defect. This is a place to **adopt from eng rather than push back** — worth
stating plainly in the phase-3 memo, since conceding where their model is better buys credibility for
where ours does.

Schema drift is now guarded by `scripts/test_schema_convergence.py`: `init_db` brings any DB
(a fresh one, a 001-base one, or a historical `data/*.db`) to one canonical schema — verified
across all 10 historical DBs.

_Retracted:_ an earlier note here flagged `v2_event_type` as undocumented CREATE/reality drift.
That was a false positive from reading `001_initial.sql` alone — it is defined in
`schema/002_v2_prompt_alignment.sql:18,69`. The schema of record is `schema/*.sql` collectively
(001 + 002 + 003) plus the db.py migration list; see decisions.md "Schema Sources of Record".
