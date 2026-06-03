"""
Provider-aware LLM client used by prompts/base.py.

The pipeline stages keep calling prompts.base.call_prompt().  This module owns
the provider-specific API details so Anthropic and OpenAI can share one internal
interface.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from config import Config


@dataclass(frozen=True)
class LLMRequest:
    prompt_name: str
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    model_id: str
    raw_text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMProviderError(Exception):
    """Raised when the selected provider cannot complete an LLM request."""

    def __init__(self, failure_type: str, message: str) -> None:
        super().__init__(message)
        self.failure_type = failure_type


_ANTHROPIC_NO_TEMPERATURE_PREFIXES = ("claude-opus-4",)

_OPENAI_MODEL_BY_PROMPT: dict[str, str] = {
    "relevancy_filter": "openai_relevancy_model",
    "deal_type_classifier": "openai_classification_model",
    "high_confidence_extraction": "openai_extract_model",
    "low_confidence_extraction": "openai_reasoning_model",
    "agreement_recitals": "openai_legal_extract_model",
    "agreement_consideration": "openai_legal_extract_model",
    "agreement_capitalization": "openai_legal_extract_model",
    "agreement_termination": "openai_legal_extract_model",
    "agreement_conditions": "openai_legal_extract_model",
    "aggregation": "openai_classification_model",
    "deal_summary": "openai_classification_model",
    "strategic_rationale": "openai_reasoning_model",
}


def get_provider_name(cfg: Config) -> str:
    """Return the normalized provider name from config."""
    return getattr(cfg, "llm_provider", "anthropic").lower()


def resolve_model_id(cfg: Config, *, prompt_name: str, requested_model: str) -> str:
    """Resolve stage aliases to the selected provider's concrete model ID."""
    provider = get_provider_name(cfg)
    if provider == "anthropic":
        return _resolve_anthropic_model(cfg, requested_model)
    if provider == "openai":
        return _resolve_openai_model(cfg, prompt_name, requested_model)
    raise LLMProviderError("API_ERROR", f"Unsupported LLM provider: {provider!r}")


def instantiate_provider_client(cfg: Config) -> Any:
    """Instantiate the selected provider client without issuing an API request."""
    provider = get_provider_name(cfg)
    if provider == "anthropic":
        return _make_anthropic_client(cfg)
    if provider == "openai":
        return _make_openai_client(cfg)
    raise LLMProviderError("API_ERROR", f"Unsupported LLM provider: {provider!r}")


def call_llm(request: LLMRequest, *, cfg: Config, log: Any) -> LLMResponse:
    """Call the configured provider and return raw text plus usage metadata."""
    provider = get_provider_name(cfg)
    if provider == "anthropic":
        return _call_anthropic(request, cfg=cfg, log=log)
    if provider == "openai":
        return _call_openai(request, cfg=cfg, log=log)
    raise LLMProviderError("API_ERROR", f"Unsupported LLM provider: {provider!r}")


def _resolve_anthropic_model(cfg: Config, requested_model: str) -> str:
    if requested_model == "opus":
        return cfg.opus_model
    if requested_model == "haiku":
        return cfg.haiku_model
    return requested_model


def _resolve_openai_model(cfg: Config, prompt_name: str, requested_model: str) -> str:
    if requested_model not in {"opus", "haiku"}:
        return requested_model
    attr_name = _OPENAI_MODEL_BY_PROMPT.get(prompt_name)
    if attr_name:
        return getattr(cfg, attr_name)
    return cfg.openai_extract_model


def _make_anthropic_client(cfg: Config) -> Any:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMProviderError(
            "API_ERROR",
            "The 'anthropic' package is required when LLM_PROVIDER=anthropic.",
        ) from exc
    return anthropic.Anthropic(api_key=cfg.anthropic_api_key)


def _make_openai_client(cfg: Config) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMProviderError(
            "API_ERROR",
            "The 'openai' package is required when LLM_PROVIDER=openai.",
        ) from exc
    return OpenAI(api_key=cfg.openai_api_key)


def _anthropic_supports_temperature(model_id: str) -> bool:
    return not any(model_id.startswith(p) for p in _ANTHROPIC_NO_TEMPERATURE_PREFIXES)


