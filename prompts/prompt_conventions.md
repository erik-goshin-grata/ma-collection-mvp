# Prompt Conventions

**Version:** 0.1 (draft)
**Repo path:** `prompts/prompt_conventions.md`

Conventions that apply to every prompt file in this directory. Each individual prompt file references this document by default and only specifies deviations.

---

## 1. File Structure

Each prompt file has the following sections:

1. **Purpose** — what pipeline stage it serves and what decision it makes.
2. **Model & Parameters** — Opus vs Haiku, temperature, max_tokens.
3. **Input Schema** — the exact JSON the caller passes to the prompt.
4. **System Prompt** — the instruction text sent as the system role.
5. **User Prompt Template** — the user-role template with field placeholders.
6. **Output Schema** — the exact JSON the model must return.
7. **Few-Shot Examples** — representative inputs and correct outputs.
8. **Failure Modes** — known bad behaviors and how the parser handles them.
9. **Versioning** — change history for this prompt.

---

## 2. Model Selection

| Stage | Model | Rationale |
| :--- | :--- | :--- |
| Relevancy filter | Haiku 4.5 | High volume, low stakes, cheap |
| Deal type classification | Opus 4.5 | Single decision, downstream branches depend on it |
| High-confidence extraction | Opus 4.5 | Core fields, must be accurate |
| Low-confidence extraction | Opus 4.5 | Nuanced fields (advisors, consideration), signal quality matters |
| Aggregation conflict resolution | Opus 4.5 | Judgment calls on source reconciliation |
| Deal summary | Opus 4.5 | Readable prose output |
| Strategic rationale | Opus 4.5 | Judgment against taxonomy |

Exact model strings (for API calls): `claude-opus-4-5` and `claude-haiku-4-5`. Implementation should read these from configuration, not hardcode them in prompt code — model strings change.

---

## 3. Temperature

| Stage | Temp | Rationale |
| :--- | :--- | :--- |
| Relevancy filter | 0.0 | Binary classification, deterministic |
| Deal type classifier | 0.0 | Enum classification, deterministic |
| High-confidence extraction | 0.0 | Factual extraction, deterministic |
| Low-confidence extraction | 0.0 | Factual extraction, deterministic |
| Aggregation | 0.1 | Slight variance acceptable — tiny nudge from zero helps when tied sources need a tie-breaker |
| Deal summary | 0.3 | Natural prose needs some variance |
| Strategic rationale | 0.0 | Enum classification, deterministic |

---

## 4. JSON I/O Contract

Every prompt returns JSON. No prose, no Markdown fences, no preamble. The system prompt for every prompt includes this instruction verbatim:

> Return a single JSON object matching the schema. Do not include any text before or after the JSON. Do not wrap the JSON in Markdown code fences. Do not include comments.

If the model returns anything other than a parseable JSON object:
1. The parser logs the raw response to `logs/prompt_failures/<stage>_<timestamp>.json`.
2. The row is marked `staging_extraction.status = PROMPT_FAILED`.
3. The orchestrator continues with the next row. No halt on individual failures.

---

## 5. Field Conventions

Every JSON output includes these meta fields in addition to the stage-specific fields:

| Field | Type | Purpose |
| :--- | :--- | :--- |
| `prompt_version` | string | Semantic version of the prompt that produced the output (e.g., `"relevancy_filter:0.1"`). Set by the caller before the prompt runs, returned unchanged by the model. |
| `model_confidence` | enum: `HIGH` / `MEDIUM` / `LOW` / `NONE` | Self-assessed confidence. `NONE` if the model cannot make a determination at all. |
| `notes` | string or null | Freeform notes from the model — ambiguities, assumptions, caveats. Short (≤200 chars). Omitted if nothing worth noting. |

`model_confidence` semantics:
- `HIGH` — explicit, unambiguous evidence in the text.
- `MEDIUM` — inference from partial evidence.
- `LOW` — best guess from weak signal.
- `NONE` — field not determinable from the input.

Stage-specific fields override nothing here. If a stage needs to express uncertainty at a finer grain, it uses its own fields on top of `model_confidence`.

---

## 6. Null Handling

- Missing or unknowable values are returned as JSON `null`, not empty strings, not `"UNKNOWN"`, not `"N/A"`.
- Exceptions: fields whose enum includes `UNKNOWN` as a valid value (e.g., `deal_type`) use the enum, not null. See individual prompt files.
- The parser treats `null` as a genuine absence of signal. `UNKNOWN` as an enum value means "the model considered and could not determine."

This distinction matters for downstream: `null` means no evidence was present; `UNKNOWN` means evidence was ambiguous. Different signals for different pipeline decisions.

---

## 7. Error Posture

No prompt can halt the pipeline. If a prompt fails, the orchestrator:

1. Logs the failure mode (parse error, API error, timeout, refusal).
2. Marks the staging row with the appropriate status.
3. Continues to the next row.

Prompts are written defensively:
- No instruction tells the model to "ask a clarifying question."
- No instruction tells the model to "refuse if unsure."
- Uncertainty is expressed through `model_confidence` and enum values like `UNKNOWN`, never through refusal.

---

## 8. Versioning

Each prompt file has a semantic version (e.g., `0.1`, `0.2`, `1.0`). Version increments when:

- **Patch (0.1 → 0.1.1):** typo fix, clarification, no behavior change expected.
- **Minor (0.1 → 0.2):** behavior change, new field, new enum value.
- **Major (0.1 → 1.0):** breaking schema change, rerun required on historical data.

The `prompt_version` table in the schema records each version used in production. The `staging_extraction.prompt_version` column records which version produced each row. This lets us rerun old rows through new prompts and compare, without losing the original output.

---

## 9. Few-Shot Example Discipline

Few-shot examples are part of the prompt, not decorative. They demonstrate:

- A typical case the model should handle cleanly.
- An edge case where the instruction matters.
- A failure mode the model is prone to (shown as a correct handling of what could go wrong).

Number of examples: 2–4 per prompt. More examples cost tokens without proportional lift.

All examples are synthetic. No real press release text, no real company data, no real filings. This keeps the prompt file distributable and avoids inadvertently biasing the model toward specific companies in training memory.

---

## 10. Document Control

| Version | Date | Change |
| :--- | :--- | :--- |
| 0.1 | 2026-04-22 | Initial draft |
