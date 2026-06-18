"""Wiring tests for OpenAIRuntime.

The runtime no longer formats OpenAI-shaped requests itself; that job
moved to pi-ai. These tests verify the thin wiring layer:

  - missing API key raises
  - constructor resolves the model id through the pi-ai registry
  - the resulting Runtime uses the new ``Runtime("openai:<id>")`` path
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.openai_responses.runtime import OpenAIRuntime


class TestOpenAIRuntime:
    def test_no_api_key_raises(self, monkeypatch):
        # Keys resolve through the AuthStore now (env reading retired); force
        # the resolver to find nothing so this tests the genuine
        # "no credential anywhere" path on any dev machine.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(
            "openprogram.providers.env_api_keys.resolve_provider_key",
            lambda *a, **k: None,
        )
        with pytest.raises(ValueError, match="API key"):
            OpenAIRuntime(api_key=None)

    @pytest.mark.xfail(
        reason="env-var key reading is retired — provider keys resolve only "
        "from the AuthStore now (see project_authstore_only_keys / "
        "env_api_keys.py). This test asserts the old OPENAI_API_KEY-from-env "
        "behaviour; rewrite to seed the AuthStore instead.",
        strict=False,
    )
    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        rt = OpenAIRuntime()
        assert rt.api_key == "env-key"

    def test_api_key_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        rt = OpenAIRuntime(api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_provider(self):
        rt = OpenAIRuntime(api_key="k", model="gpt-4o-mini")
        assert rt.model == "openai:gpt-4o-mini"

    def test_api_model_resolved_from_registry(self):
        rt = OpenAIRuntime(api_key="k", model="gpt-4o-mini")
        assert rt.api_model is not None
        assert rt.api_model.provider == "openai"
        assert rt.api_model.id == "gpt-4o-mini"

    def test_uses_default_path_not_legacy(self):
        rt = OpenAIRuntime(api_key="k", model="gpt-4o-mini")
        # Single path now (_uses_legacy_call removed in unification).
        assert type(rt)._call is Runtime._call

    def test_list_models_filters_by_provider(self):
        rt = OpenAIRuntime(api_key="k", model="gpt-4o-mini")
        ids = rt.list_models()
        assert ids, "registry should expose at least one OpenAI model"
        assert all(isinstance(i, str) for i in ids)
        assert "gpt-4o-mini" in ids
