# Handoff: Decision #9 — VC_ROUND vs GROWTH_EQUITY boundary

**Repo:** `ma-collection-mvp` · **Prompt:** `prompts/deal_type_classifier.md` (0.6), types #9/#10
**Type:** taxonomy DECISION (not a bug) — the classifier is following the current rule correctly.

## The situation
`Base Power` raised a **$1B Series D at ~$13B post-money** (Ribbit, Addition, Valor Equity,
JPMorgan Strategic Investment Group, Altimeter, D1, a16z/Lightspeed per the source). The pipeline
classified it **`VC_ROUND`**. Intuitively a $1B Series D reads as *growth/late-stage*, so: should
it be `GROWTH_EQUITY`?

## Current rule (why VC_ROUND is "correct" today)
`deal_type_classifier.md`:
- **#9 VC_ROUND** — a priced/unpriced venture round, Seed → Series N, angel, crowdfunding,
  convertible notes as primary funding.
- **#10 GROWTH_EQUITY** — "a growth-equity/late-stage investor (General Atlantic, Summit, TA
  Associates) taking a minority stake in a **profitable or near-profitable** company. **When
  unclear between VC_ROUND and GROWTH_EQUITY, use VC_ROUND.**"

So the split keys on **investor archetype + company profitability**, and *ignores round
size/stage*, with an explicit tie-break to VC_ROUND. Base Power → VC_ROUND because: labeled
Series D (venture round), classic VC/crossover investors (not growth-PE names), pre-profit
venture-backed startup, and the tie-break defaults to VC_ROUND.

## The decision
**Should stage/size tip the call — e.g. `Series D+` and/or round `≥ $X` → GROWTH_EQUITY,
regardless of investor type?** (Erik leaning **Series D+ → GROWTH_EQUITY**.)

Trade-offs:
- **For a stage/size rule:** simple, deterministic, matches market shorthand ("mega/late round
  = growth").
- **Against / caveats:** a labeled Series D can still be a classic VC round; growth equity can
  occur as early as Series B; investor archetype (crossover/hedge funds vs. pure VC) is often the
  truer signal. A pure stage threshold will misfile some deals either way.

## If adopted, what changes
1. Rewrite #9/#10 in `deal_type_classifier.md` to state the **precedence**: does a stage/size
   threshold override the investor-type/profitability test, or only break ties?
2. Pick the threshold(s): stage (`Series D+`?), size (`≥ $X`?), or both (either-triggers).
3. Update the worked examples (Base Power is a good canonical GROWTH_EQUITY example if adopted).
4. Confirm downstream consumers treat `GROWTH_EQUITY` like `VC_ROUND` for the funding path
   (extraction stage 4b already lists VC_ROUND / GROWTH_EQUITY / VENTURE_DEBT; the #5 clustering
   fix should include GROWTH_EQUITY in the funding branch).

## No code bug here
The classifier obeyed the rule (its note: "*Despite the $1B size, this is clearly a priced VC
funding round (Series D)… Overrides AMBIGUOUS hint*"). This is purely a definition choice for the
team; nothing to "fix" until the rule changes.
