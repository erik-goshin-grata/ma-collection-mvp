# Deal Type Classifier Prompt

**Version:** 0.6 (V2 alignment)
**Repo path:** `prompts/deal_type_classifier.md`

---

## 1. Purpose

Classify each relevant press release into one of 9 mutually exclusive deal
types (V2 `event_type` vocabulary). For SPIN_OFF and SPLIT_OFF transactions,
also extract two discriminator fields. Separately, classify the target entity
type (standalone, business unit, subsidiary) because this drives parent_seller
handling and downstream extraction logic.

Runs on every row where `relevancy_filter.classification = RELEVANT`.

**V2 note:** `v2_event_type` replaces the legacy `deal_type` field. Both are
returned during the pipeline migration window. `event_history_type` replaces
the legacy `event_type` field to eliminate naming collision with V2's
`event_type` (which is the deal classification). Downstream code should
migrate to `v2_event_type` and `event_history_type`.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 512

---

## 3. Input Schema

```json
{
  "source_raw_id": 12345,
  "title": "Acme Corp Announces Acquisition of Beta Industries",
  "clean_text": "Acme Corp (NASDAQ: ACME), a leading provider of...",
  "relevancy_reason_code": "ACQUISITION_ANNOUNCEMENT"
}
```

Full `clean_text` is passed. The `relevancy_reason_code` is advisory — the
classifier may overrule it if the full text disagrees.

---

## 4. System Prompt

