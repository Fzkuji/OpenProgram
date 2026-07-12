"""Model-list fetch routing for Anthropic-wire third-party providers.

MiniMax (minimax / minimax-cn) ships its API in the Anthropic Messages
wire format (base_url ends in ``/anthropic``). Three things must line up
or the provider half-works in confusing ways:

  * the fetch dispatcher must route it to the Anthropic ``/v1/models``
    fetcher (the OpenAI-compatible ``GET /models`` 404s on its host);
  * that fetcher must hit the provider's OWN base_url, not
    api.anthropic.com;
  * ``_PROVIDER_DEFAULT_API`` must stamp fetched/custom rows
    ``anthropic-messages`` (matching enabled_models) so chat routes to
    the right stream function instead of POST /chat/completions.

No network: httpx + storage resolvers are stubbed.
"""
from __future__ import annotations

import pytest

from openprogram.webui._model_listing import fetchers as F
from openprogram.webui._model_listing import providers as P
from openprogram.webui._model_listing import storage as st
from openprogram.providers.anthropic import list_models as A


# api-stamp consistency (drift guard)

@pytest.mark.parametrize("pid", ["minimax", "minimax-cn"])
def test_default_api_matches_enabled_models(pid, monkeypatch):
    # Post-Task-3 the registry holds only enabled rows, so enable one
    # MiniMax model (which inherits its anthropic-messages wire from
    # provider.json) and rebuild the registry. The row's api must then
    # agree with the catalog's derived stamp — otherwise a fetched row
    # would route to the wrong stream function.
    import openprogram.providers._config_read as cr
    import openprogram.providers.enabled_models as mg
    monkeypatch.setattr(
        cr, "read_providers_config",
        lambda: {pid: {"models": [{"id": "MiniMax-M2", "name": "MiniMax M2"}]}},
    )
    reg = mg._load()
    apis = {m.api for m in reg.values() if m.provider == pid}
    assert apis == {"anthropic-messages"}, f"{pid} models: {apis}"
    # The catalog's stamp map must agree with the enabled row, or fetched
    # rows route to the wrong stream function.
    assert P._default_api_for(pid) == "anthropic-messages"


@pytest.mark.parametrize("md_base,canonical", [
    ("https://api.minimaxi.com/anthropic/v1", "https://api.minimaxi.com/anthropic"),
    ("https://api.minimax.io/anthropic/v1", "https://api.minimax.io/anthropic"),
    ("https://foo.example/anthropic", "https://foo.example/anthropic"),
])
def test_community_anthropic_wire_derived_and_base_normalized(monkeypatch, md_base, canonical):
    # A community-only provider (no static row) whose models.dev base is an
    # …/anthropic endpoint must be auto-classified Anthropic-wire and have
    # any trailing /v1 stripped — no per-provider table entry. This is the
    # general mechanism that replaced the MiniMax-specific override.
    pid = "some-community-plan"
    from openprogram.webui._model_listing import providers as cat
    from openprogram.webui._model_listing import storage as st
    from openprogram.webui._model_listing.credentials import _kind_for
    import openprogram.providers as PR

    monkeypatch.setattr(cat, "_default_base_url_for", lambda p: md_base)
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {})
    monkeypatch.setattr(PR, "get_models", lambda p=None: [])

    assert cat._default_api_for(pid) == "anthropic-messages"  # via /anthropic heuristic
    assert _kind_for(pid) == "anthropic_compat"
    assert st._resolve_base_url(pid) == canonical             # trailing /v1 stripped


def test_provider_default_api_table_is_empty_by_default():
    # Everything derives — the manual override table must stay empty so no
    # one re-introduces per-provider drift. (Add entries only to override a
    # enabled_models mislabel; if you do, document why here.)
    assert P._PROVIDER_DEFAULT_API == {}


# dispatcher routing

