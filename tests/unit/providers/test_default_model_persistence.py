"""Global model choice must round-trip through config.json.

Two halves of the same bug: the top-bar switch never wrote
``default_provider``/``default_model``, and startup ignored them anyway —
so every restart reverted to the head of ``_PROVIDER_PRIORITY``.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from openprogram.webui import _runtime_management as rm
from openprogram.providers import storage


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """Point every config reader/writer at a temp file — never the user's."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.setup.get_config_path", lambda: p)

    def _read():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(cfg):
        p.write_text(json.dumps(cfg), encoding="utf-8")
        os.chmod(p, 0o600)

    monkeypatch.setattr("openprogram.setup._read_config", _read)
    monkeypatch.setattr("openprogram.setup._write_config", _write)
    return p


def _write_cfg(path, **kw):
    path.write_text(json.dumps(kw), encoding="utf-8")
    os.chmod(path, 0o600)


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


def test_session_global_fallback_preserves_runtime_prefixed_model(monkeypatch):
    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {
        ("minimax-cn-coding-plan", "MiniMax-M3"),
    })
    monkeypatch.setattr(rm, "_chat_provider", "minimax-cn-coding-plan")
    monkeypatch.setattr(
        rm,
        "_chat_model",
        "minimax-cn-coding-plan:MiniMax-M3",
    )
    assert rm._resolve_session_provider_model({"id": "s"}) == (
        "minimax-cn-coding-plan",
        "minimax-cn-coding-plan:MiniMax-M3",
    )


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


# --- per-session switch pins the picker choice, both entry points --------

def test_session_switch_sets_picker_overrides():
    """The dispatcher reads the session's model pin from
    ``provider_override`` / ``model_override``, and restart restore only
    trusts those keys. Both switch entry points (REST and ws) must set
    them and persist — a path that only writes ``provider_name`` leaves
    the session re-resolving through the enabled list on every turn
    (answers on a different model than the chip shows, and the pin is
    lost across worker restarts)."""
    import inspect
    from openprogram.webui.routes import runtime as rest
    from openprogram.webui.ws_actions import runtime as ws

    for src in (inspect.getsource(rest), inspect.getsource(ws.handle_switch_model)):
        assert 'conv["provider_override"]' in src
        assert 'conv["model_override"]' in src
        assert "_save_session" in src


def test_restored_session_function_run_uses_its_agent_model(monkeypatch):
    from openprogram.webui import server
    from openprogram.webui.routes import chat as routes_chat

    class Persist:
        def list_sessions(self):
            return [("agent-custom", "restored-s1")]

        def load_session(self, agent_id, session_id):
            return {
                "agent_id": agent_id,
                "title": "restored",
                "messages": [],
            }

    monkeypatch.setattr(server, "_persist", Persist())
    monkeypatch.setattr(server, "_sessions", {})
    server._restore_sessions()
    conv = server._sessions["restored-s1"]

    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {
        ("agent-provider", "agent-model"),
        ("global-provider", "global-model"),
    })
    monkeypatch.setattr(rm, "_chat_provider", "global-provider")
    monkeypatch.setattr(rm, "_chat_model", "global-model")
    monkeypatch.setattr(
        "openprogram.agent.management.manager.get",
        lambda agent_id: SimpleNamespace(
            model=SimpleNamespace(
                provider="agent-provider",
                id="agent-model",
            ),
        ),
    )
    monkeypatch.setattr(server, "_get_or_create_session", lambda sid=None: conv)
    monkeypatch.setattr(server, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(server, "_default_agent_id", lambda: "main")
    monkeypatch.setattr(server, "_emit_running_task_event", lambda *a: None)
    monkeypatch.setattr(
        "openprogram.webui.ws_actions.session.broadcast_sessions_list",
        lambda: None,
    )

    class Tool:
        name = "agentic_probe"
        _is_agentic = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools",
        lambda names=None: [Tool()],
    )
    monkeypatch.setattr(
        "openprogram.programs._runtime.get",
        lambda name, *a, **k: Tool() if name == Tool.name else None,
    )

    class DB:
        def message_exists(self, session_id, msg_id):
            return True

        def get_session(self, session_id):
            return {"agent_id": "agent-custom", "created_at": 1}

        def update_session(self, *args, **kwargs):
            pass

    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: DB())

    captured = {}
    from openprogram.agent import production_driver
    from openprogram.execution.model import ExecutionStatus
    real_adapter = production_driver.CanonicalAgentAdapter

    class _Adapter:
        def __init__(self, *args, **kwargs):
            self._real = real_adapter(*args, **kwargs)

        def admit_payload(self, **kwargs):
            captured.update(kwargs["payload"])
            return self._real.admit_payload(**kwargs)

        async def activate(self, admission, *, on_activated=None):
            service = self._real.driver._control_service()
            attempt, leased = service.attempts.lease(
                admission.execution_id,
                expected_version=admission.status_version,
                owner_id="unit-test",
                ttl_seconds=30,
            )
            active, running = service.attempts.activate(
                attempt.attempt_id,
                generation=attempt.generation,
                expected_execution_version=leased.status_version,
            )
            service.finish_attempt(
                attempt_id=active.attempt_id,
                generation=active.generation,
                expected_execution_version=running.status_version,
                target=ExecutionStatus.COMPLETED,
                outcome="completed",
            )
            activation = SimpleNamespace(
                admission=admission, status_version=running.status_version,
            )
            if on_activated is not None:
                on_activated(activation)
            return activation, SimpleNamespace(failed=False, error=None)

        def fail_admission(self, *args, **kwargs):
            return self._real.fail_admission(*args, **kwargs)

    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", _Adapter)
    monkeypatch.setattr(
        "openprogram.agentic_programming.function.create_pending_call_node",
        lambda **k: None,
    )

    def inline_thread(target=None, args=(), kwargs=None, daemon=None):
        return SimpleNamespace(
            start=lambda: target(*(args or ()), **(kwargs or {})),
            is_alive=lambda: False,
        )

    monkeypatch.setattr(
        routes_chat,
        "threading",
        SimpleNamespace(Thread=inline_thread),
    )

    result = routes_chat.run_agentic_function_call(
        "agentic_probe", {}, "restored-s1",
    )
    assert "error" not in result
    assert captured["provider"] == "agent-provider"
    assert captured["model"] == "agent-model"
