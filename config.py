"""
Configuration loader for the M&A Collection MVP pipeline.

Reads environment variables from a .env file via python-dotenv and exposes a
frozen Config dataclass. Missing required variables raise ConfigurationError
at load time so failures are caught before any pipeline work starts.

Spec references: specs/pipeline.md §7, .env.example
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (same directory as this file).
# override=False: real environment variables take precedence over .env values.
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


class ConfigurationError(Exception):
    """Raised when a required env var is missing or a value is invalid."""


@dataclass(frozen=True)
class Config:
    # --- Required / provider-selected ---
    llm_provider: str
    anthropic_api_key: str
    openai_api_key: str
    sec_api_key: str
    operator_contact_email: str

    # --- PR Newswire adapter ---
    max_fetches: int
    request_delay_seconds: float
    user_agent_string: str

    # --- sec-api.io adapter ---
    sec_api_request_delay_seconds: float
    sec_date_window_days: int
    sec_extractor_max_retries: int
    sec_extractor_retry_delay_ms: int

    # --- Database & logging ---
    db_path: str
    log_level: str
    run_id_prefix: str

    # --- Model strings ---
    opus_model: str
    haiku_model: str
    openai_relevancy_model: str
    openai_classification_model: str
    openai_extract_model: str
    openai_legal_extract_model: str
    openai_reasoning_model: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"Required environment variable '{name}' is missing or empty. "
            "Copy .env.example to .env and fill in your values."
        )
    return value


def _opt_str(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def _opt_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(
            f"Environment variable '{name}' must be an integer, got: {raw!r}"
        )


def _opt_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(
            f"Environment variable '{name}' must be a number, got: {raw!r}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_config() -> Config:
    """Load and validate all configuration from environment variables.

    Returns a frozen Config dataclass. Raises ConfigurationError if a
    required variable is absent or a value fails validation.
    """
    llm_provider = _opt_str("LLM_PROVIDER", "anthropic").lower()
    if llm_provider not in {"anthropic", "openai"}:
        raise ConfigurationError(
            "LLM_PROVIDER must be one of: 'anthropic', 'openai'. "
            f"Got: {llm_provider!r}"
        )

    anthropic_api_key = (
        _require("ANTHROPIC_API_KEY")
        if llm_provider == "anthropic"
        else _opt_str("ANTHROPIC_API_KEY", "")
    )
    openai_api_key = (
        _require("OPENAI_API_KEY")
        if llm_provider == "openai"
        else _opt_str("OPENAI_API_KEY", "")
    )
    sec_api_key = _require("SEC_API_KEY")
    operator_contact_email = _require("OPERATOR_CONTACT_EMAIL")

    max_fetches = _opt_int("MAX_FETCHES", 100)
    request_delay_seconds = _opt_float("REQUEST_DELAY_SECONDS", 1.5)
    # USER_AGENT_STRING defaults to a polite crawler string derived from the email.
    user_agent_string = _opt_str(
        "USER_AGENT_STRING",
        f"MA-Collection-MVP/0.1 (contact: {operator_contact_email})",
    )

    sec_api_request_delay_seconds = _opt_float("SEC_API_REQUEST_DELAY_SECONDS", 0.2)
    sec_date_window_days = _opt_int("SEC_DATE_WINDOW_DAYS", 7)
    sec_extractor_max_retries = _opt_int("SEC_EXTRACTOR_MAX_RETRIES", 3)
    sec_extractor_retry_delay_ms = _opt_int("SEC_EXTRACTOR_RETRY_DELAY_MS", 750)

    db_path = _opt_str("DB_PATH", "data/ma_mvp.db")
    log_level = _opt_str("LOG_LEVEL", "INFO").upper()
    run_id_prefix = _opt_str("RUN_ID_PREFIX", "")

    opus_model = _opt_str("OPUS_MODEL", "claude-opus-4-7")
    haiku_model = _opt_str("HAIKU_MODEL", "claude-haiku-4-5-20251001")
    openai_relevancy_model = _opt_str("OPENAI_RELEVANCY_MODEL", "gpt-5-nano")
    openai_classification_model = _opt_str("OPENAI_CLASSIFICATION_MODEL", "gpt-5-mini")
    openai_extract_model = _opt_str("OPENAI_EXTRACT_MODEL", "gpt-5-mini")
    openai_legal_extract_model = _opt_str("OPENAI_LEGAL_EXTRACT_MODEL", "gpt-5.2")
    openai_reasoning_model = _opt_str("OPENAI_REASONING_MODEL", "gpt-5.2")

    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level not in valid_levels:
        raise ConfigurationError(
            f"LOG_LEVEL must be one of {sorted(valid_levels)}, got: {log_level!r}"
        )

    return Config(
        llm_provider=llm_provider,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        sec_api_key=sec_api_key,
        operator_contact_email=operator_contact_email,
        max_fetches=max_fetches,
        request_delay_seconds=request_delay_seconds,
        user_agent_string=user_agent_string,
        sec_api_request_delay_seconds=sec_api_request_delay_seconds,
        sec_date_window_days=sec_date_window_days,
        sec_extractor_max_retries=sec_extractor_max_retries,
        sec_extractor_retry_delay_ms=sec_extractor_retry_delay_ms,
        db_path=db_path,
        log_level=log_level,
        run_id_prefix=run_id_prefix,
        opus_model=opus_model,
        haiku_model=haiku_model,
        openai_relevancy_model=openai_relevancy_model,
        openai_classification_model=openai_classification_model,
        openai_extract_model=openai_extract_model,
        openai_legal_extract_model=openai_legal_extract_model,
        openai_reasoning_model=openai_reasoning_model,
    )


_config: Config | None = None


def get_config() -> Config:
    """Return the singleton Config instance, loading it on first call.

    Subsequent calls return the cached instance without re-reading the
    environment.
    """
    global _config
    if _config is None:
        _config = load_config()
    return _config
