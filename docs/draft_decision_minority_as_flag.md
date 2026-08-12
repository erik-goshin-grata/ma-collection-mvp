# Draft decision entry — minority as a flag, not a type

**Status: DRAFT for Erik's review.** One entry intended for `docs/decisions.md`, plus an amendment
to an existing entry. ChatGPT authors the decision language; Codex lands it after approval.

---

## 2026-08-12 - Minority Is a Flag, Not an Event Type

Status: **proposed — decision required.** Structural; requires re-extraction, not migration.

Decision:

- **Remove `MINORITY_INVESTMENT` from the event-type vocabulary.** Partiality becomes
  `is_minority`, a derived flag.
- The event type carries **what happened**; the flag carries **how much**. A negotiated stake
  purchase is `ACQUISITION` + `is_minority`. A growth round is `GROWTH_EQUITY` + `is_minority`. A
  PIPE is a funding type + `is_minority`.
- `is_minority` is **derived in aggregation**, following the take-private precedent — not
  extracted, and no extractor decides it. Inputs: resolved `pct_acquired` where available, and the
  partiality signal the classifier already produces where it is not.
- **No `capital_flow` or `instrument_class` field.** That rejection stands. The primary/secondary
  question is asked of a source, not stored as a dimension, and it remains a prompt instruction so
  it survives enum changes.

### Why the type is wrong

- **Every other event type routes to exactly one value path. `MINORITY_INVESTMENT` routes to
  both.** `ACQUISITION` is M&A, `VC_ROUND` is funding, `RECAPITALIZATION` is recap. Minority does
  not determine the path, because it is not describing an event — it is describing a degree.
  Degrees are flags.
- **The precedent is already set.** "Take-Private Derived Flag Rule" kept take-private a derived
  flag rather than a top-level type for the same reason. This applies the established pattern to
  the one remaining case that breaks it.
- **The ambiguity is manufactured by the type, not inherent to the deals.** As a single bucket,
  `MINORITY_INVESTMENT` spans primary and secondary capital — a growth round and a stake bought
  from a selling shareholder. With minority as a flag there is no bucket for the two to be
  conflated inside, and the value path stops depending on a distinction the enum cannot express.

### Resolution rule — when the source does not say

Removing the type forces a commitment the source may not support. "Acme acquires a 20% stake in
Beta" does not say whether Beta issued new shares or a holder sold theirs, and press releases
frequently do not. The rule below resolves it from `target_status`, which the classifier already
produces — **no new extraction primitive**, the same virtue as the `pct_acquired ≥ 50` threshold.

| `target_status` | Silent source resolves to | Reasoning |
|---|---|---|
| `PUBLIC` | M&A path (secondary) | Shares bought from holders. Primary capital into a public company is a PIPE, which self-identifies. |
| `SUBSIDIARY_OF_PUBLIC` | M&A path (secondary) | Bought from the parent. Secondary by construction, regardless of the parent's listing. |
| `SUBSIDIARY_OF_PRIVATE` | M&A path (secondary) | As above. |
| `PRIVATE` (standalone) | Funding path (`GROWTH_EQUITY`) | A private company taking outside capital is predominantly raising it. A selling shareholder is normally named. |

**Silence is the trigger, not the absence of a keyword.** PIPEs, private placements and registered
directs identify themselves; where such language is present the rule does not fire.

**The private default errs conservatively, and that is the point.** The two failure modes are not
symmetric:

- Wrongly on the **funding path** — no implied equity, no multiples. Costs *coverage*.
- Wrongly on the **M&A path** — implied equity grossed up from capital that went *into* the
  company. *Manufactures a valuation.*

Defaulting toward the error that under-reports rather than the one that invents is deliberate. It
is the same principle as "do not assume debt = 0" and as nulling `deal_value_currency` on
conflict: refuse to guess in the direction that fabricates.

**The public side of the rule carries the higher manufacturing risk and is also the side where the
error is catchable** — implied equity on a public target can be sanity-checked against market
capitalisation. No such check exists for the private case, which is a second reason the private
default points away from implied values.

### Consequences

