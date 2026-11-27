"""New chats start on the last used model; there is no default model.

The model the user last picked or sent with is recorded in config.json
and survives a restart. A fresh install has none, so a new chat has no
model until the user picks one. An install from before the record adopts
its newest conversation's model once.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from openprogram.interfaces.server import runtime_management as rm
from openprogram.models.providers.catalog import last_used


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """Point every config reader/writer at a temp file — never the user's."""
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)

    def _read():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write(cfg):
        p.write_text(json.dumps(cfg), encoding="utf-8")
        os.chmod(p, 0o600)

    monkeypatch.setattr("openprogram.state.config.read_config", _read)
    monkeypatch.setattr("openprogram.state.config.write_config", _write)
    monkeypatch.setattr(last_used, "_newest_session_model", lambda: None)
    monkeypatch.setattr(last_used, "_release_default_agent_model", lambda: None)
    return p


def _write_cfg(path, **kw):
    path.write_text(json.dumps(kw), encoding="utf-8")
    os.chmod(path, 0o600)


class _FakeRT:
    def __init__(self, model):
        self.model = model


# --- read side ------------------------------------------------------------

def _last(provider, model):
    return {"last_used_model": {"provider": provider, "model": model}}


def test_fresh_install_has_no_model(cfg_path):
    assert last_used.read_last_used_model() is None
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["last_used_model"] == {}


def test_probe_order_puts_last_used_provider_first(cfg_path):
    _write_cfg(cfg_path, **_last("anthropic", "opus-x"))
    order = rm._probe_order()
    assert order[0] == "anthropic"
    # no duplicates, nothing dropped
    assert sorted(order) == sorted(set(rm._PROVIDER_PRIORITY) | {"anthropic"})


def test_probe_order_falls_back_to_hardcoded_priority(cfg_path):
    assert rm._probe_order() == list(rm._PROVIDER_PRIORITY)


def test_last_used_model_wins_over_first_available(cfg_path):
    _write_cfg(cfg_path, **_last("anthropic", "opus-x"))
    rt = _FakeRT("sonnet-x")
    rm._apply_last_used_model("anthropic", rt, ["sonnet-x", "opus-x"])
    assert rt.model == "opus-x"


def test_last_used_model_ignored_for_other_provider(cfg_path):
    _write_cfg(cfg_path, **_last("anthropic", "opus-x"))
    rt = _FakeRT("gpt-x")
    rm._apply_last_used_model("openai", rt, ["gpt-x"])
    assert rt.model == "gpt-x"


def test_disabled_last_used_model_falls_back_not_blank(cfg_path, monkeypatch):
    """A model the user has since disabled must not blank the runtime."""
    _write_cfg(cfg_path, **_last("anthropic", "gone-x"))
    monkeypatch.setattr(rm, "_default_is_enabled", lambda p, m: False)
    rt = _FakeRT("sonnet-x")
    rm._apply_last_used_model("anthropic", rt, ["sonnet-x"])
    assert rt.model == "sonnet-x"


def test_new_chat_shows_last_used_model_or_nothing(cfg_path, monkeypatch):
    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {("anthropic", "opus-x")})
    assert rm._get_provider_info()["model"] is None
    last_used.record_last_used_model("anthropic", "opus-x")
    info = rm._get_provider_info()
    assert (info["provider"], info["model"]) == ("anthropic", "opus-x")


def test_disabled_last_used_model_is_not_offered(cfg_path, monkeypatch):
    _write_cfg(cfg_path, **_last("anthropic", "gone-x"))
    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {("anthropic", "opus-x")})
    assert rm._last_used_chat_model() == (None, None)
    assert rm._resolve_session_provider_model({"id": None}) == (None, None)


def test_forgotten_model_stays_forgotten(cfg_path, monkeypatch):
    last_used.record_last_used_model("anthropic", "opus-x")
    monkeypatch.setattr(
        last_used, "_newest_session_model", lambda: ("anthropic", "opus-x")
    )
    last_used.forget_last_used_model()
    # Empty record, not a missing one: the old conversation is not re-adopted.
    assert last_used.read_last_used_model() is None


