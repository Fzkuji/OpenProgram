"""Owner-facing Goal controls expose shared eligibility and exact blockers."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_chat_goal.runtime


@pytest.fixture
def client(runtime):
    from openprogram.interfaces.server.routes.execution.goal import register
    app = FastAPI()
    register(app)
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize("state", ["untracked", "missing_execution", "budget_exhausted", "cancelled"])
def test_goal_http_controls_explain_resume_without_guessing_execution_state(runtime, client, monkeypatch, state):
    goals, _, _ = runtime
    goal = goals.apply_goal_action("goal-chat", "pause")
    if state == "missing_execution":
        goal["execution_id"] = "missing-record"
        goals.save_goal("goal-chat", goal)
    elif state == "budget_exhausted":
        monkeypatch.setattr(goals, "budget_exhausted", lambda goal, **_: "tokens")
    elif state == "cancelled":
        goals.apply_goal_action("goal-chat", "cancel")
    response = client.get("/api/sessions/goal-chat/goal")
    assert response.status_code == 200
    controls = response.json()["controls"]
    assert controls["can_resume"] is (state == "untracked")
    assert controls["can_end"] is (state != "cancelled")
    assert controls["can_verify"] is False  # No completed work to inspect yet.
    expected = {"untracked": None, "missing_execution": "execution_unavailable",
                "budget_exhausted": "budget_exhausted", "cancelled": "goal_terminal"}
    assert controls["reasons"]["resume"] == expected[state]


def test_resume_rejects_a_different_active_chat_before_changing_goal(runtime, client):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, _, store = runtime
    goals.apply_goal_action("goal-chat", "pause")
    CanonicalAgentAdapter(store=store).admit(TurnRequest("goal-chat", "other work", "main", "web"),
        trusted_actor={}, user_message_id="other-user", config_snapshot_ref="test")
    before = goals.load_goal("goal-chat")
    response = client.post("/api/sessions/goal-chat/goal", json={"action": "resume"})
    assert response.status_code == 409
    assert goals.load_goal("goal-chat")["status"] == before["status"] == "paused"
    assert goals.load_goal("goal-chat")["run_id"] == before["run_id"]


def test_unknown_effect_is_inspectable_without_disabling_safe_new_turn(runtime, client):
    from tests.component.agent.test_chat_restart_recovery import interrupted_chat
    from openprogram.runtime.execution.effects import EffectStore
    goals, _, store = runtime
    admission, effect = interrupted_chat(store)
    goal = goals.load_goal("goal-chat")
    goal.update(status="paused_recoverable", phase="paused", pause_reason="worker_restart")
    goals.save_goal("goal-chat", goal)
    payload = client.get("/api/sessions/goal-chat/goal").json()
    assert payload["controls"]["can_resume"] is True
    assert payload["controls"]["can_end"] is True
    assert payload["controls"]["can_verify"] is False
    assert payload["controls"]["operations"] == [{
        "effect_id": effect.effect_id, "execution_id": admission.execution_id,
        "status": "dispatched", "tool_name": "bash", "created_at": effect.created_at,
        "dispatched_at": EffectStore(store).get(effect.effect_id).dispatched_at,
    }]
    assert payload["controls"]["operations"][0]["dispatched_at"] is not None


@pytest.mark.parametrize("process_status", ["running", "unknown", "exited"])
@pytest.mark.parametrize("related", [True, False])
def test_verification_checks_only_managed_work_owned_by_this_goal(runtime, client, process_status, related):
    import asyncio
    from types import SimpleNamespace
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.host.processes import ProcessStore
    goals, _, store = runtime
    def work(*, request, cancel_event):
        goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    adapter = CanonicalAgentAdapter(store=store, turn_runner=work)
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="work", config_snapshot_ref="test")
    asyncio.run(adapter.activate(admission))
    processes = ProcessStore()
    process = processes.create(session_id="goal-chat", execution_id=admission.execution_id if related else None,
                               tool_call_id="background", command="work", cwd=None, backend_id="local")
    processes.update(process["id"], status=process_status)
    result = client.get("/api/sessions/goal-chat/goal").json()
    blocked = related and process_status != "exited"
    assert result["controls"]["can_verify"] is not blocked
    if blocked:
        assert result["controls"]["reasons"]["verify"] == "managed_work_unfinished"
        assert result["controls"]["processes"][0]["id"] == process["id"]
        assert client.post("/api/sessions/goal-chat/goal", json={"action": "verify"}).status_code == 409


@pytest.mark.parametrize("surface", ["http", "command"])
def test_explicit_verify_starts_one_independent_turn_not_direct_completion(runtime, client, monkeypatch, surface):
    import asyncio
    import threading
    from types import SimpleNamespace
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    def work(*, request, cancel_event):
        goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    adapter = CanonicalAgentAdapter(store=store, turn_runner=work)
    work_admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                                  trusted_actor={}, user_message_id="work", config_snapshot_ref="test")
    asyncio.run(adapter.activate(work_admission))
    before = client.get("/api/sessions/goal-chat/goal").json()
    assert before["controls"]["can_verify"] is True
    entered, release, ended = threading.Event(), threading.Event(), threading.Event()
    requests = []
    def verify(*, request, cancel_event):
        requests.append(request)
        entered.set()
        assert release.wait(30)
        goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    real_activate = CanonicalAgentAdapter.activate
    async def activate(self, admission, **kwargs):
        try:
            return await real_activate(self, admission, **kwargs)
        finally:
            ended.set()
    monkeypatch.setattr(CanonicalAgentAdapter, "activate", activate)
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=verify, **kw))
    expected = {key: before["goal"][key] for key in ("goal_id", "revision", "run_id", "version")}
    try:
        if surface == "http":
            response = client.post("/api/sessions/goal-chat/goal", json={"action": "verify", "expected": expected})
            assert response.status_code == 200, response.text
        else:
            response = goals.handle_goal_command("goal-chat", "verify")
            assert response.get("send_text") is None, response
            assert "Verifying" in response["text"], response
        assert entered.wait(30)
        current = client.get("/api/sessions/goal-chat/goal").json()
        assert current["goal"]["status"] == "active" and current["goal"]["phase"] == "verifying"
        assert current["goal"]["execution_id"] != work_admission.execution_id
        assert current["controls"]["can_verify"] is False
        assert requests[0].goal_verification
        assert client.post("/api/sessions/goal-chat/goal", json={"action": "verify", "expected": expected}).status_code == 409
        assert len(requests) == 1
    finally:
        release.set()
        if entered.is_set():
            assert ended.wait(30)
