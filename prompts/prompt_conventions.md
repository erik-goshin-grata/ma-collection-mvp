# Prompt Conventions

**Version:** 0.3 (V2 alignment)
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
| Deal type classification | Sonnet 4.6 | Fixed-enum single pick, temp 0.0 — mid-tier handles it; downgraded from Opus 2026-08-02 |
| High-confidence extraction | Sonnet 4.6 | Explicit-fact extraction (pattern-matching, not judgment); downgraded from Opus 2026-08-02 |
| Low-confidence extraction | Opus 4.7 | Nuanced fields (advisors, consideration), signal quality matters |
| Aggregation conflict resolution | Opus 4.7 | Judgment calls on source reconciliation |
| Deal summary | Sonnet 4.6 | Prose over already-extracted facts, temp 0.3 — mid-tier task; downgraded from Opus 2026-08-02 |
| Strategic rationale | Opus 4.7 | Judgment against taxonomy |

Exact model strings (for API calls): `claude-opus-4-7`, `claude-sonnet-4-6`, and `claude-haiku-4-5-20251001`.
(Funding HC extraction, stage 4b, follows high-confidence extraction → Sonnet 4.6.)
Implementation reads these from `Config.opus_model` and `Config.haiku_model` — never
hardcoded in prompt code. Model strings change; config values do not require code changes.

**OpenAI provider:** When `LLM_PROVIDER=openai`, the pipeline uses a separate model
hierarchy defined in config:
- `openai_relevancy_model` — high-volume classification (e.g., `gpt-5-nano`)
- `openai_classification_model` — deal type, event category routing (e.g., `gpt-5-mini`)
- `openai_extract_model` — HC/LC extraction (e.g., `gpt-5-mini`)
- `openai_legal_extract_model` — agreement extraction prompts (e.g., `gpt-5.2`)
- `openai_reasoning_model` — conflict resolution, rationale (e.g., `gpt-5.2`)

**Model upgrade policy:** Each model version bump requires validation against the existing
prompt suite before changing the default. Breaking changes observed in Opus 4.7+: tokenizer
produces 1.0–1.35x more tokens for the same text; model follows instructions more literally
— existing prompts may behave differently. Test on a known validation DB before promoting.

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
| 0.2 | 2026-04-23 | Added §11 Prompt File Structure Rule (RESPONSE FORMAT requirement). |
| 0.3 | 2026-07-28 | V2 alignment. Model strings updated: claude-opus-4-5 → claude-opus-4-7, claude-haiku-4-5 → claude-haiku-4-5-20251001. Model upgrade policy added. OpenAI provider model hierarchy documented. Stage table updated to reflect current defaults. |

---

## 11. Prompt File Structure Rule

Each prompt file's **§ 4 System Prompt** fence must end with a `RESPONSE FORMAT` block containing a concrete JSON example of the expected response shape. This is the text that `load_prompt_file()` ships to the model at runtime.

**§ 6 Output Schema** remains as human-facing documentation — field definitions, enum expansions, field notes — and is **not** extracted by `load_prompt_file()` and therefore **not** sent to the model.

When adding a new prompt file or revising an existing one, update both the `RESPONSE FORMAT` block in § 4 and the JSON example in § 6 together; they must stay in sync.

**Required format** (place immediately before the closing fence in § 4):

```
RESPONSE FORMAT

Return a single JSON object with exactly these fields. No prose, no Markdown code fences, no preamble.

{ ... concrete JSON example ... }

All fields are required. Use null for optional fields that have no value. "prompt_version" is returned unchanged from the value passed in the user prompt.
```