def test_disabling_the_last_used_model_forgets_it():
    from pathlib import Path

    src = Path("openprogram/interfaces/server/routes/identity/providers.py")
    body = src.read_text(encoding="utf-8").split("def _clear_stale_defaults", 1)[1]
    body = body.split("def _broadcast_settings_changed", 1)[0]
    assert "forget_last_used_model()" in body


def test_newest_conversation_model_reads_each_pin(monkeypatch):
    # Listing rows carry no model; the pin is read per conversation.
    rows = [{"id": "a", "title": "no pin"}, {"id": "b"}, {"id": "c"}]
    monkeypatch.setattr(
        "openprogram.runtime.context.session_db.default_db",
        lambda: SimpleNamespace(list_sessions=lambda **_kw: rows),
    )
    pins = {"b": ("openai", "openai:gpt-x"), "c": ("deepseek", "flash")}
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_settings.session_model.read_session_chat_model",
        lambda sid: pins.get(sid, (None, None)),
    )
    assert last_used._newest_session_model() == ("openai", "gpt-x")


def test_existing_install_adopts_newest_conversation_model(cfg_path, monkeypatch):
    released = []
    _write_cfg(cfg_path, default_provider="deepseek", default_model="deepseek-flash")
    monkeypatch.setattr(last_used, "_newest_session_model", lambda: ("openai", "gpt-6.1-sol"))
    monkeypatch.setattr(last_used, "_release_default_agent_model", lambda: released.append(True))
    assert last_used.read_last_used_model() == ("openai", "gpt-6.1-sol")
    assert last_used.read_last_used_model() == ("openai", "gpt-6.1-sol")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "default_provider" not in cfg and "default_model" not in cfg
    assert released == [True]


def test_unreadable_conversations_do_not_record_nothing(cfg_path, monkeypatch):
    def fail():
        raise OSError("synthetic")

    monkeypatch.setattr(last_used, "_newest_session_model", fail)
    assert last_used.read_last_used_model() is None
    assert "last_used_model" not in json.loads(cfg_path.read_text(encoding="utf-8"))


def test_adoption_releases_the_default_agent_model(monkeypatch):
    from openprogram.runtime.agent_registry import manager

    patches = []
    spec = SimpleNamespace(id="main", model=SimpleNamespace(provider="deepseek", id="deepseek-flash"))
    monkeypatch.setattr(manager, "get_default", lambda: spec)
    monkeypatch.setattr(manager, "update", lambda agent_id, patch: patches.append((agent_id, patch)))
    last_used._release_default_agent_model()
    assert patches == [("main", {"model": {"provider": "", "id": ""}})]


# --- write side -----------------------------------------------------------

def test_record_persists(cfg_path):
    last_used.record_last_used_model("anthropic", "opus-x")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["last_used_model"] == {"provider": "anthropic", "model": "opus-x"}


def test_record_strips_provider_prefix(cfg_path):
    last_used.record_last_used_model("anthropic", "anthropic:opus-x")
    assert last_used.read_last_used_model() == ("anthropic", "opus-x")


def test_record_preserves_other_keys_and_drops_legacy_default(cfg_path):
    _write_cfg(cfg_path, providers={"anthropic": {"enabled": True}},
               default_provider="openai", default_model="gpt-x")
    last_used.record_last_used_model("anthropic", "opus-x")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert cfg["providers"] == {"anthropic": {"enabled": True}}
    assert "default_provider" not in cfg and "default_model" not in cfg


def test_round_trip_switch_then_restart(cfg_path):
    """Switch → restart → still the chosen model."""
    last_used.record_last_used_model("anthropic", "opus-x")
    assert rm._probe_order()[0] == "anthropic"
    rt = _FakeRT("sonnet-x")
    rm._apply_last_used_model("anthropic", rt, ["sonnet-x", "opus-x"])
    assert rt.model == "opus-x"


