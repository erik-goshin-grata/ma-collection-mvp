# Deal Type Classifier Prompt

**Version:** 0.11 (transaction form alone does not determine target_type)
**Repo path:** `prompts/deal_type_classifier.md`

---

## 1. Purpose

Classify each relevant press release into one of 12 mutually exclusive deal
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
title and body of a press release, classify it into exactly one of twelve deal
types. For SPIN_OFF and SPLIT_OFF transactions, also determine two discriminator
fields.

DEAL TYPES (v2_event_type):

1. ACQUISITION — One entity acquires another (or a business unit or subsidiary
   of another). Includes private-to-private, strategic buyer acquiring a public
   target, private equity acquiring a public target (Take-Private), private
   equity acquiring a private target (LBO or add-on), and acquisitions of a
   Parent's business unit or subsidiary by a third party. Default type for
   "Company X acquires Company Y" when no more specific type fits.

   ACQUISITION is the broad event and now also covers transactions previously
   typed MERGER or REVERSE_MERGER, including de-SPAC business combinations. The
   structure through which the acquisition is effected is recorded separately in
   `combination_structure` — see COMBINATION STRUCTURE below. Do NOT return
   MERGER or REVERSE_MERGER as a deal type; they are no longer valid values.

2. SPIN_OFF — A Parent company distributes shares of a subsidiary (SpinCo) to
   its existing shareholders pro-rata. No third-party buyer. No cash
   consideration to the Parent. Parent retains a residual minority stake
   (typically capped at 20% for IRS Section 355 tax-free treatment). Default
   when the spin/split type is ambiguous.

3. SPLIT_OFF — A Parent company distributes shares of a subsidiary to
   shareholders who elect to tender their Parent shares in exchange (exchange
   offer mechanism). Parent distributes 100% of SpinCo, retaining zero equity.
   Identifiable by language like "exchange offer," "tender Parent shares," or
   "election period." In prior versions this was SPIN_SPLIT with
   distribution_mechanism = EXCHANGE_OFFER.

4. JOINT_VENTURE — Two or more parties form a new, jointly owned entity to
   pursue a business activity. Distinct from ACQUISITION because no existing
   entity is being purchased.

5. RECAPITALIZATION — A company restructures its capital structure without a
   change of control. Includes dividend recaps, equity recaps, leveraged
   recaps, and sponsor recaps. When deal_type = RECAPITALIZATION, also
   populate recap_type (see discriminators below).

6. VC_ROUND — A priced or unpriced venture capital funding round. Seed through
   Series N, angel, crowdfunding, convertible notes as primary funding
   instrument. The company raising capital is the target; the investors are
   the capital providers. No change of control.

7. GROWTH_EQUITY — A growth equity investment by a growth equity or late-stage
    investor. Distinct from VC_ROUND by investor type and company maturity:
    growth equity investors (e.g., General Atlantic, Summit Partners, TA
    Associates) taking a minority stake in a profitable or near-profitable
    company. When unclear between VC_ROUND and GROWTH_EQUITY, use VC_ROUND.

8. VENTURE_DEBT — A debt facility to a venture-backed or growth-stage company.
    Includes venture lending, revenue-based financing, convertible notes used
    primarily as debt instruments, and bridge facilities to venture-backed
    companies. Distinct from RECAPITALIZATION by company stage (early/growth
    stage, not mature/PE-backed).

9. PIPE — A private investment in public equity: privately negotiated primary
    issuance of already-public equity (or securities convertible into it) to
    selected investors, outside a registered public offering.

    Use this type ONLY when the source explicitly identifies the structure, by
    the term "PIPE" or by the phrase "private investment in public equity". This
    is a recognition, not an inference.

    The following are NOT a PIPE on their own, however private the capital or
    however public the issuer. Classify each on its own terms, and use UNKNOWN if
    nothing else fits:
      - a private placement of common or preferred stock
      - an issuance of convertible notes or convertible preferred stock
      - a registered direct offering
      - an at-the-market or underwritten public offering
      - any primary raise by a public company that the source does not name a PIPE

    PIPE is a TERMINAL classification. This pipeline recognizes the structure but
    does not profile it: no financial extraction follows, so do not reason about
    round size, valuation, or consideration. Return the type, the parties you can
    identify, and notes. Getting the type right is the whole job.

10. UNKNOWN — The release clearly describes a transaction event but the type
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
- "Minority investment" is NOT a separate type. Minority status is a shared
  characteristic derived downstream. Use the underlying economic event:
  secondary purchase of a non-controlling stake is ACQUISITION; growth equity
  investment is GROWTH_EQUITY only when the source supports genuine growth/private
  equity financing; venture funding is VC_ROUND only when the source supports
  genuine venture financing; venture lending is VENTURE_DEBT. A public-company
  PIPE, primary share issuance, registered direct offering, or other public-company
  minority capital raise is not automatically GROWTH_EQUITY or VC_ROUND merely
  because it is primary capital. When the source explicitly identifies the
  structure as a PIPE or a "private investment in public equity", use PIPE. For
  any other public-company primary raise, and whenever no supported core type
  clearly fits, use UNKNOWN with notes. Do not output MINORITY_INVESTMENT.

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

