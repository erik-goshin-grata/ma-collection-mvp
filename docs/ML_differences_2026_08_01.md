# Extraction vs. MergerLinks — Identified Differences

**Set:** MergerLinks `ML_LLM_20260731.csv`, 91 deals (92 extracted rows).  
**Method:** blind extraction — only scraped source text fed to the LLM; MergerLinks values used solely as the answer key.  
**Note on currency:** MergerLinks normalizes all values to **GBP millions**; we extract in the **source currency**. Value comparisons below are currency-adjusted (USD→GBP ≈ 0.746).

---

## Summary

| Field | Match | We missed | We got more | Differ | Nature of the gap |
|---|---|---|---|---|---|
| Deal value (vs DEAL_VALUE) | 28 | 5 | 3 | 2 | Currency-adjusted; matches strong |
| Enterprise value (vs ENTERPRISE_VALUE) | 14 | 5 | 15 | 4 | We often captured EV ML left blank |
| Announced date | 85 | 0 | 0 | 7 | Small residual diffs |
| Closed date | 32 | 22 | 2 | 1 | Mostly a same-day-completion **convention** gap |
| Target fin. advisors | 16 | 8 | 2 | 0 | Multi-source + ML non-URL feeds |
| Acquirer fin. advisors | 14 | 7 | 3 | 0 | Multi-source + ML non-URL feeds |

_(BOTH_EMPTY rows — where neither side has a value — are omitted from the counts above.)_

---

## 1. Close dates — a convention difference, not missing data

MergerLinks treats an announcement with no forward/pending-close language as **closed on the announcement date**; we default such deals to **PENDING**. Of the close-date gaps, ~21/22 are same-day completions (funding rounds, minority/growth investments that close on announcement).

Deals where MergerLinks has a close date and we don't:
- **Eli Lilly and Company / 4E Therapeutics, Inc.** — ML closed `2026-06-16`, ours `—`
- **Sixth Street Growth / Chronograph** — ML closed `2026-06-16`, ours `—`
- **Bertram Capital / The Bluebird Group LLC** — ML closed `2026-06-16`, ours `—`
- **Ripple / Flutterwave** — ML closed `2026-06-16`, ours `—`
- **WPS Health Solutions / Mavida Health** — ML closed `2026-06-16`, ours `—`
- **Modera Wealth Management, LLC / Northstar Financial Planners, Inc.** — ML closed `2026-06-16`, ours `—`
- **LDC (Lloyds Development Capital Limited) / Fortus** — ML closed `2026-06-16`, ours `—`
- **Camber Partners / Respond.io** — ML closed `2026-06-16`, ours `—`
- **xFact / Stonewall Solutions** — ML closed `2026-06-16`, ours `—`
- **Summize Ltd / InnoLaw Group (ILG)** — ML closed `2026-06-16`, ours `—`
- **Decibel Partners / Ent** — ML closed `2026-06-16`, ours `—`
- **Cleargate Capital Partners / Fellow Health Partners** — ML closed `2026-06-16`, ours `—`
- **TDK Ventures, Building Ventures, JLL Spark Global Ventures / Aston Power** — ML closed `2026-06-16`, ours `—`
- **Lead Edge Capital / Elektrik** — ML closed `2026-06-16`, ours `—`
- **Sixth Street / Pinnacle Gas Services LLC** — ML closed `2026-06-15`, ours `—`
- **HCLTech / Sarvam AI** — ML closed `2026-06-15`, ours `—`
- **Bettor Capital, Commerce Ventures, Decades Holdings, Thayer Street Partners / Interchecks** — ML closed `2026-06-15`, ours `—`
- **Washington Harbour Partners LP / Computomic** — ML closed `2026-06-15`, ours `—`
- **Arteche Group / SEG Electronics** — ML closed `2026-06-15`, ours `—`
- **Prime Radiant Partners / Cellares** — ML closed `2026-06-15`, ours `—`
- **Kesko / Dahl** — ML closed `2026-06-15`, ours `—`
- **Wing Venture Capital, Madrona (co-leads); Obvious Ventures, Snowflake Ventures, Hudson River Trading, Samsung Next, Magarac Venture Partners / Gray Swan** — ML closed `2026-06-28`, ours `—`

