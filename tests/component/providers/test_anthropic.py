"""Wiring tests for the ``anthropic`` provider.

The dedicated Runtime shell is retired: ``create_runtime(provider=
"anthropic")`` resolves the credential (api-key OR subscription OAuth)
and returns the base ``Runtime("anthropic:<id>")``. These verify that
thin wiring layer:

  - missing credential raises with guidance
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
def _enable_anthropic(monkeypatch):
    # The runtime registry now holds only the user's enabled models. Seed the
    # one Anthropic model these wiring tests resolve so the runtime can find it
    # regardless of what's in the dev's ~/.openprogram/config.json (empty in CI).
    install_registry(monkeypatch, {"anthropic": {"models": [
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    ]}})


class TestAnthropicProviderWiring:
    def test_no_credential_raises(self, monkeypatch):
        # Keys resolve only through the AuthStore (env reading retired,
        # project_authstore_only_keys). Force the resolver to find nothing so
        # this tests the genuine "no credential anywhere" path, regardless of
        # what's stored on the dev machine running the suite.
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "openprogram.auth.resolver.resolve_api_key_sync",
            lambda *a, **k: None,
        )
        with pytest.raises(ValueError, match="(?i)credential"):
            create_runtime(provider="anthropic")

    def test_api_key_arg_wins(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        rt = create_runtime(provider="anthropic", api_key="explicit-key")
        assert rt.api_key == "explicit-key"

    def test_model_prefixed_with_namespace(self):
        rt = create_runtime(
            provider="anthropic", model="claude-sonnet-4-6", api_key="k"
        )
        assert rt.model == "anthropic:claude-sonnet-4-6"

    def test_default_model_from_table(self):
        rt = create_runtime(provider="anthropic", api_key="k")
        assert rt.model == "anthropic:claude-sonnet-4-6"

    def test_api_model_resolved_from_registry(self):
        rt = create_runtime(
            provider="anthropic", model="claude-sonnet-4-6", api_key="k"
        )
        assert rt.api_model is not None
        assert rt.api_model.provider == "anthropic"
        assert rt.api_model.id == "claude-sonnet-4-6"

    def test_base_runtime_with_provider_id(self):
        """No subclass — the base Runtime carries the provider identity."""
        rt = create_runtime(
            provider="anthropic", model="claude-sonnet-4-6", api_key="k"
        )
        assert type(rt) is Runtime
        assert rt.provider_id == "anthropic"

    def test_list_models_filters_by_provider(self):
        rt = create_runtime(
            provider="anthropic", model="claude-sonnet-4-6", api_key="k"
        )
        ids = rt.list_models()
        assert ids, "registry should expose at least one Anthropic model"
        assert all(isinstance(i, str) for i in ids)
        assert "claude-sonnet-4-6" in ids