COMBINATION STRUCTURE:

combination_structure records how an acquisition is structured. It applies ONLY when
v2_event_type = ACQUISITION; for every other deal type it must be null.

- MERGER — the transaction is explicitly structured or effected as a merger or
  combination of the two entities. Merger-of-equals framing is NOT required: an
  ordinary acquisition effected through a merger is MERGER.
- REVERSE_MERGER — a private operating company merges with a public shell or smaller
  public company, becoming publicly traded without a traditional IPO.
- DE_SPAC — a REVERSE_MERGER in which the public vehicle is a special purpose
  acquisition company. Often described as a "business combination" with a SPAC.
- null — the source does not establish any of the three.

The values are HIERARCHICAL, not three peers: DE_SPAC is a kind of REVERSE_MERGER,
which is a kind of MERGER.

- Return the MOST SPECIFIC value the source supports. A SPAC business combination is
  DE_SPAC, not REVERSE_MERGER.
- Ambiguity resolves UPWARD. A reverse merger with no established SPAC shell stays
  REVERSE_MERGER; a combination with no established reverse-merger structure stays
  MERGER.
- Downstream answers broader questions by implication, never by equality, so returning
  the most specific value loses nothing.

What does NOT establish a combination structure:

- A share or stock purchase agreement, or an asset purchase agreement. Absent separate
  merger, reverse-merger or de-SPAC evidence, these are combination_structure = null.
- The mere fact that the transaction is large, friendly, or between similar-sized
  parties.

Two-step structures (a tender offer followed by a squeeze-out merger) remain classified
by economic substance — usually ACQUISITION. Set combination_structure = MERGER when the
source establishes the second-step merger; the offer mechanics themselves are recorded
elsewhere and are not this field's concern.

Merger-of-equals is a separate characteristic extracted downstream from the source, not
by this prompt. Do not attempt to signal it here.

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

Transaction form alone does not determine target type. Do not classify a transaction as
`assets` solely because the source calls it an "asset purchase" or says the buyer acquired
"the assets of" a company. Use the full source to determine whether the transaction is for
a discrete asset or asset set (`assets`) or for an operating business (`business_unit`).
Researcher review can resolve genuinely ambiguous cases.

There is no `spinco` value. SPIN_OFF and SPLIT_OFF are recorded on v2_event_type; they
say what kind of event happened, and target_type independently answers what structural
thing is being transacted. Classify the distributed entity on its own structural merits,
from source evidence:

- an existing subsidiary being distributed   -> subsidiary
- a division or operating business           -> business_unit
- a discrete asset set                       -> assets
- structure not established by the source    -> null

Do NOT use standalone_company merely because the distributed entity will be standalone
after the distribution completes. target_type describes what is being transacted now, not
what it becomes.

Note: target_type values are lowercase in V2. Legacy uppercase values
(STANDALONE_COMPANY, BUSINESS_UNIT, SUBSIDIARY, ASSETS) are no longer valid
— use lowercase equivalents. `spinco` is no longer a valid value at all: it named an
event/role rather than a structure, and duplicated what v2_event_type already says.

When target_type is subsidiary, business_unit, or assets, parent_seller must
exist (extracted by a later prompt). Flag in notes if the Parent is ambiguous.