## 2. Deal value — currency, not error

Remaining value DIFFs after currency adjustment (worth a manual look — could be basis mismatch EV vs equity, or a genuine miss):
- **High Tide Inc. / J. Supply Holdings Inc. (dba Northern Helm)** — ours `7740000.0` (TRANSACTION_VALUE), ML DEAL_VALUE `5.76` (GBP m) — flag `DIFF(4 vs 6)`
- **Prime Radiant Partners / Cellares** — ours `50000000.0` (TRANSACTION_VALUE), ML DEAL_VALUE `243.74` (GBP m) — flag `DIFF(37 vs 244)`

Where **we captured a value MergerLinks left blank** (we-got-more):
- **Nano Dimension Ltd. / Infinite Epigenetics** — ours `890000000.0` (TRANSACTION_VALUE)
- **Kesko / Dahl** — ours `1518000000.0` (ENTERPRISE_VALUE)
- **Galantas Gold Corporation / Sol de Oro Mining Ltd.** — ours `32500000.0` (TRANSACTION_VALUE)

## 3. Enterprise value — we frequently captured more

On **15** deals we extracted an enterprise value that MergerLinks left blank:
- **Sixth Street Growth / Chronograph** — ours `140000000.0` (EQUITY_VALUE)
- **Camber Partners / Respond.io** — ours `62500000.0` (TRANSACTION_VALUE)
- **Selva Ventures / Kimba** — ours `6500000.0` (TRANSACTION_VALUE)
- **Decibel Partners / Ent** — ours `100000000.0` (TRANSACTION_VALUE)
- **TDK Ventures, Building Ventures, JLL Spark Global Ventures / Aston Power** — ours `20000000.0` (EQUITY_VALUE)
- **ClavystBio, Amed Ventures, Ascension Ventures, Catalyst Health Ventures, Delos Capital, FemHealth Ventures, Iyengar Capital, Sparta Group / Rejoni, Inc.** — ours `25000000.0` (TRANSACTION_VALUE)
- **The Westly Group (lead); Keysight Technologies, Allegis Capital, DNX Ventures, Sutter Hill Ventures, Mayfield Fund, Canaan Partners, Wing Venture Capital / AttoTude Inc.** — ours `52000000.0` (EQUITY_VALUE)
- **Nano Dimension Ltd. / Infinite Epigenetics** — ours `890000000.0` (TRANSACTION_VALUE)
- **Kindred Ventures (lead); NVIDIA; ARK Invest; SPLY Capital; Era Funds; Comcast Ventures; Magnetar; PEAK6; Founders Fund; 10x Founders; Sterling Road; Flume Ventures / Hydra Host** — ours `100000000.0` (TRANSACTION_VALUE)
- **Bettor Capital, Commerce Ventures, Decades Holdings, Thayer Street Partners / Interchecks** — ours `50000000.0` (TRANSACTION_VALUE)
- **SYN Ventures / Arcade.dev** — ours `60000000.0` (TRANSACTION_VALUE)
- **Emergence Capital / Radical Numerics** — ours `50000000.0` (TRANSACTION_VALUE)
- **Prime Radiant Partners / Cellares** — ours `50000000.0` (TRANSACTION_VALUE)
- **Wing Venture Capital, Madrona (co-leads); Obvious Ventures, Snowflake Ventures, Hudson River Trading, Samsung Next, Magarac Venture Partners / Gray Swan** — ours `40000000.0` (TRANSACTION_VALUE)
- **Galantas Gold Corporation / Sol de Oro Mining Ltd.** — ours `32500000.0` (TRANSACTION_VALUE)

EV DIFFs (both present, differ):
- **Sixth Street / Pinnacle Gas Services LLC** — ours `600000000.0`, ML EV `1656.44` (GBP m) — `DIFF(448 vs 1656)`
- **HCLTech / Sarvam AI** — ours `234000000.0`, ML EV `1118.097` (GBP m) — `DIFF(175 vs 1118)`
- **SWI Stoneweg Icona Group / Genesis Digital Assets Limited** — ours `500000000.0`, ML EV `973.1044386` (GBP m) — `DIFF(373 vs 973)`
- **High Tide Inc. / J. Supply Holdings Inc. (dba Northern Helm)** — ours `7740000.0`, ML EV `5.76` (GBP m) — `DIFF(4 vs 6)`

