"""Wiring tests for the ``gemini`` provider (Google Generative Language API).

The dedicated Runtime shell is retired: ``create_runtime(provider=
"gemini")`` resolves the API key from the AuthStore and returns the base
``Runtime("google:<id>")`` — the ``gemini`` provider streams models under
the ``google`` registry namespace. These verify that thin wiring layer:

  - missing key raises with guidance
  - the model id resolves through the registry, namespace-prefixed
  - the instance carries the authoritative ``provider_id``
  - ``list_models`` filters the registry by provider
"""

from __future__ import annotations

import pytest

from openprogram.agentic_programming.runtime import Runtime
from openprogram.providers.registry import create_runtime

from ._registry_fixture import install_registry


@pytest.fixture(autouse=True)
def _enable_gemini(monkeypatch):
    # The registry now holds only enabled models; enable the Google models
    # these wiring tests resolve so the runtime can find them in the registry.
    install_registry(monkeypatch, {"google": {"models": [
        {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro"},
        {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},  # table default
    ]}})


class TestGeminiProviderWiring:
    def test_no_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
        monkeypatch.setattr(
            "openprogram.providers.env_api_keys.resolve_provider_key",
            lambda *a, **k: None,
        )
        with pytest.raises(ValueError, match="API key"):
            create_runtime(provider="gemini")

    def test_api_key_arg_wins(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "env-key")
        rt = create_runtime(provider="gemini", api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_google_namespace(self):
        rt = create_runtime(provider="gemini", model="gemini-2.5-pro", api_key="k")
        assert rt.model == "google:gemini-2.5-pro"

    def test_default_model_from_table(self):
        rt = create_runtime(provider="gemini", api_key="k")
        assert rt.model == "google:gemini-2.5-flash"

    def test_api_model_resolved_from_registry(self):
        rt = create_runtime(provider="gemini", model="gemini-2.5-pro", api_key="k")
        assert rt.api_model is not None
        assert rt.api_model.provider == "google"
        assert rt.api_model.id == "gemini-2.5-pro"

    def test_base_runtime_with_provider_id(self):
        """No subclass — the base Runtime carries the provider identity,
        derived from the model namespace (``google``)."""
        rt = create_runtime(provider="gemini", model="gemini-2.5-pro", api_key="k")
        assert type(rt) is Runtime
        assert rt.provider_id == "google"

    def test_list_models_filters_by_provider(self):
        rt = create_runtime(provider="gemini", model="gemini-2.5-pro", api_key="k")
        ids = rt.list_models()
        assert ids, "registry should expose at least one Google model"
        assert all(isinstance(i, str) for i in ids)
        assert "gemini-2.5-pro" in ids
