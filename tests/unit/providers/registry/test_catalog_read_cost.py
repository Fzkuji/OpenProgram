"""Catalog status snapshots reduce enumeration without caching across requests."""
from types import SimpleNamespace
from openprogram.models.providers.catalog import metadata, sources
from openprogram.models.listing import listing


def test_catalog_enumerates_credentials_once_and_refreshes_next_request(monkeypatch):
    import openprogram.models.providers as registry
    from openprogram.models.auth import credential_provider
    from openprogram.models.providers.settings import storage, config_read, env_api_keys
    ids = ["openai", "anthropic", "deepseek", "groq"]
    pools = [SimpleNamespace(provider_id=pid, credentials=[object()]) for pid in ids]
    calls = []
    store = SimpleNamespace(list_pools=lambda: calls.append(1) or pools)
    monkeypatch.setattr(credential_provider, "get_credential_provider", lambda: SimpleNamespace(store=store))
    monkeypatch.setattr(registry, "get_providers", lambda: [])
    monkeypatch.setattr(registry, "get_models", lambda _pid: [])
    monkeypatch.setattr(metadata, "_shipped_provider_ids", lambda: set(ids))
    monkeypatch.setattr(sources.models_dev, "list_providers", lambda: [])
    monkeypatch.setattr(sources.models_dev, "provider_info", lambda _pid: None)
    monkeypatch.setattr(storage, "_read_providers_cfg", lambda: {})
    monkeypatch.setattr(config_read, "read_providers_config", lambda: {})
    monkeypatch.setattr(env_api_keys, "is_configured", lambda _pid: False)
    first = {p["id"]: p for p in listing.list_providers()}
    assert all(first[pid]["configured"] for pid in ids)
    assert len(calls) == 1
    pools[0].credentials = []
    second = {p["id"]: p for p in listing.list_providers()}
    assert second["openai"]["configured"] is False
    assert second["deepseek"]["configured"] is True
    assert len(calls) == 2


def test_snapshot_resets_after_exception_and_does_not_cache_direct_reads(monkeypatch):
    from openprogram.models.auth import credential_provider
    pools = [SimpleNamespace(provider_id="openai", credentials=[object()])]
    monkeypatch.setattr(credential_provider, "get_credential_provider", lambda: SimpleNamespace(store=SimpleNamespace(list_pools=lambda: pools)))
    try:
        with metadata.credential_status_snapshot():
            assert metadata.auth_store_has_credential("openai")
            pools.clear()
            assert metadata.auth_store_has_credential("openai")
            raise RuntimeError("request failed")
    except RuntimeError:
        pass
    assert metadata.auth_store_has_credential("openai") is False
