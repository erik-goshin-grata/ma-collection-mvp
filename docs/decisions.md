# Architectural Decisions

This file records project-level decisions that affect future implementation
work. It is intentionally brief; implementation detail belongs in drop design
docs and code.

## 2026-06-03 - LLM Provider Abstraction

Status: accepted.

Decision:

- Route prompt execution through an internal provider abstraction.
- Preserve Anthropic as a provider.
- Add OpenAI as a provider behind `LLM_PROVIDER=openai`.
- Use OpenAI Responses API and Structured Outputs for JSON-returning prompt
  paths where practical.

Context:

- Codex Enterprise access does not make the local Python pipeline use OpenAI.
  The repo must call OpenAI explicitly.
- The project needs to switch providers without changing prompts, schemas, or
  extraction semantics.

Consequences:

- Anthropic remains the current supported live path.
- OpenAI live validation is deferred until a local API key is available.
- Prompt and schema changes should not be bundled with provider migration.

## 2026-06-03 - Shared Field Priority and Confidence Tiebreak

Status: accepted.

Decision:

- Centralize field filing-type priority and tier ordering in
  `lib/field_priority.py`.
- Keep existing priority rules.
- Apply deterministic same-tier model-confidence priority before invoking LLM
  conflict resolution.

Context:

- Drop 3.31a needed to reduce unnecessary conflict calls without changing
  schemas, prompts, or aggregation semantics.

Consequences:

- Same-tier `HIGH` observations beat same-tier `MEDIUM` observations before LLM
  conflict resolution.
- Remaining same-tier conflicts still use the existing LLM conflict path.

## 2026-06-03 - Observation Provenance as Stage 9 Read Substrate

Status: accepted.

Decision:

- Extend `transaction_field_observation` so source-row extraction observations
  carry the provenance Stage 9 needs:
  - `staging_extraction_id`
  - `source_raw_id`
  - `source_type`
  - `source_tier`
  - `model_confidence`
  - `source_published_date`
  - `filing_type`
  - `agreement_dated_as_of`
  - `observation_source_stage`
- Dual-write Stage 4 and Stage 7 source-row observations.
- Backfill existing copied DBs idempotently.

Context:

- Stage 9 originally reconstructed transient observations from
  `staging_extraction JOIN source_raw`.
- Moving aggregation toward durable observations required source-row
  provenance without changing Stage 9 behavior in Drop 3.31b.

Consequences:

- Stage 9 can now be validated against a durable observation layer.
- `staging_extraction` remains part of the current pipeline; 3.31b did not
  remove it or make observations the only source of truth.

## 2026-06-03 - Guarded Observation-Backed Stage 9 Read Path

Status: accepted.

Decision:

- Add `AGGREGATION_READ_SOURCE=staging|observation`.
- Keep `staging` as the default for now.
- Add an observation-backed Stage 9 loader that normalizes observations into
  the same in-memory shape as the staging loader.
- Limit the first observation-backed read path to source-row observations from
  `DT_CLASSIFY`, `HC_EXTRACT`, `LC_EXTRACT`, and `BACKFILL`.
- Exclude `AGREEMENT_EXTRACT` observations from Stage 9 routing.
- Preserve the staging read path as rollback/comparison path.

Context:

- Drop 3.31c was intended as a read-side parity switch, not a semantic rewrite.
- Agreement supersession is a separate design problem and should not be folded
  into the source-row parity switch.

Consequences:

- 3.31c can close because copied-real-DB parity passed with zero canonical
  transaction diffs and zero transaction-source diffs.
- A future decision is still needed before changing the default read path to
  `observation`.
- A future agreement-supersession drop is still needed before agreement
  observations participate in Stage 9 canonical routing.

## 2026-06-03 - Participant-Centric Multi-Party Organization Model

Status: accepted, implemented in `577364d`, and recorded in the post-3.32a
documentation closeout.

Decision:

- Implement Drop 3.32a as participant-centric multi-party organization support.
- Use four active tables:
  - `entity`
  - `entity_alias`
  - `transaction_participant`
  - `transaction_participant_group`
- Remove `entity_relationship` from active 3.32a scope.
- Represent transaction context through roles such as `BUYER_SPONSOR`,
  `SELLER_SPONSOR`, `BUYER_PLATFORM`, `SELLER_PLATFORM`,
  `PARENT_ACQUIRER`, `PARENT_SELLER`, and `MERGER_SUB`.
- Do not write abstract relationship rows such as `PORTFOLIO_COMPANY_OF`,
  `SPONSORED_BY`, `CORPORATE_VC_ARM_OF`, `MANAGED_BY`, or
  `SUBSIDIARY_OF` in 3.32a.

Context:

- The immediate business problem is representing all disclosed organizations
  participating in a transaction: multiple buyers, sponsors, investor groups,
  consortiums, parent sellers/acquirers, platforms, and merger subs.
- Researchers reason first in transaction terms: target, buyer side, seller
  side, sponsors, platforms, parents, merger subs, investors, and issuers.
- Abstract organization relationships are useful later, but they increase
  review burden and drift away from the collection workflow in this slice.

Consequences:

- 3.32a adds normalized organization participant storage without changing
  `transaction_record`, advisors, Stage 9, prompts, exports, or pipeline
  behavior.
- Consortiums, investor groups, and seller groups are represented as
  transaction participant groups, not synthetic entities.
- Future relationship or graph-style modeling should be designed separately
  after the participant model is stable.
- Copied-real-DB validation passed with transaction output unchanged, advisor
  rows unchanged, zero duplicate participants, zero duplicate groups, zero
  synthetic group entities, and zero foreign key issues.

## 2026-07-22 - Announcement vs Close Prompt Semantics

Status: accepted.

Decision:

- Treat `event_type` as the source observation type, not only the deal lifecycle
  status.
- Reserve `CLOSE` for a separate later release that explicitly references a
  previously announced transaction.
- Keep first-observed same-day completed private acquisitions and advisor
  tombstone releases as `ANNOUNCEMENT`.
- In high-confidence extraction, populate both `announced_date` and
  `closed_date` for same-day completed-deal announcements when there is no
  pending-close language.
- Let pending-close language win over completed-sounding headlines or deal
  framing.

Context:

- PR/news sources often use completed-deal wording such as "announced its
  acquisition of," "has acquired," "announced the sale of," "completes
  acquisition," or "advises on the sale of" when the release is still the
  first public disclosure available to the pipeline.
- The prior classifier wording could overclassify those first-observed private
  deal announcements as `CLOSE`.

Consequences:

- `deal_type_classifier` is versioned to `0.5`.
- `high_confidence_extraction` is versioned to `0.10`.
- Transaction status remains derived downstream from extracted dates.
- Validation on a six-source local DB passed with all original sources
  classified as `ANNOUNCEMENT`, expected pending deals left without
  `closed_date`, and expected same-day completed deals populated with
  `closed_date`.

## 2026-07-22 - Take-Private Derived Flag Rule

Status: accepted.

Decision:

- Keep take-private as a derived flag, not a top-level deal type.
- Derive `is_take_private` in Stage 9 for public standalone company
  acquisitions where the buyer/ownership outcome is private or non-public.
- Allow private strategic buyers, sponsor-backed/platform buyers, financial
  sponsors, management/family-style buyers, and private consortiums to satisfy
  the buyer side of the rule.
- Do not derive take-private when the acquirer has a public ticker.
- Do not derive take-private for public-company mergers, public-acquirer
  acquisitions, public-target asset sales, carve-outs/business unit sales, or
  minority investments.

Context:

- The prior shorthand, `target_status=PUBLIC + acquirer_type=PRIVATE_EQUITY`,
  missed valid take-private transactions led by private strategic buyers.
- Utz/Intersnack is the motivating example: canonical `deal_type` remains
  `ACQUISITION`, but the product-facing flag should still identify it as a
  take-private after aggregation.

Consequences:

- Stage 9 owns the product/export flag.
- Summary generation receives `flags.is_take_private` directly.
- Prompt notes now refer to the derived flag rather than a PE-only shorthand.
- Public-public merger false positives remain guarded by deal type, target
  type, target status, and public acquirer ticker checks.

## 2026-08-02 - Per-Stage Model Tiering (Sonnet tier added)

Status: accepted, implemented in `f5f4e88`.

Decision:

- Add a `sonnet` model alias resolving to `Config.sonnet_model`
  (`SONNET_MODEL`, default `claude-sonnet-4-6`) in `lib/llm_client.py`, for both
  the Anthropic and OpenAI resolvers.
- Move four stages from Opus to Sonnet:
  - `deal_type_classify` — fixed-enum single pick, temp 0.0.
  - `high_confidence_extract` — explicit-fact extraction (pattern-match, not judgment).
  - `funding_hc_extract` (Stage 4b) — funding variant of HC.
  - `summarize` / deal summary — prose over already-extracted facts.
- Keep on Opus: `low_confidence_extract` (nuanced fields), `agreement_extract`
  (legal precision), aggregation conflict resolution (rare, low volume),
  `strategic_rationale`.
- Keep relevancy on Haiku.

Context:

- Every LLM stage except relevancy had been on Opus since the stages' initial
  implementation (2026-04-23). The 2026-07-28 change was only a version bump
  (`claude-opus-4-5` to `claude-opus-4-7`); Opus 4.7 emits 1.0-1.35x more tokens
  for the same text, a real cost increase. Opus was paying for judgment that
  enum / explicit-extraction / prose stages do not require.
- No Sonnet tier existed; the resolver only knew `opus` and `haiku`.

Consequences:

- The highest-volume Opus stages move to Sonnet 4.6; precision-sensitive stages
  (legal extraction, low-confidence nuance) stay on Opus.
- `prompts/prompt_conventions.md` §2 updated to match.
- `strategic_rationale` stays Opus pending a gold-set test — the cited "cheaper
  tier matched/beat Opus at 11x lower cost" result is not recorded in this repo.
- `funding_hc_extract -> Sonnet` was an inferred extension of the HC decision;
  revisit if funding extraction quality regresses.
- Any tier change should be validated via the evaluation harness
  (`gold_set` + `specs/evaluation.md`) before being treated as proven.

## 2026-08-10 - Two-Tier Value Model

Status: accepted; implied-enterprise-value rewire implemented in the validation
harness on 2026-08-12.

Decision:

- Split every value field into exactly one of two tiers.
- As-transacted tier — `equity_value`, `transaction_value`, `transaction_size`. Records
  what changed hands. Never a multiple numerator.
- 100%-basis tier — `implied_equity_value`, `implied_enterprise_value`. Whole-company
  valuation, normalized for comparison. The only legal multiple numerators.
