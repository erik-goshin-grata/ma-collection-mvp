"""
prompts/base.py — shared Anthropic API infrastructure.

Every LLM-powered stage imports from here.  Nothing in this module writes to
staging_extraction; callers own row-status updates.

Public interface:
  call_prompt()             — central API call, retry, parse, log
  parse_json_response()     — extract JSON from raw model output
  log_prompt_failure()      — write to extraction_failure_log (never raises)
  register_prompt_version() — upsert into prompt_version
  load_prompt_file()        — parse prompts/*.md for system + user template
  PromptFailure             — exception carrying failure_type + raw_response
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from config import Config

_PROMPTS_DIR = Path(__file__).parent
_FAILURE_LOG_DIR = Path(__file__).parent.parent / "logs" / "prompt_failures"

# Match "## 4. System Prompt" or "## 5. User Prompt Template" section headers
# followed by one or more blank lines, then a ``` fence, then content, then ```.
_SYSTEM_RE = re.compile(
    r"##\s+4\.\s+System Prompt[^\n]*\n+```[^\n]*\n(.*?)\n```",
    re.DOTALL,
)
_USER_RE = re.compile(
    r"##\s+5\.\s+User Prompt Template[^\n]*\n+```[^\n]*\n(.*?)\n```",
    re.DOTALL,
)

_REFUSAL_RE = re.compile(
    r"\b(I cannot|I'm unable|I can't|I am unable|I refuse|I won't|"
    r"unable to|cannot assist|cannot provide|not able to)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PromptFailure(Exception):
    """Raised by call_prompt on any unrecoverable failure.

    Attributes
    ----------
    failure_type:
        One of: PARSE_ERROR | API_ERROR | TIMEOUT | REFUSAL | SCHEMA_VIOLATION
    raw_response:
        The raw text returned by the model (empty string on API/timeout errors).
    """

    def __init__(self, failure_type: str, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.failure_type = failure_type
        self.raw_response = raw_response


# ---------------------------------------------------------------------------
# Prompt file loading
# ---------------------------------------------------------------------------

def load_prompt_file(prompt_name: str) -> dict:
    """Read prompts/<prompt_name>.md and extract the system prompt and user template.

    Returns
    -------
    dict with keys:
        system         — system prompt text (stripped)
        user_template  — user prompt template with {placeholder} fields (stripped)
        file_hash      — SHA-256 hex digest of the raw file contents

    Raises
    ------
    FileNotFoundError
        If the prompt file does not exist.
    ValueError
        If the expected section headers or fences cannot be found.
    """
    path = _PROMPTS_DIR / f"{prompt_name}.md"
    content = path.read_text(encoding="utf-8")
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    system_m = _SYSTEM_RE.search(content)
    if not system_m:
        raise ValueError(
            f"Could not find '## 4. System Prompt' section with backtick fences in {path}"
        )

    user_m = _USER_RE.search(content)
    if not user_m:
        raise ValueError(
            f"Could not find '## 5. User Prompt Template' section with backtick fences in {path}"
        )

    return {
        "system": system_m.group(1).strip(),
        "user_template": user_m.group(1).strip(),
        "file_hash": file_hash,
    }


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def parse_json_response(raw_text: str) -> dict:
    """Extract the first valid JSON object from a model response.

    Handles:
    - Clean JSON (the expected normal case)
    - JSON wrapped in markdown fences (```json ... ``` or ``` ... ```)
    - JSON with a leading explanation paragraph or trailing commentary

    Raises
    ------
    PromptFailure(PARSE_ERROR)
        If no valid JSON object can be extracted after all attempts.
    """
    text = raw_text.strip()

    # Strip markdown fences if present
    defenced = re.sub(r"```(?:json)?\s*\n(.*?)\n?```", r"\1", text, flags=re.DOTALL).strip()

    for candidate in (defenced, text):
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    # Last resort: slice from first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            result = json.loads(text[start : end + 1])
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    raise PromptFailure("PARSE_ERROR", "No valid JSON object found in response", raw_text)


def _detect_failure_type(raw_text: str) -> str:
    """Return 'REFUSAL' if the text looks like a model refusal, else 'PARSE_ERROR'."""
    return "REFUSAL" if _REFUSAL_RE.search(raw_text) else "PARSE_ERROR"


# ---------------------------------------------------------------------------
# Failure logging
# ---------------------------------------------------------------------------

def log_prompt_failure(
    conn: sqlite3.Connection,
    *,
    source_raw_id: int | None,
    extraction_id: int | None,
    stage: str,
    failure_type: str,
    raw_response: str,
    error_message: str,
    prompt_version: str,
    run_id: str,
) -> None:
    """Insert a row into extraction_failure_log.  Never raises."""
    try:
        conn.execute(
            """
            INSERT INTO extraction_failure_log
                (source_raw_id, extraction_id, stage, failure_type,
                 raw_response, error_message, prompt_version, run_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_raw_id,
                extraction_id,
                stage,
                failure_type,
                raw_response[:10_000] if raw_response else None,
                error_message[:2_000] if error_message else None,
                prompt_version,
                run_id,
            ),
        )
        conn.commit()
    except Exception:
        pass  # failure logging must never cascade


