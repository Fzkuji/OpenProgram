"""Per-provider fetched-model store + models.dev merge."""
from __future__ import annotations

import pytest

from openprogram.webui._model_catalog import provider_models as pm


@pytest.fixture
def _tmp_store(tmp_path, monkeypatch):
    """Point the store at a tmp dir so tests don't touch the repo."""
    # Create a fake provider dir structure
    anthropic_dir = tmp_path / "anthropic"
    anthropic_dir.mkdir()
    monkeypatch.setattr(pm, "_PROVIDERS_DIR", tmp_path)
    return tmp_path


def test_save_then_load_roundtrip(_tmp_store):
    pm.save_fetched("anthropic", [{"id": "claude-opus-4-8", "context_window": 1_000_000}])
    got = pm.load_fetched("anthropic")
    assert got == [{"id": "claude-opus-4-8", "context_window": 1_000_000}]


def test_load_missing_is_empty(_tmp_store):
    assert pm.load_fetched("never-fetched") == []


def test_save_overwrites(_tmp_store):
    pm.save_fetched("anthropic", [{"id": "a"}])
    pm.save_fetched("anthropic", [{"id": "b"}])
    assert [m["id"] for m in pm.load_fetched("anthropic")] == ["b"]


def test_combined_merges_fetch_authority_with_models_dev(_tmp_store, monkeypatch):
    pm.save_fetched("anthropic", [{"id": "claude-opus-4-8", "context_window": 1_000_000}])
    monkeypatch.setattr(pm, "_models_dev_for", lambda p: {
        "claude-opus-4-8": {"input_cost": 5.0, "output_cost": 25.0, "vision": True,
                            "context_window": 200000},
    })
    out = pm.combined_models("anthropic")
    assert len(out) == 1
    m = out[0]
    assert m["context_window"] == 1_000_000
    assert m["input_cost"] == 5.0
    assert m["vision"] is True


def test_combined_falls_back_to_models_dev_when_never_fetched(_tmp_store, monkeypatch):
    monkeypatch.setattr(pm, "_models_dev_for", lambda p: {
        "claude-sonnet-4-6": {"input_cost": 3.0, "context_window": 1_000_000},
    })
    out = pm.combined_models("anthropic")
    assert [m["id"] for m in out] == ["claude-sonnet-4-6"]


def test_subscription_borrows_sibling(_tmp_store, monkeypatch):
    seen = {}
    def _fake_list(src):
        seen["src"] = src
        return {"claude-opus-4-8": {"input_cost": 5.0}}
    from openprogram.webui._model_catalog.sources import models_dev
    monkeypatch.setattr(models_dev, "list_models", _fake_list)
    pm.combined_models("claude-code")
    assert seen["src"] == "anthropic"