- Remove stake-level `enterprise_value`. All routes to an enterprise value — stated at a
  partial stake, stated generally, or computed — converge on `implied_enterprise_value`,
  distinguished by method flag.
- "Implied" means 100%-basis, not "derived." A source-stated figure populates these fields
  exactly as a computed one does.
- Multiple numerators become `implied_enterprise_value | implied_equity_value`.

Context:

- Stake-level enterprise value adds full company debt to a partial equity stake, which
  corresponds to no economic quantity. A 27% stake bought for 270 against company debt of
  500 produced an enterprise value of 770 and a 5.1x multiple against a true 10.0x.
- Equity keeps both tiers because a partial equity stake is a real quantity — it is what
  the buyer paid for their shares. The asymmetry between equity and enterprise value is
  deliberate.

Consequences:

- The canonical field family is `implied_enterprise_value_*`. Legacy
  `enterprise_value` / `enterprise_value_basis` remain temporarily as
  compatibility mirrors pending a later downstream inventory/reorganization.
- Multiples cannot be struck off as-transacted values structurally, rather than by
  convention.
- Evidence hierarchy for `implied_enterprise_value`:
  1. Source-stated whole-company `ENTERPRISE_VALUE` populates
     `implied_enterprise_value` directly.
  2. Otherwise, calculate `implied_enterprise_value = implied_equity_value + net_debt`.
  3. Reported/manual `net_debt` is preferred when available.
  4. Otherwise calculate `net_debt = total_debt - Cash_ST` only when both
     components exist on an appropriate basis.
  5. Missing debt or cash/ST is never assumed to be zero.
- Multiples use canonical Tier 2 whole-company valuation numerators
  (`implied_enterprise_value` or `implied_equity_value` where applicable), not
  `transaction_value` or stake-level `equity_value`.
- One item is parked: feeding `implied_enterprise_value` into `transaction_size` when
  neither transaction value nor equity value is available. Control deals only.

## 2026-08-10 - Transaction Value Follows Control

Status: accepted.

Decision:

- `transaction_value` is populated as-reported wherever a source states one.
- Where it is calculated:
  - `pct_acquired` < 50 → `transaction_value` = `equity_value`. No debt is added.
  - `pct_acquired` ≥ 50 → `transaction_value` = `equity_value` + total debt.
  - `pct_acquired` ≥ 50 with qualified equity consideration and debt unknown →
    `transaction_value` = `equity_value`, basis `EQUITY_VALUE_ONLY`. This preserves
    the known consideration for the stake acquired and does not assume debt = 0.
- Cash is never netted. `transaction_value` − cash = `implied_enterprise_value`.
- **The test is `pct_acquired ≥ 50`.** A control-crossing test using pre- and
  post-transaction ownership was considered and rejected — see below.
- No `implied_transaction_value` field. A grossed-up, 100%-basis transaction value was
  considered and rejected.

Context:

- Below control no debt transfers: a minority buyer takes on none of it, and equity-method
  treatment consolidates nothing. `transaction_value` = `equity_value` there is a statement
  about the transaction, not a claim that the company is debt-free.
- At or above control the acquirer consolidates the target's balance sheet and effectively
  takes on its debt, so adding total debt records something that happened when total debt is
  available. When debt is unknown, `EQUITY_VALUE_ONLY` records the known equity consideration
  component without treating missing debt as zero.
- **The simple threshold is wrong in one case and right in four.** A step-up from a
  minority position into control — 30% to 60%, `pct_acquired` = 30 — reads as below
  control and adds no debt, when it should. Buying from an existing minority position
  into control is uncommon, and the failure understates rather than inflates.
- The alternative, a control-crossing test, requires extracting pre-transaction ownership,
  which sources state far less often than they state the stake acquired. It also requires
  a derived control-flag family. The accuracy gain did not justify a new extraction
  primitive and a flag set with no other consumer.
- **The 50–99% band mixes partial equity with full debt**, which is the market convention
  (CIQ Total Transaction Value) rather than an oversight. Grossing up in that band would
  produce a 100%-basis figure — effectively an implied transaction value — which was
  rejected as a field nobody asked for and which duplicates implied enterprise value up to
  cash.

Consequences:

- **This redefines an existing field.** The prior rule added total debt unconditionally, so
  previously computed rows for partial stakes will change value. A backfill decision is
  required.
- `transaction_value` equals `equity_value` for minority deals, and for control deals where
  the only known transaction component is qualified equity consideration. The basis stamp
  distinguishes below-control no-debt treatment from control debt-unknown treatment.
- The reconciliation identity `transaction_value - cash = implied_enterprise_value` holds
  for control deals only when `transaction_value_basis` is `STATED` or
  `EQUITY_PLUS_TOTAL_DEBT`, not when it is `EQUITY_VALUE_ONLY`.
- Requires no extraction primitive beyond `pct_acquired`, which already exists. Neither
  pre-transaction ownership nor a derived control-flag family is needed for the value
  model. Those may still be built for comps segmentation and filtering, on their own
  merits.
- `pct_acquired` must be stamped alongside `transaction_value` wherever it is displayed,
  so that partiality in the 50–99% band is legible rather than hidden.

## 2026-08-10 - Transaction Size as Universal Magnitude

Status: accepted.

Decision:

- Add `transaction_size` — a single magnitude populated across all deal types.
- Derived in aggregation. Never extracted; no extractor decides what belongs in it.
- Waterfall: M&A takes `transaction_value`, then equity consideration where equity is
  stated and debt unknown. Funding takes round size, then a sole investor's check.
- `transaction_size_basis` is NOT NULL wherever the field is populated, and travels with it
  in every export, sheet and view.
- Named `transaction_size`, not `deal_size`, for vocabulary consistency with the rest of
  the `transaction_*` schema.

Context:

- Reviewers presented with a single unlabeled value column picked the largest figure
  available. Splitting the roles and stamping the basis makes that structurally harder.
- An unqualified source figure — "acquired for $500MM" — populates `transaction_value` and
  `transaction_size` only. It must never populate equity value or either implied value,
  because grossing up an unqualified number and striking a multiple off it manufactures a
  figure no source ever qualified.

Consequences:

- `transaction_size` must not be summed across bases. A control acquisition and a minority
  check are different events and their sum is not a deal-volume figure. Enforce in the
  query layer.
- Sole-investor rounds are the only safe check-based fallback. Per-investor disclosure runs
  around 30% for leads and under 5% for other participants, so summing whatever amounts
  exist understates the round while presenting as a round size.
- A share of transaction-value-basis rows carry figures whose debt-inclusivity is assumed
  rather than determined, biasing private deals low on disclosure rather than size.

## 2026-08-10 - Value Path Keyed on Where the Money Goes

Status: accepted. Does not alter the `deal_type` enum, and adds no schema dimension.

Decision:

- Which value path applies depends on where the money goes, not on control or stake size.
  - Money to a selling shareholder — M&A value path: `equity_value`, `transaction_value`,
    `transaction_size`.
  - Money into the company — funding value path: `post_money_valuation` and round size only.
- Stake size does not determine the path. A minority stake purchase takes the M&A path with a
  minority feature; a minority primary investment takes the funding path.
- Features carrying the M&A distinctions: `is_minority`, `pre_existing_control`,
  `acquires_remaining`.
- Debt attaches only where `pre_existing_control` is false.
- **No `capital_flow` or `instrument_class` field is introduced.** Both were considered and
  rejected as premature taxonomy. The value path continues to key on `deal_type`.
- Extraction rules are written as a test the model applies — did this money go into the
  company or to a selling shareholder — rather than as a rule keyed on deal type. This is a
  prompt instruction, not a stored field, which is what lets it survive taxonomy changes.

Context:

- `MINORITY_INVESTMENT` as defined spans growth equity rounds, strategic minority stakes and
  PIPEs — primary and secondary capital in one bucket, discriminated by control rather than
  by where the money went. Keying the value path on capital flow removes the ambiguity
  without requiring the deal type itself to change.
- Growth equity routinely combines a primary and a secondary leg in one transaction, so
  capital flow needs a `both` state and the legs need separate amounts.
- A 49.9% purchase by a holder already at 50.1% obtains no control and transfers no debt,
  so `pct_acquired` alone is the wrong control test.

Consequences:

- `transaction_size` is stable across a misclassification between the two paths: both give
  the same magnitude. Misclassification costs valuation coverage, not a wrong deal size.
- The extraction rule survives any subsequent change to the deal-type enum, including the
  open Decision #9 boundary, without revision.
- Growth and VC handling is unchanged by this decision. Venture debt is out of scope —
  flag from stories and handle separately.
- Public ownership positions from 13D/13G filings remain out of scope. Positions are state
  with a time dimension; transactions are discrete events. The two link rather than merge,
  so nothing in the transaction model needs to anticipate them. Those filings remain valid
  as sources of negotiated block purchases.

## 2026-08-10 - VC vs Growth Boundary: DEFERRED to Decision #9

Status: **deferred — no decision taken today.**

Position:

- Today's session discussed a boundary rule keyed on round label — a Series label meaning
  VC, an institutional minority investment without one meaning growth. **That was proposed
  without knowledge of the existing rule and is not adopted.**
- The live rule in `deal_type_classifier.md` (0.6) keys types #9/#10 on **investor archetype
  plus company profitability**, explicitly ignores round size and stage, and tie-breaks to
  `VC_ROUND`.
- Decision #9 is open, with the recorded lean toward `Series D+` implying `GROWTH_EQUITY` —
  roughly the inverse of what was proposed today. Nothing in this session's discussion
  should be read as settling it.

Consequences:

- The value-path decision above is deliberately independent of this boundary. Both
  `VC_ROUND` and `GROWTH_EQUITY` are primary capital and take the same value path, so the
  boundary can move without affecting value semantics.
- Growth equity's role as a platform anchor is a separate argument for keeping the two types
  distinct, and is unaffected by where the boundary is drawn.

## 2026-08-10 - Funding Valuation Scope

Status: accepted.

Decision:

- Funding rounds populate `post_money_valuation` only.
- No `implied_equity_value` for funding rounds, and therefore no implied enterprise value.
- Funding rounds produce no multiples of any kind.

Context:

- Enforced structurally rather than by rule. With no implied equity for a funding round,
  there is nothing for debt to attach to and no enterprise value can compute. The
  constraint holds because the inputs do not exist, not because a rule is remembered.
- Post-money is as-converted and preference-laden, so post-money-derived multiples run rich
  against M&A and should not blend with them.

Consequences:

