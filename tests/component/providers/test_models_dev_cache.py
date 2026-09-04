"""models.dev source cache + tier-2 provider surfacing.

Regression guard for the "settings page shows only tier-1 providers"
symptom: a failed/empty models.dev fetch must NOT be cached as a
success for the full hour (or the community-provider tier vanishes
until the TTL expires), and a good fetch must surface as tier-2 rows
in ``list_providers()``.

No network: the registry-keyed safe client is stubbed at the seam models_dev binds.
"""
from __future__ import annotations

import copy

import pytest

from openprogram.providers.sources import models_dev as md
from openprogram.webui._model_listing import listing
from openprogram.providers import storage as st


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    # Isolate the disk-cache fallback too (22d98d80): on a dev machine
    # ~/.openprogram/cache/models_dev.json exists, and a failed fetch
    # deliberately serves it — which would mask the fail-TTL behaviour
    # these tests pin down.
    monkeypatch.setattr(md, "_disk_cache_path", lambda: tmp_path / "models_dev.json")
    md._cache.update({
        "data": None,
        "fetched_at": 0.0,
        "last_attempt_at": 0.0,
        "refreshing": False,
    })
    yield
    md._cache.update({
        "data": None,
        "fetched_at": 0.0,
        "last_attempt_at": 0.0,
        "refreshing": False,
    })


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Client:
    def __init__(self, get):
        self._get = get

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, **kwargs):
        return self._get(url, **kwargs)


def _patch_safe_get(monkeypatch, get):
    def factory(consumer):
        assert consumer == "webui.model_listing.fixed"
        return _Client(get)

    monkeypatch.setattr(md.safe_http, "safe_client", factory)


def test_empty_fetch_not_cached_as_success(monkeypatch):
    """A failed fetch (empty dict) is only held for the short fail-TTL, so the
    very next call after that window retries instead of serving empty for an
    hour."""
    calls = {"n": 0}

    def _get(url, timeout=10):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")  # first fetch fails
        return _Resp({"openrouter": {"models": {"x": {}}}})

    _patch_safe_get(monkeypatch, _get)

    assert md._load() == {}  # failure → empty
    assert md.list_providers() == []

    # Empty result must NOT be pinned for the full hour: expire the fail
    # window and the next call retries and succeeds.
    md._cache["fetched_at"] -= md._FAIL_TTL_SECONDS + 1
    data = md._load()
    assert data and "openrouter" in data
    assert calls["n"] == 2  # it actually re-fetched


def test_success_cached_for_full_ttl(monkeypatch):
    calls = {"n": 0}

    def _get(url, timeout=10):
        calls["n"] += 1
        return _Resp({"openrouter": {"models": {"x": {}}}})

    _patch_safe_get(monkeypatch, _get)

    assert "openrouter" in md._load()
    # A non-expired success is served from cache — no second fetch.
    md._cache["fetched_at"] -= md._FAIL_TTL_SECONDS + 1  # past fail window, within success TTL
    md._load()
    assert calls["n"] == 1


def test_tier2_providers_appear_in_list_providers(monkeypatch):
    """A live models.dev catalogue surfaces as tier-2 rows the static registry
    doesn't already cover."""
    def _get(url, timeout=10):
        return _Resp({
            "openrouter": {"name": "OpenRouter", "api": "https://openrouter.ai/api/v1",
                           "models": {"a/b": {}, "c/d": {}}},
            "togetherai": {"name": "Together", "models": {"m1": {}}},
        })

    _patch_safe_get(monkeypatch, _get)
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {})

    ids = {p["id"] for p in listing.list_providers()}
    assert {"openrouter", "togetherai"} <= ids
    # togetherai has no static-registry entry → surfaces as a tier-2 row.
    row = next(p for p in listing.list_providers() if p["id"] == "togetherai")
    assert row["community_source"] == "models.dev"
    assert row["model_count"] == 1


def test_failed_fetch_falls_back_to_disk_cache(monkeypatch, tmp_path):
    """When the network is down but a previous success was persisted,
    _load serves the stale catalogue instead of an empty dict."""
    import json

    (tmp_path / "models_dev.json").write_text(
        json.dumps({"openrouter": {"models": {"x": {}}}}), encoding="utf-8"
    )

    def _get(url, timeout=10):
        raise RuntimeError("network down")

    _patch_safe_get(monkeypatch, _get)

    data = md._load()
    assert data and "openrouter" in data