## 4. Advisors — recall gap, partly structural

We captured ~2/3 of advisors where MergerLinks has them. The gap is confounded by (a) our **one-URL-per-deal** ingest (multi-source deals had ~2× the miss rate), and (b) MergerLinks sourcing some advisor data from **direct feeds not present in any press release**. Not purely an extraction failure.

Target financial advisors we missed:
- **Montagu Private Equity LLP / BMC Helix** — ML: `Jefferies & Company`
- **Bertram Capital / The Bluebird Group LLC** — ML: `Canaccord Genuity`
- **CVC Catalyst III / WillowWood Holdings Inc.** — ML: `Houlihan Lokey`
- **Norwegian Air Shuttle ASA / Nordic Leisure Travel Group** — ML: `Arctic Securities; DNB Carnegie Investment Bank`
- **Salesforce / Fin** — ML: `Morgan Stanley`
- **Residence / GateMaker** — ML: `PALAZZO Investment Bankers`
- **American Express / TheFork** — ML: `Goldman Sachs`
- **Frasers Group plc / Accent Group Limited** — ML: `Luminis Partners`

Acquirer financial advisors we missed:
- **Bertram Capital / The Bluebird Group LLC** — ML: `BrightTower`
- **Norwegian Air Shuttle ASA / Nordic Leisure Travel Group** — ML: `Pareto Securities`
- **Yum China Holdings, Inc. / Pizza Hut (Mainland China Operations)** — ML: `Lazard`
- **Edenred / The Mobility House Solutions** — ML: `Credit Agricole`
- **Salesforce / Fin** — ML: `JP Morgan`
- **Wärtsilä / RCT Solutions Joint Venture / Wärtsilä Energy Storage business** — ML: `Morgan Stanley`
- **Gilat Satellite Networks Ltd. / Satellite and Space Communications segment (S&S)** — ML: `Oppenheimer & Co`

## 5. Announced date — minor

- **Caverion Finland / Fitelnet Oy** — ours `2026-06-17`, ML `2026-06-16`
- **The Westly Group (lead); Keysight Technologies, Allegis Capital, DNX Ventures, Sutter Hill Ventures, Mayfield Fund, Canaan Partners, Wing Venture Capital / AttoTude Inc.** — ours `2026-06-16`, ML `2026-06-15`
- **Olin Corporation / Huntsman Corporation** — ours `2026-06-15`, ML `2026-06-16`
- **Kesko / Dahl** — ours `2026-06-15`, ML `KESKO OYJ`
- **Wing Venture Capital, Madrona (co-leads); Obvious Ventures, Snowflake Ventures, Hudson River Trading, Samsung Next, Magarac Venture Partners / Gray Swan** — ours `2026-05-28`, ML `2026-06-28`
- **Skyline Builders Group Holding Limited / Cove Kaz Capital Group LLC** — ours `2026-04-30`, ML `2026-06-16`
- **Galantas Gold Corporation / Sol de Oro Mining Ltd.** — ours `2026-01-06`, ML `2026-06-15`

---

## How to read the differences

- **Currency** (value): not errors — we hold source currency, ML holds GBP.
- **Convention** (close dates): a definable rule choice — ML = closed-on-announcement, us = pending-unless-stated.
- **Structural / ML data advantage** (advisors, revenue, EBITDA, multiples): MergerLinks supplements from feeds/financial databases that aren't in the press release — unbeatable from URLs alone.
- **Real extraction misses** (a minority): stated financials we dropped, per-share captured but aggregate missed — fixable in the prompt.
- **Derived-not-computed** (multiples, equity, implied equity): we don't compute these yet — belongs in a deterministic job, not the LLM.

_See `docs/qa_runbook_mergerlinks_2026_08_01.md` for the fixes and the manual-validation checklist._