"""Subscription-login writes DEFAULT models as config spec rows (no import seed).

Design (docs/design/providers/models/models.md §4.2): dynamic registration for
subscription providers = the program performs an enable on the user's behalf,
writing to the same config list. These tests pin:

  * no import-time dict seeding — the registry contains ONLY config spec rows;
  * a fresh login (zero spec rows) writes the default set once;
  * a provider that already has spec rows is NOT touched (a disabled default
    does not resurrect);
  * reload() rebuilds from config only — no seed resurrection.
"""
from __future__ import annotations

import openprogram.auth.login_enable as le
import openprogram.providers._config_read as cr
import openprogram.providers.enabled_models as mg
import openprogram.webui._model_listing.storage as st


def _mem_config(monkeypatch, initial: dict) -> dict:
    """Route storage reads/writes and the registry loader at one in-memory
    providers dict, so login-enable and reload share the same store."""
    store = {"providers": dict(initial)}
    monkeypatch.setattr(st, "_read_providers_cfg", lambda: store["providers"])
    monkeypatch.setattr(st, "_write_providers_cfg",
                        lambda p: store.__setitem__("providers", p))
    monkeypatch.setattr(cr, "read_providers_config",
                        lambda: store["providers"])
    return store


def test_no_import_seed_in_registry(monkeypatch):
    # With an empty config, the registry is empty — no claude-code / codex
    # rows appear from an import-time dict write.
    monkeypatch.setattr(cr, "read_providers_config", lambda: {})
    reg = mg._load()
    assert not any(k.startswith("claude-code/") for k in reg)
    assert not any(k.startswith("openai-codex/") for k in reg)


def test_fresh_login_writes_claude_code_defaults(monkeypatch):
    store = _mem_config(monkeypatch, {})
    written = le.enable_default_models_on_login("claude-code")
    assert set(written) == {"claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"}
    rows = store["providers"]["claude-code"]["models"]
    assert {r["id"] for r in rows} == set(written)
    assert all(r["source"] == "subscription-login" for r in rows)
    # And they are now in the live registry.
    assert "claude-code/claude-opus-4-8" in mg.ENABLED_MODELS


def test_fresh_login_writes_small_codex_default(monkeypatch):
    _mem_config(monkeypatch, {})
    written = le.enable_default_models_on_login("openai-codex")
    # SMALL set, not the whole seed list of 11.
    assert 1 <= len(written) <= 3
    assert "gpt-5.5" in written


def test_login_enable_idempotent_and_respects_disable(monkeypatch):
    # Provider already has spec rows (user enabled one, disabled the rest) →
    # login-enable is a no-op; the disabled defaults do not resurrect.
    store = _mem_config(monkeypatch, {
        "claude-code": {"enabled": True, "models": [
            {"id": "claude-opus-4-8", "name": "Opus", "api": "anthropic-messages"},
        ]},
    })
    written = le.enable_default_models_on_login("claude-code")
    assert written == []
    ids = {r["id"] for r in store["providers"]["claude-code"]["models"]}
    assert ids == {"claude-opus-4-8"}  # sonnet/haiku stayed disabled


def test_reload_has_no_seed_resurrection(monkeypatch):
    # C1 rewrite: reload() rebuilds from config spec rows only. A config with
    # NO claude-code rows yields a registry with NO claude-code rows after
    # reload — the deleted seed does not come back.
    _mem_config(monkeypatch, {
        "openai": {"models": [
            {"id": "gpt-x", "name": "GPT-X", "api": "openai-completions"}]},
    })
    mg.reload()
    assert "openai/gpt-x" in mg.ENABLED_MODELS
    assert not any(k.startswith("claude-code/") for k in mg.ENABLED_MODELS)


def test_reload_preserves_config_claude_code_rows(monkeypatch):
    # When claude-code IS in config (post-login), reload keeps its rows —
    # they live in config now, not in a dynamic seed.
    _mem_config(monkeypatch, {
        "claude-code": {"enabled": True, "models": [
            {"id": "claude-opus-4-8", "name": "Opus",
             "api": "anthropic-messages", "source": "subscription-login"}]},
    })
    mg.reload()
    assert "claude-code/claude-opus-4-8" in mg.ENABLED_MODELS


def test_seed_default_when_credentials_exist(monkeypatch):
    _mem_config(monkeypatch, {})
    monkeypatch.setattr(le, "_has_credentials", lambda pid: pid == "claude-code")
    assert le.seed_default_models_if_logged_in("claude-code")
    assert le.seed_default_models_if_logged_in("openai-codex") == []
