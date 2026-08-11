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

Status: accepted in principle; enterprise-value implementation under review.

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

- The `enterprise_value_*` field family is renamed to `implied_enterprise_value_*`.
- Multiples cannot be struck off as-transacted values structurally, rather than by
  convention.
- Currency and period-coherence questions remain open and block implementation: implied
  enterprise value adds consideration in deal currency to net debt in the target's
  reporting currency, and net debt anchors to announced date while the multiple
  denominator carries its own period basis.
- One item is parked: feeding `implied_enterprise_value` into `transaction_size` when
  neither transaction value nor equity value is available. Control deals only.

## 2026-08-10 - Transaction Value Follows Control

Status: accepted.

Decision:

- `transaction_value` is populated as-reported wherever a source states one.
- Where it is calculated:
  - `pct_acquired` < 50 → `transaction_value` = `equity_value`. No debt is added.
  - `pct_acquired` ≥ 50 → `transaction_value` = `equity_value` + gross debt.
  - `pct_acquired` ≥ 50 with debt unknown and nothing stated → null. Do not assume
    debt = 0.
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
  takes on its debt, so adding gross debt records something that happened.
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
- `transaction_value` equals `equity_value` for minority deals. Redundant but honest, and
  it keeps `transaction_size` populated without a special case.
- The reconciliation identity `transaction_value - cash = implied_enterprise_value` holds
  for control deals only.
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
