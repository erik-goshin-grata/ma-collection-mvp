# V3 Slices S-A – S-F — Implementation and Validation Reconciliation

**Date:** 2026-08-20 · **Reconciled against:** the working tree at the commit that adds this file.

One page covering what shipped, what was validated, what is known-broken, and what is
deliberately parked. Written after six vertical slices and five real-text validation runs.

---

## 1. Two gates, and why the distinction matters

**Gate 1 — deterministic structural regression.** Proves the field survives the production
path. For any new or changed extracted canonical field the chain is:

```
extraction/staging → production observation writer → observation ledger
                   → configured aggregation read source → canonical transaction_record
```

Four requirements, all of which exist because something failed without them:

1. **A demonstrated pre-fix failure.** The test must fail against the commit before the change.
2. **An unchanged neighbouring control** carried through the same path, so a change that
   empties or restructures the path is caught.
3. **Production field groups and production `include_*` flags** — never a list duplicated into
   the test. S-A added two fields to `_FIELDS` but not to `LC_SCALAR_FIELDS`; the canonical
   columns would have been NULL forever, and no prompt or parser test would have noticed.
4. **The configured aggregation read source** (`DEFAULT_AGGREGATION_READ_SOURCE = "observation"`),
   not a hand-built alternative.

**Derived fields are tested through their real architecture.** A field Stage 9 computes is
tested by driving Stage 9, not by manufacturing observations for a field nothing observes.

**Gate 2 — representative real-text boundary validation.** Product-supplied source text through
the shipped prompts and stages. It is *representative*, not exhaustive: one source per
deterministic assertion is not the standard, and a case earns its place by sitting on a boundary
where a specific, predictable failure was available. Provenance is recorded per case —
**independent** (appears in no prompt), **partial** (an excerpt appears), **smoke** (the source
is a worked example). Only independent cases are evidence.

**Implementation status ≠ Product status.** A slice can be structurally complete and still
unvalidated (S-E, S-F), or validated and still carrying an open defect (S-A).

---

## 2. Slice status

| Slice | Field(s) | Prompt | Owning stage | Observation group | Gate 1 | Gate 2 |
| --- | --- | --- | --- | --- | --- | --- |
| S-A | `deal_attitude`, `approach_type` | `low_confidence_extraction:0.8` | 7 | `LC_SCALAR_FIELDS` | ✅ | ⚠️ **PARTIAL / OPEN** |
| S-B | `combination_structure` | `deal_type_classifier:0.11` | 3 | `STAGE3_FIELDS` | ✅ | ✅ **8/8** |
| S-C | `target_type`, `asset_type` | `deal_type_classifier:0.11`, `high_confidence_extraction:0.20` | 3, 4 | `STAGE3_FIELDS`, `HC_FIELDS` | ✅ | ✅ **7/7** |
| S-D | `offer_mechanism` | `high_confidence_extraction:0.20` | 4 (11 corroborates) | `HC_FIELDS` | ✅ | ✅ **4/4** |
| S-E | `round`, `vc_stage`, `round_price_direction` | `funding_hc_extraction:0.2` | 4b, 9 derives | `FUNDING_FIELDS` | ✅ | ⏳ pending real text |
| S-F | consideration forms / `CONTINGENT_CONSIDERATION` | `low_confidence_extraction:0.8` | 7 | LC path | ✅ | ⏳ pending real text |

`STAGE3_FIELDS` is written when **Stage 4** runs with `include_stage3=True` — Stage 3 writes no
observations of its own. Omitting a classifier field from that group strands it on staging.

---

## 3. Gate 2 results

### S-B — combination structure — PASSED

8 independent Product-supplied sources. `event_type` 8/8 · `combination_structure` 8/8 ·
positive merger-of-equals 2/2 · **no** false-positive MoE 6/6 · reverse mergers 2/2 · De-SPAC 2/2.

Three cases could each have failed predictably and did not: a reverse merger the source calls a
"merger agreement"; a reverse merger where the **legal acquirer** is the economically acquired
party (Skye/Redx, a UK scheme); and two SPAC deals that resolved to the most specific value
`DE_SPAC` rather than stopping at `MERGER`.

**MoE evidence rule.** Explicit merger-of-equals characterisation, **or** sufficiently balanced
stated ownership, may establish it. Generic merge/combine language alone does not. **There is no
hard percentage threshold.** A negative expectation accepts `0` **or** `NULL` — the field is
populated only on explicit or qualified evidence, so absence is `NULL` by design, and demanding
`0` would pressure extraction into asserting a negative it never observed.

### S-C — target type and asset type — PASSED 7/7 after remediation

One defect found, fixed, and re-validated. On 0.10 the classifier returned `assets`/`OTHER` for
GMS / Evergreen from the phrase *"acquired the assets of"*, though the substance was an operating
business continuing under the buyer. The same wrong answer appeared on the lead sentence **and**
the full release, ruling out fixture truncation and locating the cause in the prompt.