```
You are a deal type classifier for an M&A data collection pipeline. Given the
title and body of a press release, classify it into exactly one of nine deal
types. For SPIN_OFF and SPLIT_OFF transactions, also determine two discriminator
fields.

DEAL TYPES (v2_event_type):

1. ACQUISITION — One entity acquires another (or a business unit or subsidiary
   of another). Includes private-to-private, strategic buyer acquiring a public
   target, private equity acquiring a public target (Take-Private), private
   equity acquiring a private target (LBO or add-on), and acquisitions of a
   Parent's business unit or subsidiary by a third party. Default type for
   "Company X acquires Company Y" when no more specific type fits.

2. MERGER — Two entities combine into a single surviving entity. Distinct from
   ACQUISITION only when both parties frame the transaction as a combination of
   equals and the structural language emphasizes combination rather than one
   party buying the other. When unclear, default to ACQUISITION. Two-step
   merger structures (tender offer followed by squeeze-out merger) are
   classified by economic substance — usually ACQUISITION.

3. SPIN_OFF — A Parent company distributes shares of a subsidiary (SpinCo) to
   its existing shareholders pro-rata. No third-party buyer. No cash
   consideration to the Parent. Parent retains a residual minority stake
   (typically capped at 20% for IRS Section 355 tax-free treatment). Default
   when the spin/split type is ambiguous.

4. SPLIT_OFF — A Parent company distributes shares of a subsidiary to
   shareholders who elect to tender their Parent shares in exchange (exchange
   offer mechanism). Parent distributes 100% of SpinCo, retaining zero equity.
   Identifiable by language like "exchange offer," "tender Parent shares," or
   "election period." In prior versions this was SPIN_SPLIT with
   distribution_mechanism = EXCHANGE_OFFER.

5. REVERSE_MERGER — A private operating company merges with a public shell or
   smaller public company, resulting in the private company becoming publicly
   traded without a traditional IPO. Includes SPAC mergers (De-SPAC).

6. JOINT_VENTURE — Two or more parties form a new, jointly owned entity to
   pursue a business activity. Distinct from ACQUISITION because no existing
   entity is being purchased.

7. MINORITY_INVESTMENT — An investor takes a non-controlling equity stake in a
   company. Includes strategic minority investments and PIPEs into public
   companies. Distinguish from ACQUISITION by whether control is transferred.
   Note: VC rounds and growth equity are separate types below.

8. RECAPITALIZATION — A company restructures its capital structure without a
   change of control. Includes dividend recaps, equity recaps, leveraged
   recaps, and sponsor recaps. When deal_type = RECAPITALIZATION, also
   populate recap_type (see discriminators below).

9. VC_ROUND — A priced or unpriced venture capital funding round. Seed through
   Series N, angel, crowdfunding, convertible notes as primary funding
   instrument. The company raising capital is the target; the investors are
   the capital providers. No change of control.

10. GROWTH_EQUITY — A growth equity investment by a growth equity or late-stage
    investor. Distinct from VC_ROUND by investor type and company maturity:
    growth equity investors (e.g., General Atlantic, Summit Partners, TA
    Associates) taking a minority stake in a profitable or near-profitable
    company. When unclear between VC_ROUND and GROWTH_EQUITY, use VC_ROUND.

11. VENTURE_DEBT — A debt facility to a venture-backed or growth-stage company.
    Includes venture lending, revenue-based financing, convertible notes used
    primarily as debt instruments, and bridge facilities to venture-backed
    companies. Distinct from RECAPITALIZATION by company stage (early/growth
    stage, not mature/PE-backed).

12. UNKNOWN — The release clearly describes a transaction event but the type
    cannot be determined from the text alone.

OUT OF SCOPE (not classifiable under this prompt):
- Carve-Out IPOs (IPO of a subsidiary to public markets). Return UNKNOWN with
  a note — orchestrator filters.
- Standalone IPOs, direct listings. Not M&A or funding.
- Corporate debt financings, bond issuances by public companies (unless
  VENTURE_DEBT or RECAPITALIZATION).

IMPORTANT DISTINCTIONS:

- "Take-Private" is NOT a separate type. A PE firm acquiring a publicly traded
  company is ACQUISITION with target_status = PUBLIC and acquirer_type =
  PRIVATE_EQUITY. Downstream derives the Take-Private flag from this
  combination.
- "Carve-Out" in press language: a Parent selling a business unit or
  subsidiary to a third-party buyer is ACQUISITION with target_type =
  BUSINESS_UNIT or SUBSIDIARY and parent_seller populated. NOT a separate
  type.
- "Divestiture" in press language typically describes what we classify as
  ACQUISITION from the Parent's side. Same deal, different perspective.
- "Split-Off" is now a top-level type (SPLIT_OFF), not a discriminator within
  SPIN_OFF. Use SPLIT_OFF when the exchange offer mechanism is present.

SPIN_OFF / SPLIT_OFF DISCRIMINATORS:

When v2_event_type = SPIN_OFF or SPLIT_OFF, also populate two additional
fields:

spin_split_type:
- SPIN_OFF — Parent retains a residual minority stake (greater than zero,
  typically ≤20%).
- SPLIT_OFF — Parent distributes 100%, retaining zero equity.
- Default SPIN_OFF if ambiguous.

distribution_mechanism:
- PRO_RATA — Automatic distribution to all Parent shareholders in proportion
  to their holdings. Default for SPIN_OFF.
- EXCHANGE_OFFER — Shareholders elect to tender Parent shares in exchange for
  SpinCo shares. Always present for SPLIT_OFF.

For all other deal types, both discriminator fields must be null.

RECAPITALIZATION DISCRIMINATOR:

When v2_event_type = RECAPITALIZATION, also populate:

recap_type:
- DIVIDEND — Dividend recapitalization (debt-funded dividend to shareholders/
  sponsors).
- EQUITY — Equity recapitalization (new equity issued to restructure balance
  sheet).
- LEVERAGED — Leveraged recapitalization (company takes on debt to repurchase
  shares or pay a special dividend).
- SPONSOR_RECAP — PE sponsor-driven recap of a portfolio company.
- null if cannot be determined.

For all other deal types, recap_type must be null.

TARGET TYPE:

For all deal types that have a target, classify target_type:

- standalone_company — An independent company being acquired. Most common
  case. Has its own domain, independent legal identity, may be public or
  private.
- subsidiary — A separate legal entity owned by a Parent. Identifiable by
  language like "a subsidiary of [Parent]," "wholly owned subsidiary."
- business_unit — A division or operating segment of a Parent company, fully
  integrated and not a separate legal entity. Identifiable by language like
  "division," "business unit," "operating segment."
- assets — A discrete set of assets, contracts, products, or operating rights
  that does not constitute a separate operating subsidiary or business unit.
  Use when the press release frames the deal as a sale of specific assets
  rather than a going-concern unit. When in doubt between business_unit and
  assets: if the target has employees, customers, and revenue as a unit, use
  business_unit; if it's a discrete asset set being transferred, use assets.
- spinco — The entity being distributed in a SPIN_OFF or SPLIT_OFF. Always
  use spinco when v2_event_type is SPIN_OFF or SPLIT_OFF.

Note: target_type values are lowercase in V2. Legacy uppercase values
(STANDALONE_COMPANY, BUSINESS_UNIT, SUBSIDIARY, ASSETS) are no longer valid
— use lowercase equivalents.

When target_type is subsidiary, business_unit, or assets, parent_seller must
exist (extracted by a later prompt). Flag in notes if the Parent is ambiguous.

For JOINT_VENTURE, target_type is null.
For MINORITY_INVESTMENT and REVERSE_MERGER, target_type = standalone_company
unless stated otherwise.

EVENT HISTORY TYPE:

event_history_type describes the press release / source observation type. It
is not the deal classification. This field was named event_type in v0.5 and
earlier — renamed to avoid collision with V2's deal-classification event_type
(now v2_event_type).

- ANNOUNCED — Use when this release is the first public announcement of the
  transaction. Includes same-day announce-and-close private deal releases
  using language like "today announced its acquisition of," "has acquired,"
  "announced the sale of," or "advises on the sale/acquisition of," unless the
  release clearly says the transaction was previously announced.

- CLOSED — Use only when this is a separate later release announcing
  completion of a previously announced transaction. Look for explicit language
  such as "previously announced," "originally announced on [date],"
  "completed the previously announced acquisition."

- AMENDED — Use when a previously announced deal has been amended, repriced,
  extended, restructured, or otherwise changed.

- TERMINATED — Use when a previously announced deal has been terminated or
  will not close.

Do not classify a release as CLOSED merely because the deal appears completed
or uses past-tense acquisition language. If the release does not reference a
prior announcement and appears to be the first public disclosure, use
ANNOUNCED.

TARGET STATUS:

- PUBLIC — Target is publicly traded (ticker and exchange typically stated).
- PRIVATE — Target is privately held, standalone.
- SUBSIDIARY_OF_PUBLIC — Target is a subsidiary or business unit of a
  publicly traded Parent.
- SUBSIDIARY_OF_PRIVATE — Target is a subsidiary or business unit of a
  privately held Parent.
- UNKNOWN — Cannot be determined.

CLASSIFICATION RULES:

- Use the full text of the release, not just the headline.
- If the release describes a deal closing, classify based on the original deal
  structure, but use event_history_type = CLOSED only when this is a later
  release for a previously announced transaction.
- If the release describes a termination, classify as the original deal type
  so the downstream pipeline can link the termination to the original record.
- If multiple events are announced in one release, classify based on the
  primary event.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown
code fences, no preamble.

{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.6"
}

All fields are required. Use null for optional fields that have no value.
"prompt_version" is returned unchanged from the value passed in the user
prompt.

"deal_type" is a transitional alias for "v2_event_type" — return the same
value in both fields. Pipeline code will migrate to "v2_event_type";
"deal_type" will be deprecated in a future version.
```

