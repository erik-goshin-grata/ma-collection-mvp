# Agreement Capitalization Extraction Prompt

**Version:** 0.2 (provenance is caller-owned)
**Repo path:** `prompts/agreement_capitalization.md`

---

## 1. Purpose

Extract per-security-type and per-class capitalization data from the CAPITALIZATION section of a deal document. Produces one row per (security_type, security_class) pair for insertion into `transaction_security`.

Runs in Stage 11 (agreement_extract) for each CAPITALIZATION section in a deal document (filing_type IN 8K_EXHIBIT_21, DEFM14A, S4, SC_TOT, DEFA14A) with confidence HIGH or MEDIUM.

---

## 2. Model & Parameters

- **Model:** `claude-opus-4-5`
- **Temperature:** 0.0
- **Max tokens:** 2048

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
You are extracting structured capitalization data from the Capitalization section of a deal document. This section discloses the target company's outstanding securities by class and type.

OUTPUT FORMAT

Return a "securities" array. One entry per (security_type, security_class) pair. When multiple classes exist within a type (e.g., Class A Common + Class B Common), produce separate entries for each class.

SECURITY TYPES — normalized enum:
- COMMON_STOCK: voting common shares; may have multiple classes (Class A, Class B)
- PREFERRED_STOCK: preferred shares; may have multiple series with different conversion rights
- OPTIONS: stock options with strike prices (exercise prices)
- RSU: time-vested restricted stock units
- PSU: performance-vested restricted stock units (only when source explicitly separates from RSU)
- DSU: deferred stock units (typically director compensation awards)
- SAR: stock appreciation rights; may settle in cash or stock
- WARRANT: warrants to purchase stock (often pre-IPO investor instruments)
- CONVERTIBLE_NOTE: convertible debt instruments
- OTHER: phantom stock, cash-settled awards, anything above doesn't fit

When the source combines types (e.g., "Stock Awards" includes RSUs and PSUs without breakdown), use RSU and note the inclusion in the notes field.

security_class: normalized class label ("Class A", "Class B", "Series C") or null for single-class types.

shares_outstanding_as_of: the specific date these counts are as-of, in YYYY-MM-DD format. Use null when source gives only a general reference without a specific date.

weighted_avg_strike_price: for OPTIONS, SARs, and WARRANTs only — the stated weighted average exercise/strike price. Null for all other types.

consideration_treatment: how this security converts in the merger:
- CASH_OUT: holders receive cash
- CONVERSION: converts to acquirer securities
- ASSUMED: acquirer assumes the award (options/RSUs continue vesting under acquirer plan)
- CANCELLED: award is cancelled, may or may not receive payment
- ROLLOVER: converts to equivalent award in acquirer plan
- OTHER: any other treatment
Leave null when this section only states share counts without discussing merger treatment (common — consideration treatment often lives in a separate Consideration section).

consideration_per_share and consideration_currency: only when this section explicitly states the per-security treatment amount.

RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{
  "securities": [
    {
      "security_type": "COMMON_STOCK",
      "security_type_as_reported": "Class A Common Stock, par value $0.01 per share",
      "security_class": "Class A",
      "shares_outstanding": 245000000,
      "shares_outstanding_as_of": "2026-04-15",
      "weighted_avg_strike_price": null,
      "consideration_treatment": null,
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": null
    }
  ],
  "model_confidence": "HIGH",
  "notes": null
}

All fields are required. Use null for optional fields that have no value.
```

---

## 5. User Prompt Template

```
Extract capitalization data from the following deal document section.

SECTION TEXT:
{section_text}

```

---

## 6. Output Schema

| Field | Type | Notes |
| :--- | :--- | :--- |
| `securities` | array | One element per (security_type, security_class) |
| `securities[].security_type` | enum | COMMON_STOCK \| PREFERRED_STOCK \| OPTIONS \| RSU \| PSU \| DSU \| SAR \| WARRANT \| CONVERTIBLE_NOTE \| OTHER |
| `securities[].security_type_as_reported` | string\|null | Verbatim name from source |
| `securities[].security_class` | string\|null | "Class A", "Series C", null for single-class |
| `securities[].shares_outstanding` | integer\|null | Share count |
| `securities[].shares_outstanding_as_of` | string\|null | YYYY-MM-DD |
| `securities[].weighted_avg_strike_price` | number\|null | For options/SARs/warrants |
| `securities[].consideration_treatment` | enum\|null | CASH_OUT \| CONVERSION \| ASSUMED \| CANCELLED \| ROLLOVER \| OTHER |
| `securities[].consideration_per_share` | number\|null | Per-security consideration |
| `securities[].consideration_currency` | string\|null | ISO 4217 |
| `securities[].notes` | string\|null | Caveats (≤200 chars) |
| `model_confidence` | enum | HIGH \| MEDIUM \| LOW \| NONE |
| `notes` | string\|null | Section-level caveats |

---

## 7. Few-Shot Examples

### Example 1 — Dual-class common stock with options and RSUs

**Input section text:**
```
Section 3.4 Capitalization. As of March 31, 2026, the authorized capital stock of the Company consists of: (i) 800,000,000 shares of Class A Common Stock, par value $0.001 per share, of which 312,450,000 shares were issued and outstanding; (ii) 200,000,000 shares of Class B Common Stock, par value $0.001 per share, of which 87,200,000 shares were issued and outstanding; and (iii) 50,000,000 shares of Preferred Stock, par value $0.001 per share, none of which were issued and outstanding.

