# Agreement Recitals Extraction Prompt

**Version:** 0.7 (provenance is caller-owned)
**Repo path:** `prompts/agreement_recitals.md`

---

## 1. Purpose

Extract structured party information from the RECITALS or preamble section of a deal document. Identifies every named party the text casts in one of a fixed set of transaction roles (buyer, target, seller, parent acquirer/seller, sponsor, merger sub) and determines the merger structure. Party identification is anchored to the agreement's own defined roles and transaction mechanics — never inferred from entity names or corporate relationships not stated in the text.

Runs in Stage 11 (agreement_extract) for each RECITALS section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 1024

---

## 3. Input Schema

```json
{
  "section_text": "..."
}
```

---

## 4. System Prompt

```
You are extracting structured party information from the recitals or preamble section of a merger agreement, proxy statement, tender offer document, or purchase agreement.

OUTPUT IS A LIST OF PARTIES

Return a "parties" array. Each entry is {"role": <ROLE>, "name": <legal name as the agreement states it>}. There is no cap on how many entries you return, including zero for a role, more than one for a role, or the same name under two different roles. Only these eight role values exist — do not invent, rename, or add any other role:

BUYER · TARGET · SELLER · PARENT_ACQUIRER · PARENT_SELLER · SPONSOR_BUYER · SPONSOR_SELLER · MERGER_SUB

PARTY IDENTIFICATION — ANCHOR TO THE AGREEMENT'S OWN DEFINED ROLES AND MECHANICS

Identify parties from the defined terms and transaction-mechanics language the agreement itself uses — "Parent," "Buyer," "Purchaser," "Company," "Target," "Seller," "Merger Sub," etc. Then assign each to whichever role its own mechanics establish, not merely the label attached to it:

- BUYER = whichever defined party the transaction mechanics establish as acquiring the target — the party the target merges into/with, or that is purchasing the target/its assets/interests. This is true whether the agreement calls that party "Parent," "Acquirer," "Buyer," or "Purchaser." Do not invent an interpretive concept ("the real buyer") beyond this: find the party the text's own mechanics cast in the acquiring role, and use the agreement's own name for it. A party that joins the agreement only in a limited capacity — e.g., "solely for the purpose of Section X" — is not BUYER merely because of a "Parent"-shaped label; if the mechanics establish a different party (often called "Purchaser" or "Buyer") as the one actually acquiring the target, that party is BUYER, and the limited-purpose party is omitted (or, if you judge it worth preserving, described in notes only — never assigned a role on label alone).
- TARGET = the company, business, or assets being acquired — the party whose equity, assets, or interests are changing hands. When the agreement calls a party "Seller," decide in this order:
  1. If Seller's own business or assets are what's being acquired, and the recitals do not identify a separate, distinctly-named legal entity being divested, TARGET is that Seller (e.g., an owner selling substantially all of its own business's assets) — and that same entity is also SELLER (see below). One entity can hold two roles.
  2. If Seller is instead disposing of a separately identifiable company, subsidiary, or set of interests — i.e., Seller itself is not what's being acquired, it's the one doing the divesting — TARGET is that separately identified entity, if the recitals name it. If the recitals don't name it, omit TARGET entirely. Do not substitute Seller's own name in this case.
- SELLER = a party the recitals explicitly designate with that defined term (typically `("Seller")` in the preamble). Only from an explicit defined-term tag — never inferred from a shared name, a subsidiary relationship you reason out yourself, or the general shape of the deal.
- PARENT_ACQUIRER = a distinct corporate parent standing above BUYER, when — and only when — the supplied text explicitly establishes that relationship *to BUYER specifically* (e.g., "[Entity], the parent company of Buyer," "Buyer, a wholly-owned subsidiary of [Entity]," "[Entity] hereby guarantees the obligations of Buyer"). None of the following, alone or combined, is sufficient evidence — omit the role rather than infer a relationship from them: (a) a defined-term label that itself contains the word "Parent" (e.g., "Purchaser Parent," "Parent Guarantor") — the label is a name the agreement assigns that party, not a stated fact about its relationship to Buyer; (b) a shared brand name or similar-looking name between two parties; (c) general name similarity or real-world knowledge of a corporate family; (d) a party joining "solely for the purpose of Section X" with no further stated relationship. If the text names a limited-purpose party but never explicitly states what it is the parent/guarantor of, omit it entirely rather than assign it a role.
- PARENT_SELLER = the same evidence bar as PARENT_ACQUIRER, mirrored on the sell side (a distinct corporate parent standing above SELLER, only with explicit hierarchy/guaranty language).
- SPONSOR_BUYER / SPONSOR_SELLER = a financial sponsor, only when that sponsor entity is itself a named party to *this* agreement (appears in the preamble's own party list). A sponsor referenced elsewhere in the document (e.g., in a termination or forfeiture provision) for a *separate* ancillary agreement it is not shown to be a signatory of here does not qualify — mention is not party.
- MERGER_SUB = an acquisition-vehicle shell. See MERGER SUB below for how to identify one and how multiplicity works.

MERGER SUB

Language that identifies a Merger Sub:
- "wholly-owned subsidiary of [Parent]"
- "newly formed for the purpose of the [Merger/Acquisition]"
- "formed solely to effectuate the transactions contemplated"
- Names like "[X] Acquisition Corp.", "[X] Merger Sub, Inc.", "Project [Codename] Merger Sub"

If the recitals name more than one merger sub (a two-step structure — see MERGER STRUCTURE DETERMINATION below), return one MERGER_SUB entry per merger sub, each with its own name. Never join multiple entity names into a single entry with a delimiter of any kind — two merger subs are two entries in the array, in the order the agreement uses them (the one the target merges into first, listed first).

Two-party structure (direct merger, no Merger Sub):
- BUYER and TARGET only, no MERGER_SUB entry
- merger_structure = DIRECT

NOT A MERGER

If the recitals describe an asset purchase, an equity/interest purchase, or a contribution/investment/joint-venture formation rather than a merger, return null for merger_structure and omit any MERGER_SUB entry — do not force one of the merger-structure values, or a merger sub, onto a transaction that isn't a merger.

Two different cases follow from there, and they are not the same:

- **An asset or equity/interest purchase still has a buy-side and a sell-side.** BUYER/TARGET/SELLER (and PARENT_ACQUIRER/PARENT_SELLER/SPONSOR_BUYER/SPONSOR_SELLER where evidenced) apply normally — one party is buying, another is selling or being acquired, even without a merger.
- **A capital contribution, investment, or joint-venture formation does not have a buy-side/sell-side relationship at all**, even though the agreement names its own parties and roles (e.g., "Investor," "Contributor," a "Company"/JV entity that parties are contributing into). Do not relabel those parties as BUYER, SELLER, or TARGET merely because those are the role values this schema offers — an Investor contributing capital for units in a JV is not a BUYER acquiring a TARGET, and forcing that label on is fabricating a relationship the text doesn't state. If the transaction's own mechanics do not explicitly establish one of the eight roles as actually applying, return `"parties": []` — an empty array is the correct, complete answer, not a fallback.

The same rule applies to any internal reorganization step described in the recitals (e.g., one entity merging into another as a preparatory step before the real transaction): a party's role in that internal step is not itself grounds for assigning it BUYER/SELLER/TARGET in the overall transaction unless the mechanics establish that independently.

MERGER STRUCTURE DETERMINATION

merger_structure is a separate top-level field, not part of the parties array. Identify from the recitals language:
- DIRECT: "Target shall merge with and into Acquirer" (no Merger Sub; Acquirer survives)
- FORWARD_TRIANGULAR: "Target shall merge with and into Merger Sub" (Merger Sub survives, Target disappears)
- REVERSE_TRIANGULAR: "Merger Sub shall merge with and into Target" (Target survives — most common in modern public M&A)
- TWO_STEP_REVERSE_TRIANGULAR: a reverse triangular merger (Merger Sub merges into Target, Target survives) immediately followed by a second, forward merger of the surviving Target into a second merger sub (often an LLC), which survives. Use this instead of REVERSE_TRIANGULAR when both steps are described.
- TENDER_OFFER: tender offer mechanics described; often combined with a subsequent second-step merger

If the merger mechanism cannot be determined from this section, return null for merger_structure. Do not return "UNKNOWN" — null means no observation, "UNKNOWN" is not a valid value.

EVIDENCE RULES

- Mention is not the same as party. Only add a parties[] entry if the text casts that entity in one of the eight roles for this agreement — not because it's mentioned in passing (a financing source, a confidentiality-agreement counterparty, an affiliated fund, or a party to a separate ancillary document).
- A defined term is not automatically one of the eight roles. A party can be defined in the preamble (e.g., a Securityholder Representative) without fitting any of BUYER/TARGET/SELLER/PARENT_ACQUIRER/PARENT_SELLER/SPONSOR_BUYER/SPONSOR_SELLER/MERGER_SUB — omit it rather than force it into the nearest-sounding role.
- Do not infer a role from what an entity's name sounds like, from a shared brand name between two parties, or from real-world knowledge of a corporate family. A name like "[X] Acquisition Corp." is a hint toward MERGER_SUB, not proof — confirm from the actual merger-mechanics or hierarchy language stated in the text. PARENT_ACQUIRER and PARENT_SELLER in particular require explicit hierarchy/guaranty language, never a naming resemblance.
- No party role is assigned without textual support. Never fabricate a name, and never return "UNKNOWN" for merger_structure.
- Express genuine uncertainty through model_confidence and notes, not by guessing at a role.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "parties": [
    {"role": "BUYER", "name": "Acme Corporation"},
    {"role": "MERGER_SUB", "name": "Project Alpha Merger Sub, Inc."},
    {"role": "TARGET", "name": "Beta Industries, Inc."}
  ],
  "merger_structure": "REVERSE_TRIANGULAR",
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. "parties" may be an empty array. Use null for merger_structure and notes when they have no value.
```

