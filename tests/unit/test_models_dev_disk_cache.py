"""Disk-cache fallback for the models.dev catalogue (offline worker)."""

import json

from openprogram.providers.sources import models_dev


def _reset_mem_cache():
    models_dev._cache["data"] = None
    models_dev._cache["fetched_at"] = 0.0


def test_fetch_failure_falls_back_to_disk_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "models_dev.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"openai": {"name": "OpenAI", "models": {"gpt": {}}}}))
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)
    monkeypatch.setattr(models_dev, "_CATALOGUE_URL", "http://127.0.0.1:1/nope")
    _reset_mem_cache()
    try:
        data = models_dev._load()
        assert "openai" in data
    finally:
        _reset_mem_cache()


def test_successful_fetch_writes_disk_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "models_dev.json"
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"groq": {"name": "Groq", "models": {}}}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    _reset_mem_cache()
    try:
        data = models_dev._load()
        assert "groq" in data
        assert json.loads(cache.read_text())["groq"]["name"] == "Groq"
    finally:
        _reset_mem_cache()
