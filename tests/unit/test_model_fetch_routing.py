"""Model-list fetch routing for Anthropic-wire third-party providers.

MiniMax (minimax / minimax-cn) ships its API in the Anthropic Messages
wire format (base_url ends in ``/anthropic``). Three things must line up
or the provider half-works in confusing ways:

  * the fetch dispatcher must route it to the Anthropic ``/v1/models``
    fetcher (the OpenAI-compatible ``GET /models`` 404s on its host);
  * that fetcher must hit the provider's OWN base_url, not
    api.anthropic.com;
  * ``_PROVIDER_DEFAULT_API`` must stamp fetched/custom rows
    ``anthropic-messages`` (matching models_generated) so chat routes to
    the right stream function instead of POST /chat/completions.

No network: httpx + storage resolvers are stubbed.
"""
from __future__ import annotations

import pytest

from openprogram.webui._model_catalog import fetchers as F
from openprogram.webui._model_catalog import providers as P
from openprogram.webui._model_catalog import storage as st
from openprogram.webui._model_catalog.fetchers import anthropic as A


# ── api-stamp consistency (drift guard) ───────────────────────────────────────

@pytest.mark.parametrize("pid", ["minimax", "minimax-cn"])
def test_default_api_matches_models_generated(pid):
    from openprogram.providers.models_generated import MODELS
    apis = {m.api for m in MODELS.values() if m.provider == pid}
    assert apis == {"anthropic-messages"}, f"{pid} models: {apis}"
    # The catalog's stamp map must agree with the static registry, or
    # fetched rows route to the wrong stream function.
    assert P._default_api_for(pid) == "anthropic-messages"


@pytest.mark.parametrize("pid,host", [
    ("minimax-coding-plan", "https://api.minimax.io/anthropic"),
    ("minimax-cn-coding-plan", "https://api.minimaxi.com/anthropic"),
])
def test_coding_plan_wired_as_anthropic_with_canonical_base(pid, host):
    # The community-only Token-Plan rows must be classified Anthropic-wire
    # (so credential probe + fetch + chat all use /v1/models, /v1/messages)
    # and pinned to a base WITHOUT the models.dev trailing /v1 that would
    # otherwise double under our api layer.
    from openprogram.webui._model_catalog.credentials import _kind_for
    assert P._default_api_for(pid) == "anthropic-messages"
    assert _kind_for(pid) == "anthropic_compat"
    assert P._default_base_override(pid) == host
    assert not host.endswith("/v1")


# ── dispatcher routing ────────────────────────────────────────────────────────

def test_fetch_dispatch_routes_anthropic_wire_to_anthropic_fetcher(monkeypatch):
    used = {"which": None}

    def fake_anthropic(provider_id, timeout):
        used["which"] = "anthropic"
        return [{"id": "MiniMax-M3", "name": "MiniMax-M3"}]

    def fake_openai(provider_id, timeout):  # must NOT be chosen
        used["which"] = "openai_compat"
        return {"error": "wrong fetcher"}

    monkeypatch.setattr(F, "_fetch_anthropic", fake_anthropic)
    monkeypatch.setattr(F, "_fetch_openai_compat", fake_openai)
    # Avoid touching config / network in the normalize+store tail.
    monkeypatch.setattr(
        st, "replace_fetched_models",
        lambda pid, models: {"added": len(models), "removed": 0,
                             "total": len(models), "dropped_enabled": []},
    )
    from openprogram.webui._model_catalog import sources as S
    monkeypatch.setattr(S, "enrich", lambda pid, mid: {})

    res = F.fetch_models_remote("minimax-cn", timeout=5.0)
    assert used["which"] == "anthropic"
    assert res.get("fetched") == 1 and "error" not in res


# ── generalized Anthropic fetcher hits the provider's own host ────────────────

def test_anthropic_fetcher_uses_provider_base_url(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self): return None
        def json(self): return {"data": [{"id": "MiniMax-M3", "display_name": "MiniMax-M3"}]}

    def fake_get(url, *, headers=None, timeout=15.0):
        seen["url"] = url
        seen["headers"] = headers or {}
        return _Resp()

    monkeypatch.setattr(st, "_resolve_api_key", lambda pid: "k")
    monkeypatch.setattr(st, "_resolve_base_url", lambda pid: "https://api.minimaxi.com/anthropic")
    import httpx
    monkeypatch.setattr(httpx, "get", fake_get)

    out = A._fetch_anthropic("minimax-cn", 5.0)
    assert isinstance(out, list) and out[0]["id"] == "MiniMax-M3"
    assert seen["url"] == "https://api.minimaxi.com/anthropic/v1/models"
    assert seen["headers"].get("x-api-key") == "k"
    assert "anthropic-version" in seen["headers"]


def test_anthropic_fetcher_native_still_uses_anthropic_host(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self): return None
        def json(self): return {"data": []}

    monkeypatch.setattr(st, "_resolve_api_key", lambda pid: "k")
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url) or _Resp())

    A._fetch_anthropic("anthropic", 5.0)
    assert seen["url"] == "https://api.anthropic.com/v1/models"