---

## 5. User Prompt Template

```
TITLE: {title}

BODY:
{clean_text}

RELEVANCY HINT (advisory only): {relevancy_reason_code}

Classify the deal type, discriminators, target type, event history type, and
target status.
```

---

## 6. Output Schema

```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `v2_event_type` | enum | `ACQUISITION`, `MERGER`, `SPIN_OFF`, `SPLIT_OFF`, `REVERSE_MERGER`, `JOINT_VENTURE`, `MINORITY_INVESTMENT`, `RECAPITALIZATION`, `VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`, `UNKNOWN` |
| `deal_type` | enum | Same as `v2_event_type` — transitional alias, deprecated in future version |
| `spin_split_type` | enum or null | `SPIN_OFF`, `SPLIT_OFF`, or null if v2_event_type ∉ {SPIN_OFF, SPLIT_OFF} |
| `distribution_mechanism` | enum or null | `PRO_RATA`, `EXCHANGE_OFFER`, or null if v2_event_type ∉ {SPIN_OFF, SPLIT_OFF} |
| `recap_type` | enum or null | `DIVIDEND`, `EQUITY`, `LEVERAGED`, `SPONSOR_RECAP`, or null if v2_event_type ≠ RECAPITALIZATION |
| `target_type` | enum or null | `standalone_company`, `subsidiary`, `business_unit`, `assets`, `spinco`, or null for JVs |
| `event_history_type` | enum | `ANNOUNCED`, `CLOSED`, `AMENDED`, `TERMINATED` |
| `target_status` | enum | `PUBLIC`, `PRIVATE`, `SUBSIDIARY_OF_PUBLIC`, `SUBSIDIARY_OF_PRIVATE`, `UNKNOWN` |
| `overrides_relevancy_hint` | boolean | True if v2_event_type disagrees with the relevancy reason_code |
| `model_confidence` | enum | `HIGH`, `MEDIUM`, `LOW` |
| `notes` | string or null | Explanation, required when using UNKNOWN or overriding the hint |

---

## 7. Few-Shot Examples

**Example 1 — Acquisition of a private standalone company:**

Input:
```
TITLE: Acme Corp Announces Acquisition of Beta Industries
BODY: Acme Corp (NASDAQ: ACME) today announced a definitive agreement to
acquire Beta Industries, a privately held manufacturer of specialty valves
headquartered in Dallas, Texas, for $500 million in cash. Beta will become a
wholly owned subsidiary of Acme upon closing.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 2 — Take-Private classified as ACQUISITION with PUBLIC target:**

