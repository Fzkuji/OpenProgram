from __future__ import annotations

import asyncio
import json
import threading
import pytest



class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_goal_form_starts_a_chat_goal_from_its_prompt(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.routes.chat import run_agent_method_call
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat

    db = SessionDB(tmp_path / "sessions")
    db.create_session("form-goal", "main")
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(server, "_get_or_create_session", lambda sid: {"id": sid})
    monkeypatch.setattr(
        server.runtime_management, "_enabled_model_keys", lambda: ["test:model"]
    )
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a: None)
    started = []
    monkeypatch.setattr(
        chat,
        "start_from_controls",
        lambda sid: started.append(goals.load_goal(sid))
        or {"execution_id": "chat-form"},
    )
    try:
        result = run_agent_method_call("goal", {"prompt": "test"}, session_id="form-goal")
        assert result["execution_id"] == "chat-form"
        assert started[0]["execution_mode"] == "chat"
        assert started[0]["text"] == "test"
        assert started[0]["budget"] == {"max_tokens": None, "max_elapsed_s": None, "max_cost_usd": None}
        assert run_agent_method_call("goal", {"prompt": "again"}, session_id="form-goal")["status_code"] == 409
    finally:
        db.close()


def test_web_goal_set_saves_chat_state_without_forced_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    from openprogram.runtime.context.session_db import SessionDB
    import openprogram.programs.tools.planning.goal as goal_pkg
    from openprogram.interfaces.server import server as server
    from openprogram.interfaces.server.ws_actions.chat import handle_chat
    from openprogram.interfaces.server.ws_actions import webtab
    import openprogram.interfaces.server.routes.chat as chat_routes

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("web-goal", "main")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store",
        lambda: db,
    )
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    server._sessions.clear()
    server._msg_cache.clear()
    server._get_or_create_session("web-goal", agent_id="main")

    calls: list[tuple] = []
    old_chat_loop_started = threading.Event()

    def fake_run(name, kwargs, session_id=None, **options):
        calls.append((name, kwargs, session_id, options))
        return {
            "session_id": session_id,
            "msg_id": "goal-msg",
            "execution_id": "goal-exec",
        }

    monkeypatch.setattr(chat_routes, "run_agent_method_call", fake_run)

    async def fake_activate(self, admission, **kwargs):
        old_chat_loop_started.set()
        return None

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter.activate",
        fake_activate,
    )
    ws = _FakeWS()
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(ws, "window-1", 1)],
    )
    asyncio.run(
        handle_chat(
            ws,
            {
                "text": "/goal tests pass",
                "session_id": "web-goal",
                "surface": {
                    "version": 1,
                    "window_id": "window-1",
                    "tab_id": "tab-submitted",
                },
            },
        )
    )

    assert calls == []
    assert goal_pkg.load_goal("web-goal")["execution_mode"] == "chat"
    assert goal_pkg.load_goal("web-goal")["text"] == "tests pass"
    assert old_chat_loop_started.wait(30)
    assert not any(frame.get("data", {}).get("function_run") for frame in ws.sent)

    calls.clear()
    asyncio.run(
        handle_chat(
            ws,
            {
                "text": "/goal must not dispatch",
                "session_id": "web-goal",
                "surface": {
                    "version": 1,
                    "window_id": "window-other",
                    "tab_id": "tab-forged",
                },
            },
        )
    )
    assert calls == []
    errors = [frame for frame in ws.sent if frame.get("type") == "chat_response"]
    assert errors[-1]["data"]["code"] == "page_context_stale"


@pytest.mark.parametrize("mode", [None, "chat"])
def test_worker_restart_projection_preserves_chat_goal_intent(
    tmp_path,
    monkeypatch,
    mode,
) -> None:
    from openprogram.runtime.context.session_db import SessionDB
    import openprogram.programs.tools.planning.goal as goal_pkg
    from openprogram.runtime.execution.dag import reconcile_interrupted_runs

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("restart-goal", "main")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    goal_pkg.save_goal(
        "restart-goal",
        {
            "text": "write survey",
            "status": "active" if mode == "chat" else "running",
            "phase": "working",
            "version": 0,
            "checkpoint": {"phase": "working", "round": 3},
            "turns_used": 3,
            "execution_mode": mode,
        },
    )

    assert reconcile_interrupted_runs() == (0 if mode == "chat" else 1)
    recovered = goal_pkg.load_goal("restart-goal")
    if mode == "chat":
        assert recovered["status"] == "active"
        assert recovered["phase"] == "working"
        assert recovered["turns_used"] == 3
        db.close()
        return
    assert recovered["status"] == "paused_recoverable"
    assert recovered["pause_reason"] == "worker_restart"
    assert recovered["checkpoint"] == {"phase": "working", "round": 3}
    assert recovered["turns_used"] == 3
    assert recovered["version"] == 2
    assert recovered["active_started_at"] is None


def test_goal_cas_rejects_stale_state_from_another_store_instance(
    tmp_path,
    monkeypatch,
) -> None:
    from openprogram.runtime.context.session_db import SessionDB
    import openprogram.programs.tools.planning.goal as goal_pkg

    root = tmp_path / "sessions-git"
    first = SessionDB(root)
    first.create_session("shared-goal", "main")
    second = SessionDB(root)
    monkeypatch.setattr(goal_pkg, "_db", lambda: first)
    goal_pkg.save_goal(
        "shared-goal",
        {
            "goal_id": "goal-1",
            "run_id": "run-1",
            "text": "x",
            "status": "active",
            "version": 0,
        },
    )
    state_a = goal_pkg.load_goal("shared-goal")
    monkeypatch.setattr(goal_pkg, "_db", lambda: second)
    state_b = goal_pkg.load_goal("shared-goal")

    monkeypatch.setattr(goal_pkg, "_db", lambda: first)
    state_a["status"] = "paused"
    goal_pkg.save_goal("shared-goal", state_a)

    monkeypatch.setattr(goal_pkg, "_db", lambda: second)
    state_b["status"] = "achieved"
    try:
        goal_pkg.save_goal("shared-goal", state_b)
    except goal_pkg.GoalConflictError:
        pass
    else:
        raise AssertionError("stale cross-instance Goal write was accepted")
    assert goal_pkg.load_goal("shared-goal")["status"] == "paused"