`deal_type_classifier` 0.11 added one principle — transaction form alone does not determine
`target_type`. On the rerun **one case moved and six held**: `target_type` 7/7, `asset_type` 7/7,
and genuine `assets` recall **3/3** across intellectual property, real estate and natural
resources. That asymmetry was the point: a guardrail that merely biased away from `assets` would
have shown up as the genuine asset cases drifting.

`asset_type` needed no separate fix. It is subordinate to `target_type = assets` and returned to
NULL on its own once the target type was right.

### S-D — offer mechanism — PASSED 4/4

`TENDER_OFFER` positive 2/2 · false-positive 0/2 · mechanism-and-structure coexistence 1/1.
A UK scheme of arrangement described as a *"recommended cash **offer**"* correctly returned NULL —
offer wording did not produce the mechanism. A two-step deal recorded `TENDER_OFFER` **and**
`combination_structure = MERGER` on one transaction, which V2's single-valued `merger_structure`
could not represent.

### S-A — attitude and approach — PARTIAL / OPEN

Earlier independent validation of `FRIENDLY` and `UNSOLICITED` stands. The open defect:

| Field | Expected | `0.7` | `0.8` |
| --- | --- | --- | --- |
| `deal_attitude` | `HOSTILE` | `HOSTILE` | `HOSTILE` |
| `competing_bid` | `0` | `0` | `0` |
| `approach_type` | **`NULL`** | `UNSOLICITED` | `UNSOLICITED` |

A mandatory/regulatory offer does not by itself establish `UNSOLICITED`. `approach_type` describes
the **origin of the approach**; a statutory obligation to make an offer after crossing an ownership
threshold says nothing about whether the approach was solicited. The tested source contains the
word "unsolicited" zero times.

**The 0.8 clarification is semantically correct and did not change the behaviour.** It stated the
rule explicitly, and the rerun returned `UNSOLICITED` anyway — on a prompt that already said
*"null otherwise"*, *"null is a first-class outcome"* and *"do not infer either value from the
absence of the other"*. **Restating a rule the prompt already carries is not a remediation.** That
is the transferable lesson; the S-C fix worked because it named a *cue* the prompt had never
addressed, not because it repeated an existing rule more firmly.

**The `approach_type` evidence rule.** `UNSOLICITED` requires the source to state or establish an
unsolicited bid; `SOLICITED` requires it to state or establish a solicited process — a sale
process, auction, strategic review, outreach, or an invitation to bid. Neither value is inferred
from the absence of the other, and **null is a first-class outcome that is expected to be the most
common one.** `SOLICITED` remains **unexercised** in validation — no source establishing a
target-initiated process has been supplied.

---

## 4. The HOSTILE rule — Product, preserved exactly

> Target rejection or opposition **plus** bidder persistence, maintenance, return, or a new or
> improved unsolicited bid; **or** an explicit hostile characterisation.
> **A single rejection does not by itself establish HOSTILE.**

**Mandatory-offer caveat.** In a statutorily compelled offer, the offer's continued availability
may be **legally required rather than evidence of bidder persistence**, so the second limb cannot
be read off the offer remaining open.

The Kontron source returned `HOSTILE` on both prompt versions and appears in no prompt. That is
recorded as a **boundary observation, not proof of the general rule** — it must not be read as
establishing that HOSTILE reduces to "the board recommends rejection."

---

## 5. Known prompt/model coupling risk — `approach_type`

Recorded as a risk, **not** an instruction to add examples now:

- `deal_attitude` and `approach_type` are **independent** dimensions.
- A mandatory/regulatory offer does not establish approach origin.
- `low_confidence_extraction` 0.8 says so explicitly and did not change the tested behaviour.
- **All three worked examples that populate `approach_type` return `UNSOLICITED`.**
- **There is no `SOLICITED` worked example.**
- **Example 9 is the only worked case where `deal_attitude` and `approach_type` appear together,
  and there they co-vary** — a board rejecting a bid, paired with `UNSOLICITED`. That is the
  tested source's exact surface shape, and the prose asserting independence is not backed by any
  worked case showing the two apart.

Researcher review remains available for ambiguous or misclassified secondary fields.

---

## 6. Accumulated implementation corrections

- **S-B.** `MERGER` and `REVERSE_MERGER` are removed as `v2_event_type` values and are invalid
  new output; both are `combination_structure`, hierarchical `DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`,
  valid only when `v2_event_type = ACQUISITION`. Store the most specific value and **query by
  implication, never equality**. Stored legacy rows keep both values — read tolerance in
  `aggregate.py` is deliberate.
- **MoE evidence rule.** As in §3 above. No percentage threshold.
- **S-C.** Transaction form alone does not determine `target_type`; `assets` not chosen *solely*
  from asset-purchase wording.
