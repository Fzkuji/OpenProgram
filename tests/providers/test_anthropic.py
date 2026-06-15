"""Wiring tests for AnthropicRuntime.

The runtime no longer formats Anthropic-shaped requests itself; that
job moved to pi-ai. These tests verify the thin wiring layer:

  - missing API key raises
  - constructor resolves the model id through the pi-ai registry
  - the resulting Runtime uses the new ``Runtime("anthropic:<id>")``
    code path (no subclass ``_call`` override)
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.anthropic.runtime import AnthropicRuntime


class TestAnthropicRuntime:
    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            AnthropicRuntime(api_key=None)

    @pytest.mark.xfail(
        reason="env-var key reading is retired — keys resolve only from the "
        "AuthStore now (project_authstore_only_keys). Rewrite to seed the "
        "AuthStore instead of ANTHROPIC_API_KEY.",
        strict=False,
    )
    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        rt = AnthropicRuntime()
        assert rt.api_key == "env-key"

    def test_api_key_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        rt = AnthropicRuntime(api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_provider(self):
        rt = AnthropicRuntime(api_key="k", model="claude-sonnet-4-6")
        assert rt.model == "anthropic:claude-sonnet-4-6"

    def test_api_model_resolved_from_registry(self):
        rt = AnthropicRuntime(api_key="k", model="claude-sonnet-4-6")
        assert rt.api_model is not None
        assert rt.api_model.provider == "anthropic"
        assert rt.api_model.id == "claude-sonnet-4-6"

    def test_uses_default_path_not_legacy(self):
        """No ``_call`` override — runs through ``_call_via_providers``."""
        rt = AnthropicRuntime(api_key="k", model="claude-sonnet-4-6")
        assert rt._uses_legacy_call() is False
        assert type(rt)._call is Runtime._call

    def test_list_models_filters_by_provider(self):
        rt = AnthropicRuntime(api_key="k", model="claude-sonnet-4-6")
        ids = rt.list_models()
        assert ids, "registry should expose at least one Anthropic model"
        assert all(isinstance(i, str) for i in ids)
        assert "claude-sonnet-4-6" in ids
