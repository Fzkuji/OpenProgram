"""Provider-settings invariants: default-model gating, stale-default
clearing, toggle broadcasts, custom-provider add_mode.

These nail the cross-module rules from commits 66cb7f73 / cb78dde1 /
f09ed1c2 — disabling the current default must forget it everywhere, the
settings routes must gate the advertised default on the enabled set, and
every toggle path must broadcast so other tabs refetch. They regress the
moment someone refactors the routes without re-wiring the event bus or the
default-clearing helper.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---- 3. GET /api/agent_settings gates on the enabled set ----------------

@pytest.fixture
def settings_client(monkeypatch):
    """Minimal app with just the runtime routes; server side effects
    stubbed so we exercise only the enabled-set gating."""
    from openprogram.webui import server as _s
    from openprogram.webui.routes import runtime as _runtime

    monkeypatch.setattr(_s, "_init_providers", lambda: None)
    monkeypatch.setattr(_s, "_get_thinking_config_for_model",
                        lambda p, m: None)
    _rm = _s._runtime_management
    monkeypatch.setattr(_rm, "_chat_provider", "openai")
    monkeypatch.setattr(_rm, "_chat_model", "gpt-5.5")
    monkeypatch.setattr(_rm, "_exec_provider", "openai")
    monkeypatch.setattr(_rm, "_exec_model", "gpt-5.5")

    app = FastAPI()
    _runtime.register(app)
    return TestClient(app), _rm


def test_agent_settings_none_when_default_disabled(settings_client, monkeypatch):
    c, _rm = settings_client
    # Current default fell out of the enabled set → gate it to None.
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: False)
    body = c.get("/api/agent_settings").json()
    assert body["chat"]["provider"] is None
    assert body["chat"]["model"] is None
    assert body["exec"]["provider"] is None
    assert body["exec"]["model"] is None


def test_agent_settings_kept_when_default_enabled(settings_client, monkeypatch):
    c, _rm = settings_client
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: True)
    body = c.get("/api/agent_settings").json()
    assert body["chat"]["provider"] == "openai"
    assert body["chat"]["model"] == "gpt-5.5"


# ---- 4. _clear_stale_defaults (providers.py) ----------------------------
# The helper is a closure inside providers.register, so we drive it through
# the toggle route that calls it rather than reaching into route globals.

@pytest.fixture
def providers_client(monkeypatch):
    """App with the providers routes; model-listing + event bus + agent
    manager all stubbed so toggles run without real state."""
    from openprogram.webui import server as _s
    from openprogram.webui.routes import providers as _providers
    from openprogram.webui import _model_listing as _mc

    # toggle_model / toggle_provider / delete_custom_provider return ok.
    monkeypatch.setattr(_mc, "toggle_model", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(_mc, "toggle_provider", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(_mc, "delete_custom_provider",
                        lambda *a, **k: {"ok": True})

    frames = []
    monkeypatch.setattr(
        "openprogram.agent.event_bus.emit_ws_frame",
        lambda frame: frames.append(frame),
    )

    updates = []
    from openprogram.agent.management import manager as _agents
    monkeypatch.setattr(_agents, "update",
                        lambda aid, patch: updates.append((aid, patch)))

    app = FastAPI()
    _providers.register(app)
    return TestClient(app), _s._runtime_management, frames, updates


def test_clear_stale_defaults_clears_disabled_default(providers_client, monkeypatch):
    c, _rm, frames, updates = providers_client
    monkeypatch.setattr(_rm, "_chat_provider", "openai")
    monkeypatch.setattr(_rm, "_chat_model", "gpt-5.5")
    monkeypatch.setattr(_rm, "_exec_provider", "openai")
    monkeypatch.setattr(_rm, "_exec_model", "gpt-5.5")
    # Disabling made the current default fall out of the enabled set.
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: False)

    r = c.post("/api/providers/openai/models/gpt-5.5/toggle",
               json={"enabled": False})
    assert r.status_code == 200
    # Globals nulled.
    assert _rm._chat_provider is None and _rm._chat_model is None
    assert _rm._exec_provider is None and _rm._exec_model is None
    # Default agent's agent.json model blanked.
    from openprogram.agent.management import manager as _agents
    assert (_agents.DEFAULT_AGENT_ID,
            {"model": {"provider": "", "id": ""}}) in updates


def test_clear_stale_defaults_leaves_unrelated_default(providers_client, monkeypatch):
    c, _rm, frames, updates = providers_client
    monkeypatch.setattr(_rm, "_chat_provider", "openai")
    monkeypatch.setattr(_rm, "_chat_model", "gpt-5.5")
    monkeypatch.setattr(_rm, "_exec_provider", "openai")
    monkeypatch.setattr(_rm, "_exec_model", "gpt-5.5")
    # The current default is still enabled — toggling an unrelated model
    # must not touch it.
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: True)

    c.post("/api/providers/anthropic/models/claude/toggle",
           json={"enabled": False})
    assert _rm._chat_provider == "openai" and _rm._chat_model == "gpt-5.5"
    assert _rm._exec_provider == "openai"
    assert updates == []  # agent.json untouched


# ---- 5. toggle routes broadcast agent_settings_changed ------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/api/providers/openai/toggle"),
    ("post", "/api/providers/openai/models/gpt-5.5/toggle"),
    ("delete", "/api/providers/custom/mine"),
])
def test_toggle_routes_broadcast_settings_changed(providers_client, monkeypatch,
                                                  method, path):
    c, _rm, frames, updates = providers_client
    # Default stays enabled so _clear_stale_defaults is a no-op and the
    # only frame is the broadcast we're asserting on.
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: True)

    if method == "post":
        c.post(path, json={"enabled": True})
    else:
        c.delete(path)

    assert any(f.get("type") == "agent_settings_changed" for f in frames), frames


def test_delete_custom_provider_no_broadcast_on_failure(providers_client, monkeypatch):
    c, _rm, frames, updates = providers_client
    from openprogram.webui import _model_listing as _mc
    monkeypatch.setattr(_mc, "delete_custom_provider",
                        lambda *a, **k: {"ok": False, "error": "not custom"})
    monkeypatch.setattr(_rm, "_default_is_enabled", lambda p, m: True)
    r = c.delete("/api/providers/custom/builtin")
    assert r.status_code == 400
    # Refusal → no settings-changed frame.
    assert not any(f.get("type") == "agent_settings_changed" for f in frames)


# ---- 6. custom-provider accounts add_mode=api_key -----------------------

def test_custom_provider_api_key_env_synthesized(monkeypatch):
    """_api_key_env returns a non-empty synthesized env for a custom
    provider (env_vars_for gives nothing), so _generic_summary flips
    add_mode to api_key and the web form shows the key-paste box."""
    from openprogram.webui.routes import accounts as _acc

    monkeypatch.setattr("openprogram.providers.env_api_keys.env_vars_for",
                        lambda p: [])
    monkeypatch.setattr(
        "openprogram.webui._model_listing.storage._is_custom_provider",
        lambda p: True,
    )
    monkeypatch.setattr(
        "openprogram.webui._model_listing.providers._synth_env_var",
        lambda p: "OPENPROGRAM_CUSTOM_MINE_KEY",
    )
    env = _acc._api_key_env("mine")
    assert env == "OPENPROGRAM_CUSTOM_MINE_KEY"
    assert env != ""


def test_non_custom_provider_no_synthesized_env(monkeypatch):
    from openprogram.webui.routes import accounts as _acc
    monkeypatch.setattr("openprogram.providers.env_api_keys.env_vars_for",
                        lambda p: [])
    monkeypatch.setattr(
        "openprogram.webui._model_listing.storage._is_custom_provider",
        lambda p: False,
    )
    assert _acc._api_key_env("some-builtin") == ""
