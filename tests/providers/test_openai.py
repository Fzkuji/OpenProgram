"""Wiring tests for the ``openai`` provider.

The dedicated Runtime shell is retired: ``create_runtime(provider=
"openai")`` resolves the API key from the AuthStore and returns the base
``Runtime("openai:<id>")``. These verify that thin wiring layer:

  - missing key raises with guidance
  - the model id resolves through the registry, provider-prefixed
  - the instance carries the authoritative ``provider_id``
  - ``list_models`` filters the registry by provider
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.registry import create_runtime

from ._registry_fixture import install_registry


@pytest.fixture(autouse=True)
def _enable_openai(monkeypatch):
    # The runtime registry now holds only the user's enabled models. Seed
    # gpt-4.1 (the table default used when no model= is passed) and the id
    # the explicit-model tests resolve.
    install_registry(monkeypatch, {"openai": {"models": [
        {"id": "gpt-4.1", "name": "GPT-4.1", "api": "openai-responses"},
        {"id": "gpt-4o-mini", "name": "GPT-4o mini", "api": "openai-responses"},
    ]}})


class TestOpenAIProviderWiring:
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
            create_runtime(provider="openai")

    def test_api_key_arg_wins(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        rt = create_runtime(provider="openai", api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_namespace(self):
        rt = create_runtime(provider="openai", model="gpt-4o-mini", api_key="k")
        assert rt.model == "openai:gpt-4o-mini"

    def test_default_model_from_table(self):
        rt = create_runtime(provider="openai", api_key="k")
        assert rt.model == "openai:gpt-4.1"

    def test_api_model_resolved_from_registry(self):
        rt = create_runtime(provider="openai", model="gpt-4o-mini", api_key="k")
        assert rt.api_model is not None
        assert rt.api_model.provider == "openai"
        assert rt.api_model.id == "gpt-4o-mini"

    def test_base_runtime_with_provider_id(self):
        """No subclass — the base Runtime carries the provider identity."""
        rt = create_runtime(provider="openai", model="gpt-4o-mini", api_key="k")
        assert type(rt) is Runtime
        assert rt.provider_id == "openai"

    def test_list_models_filters_by_provider(self):
        rt = create_runtime(provider="openai", model="gpt-4o-mini", api_key="k")
        ids = rt.list_models()
        assert ids, "registry should expose at least one OpenAI model"
        assert all(isinstance(i, str) for i in ids)
        assert "gpt-4o-mini" in ids
