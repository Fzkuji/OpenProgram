"""Global model choice must round-trip through config.json.

Two halves of the same bug: the top-bar switch never wrote
``default_provider``/``default_model``, and startup ignored them anyway —
so every restart reverted to the head of ``_PROVIDER_PRIORITY``.
"""
from __future__ import annotations

import json

import pytest

from openprogram.webui import _runtime_management as rm
from openprogram.webui._model_listing import storage


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """Point every config reader/writer at a temp file — never the user's."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("openprogram.paths.get_config_path", lambda: p)

    def _read():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(cfg):
        p.write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setattr("openprogram.webui.server._load_config", _read)
    monkeypatch.setattr("openprogram.webui.server._save_config", _write)
    return p


def _write_cfg(path, **kw):
    path.write_text(json.dumps(kw), encoding="utf-8")


class _FakeRT:
    def __init__(self, model):
        self.model = model


# --- read side ------------------------------------------------------------

def test_probe_order_puts_config_provider_first(cfg_path):
    _write_cfg(cfg_path, default_provider="anthropic")
    order = rm._probe_order()
    assert order[0] == "anthropic"
    # no duplicates, nothing dropped
    assert sorted(order) == sorted(set(rm._PROVIDER_PRIORITY) | {"anthropic"})


def test_probe_order_falls_back_to_hardcoded_priority(cfg_path):
    assert rm._probe_order() == list(rm._PROVIDER_PRIORITY)


def test_config_model_wins_over_first_available(cfg_path):
    _write_cfg(cfg_path, default_provider="anthropic", default_model="opus-x")
    rt = _FakeRT("sonnet-x")
    rm._apply_config_default_model("anthropic", rt, ["sonnet-x", "opus-x"])
    assert rt.model == "opus-x"


def test_config_model_ignored_for_other_provider(cfg_path):
    _write_cfg(cfg_path, default_provider="anthropic", default_model="opus-x")
    rt = _FakeRT("gpt-x")
    rm._apply_config_default_model("openai", rt, ["gpt-x"])
    assert rt.model == "gpt-x"


def test_disabled_config_model_falls_back_not_blank(cfg_path, monkeypatch):
    """A model the user has since disabled must not blank the top bar."""
    _write_cfg(cfg_path, default_provider="anthropic", default_model="gone-x")
    monkeypatch.setattr(rm, "_default_is_enabled", lambda p, m: False)
    rt = _FakeRT("sonnet-x")
    rm._apply_config_default_model("anthropic", rt, ["sonnet-x"])
    assert rt.model == "sonnet-x"


def test_prefixed_config_model_matches_bare_id(cfg_path):
    _write_cfg(cfg_path, default_provider="anthropic",
               default_model="anthropic:opus-x")
    rt = _FakeRT("sonnet-x")
    rm._apply_config_default_model("anthropic", rt, ["sonnet-x", "opus-x"])
    assert rt.model == "opus-x"


# --- write side -----------------------------------------------------------

def test_save_default_model_persists(cfg_path):
    storage.save_default_model("anthropic", "opus-x")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["default_provider"] == "anthropic"
    assert cfg["default_model"] == "opus-x"


def test_save_default_model_strips_provider_prefix(cfg_path):
    storage.save_default_model("anthropic", "anthropic:opus-x")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["default_model"] == "opus-x"


def test_save_default_model_preserves_other_keys(cfg_path):
    _write_cfg(cfg_path, providers={"anthropic": {"enabled": True}})
    storage.save_default_model("anthropic", "opus-x")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["providers"] == {"anthropic": {"enabled": True}}
    assert cfg["default_model"] == "opus-x"


def test_round_trip_switch_then_restart(cfg_path):
    """The actual bug: switch → restart → still the chosen model."""
    storage.save_default_model("anthropic", "opus-x")
    assert rm._probe_order()[0] == "anthropic"
    rt = _FakeRT("sonnet-x")
    rm._apply_config_default_model("anthropic", rt, ["sonnet-x", "opus-x"])
    assert rt.model == "opus-x"


# --- all three global write entry points route through save_default_model --

def test_all_global_entry_points_persist(monkeypatch):
    """REST /api/model, /api/agent_settings exec, and the ws switch_model
    global branch must each call ``save_default_model``."""
    import inspect
    from openprogram.webui.routes import runtime as rest
    from openprogram.webui.ws_actions import runtime as ws

    rest_src = inspect.getsource(rest)
    assert rest_src.count("save_default_model") >= 4  # 2 imports + 2 calls
    assert "save_default_model" in inspect.getsource(ws.handle_switch_model)