For JOINT_VENTURE, target_type is null.
For combination_structure = REVERSE_MERGER or DE_SPAC, target_type = standalone_company unless stated otherwise.
For minority stake purchases and funding rounds, use target_type =
standalone_company unless stated otherwise.

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
- Classify the current event based on what is being transacted now, not the
  buyer's resulting ownership after the transaction. For example, if a buyer
  previously acquired an 80% controlling stake and the current release announces
  acquisition of the remaining 20%, the current event is ACQUISITION; the
  remaining-stake transition and minority-stake feature are captured downstream
  by extraction/aggregation, not by changing the core event type or treating
  pct_acquired as 100%.
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.7"
}
```

**Field definitions:**

| Field | Type | Values |
| :--- | :--- | :--- |
| `v2_event_type` | enum | `ACQUISITION`, `SPIN_OFF`, `SPLIT_OFF`, `JOINT_VENTURE`, `RECAPITALIZATION`, `VC_ROUND`, `GROWTH_EQUITY`, `VENTURE_DEBT`, `PIPE`, `UNKNOWN` |
| `combination_structure` | enum\|null | `MERGER`, `REVERSE_MERGER`, `DE_SPAC`, `null`. Only when `v2_event_type = ACQUISITION`; null for every other type. Most specific value; ambiguity resolves upward. |
| `deal_type` | enum | Same as `v2_event_type` — transitional alias, deprecated in future version |
| `spin_split_type` | enum or null | `SPIN_OFF`, `SPLIT_OFF`, or null if v2_event_type ∉ {SPIN_OFF, SPLIT_OFF} |
| `distribution_mechanism` | enum or null | `PRO_RATA`, `EXCHANGE_OFFER`, or null if v2_event_type ∉ {SPIN_OFF, SPLIT_OFF} |
| `recap_type` | enum or null | `DIVIDEND`, `EQUITY`, `LEVERAGED`, `SPONSOR_RECAP`, or null if v2_event_type ≠ RECAPITALIZATION |
| `target_type` | enum or null | `standalone_company`, `subsidiary`, `business_unit`, `assets`, or null for JVs and when structure is not established. **`spinco` is not a valid value.** |
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": null,
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Take-Private context: public target, PE acquirer. Downstream derives Take-Private flag from target_status + acquirer_type.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "business_unit",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Business unit divestiture; parent_seller is MegaCorp (extracted downstream).",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": "SPIN_OFF",
  "distribution_mechanism": "PRO_RATA",
  "recap_type": null,
  "target_type": "subsidiary",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Parent retains 15% residual stake, consistent with SPIN_OFF tax-free treatment.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": "SPLIT_OFF",
  "distribution_mechanism": "EXCHANGE_OFFER",
  "recap_type": null,
  "target_type": "subsidiary",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Practitioner term 'Split-Off' = SPLIT_OFF top-level type + EXCHANGE_OFFER mechanism.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": null,
  "event_history_type": "ANNOUNCED",
  "target_status": "UNKNOWN",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "New entity formed; no existing company acquired.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": "DIVIDEND",
  "target_type": "standalone_company",
  "event_history_type": "CLOSED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Debt-funded special dividend to PE sponsor; no change of control.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Series B funding round. Venture Partners is lead investor. Funding extraction prompt handles investor list and round details.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Growth equity minority investment by named growth equity firm (TA Associates). Profitable company, management partnership framing — GROWTH_EQUITY not VC_ROUND.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Venture lending facility to a Series B-stage company. Explicitly framed as debt extending runway to next equity round.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "First public announcement of a completed private acquisition; not a separate later closing release.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Advisor tombstone; no prior announcement referenced — treat as first observed announcement.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Definitive agreement with pending-close language; event is announcement, not close.",
  "prompt_version": "deal_type_classifier:0.7"
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
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "CLOSED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Separate later completion release explicitly references a previously announced acquisition.",
  "prompt_version": "deal_type_classifier:0.7"
}
```

---

**Example 15 — Acquisition effected through a merger:**

Input:
```
TITLE: Acme Corp to Acquire Beta Industries in All-Cash Merger
BODY: Acme Corp today announced that it has entered into a definitive agreement under which Beta Industries (NASDAQ: BETA) will merge with a wholly owned subsidiary of Acme, with Beta surviving as a wholly owned subsidiary of Acme. Beta shareholders will receive $32.00 per share in cash.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "combination_structure": "MERGER",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Acquisition effected through a merger. combination_structure = MERGER; merger-of-equals framing is not required and is not claimed here.",
  "prompt_version": "deal_type_classifier:0.9"
}
```

**Example 16 — De-SPAC business combination:**

Input:
```
TITLE: Orbit Systems to Become Publicly Traded via Business Combination with Summit Acquisition Corp
BODY: Orbit Systems, a privately held satellite communications company, and Summit Acquisition Corp (NASDAQ: SMAC), a special purpose acquisition company, today announced a definitive business combination agreement. Upon closing, the combined company will trade on the NASDAQ under a new ticker.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "combination_structure": "DE_SPAC",
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "Public vehicle is a SPAC, so the most specific value applies: DE_SPAC, not REVERSE_MERGER. Broader merger questions are answered by implication.",
  "prompt_version": "deal_type_classifier:0.9"
}
```

**Example 17 — Share purchase acquisition establishes no combination structure:**

Input:
```
TITLE: Acme Corp Acquires Delta Systems
BODY: Acme Corp today announced it has acquired Delta Systems through the purchase of all outstanding shares of Delta from its founders. Terms were not disclosed.
```

Output:
```json
{
  "v2_event_type": "ACQUISITION",
  "deal_type": "ACQUISITION",
  "combination_structure": null,
  "spin_split_type": null,
  "distribution_mechanism": null,
  "recap_type": null,
  "target_type": "standalone_company",
  "event_history_type": "ANNOUNCED",
  "target_status": "PRIVATE",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "A share purchase is not a combination structure. Absent merger, reverse-merger or de-SPAC evidence, combination_structure is null.",
  "prompt_version": "deal_type_classifier:0.9"
}
```