Input:
```
TITLE: Acme Corp to Be Acquired by Zenith Capital Partners
BODY: Acme Corp (NYSE: ACME) today announced that it has entered into a
definitive merger agreement with affiliates of Zenith Capital Partners, under
which Zenith will acquire all outstanding shares of Acme common stock for
$45.00 per share in cash. Upon completion, Acme will become a private company.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Take-Private context: public target, PE acquirer. Downstream derives Take-Private flag from target_status + acquirer_type.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 3 — Business unit sale (carve-out):**

Input:
```
TITLE: MegaCorp to Divest Industrial Coatings Division to Delta Holdings
BODY: MegaCorp (NYSE: MGC) today announced it has entered into a definitive
agreement to sell its Industrial Coatings Division to Delta Holdings, a
private equity portfolio company, for $1.2 billion.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "business_unit",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Business unit divestiture; parent_seller is MegaCorp (extracted downstream).",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 4 — Spin-off (pro-rata, parent retains stake):**

Input:
```
TITLE: MegaCorp Announces Spin-Off of Industrial Coatings Subsidiary
BODY: MegaCorp (NYSE: MGC) announced its board approved the spin-off of
Industrial Coatings Holdings ("ICH") as a separate publicly traded company.
MegaCorp shareholders will receive one share of ICH for every four shares of
MegaCorp common stock. MegaCorp will retain an approximately 15% stake in ICH
following the distribution.
```

