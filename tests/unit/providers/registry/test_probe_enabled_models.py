"""Startup detection skips a provider the owner enabled no models of, quietly."""
from __future__ import annotations

from openprogram.interfaces.server import runtime_management


def test_a_provider_with_no_enabled_models_is_skipped_without_a_log(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(runtime_management, "_log", logged.append)
    monkeypatch.setattr("openprogram.models.providers.catalog.metadata.is_configured", lambda name: True)
    monkeypatch.setattr("openprogram.models.providers.catalog.models.get_models", lambda namespace=None: [])
    built: list[str] = []
    monkeypatch.setattr(runtime_management, "_create_runtime_for_visualizer", built.append)

    assert runtime_management._probe_one_provider("anthropic") is None
    assert logged == [] and built == []


def test_a_provider_outside_the_runtime_table_still_counts_as_having_models():
    assert runtime_management._has_enabled_models("claude-code") is True