- Two leaks are closed that are otherwise unblocked: implied enterprise value carries no
  event-type restriction, and the multiple numerator type carries none either.
- There is no cross-deal-type equity valuation comparison. Funding carries a stated
  post-money; M&A carries implied equity; the two are not placed in a common column.
- If post-money-based multiples are wanted later, post-money must be added as a deliberate
  third numerator type. It should not arrive by inheritance.

## 2026-08-10 - pct_acquired Default Retained, Now a Valuation Input

Status: accepted.

Decision:

- Retain the 100% default where the event type conveys control and the source is silent.
- Record `pct_acquired_source` as `stated` or `assumed`. Diagnostic only; it suppresses
  nothing.
- Never apply the default to inherently partial types — minority investments, growth
  equity, secondaries, recapitalizations. There, silence means unknown.
- Partiality language in the source suppresses the default and routes to review.

Context:

- Silence on an acquisition means whole-company in the large majority of cases; sources
  that mean partial nearly always say so. Withholding the default would forfeit implied
  values across most of the M&A set to guard a small error rate.
- The field now grosses up every 100%-basis value, so an error propagates into every
  multiple struck off that deal.

Consequences:

- The default's accuracy is inherited entirely from the deal-type classifier's precision on
  the control/minority boundary. QA effort belongs there, not on the default.
- The stamp exists so the error rate can be measured rather than debated.

## 2026-08-10 - PENDING: equity_value Stake-Level Migration

Status: **pending — decision required before the change merges.**

Decision required:

- Making `equity_value` consistently stake-level changes what stored rows mean. The control
  path already stores stake-level equity; the funding path stores the 100% figure
  (post-money). After the change, existing rows are mixed-semantics with nothing
  distinguishing them.
- Options: re-aggregate affected rows, or stamp existing rows with the semantics they were
  written under.

Context:

- This is the second field in this batch whose meaning changes rather than being added —
  see also the transaction-value redefinition above, which has the same backfill question
  for partial stakes.
- Escalated deliberately rather than left to the implementer. Merging without a decision
  leaves a column that cannot be interpreted.

Consequences:

- Blocks the `equity_value` path-consistency fix from merging.
- Whichever option is chosen should be applied consistently with the transaction-value
  redefinition, since both alter stored meaning for partial-stake deals.

## 2026-08-10 - Named Value Fields Replace the Single Value Slot

Status: accepted in principle; migration unscheduled.

Decision:

- Replace the single `value.amount` + `value.type` pair in
  `prompts/high_confidence_extraction.md` with named as-reported fields, one per value
  type: `equity_value_as_reported`, `transaction_value_as_reported`,
  `enterprise_value_as_reported`. `per_share_price` is already separate and unchanged.
- Populate every field the source states. No inference and no arithmetic.
- An unqualified figure — "acquired for $500MM", no basis given — continues to route to
  `transaction_value_as_reported` with low type confidence, preserving today's default.
- `UNDISCLOSED` is not a value field. It routes to `financials_disclosure_status`, which
  already exists in the same prompt.

Context:

- The `value` object is a single slot. Where a source states more than one figure — common
  in larger announcements, e.g. "$45.00 per share, representing an equity value of
  approximately $2.1 billion and an enterprise value of approximately $2.4 billion" — the
  model must pick one and the rest is dropped, with nothing recording that a choice was
  made.
- The single slot also forces the model to *classify* rather than to *record*, which is the
  mechanism behind the check-recorded-as-equity-value defect. That defect is a symptom of
  this shape, not an independent bug.
- `prompts/funding_hc_extraction.md` already does this correctly — `round.size`,
  `round.pre_money_valuation`, `round.post_money_valuation` and `facility_size` are separate
  named fields, so a source stating several loses none. The M&A path is the outlier.
- The V2 model already specifies `*_as_reported` columns per value type, so this is a
  planned migration pulled forward to the extraction layer rather than a new design.

Consequences:

- Blast radius beyond the prompt: the parser, the staging columns, the aggregation read, and
  the low-confidence input template. Aggregation currently routes by `value_type`; with
  named fields there is nothing to route, which simplifies that read.
- `low_confidence_extraction` consumes HC's value and does not produce one, so no LC
  extraction logic changes — only its input template. With named fields there is no single
  "deal value" to pass, so what LC receives becomes an explicit decision rather than a
  template default.
- Existing rows migrate mechanically: each `value_amount` / `value_type` pair maps to the
  matching named column.
- **Figures already dropped by the single slot are unrecoverable** without re-extraction.
  Not in scope; note it if a backfill is ever scoped.
- Open sub-decision: whether `currency`, `qualifier` and `type_confidence` become per-field
  or stay shared. Per-field is more correct where a source mixes currencies; shared is
  simpler. Not decided.

## 2026-08-10 - deal_value_currency: single currency tag on derived values

Status: accepted.

Decision:

- Derived value fields (`equity_value`, `implied_equity_value`, `enterprise_value`,
  `investment_amount`) carry a single `deal_value_currency` on `transaction_record`,
  not per-field `*_currency` columns. Tag-and-defer: attach the currency, never
  assume USD, do not convert.
- Precedence: `valuation_currency` (post-money-based funding values) then
  `value_currency` (control-deal values).
- Mismatch guard: when both `valuation_currency` and `value_currency` are present
  and differ, `deal_value_currency` is null. The precedence is a fixed rule, not a
  provenance lookup, so on a cross-border record (USD check + EUR post-money) it
  would otherwise mislabel; null refuses to guess.

Context:

- **The null is itself the queryable signal.** A row with a derived value populated
  and `deal_value_currency` null (with a currency actually present) is the mismatch
  set, detectable in SQL — no flag column, and no dependence on the run log, is
  needed. The logged warning is a run-time convenience only. Do not add a
  review/flag column to "fix" this; the data already carries the state.
- Single tag over per-field: per-field currencies matter mainly for
  `implied_enterprise_value`, which adds two potentially different currencies, and
  that is parked on the §2.10 currency question. Building per-field before that
  resolves means building it twice. The `_basis` flags keep provenance-following
  available later without re-architecting.

Consequences:

- Closes spec gap 7 (computed-but-not-inserted); the value is now persisted.
- Per-field `*_currency` columns deferred, revisited with the §2.10
  currency-normalization / FX work.
- Test: `scripts/test_deal_value_currency.py` — non-null where a currency is present
  and does not conflict; null on conflict (the second assertion proves the guard).

## 2026-08-10 - Debt and Cash Inputs

Status: accepted; amended 2026-08-10 (cash defined as `Cash_ST`) and
implementation-refined 2026-08-12. `total_debt`, `net_debt`, and `Cash_ST` are
manual/interim transaction-record inputs in the harness; extraction remains
deferred.

Decision:

- `total_debt` is **total debt, not net of cash**. It is the input to
  `transaction_value` at `pct_acquired ≥ 50`.
- `net_debt` remains the input to `implied_enterprise_value`.
- `net_debt`, `total_debt`, and `Cash_ST` are preserved across re-aggregation.
- **When extracted, `total_debt` and `cash_st` belong in `target_financials`** alongside
  `target_revenue` and `target_ebitda` — with period type and `period_end_date` — not as
  standalone columns. Balance-sheet figures without a period are not usable in a bridge.
- **`cash` is captured as a single field, `Cash_ST`** — cash + cash equivalents +
  short-term / marketable investments (the CapIQ "Cash & Short-Term Investments" convention,
  explicitly broader than strict cash & equivalents). Its one consumer is
  `net_debt = total_debt − Cash_ST`, which feeds `implied_enterprise_value`. `Cash_ST` must
  carry the same `period_end_date` as `total_debt`, or the derivation is incoherent.
- Collection is flexible: researchers may supply components (`total_debt`, `Cash_ST`) or only
  `net_debt`. Reported/manual `net_debt` is preferred when present; otherwise
  `net_debt` is calculated only when both `total_debt` and `Cash_ST` exist. A row
  with only `net_debt` yields an enterprise value and no calculated transaction
  value. That is expected, not a defect.

SEC enrichment boundary:

- SEC evidence in this harness is limited to transaction/merger documents,
  relevant exhibits such as 99s, and manually collected values. This is not a
  mandate for general SEC financial-statement mining for shares outstanding,
  debt, cash, or financial denominators.

Context:

- `transaction_value` needs `total_debt` and `implied_enterprise_value` needs `net_debt`. `total_debt` cannot be
  recovered from `net_debt`, so capturing only `net_debt` forecloses the transaction value
  permanently.
- Asking researchers for net debt asks them to compute. The raw balance-sheet lines — total
  debt and cash — give all three figures; a computed input gives one.
- An earlier claim that `total_debt` − `net_debt` yields cash "for free" was withdrawn: it
  holds only where both are populated, and if one is extracted while the other is manual they
  land on largely different rows, so `transaction_value` and `implied_enterprise_value` would
  populate on different deals.
- `Cash_ST` uses the comp-source convention (CapIQ Cash & Short-Term Investments) so the
  resulting EVs line up against the comps they will be measured against. A narrower cash
  definition overstates `net_debt` and inflates every multiple relative to those comps.

Consequences:

- **`total_debt` and `net_debt` sit adjacent and are one word apart.** The column comment must
  state that `total_debt` is total debt, not net, or a net figure will eventually be entered into it and
  nothing downstream will catch it.
- QA check available on manual inputs: `total_debt >= net_debt` wherever both are populated.
  Cash is non-negative, so `total_debt` can never be below `net_debt`.
- Broad extraction of `total_debt` and `Cash_ST` requires the period-anchoring
  question to be settled first. The manual/reported `net_debt` path can already
  populate `implied_enterprise_value`; broad component extraction remains a later
  piece of work.
- `Cash_ST` now enables calculated `net_debt` where both components exist, while
  preserving the manual/reported `net_debt` path for rows that already have it.
- **Considered and rejected: capturing cash and short-term investments as separate
  components.** Debt needs components because two derived fields consume different
  combinations — `transaction_value` needs total debt, `implied_enterprise_value` needs net debt. Cash
  has exactly one consumer (`net_debt`), so a split buys nothing present-tense. One field.

## 2026-08-10 - pct_acquired Must Be Resolved Before Threshold Evaluation

Status: accepted, implemented in `18720b7`.

Decision:

- The `pct_acquired ≥ 50` test in `transaction_value` derivation must operate on a
  **resolved** value, never on the raw column.
- Resolution: NULL becomes 100 for control event types (ACQUISITION, MERGER), per the
  existing default. Otherwise unknown.
- `pct_acquired_source` records `stated` or `assumed`.

Context:

- `pct_acquired` is NULL when 100% is implicit, per its own column comment. `NULL >= 50` does
  not evaluate true, so reading the column raw routes **every ordinary 100% acquisition with
  an unstated percentage** into the below-control branch and adds no debt.
- That is the most common deal in the set silently taking the minority path, producing a
  plausible wrong number rather than an obvious failure.

Consequences:

- This is a general hazard, not specific to `transaction_value`. Any future rule keyed on
  `pct_acquired` must resolve first. The field became a valuation input when the implied tier
  was introduced; its NULL-means-100 convention predates that.
- Test coverage asserts a null-percentage 100% acquisition takes the control branch.

## 2026-08-10 - Schema Sources of Record

Status: accepted.

Decision:

- The schema of record is **`schema/*.sql` collectively** — currently `001_initial.sql`,
  `002_v2_prompt_alignment.sql`, `003_funding_path.sql` — plus the `db.py`
  `_apply_migrations` list. No single file describes the schema.
- **`mvp_goal_and_schema.md` is superseded.** It is Version 0.1, scoped to a 100-transaction
  proof loop, self-described as "not production." Its §6 DDL specifies `transaction`,
  `consideration`, `valuation`, `target_financials` and `deal_characteristic` *tables*; what
  was built flattened all of them into columns.
- The V2 documents — `Transactions Data Model.md` and the `*_transaction_schema.md` set — are
  **design intent, not schema.** They specify `financial_metric`, `consideration_component`
  and `transaction_multiple` tables that do not exist.
- Each of the above needs a header line saying so. Not a rewrite.

Context:

- Three generations of schema thinking are in circulation and two describe tables that were
  never built. During this session that produced repeated wrong claims — references to a
  `consideration_component` table, to a `financial_metric` table, and to "financial metrics" as
  a location rather than columns. Every one was a reasonable reading of the document consulted.
- `001_initial.sql` was also treated as the whole schema by both parties, which is why
  `v2_event_type` was briefly reported as undocumented when it is defined in
  `002_v2_prompt_alignment.sql`.

Consequences:

- **Spec gap 8 mostly dissolves.** It describes enum drift between `mvp_goal_and_schema.md`
  (`TOTAL_TRANSACTION_VALUE`) and the prompts (`TRANSACTION_VALUE`). With that document
  superseded there is no live conflict; the prompt is authoritative and the gap reduces to
  confirming nothing else references the old token.
- Any parity check comparing CREATE statements against the migration list must read all
  `schema/*.sql` files. Comparing against `001_initial.sql` alone produces a false positive
  for every column added by 002 and 003.

## 2026-08-10 - Field Inventory Method and Naming Conventions

Status: accepted as method. Inventory not yet built.

Decision:

- Field definitions are **generated by origin, never transcribed**:
  - **Extracted** — from the prompts, which are authoritative for what is extracted and for
    the enum values.
  - **Derived** — from the aggregation code, where the `_derive_*` functions already declare
    inputs and outputs.
  - **Manual** — a short explicit list. Currently `net_debt`, `total_debt`,
    `cash_st`.
- **Definition precedes parity.** Establish what the field set should be before testing that
  each field reaches all its homes. A parity test over an undefined field set checks
  consistency without checking correctness.
- Naming: `_basis` records **which rung of a waterfall fired**, including `STATED` as a rung.
  `equity_value_basis` (`STATED | PER_SHARE_X_SHARES`) established this, and
  `transaction_value_basis` follows it. Do not import `_method` from the V2 model, where it
  means something narrower.

Context:

- A field's definition is currently spread across up to seven places: the prompt, the parser,
  the `staging_extraction` column, the aggregation read (`_FIELDS`), the observation read
  (`HC_FIELDS`), a `schema/*.sql` file, and the `db.py` migration list. Nothing checks that
  they agree.
- Two divergences surfaced on 2026-08-10: `round_size` absent from `HC_FIELDS` while present
  on the staging read path (would have nulled `investment_amount` for every minority raise on
  the observation loader, fixed in `e66c88c`), and `investment_amount` / `deal_value_currency`
  present only in the migration list.
- The schema is downstream of extraction, so auditing it first examines the consequence
  without knowing the intent.

Consequences:

- A parity test remains wanted, following the pattern of `test_reason_code_parity.py` — the
  answer to drift is a test that fails, not a reference document that goes stale.
- Any committed field inventory must be generated output, not hand-maintained, or it becomes
  the same stale-document problem one layer down.

## 2026-08-11 - Observation Write Path Must Cover Every Field Aggregation Reads

Status: accepted.

Decision:

- **Two invariants**, because the observed defects fall into two shapes:
  1. **Read parity** — every `staging_extraction` column that `_FIELDS` reads must have a
     corresponding observation write. A field readable on the staging path and absent on the
     observation path is a defect, not a configuration.
  2. **Extraction reaches a reader** — every field an extraction prompt emits into a
     `staging_extraction` column must be read by aggregation, or be allow-listed with a reason.
     Invariant 1 alone cannot catch a field that is absent from `_FIELDS` in the first place.
- **Remediation is to wire, not to delete.** Where a field is described below as *dropped*, that
  means the data is being lost, and the fix is to connect it. No column is removed by this entry.
- Extend observation writing to `stages/funding_hc_extract.py` under a new
  `observation_source_stage` value, `FUNDING_HC_EXTRACT`, and add it to the observation loader's
  accepted stages.
- Add the 19 unwired fields to the appropriate observation field group.
- Fields legitimately exclusive to one path — derived rather than extracted — are declared in an
  explicit allow-list **with a reason**, never omitted silently.
- Enforced by check 2 of `docs/spec_field_parity_test.md`, which becomes the mechanism that stops
  this recurring.

Context:

- **No migration has ever updated the observation writer.** Verified 2026-08-11 across both
  migrations:

  | Migration | Staging columns added | Wired to both paths | In `_FIELDS` only | Read by neither |
  |---|---|---|---|---|
  | `002_v2_prompt_alignment` | 13 | 3 | 7 | 3 |
  | `003_funding_path` | 14 | 1 | 12 | 1 |

  The three wired in 002 are precisely the classifier-side fields. The one wired in 003 is
  `round_size`, added retroactively by `e66c88c` as a bug fix rather than by the migration.

- `stages/funding_hc_extract.py` contains no reference to the observation writer; only Stages 4a
  and 7 import it. There is no `FUNDING_HC_EXTRACT` among the `observation_source_stage` values.
  `[verified: stages/funding_hc_extract.py, lib/observation_writer.py — 2026-08-11]`

- **This completes the original decision rather than widening it.** "Guarded Observation-Backed
  Stage 9 Read Path" limited the loader to four stages and excluded `AGREEMENT_EXTRACT` because
  agreement supersession is a separate design problem. That reason does not apply to funding HC
  extraction, which is structurally identical to Stage 4a: source row, extraction, staging, no
  supersession. Stage 4b simply did not exist when the decision was written —
  `003_funding_path.sql` postdates it.

- The acceptance criterion for keeping the observation path was "zero canonical transaction
  diffs." That guarantee is currently false and has been since 002.

Consequences:

- **`AGGREGATION_READ_SOURCE=observation` is not switchable today.** Switching now nulls every
  funding-path field and the seven V2 fields from 002. The default remains `staging` until the
  coverage gap closes and parity is re-validated.
- The 12 tier-2 funding fields are mechanical — they have staging columns and `_FIELDS` entries
  already, and need only observation writes.
- **Tier 3 is not uniform, but every case resolves from evidence — no intent call is needed.**
  - `round_stage_category` — legitimately unread. `_derive_round_stage_category` computes it, and
    `003_funding_path.sql` says so in a comment. Allow-list, with that reason.
  - `target_type_v2`, `spin_split_type_v2` — **data being dropped; wire them.** Add to `_FIELDS`
    with the legacy-fallback read, and to the observation write path.
    `002_v2_prompt_alignment.sql` states the rule in its migration notes: *"Aggregation reads
    `_v2` columns when non-null, falls back to legacy."* Three of the five `_v2` columns implement
    that; these two do not. The file already says what was supposed to happen.
  - `signing_date_precision` — **data being dropped; wire it.** Add to `_FIELDS`, to the
    observation write path, and add the missing `transaction_record` column. Added to staging in
    the same batch as `announced_date_precision` and `closed_date_precision`, both of which are
    read; it also never received a `transaction_record` column, unlike the other two, so the same
    omission occurred twice.

  These are **local migration artifacts and stay local.** The `_v2` columns exist only in this
  repo. They are our shadow of the V2 vocabulary during an expand-and-contract that has not yet
  contracted, and nothing here belongs in a Grata-facing document.

  The supporting claim — zero occurrences of `_v2` in Grata's `enums.py` and `schemas.py` — was
  checked against copies of those files supplied by Erik on 2026-08-11 from
  `grataio/datawarehouse`, `DBX/data_financial_transaction/code/`. **Those files are not in this
  repo and the check cannot be reproduced from it.** Re-verify against the source repo before
  relying on it; treat it as accurate as of that date rather than as a standing fact.
- **`consideration_type` is resolved and is not a drop.** It is derived by
  `_derive_consideration_type` from `consideration_components`, and the extracted value is
  intentionally ignored. Allow-list entry with that reason.
  `[verified: stages/aggregate.py — 2026-08-11]`
- Re-validation of the observation path is owed once coverage closes, and should not be bundled
  with either of the two owed re-aggregations.

**Nothing open.** An earlier revision of this entry parked `target_type_v2`,
`spin_split_type_v2` and `signing_date_precision` as intent calls for Erik. All three were
subsequently resolved from the migration files themselves — see the tier-3 breakdown above — and
the remediation for each is to wire, not to delete. Once the two invariants are accepted, this
entry is mechanical.

## 2026-08-11 - Round Currency Enters the Derived-Value Currency Tag

Status: accepted.

Decision:

- Add a `round_size` currency column to `staging_extraction` and `transaction_record`, populated
  from the `round.currency` the funding HC prompt already emits.
- Generalize `deal_value_currency` resolution from ordered precedence to **unanimity or null**:
  collect every currency present among the contributing sources; if all agree, tag with it; if any
  two disagree, null.
- No conversion. Tag-and-defer is unchanged.

Context:

- `prompts/funding_hc_extraction.md` emits `round.currency`, but `round_size` is a bare `REAL`
  with no currency column anywhere in `schema/*.sql` or the `db.py` migration list.
  `[verified: 2026-08-11]` A €50M round therefore stores as `round_size = 50000000`, indistinguishable
  from dollars.