def test_session_fallback_is_the_last_used_model(cfg_path, monkeypatch):
    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {
        ("minimax-cn-coding-plan", "MiniMax-M3"),
    })
    last_used.record_last_used_model(
        "minimax-cn-coding-plan", "minimax-cn-coding-plan:MiniMax-M3",
    )
    assert rm._resolve_session_provider_model({"id": None}) == (
        "minimax-cn-coding-plan",
        "MiniMax-M3",
    )


# --- every switch and every send records the last used model --------------

from tests.unit.dag.test_rewind_modes import store


@pytest.mark.parametrize("transport", ["rest", "ws"])
def test_session_switch_persists_picker_and_last_used(transport, store, monkeypatch, cfg_path):
    import asyncio
    from fastapi import FastAPI
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.execution import runtime as rest
    from openprogram.interfaces.server.ws_actions import runtime as ws

    store.create_session("switch-session", "main")
    branch_id = store.get_session("switch-session")["active_branch_id"]
    conv = {"id": "switch-session", "provider_name": "openai", "runtime": _FakeRT("old")}
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr(server, "_sessions", {"switch-session": conv})
    monkeypatch.setattr(server, "_broadcast", lambda *_: None)
    monkeypatch.setattr(server, "_get_provider_info", lambda *_: {})
    monkeypatch.setattr(server, "_get_thinking_config_for_model", lambda *_: {})
    monkeypatch.setattr(server, "refresh_context_stats", lambda *_: None)
    monkeypatch.setattr(rest, "emit_ws_frame", lambda *_: None)
    body = {"session_id": "switch-session", "model": "picked", "provider": "openai",
            "expected_branch_id": branch_id}
    if transport == "rest":
        app = FastAPI(); rest.register(app)
        endpoint = next(r.endpoint for r in app.routes if r.path == "/api/model" and "POST" in r.methods)
        assert asyncio.run(endpoint(body)).status_code == 200
    else:
        class Socket:
            frames = []
            async def send_text(self, frame):
                self.frames.append(json.loads(frame))
        socket = Socket()
        asyncio.run(ws.handle_switch_model(socket, body))
        assert socket.frames[-1]["type"] == "model_switched"
    row = store.get_session("switch-session")
    assert (row["provider_override"], row["model_override"]) == ("openai", "picked")
    assert (conv["provider_override"], conv["model_override"]) == ("openai", "picked")
    assert last_used.read_last_used_model() == ("openai", "picked")


def test_restored_session_function_run_uses_its_agent_model(monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes import chat as routes_chat

    class Stored:
        def get_branch(self, session_id):
            return []

        def get_session(self, session_id):
            return {"agent_id": "agent-custom", "title": "restored"}

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: Stored())
    monkeypatch.setattr(server, "_sessions", {})
    # A conversation is read from the store the first time it is asked for.
    conv = server._get_or_create_session("restored-s1")

    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {
        ("agent-provider", "agent-model"),
        ("global-provider", "global-model"),
    })
    monkeypatch.setattr(rm, "_chat_provider", "global-provider")
    monkeypatch.setattr(rm, "_chat_model", "global-model")
    monkeypatch.setattr(
        "openprogram.runtime.agent_registry.manager.get",
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
        "openprogram.interfaces.server.ws_actions.session.broadcast_sessions_list",
        lambda: None,
    )

    class Tool:
        name = "agentic_probe"
        _is_agent_method = True

    monkeypatch.setattr(
        "openprogram.programs.agent_tools",
        lambda names=None: [Tool()],
    )
    monkeypatch.setattr(
        "openprogram.programs._host.runtime.get",
        lambda name, *a, **k: Tool() if name == Tool.name else None,
    )

    class DB:
        def message_exists(self, session_id, msg_id):
            return True

        def get_session(self, session_id):
            return {"agent_id": "agent-custom", "created_at": 1}

        def update_session(self, *args, **kwargs):
            pass

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: DB())

    captured = {}
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.execution.model import ExecutionStatus
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
        "openprogram.runtime.agent.call_state.create_pending_call_node",
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

    result = routes_chat.run_agent_method_call(
        "agentic_probe", {}, "restored-s1",
    )
    assert "error" not in result
    assert captured["provider"] == "agent-provider"
    assert captured["model"] == "agent-model"
