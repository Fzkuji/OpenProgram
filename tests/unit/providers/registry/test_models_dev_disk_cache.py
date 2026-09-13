"""Disk-cache fallback for the models.dev catalogue (offline worker)."""

import json

from openprogram.providers.sources import models_dev


def _reset_mem_cache():
    models_dev._cache.update({
        "data": None,
        "fetched_at": 0.0,
        "last_attempt_at": 0.0,
        "refreshing": False,
    })


def test_expired_disk_cache_returns_stale_and_schedules_refresh(
    tmp_path, monkeypatch
):
    cache = tmp_path / "cache" / "models_dev.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"openai": {"name": "OpenAI", "models": {"gpt": {}}}}))
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)
    request_called = False

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            nonlocal request_called
            request_called = True
            raise AssertionError("stale load must not make a synchronous request")

    scheduled = []
    monkeypatch.setattr(models_dev.safe_http, "safe_client", lambda *_a, **_k: _Client())
    monkeypatch.setattr(
        models_dev,
        "_start_background_refresh",
        lambda: scheduled.append(models_dev._refresh_cache),
        raising=False,
    )
    _reset_mem_cache()
    try:
        data = models_dev._load()
        assert "openai" in data
        assert request_called is False
        assert len(scheduled) == 1
        attempt_started = models_dev._cache["last_attempt_at"]
        scheduled[0]()
        assert request_called is True
        assert models_dev._cache["data"] == data
        assert models_dev._cache["refreshing"] is False
        assert models_dev._cache["last_attempt_at"] >= attempt_started
    finally:
        _reset_mem_cache()


def test_cold_start_fetches_with_three_second_timeout(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "models_dev.json"
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)
    calls = []

    class _Resp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"openai": {"name": "OpenAI", "models": {}}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return _Resp()

    monkeypatch.setattr(models_dev.safe_http, "safe_client", lambda *_a, **_k: _Client())
    _reset_mem_cache()
    try:
        data = models_dev._load()
        assert "openai" in data
        assert calls == [(models_dev._CATALOGUE_URL, {"timeout": 3})]
    finally:
        _reset_mem_cache()


def test_successful_fetch_writes_disk_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "models_dev.json"
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: cache)

    class _Resp:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"groq": {"name": "Groq", "models": {}}}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return _Resp()

    def fixed(consumer):
        assert consumer == "webui.model_listing.fixed"
        return _Client()

    monkeypatch.setattr(models_dev.safe_http, "safe_client", fixed)
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
    # This test covers disk-to-model projection, not network scheduling.
    # Keep stale refresh queued so no real thread survives fixture teardown.
    scheduled = []
    monkeypatch.setattr(models_dev, "_start_background_refresh", lambda: scheduled.append(True))
    _reset_mem_cache()
    try:
        rows = models_dev.list_models("acme")
        assert scheduled == [True]
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
        headers = {"content-type": "application/json"}

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

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return Response()

    def save(config):
        store.clear()
        store.update(copy.deepcopy(config["providers"]))

    def update(config_mutator):
        config = {"providers": copy.deepcopy(store)}
        config_mutator(config)
        save(config)
        return config

    monkeypatch.setattr(
        models_dev.safe_http,
        "safe_client",
        lambda consumer: Client(),
    )
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
