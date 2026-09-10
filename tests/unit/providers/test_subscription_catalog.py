from __future__ import annotations

import json

from openprogram.providers import subscription_catalog as catalog


def test_catalog_round_trip_is_atomic_and_profile_local(monkeypatch, tmp_path):
    path = tmp_path / "cache" / "subscription-models" / "openai-codex.json"
    monkeypatch.setattr(catalog, "_cache_path", lambda _provider: path)

    catalog.save_catalog("openai-codex", [{"id": "future-model", "name": "Future"}])
    rows, fetched_at = catalog.load_catalog("openai-codex")

    assert rows == [{"id": "future-model", "name": "Future"}]
    assert fetched_at is not None
    assert json.loads(path.read_text())["schema_version"] == 1
    assert path.stat().st_mode & 0o777 == 0o600


def test_catalog_rejects_wrong_provider(monkeypatch, tmp_path):
    path = tmp_path / "openai-codex.json"
    path.write_text(json.dumps({
        "provider": "xai-subscription",
        "fetched_at": 1,
        "models": [{"id": "wrong"}],
    }))
    monkeypatch.setattr(catalog, "_cache_path", lambda _provider: path)
    assert catalog.load_catalog("openai-codex") == ([], None)