---

## 5. User Prompt Template

```
Extract party and structure information from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `parties` | array of `{role, name}` | Zero or more entries. `role` is one of `BUYER \| TARGET \| SELLER \| PARENT_ACQUIRER \| PARENT_SELLER \| SPONSOR_BUYER \| SPONSOR_SELLER \| MERGER_SUB` — no other value. Multiple entries per role are valid (e.g. two `MERGER_SUB` entries in a two-step structure); the same `name` may appear under two roles (e.g. Seller whose own business is the Target). Stage 11 writes each entry as its own `party.<ROLE>` observation — see `stages/agreement_extract.py`'s `_write_observations`. This is Stage 11's complete responsibility for these facts: no entity_id or `transaction_participant` row is assigned here — entity resolution is a separate, downstream concern. |
| `merger_structure` | enum\|null | DIRECT \| FORWARD_TRIANGULAR \| REVERSE_TRIANGULAR \| TWO_STEP_REVERSE_TRIANGULAR \| TENDER_OFFER \| null (not determinable, or not a merger) |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Ambiguities, caveats (≤200 chars) |

---

## 7. Few-Shot Examples

### Example 1 — Reverse triangular (most common)

**Input section text:**
```
AGREEMENT AND PLAN OF MERGER

dated as of April 10, 2026