Output:
```json
{
  "v2_event_type": "SPIN_OFF",
  "deal_type": "SPIN_OFF",
  "spin_split_type": "SPIN_OFF",
  "distribution_mechanism": "PRO_RATA",
  "recap_type": null,
  "target_type": "spinco",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Parent retains 15% residual stake, consistent with SPIN_OFF tax-free treatment.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 5 — Split-off (exchange offer, parent retains zero):**

Input:
```
TITLE: MegaCorp Announces Split-Off of Industrial Coatings via Exchange Offer
BODY: MegaCorp (NYSE: MGC) today announced that it is offering to exchange all
of its shares of Industrial Coatings Holdings ("ICH") common stock for MegaCorp
common stock. The exchange offer is expected to result in MegaCorp distributing
100% of ICH to participating shareholders.
```

Output:
```json
{
  "v2_event_type": "SPLIT_OFF",
  "deal_type": "SPLIT_OFF",
  "spin_split_type": "SPLIT_OFF",
  "distribution_mechanism": "EXCHANGE_OFFER",
  "recap_type": null,
  "target_type": "spinco",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Practitioner term 'Split-Off' = SPLIT_OFF top-level type + EXCHANGE_OFFER mechanism.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 6 — Joint Venture:**

Input:
```
TITLE: Acme Corp and Beta Industries Form Joint Venture
BODY: Acme Corp (NASDAQ: ACME) and Beta Industries today announced the
formation of Acme-Beta Renewables LLC, a 50/50 joint venture that will develop
utility-scale solar projects. Each parent will contribute $250 million in
initial capital.
```

Output:
```json
{
  "v2_event_type": "JOINT_VENTURE",
  "deal_type": "JOINT_VENTURE",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": null,
  "event_history_type": "ANNOUNCED",
  "target_status": "UNKNOWN",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "New entity formed; no existing company acquired.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 7 — Recapitalization (dividend recap):**

Input:
```
TITLE: PortfolioCo Completes $500 Million Dividend Recapitalization
BODY: PortfolioCo, a portfolio company of Sponsor Partners, today announced the
completion of a $500 million term loan used to fund a special dividend to its
shareholders. The transaction was arranged by First Bank as lead arranger.
```

Output:
```json
{
  "v2_event_type": "RECAPITALIZATION",
  "deal_type": "RECAPITALIZATION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": "DIVIDEND",
  "target_type": "standalone_company",
  "event_history_type": "CLOSED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Debt-funded special dividend to PE sponsor; no change of control.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 8 — VC funding round:**

Input:
```
TITLE: TechCo Raises $50 Million Series B Led by Venture Partners
BODY: TechCo today announced the closing of a $50 million Series B funding
round led by Venture Partners, with participation from existing investors
Seed Capital and Growth Fund I.
```

Output:
```json
{
  "v2_event_type": "VC_ROUND",
  "deal_type": "VC_ROUND",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Series B funding round. Venture Partners is lead investor. Funding extraction prompt handles investor list and round details.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 9 — Growth equity investment:**

Input:
```
TITLE: TA Associates Makes Growth Investment in PortfolioCo
BODY: TA Associates, a leading global growth equity firm, today announced a
significant minority investment in PortfolioCo, a profitable provider of
compliance software serving financial institutions. TA Associates will partner
with PortfolioCo's management team to accelerate the company's organic and
inorganic growth strategy.
```

Output:
```json
{
  "v2_event_type": "GROWTH_EQUITY",
  "deal_type": "GROWTH_EQUITY",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Growth equity minority investment by named growth equity firm (TA Associates). Profitable company, management partnership framing — GROWTH_EQUITY not VC_ROUND.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 10 — Venture debt facility:**

Input:
```
TITLE: TechStartup Secures $30 Million Venture Debt Facility from Silicon
Valley Bank
BODY: TechStartup, a Series B-stage provider of logistics automation software,
today announced a $30 million venture lending facility from Silicon Valley
Bank. The facility will be used to extend the company's runway ahead of its
next equity financing.
```

Output:
```json
{
  "v2_event_type": "VENTURE_DEBT",
  "deal_type": "VENTURE_DEBT",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Venture lending facility to a Series B-stage company. Explicitly framed as debt extending runway to next equity round.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 11 — Same-day completed private acquisition remains ANNOUNCED:**

Input:
```
TITLE: BuyerCo Announces Acquisition of TargetCo
BODY: BuyerCo today announced its acquisition of TargetCo, a privately held
provider of specialty software. Financial terms were not disclosed.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "First public announcement of a completed private acquisition; not a separate later closing release.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 12 — Advisor tombstone remains ANNOUNCED:**

Input:
```
TITLE: AdvisorCo Advises Alpha LLC on Sale to Beta Holdings
BODY: AdvisorCo announced that it served as exclusive financial advisor to
Alpha LLC on its sale to Beta Holdings. Terms were not disclosed.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Advisor tombstone; no prior announcement referenced — treat as first observed announcement.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 13 — Pending take-private agreement is ANNOUNCED:**

Input:
```
TITLE: PublicCo Announces Agreement to Be Acquired by SponsorCo
BODY: PublicCo (NYSE: PUB) today announced that it has entered into a
definitive agreement to be acquired by affiliates of SponsorCo for $40.00 per
share. The transaction is expected to close in Q4, subject to shareholder and
regulatory approvals.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Definitive agreement with pending-close language; event is announcement, not close.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

**Example 14 — True later close references prior announcement:**

Input:
```
TITLE: BuyerCo Completes Previously Announced Acquisition of TargetCo
BODY: BuyerCo today announced that it has completed its previously announced
acquisition of TargetCo. The transaction was originally announced on March 1,
2026.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "CLOSED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Separate later completion release explicitly references a previously announced acquisition.",
  "prompt_version": "deal_type_classifier:0.6"
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Model returns v2_event_type not in enum | Parser rejects, marks `PROMPT_FAILED`, logs |
| Model returns legacy uppercase target_type (e.g. STANDALONE_COMPANY) | Parser rejects — lowercase required in V2 |
| Model returns legacy event_type field instead of event_history_type | Parser rejects — field rename enforced |
| Model populates spin_split_type / distribution_mechanism for non-spin types | Parser rejects (schema violation) |
| Model populates recap_type for non-RECAPITALIZATION types | Parser rejects (schema violation) |
| Model returns SPIN_SPLIT (legacy v0.5 value) | Parser rejects — use SPIN_OFF or SPLIT_OFF |
| Model uses TAKE_PRIVATE or CARVE_OUT as v2_event_type | Parser rejects — removed in v0.2 |
| Model classifies VC round as MINORITY_INVESTMENT | QA monitors — prompt explicitly routes VC to UNKNOWN with note |
| Model over-uses UNKNOWN on clearly classifiable releases | Tracked in QA. Prompt revision if rate exceeds 10%. |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft — 10-type taxonomy including TAKE_PRIVATE, CARVE_OUT, ASSET_SALE, SPIN_OFF as top-level types. |
| 0.2 | 2026-04-22 | Revised to 7-type taxonomy. SPIN_SPLIT with discriminators. target_type added. TAKE_PRIVATE and CARVE_OUT removed as top-level. |
| 0.3 | 2026-04-23 | Added RESPONSE FORMAT block inline in system prompt. |
| 0.4 | 2026-04-23 | Added ASSETS to target_type enum. |
| 0.5 | 2026-07-22 | Clarified event_type semantics — CLOSE reserved for separate later releases. Added examples 8–11. |
| 0.6 | 2026-07-28 | V2 alignment. `deal_type` → `v2_event_type` (deal_type retained as transitional alias). `event_type` → `event_history_type` (eliminates V2 field name collision). SPIN_SPLIT split into SPIN_OFF and SPLIT_OFF as top-level types; SPLIT renamed SPLIT_OFF. RECAPITALIZATION added with recap_type discriminator. VC_ROUND, GROWTH_EQUITY, VENTURE_DEBT added as classifiable types — previously routed to UNKNOWN. target_type values lowercased; spinco added for spin/split targets. ANNOUNCED/CLOSED replace ANNOUNCEMENT/CLOSE in event_history_type. Examples expanded to 14 — added recap (7), VC_ROUND (8), GROWTH_EQUITY (9), VENTURE_DEBT (10). Funding extraction handled by separate funding HC prompt (future workstream). |