- This reaches past funding. `round_size` is an input to `_derive_investment_amount` and the first
  funding rung of the `transaction_size` waterfall (`docs/handoff_transaction_size.md`).
- **Unanimity-or-null is not a change to the recorded rule; it is the same rule generalized.**
  "deal_value_currency: single currency tag on derived values" set precedence
  `valuation_currency` then `value_currency`, with a mismatch guard nulling on conflict. With two
  sources, "ordered precedence plus null-on-mismatch" and "unanimity or null" are the same
  function. Stating it as unanimity extends to a third source without inventing a tiebreak, and
  preserves the property the original entry relied on: **the null is the queryable signal.**
- An ordered precedence with three sources would require deciding whether round currency outranks
  post-money currency — a question with no principled answer, since a round stated in euros with a
  post-money in dollars is a genuine mismatch rather than a ranking problem.

Consequences:

- Rows where a source states round size and post-money in different currencies become null-tagged
  and enter the same queryable mismatch set the original entry defined. Expected, not a defect.
- The column must land through `_apply_migrations` **before** either owed re-aggregation runs, or
  the re-aggregation writes without it. Assert the column is present before running.
- `scripts/test_deal_value_currency.py` needs a third-source case: three currencies agreeing tags,
  any two disagreeing nulls.
- Per-field `*_currency` columns remain deferred to the §2.10 currency/FX work. This adds one
  input to the existing single tag; it does not start the per-field build.

## 2026-08-12 - implied_equity_value Derives From equity_value Only

Status: accepted. Implements an existing prohibition that proved too implicit to prevent a
violation.

Decision:

- `implied_equity_value` is derived from `equity_value` — the stake-level, qualified-as-equity
  figure — grossed to 100% basis by `pct_acquired`. Or it is source-stated.
- **It is never derived from `transaction_value`, under any basis**, and **never from
  `post_money_valuation`.** Where `equity_value` is null and no source states an implied figure,
  `implied_equity_value` is null.
- **The change is "replace," not "remove."** The non-control branch of `_derive_implied_equity`
  grossed up `_derive_investment_amount`, which returns `round_size or value_amount` — so the
  `transaction_value` route is one indirection deeper than "a `transaction_value` reference."
  Deleting the branch would also strip the *correct* case, which reaches its right answer through
  the same path. Gross up `equity_value` instead.
- **The `post_money_valuation` branch goes too.** It returned an implied equity for non-control
  types including funding, inverting the Funding Valuation Scope prohibition.

  **Correction, 2026-08-12.** This entry originally described that branch as latent, on the basis
  that no funding row carried an implied value. **That was false, and the error is instructive.**
  Seven funding rows on `pl_funding.db` carried post-money-derived implied equity — Base Power
  13B, DeepX 3.14T, Sarvam 1.5B, Sol.One, OLIX (×2), Horizon3 — every one a live Funding Valuation
  Scope violation and a legal multiple numerator. `[verified: pl_funding_pre_fix.db — 2026-08-12]`

  The claim came from reading a **diff** as a statement about **state**: a check reporting
  `implied_equity_value` NULL→val on two rows was a count of rows that *changed*, and rows already
  carrying the value never appeared in it. The conclusion was then written into this entry as a
  fact about the data.

  The practical consequence: removing this branch corrected seven active violations rather than
  pre-empting a dormant one. Strict scope discipline — "don't fix what isn't firing" — would have
  left them in place. **Any claim about how many rows hold a value must be measured against state,
  never inferred from a diff.**
- **`pct_acquired` must be §2.6-resolved, not read raw.** `_derive_implied_equity` takes the
  resolved `pct` as a parameter (the same one `_derive_transaction_value` receives from the single
  `_resolve_pct_acquired` call), never `fv.get("pct_acquired")`. A raw NULL pct on an
  inherently-partial type must yield None, not fall through to a 100% gross-up — that would
  reopen the manufactured-numerator defect through a `pct`-null door. `NULL`/non-positive pct → None.
- Guard by test, not by comment: assert `implied_equity_value` is null wherever `equity_value` is
  null and no source-stated implied value exists; that no funding-type row carries one; and that a
  partial-type row with `equity_value` populated but `pct_acquired` NULL yields null.

Context:

- **This is a restatement, not a new rule.** "Transaction Size as Universal Magnitude" already
  prohibits it: an unqualified source figure populates `transaction_value` and `transaction_size`
  only, and must never populate equity value or either implied value, because grossing up an
  unqualified number and striking a multiple off it manufactures a figure no source ever qualified.
- The prohibition was written in terms of *unqualified figures*. The implementation read
  `transaction_value` as an acceptable input. Both readings are defensible from the original text,
  which is why the rule needs stating in terms of the **field** rather than the **qualification**.
- Three instances found, all with the same signature — `equity_value` NULL, `transaction_value`
  with basis `STATED`, `implied_equity_value` = `transaction_value / pct_acquired`:
  - TC Skyward — implied 3.892B from a stated 1.946B at pct 50
  - KG Mobility `tc_616c` — implied 750M from a stated 75M at pct 10
  - Genesis Digital Assets (`ma_mvp.db`, dry run) — implied 1.305B from a stated 500M at pct 38.3

  `[verified: pl_funding.db post-step-7, ma_mvp.db dry run on copy — 2026-08-12]`

- **The contrast case proves the correct path already works.** KG Mobility `tc_9731` — the same
  deal, from a different article that failed to cluster — has `equity_value` 75M stated and
  `implied_equity_value` 750M, a ratio of exactly `100/pct`. Both KG rows reach the number through
  the *same* code branch and differ only in whether `equity_value` is populated — which is why the
  fix is a replacement, and why a literal "remove the fallback" would have deleted the correct case
  along with the violations.
- **Grossing up `equity_value` enforces Funding Valuation Scope structurally.** Funding rounds
  vacate `equity_value`, so with `equity_value` the sole input there is nothing for a funding round
  to gross up. The constraint holds because the input does not exist.
- **Why a three-row bug matters more than three rows.** `implied_equity_value` is one of the two
  legal multiple numerators. No multiple has been computed off these figures only because no
  denominator exists in those DBs — the manufactured numerator is already in place.

Consequences:

- **A corrective re-aggregation is owed on `pl_funding.db`** to null the two manufactured values.
  This is not the second owed re-aggregation (`total_debt` + `cash`), which remains separate.
- **`ma_mvp.db` step 7 waits for this fix**, so the run corrects rather than creates.
- Coverage narrows: deals whose only value figure is an unqualified `transaction_value` now yield
  no implied equity, and therefore no implied enterprise value and no multiples. **That is the
  intended outcome.** `transaction_size` still carries their magnitude.
- **Deferred, tracked as the recurrence guard:** an `implied_equity_value_basis` stamp
  (`STATED | GROSSED_UP_FROM_EQUITY`) would make the derivation path legible in the data. Not
  bundled here — it adds a column while re-aggregations are in flight, and the test guards more
  cheaply. It belongs with the implied-tier work.
- The underlying cause is upstream: the single `value.amount` + `value.type` slot forces the model
  to *classify* rather than *record*, so the same figure routes to `equity_value` in one extraction
  and `transaction_value` in another — the "Named Value Fields Replace the Single Value Slot"
  decision (accepted, unmigrated). **This fix bounds the damage; it does not remove the cause.**

## 2026-08-12 - Minority as Feature, Not Core Classifier Output

Status: accepted and implemented in the validation harness.

Decision:

- `MINORITY_INVESTMENT` is no longer a validated core classifier output.
- Minority status is represented as a derived transaction feature, `is_minority`.
- `stake_transition_type` is a nullable explicit-evidence field. `NULL` means insufficient
  explicit ownership-transition evidence, not `UNKNOWN`.
- Accepted `stake_transition_type` values include `NEW_MAJORITY_STAKE`.
- Current-transaction stake evidence takes precedence for minority derivation. Ownership-history
  transition labels are fallback evidence only when current stake evidence is unavailable.

Context:

- `MINORITY_INVESTMENT` conflated underlying economic event type with a stake characteristic,
  spanning M&A minority-stake purchases, growth equity and venture rounds.
- The value model already keys valuation behavior on the underlying economic event and on where
  the money goes, so minority needed to become an orthogonal feature.

Consequences:

- Deal-type classification routes minority transactions to their underlying core event
  (`ACQUISITION`, `GROWTH_EQUITY`, `VC_ROUND`, etc.).
- `is_minority` does not describe post-transaction control state; it describes the current
  transaction's minority-stake characteristic.
- Validation guardrails assert that the classifier rejects `MINORITY_INVESTMENT` as a core output,
  that nullable `stake_transition_type` semantics hold, and that `NEW_MAJORITY_STAKE` does not
  imply minority absent current minority stake evidence.

## 2026-08-12 - High-Confidence Multi-Transaction Shared-Event Guardrail

Status: accepted and implemented in the validation harness.

Decision:

- High-confidence extraction may emit multiple transactions from one source only when the source
  describes a shared announcement/event context.
- Do not split unrelated summary, roundup, market-brief, tombstone-list, or portfolio-list stories
  merely because they mention multiple companies or deals.
- The multi-transaction HC insert shape is guarded so a source with multiple extracted
  transactions does not fail on column/parameter mismatch.

Context:

- PredictLeads-style sources include roundups and multi-company summaries that can mention many
  events without providing a single shared transaction context.
- A prior Stage 4b multi-transaction insert bug crashed on sources with two or more funding
  events.

Consequences:

- Multi-transaction extraction remains available for true shared-event announcements.
- Roundup/list content routes conservatively instead of manufacturing unrelated canonical
  transactions from shared article context.

## 2026-08-13 - Grata V2 Inventory Documents Are Recommendation Inputs

Status: accepted as documentation/harness reconciliation framing only.

Decision:

- Treat `docs/grata_v2_inventory_and_recommendations.md` and
  `docs/grata_v2_data_dictionary.md` as the newest Grata inventory and recommendation inputs.
- Recommendations in those documents are not implementation decisions unless separately accepted
  here or implemented/tested in the harness.

Consequences:

- Proposed items such as merger/reverse-merger flags, de-SPAC placement under M&A,
  `target_type`, generalized security/share mechanics, `consideration_component`,
  Spin/Split mechanics, `transaction_terms_disclosure_status`, neutral metric renames, and
  advisor-person cardinality changes remain recommendations, not accepted implementation
  decisions.

## 2026-08-14 - Recent Annual Actuals May Feed Trailing EV Multiples

Status: accepted and implemented for existing EV/Revenue and EV/EBITDA paths only.

Decision:

- Preserve source financial periods exactly. An `ANNUAL` revenue or EBITDA metric remains
  `ANNUAL`; it is not relabeled to `LTM`.
- The multiple engine may use a recent historical `ANNUAL` actual as a trailing denominator and
  populate the existing `_ltm` analytical slot when date-aligned.
- Eligibility requires a known `announced_date`, a known annual `period_end`, `period_end` not
  after announcement, and `period_end` no more than 455 days before announcement.
- The 455-day window is provisional pending broader corpus review; it is a conservative
  operational threshold, not a permanent taxonomy rule.
- Year-only annual period ends such as `2025` are treated as December 31 of that year for
  eligibility testing only. The stored source period remains `2025`; no fiscal date is persisted
  or silently rewritten.
- Explicit `LTM`/`TTM` remains directly eligible and preferred. `NTM` remains separate.
- Funding gating, currency mismatch behavior, and EV numerator rules are unchanged.

Consequences:

- A transaction announced in March 2026 with FY2025 annual revenue may calculate EV/Revenue in the
  LTM analytical slot while preserving the denominator as an annual source fact.
- Stale annual facts and future annual period ends do not auto-calculate.
- P/E and P/B remain out of scope because this pipeline does not implement net-income/book-value
  denominator capture or equity-multiple calculation.

## 2026-08-17 - Stage 9 Reads the Observation Ledger by Default

Status: accepted.

Decision:

- `AGGREGATION_READ_SOURCE` defaults to `observation`. Stage 9 reads
  `transaction_field_observation` unless told otherwise.
- `staging` remains a supported, explicitly selectable value. It is the rollback
  and debug path and is not deprecated or removed by this change.
- The default is defined once, as `config.DEFAULT_AGGREGATION_READ_SOURCE`, and
  imported by `stages/aggregate.py` for its own `getattr` fallback.

Context:

- The observation read is the only path whose source key is per fact
  (`staging_extraction_id, source_raw_id, observation_fact_key`). The staging read
  carries one collapsed `value_amount`/`value_type` pair per extraction.
- Because of that, a source stating two independently typed values — a stake-level
  equity figure and a whole-company enterprise value — can only keep both under the
  observation read. Under staging the second fact has nowhere to live and is lost
  structurally, not by a defect that could be fixed in the staging path.
- Previously two defaults existed and now disagreed: `load_config` defaulted to
  `staging`, and `stages/aggregate.py` separately defaulted its `getattr` fallback
  to `staging`. Leaving the second in place would have let a config-less caller
  silently take the legacy path.

Consequences:

- Runs that do not set `AGGREGATION_READ_SOURCE` change read path. This is a
  behaviour change for any caller relying on the old implicit default.
- Aggregation remains incremental: only `CLUSTERED` rows are derived. Existing
  `AGGREGATED` rows keep whatever semantics produced them, so this default switch
  does not retroactively re-derive any database. A deliberate
  AGGREGATED→CLUSTERED reset is still required to apply it to existing rows.
- `scripts/test_aggregation_read_default.py` guards the default at both levels:
  the config value and the shared constant, and the behaviour that only the
  observation read can produce (both typed facts surviving into their own
  canonical fields).
- `scripts/validate_331c_observation_read.py` remains the staging-vs-observation
  parity check and still requires a live `--source-db`.

## 2026-08-17 - Financial Qualifiers Anchor to the Source of Their Own Amount

Status: accepted. Implements spec §2.10 items 1 and 2.

Decision:

- A financial qualifier — period type, period end, currency — is resolved from the
  source that supplied the amount it qualifies, not independently across the cluster.
- When the anchoring source stated no qualifier, the qualifier is null. It is never
  borrowed from another source.
- `financials_currency` is shared by revenue and EBITDA, so it cannot anchor to one
  of them. It resolves by unanimity over the currencies the anchoring sources
  actually stated, and to null on disagreement — the same rule as
  `deal_value_currency`.
- `implied_enterprise_value` is not derived from a calculated basis when the deal
  currency and the balance-sheet currency are both known and differ. No conversion
  is attempted; a conversion needs an FX date this pipeline does not carry.
- The cross-currency guard requires both currencies to be known. An unknown
  balance-sheet currency stays permissive.

Context:

- Aggregation resolves every canonical field independently. `target_revenue` could
  be selected from one source while `target_revenue_period_end` and
  `financials_currency` were selected from another, re-labelling an amount with a
  qualifier its own source never stated.
- This is not cosmetic. The annual-as-trailing rule keys off `period_end`, so a
  borrowed date decides whether a multiple is computed at all. The regression
  fixture shows a 5.0x EV/Revenue struck against a period the amount's source never
  stated; after anchoring, the period is null and no multiple is produced.
- It is the same defect class as the typed-value collapse fixed on 2026-08-17: a
  per-fact qualifier resolved independently of the fact it qualifies.

Consequences:

- Some rows will lose a period end, a financials currency, or a multiple that was
  previously populated from a borrowed qualifier. Those values were not supported by
  the source of their own amount; the null is the correct answer, and it is
  queryable.
- Four nullable columns are added to `transaction_record`: `net_debt_currency`,
  `total_debt_currency`, `cash_st_currency`, `balance_sheet_as_of_date`. They are
  manual interim inputs alongside the amounts they qualify, preserved across
  re-aggregation, and unpopulated until debt/cash extraction lands.
- The cross-currency guard is dormant until those currency columns are populated.
  That is deliberate: it is the precondition debt/cash extraction must satisfy, and
  it exists now so the extraction has a defined place to write.
- Aggregation remains incremental. Existing `AGGREGATED` rows keep their unanchored
  qualifiers until a deliberate AGGREGATED→CLUSTERED reset re-derives them.
- Period *coherence* between the balance-sheet as-of date and the multiple
  denominator's period (§2.10 item 2's second half) is not yet enforced. The anchor
  column exists; the tolerance rule is deliberately left undecided rather than
  invented here, and is owed before debt/cash extraction.

## 2026-08-17 - total_debt / Cash_ST Extraction and Debt-Inclusive Arithmetic

Status: accepted. Implements the extraction deferred by "Debt and Cash Inputs" and
closes spec §2.10 item 1.

Decision:

- `total_debt` and `Cash_ST` are extracted as point-in-time balance-sheet items in
  `target_financials`, with `total_debt_currency`, `cash_st_currency` and an exact
  `balance_sheet_as_of_date`.
- Their economic period type is recorded as **`POINT_IN_TIME`**, in
  `balance_sheet_period_type`. There is no LTM/TTM/NTM concept for a balance sheet —
  it is a position on one date, not a period. The value is **derived by aggregation,
  not extracted**: it is a constant, and a constant the model never writes is a
  constant the model cannot mislabel. It is null when no balance-sheet amount is
  present.
- **No annual/quarterly field** is introduced. Filing frequency describes the filing
  a figure came from, not the economic period of the amount; it can be added later
  against a concrete downstream need.
- A **derived** `net_debt` requires both components to share one currency and one
  `balance_sheet_as_of_date`, both known. Reported/manual `net_debt` stays preferred
  and carries no component-coherence requirement, only its own currency.
- Arithmetic mixing consideration with debt or cash requires the relevant currencies
  to be **known and equal**. This covers `implied_enterprise_value`'s calculated
  bases and `transaction_value`'s `EQUITY_PLUS_TOTAL_DEBT`. Unknown on either side
  does not calculate; known-and-differing does not calculate. No FX conversion.
- A source-stated enterprise value is one figure, not a sum, so the guard does not
  apply to `STATED`.
- Extraction prefers a **source-stated** USD figure when the source states the same
  amount in both a local currency and USD. It never performs its own conversion.
- No announced-date tolerance, and no requirement that the balance-sheet date match
  the revenue/EBITDA denominator period. Both deliberately unenforced; the as-of date
  is preserved so corpus behaviour can be evaluated later.

Context:

- This supersedes the permissive-on-unknown-currency behaviour accepted on
  2026-08-17 in "Financial Qualifiers Anchor to the Source of Their Own Amount".
  That leniency was justified while `net_debt` was an unpopulated manual column, by
  analogy to the currency *tagging* idiom. Extraction makes it live, and the analogy
  does not carry: `handoff_currency_normalization.md` licenses the permissive
  multiples guard by naming the plausible-range check as its backstop, and
  `implied_enterprise_value` has no such backstop. A wrong-currency sum there looks
  entirely plausible and becomes a multiple numerator.
- `total_debt` and `Cash_ST` may arrive from different sources, so each takes its
  currency and as-of date from the source that supplied it. `balance_sheet_as_of_date`
  is one column serving both, so it is resolved once per anchor rather than read once
  and shared.

Consequences:

- **Extracted values take precedence over the preserved manual columns.** Manual
  entry was the interim mechanism; an extracted figure arrives with its qualifiers
  attached and must be able to update on re-aggregation rather than be pinned by the
  value it wrote last time. A manual-only row is unaffected.
- `EQUITY_PLUS_TOTAL_DEBT` falls back to `EQUITY_VALUE_ONLY` when the currency guard
  refuses, rather than nulling `transaction_value`. The known equity consideration is
  still real, and `EQUITY_VALUE_ONLY` has never implied debt is zero.
- Rows whose manual `net_debt` has no recorded currency now yield **no** calculated
  `implied_enterprise_value`. This is the intended tightening. The population size is
  unmeasured here — no live DB was available — and should be checked before the
  owed re-aggregation.
- `_derive_implied_enterprise_value` no longer takes `total_debt`/`cash_st`; net debt
  is resolved by `_derive_net_debt` first. Tests encoding the previous signature and
  the previous permissive rule were updated, not worked around.
- Aggregation remains incremental. No corpus-wide AGGREGATED→CLUSTERED reset was
  performed; the second owed re-aggregation is now unblocked but not discharged.
- Review XLSX shape is unchanged.

## 2026-08-17 - Path A Re-aggregation: Accepted

Status: executed and accepted on `data/ma_mvp.db`.

The §4.2-owed re-aggregation, run at `read_source=observation` against the live
92-transaction corpus. Structural invariants held: 92 → 92 transaction records, 98
AGGREGATED + 1 PROMPT_FAILED unchanged, 92 clusters, 92/92 upserted, 0 failed, 0 LLM
conflicts. Stage 10/11 column census was 0/0/0/0 beforehand, so the
INSERT OR REPLACE hazard documented in the runbook cost nothing on this corpus.

Material changes, all accepted as supported:

- `transaction_value` populated on 2 additional rows, both `EQUITY_VALUE_ONLY`
  (Anysphere $60.0B, Payoneer $2.75B). `equity_value` already held the same amount on
  both, so this is the typed-equity fix plus the observation read path recovering a
  Tier-1 value that the collapsed legacy slot had been suppressing.
