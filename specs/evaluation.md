# Evaluation Spec

**Version:** 0.1 (draft)
**Repo path:** `specs/evaluation.md`

---

## 1. Purpose

Defines the gold-set methodology for measuring pipeline output quality. The operator grades extractions against source truth; Claude does not self-evaluate. This avoids the well-known failure mode of LLMs agreeing with their own outputs when asked to check them.

---

## 2. Gold Set Construction

The gold set is a CSV file (`eval/gold_set_<date>.csv`) containing operator-labeled truth for a sample of the 100 MVP PRs.

### 2.1 Sampling strategy

From each production run, select the sample as follows:

| Dimension | Coverage |
| :--- | :--- |
| All relevant PRs | Full coverage on core fields |
| Date / rationale | 20 of 100 PRs spot-checked |
| Irrelevant PRs | Sample 10 of 100 to check for false negatives |

Rationale for tiered coverage: parties / deal_type / value are the fields that drive downstream data value. Errors there matter most and deserve full verification. Dates and rationales are secondary in the MVP scope — a 20% sample balances signal against labeling effort. Irrelevant PRs need spot-checking to catch cases where relevancy filter wrongly drops an in-scope deal.

### 2.2 Labeling protocol

For each sampled PR, the operator:

1. Reads the source text (in `source_raw.clean_text`).
2. Records the correct value for each graded field.
3. Notes ambiguity where present (some fields genuinely cannot be determined from the text).
4. Marks the row as `labeled = true`.

Labeling happens in a spreadsheet tool (Excel, Google Sheets, or direct CSV edit). No in-pipeline UI for MVP.

### 2.3 What gets labeled (full-coverage fields)

For every relevant PR:

- `target_name`
- `acquirer_name`
- `parent_seller_name` (if applicable)
- `deal_type` (7-type enum)
- `target_type` (3-type enum)
- `target_status`
- `event_type`
- `value_amount` (numeric, in source currency)
- `value_currency`
- `value_type` (4-type enum)
- `announced_date`
- `closed_date` (if stated)
- `consideration_type` (5-type enum: CASH, STOCK, CASH_AND_STOCK, ELECTION, OTHER)

### 2.4 What gets labeled (spot-check fields, 20/100)

- `target_revenue` + period type + period end
- `target_ebitda` + period type + period end
- Strategic rationale (primary)
- Advisors (count; names not individually verified)

### 2.5 Handling irresolvable fields

Some fields are genuinely not in the source. The gold set distinguishes:

- `null` — field is not in source, extraction should also be null
- `UNDISCLOSED` — source explicitly says "terms not disclosed"
- `AMBIGUOUS` — source mentions but text is unclear

Operator marks each field's state. Downstream scoring treats null and UNDISCLOSED as distinct outcomes.

---

## 3. Gold Set CSV Format

See `eval/gold_set_template.csv` for the empty template. Columns:

```
source_raw_id
title
published_date
labeled                # true/false; filter on this for scoring
target_name
acquirer_name
parent_seller_name
deal_type
spin_split_type
distribution_mechanism
target_type
target_status
event_type
value_amount
value_currency
value_type
announced_date
closed_date
consideration_type
target_revenue
target_revenue_period
target_ebitda
target_ebitda_period
primary_rationale
advisor_count
notes
```

`notes` is a free-text column for operator comments (e.g., "value stated as 'approximately $500M' — captured 500M").

---

## 4. Scoring

### 4.1 Per-field precision

For each graded field, compute:

```
correct / (correct + incorrect) = precision
```

Where:
- **correct:** pipeline output matches gold label.
- **incorrect:** pipeline output differs from gold label (including pipeline producing a value when gold is null, or vice versa).

Numeric fields (value_amount, target_revenue, target_ebitda): allow ± 1% tolerance to accommodate rounding in extraction. Below that, exact match required.

Date fields: exact match required. A one-day variance is an error.

String fields (names): fuzzy match with rapidfuzz token_set_ratio ≥ 90 counts as correct. Below that is an error.

Enum fields (deal_type, value_type, etc.): exact match required.

