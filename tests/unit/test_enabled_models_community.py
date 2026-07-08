"""list_enabled_models reads the runtime registry (config spec rows).

Post enabled-models-redesign the chat composer picker no longer iterates
config ``custom_models`` — it reshapes ``ENABLED_MODELS`` (which is built
from the user's enabled ``providers.<p>.models`` spec rows) directly. These
tests pin:

  * a community-only provider (no static enabled_models row) whose enabled
    spec row lands in the registry is surfaced in the picker;
  * a provider whose ``enabled`` toggle is off is excluded;
  * the registry key's provider carries through as the row's provider id.
"""
from __future__ import annotations

import pytest

import openprogram.providers.enabled_models as mg
from openprogram.providers.types import Model
from openprogram.webui._model_listing import listing
from openprogram.webui._model_listing import storage as st
from openprogram.webui._model_listing import providers as cat


def _model(mid, provider, api="anthropic-messages", ctx=200000):
    return Model.model_validate({
        "id": mid, "name": mid, "provider": provider, "api": api,
        "base_url": "https://x/anthropic", "context_window": ctx,
    })


@pytest.fixture
def stub_labels(monkeypatch):
    monkeypatch.setattr(cat, "_label", lambda pid: pid)


def test_community_only_enabled_model_is_surfaced(monkeypatch, stub_labels):
    monkeypatch.setattr(mg, "ENABLED_MODELS", {
        "foo-coding-plan/Foo-1": _model("Foo-1", "foo-coding-plan"),
    })
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {
        "foo-coding-plan": {"enabled": True, "models": [{"id": "Foo-1"}]},
    })
    out = listing.list_enabled_models()
    ids = [(m["provider"], m["id"]) for m in out]
    assert ("foo-coding-plan", "Foo-1") in ids
    row = next(m for m in out if m["id"] == "Foo-1")
    assert row["api"] == "anthropic-messages"
    assert row["context_window"] == 200000


def test_disabled_community_provider_not_surfaced(monkeypatch, stub_labels):
    monkeypatch.setattr(mg, "ENABLED_MODELS", {
        "foo-coding-plan/Foo-1": _model("Foo-1", "foo-coding-plan"),
    })
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {
        "foo-coding-plan": {"enabled": False, "models": [{"id": "Foo-1"}]},
    })
    out = listing.list_enabled_models()
    assert all(m["provider"] != "foo-coding-plan" for m in out)
