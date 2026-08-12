# Decision - minority as a flag, with explicit stake transitions

**Status: ACCEPTED for this validation harness as of 2026-08-12.**

This document records the harness design only. It does not propose or change
Grata production schema/enums.

---

## 2026-08-12 - Minority Is a Flag, Not a Core Event Type

Status: **accepted in harness.** Structural classifier/extraction change; no
historical migration or production-schema change.

Decision:

- `MINORITY_INVESTMENT` is removed from the validated core event classifier
  output vocabulary in this harness.
- Core event type answers what transaction occurred: `ACQUISITION`,
  `GROWTH_EQUITY`, `VC_ROUND`, `MERGER`, etc.
- Minority status is a shared characteristic derived in aggregation, following
  the take-private derived-flag pattern.
- Relevancy reason code `MINORITY_INVESTMENT` is preserved for now. It describes
  why a story is relevant, not the core transaction type.
- Do not infer minority status from `GROWTH_EQUITY` or `VC_ROUND` alone.
- Public-company PIPE / primary issuance language must not force
  `GROWTH_EQUITY` or `VC_ROUND` unless the source supports that underlying
  economic event. Use `UNKNOWN` when no supported core event fits.

Why:

- Minority is a cross-cutting characteristic, not an event. A secondary
  non-control stake purchase is still an acquisition; a Series A is still a VC
  round; a growth investment is still growth equity.
- The old core type conflated primary funding and secondary stake purchases.
  That made classification analytically tempting but structurally ambiguous.
- Removing the core type prevents a transaction from being routed by stake size
  instead of by what actually happened.

---

## 2026-08-12 - Explicit Stake Transition Model

Status: **accepted in harness.**

Lumina/TNQTech showed that `pct_acquired < 50` is not sufficient to describe
post-transaction control state: an existing 80% owner acquiring the remaining
20% is already in control and becomes a 100% owner. It still involves a
minority-sized stake in the current transaction.

Decision:

- Add nullable `stake_transition_type` to the harness staging and transaction
  records.
- HC extraction populates it only when the source explicitly states enough
  ownership-transition evidence to distinguish prior ownership, current stake
  acquired, and/or resulting ownership/control.
- `NULL`, not `UNKNOWN`, is the deliberate no-observation state. If the source
  does not provide enough explicit evidence, leave `stake_transition_type` null
  so aggregation can apply its conservative fallback rules.
- Aggregation derives `is_minority` from `stake_transition_type` first, using it
  as evidence that the transaction involves a minority interest/stake feature,
  then falls back to legacy `MINORITY_INVESTMENT` and stated
  `pct_acquired < 50` only when no explicit transition evidence exists.
- Value formulas are unchanged in this slice.

Harness enum:

- `NEW_MINORITY_STAKE`
- `FULL_ACQUISITION`
- `MINORITY_ACQUIRING_MAJORITY`
- `MAJORITY_ACQUIRE_REMAINING`
- `MINORITY_ACQUIRING_REMAINING`
- `MAJORITY_INCREASING_STAKE`
- `MINORITY_INCREASING_STAKE`

`UNKNOWN` is intentionally not part of this enum. Ambiguity or insufficient
evidence is represented by null.

`is_minority` rule:

- `true` for `NEW_MINORITY_STAKE`, `MINORITY_INCREASING_STAKE`,
  `MAJORITY_INCREASING_STAKE`, `MAJORITY_ACQUIRE_REMAINING`,
  `MINORITY_ACQUIRING_MAJORITY`, and `MINORITY_ACQUIRING_REMAINING`.
- `false` for `FULL_ACQUISITION`.
- Fallback only: if `stake_transition_type` is null, legacy rows with
  `MINORITY_INVESTMENT` or a stated `pct_acquired < 50` derive `is_minority`.

Canonical Lumina/TNQTech result:

- Source language: Lumina previously signed to acquire an 80% controlling stake;
  the current announcement says it acquired the remaining 20%, making TNQTech a
  100% wholly owned subsidiary.
- Core event type: `ACQUISITION`.
- `pct_acquired`: `20`.
- `stake_transition_type`: `MAJORITY_ACQUIRE_REMAINING`.
- `is_minority`: `true`.
- The resulting 100% ownership must not replace the current transaction's 20%
  stake acquired.

Regression expectations:

| Transition | Core event type | pct_acquired | stake_transition_type | is_minority |
|---|---:|---:|---:|---:|
| 0% -> 20% | `ACQUISITION` if secondary; funding type if primary | `20` | `NEW_MINORITY_STAKE` | `true` |
| 30% -> 60% | `ACQUISITION` | `30` | `MINORITY_ACQUIRING_MAJORITY` | `true` |
| 20% -> 100% | `ACQUISITION` | `80` | `MINORITY_ACQUIRING_REMAINING` | `true` |
| 60% -> 80% | `ACQUISITION` | `20` | `MAJORITY_INCREASING_STAKE` | `true` |
| 20% -> 35% | `ACQUISITION` | `15` | `MINORITY_INCREASING_STAKE` | `true` |
| 80% -> 100% | `ACQUISITION` | `20` | `MAJORITY_ACQUIRE_REMAINING` | `true` |
| 0% -> 100% | `ACQUISITION` | `100` | `FULL_ACQUISITION` | `false` |

Downstream value implication:

- This slice intentionally does not change transaction-value, implied-equity,
  enterprise-value, or multiple formulas.
- The new field prevents `is_minority` from being mistaken for post-transaction
  control state. Control/debt valuation refinements remain a separate decision.

Validation:

- Four-story live validation rerun on 2026-08-12:
  - Lumina/TNQTech: `ACQUISITION`, `pct_acquired=20`,
    `stake_transition_type=MAJORITY_ACQUIRE_REMAINING`, `is_minority=1`.
  - LMPG/Platinum: `ACQUISITION`, no explicit transition evidence,
    `stake_transition_type=null`, stable current default behavior.
  - Lydian Series A: `VC_ROUND`, `stake_transition_type=null`, `is_minority=0`.
  - Paradium/InfoSentience: `ACQUISITION`,
    `stake_transition_type=FULL_ACQUISITION`, `is_minority=0`.

Implementation touchpoints:

- `prompts/deal_type_classifier.md`
- `stages/deal_type_classify.py`
- `prompts/high_confidence_extraction.md`
- `stages/high_confidence_extract.py`
- `schema/001_initial.sql`
- `db.py`
- `lib/observation_writer.py`
- `stages/aggregate.py`
- `scripts/test_minority_core_classification.py`
- `scripts/test_minority_flag_foundation.py`