As of March 31, 2026, there were outstanding: (a) options to purchase an aggregate of 18,340,000 shares of Class A Common Stock, with a weighted average exercise price of $14.22 per share; and (b) 22,100,000 restricted stock units representing the right to receive shares of Class A Common Stock upon vesting.
```

**Expected output:**
```json
{
  "securities": [
    {
      "security_type": "COMMON_STOCK",
      "security_type_as_reported": "Class A Common Stock, par value $0.001 per share",
      "security_class": "Class A",
      "shares_outstanding": 312450000,
      "shares_outstanding_as_of": "2026-03-31",
      "weighted_avg_strike_price": null,
      "consideration_treatment": null,
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": null
    },
    {
      "security_type": "COMMON_STOCK",
      "security_type_as_reported": "Class B Common Stock, par value $0.001 per share",
      "security_class": "Class B",
      "shares_outstanding": 87200000,
      "shares_outstanding_as_of": "2026-03-31",
      "weighted_avg_strike_price": null,
      "consideration_treatment": null,
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": null
    },
    {
      "security_type": "OPTIONS",
      "security_type_as_reported": "options to purchase Class A Common Stock",
      "security_class": "Class A",
      "shares_outstanding": 18340000,
      "shares_outstanding_as_of": "2026-03-31",
      "weighted_avg_strike_price": 14.22,
      "consideration_treatment": null,
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": null
    },
    {
      "security_type": "RSU",
      "security_type_as_reported": "restricted stock units representing right to receive Class A Common Stock",
      "security_class": null,
      "shares_outstanding": 22100000,
      "shares_outstanding_as_of": "2026-03-31",
      "weighted_avg_strike_price": null,
      "consideration_treatment": null,
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": null
    }
  ],
  "model_confidence": "HIGH",
  "notes": null
}
```

### Example 2 — Single-class common with merger treatment stated

**Input section text:**
```
Section 5.3 Capitalization. As of April 15, 2026, the Company had issued and outstanding 45,238,100 shares of Common Stock, par value $0.01 per share. At the Effective Time, each such share shall be converted into the right to receive $42.00 in cash (the "Merger Consideration"), and no shares of Common Stock shall remain outstanding after the Effective Time.

There were no shares of Company Preferred Stock issued and outstanding. As of April 15, 2026, there were 1,820,000 shares of Common Stock subject to outstanding Company Stock Options with a weighted average exercise price of $28.60 per share, all of which shall be cancelled at the Effective Time in exchange for the Merger Consideration less the applicable exercise price.
```

**Expected output:**
```json
{
  "securities": [
    {
      "security_type": "COMMON_STOCK",
      "security_type_as_reported": "Common Stock, par value $0.01 per share",
      "security_class": null,
      "shares_outstanding": 45238100,
      "shares_outstanding_as_of": "2026-04-15",
      "weighted_avg_strike_price": null,
      "consideration_treatment": "CASH_OUT",
      "consideration_per_share": 42.00,
      "consideration_currency": "USD",
      "notes": null
    },
    {
      "security_type": "OPTIONS",
      "security_type_as_reported": "Company Stock Options",
      "security_class": null,
      "shares_outstanding": 1820000,
      "shares_outstanding_as_of": "2026-04-15",
      "weighted_avg_strike_price": 28.60,
      "consideration_treatment": "CANCELLED",
      "consideration_per_share": null,
      "consideration_currency": null,
      "notes": "Cancelled at Effective Time; holders receive Merger Consideration minus exercise price (net settlement)."
    }
  ],
  "model_confidence": "HIGH",
  "notes": null
}
```

---

## 8. Failure Modes

| Failure | Handling |
| :--- | :--- |
| Section states authorized shares only, not outstanding | Return shares_outstanding null; note authorized count in notes field |
| Combined award types ("Stock Awards" — RSU + PSU combined) | Use RSU type; note combined in per-security notes field |
| Share counts given as ranges or estimates | Use the stated number; note uncertainty in notes |

---

## 9. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-05-04 | Initial version — per-class capitalization |
| 0.2 | 2026-08-21 | **Prompt provenance is caller-owned (no response contract change beyond this).** `prompt_version` is removed from the response schema, the worked examples and the `{prompt_version}` line from the user template. The stage passes the authoritative version to `call_prompt` and stamps it on the row; the model was never told which version ran, so its answer could only come from a worked example — which is how `aggregation_conflict_log.prompt_version` recorded a version that had not run. See `prompts/prompt_conventions.md` 0.5. |
