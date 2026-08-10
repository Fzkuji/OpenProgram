"""Which credential names the Web UI accepts — and where that list comes from.

``is_declared_credential_name`` gates every credential write. It must read
LIVE registries, not a table in this repo: community LLM providers arrive
from the models.dev catalogue and Web-search backends register at import
time, so a hard-coded list would silently refuse most real providers.
"""
from __future__ import annotations

import pytest

from openprogram.webui.routes._credential_secrets import (
    declared_credential_names,
    has_credential_field,
    is_declared_credential_name,
)


@pytest.mark.parametrize(
    "env_var",
    ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"],
)
def test_static_provider_names_are_declared(env_var):
    assert is_declared_credential_name(env_var)


@pytest.mark.parametrize(
    "env_var", ["TAVILY_API_KEY", "BRAVE_API_KEY", "EXA_API_KEY", "SERPER_API_KEY"]
)
def test_web_search_registry_names_are_declared(env_var):
    """These come from ``requires_env`` on registered backends, not a table."""
    assert is_declared_credential_name(env_var)


def test_web_search_names_come_from_the_live_registry(monkeypatch):
    """A backend registered at runtime is accepted with no code change here."""
    import openprogram.functions.tools.web_search.providers  # noqa: F401
    from openprogram.functions.tools.web_search.registry import registry
    from types import SimpleNamespace

    invented = "INVENTED_RUNTIME_SEARCH_KEY"
    assert not is_declared_credential_name(invented)

    real_all = registry.all
    monkeypatch.setattr(
        registry,
        "all",
        lambda: list(real_all()) + [SimpleNamespace(requires_env=(invented,))],
    )

    assert is_declared_credential_name(invented)


def test_community_provider_names_are_declared():
    """models.dev providers have no static row — their env vars must still
    resolve, or a community provider's key could never be saved."""
    from openprogram.providers.sources import models_dev

    catalogue = models_dev.list_providers()
    if not catalogue:
        pytest.skip("models.dev catalogue cache is empty")

    from openprogram.providers.env_api_keys import _PROVIDER_ENV_VARS

    static = {name for names in _PROVIDER_ENV_VARS.values() for name in names}
    community = [
        provider["env_var"]
        for provider in catalogue
        if provider.get("env_var") and provider["env_var"] not in static
    ]
    assert community, "expected community providers outside the static table"

    undeclared = [name for name in community if not is_declared_credential_name(name)]
    assert undeclared == []


def test_community_names_come_from_the_catalogue_not_a_local_table(monkeypatch):
    """Proves the catalogue is actually consulted, not shadowed by a table."""
    from openprogram.providers.sources import models_dev

    invented = "INVENTED_COMMUNITY_PROVIDER_KEY"
    assert not is_declared_credential_name(invented)

    real_list = models_dev.list_providers
    monkeypatch.setattr(
        models_dev,
        "list_providers",
        lambda: list(real_list()) + [{"id": "invented", "env_var": invented}],
    )

    assert is_declared_credential_name(invented)


def test_unreachable_catalogue_still_declares_static_names(monkeypatch):
    """A cold or offline catalogue must not revoke the built-in providers."""
    from openprogram.providers.sources import models_dev

    monkeypatch.setattr(
        models_dev,
        "list_providers",
        lambda: (_ for _ in ()).throw(RuntimeError("catalogue unreachable")),
    )

    assert is_declared_credential_name("OPENAI_API_KEY")
    assert is_declared_credential_name("TAVILY_API_KEY")


@pytest.mark.parametrize(
    "name", ["", None, 5, "UNDECLARED_API_KEY", "PATH", "HOME", "not-a-name"]
)
def test_undeclared_names_are_refused(name):
    assert not is_declared_credential_name(name)


def test_declared_names_is_a_nonempty_frozenset():
    names = declared_credential_names()

    assert isinstance(names, frozenset)
    assert "OPENAI_API_KEY" in names


@pytest.mark.parametrize(
    "body",
    [
        {"api_key": "x"},
        {"apiKey": "x"},
        {"API_KEY": "x"},
        {"access_token": "x"},
        {"client_secret": "x"},
        {"password": "x"},
        {"credential": "x"},
        {"OPENAI_API_KEY": "x"},
        {"TAVILY_API_KEY": "x"},
        {"id": "ok", "refresh_token": "x"},
    ],
)
def test_credential_shaped_fields_are_detected(body):
    assert has_credential_field(body)


@pytest.mark.parametrize(
    "body",
    [{}, {"id": "work"}, {"enabled": True}, {"base_url": "http://x"}, {"model": "m"}],
)
def test_ordinary_fields_are_not_credential_shaped(body):
    assert not has_credential_field(body)