def _call_anthropic(request: LLMRequest, *, cfg: Config, log: Any) -> LLMResponse:
    try:
        import anthropic
    except ImportError as exc:
        raise LLMProviderError(
            "API_ERROR",
            "The 'anthropic' package is required when LLM_PROVIDER=anthropic.",
        ) from exc

    model_id = resolve_model_id(cfg, prompt_name=request.prompt_name, requested_model=request.model)
    client = _make_anthropic_client(cfg)
    api_kwargs: dict[str, Any] = {
        "model": model_id,
        "max_tokens": request.max_tokens,
        "system": request.system_prompt,
        "messages": [{"role": "user", "content": request.user_prompt}],
    }
    if _anthropic_supports_temperature(model_id):
        api_kwargs["temperature"] = request.temperature

    message = None
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            message = client.messages.create(**api_kwargs)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMProviderError("API_ERROR", f"Auth failure ({type(exc).__name__}): {exc}") from exc
        except anthropic.RateLimitError as exc:
            if attempt == 0:
                log.warning("%s: 429 rate limit - retry in 5s", request.prompt_name)
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"Rate limit after retry: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            if attempt == 0:
                log.warning("%s: timeout - retry in 10s", request.prompt_name)
                time.sleep(10)
                continue
            raise LLMProviderError("TIMEOUT", f"Timeout after retry: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if attempt == 0:
                log.warning(
                    "%s: API status error %s - retry in 5s",
                    request.prompt_name,
                    exc.status_code,
                )
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"HTTP {exc.status_code} after retry: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            if attempt == 0:
                log.warning("%s: connection error - retry in 5s", request.prompt_name)
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"Connection error after retry: {exc}") from exc
        break

    if message is None:
        raise LLMProviderError("API_ERROR", "Anthropic response was empty.")

    latency_ms = int((time.monotonic() - t0) * 1000)
    raw_text = message.content[0].text if message.content else ""
    return LLMResponse(
        provider="anthropic",
        model_id=model_id,
        raw_text=raw_text,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        latency_ms=latency_ms,
    )


def _call_openai(request: LLMRequest, *, cfg: Config, log: Any) -> LLMResponse:
    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )
    except ImportError as exc:
        raise LLMProviderError(
            "API_ERROR",
            "The 'openai' package is required when LLM_PROVIDER=openai.",
        ) from exc

    model_id = resolve_model_id(cfg, prompt_name=request.prompt_name, requested_model=request.model)
    client = _make_openai_client(cfg)
    api_kwargs: dict[str, Any] = {
        "model": model_id,
        "input": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        "max_output_tokens": request.max_tokens,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": _safe_schema_name(request.prompt_name),
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "strict": False,
            }
        },
    }

    response = None
    for attempt in range(2):
        t0 = time.monotonic()
        try:
            response = client.responses.create(**api_kwargs)
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise LLMProviderError("API_ERROR", f"Auth failure ({type(exc).__name__}): {exc}") from exc
        except RateLimitError as exc:
            if attempt == 0:
                log.warning("%s: 429 rate limit - retry in 5s", request.prompt_name)
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"Rate limit after retry: {exc}") from exc
        except APITimeoutError as exc:
            if attempt == 0:
                log.warning("%s: timeout - retry in 10s", request.prompt_name)
                time.sleep(10)
                continue
            raise LLMProviderError("TIMEOUT", f"Timeout after retry: {exc}") from exc
        except APIStatusError as exc:
            if attempt == 0:
                log.warning(
                    "%s: API status error %s - retry in 5s",
                    request.prompt_name,
                    exc.status_code,
                )
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"HTTP {exc.status_code} after retry: {exc}") from exc
        except APIConnectionError as exc:
            if attempt == 0:
                log.warning("%s: connection error - retry in 5s", request.prompt_name)
                time.sleep(5)
                continue
            raise LLMProviderError("API_ERROR", f"Connection error after retry: {exc}") from exc
        break

    if response is None:
        raise LLMProviderError("API_ERROR", "OpenAI response was empty.")

    latency_ms = int((time.monotonic() - t0) * 1000)
    input_tokens, output_tokens = _extract_openai_usage(response)
    return LLMResponse(
        provider="openai",
        model_id=model_id,
        raw_text=_extract_openai_text(response),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )


def _safe_schema_name(prompt_name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", prompt_name).strip("_")
    return (name or "ma_collection_response")[:64]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_openai_usage(response: Any) -> tuple[int, int]:
    usage = _field(response, "usage")
    input_tokens = _field(usage, "input_tokens", 0)
    output_tokens = _field(usage, "output_tokens", 0)
    return int(input_tokens or 0), int(output_tokens or 0)


def _extract_openai_text(response: Any) -> str:
    output_text = _field(response, "output_text")
    if output_text:
        return str(output_text)

    chunks: list[str] = []
    for item in _field(response, "output", []) or []:
        for content in _field(item, "content", []) or []:
            text = _field(content, "text")
            if text is not None:
                chunks.append(str(text))
    return "".join(chunks)
