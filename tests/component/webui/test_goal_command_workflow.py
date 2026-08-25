from __future__ import annotations

import asyncio
import json
import threading


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def test_web_goal_set_dispatches_the_registered_goal_workflow(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.webui.routes.chat as chat_routes

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("web-goal", "main")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(
        "openprogram.store.session.session_store.default_store", lambda: db,
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

    monkeypatch.setattr(chat_routes, "run_agentic_function_call", fake_run)
    monkeypatch.setattr(
        server,
        "_execute_in_context",
        lambda *_args, **_kwargs: old_chat_loop_started.set(),
    )

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {
        "text": "/goal tests pass",
        "session_id": "web-goal",
    }))

    assert calls == [(
        "goal",
        {
            "prompt": "tests pass",
            "condition": "tests pass",
            "context_mode": "session",
        },
        "web-goal",
        {},
    )]
    assert goal_pkg.load_goal("web-goal") is None
    assert old_chat_loop_started.wait(0.2) is False
    ack = [frame for frame in ws.sent if frame.get("type") == "chat_ack"]
    assert ack and ack[-1]["data"]["function_run"] is True


def test_user_forced_goal_fills_missing_context_mode_as_session() -> None:
    from openprogram.webui.routes.chat import apply_user_goal_context_mode

    filled = apply_user_goal_context_mode("goal", {"prompt": "do it"})
    assert filled["context_mode"] == "session"
    kept = apply_user_goal_context_mode(
        "goal", {"prompt": "do it", "context_mode": "isolated"},
    )
    assert kept["context_mode"] == "isolated"
    other = apply_user_goal_context_mode("research", {"prompt": "do it"})
    assert "context_mode" not in other
