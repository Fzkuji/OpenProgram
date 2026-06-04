"""list_enabled_models must include community-only providers.

Providers with no static models_generated row (every models.dev community
entry, e.g. minimax-cn-coding-plan) live entirely in their config's
``custom_models``. list_enabled_models used to iterate only
get_providers() (the static registry), so a community provider the user
enabled — with enabled_models — was silently dropped from the chat
composer picker. This pins that it's surfaced.
"""
from __future__ import annotations

import pytest

import openprogram.providers as P
from openprogram.webui._model_catalog import listing
from openprogram.webui._model_catalog import storage as st
from openprogram.webui._model_catalog import providers as cat


@pytest.fixture
def stub_catalog(monkeypatch):
    # Static registry knows only "openai"; the user's enabled provider
    # "foo-coding-plan" is community-only (NOT in get_providers).
    monkeypatch.setattr(P, "get_providers", lambda: ["openai"])
    monkeypatch.setattr(P, "get_models", lambda pid=None: [])
    monkeypatch.setattr(cat, "_is_configured", lambda pid: True)
    monkeypatch.setattr(cat, "_label", lambda pid: pid)
    monkeypatch.setattr(
        cat, "_PROVIDER_DEFAULT_API",
        {**cat._PROVIDER_DEFAULT_API, "foo-coding-plan": "anthropic-messages"},
    )
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {
        "foo-coding-plan": {
            "enabled": True,
            "enabled_models": ["Foo-1"],
            "custom_models": [{"id": "Foo-1", "name": "Foo One",
                               "context_window": 200000}],
        },
    })


def test_community_only_enabled_model_is_surfaced(stub_catalog):
    out = listing.list_enabled_models()
    ids = [(m["provider"], m["id"]) for m in out]
    assert ("foo-coding-plan", "Foo-1") in ids
    row = next(m for m in out if m["id"] == "Foo-1")
    # api comes from the catalog default-api map (so chat routes correctly)
    assert row["api"] == "anthropic-messages"
    assert row["context_window"] == 200000


def test_no_legacy_alias_is_ever_surfaced(monkeypatch, stub_catalog):
    # General invariant: a stale config entry under ANY known alias id
    # (e.g. chatgpt-subscription → openai-codex) must never surface as its
    # own provider — the canonical id already represents it, and the
    # settings list only shows the canonical one. A surfaced alias is a
    # phantom duplicate in the chat picker the user can't find/manage.
    from openprogram.auth.aliases import known_aliases
    aliases = {a: c for a, c in known_aliases().items() if a != c}
    assert aliases, "expected at least one alias to test"
    cfg = {
        alias: {"enabled": True, "enabled_models": ["m1"],
                "custom_models": [{"id": "m1"}]}
        for alias in aliases
    }
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: cfg)
    surfaced = {m["provider"] for m in listing.list_enabled_models()}
    leaked = surfaced & set(aliases)
    assert not leaked, f"alias provider ids leaked into the picker: {leaked}"


def test_disabled_community_provider_not_surfaced(monkeypatch, stub_catalog):
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {
        "foo-coding-plan": {
            "enabled": False,  # provider toggle off
            "enabled_models": ["Foo-1"],
            "custom_models": [{"id": "Foo-1"}],
        },
    })
    out = listing.list_enabled_models()
    assert all(m["provider"] != "foo-coding-plan" for m in out)