- `ev_to_revenue_ltm` populated on 1 additional row (Dahl: EV €1.518B / revenue €2.0B
  = 0.76x, ANNUAL period end 2025, EUR/EUR). Supported: same currency on both sides,
  and the annual actual is inside the trailing-eligibility window.
- No count change in `equity_value`, `implied_equity_value`, `enterprise_value`,
  `target_revenue`, `target_revenue_period_end`, or `financials_currency`.

Notably the anchoring change removed nothing. The predicted losses — a borrowed
`period_end` or `financials_currency` being nulled — did not occur, so no row in this
corpus was relying on a cross-source qualifier. That is a real result, not an absence
of evidence: the fixture that motivated the fix reproduces the defect, and the corpus
simply does not contain that shape today.

The currency-gap quantifier reports zero at-risk rows before and after, consistent
with the corpus having no `net_debt` at all.

Consequence: the debt-inclusive paths remain unexercised on real data. Zero rows carry
`net_debt`, zero carry a calculated enterprise value, zero carry
`EQUITY_PLUS_TOTAL_DEBT`. Path A could not change that — see the Path B runbook.

## 2026-08-17 - Stage 9 Writes Only the Columns It Owns

Status: accepted.

Decision:

- Stage 9 writes `transaction_record` with
  `INSERT ... ON CONFLICT(transaction_id) DO UPDATE SET ...` scoped to the 115
  columns it owns, replacing `INSERT OR REPLACE`.
- The 15 columns it does not own are preserved: Stage 10's `linked_filings_count`;
  Stage 11's `acquirer_merger_sub_name`, `merger_structure`, `has_mac_clause`,
  `requires_target_shareholder_vote`, `target_vote_threshold`,
  `closing_conditions_summary`, `target_total_diluted_shares`,
  `fully_diluted_calc_quality`, `agreement_extraction_status`,
  `has_observation_changes`, `observation_changes_field_count`,
  `observation_changes_summary`; plus `notes` and `created_at`.
- The conflict clause assigns `excluded.<col>` directly, **not** COALESCE. A
  Stage-9-owned field whose newly aggregated evidence says NULL must become NULL.
- The owned-column list is the single source of truth: the placeholder count and the
  conflict-update clause are both derived from it, so they cannot drift apart.

Context:

- `INSERT OR REPLACE` deletes the row and inserts a new one, so any column absent
  from the INSERT list was reset to NULL on every re-aggregation — silently, and
  including columns a later stage owns. Re-running Stage 9 alone destroyed Stage
  10/11 output.
- The obvious protection — COALESCE, or writing only non-null values — fixes the
  preservation half and breaks the clearing half, turning every canonical field into
  a high-water mark that can never be retracted. That is a worse defect than the
  original: a value the evidence no longer supports would persist indefinitely.
  `scripts/test_stage9_field_ownership.py` asserts both halves, and was verified to
  fail against a COALESCE implementation.

Consequences:

- The re-aggregation runbook's snapshot-and-restore step is withdrawn; nothing needs
  saving before a reset.
- Ownership is defined by presence in the INSERT column list, which was already the
  de-facto boundary. No column changed hands, so no existing value is reinterpreted.
- Transaction identity and `transaction_source` rows are unaffected — asserted by the
  regression, since REPLACE-then-insert had been re-creating the row each pass.

## 2026-08-17 - FINDING: equity_value Conflates Stake-Level and 100%-Basis Scope

Status: **accepted and FIXED 2026-08-17** (see the resolution entry below). Recorded
here in full because the finding, its blast radius, and the live-data verdict remain
the reasoning behind the fix.

Finding:

`equity_value` is defined as the consideration for the stake actually acquired
(§4.2), but two writers can put a whole-company figure in it, and nothing
downstream can tell the difference.

1. **`STATED` — the HC prompt admits market capitalization.**
   `prompts/high_confidence_extraction.md` defines `EQUITY_VALUE` as "equity
   purchase price, a per-share x shares aggregate the source itself states, or
   **market capitalization**". A market cap is whole-company.
2. **`PER_SHARE_X_SHARES` is 100%-basis by construction.** It computes
   `per_share_price x sec_shares`, where `sec_shares` is the target's TOTAL fully
   diluted count. That is the price of 100% of the equity, not of the stake.

Blast radius (`stages/aggregate.py`, the cluster loop):

    equity_value -> _derive_implied_equity(pct) -> implied_equity_value
                        -> implied_enterprise_value -> enterprise_value
                                                    -> ev_to_revenue_ltm
                                                    -> ev_to_ebitda_ltm
                 -> _derive_transaction_value(pct) -> transaction_value (+ _basis)
                        -> (future) transaction_size

**The damage condition is uniform: `pct_resolved < 100`.** `_derive_implied_equity`
divides by pct, so a figure already at 100% is grossed up a second time; a $2.2B
market cap at pct 27 yields $8.15B implied equity. At `pct = 100` the conflation is
inert, because stake-level and whole-company coincide. `pct` null yields None, so
there is no leak through that door.

Classification:

- **`PER_SHARE_X_SHARES`: dormant code defect.** `sec_shares` is hardcoded `None`,
  so the branch has never executed. Zero live rows, provably. It activates silently
  when SEC share count is wired (spec §4 gap 5) unless gated first.
- **Market cap: confirmed prompt/taxonomy defect; live-data status UNDETERMINED.**
  The container carrying this analysis has no corpus DB. The permission has existed
  since `97fe6b1` (2026-08-07) across every HC version that produced the corpus, so
  exposure is plausible but not established. Determine by running the read-only
  diagnostic before asserting either way; do not infer.

Context:

- "market capitalization" appears exactly once in the repo — that prompt line. It is
  in no spec section, no decisions entry, and no dictionary row.
- It predates the rule it contradicts. §4.2 made `equity_value` stake-level on
  2026-08-10; the prompt was not revisited.
- `value_observations` carries no scope discriminator: types are `EQUITY_VALUE` /
  `TRANSACTION_VALUE` / `ENTERPRISE_VALUE` / `UNDISCLOSED`, and `basis` is `STATED`.
- `_pick_value_amount_for_type` ranks candidates by source tier and model confidence
  only. Two same-tier, same-confidence `EQUITY_VALUE` observations from one source —
  a stake price and a market cap — tie-break positionally, so the picker can select
  the market cap while the correct figure sits beside it.

Minimum correction (proposed, not implemented):

The invariant is that every writer into `equity_value` must be stake-level by
construction. There are exactly two writers, so exactly two changes:

- **Taxonomy.** Redefine `EQUITY_VALUE` as stake-level consideration only, and give
  market capitalization its own observation type, retained as a fact rather than
  routed into any canonical field. A market cap is a *market* valuation, not a
  transaction-implied one, so it does not belong in `implied_equity_value` either.
  This needs **no new scope column** — `value_type` is already the discriminator, it
  is merely under-specified.
- **Derivation.** Gate `PER_SHARE_X_SHARES` on `pct_resolved == 100`. Do not reroute
  it to `implied_equity_value` (that would leave Tier 1 empty for the ordinary public
  take-private) and do not scale it by pct (we hold total shares, not acquired
  shares, so any scaling manufactures a figure no source stated).

Consequences:

- The two fixes are not equal in cost or effect. The derivation gate is code-only,
  deterministic, and zero-risk on a dormant branch. The taxonomy fix needs a prompt
  version bump and **only takes effect on re-extracted rows**, so it does not
  retroactively clean the corpus — existing rows stay ambiguous until Path B, which
  is deferred. Assessing them is a diagnostic-plus-human-review job, not a code fix.
- `transaction_size` is not blocked by either. Having removed the direct equity rung,
  it reads `transaction_value` only, so it inherits this defect without amplifying it.

## 2026-08-17 - equity_value Is Stake-Level Only; Market Cap Is Its Own Type

Status: accepted. Implemented; forward-looking. **No re-extraction** — Path B stays
deferred, so existing rows are unchanged by this commit.

Decision:

- `EQUITY_VALUE` means the equity purchase price for the **stake actually acquired**,
  or a per-share x shares aggregate the source itself states. It is consideration that
  changed hands, not a valuation of the whole company.
- **Market capitalization is no longer an `EQUITY_VALUE`.** It gets its own type,
  `MARKET_CAPITALIZATION` (HC prompt 0.18), which is captured so the fact survives in
  the observation ledger but never flows into canonical consideration — not into
  `equity_value`, and not into `implied_equity_value` either.
- `PER_SHARE_X_SHARES` may populate stake-level `equity_value` **only when
  `pct_acquired == 100`**. Unknown pct is refused along with pct below 100.
- **It is not scaled.** `per_share x total_shares x pct` is not derived below 100,
  because the pipeline holds total shares and never acquired shares, so any scaling
  manufactures a stake amount no source stated. None is the correct output.
- `MARKET_CAPITALIZATION` is added to `_VALID_VALUE_TYPES`, without which a 0.18
  extraction emitting it would be rejected wholesale rather than merely ignored.
- A market cap is never the primary/legacy value fact. The primary is the most
  transaction-specific one.

Context:

- The live diagnostic on `data/ma_mvp.db` found **no evidence of contamination**: 92
  records, 7 with `equity_value`, 1 at `pct < 100` and therefore exposed, 0 rows at
  `PER_SHARE_X_SHARES`, 0 confirmed market-cap candidates. Text matching is heuristic,
  so this is *no evidence found*, **not proof of absence** — and the fix is therefore
  framed as forward-looking rather than as a cleanup.
- The one exposed row is where a contaminated extraction would land, which is why the
  guard is worth having even on a clean corpus.
- `_derive_implied_equity` divides by pct, so a whole-company amount reaching
  `equity_value` is grossed up a second time: 2.2B at pct 27 yields 8.15B of implied
  equity, and any multiple struck off it is manufactured.
- `value_type` was already the natural scope discriminator; it was merely
  under-specified. Splitting the type therefore needs **no new column**.

Consequences:

- Guarded by `scripts/test_equity_value_scope.py`, which was verified to fail against
  both halves independently — the ungated per-share branch, and a prompt that still
  admits market cap.
- Its end-to-end fixture deliberately resolves the collapsed legacy value slot **to the
  market cap**. `equity_value` must still be the stake consideration, because each
  canonical field consumes its own semantic type rather than whichever fact wins the
  legacy collapse. A stub picking the equity fact would let a scope-blind
  implementation pass.
- The taxonomy half only takes effect on rows extracted at 0.18 or later. It does not
  retroactively clean the corpus, and no re-extraction is scheduled to make it do so.