### 4.2 Scorecard format

Output to `eval/scorecard_<run_id>.md`:

```markdown
# Pipeline Scorecard — run_20260423_120000

## Summary
- Total PRs: 100
- Relevant (pipeline): 78
- Relevant (gold sample): 10 of 10 correctly labeled relevant; 1 false negative caught in irrelevant sample (see below)

## Per-field precision (full coverage)

| Field | Correct | Incorrect | Precision | Target | Status |
| :--- | ---: | ---: | ---: | :--- | :--- |
| target_name | 76 | 2 | 97.4% | > 95% | PASS |
| acquirer_name | 77 | 1 | 98.7% | > 95% | PASS |
| deal_type | 72 | 6 | 92.3% | > 90% | PASS |
| value_amount | 71 | 7 | 91.0% | > 90% | PASS |
| value_type | 68 | 10 | 87.2% | > 90% | FAIL |
| announced_date | 77 | 1 | 98.7% | > 98% | PASS |

## Per-field precision (spot check, 20 of 100)

| Field | Correct | Incorrect | Precision |
| :--- | ---: | ---: | ---: |
| target_revenue | 15 | 2 | 88.2% |
| primary_rationale | 16 | 4 | 80.0% |

## Dedup

- Clusters formed: 71
- False merges (confirmed in gold): 0
- Missed merges (same deal in 2+ clusters): 2
- Dedup precision: 100% (0 false merges)
- Dedup recall: 72 / 74 = 97.3%

## Failures

- 2 PRs failed prompt (see logs/extract_xxx.log)

## Notes
- value_type FAIL driven by 10 cases where pipeline output TRANSACTION_VALUE and gold labeled EQUITY_VALUE. Review prompt §4 value_type_confidence rules.
- Two missed merges: close announcement released 90+ days after original; announced_date field is the close date not the original date. Extraction improvement, not clustering.
```

### 4.3 Scorecard generation

Run via `python eval/score.py --gold eval/gold_set_20260423.csv --run-id run_20260423_120000`. Reads the DB, joins gold set rows by `source_raw_id`, computes per-field metrics, writes the scorecard.

---

## 5. Regression Mode

When a prompt is revised (e.g., `deal_type_classifier:0.2` → `0.3`), regression testing ensures the new version doesn't degrade previously-correct outputs.

Workflow:

1. Save current gold set outputs to `eval/baseline_<prompt>_<version>.csv`.
2. Rerun the pipeline in `--mode=rerun-prompt --prompt=deal_type_classifier --version=0.3`.
3. New outputs land in staging with `is_current = true` and prompt_version = `:0.3`; old outputs flipped to `is_current = false`.
4. Run `python eval/regression.py --gold eval/gold_set_20260423.csv --old-version 0.2 --new-version 0.3`.
5. Output: per-field diff of 0.2 vs 0.3 against gold, highlighting new correct / new incorrect cases.

Acceptance: new version should never decrease precision by more than 2% on any full-coverage field without explicit operator approval.

---

## 6. Why the Operator Grades, Not Claude

Using an LLM to grade another LLM's output has a well-documented failure mode: the grading model agrees with the graded model's reasoning, even when the output is wrong, because both models share training and reasoning tendencies. This is especially acute when the two are the same model family (Opus grading Opus).

Operator grading is slower but provides ground truth that doesn't drift as the pipeline's prompts are iterated. A 100-PR gold set takes an afternoon to label manually; the signal it produces guides prompt revisions for weeks.

A human grader may still be biased or inconsistent, but the failure modes are different from the model's, which is what matters for catching regressions.

---

## 7. Gold Set Versioning

Gold set files are dated. As the operator labels more PRs or updates a label after discovering an error:

- Dated files (`gold_set_20260423.csv`, `gold_set_20260505.csv`) are additive — newer files extend older ones.
- The scorecard always references the specific gold set file used.
- Never edit a labeled row silently — if a label is corrected, note the correction in the `notes` column with a date.

This preserves an audit trail. If a scorecard changes, the operator can see whether the pipeline improved or the gold set was re-labeled.

---

## 8. Versioning

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