def test_fetch_dispatch_routes_anthropic_wire_to_anthropic_fetcher(monkeypatch):
    used = {"which": None}

    def fake_anthropic(provider_id, timeout):
        used["which"] = "anthropic"
        return [{"id": "MiniMax-M3", "name": "MiniMax-M3"}]

    def fake_openai(provider_id, timeout):  # must NOT be chosen
        used["which"] = "openai_compat"
        return {"error": "wrong fetcher"}

    monkeypatch.setattr(A, "fetch", fake_anthropic)
    monkeypatch.setattr(F, "_fetch_openai_compat", fake_openai)
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S, "enrich", lambda pid, mid: {})

    # Routing + normalize now live in fetch_and_normalize (no persistence).
    res = F.fetch_and_normalize("minimax-cn", timeout=5.0)
    assert used["which"] == "anthropic"
    assert "error" not in res
    assert [m["id"] for m in res["models"]] == ["MiniMax-M3"]


# generalized Anthropic fetcher hits the provider's own host

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

    out = A.fetch("minimax-cn", 5.0)
    assert isinstance(out, list) and out[0]["id"] == "MiniMax-M3"
    assert seen["url"] == "https://api.minimaxi.com/anthropic/v1/models"
    assert seen["headers"].get("x-api-key") == "k"
    assert "anthropic-version" in seen["headers"]


def test_claude_code_browse_borrows_anthropic_with_empty_registry(monkeypatch):
    # I1 regression: claude-code has no list-models API of its own — it IS the
    # anthropic Claude catalog. Post-migration ENABLED_MODELS is empty on a
    # fresh install, so the old "iterate ENABLED_MODELS for anthropic rows"
    # fetcher yielded nothing. Browse must instead borrow anthropic's data.
    import openprogram.providers.enabled_models as mg
    from openprogram.webui._model_listing import listing, provider_models as pm
    from openprogram.webui._model_listing import providers as cat

    monkeypatch.setattr(mg, "ENABLED_MODELS", {})  # empty registry
    listing._reset_browse_cache()
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: {})  # no ambient config
    # No credential → browse borrows anthropic's models.dev rows (the
    # _SUBSCRIPTION_BORROW map), NOT the registry.
    monkeypatch.setattr(cat, "_is_configured", lambda pid: False)
    borrowed = {"claude-opus-4-8": {"name": "Claude Opus 4.8"},
                "claude-haiku-4-5": {"name": "Claude Haiku 4.5"}}
    # _models_dev_for already borrows anthropic for claude-code; assert it does.
    assert pm._SUBSCRIPTION_BORROW["claude-code"] == "anthropic"
    monkeypatch.setattr(pm, "_models_dev_for",
                        lambda pid: borrowed if pid == "claude-code" else {})

    rows = listing.list_models_for_provider("claude-code")
    ids = {r["id"] for r in rows}
    assert ids == {"claude-opus-4-8", "claude-haiku-4-5"}


def test_claude_code_fetch_routes_to_anthropic(monkeypatch):
    # The Fetch button for claude-code hits anthropic's live /v1/models (Bearer
    # OAuth), same as the anthropic provider — no dead ENABLED_MODELS scan.
    # claude-code has no directory of its own, so the dispatcher maps it to
    # anthropic's list_models module by convention override.
    assert F._load_fetcher("claude-code") is A.fetch
    used = {"which": None}
    # Patch anthropic's fetch so we can see claude-code route through it.
    fake = (lambda pid, timeout: used.__setitem__("which", pid) or
            [{"id": "claude-opus-4-8", "name": "Claude Opus 4.8"}])
    monkeypatch.setattr(A, "fetch", fake)
    from openprogram.webui._model_listing import sources as S
    monkeypatch.setattr(S, "enrich", lambda pid, mid: {})
    res = F.fetch_and_normalize("claude-code", timeout=5.0)
    assert used["which"] == "claude-code"
    assert [m["id"] for m in res["models"]] == ["claude-opus-4-8"]