- **Retained column ≠ continued authorship.** `is_de_spac`, `is_divestiture`, `hostile`,
  `includes_earnout`, `is_down_round` and `round_stage_category` keep their columns and their
  stored history, and simply stop being written. A retained column is not a claim that Stage 9
  still owns the field.
- **S-E rationale, corrected.** An earlier commit message said leaving fields in
  `_STAGE9_OWNED_COLUMNS` would "write NULL over stored values on every re-aggregation." That was
  **wrong**: `insert_observation` is idempotent and never deletes, so legacy observations persist.
  The decision was unaffected; the reasoning was.
- **S-F.** `CONTINGENT_CONSIDERATION` added for consideration the source states is contingent,
  deferred or milestone-based where neither `EARNOUT` nor `CVR` is established. **Most-specific
  supported form**, in both directions: do not fall back to the generic form when the source
  supports a subtype, and do not promote a vague additional payment to `EARNOUT` because earnouts
  are common. Contingent components are **additive**; they never replace base consideration.
  `includes_earnout` is retired entirely.
- **`is_stock_for_stock` is excluded from V3** unless a future Product requirement emerges.

---

## 7. `is_take_private` — known executable defect, next bounded remediation

**`is_take_private` is structurally 0 for every new transaction.**

Stage 3 validates lowercase `target_type`, and Stage 4's `_normalize_acquirer_type()` converts
legacy uppercase down to the V2 lowercase vocabulary — so storage is lowercase.
`stages/aggregate.py` compares uppercase:

```python
if fields.get("target_type") != "STANDALONE_COMPANY":                       # always true
_PRIVATE_TAKE_PRIVATE_ACQUIRER_TYPES = frozenset({"PRIVATE_EQUITY", ...})   # 12 uppercase
```

`scripts/test_take_private_derivation.py` passes because it calls `_derive_flags()` directly with
**synthetic uppercase** values, bypassing storage. **The test certifies the defect.** This is the
clearest available case for the Gate 1 rule about production paths versus manufactured inputs.

This is a live V3-derived concept with no supersession: casing is the whole defect, and repairing
the comparison repairs the field. **Scheduled as the next bounded remediation after this
documentation checkpoint lands.**

**Requirement for its regression:** it must exercise production-normalized **lowercase** values
through the real path — not call `_derive_flags()` with synthetic uppercase, which is precisely
how the existing test came to certify a broken field.

### `is_add_on` is not part of that fix

`is_add_on` shows the same uppercase comparison — `int(acquirer_type == "PE_PORTFOLIO")`, which is
also always 0 — but it is **a superseded field awaiting retirement of authorship, not an executable
defect awaiting a casing fix.**

- **§T7** supersedes the `is_platform_investment` / `is_add_on` pair with
  `sponsor_transaction_role` (`PLATFORM` / `ADD_ON` / null), and records that *"the V2 derivation
  `is_add_on := acquirer_type == PE_PORTFOLIO` is not carried forward."*
- **§T8** removes `PE_PORTFOLIO` from the acquirer vocabulary entirely — a portfolio company
  retains its underlying entity type, so `PE_PORTFOLIO` is not an entity type at all.

**Do not repair the casing here.** Doing so would revive a derivation V3 intentionally retires,
using an input V3 removes. The historical column is **kept for now** under the standing
retained-column rule, and **new authorship stops as part of implementing
`sponsor_transaction_role`** — not as a casing repair, and not before that work.

---

## 8. Deliberate legacy compatibility — do not "clean up"

- `_MA_EVENT_TYPES` in `aggregate.py` includes `MERGER`/`REVERSE_MERGER` — read tolerance for
  stored rows.
- Relevancy **reason codes** (`MERGER_ANNOUNCEMENT`, `REVERSE_MERGER`, `MINORITY_INVESTMENT`) are
  a **separate vocabulary** from event types. They remain valid; `overrides_relevancy_hint` exists
  because a hint may disagree with the classification.
- Retained schema columns for retired fields, with their `schema/*.sql` notes.
- Negative assertions naming retired values (`spinco`, `includes_earnout`) in tests and stage
  comments — these prove the value is rejected.

**Later cleanup, not reconciliation:** `stages/export.py` and `scripts/export_review_xlsx.py`
still emit retired columns; `stages/summarize.py` still logs "Stage 10" though `run.py` runs it as
Stage 12.

---

## 9. Later / parked

- LOI date distinct from definitive signing date.
- Contingent consideration interaction with Transaction Value / Enterprise Value.
- **SEC architecture review.** The current architecture remains authoritative. A later review may
  consider decomposing a filing package into separately provenance-bearing Item 1.01 / 99.1 / 2.1 /
  10.1 components; a related question is canonical V3 observations versus `sec_*` enrichment.
  **No SEC architecture change is in scope for reconciliation.**
- S-A `SOLICITED` real-text validation.
- S-E representative funding Gate 2.
- S-F explicit CVR and generic contingent Gate 2.
