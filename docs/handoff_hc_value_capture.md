# Handoff: Capital-raised rule + MINORITY_INVESTMENT capture field

**Status:** LIVE. Implementation-ready.
**Spec:** `docs/spec_transaction_value_model.md` §4.1
**Decisions:** `docs/decisions.md` — "Value Path Keyed on Where the Money Goes"
**Companion:** `handoff_stake_level_equity_value.md` — §4.2, latent, blocked (see that doc).

Self-contained. Do not require the originating discussion to implement.

> **Scope note.** An earlier draft bundled a second change — replacing the single `value` slot
> with named `*_as_reported` fields. That is an unspec'd migration and has been removed from
> this handoff. It is being routed through a decision and spec update first. Do not implement
> it from this document.

---

## Scope

**In scope:** the capital-raised precondition in `prompts/high_confidence_extraction.md`, a
capture field on the HC path scoped to `MINORITY_INVESTMENT`, and the LC input template.

**Out of scope. Do not touch:**

- Named `*_as_reported` value fields — pending decision and spec update
- `equity_value` path consistency and re-aggregation — companion handoff, blocked
- `enterprise_value`, `implied_enterprise_value`, any `enterprise_value_*` column
- `transaction_multiple.numerator_value_type`
- `stages/export.py`
- Deal-type taxonomy (Decision #9 is open)
- Currency normalisation, FX, period anchoring

**Guard:** do not export derived valuations and do not repoint multiples at the derived
enterprise value. Either converts a latent defect to a live one before its fix exists.

---

## The problem

The `value.type` vocabulary offers four values — `EQUITY_VALUE`, `TRANSACTION_VALUE`,
`ENTERPRISE_VALUE`, `UNDISCLOSED` — all of which describe the purchase of a company. None
describes capital invested *into* one.

A raise reaching this prompt therefore has no correct option, so the model force-fits, and
`EQUITY_VALUE` is the natural wrong choice because the money did buy equity. That is bug 8.

Live because `value_amount` and `value_type` export — this is how it reached
`ML_worksheet.csv` `[verified: stages/export.py, 2026-08-10]`.

---

## Prerequisite — narrowed, and now unblocked

`VC_ROUND`, `GROWTH_EQUITY` and `VENTURE_DEBT` already route to the funding path (Stage 4b)
and carry the raise in `round.size`
`[verified: docs/funding_path_design.md:73–75, stages/aggregate.py:46,50,348, 2026-08-10]`.

So the HC capture gap is **not** general. It exists only for:

- `MINORITY_INVESTMENT` deals, which stay on the HC path
- Primaries misclassified into a non-funding type

**Required:** a capture field on the HC path scoped to `MINORITY_INVESTMENT`, mirroring the
funding prompt's naming (`round.size`) so the two merge cleanly.

Do not null any value field until that capture field exists. Nulling without it converts a
mislabelling bug into a data-loss bug.

### The aggregation read must move with it

`_derive_investment_amount` routes `round_size or value_amount` → `investment_amount`
`[verified: stages/aggregate.py:351–353, 2026-08-10]`. On the HC path there is no `round_size`,
so today the check reaches `investment_amount` **via `value_amount`** — the derivation works
*because* of the mislabel.

Nulling `value_amount` for capital-raised deals therefore breaks a derivation that currently
succeeds: `investment_amount` goes null for every minority investment.

**So the change is two-sided.** Add the capture field on the prompt, and point
`_derive_investment_amount` at it. Shipping the prompt half alone is a regression.

Add to the verification set: `investment_amount` is populated on every minority-investment row
after the change that was populated before it.

*Note: `MINORITY_INVESTMENT` is slated for dissolution under the taxonomy work
(`docs/decisions.md`). This capture field serves a transitional type deliberately — the raise
must be captured now, and the rule below is keyed on capital flow rather than on the type, so
it survives the dissolution without revision.*

---

## The change

Insert above the value-type rules:

```
CAPITAL RAISED — precondition

If the stated amount is capital being raised by, or invested into, the company —
a funding round, growth investment, PIPE, or subscription for newly issued shares
— it is not a value type. Signals: "raised", "$X funding round", "investment of
$X in <company>", "to fund expansion".

Record the figure in the round-size capture field, set value.amount = null and
value.type = null, and record the reason in notes as PRIMARY_CAPITAL. An amount
invested as new capital is never the company's equity value, enterprise value, or
transaction value.

Otherwise continue to the rules below. Buying shares from an existing holder —
including a minority stake — is an ordinary acquisition and classifies normally.

If the source does not permit the distinction, set value_type_confidence = LOW and
note the ambiguity.
```

**The test is one-directional by design.** Only the capital-in case needs detecting;
everything else falls through to the four existing values, which already handle it.

**Key it on where the money goes, not on deal type.** An acquisition of a minority stake
*should* map its consideration to `EQUITY_VALUE`, so a deal-type-keyed rule would break the
correct case along with the broken one. A capital-flow rule also survives both the
`MINORITY_INVESTMENT` dissolution and the open Decision #9 boundary without revision. This is
a prompt-level reasoning test, **not a stored field** — no `capital_flow` column is being
added; that was considered and rejected.

**Record the reason for the null.** A capital-raised null is otherwise indistinguishable from
a genuinely undisclosed value or a missed extraction, and QA cannot tell a deliberate null
from a whiffed one. A note value suffices — `financials_disclosure_status` (`DISCLOSED` /
`UNDISCLOSED` / `UNKNOWN`) already exists in the same prompt for the disclosure axis proper.

**Identify the insertion point by content, not by number.** The live block is a four-value
definition list, not a numbered priority structure. Earlier drafts referred to a "rule 3c";
no such rule exists.

---

## Downstream — LC input template only

`prompts/low_confidence_extraction.md` **consumes** HC's value and does not produce one of its
own `[verified: prompts/low_confidence_extraction.md:24, 2026-08-10]`. So no LC extraction
logic changes — only its input template.

It reconciles consideration components against the passed value: input contract at `:63`,
template at `:242` (`DEAL VALUE: {value_amount} {value_currency} ({value_type})`),
reconciliation instructions at `:147–148`.

Null deal values are already tolerated — `:474` shows `DEAL VALUE: null null (UNDISCLOSED)`.
But capital-raised renders `null null (null)`, with a null `value_type` rather than
`UNDISCLOSED`, and that shape has no example in the prompt.

- Extend the `:474` precedent to cover it.
- Confirm the reconciliation instruction at `:147–148` does not fire against a null total.

If the round amount is more useful context for these deals, pass it explicitly — but never in
the `value_amount` slot, which reintroduces the same conflation one stage downstream.

---

## Acceptance tests

### Minority investment raise — the origin case

> Company raises $200M from an investor at a $1B post-money valuation, classified
> `MINORITY_INVESTMENT`.

| Field | Expected |
|---|---|
| `value.amount` | null |
| `value.type` | null, notes = `PRIMARY_CAPITAL` |
| round size capture | 200,000,000 |

Catches both failure modes: 1,000 recorded as equity value, and 200 recorded as equity value.

### Minority stake acquisition — the regression guard

> Investor acquires a 27% stake in the company for $600M from an existing holder.

| Field | Expected |
|---|---|
| `value.amount` | 600,000,000 |
| `value.type` | `EQUITY_VALUE` |

Ordinary M&A. If the precondition nulls this, it has over-reached. This is the one way the
change breaks something currently correct — verify explicitly.

### Funding-path regression

> A `VC_ROUND` or `GROWTH_EQUITY` deal.

Should be unaffected — these route to Stage 4b and never reach this prompt. Confirm the change
does not alter that path.

---

## Versioning

`high_confidence_extraction` needs a version bump, and `low_confidence_extraction` too if its
input template changes. Record both in `docs/prompt_versions.md` per
`prompts/prompt_conventions.md`.
