from __future__ import annotations

import asyncio
import json
import threading
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


@pytest.mark.parametrize("unsupported", [None, {"judge_model": "reviewer:model"}, {"context_mode": "isolated"}])
def test_goal_form_preserves_budgets_and_rejects_workflow_only_options(tmp_path, monkeypatch, unsupported):
    from openprogram.agent.session_db import SessionDB
    from openprogram.webui import server
    from openprogram.webui.routes.chat import run_agentic_function_call
    import openprogram.programs.workflow.goal as goals
    from openprogram.programs.workflow.goal import chat
    db = SessionDB(tmp_path / "sessions")
    db.create_session("form-goal", "main")
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(server, "_get_or_create_session", lambda sid: {"id": sid})
    monkeypatch.setattr(server._runtime_management, "_enabled_model_keys", lambda: ["test:model"])
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a: None)
    started = []
    monkeypatch.setattr(chat, "start_from_controls", lambda sid:
                        started.append(goals.load_goal(sid)) or {"execution_id": "chat-form"})
    try:
        result = run_agentic_function_call("goal", {"prompt": "test", "max_rounds": 2,
            "max_tokens": 1000, "max_elapsed_s": 30, "max_cost_usd": 0.1,
            **(unsupported or {})}, session_id="form-goal")
        if unsupported:
            assert result["status_code"] == 409
            assert not started and goals.load_goal("form-goal") is None
        else:
            assert result["execution_id"] == "chat-form"
            assert started[0]["budget"] == {"max_turns": 2, "max_tokens": 1000,
                                            "max_elapsed_s": 30.0, "max_cost_usd": 0.1}
    finally:
        db.close()


def test_web_goal_set_saves_chat_state_without_forced_workflow(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui import server as server
    from openprogram.webui.ws_actions.chat import handle_chat
    from openprogram.webui.ws_actions import webtab
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
    async def fake_activate(self, admission, **kwargs):
        old_chat_loop_started.set()
        return None
    monkeypatch.setattr("openprogram.agent.production_driver.CanonicalAgentAdapter.activate", fake_activate)
    monkeypatch.setattr(
        server,
        "_execute_in_context",
        lambda *_args, **_kwargs: old_chat_loop_started.set(),
    )

    ws = _FakeWS()
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(ws, "window-1", 1)],
    )
    asyncio.run(handle_chat(ws, {
        "text": "/goal tests pass",
        "session_id": "web-goal",
        "surface": {
            "version": 1,
            "window_id": "window-1",
            "tab_id": "tab-submitted",
        },
    }))

    assert calls == []
    assert goal_pkg.load_goal("web-goal")["execution_mode"] == "chat"
    assert goal_pkg.load_goal("web-goal")["text"] == "tests pass"
    assert old_chat_loop_started.wait(2)
    assert not any(frame.get("data", {}).get("function_run") for frame in ws.sent)

    calls.clear()
    asyncio.run(handle_chat(ws, {
        "text": "/goal must not dispatch",
        "session_id": "web-goal",
        "surface": {
            "version": 1,
            "window_id": "window-other",
            "tab_id": "tab-forged",
        },
    }))
    assert calls == []
    errors = [frame for frame in ws.sent if frame.get("type") == "chat_response"]
    assert errors[-1]["data"]["code"] == "page_context_stale"


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


def test_goal_http_answer_persists_and_returns_resume_invocation(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui.routes.execution import goal as goal_routes

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("web-goal", "main")
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    goal_pkg.save_goal("web-goal", {
        "text": "write survey",
        "status": "waiting_user",
        "version": 0,
        "last_question": "Which scope?",
        "last_question_id": "stale-after-restart",
    })
    app = FastAPI()
    goal_routes.register(app)
    monkeypatch.setattr("openprogram.programs.workflow.goal.chat.start_from_controls",
                        lambda sid: {"execution_id": "chat-execution"})
    client = TestClient(app)

    shown = client.get("/api/sessions/web-goal/goal")
    assert shown.status_code == 200
    assert shown.json()["goal"]["last_question"] == "Which scope?"

    answered = client.post(
        "/api/sessions/web-goal/goal",
        json={"action": "answer", "answer": "Knowledge editing"},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["goal"]["status"] == "active"
    assert body["goal"]["pending_answers"][0]["answer"] == "Knowledge editing"
    assert body["execution"]["execution_id"] == "chat-execution"
    assert "invoke" not in body


def test_goal_http_answer_resumes_newly_unblocked_work_with_other_questions_pending(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui.routes.execution import goal as goal_routes

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("web-goal", "main")
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    goal_pkg.save_goal("web-goal", {
        "text": "write survey",
        "status": "waiting_user",
        "version": 0,
        "questions": [
            {"id": "scope", "prompt": "Which scope?", "status": "pending"},
            {"id": "venue", "prompt": "Which venue?", "status": "pending"},
        ],
    })
    app = FastAPI()
    goal_routes.register(app)
    monkeypatch.setattr("openprogram.programs.workflow.goal.chat.start_from_controls",
                        lambda sid: {"execution_id": "chat-execution"})
    client = TestClient(app)

    answered = client.post(
        "/api/sessions/web-goal/goal",
        json={"action": "answer", "question_id": "scope", "answer": "Editing"},
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["goal"]["status"] == "active"
    assert body["execution"]["execution_id"] == "chat-execution"
    assert "invoke" not in body
    assert body["goal"]["questions"][1]["status"] == "pending"


def test_worker_restart_turns_active_goal_into_recoverable_pause(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg
    from openprogram.webui._exec_dag import reconcile_interrupted_runs

    db = SessionDB(tmp_path / "sessions-git")
    db.create_session("restart-goal", "main")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_db", lambda: db)
    monkeypatch.setattr(goal_pkg, "_emit_goal_update", lambda *_a, **_k: None)
    goal_pkg.save_goal("restart-goal", {
        "text": "write survey",
        "status": "running",
        "phase": "working",
        "version": 0,
        "checkpoint": {"phase": "working", "round": 3},
        "turns_used": 3,
    })

    assert reconcile_interrupted_runs() == 1
    recovered = goal_pkg.load_goal("restart-goal")
    assert recovered["status"] == "paused_recoverable"
    assert recovered["pause_reason"] == "worker_restart"
    assert recovered["checkpoint"] == {"phase": "working", "round": 3}
    assert recovered["turns_used"] == 3
    assert recovered["version"] == 2
    assert recovered["active_started_at"] is None


def test_goal_cas_rejects_stale_state_from_another_store_instance(
    tmp_path, monkeypatch,
) -> None:
    from openprogram.agent.session_db import SessionDB
    import openprogram.programs.workflow.goal as goal_pkg

    root = tmp_path / "sessions-git"
    first = SessionDB(root)
    first.create_session("shared-goal", "main")
    second = SessionDB(root)
    monkeypatch.setattr(goal_pkg, "_db", lambda: first)
    goal_pkg.save_goal("shared-goal", {
        "goal_id": "goal-1",
        "run_id": "run-1",
        "text": "x",
        "status": "active",
        "version": 0,
    })
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
