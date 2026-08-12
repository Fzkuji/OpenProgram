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


def test_structured_output_tri_state_survives_disk_model_and_web_listing(
    tmp_path, monkeypatch
):
    from openprogram.providers.enabled_models import _build_model_from_row
    import openprogram.providers.enabled_models as enabled_models
    from openprogram.providers.sources import models_dev
    from openprogram.providers import storage
    from openprogram.webui._model_listing import listing

    cache = tmp_path / "cache" / "models_dev.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({
        "acme": {
            "name": "Acme",
            "models": {
                "yes": {"structured_output": True},
                "no": {"structured_output": False},
                "unknown": {},
            },
        }
    }))
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)
    monkeypatch.setattr(models_dev, "_CATALOGUE_URL", "http://127.0.0.1:1/nope")
    _reset_mem_cache()
    try:
        rows = models_dev.list_models("acme")
        built = {
            mid: _build_model_from_row(
                {"id": mid, "name": mid, "api": "openai-completions", **row},
                "acme",
                {"default": {"base_url": "https://acme.example/v1"}},
            )
            for mid, row in rows.items()
        }
        monkeypatch.setattr(enabled_models, "ENABLED_MODELS", {
            f"acme/{mid}": model for mid, model in built.items()
        })
        monkeypatch.setattr(
            storage,
            "_read_providers_cfg",
            lambda: {"acme": {"enabled": True}},
        )

        listed = {row["id"]: row for row in listing.list_enabled_models()}
        assert [listed[mid]["structured_output"] for mid in ("yes", "no", "unknown")] == [
            True,
            False,
            None,
        ]
    finally:
        _reset_mem_cache()


def test_structured_output_tri_state_survives_real_fetch_persist_reload_and_web(
    tmp_path, monkeypatch
):
    import copy
    import httpx
    import openprogram.providers._config_read as config_read
    import openprogram.providers.enabled_models as enabled_models
    import openprogram.setup as setup
    from openprogram.providers import storage
    from openprogram.webui._model_listing import listing, toggle

    cache = tmp_path / "cache" / "models_dev.json"
    store = {
        "acme": {
            "enabled": True,
            "base_url": "https://acme.example/v1",
        }
    }

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "acme": {
                    "name": "Acme",
                    "models": {
                        "yes": {"structured_output": True},
                        "no": {"structured_output": False},
                        "unknown": {},
                    },
                }
            }

    def save(config):
        store.clear()
        store.update(copy.deepcopy(config["providers"]))

    def update(config_mutator):
        config = {"providers": copy.deepcopy(store)}
        config_mutator(config)
        save(config)
        return config

    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)
    monkeypatch.setattr(setup, "_read_config", lambda: {"providers": copy.deepcopy(store)})
    monkeypatch.setattr(setup, "_write_config", save)
    monkeypatch.setattr(setup, "update_config", update)
    monkeypatch.setattr(config_read, "read_providers_config", lambda: copy.deepcopy(store))
    monkeypatch.setattr(storage, "_read_providers_cfg", lambda: copy.deepcopy(store))
    storage._reset_spec_migration()
    listing._reset_browse_cache()
    _reset_mem_cache()
    enabled_models.reload()
    try:
        assert set(models_dev.list_models("acme")) == {"yes", "no", "unknown"}
        assert json.loads(cache.read_text())["acme"]["models"]["no"][
            "structured_output"
        ] is False

        for model_id in ("yes", "no", "unknown"):
            toggle.toggle_model("acme", model_id, True)

        persisted = {row["id"]: row for row in store["acme"]["models"]}
        assert persisted["yes"]["structured_output"] is True
        assert persisted["no"]["structured_output"] is False
        assert "structured_output" not in persisted["unknown"]

        enabled_models.reload()
        listed = {row["id"]: row for row in listing.list_enabled_models()}
        assert [listed[mid]["structured_output"] for mid in ("yes", "no", "unknown")] == [
            True,
            False,
            None,
        ]
    finally:
        store.clear()
        enabled_models.reload()
        listing._reset_browse_cache()
        _reset_mem_cache()