among

GLOBALTECH CORPORATION, a Delaware corporation ("Parent"),

GLOBALTECH ACQUISITION CORP., a Delaware corporation and a wholly-owned subsidiary of Parent ("Merger Sub"),

and

DELTA SYSTEMS, INC., a Delaware corporation (the "Company").

WHEREAS, the parties hereto desire to effect a business combination through a merger of Merger Sub with and into the Company upon the terms and subject to the conditions of this Agreement (the "Merger"), with the Company surviving the Merger as a wholly-owned subsidiary of Parent;
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "GlobalTech Corporation"},
    {"role": "MERGER_SUB", "name": "GlobalTech Acquisition Corp."},
    {"role": "TARGET", "name": "Delta Systems, Inc."}
  ],
  "merger_structure": "REVERSE_TRIANGULAR",
  "model_confidence": "HIGH",
  "notes": null
}
```

### Example 2 — Tender offer structure

**Input section text:**
```
THIS AGREEMENT AND PLAN OF MERGER (this "Agreement") is entered into as of March 1, 2026, by and among:

NORTHSTAR CAPITAL PARTNERS LP ("Parent"), a Delaware limited partnership,

NSC ACQUISITION INC., a Delaware corporation and a wholly-owned subsidiary of Parent ("Purchaser"), and

REDWOOD FINANCIAL GROUP, INC. (the "Company"), a Delaware corporation.

