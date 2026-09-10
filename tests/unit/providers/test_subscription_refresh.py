from openprogram.providers import subscription_refresh as refresh


def test_refresh_only_configured_stale_catalogues(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(
        "openprogram.providers.metadata.is_configured",
        lambda provider_id: provider_id in {"openai-codex", "xai-subscription"},
    )
    monkeypatch.setattr(
        refresh,
        "catalog_is_stale",
        lambda provider_id, _age: provider_id == "xai-subscription",
    )
    monkeypatch.setattr(
        "openprogram.webui._model_listing.fetch_models_remote",
        lambda provider_id: called.append(provider_id) or {"fetched": 2},
    )

    assert refresh.refresh_stale_catalogues(max_age_s=10) == ["xai-subscription"]
    assert called == ["xai-subscription"]


def test_refresh_failure_is_best_effort(monkeypatch):
    monkeypatch.setattr("openprogram.providers.metadata.is_configured", lambda _pid: True)
    monkeypatch.setattr(refresh, "catalog_is_stale", lambda _pid, _age: True)
    monkeypatch.setattr(
        "openprogram.webui._model_listing.fetch_models_remote",
        lambda _pid: {"error": "offline"},
    )

    assert refresh.refresh_stale_catalogues(max_age_s=10) == []