- **This cannot be migrated — but re-extraction is cheap here.** Existing `MINORITY_INVESTMENT`
  rows must become `ACQUISITION + is_minority` or a funding type `+ is_minority`, and that split is
  the primary/secondary distinction the stored fields do not preserve — earlier sampling found
  roughly a fifth clearly secondary, half primary, and about 30% undeterminable from stored data.
  No backfill query resolves it.

  **That would be expensive against production data. It is not, here.** Every DB in this repo is a
  validation fixture with no downstream consumer — nothing is served, and nothing depends on a
  stable `transaction_id`. So the cost is LLM calls over a corpus, not a data migration with
  consumers to coordinate. Re-extract rather than devising a migration path; a migration would cost
  more to design than the re-run costs to execute.
- **Decision #9 becomes load-bearing.** Growth rounds currently landing in `MINORITY_INVESTMENT`
  must go somewhere once the type is removed, and `GROWTH_EQUITY` is where. The VC/Growth boundary
  was deferrable while it moved nothing; it is not once it absorbs that population. **Decide the
  two together.**
- **Misclassification costs coverage, not magnitude.** "Value Path Keyed on Where the Money Goes"
  records that `transaction_size` is stable across a misclassification between the two paths —
  both give the same figure. That property was designed in and it holds here, which is what makes
  the resolution rule tolerable rather than reckless.
- **Implied equity already depends on this routing.** The 2026-08-12 implied-equity fix derives
  from `equity_value`, and funding rounds vacate `equity_value` — so the value path decides whether
  a deal produces an implied value at all, and therefore whether any multiple can be struck. The
  classifier's precision on this boundary is now inherited by every multiple, the same way the
  `pct_acquired` default already inherits it.
- Grata's `EventType` carries the same `MINORITY_INVESTMENT` member, and `CBI_EVENT_TYPE_MAP`
  routes both `pipe` and `corporate_round` into it. This is therefore also a phase-3 entry — and a
  structural one rather than an enum addition, so it belongs in its own section with the
  take-private precedent doing the arguing.

### Sequencing

Build after phase 2. The fresh-deal corpus exercises the new classification on deals with no legacy
rows to reconcile — the cheapest place to find out whether the flag resolves the ambiguity or
merely relocates it.

**Corpus requirement, stated precisely.** A minority stake in a **public** target where the source
states **both the percentage and a price qualified as equity consideration** — "acquired 15% for
$200MM", not "$200MM" alone. Both are needed for the path to fire: `equity_value` supplies the
numerator and `pct_acquired` the gross-up, and an unqualified figure routes to `transaction_value`
where it correctly produces no implied value at all. A source stating only the price tests nothing
here.

---

## Amendment — "Value Path Keyed on Where the Money Goes" (2026-08-10)

That entry states that no `capital_flow` or `instrument_class` field is introduced and that **the
value path continues to key on `deal_type`.** The first half stands. The second stops describing
what happens once `MINORITY_INVESTMENT` is removed, because the path will then key on the event
type *plus* the resolution rule above where the source is silent.

Amend the entry to say so rather than leaving the record contradicting the code. A boolean flag is
not the taxonomy field that was rejected, so the two are compatible — but only if the entry says
which.

**Also worth recording there:** that same entry named `is_minority`, `pre_existing_control` and
`acquires_remaining` as the M&A features, and the 2026-08-10 handoff then parked all three as *no
longer needed by the value model since TV uses `pct_acquired`*. That justification lapsed on
2026-08-12, when the implied-equity fix made path routing load-bearing. The deferral was correct
when written and is not now.

**Scope the lapse correctly — it is narrower than it first appears.** Where `pct_acquired` is
unstated, the **as-transacted tier is still covered**: an as-reported figure populates
`transaction_value`, and `transaction_size` follows from it, with no percentage required. What is
not covered is the **implied tier**, which needs `pct_acquired` to gross up and produces nothing
without it. And the routing question is separate from both — the event type decides whether a deal
takes the implied path *at all*, independently of whether a percentage was stated. So the flags
matter for routing and for the implied tier, not for deal magnitude, which stands on its own.