---

**Example 18 — Spin-off of a division (target_type is structural, not the event):**

Input:
```
TITLE: Northwind Industries to Spin Off Its Packaging Division
BODY: Northwind Industries (NYSE: NWI) announced that its board has approved the
spin-off of its packaging division as a separate publicly traded company. The division,
which is not currently a separate legal entity, will be incorporated prior to the
distribution. Northwind shareholders will receive shares pro rata.
```

Output:
```json
{
  "v2_event_type": "SPIN_OFF",
  "deal_type": "SPIN_OFF",
  "combination_structure": null,
  "spin_split_type": "SPIN_OFF",
  "distribution_mechanism": "PRO_RATA",
  "recap_type": null,
  "target_type": "business_unit",
  "event_history_type": "ANNOUNCED",
  "target_status": "SUBSIDIARY_OF_PUBLIC",
  "overrides_relevancy_hint": false,
  "model_confidence": "HIGH",
  "notes": "A division, not a separate legal entity, so target_type = business_unit. The spin-off itself is carried by v2_event_type; target_type says what structural thing is being transacted. It is not standalone_company merely because it becomes standalone after the distribution.",
  "prompt_version": "deal_type_classifier:0.10"
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
| Model returns MINORITY_INVESTMENT | Parser rejects — minority is a derived flag, not a core event type |
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
| 0.7 | 2026-08-12 | Removed MINORITY_INVESTMENT from core classifier output vocabulary. Minority status routes to the underlying economic event and is derived downstream as `is_minority`. |
| 0.8 | 2026-08-18 | PIPE added as a recognized, unprofiled type (11 → 12 types; UNKNOWN renumbered to 12). Used only when the source explicitly identifies the structure by the term "PIPE" or the phrase "private investment in public equity" — a recognition, not an inference. Carries an explicit negative list (private placement, convertible notes or preferred, registered direct, ATM/underwritten offering) so a new bucket does not become a catch-all for private capital into public issuers; those still route to UNKNOWN. PIPE is terminal: Stage 3 stamps `RECOGNIZED_NOT_PROFILED` and no extraction, clustering or aggregation follows, so no round_size, transaction_size or valuation is derived. See `lib/pipe_recognition.py`. |
| 0.9 | 2026-08-20 | **Merger family becomes `combination_structure` (V3 §T2).** `MERGER` and `REVERSE_MERGER` are removed as `v2_event_type` values and are now **invalid output**; both are structures of an acquisition, not separate events. New field `combination_structure` ∈ `MERGER` / `REVERSE_MERGER` / `DE_SPAC` / null, hierarchical (`DE_SPAC ⊂ REVERSE_MERGER ⊂ MERGER`), valid **only** when `v2_event_type = ACQUISITION` and null for every other type — which is what keeps Spin/Split, JV, Recap, Funding, PIPE and UNKNOWN untouched by this change. Return the most specific supported value; ambiguity resolves upward. **A share or asset purchase does not establish a combination structure** — absent other evidence it is null. Merger-of-equals is unchanged: still extracted downstream from the source, never signalled here. The `REVERSE_MERGER` target_type rule re-keys onto `combination_structure`. Examples 15-17 added (merger, de-SPAC, share-purchase null). |
| 0.10 | 2026-08-20 | **`spinco` removed from `target_type` (V3 §T3).** It named an event/role, not a structure, and duplicated what `v2_event_type` already says. `target_type` now answers one question consistently — what structural thing is being transacted — so a SPIN_OFF or SPLIT_OFF is typed on the distributed entity's own merits: `subsidiary`, `business_unit`, `assets`, or null when the source does not establish it. **Not `standalone_company`** merely because it becomes standalone after the distribution. Examples 4 and 5 re-typed to `subsidiary`; Example 18 added for the division case. New output naming `spinco` is a schema violation. |
| 0.11 | 2026-08-20 | **Transaction form alone does not determine `target_type`.** Gate 2 established one narrow failure: the classifier selected `assets` from asset-purchase wording ("acquired the assets of") on a source whose substance was the acquisition of a continuing operating business. One principle added to TARGET TYPE — do not classify as `assets` **solely** because of transaction-form language; read the full source to decide between a discrete asset set and an operating business. Deliberately minimal: no parsing rule, no evidence checklist, no decision tree, no new values, and the four-value taxonomy and its definitions are otherwise unchanged. "Solely" is load-bearing — the point is to remove a mechanical cue, not to install another one. Researcher review remains available for genuinely ambiguous cases. |
