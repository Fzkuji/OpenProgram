"""Wiring tests for GeminiRuntime.

The runtime no longer formats Gemini-shaped requests itself; that job
moved to pi-ai. These tests verify the thin wiring layer:

  - missing API key raises
  - both GOOGLE_API_KEY and GOOGLE_GENERATIVE_AI_API_KEY are accepted
  - constructor resolves the model id through the pi-ai registry
  - the resulting Runtime uses the new ``Runtime("google:<id>")`` path
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.google.runtime import GeminiRuntime


class TestGeminiRuntime:
    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            GeminiRuntime(api_key=None)

    @pytest.mark.xfail(
        reason="env-var key reading is retired — keys resolve only from the "
        "AuthStore now (project_authstore_only_keys). Rewrite to seed the "
        "AuthStore instead of GOOGLE_API_KEY.",
        strict=False,
    )
    def test_google_api_key_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "primary")
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        rt = GeminiRuntime()
        assert rt.api_key == "primary"

    @pytest.mark.xfail(
        reason="env-var key reading is retired — keys resolve only from the "
        "AuthStore now (project_authstore_only_keys). Rewrite to seed the "
        "AuthStore instead of GOOGLE_GENERATIVE_AI_API_KEY.",
        strict=False,
    )
    def test_genai_api_key_env_fallback(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_GENERATIVE_AI_API_KEY", "fallback")
        rt = GeminiRuntime()
        assert rt.api_key == "fallback"

    def test_api_key_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "env-key")
        rt = GeminiRuntime(api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_provider(self):
        rt = GeminiRuntime(api_key="k", model="gemini-2.5-pro")
        assert rt.model == "google:gemini-2.5-pro"

    def test_api_model_resolved_from_registry(self):
        rt = GeminiRuntime(api_key="k", model="gemini-2.5-pro")
        assert rt.api_model is not None
        assert rt.api_model.provider == "google"
        assert rt.api_model.id == "gemini-2.5-pro"

    def test_uses_default_path_not_legacy(self):
        rt = GeminiRuntime(api_key="k", model="gemini-2.5-pro")
        assert rt._uses_legacy_call() is False
        assert type(rt)._call is Runtime._call

    def test_list_models_filters_by_provider(self):
        rt = GeminiRuntime(api_key="k", model="gemini-2.5-pro")
        ids = rt.list_models()
        assert ids, "registry should expose at least one Google model"
        assert all(isinstance(i, str) for i in ids)
        assert "gemini-2.5-pro" in ids