def _stub_codex_endpoint(monkeypatch, payload):
    """Point the codex fetcher at a fake account/models endpoint response."""
    from openprogram.providers.openai_codex import oauth as _oauth
    from openprogram.providers.openai_codex import openai_codex as _oc
    from openprogram.providers.openai_codex import list_models as C

    monkeypatch.setattr(_oc, "_resolve_codex_bearer_token", lambda *_: "tok")
    monkeypatch.setattr(_oauth, "_get_account_id_from_jwt", lambda *_: "acct")

    class _Resp:
        def raise_for_status(self): return None
        def json(self): return payload

    seen = {}
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url, headers=kw.get("headers")) or _Resp())
    return C, seen


def test_codex_browse_does_not_grow_registry(monkeypatch):
    # Browse/Fetch is a READ path: it must NOT write ENABLED_MODELS (post-
    # migration the registry means "enabled"; bulk-registering every
    # browsable id floods the chat picker — live server showed openai-codex=14
    # when the user enabled 1). Dispatchability comes from enable-time config
    # rows + the runtime's on-miss single-model register.
    import openprogram.providers.enabled_models as mg

    C, seen = _stub_codex_endpoint(monkeypatch, {"models": [
        {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna",
         "context_window": 372000, "visibility": "list",
         "service_tiers": [{"id": "priority"}],
         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
        {"slug": "gpt-5.4-mini", "display_name": "GPT-5.4-Mini",
         "context_window": 272000, "visibility": "list",
         "service_tiers": [],
         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
        {"slug": "codex-auto-review", "visibility": "hide"},
    ]})

    reg = {"openai-codex/gpt-5.5": object()}  # a pre-existing enabled codex row
    monkeypatch.setattr(mg, "ENABLED_MODELS", reg)
    before = dict(reg)

    out = C.fetch("openai-codex", timeout=5.0)

    # Hidden helper models dropped; the account's real ids surface.
    assert {r["id"] for r in out} == {"gpt-5.6-luna", "gpt-5.4-mini"}
    # Fast + thinking come straight from the endpoint, no id-family guessing.
    by_id = {r["id"]: r for r in out}
    assert by_id["gpt-5.6-luna"]["fast"] is True
    assert by_id["gpt-5.4-mini"]["fast"] is False  # empty service_tiers
    assert by_id["gpt-5.6-luna"]["thinking_levels"] == ["low", "high"]
    # We present the real CLI identity so listing and dispatch agree.
    assert seen["headers"]["originator"] == "codex_cli_rs"
    assert reg == before, "browse must not register browsed ids into ENABLED_MODELS"


def test_codex_fetch_drops_ultra_and_needs_token(monkeypatch):
    # ``ultra`` isn't a wire-valid effort here → filtered out. And with no
    # token the fetch errors (keeps the saved list) instead of blanking.
    C, _ = _stub_codex_endpoint(monkeypatch, {"models": [
        {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol",
         "visibility": "list", "service_tiers": [{"id": "priority"}],
         "supported_reasoning_levels": [
             {"effort": "high"}, {"effort": "max"}, {"effort": "ultra"}]},
    ]})
    out = C.fetch("openai-codex", timeout=5.0)
    assert out[0]["thinking_levels"] == ["high", "max"]  # ultra dropped

    from openprogram.providers.openai_codex import openai_codex as _oc
    monkeypatch.setattr(_oc, "_resolve_codex_bearer_token", lambda *_: "")
    err = C.fetch("openai-codex", timeout=5.0)
    assert isinstance(err, dict) and "error" in err


def test_anthropic_fetcher_native_still_uses_anthropic_host(monkeypatch):
    seen = {}

    class _Resp:
        def raise_for_status(self): return None
        def json(self): return {"data": []}

    monkeypatch.setattr(st, "_resolve_api_key", lambda pid: "k")
    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url) or _Resp())

    A.fetch("anthropic", 5.0)
    assert seen["url"] == "https://api.anthropic.com/v1/models"
