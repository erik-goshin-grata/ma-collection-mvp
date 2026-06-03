"""
Offline smoke test for provider-aware LLM configuration.

This script does not call Anthropic or OpenAI APIs. It validates imports,
provider selection, OpenAI client instantiation, model routing, and the shared
JSON parser used by prompts/base.py.

Usage:
    python scripts/validate_llm_provider.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _set_required_env(provider: str) -> None:
    os.environ["LLM_PROVIDER"] = provider
    os.environ["SEC_API_KEY"] = "sec-test-key"
    os.environ["OPERATOR_CONTACT_EMAIL"] = "operator@example.com"
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    os.environ["OPENAI_API_KEY"] = "sk-test"


def _load_fresh_config(provider: str):
    import config

    _set_required_env(provider)
    config._config = None
    return config.load_config()


def main() -> None:
    from lib.llm_client import (
        get_provider_name,
        instantiate_provider_client,
        LLMResponse,
        resolve_model_id,
    )
    import prompts.base as prompt_base

    cfg = _load_fresh_config("openai")
    assert get_provider_name(cfg) == "openai"
    assert cfg.openai_api_key == "sk-test"

    client = instantiate_provider_client(cfg)
    assert client.__class__.__name__ == "OpenAI"

    assert (
        resolve_model_id(cfg, prompt_name="relevancy_filter", requested_model="haiku")
        == cfg.openai_relevancy_model
    )
    assert (
        resolve_model_id(cfg, prompt_name="deal_type_classifier", requested_model="opus")
        == cfg.openai_classification_model
    )
    assert (
        resolve_model_id(cfg, prompt_name="high_confidence_extraction", requested_model="opus")
        == cfg.openai_extract_model
    )
    assert (
        resolve_model_id(cfg, prompt_name="agreement_recitals", requested_model="opus")
        == cfg.openai_legal_extract_model
    )
    assert (
        resolve_model_id(cfg, prompt_name="strategic_rationale", requested_model="opus")
        == cfg.openai_reasoning_model
    )

    original_call_llm = prompt_base.call_llm
    try:
        prompt_base.call_llm = lambda request, *, cfg, log: LLMResponse(
            provider="openai",
            model_id=cfg.openai_relevancy_model,
            raw_text='```json\n{"ok": true, "prompt_version": "provider_smoke:0.1"}\n```',
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
        )
        assert prompt_base.call_prompt(
            prompt_name="provider_smoke",
            prompt_version="provider_smoke:0.1",
            user_prompt="Return JSON.",
            system_prompt="Return JSON.",
            model="haiku",
            temperature=0.0,
            max_tokens=64,
            cfg=cfg,
            conn=sqlite3.connect(":memory:"),
            run_id="provider_smoke",
        ) == {"ok": True, "prompt_version": "provider_smoke:0.1"}
    finally:
        prompt_base.call_llm = original_call_llm

    cfg = _load_fresh_config("anthropic")
    assert get_provider_name(cfg) == "anthropic"
    assert resolve_model_id(cfg, prompt_name="relevancy_filter", requested_model="haiku") == cfg.haiku_model
    assert resolve_model_id(cfg, prompt_name="deal_type_classifier", requested_model="opus") == cfg.opus_model

    assert prompt_base.parse_json_response('{"ok": true}') == {"ok": True}
    assert prompt_base.parse_json_response('```json\n{"ok": true, "items": []}\n```') == {
        "ok": True,
        "items": [],
    }

    print("PASS: imports, provider selection, OpenAI instantiation, and JSON parsing are valid.")


if __name__ == "__main__":
    main()