WHEREAS, upon the terms and conditions of this Agreement, Purchaser will commence a tender offer (the "Offer") to purchase all of the outstanding shares of Company Common Stock at a price of $34.00 per share in cash, followed by a Merger in which Purchaser shall merge with and into the Company.
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "NorthStar Capital Partners LP"},
    {"role": "MERGER_SUB", "name": "NSC Acquisition Inc."},
    {"role": "TARGET", "name": "Redwood Financial Group, Inc."}
  ],
  "merger_structure": "TENDER_OFFER",
  "model_confidence": "HIGH",
  "notes": "Tender offer followed by back-end merger; Purchaser merges into Company (reverse triangular back-end)."
}
```

### Example 3 — Two-step reverse triangular merger (Merger Sub merges into Target, Target then merges into a second Merger Sub)

**Input section text:**
```
Merger Sub I, Inc., a Delaware corporation and a wholly-owned subsidiary of Meridian Holdings, Inc. ("Parent"), shall merge with and into Crestwood Corp., a Delaware corporation (the "Company"), with the Company surviving as a wholly-owned subsidiary of Parent (the "First Merger"). Immediately thereafter, the Company shall merge with and into Merger Sub II, LLC, a Delaware limited liability company and a wholly-owned subsidiary of Parent, with Merger Sub II, LLC surviving (the "Second Merger" and, together with the First Merger, the "Mergers").
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "Meridian Holdings, Inc."},
    {"role": "MERGER_SUB", "name": "Merger Sub I, Inc."},
    {"role": "MERGER_SUB", "name": "Merger Sub II, LLC"},
    {"role": "TARGET", "name": "Crestwood Corp."}
  ],
  "merger_structure": "TWO_STEP_REVERSE_TRIANGULAR",
  "model_confidence": "HIGH",
  "notes": "Two-step structure: reverse triangular First Merger into Merger Sub I (Company survives), then a forward Second Merger of the Company into Merger Sub II, LLC (LLC survives)."
}
```

### Example 4 — Direct merger into the publicly-listed acquirer (no Merger Sub; includes SPAC-style combinations)

**Input section text:**
```
Upon the terms and subject to the conditions set forth in this Agreement, at the Merger Effective Time, Sable Holdings Ltd. ("Company") shall merge with and into Vantage Acquisition Corp III, a Cayman Islands exempted company ("Vantage"), with Vantage surviving the merger and continuing as a publicly traded company. No merger subsidiary is used in this transaction.
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "Vantage Acquisition Corp III"},
    {"role": "TARGET", "name": "Sable Holdings Ltd."}
  ],
  "merger_structure": "DIRECT",
  "model_confidence": "HIGH",
  "notes": "Company merges directly into the acquirer; no Merger Sub used."
}
```

### Example 5 — Not a merger: asset/equity purchase

**Input section text:**
```
This Asset Purchase Agreement (this "Agreement") is entered into by and between Solstice Partners LLC, a Delaware limited liability company ("Seller"), and Rangeview Capital LLC, a Delaware limited liability company ("Buyer"), pursuant to which Seller wishes to sell, and Buyer wishes to purchase, substantially all of the assets of Seller's business.
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "Rangeview Capital LLC"},
    {"role": "SELLER", "name": "Solstice Partners LLC"},
    {"role": "TARGET", "name": "Solstice Partners LLC"}
  ],
  "merger_structure": null,
  "model_confidence": "HIGH",
  "notes": "Asset purchase, not a merger; merger_structure and MERGER_SUB do not apply. Seller's own business is what's being acquired, so the same entity is both SELLER and TARGET."
}
```

### Example 6 — Limited-purpose joining party does not displace the actual acquiring party; Seller divesting an unnamed interest leaves TARGET omitted

**Input section text:**
```
EQUITY PURCHASE AGREEMENT, dated as of May 3, 2026, by and among Rangeview Capital LLC, a Delaware limited liability company ("Purchaser"), solely for the purpose of Section 7.9, Rangeview Holdings, Inc., a Delaware corporation ("Purchaser Parent"), and Solstice Partners LLC, a Delaware limited liability company (the "Seller"), pursuant to which Purchaser shall acquire the Purchased Interests (as defined below) from Seller.
```

**Expected output:**
```json
{
  "parties": [
    {"role": "BUYER", "name": "Rangeview Capital LLC"},
    {"role": "SELLER", "name": "Solstice Partners LLC"}
  ],
  "merger_structure": null,
  "model_confidence": "HIGH",
  "notes": "Equity purchase, not a merger. Rangeview Holdings, Inc. ('Purchaser Parent') joins solely for the purpose of Section 7.9 and is not the acquiring party; Rangeview Capital LLC ('Purchaser') is. Seller is divesting the 'Purchased Interests,' a separately defined interest, not itself — the specific entity/interest isn't named in this excerpt, so TARGET is omitted rather than substituting Seller."
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section contains only boilerplate (page header, no party recitals) | Return `parties: []` and `merger_structure: null`; `model_confidence: NONE` |
| Merger Sub and Parent names are similar (easy to confuse) | Look for "wholly-owned subsidiary of" language to identify the Sub |
| Multiple Merger Subs named (step-merger structures) | Runtime rule in § 4 (MERGER SUB): one `MERGER_SUB` entry per merger sub, in the order the target merges into them; never concatenate into one entry. |
| Transaction is not a merger but still has a buy-side/sell-side (asset purchase, equity/interest purchase) | `merger_structure` null, no `MERGER_SUB` entry; `BUYER`/`TARGET`/`SELLER` (and `PARENT_ACQUIRER`/`PARENT_SELLER`/`SPONSOR_BUYER`/`SPONSOR_SELLER` where evidenced) still populated from party mechanics (§ 4, NOT A MERGER) |
| Transaction is a capital contribution, investment, or JV formation with no buy-side/sell-side relationship at all (e.g., an Investor/Contributor structuring a joint venture) | Do not relabel Investor/Contributor/JV-entity parties as `BUYER`/`SELLER`/`TARGET` merely because those are the available roles. Return `"parties": []` when none of the eight roles is explicitly established by the actual transaction mechanics (§ 4, NOT A MERGER). An internal reorganization step's own party roles do not, by themselves, establish a role in the overall transaction. |
| A "Parent"/"Purchaser Parent" joins only for a limited purpose (e.g., "solely for the purpose of Section X") | Do not assign it `BUYER` or `PARENT_ACQUIRER` on that basis alone — a defined-term label containing "Parent," a shared brand name, or the limited-purpose joinder itself is not evidence of the relationship; identify the party the mechanics establish as actually acquiring the target, and omit the limited-purpose party's role rather than guess one. |
| Seller is disposing of a separately identifiable entity/interest that this excerpt doesn't name | Omit `TARGET` rather than substitute Seller's own name (§ 4, PARTY IDENTIFICATION, TARGET rule 2) |
| A shared brand name suggests a corporate-family relationship (e.g., a parent company sharing a name with Buyer) but the text states no explicit hierarchy | Do not assign `PARENT_ACQUIRER`/`PARENT_SELLER` on name resemblance alone — omit the role; explicit hierarchy or guaranty language is required |
| A sponsor entity is referenced elsewhere in the document (e.g., a forfeiture provision) but is not a named party in this preamble | Do not assign `SPONSOR_BUYER`/`SPONSOR_SELLER` — mention is not party |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — party identification + merger structure |
| 0.2 | 2026-05-05 | Remove UNKNOWN from merger_structure; null = not determinable |
| 0.3 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
| 0.4 | 2026-09-01 | **Data-model alignment (15-doc regression corpus, `logs/agreement_baseline_20260901/recitals_v04_alignment_review.md`).** (1) Added `TWO_STEP_REVERSE_TRIANGULAR` to `merger_structure` — a reverse triangular merger immediately followed by a forward merger of the surviving company into a second merger sub (usually an LLC); validated against two of five real merger documents in the regression corpus that were previously mis-collapsed into plain `REVERSE_TRIANGULAR`. (2) Moved the "use the first/primary Merger Sub, never concatenate" rule from § 8 (Failure Modes, documentation only — never sent to the model by `prompts/base.py`) into § 4's runtime system prompt; this is a bug fix, not a new rule — the rule already existed but the model was never actually told it, which is why two documents (BitGo, Victory Capital) produced a semicolon-joined `merger_sub_name`. (3) Party identification reframed around the agreement's own defined roles plus transaction mechanics, not a label read literally — fixes a validated defect where a limited-purpose joining party ("Purchaser Parent," joined "solely for the purpose of Section X") was selected over the actual operative Purchaser. (4) Added an explicit NOT A MERGER rule (asset/equity/interest purchase, contribution, or investment → `merger_structure`/`merger_sub_name` null; party fields still populated) — codifies behavior the prompt already produced correctly, now stated as a rule rather than left implicit. (5) Added explicit EVIDENCE RULES (mention ≠ party, defined term ≠ principal role, no name-based inference, no fabrication, no `UNKNOWN`) — all previously-correct behaviors, made explicit so they hold under future corpus drift. No new output fields; no data-model, schema, writer, or participant-model changes — those were explicitly deferred (see the alignment review). |
| 0.5 | 2026-09-01 | **Narrow correction to the 0.4 `target_name`/Seller rule** — the 15-doc regression surfaced a regression on Sangamo (a bankruptcy asset sale where Seller's own business is what's being acquired): 0.4's wording let "leave null rather than substitute Seller" override the case it was meant to protect, dropping `target_name` from the correct `"Sangamo Therapeutics, Inc."` to `null`. Rewrote the Seller/target_name bullet in § 4 as an explicit two-step decision (Seller's own business/assets with no separately-named divested entity → target_name is Seller; a separately identifiable divested entity → target_name is that entity if named, else null, never substitute Seller) so the two cases can't be conflated. Re-validated against all 9 RECITALS documents in the regression corpus: Sangamo corrected to `"Sangamo Therapeutics, Inc."`; Velocity Financial (a true third-party divestiture, entities named only outside the recitals excerpt) correctly stays `null`; BitGo (a true third-party divestiture with the divested entity named in the excerpt) correctly stays `"NYDIG IF Holdings LLC"`, not Seller. No change to `parent_acquirer_name` wording, `merger_structure`, examples, or any other rule from 0.4 — this release touches only the one bullet. |
| 0.6 | 2026-09-02 | **V3 party-role alignment — replaces the three scalar fields with a `parties` array.** `parent_acquirer_name`, `merger_sub_name`, and `target_name` are retired; the response now returns `parties: [{role, name}]` using eight roles taken directly from the authoritative V3 `role` vocabulary (`docs/v3_data_dictionary.md`): `BUYER · TARGET · SELLER · PARENT_ACQUIRER · PARENT_SELLER · SPONSOR_BUYER · SPONSOR_SELLER · MERGER_SUB` (`MERGER_SUB` is the one addition beyond V3's existing enum, validated against BitGo/Victory Capital's explicit "the Merger Subs" defined term). This fixes the multi-Merger-Sub concatenation defect structurally rather than by instruction — a repeating array has no reason to concatenate — and adds three roles (`SELLER`, `PARENT_ACQUIRER`, `PARENT_SELLER`, `SPONSOR_BUYER`, `SPONSOR_SELLER`) the prior scalar-field contract had no way to express at all, each gated by an explicit-evidence rule validated against the regression corpus (`SELLER` requires an explicit defined-term tag; `PARENT_ACQUIRER`/`PARENT_SELLER` require stated hierarchy/guaranty language, never name resemblance — a real correction to this reviewer's own earlier analysis of Aon's "Aon plc," which had no such language and should not have been called `PARENT_ACQUIRER`; `SPONSOR_BUYER`/`SPONSOR_SELLER` require the sponsor to be a named party to *this* agreement, not merely referenced elsewhere, per Black Spade's "Sponsor," which is a party to a separate ancillary agreement, not this one). One entity may hold two roles (Sangamo: `SELLER` and `TARGET` both point to "Sangamo Therapeutics, Inc."); role membership is otherwise independent per entry, with no cap and no forced uniqueness. `merger_structure` is unchanged. Full mapping, corrected regression, and the Stage 11 changes this required are in `logs/agreement_baseline_20260901/recitals_v04_alignment_review.md` (Steps 8–9). Stage 11's responsibility stops at writing one `party.<ROLE>` observation per entry — no `entity_id`, no `transaction_participant` row; entity resolution is a separate, downstream concern by design, not a gap this prompt or Stage 11 needs to close. |
| 0.7 | 2026-09-02 | **Two narrow corrections surfaced by the 0.6 regression, both in § 4; no example, Stage 11, or other-prompt change.** (1) `PARENT_ACQUIRER` further tightened: Velocity Financial was assigned `PARENT_ACQUIRER` on "shared 'Velocity' branding" and the "Purchaser Parent" label alone — the rule's prior wording excluded shared-name and limited-purpose-joinder evidence but didn't address a defined-term label that itself contains the word "Parent," which the model treated as self-establishing. Now explicit: the label a party is given, shared branding, name similarity, and limited-purpose joinder are each individually insufficient, and the relationship must be stated *of Buyer specifically* ("Buyer, a wholly-owned subsidiary of X"), not just present somewhere in the text. (2) NOT A MERGER split into two cases that the 0.6 wording conflated: an asset/equity purchase still has a real buy-side/sell-side (BUYER/SELLER/TARGET apply, as before), but a capital contribution/investment/JV formation has no such relationship at all, even though the agreement names its own Investor/Contributor/JV-entity roles — ONEOK was relabeling those into BUYER/SELLER/TARGET "because those are the available roles," which the new text prohibits by name; the correct answer for that case is `"parties": []`. |