- The `PER_SHARE_X_SHARES` gate is inert today (`sec_shares` is hardcoded `None`) but
  had to land **before** SEC share count is wired, which would otherwise have activated
  the defect silently.

## 2026-08-17 - transaction_size: Family-Keyed Waterfall, Two Rungs Reserved

Status: accepted. Implemented.

Decision:

- `transaction_size` is derived in aggregation, **never extracted**. Keyed on event
  family, and the families are **disjoint** — a funding round never falls through to a
  purchase price, and an M&A deal never falls through to a round size. Ordering has
  meaning only within a family.

      M&A (ACQUISITION / MERGER / REVERSE_MERGER) -> transaction_value -> TRANSACTION_VALUE
      Funding (VC_ROUND / GROWTH_EQUITY / VENTURE_DEBT) -> round_size -> ROUND_SIZE
      Spin/Split (SPIN_OFF / SPLIT_OFF)          -> reserved, no live rung
      everything else                            -> null

- `transaction_size_basis` is written whenever `transaction_size` is, and never
  separately. Both are Stage-9-owned.
- Two vocabulary values are **reserved but not live**: `SOLE_INVESTOR_AMOUNT` and
  `SPIN_SPLIT_CONSIDERATION_VALUE`. Reserving them keeps the enum stable so a later
  commit adds a branch rather than renaming stored data.
- **No equity rung and no EV rung.** `EQUITY_BELOW_CONTROL` stays exclusively a
  `transaction_value_basis` value.
- The review export's shadow waterfall is **removed**, not kept as a backstop. The
  XLSX shape stays at 67 columns.

Context:

- **Why no equity rung.** Every state where a stake-level equity figure can safely
  stand for the magnitude already produces `transaction_value`. Tracing
  `_derive_transaction_value`, the only states with `transaction_value` null and
  `equity_value` known are those where `pct_acquired` is null — i.e. transaction scope
  is unknown, so the figure could be the whole company. The genuinely safe case
  (equity stated, pct merely unstated, control event) is already caught by the pct=100
  "assumed" default and consumes the transaction-value rung. The rung's reachable set
  was exactly the unsafe complement.
- **Why no EV rung.** Below control an enterprise value is the grossed-up
  whole-company figure and would report a 27%-for-$600M deal as $2.22B. Spec §2.10
  item 3 stays parked; the currency and period work that landed on items 1-2 does not
  bear on the gross-up.
- **Why `EQUITY_BELOW_CONTROL` is not here.** Every value in this enum names the
  *source field* that supplied the magnitude. `EQUITY_BELOW_CONTROL` names a
  derivation condition, and the control status it records is already carried by
  `transaction_value_basis`, `is_minority` and `pct_acquired`. Duplicating it would
  make the field two-dimensional.
- **Why `SOLE_INVESTOR_AMOUNT` is reserved rather than built.**
  `transaction_participant` has no per-investor amount column — the funding prompt asks
  for one, but there is nowhere to store it. `transaction_record.investment_amount` is
  not a substitute: it is transaction-level and falls back to the legacy value slot, so
  reading it would report a round, or a valuation, as one investor's check.
- **Why a multi-investor round goes null.** Per-investor disclosure runs around 30% for
  leads and under 5% for others, so summing whatever amounts exist understates the
  round while presenting as one — worse than null, because the shortfall is invisible.
- **PIPE coverage is deliberately out.** The funding family is exactly the classifier's
  three types. A PIPE or public-company primary raise is not forced into them
  (`prompts/deal_type_classifier.md`, guarded by
  `scripts/test_minority_core_classification.py`), so it lands in `UNKNOWN` and gets a
  null size. Widening the family inside the waterfall would silently reclassify deals
  through the size field. Whether PIPEs deserve funding-size treatment is a separate
  classifier/product decision.

Consequences:

- Guarded by `scripts/test_transaction_size.py`, verified to fail both when an equity
  rung is re-added (the EV-only fixture then reports 2.22B) and when the export shadow
  is restored.
- **One waterfall, not two.** A canonical null now shows blank in the review sheet
  where the shadow would have printed a figure. That is intended: the canonical rules
  say the magnitude is unsupported, and a blank states it honestly. Reviewers will see
  fewer populated size cells than before, and the ones remaining are attributable.
- `transaction_size` must not be summed across bases — a control acquisition and a
  round are different events. Enforce in the query layer. Note the basis alone does not
  separate a below-control M&A from a control one (both stamp `TRANSACTION_VALUE`), so
  a grouping key wanting that distinction needs `is_minority` or `pct_acquired` too.
- Stage 9 now owns 117 columns, up from 115.

## 2026-08-17 - Funding Events Derive No transaction_value or equity_value

Status: accepted. Implemented. **Model-integrity guard, independent of the historical
backfill** — it stops the class; it does not correct the ten existing rows.

Decision:

- `_derive_transaction_value` and `_derive_equity_value` return `(None, None)` for
  `VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`. A round is primary capital into the
  company: there is no purchase price and no equity bought, so both fields are
  **categorically inapplicable**, not merely usually absent.
- **The gate is the funding family only.** `MINORITY_INVESTMENT` stays outside it — a
  secondary purchase of a non-controlling stake is an ordinary acquisition whose
  consideration is a real `EQUITY_VALUE`, and the classifier routes genuine secondaries
  to `ACQUISITION` so that stays true.
- The guard **refuses; it does not reclassify.** It never moves an amount into
  `round_size`. That is source-supported remediation, not a derivation.

Context:

- Stage 9 previously relied on funding rows simply not having the M&A value fields
  populated — an assumption about upstream, never an enforced rule. `_compute_multiples`
  and `_derive_investment_amount` already gated on family; these two did not.
- The assumption holds only for rows extracted after the funding path split on
  2026-08-07. Before that, funding rows went through the M&A extractor, which had no
  `round_size` write and no capital-raised precondition until prompt 0.13 (2026-08-11),
  so a Series A had nowhere to land but `value_amount` typed `TRANSACTION_VALUE`.
- The live corpus has **ten** such rows, **all at prompt 0.12** and **none at 0.13+**.
  That is what makes this legacy-data remediation rather than evidence of a broken
  current extraction path. Without the gate, every re-aggregation would faithfully
  regenerate a canonical M&A `transaction_value` from each of them, indefinitely.
- The stale `EQUITY_VALUE` path was the worse of the two: it would gross up through
  `_derive_implied_equity` into an implied tier and then a multiple.

Consequences:

- Guarded by `scripts/test_funding_value_family_gate.py`, verified to fail against each
  gate independently.
- **Nothing is destroyed by refusing.** The amount remains in
  `staging_extraction.value_amount`, in the observation ledger, and in the canonical
  `investment_amount` — asserted by the regression, because the remediation depends on
  those amounts staying visible.
- Ten rows will show null `transaction_value` after the next re-aggregation. That is the
  correct canonical state: the amount is not a purchase price, and no evidence yet
  supports it as a round size either. **Null is the honest answer until a human
  classifies each row against its source.**
- This is a canonical-model correction for every consumer — DB, API, analytics, the
  Grata model. It is not a review-sheet concern, and no part of it was shaped to
  preserve the historical XLSX appearance.

## 2026-08-17 - Funding Magnitude: round_size Is the Event, investment_amount Is a Check

Status: accepted. Implemented. Supersedes the sole-investor rung in
"transaction_size: Family-Keyed Waterfall, Two Rungs Reserved".

Decision:

- `round_size` is the **total amount raised in the financing event**. For Funding,
  `round_size -> transaction_size` at `transaction_size_basis = ROUND_SIZE`. Funding
  never falls back to `transaction_value`.
- `investment_amount` means **one named investor's check**. It is supplemental
  party-level detail, expected null for most deals. It must not populate
  `transaction_size`, must not substitute for `round_size`, and must not be summed to
  manufacture one.
- **`SOLE_INVESTOR_AMOUNT` is removed** from the `transaction_size` waterfall, the basis
  vocabulary, and the Grata recommendation.
- `_derive_investment_amount` no longer derives anything at transaction level. It
  previously returned `round_size or value_amount` for any non-control event.

The settled contract, as a worked example — $100M round, Firm A invests $50M:

    round_size            = 100M
    transaction_size      = 100M
    transaction_size_basis = ROUND_SIZE
    Firm A investment_amount = 50M     (party-level)

Nothing is added or rolled up. If only Firm A's $50M is known and the round total is
undisclosed: `investment_amount = 50M`, `round_size = NULL`, `transaction_size = NULL`.

Context:

- **The original sole-investor argument was wrong in kind.** It framed the hazard as
  disclosure coverage — summing sparse checks understates a round — and concluded that
  restricting to a single disclosed investor made the rollup safe. It does not. A check
  is not the event's magnitude *at any disclosure level*: reporting a $50M check as a
  $100M round's size is wrong even when that check is the only one disclosed, and even
  when there is only one investor. The disclosure statistics remain true and still rule
  out summing; they were simply never the reason the rung had to go.
- `_derive_investment_amount` broke the definition in both directions: it copied a round
  *total* into a field meaning one investor's *check*, and where no round size existed it
  fell back to a generic `value_amount` naming no investor at all. On the ten legacy
  funding rows, that second fallback is how a misclassified raise acquired a canonical
  home.
- **Correcting an earlier claim in this log:** the sole-investor rung was first reserved
  on the stated ground that no per-investor amount column existed. That was wrong —
  `staging_investor.investment_amount` does exist and Stage 4b populates it. The rung is
  removed on semantics, not on availability.

Consequences:

- Guarded by `scripts/test_transaction_size.py` (the basis vocabulary must not contain
  `SOLE_INVESTOR_AMOUNT` or `SOLE_INVESTOR_CHECK`; a known investor check with an
  undisclosed total yields a null size) and `scripts/test_funding_value_family_gate.py`
  (neither a round total nor a generic funding amount may land in `investment_amount`).
- Transaction-level `investment_amount` now derives to NULL for every row. This
  discharges "legacy `investment_amount` equal to the generic funding amount should be
  cleared" **without any data mutation** — the column is Stage-9-owned, so
  re-aggregation clears it.
- This changes an assertion made earlier in the family-gate work, that the legacy amount
  was safely preserved in `investment_amount`. Under the settled model that was itself a
  mis-mapping. The amount remains findable in `staging_extraction.value_amount` and in
  the observation ledger, which is where the remediation reads it from.
- The transaction-level column is now inert. Whether to drop it, or repurpose it as a
  materialized view of a single party-level check, is an open schema question — not
  decided here.