def _write_failure_dump(stage: str, payload: dict) -> None:
    """Persist failure details to logs/prompt_failures/<stage>_<timestamp>.json."""
    try:
        _FAILURE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        (_FAILURE_LOG_DIR / f"{stage}_{ts}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Prompt version registry
# ---------------------------------------------------------------------------

def register_prompt_version(
    conn: sqlite3.Connection,
    prompt_name: str,
    version: str,
    prompt_text_hash: str,
) -> None:
    """Insert into prompt_version; silently no-ops if the (name, version) row exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO prompt_version
            (prompt_name, version, prompt_text_hash, first_used_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            prompt_name,
            version,
            prompt_text_hash,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def _resolve_model(model: str, cfg: Config) -> str:
    """Map 'opus'/'haiku' shortcuts to full model IDs; passthrough for full IDs."""
    if model == "opus":
        return cfg.opus_model
    if model == "haiku":
        return cfg.haiku_model
    return model


# ---------------------------------------------------------------------------
# Central API call
# ---------------------------------------------------------------------------

def call_prompt(
    *,
    prompt_name: str,
    prompt_version: str,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int,
    cfg: Config,
    conn: sqlite3.Connection,
    run_id: str,
    source_raw_id: int | None = None,
    extraction_id: int | None = None,
    log: Any = None,
) -> dict:
    """Call the Anthropic API and return the parsed JSON response.

    On any failure, writes to extraction_failure_log and raises PromptFailure.
    The caller is responsible for updating staging_extraction.status.

    Parameters
    ----------
    prompt_name:
        Identifies the prompt for logging and failure records
        (e.g., ``"relevancy_filter"``).
    prompt_version:
        Full version string included in the response
        (e.g., ``"relevancy_filter:0.1"``).
    user_prompt:
        Rendered user-role message — placeholders already substituted.
    system_prompt:
        System-role instruction text.
    model:
        ``"opus"`` or ``"haiku"`` (resolved via config) or a full model ID.
    temperature:
        Sampling temperature.  Default 0.0.
    max_tokens:
        Maximum completion tokens.
    cfg:
        Loaded pipeline Config (provides API key and model strings).
    conn:
        Open database connection used for failure logging.
    run_id:
        Current run identifier, written into failure records.
    source_raw_id / extraction_id:
        Optional row IDs attached to failure records.
    log:
        Logger with .debug / .info / .warning / .error methods.
        Falls back to the module-level ``logging.getLogger`` if None.

    Returns
    -------
    dict
        The parsed JSON object from the model response.

    Raises
    ------
    PromptFailure
        On any unrecoverable error (API failure, timeout, parse failure, refusal).
    """
    if log is None:
        log = logging.getLogger("prompts.base")

    model_id = _resolve_model(model, cfg)
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    def _fail(failure_type: str, message: str, raw: str = "") -> PromptFailure:
        log_prompt_failure(
            conn,
            source_raw_id=source_raw_id,
            extraction_id=extraction_id,
            stage=prompt_name,
            failure_type=failure_type,
            raw_response=raw,
            error_message=message,
            prompt_version=prompt_version,
            run_id=run_id,
        )
        _write_failure_dump(
            prompt_name,
            {
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
                "failure_type": failure_type,
                "error_message": message,
                "raw_response": raw[:5_000],
                "model": model_id,
                "run_id": run_id,
            },
        )
        log.warning(
            "%s:%s → %s → FAIL:%s", prompt_name, prompt_version, model_id, failure_type
        )
        return PromptFailure(failure_type, message, raw)

    raw_response = ""
    message: anthropic.types.Message | None = None

    for attempt in range(2):
        t0 = time.monotonic()
        try:
            message = client.messages.create(
                model=model_id,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            # Auth failures are fatal — halt the pipeline immediately.
            raise _fail("API_ERROR", f"Auth failure ({type(exc).__name__}): {exc}")
        except anthropic.RateLimitError as exc:
            if attempt == 0:
                log.warning("%s: 429 rate limit — retry in 5s", prompt_name)
                time.sleep(5)
                continue
            raise _fail("API_ERROR", f"Rate limit after retry: {exc}")
        except anthropic.APITimeoutError as exc:
            if attempt == 0:
                log.warning("%s: timeout — retry in 10s", prompt_name)
                time.sleep(10)
                continue
            raise _fail("TIMEOUT", f"Timeout after retry: {exc}")
        except anthropic.APIStatusError as exc:
            # Covers InternalServerError (5xx) and any other HTTP status errors.
            if attempt == 0:
                log.warning(
                    "%s: API status error %s — retry in 5s", prompt_name, exc.status_code
                )
                time.sleep(5)
                continue
            raise _fail("API_ERROR", f"HTTP {exc.status_code} after retry: {exc}")
        except anthropic.APIConnectionError as exc:
            if attempt == 0:
                log.warning("%s: connection error — retry in 5s", prompt_name)
                time.sleep(5)
                continue
            raise _fail("API_ERROR", f"Connection error after retry: {exc}")
        break  # try block completed without exception

    assert message is not None

    latency_ms = int((time.monotonic() - t0) * 1000)
    in_tokens = message.usage.input_tokens
    out_tokens = message.usage.output_tokens
    raw_response = message.content[0].text if message.content else ""

    log.debug(
        "%s:%s model=%s temperature=%s in=%d out=%d latency=%dms",
        prompt_name,
        prompt_version,
        model_id,
        temperature,
        in_tokens,
        out_tokens,
        latency_ms,
    )

    try:
        result = parse_json_response(raw_response)
    except PromptFailure:
        failure_type = _detect_failure_type(raw_response)
        raise _fail(failure_type, "Response was not parseable JSON", raw_response)

    log.info(
        "%s:%s → %s → OK (tokens: %d/%d, latency: %dms)",
        prompt_name,
        prompt_version,
        model_id,
        in_tokens,
        out_tokens,
        latency_ms,
    )

    return result
