from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("source", ["web", "tui", "acp"])
def test_chat_runtime_keeps_goal_tool_membership_on_approval_resume(monkeypatch, source):
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.execution.admission_input.runtime_contract import validate_runtime_contract
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.models.providers.types import AgentTool
    from openprogram.models.providers.types import Model

    model = Model(id="fake", name="fake", api="openai-completions", provider="openai",
                  base_url="https://example.invalid/v1")
    tools = [AgentTool(name=name, label=name, description=name,
                       parameters={"type": "object"}, execute=lambda *args: None)
             for name in ("goal", "task_start", "update_goal")]
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *a, **k: tools)
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *a: tool)
    monkeypatch.setattr("openprogram.runtime.context.prompt_assembly.components.build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr("openprogram.programs.tools.planning.goal.chat.instructions", lambda *a: "")
    request = TurnRequest("approval-goal", "calculate", "main", source)
    request._execution_revision_id = "test-revision"
    initial = loop_runner.resolve_agent_runtime(request)[-1]
    assert "goal" not in {tool["name"] for tool in initial["tools"]}
    resumed = loop_runner.resolve_agent_runtime(request, saved_runtime_contract=initial)[-1]
    assert "goal" not in {tool["name"] for tool in resumed["tools"]}
    validate_runtime_contract(initial, resumed)


def test_chat_runtime_keeps_saved_tools_when_live_catalog_grows(monkeypatch):
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.execution.admission_input.runtime_contract import validate_runtime_contract
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.models.providers.types import AgentTool
    from openprogram.models.providers.types import Model

    model = Model(id="fake", name="fake", api="openai-completions", provider="openai",
                  base_url="https://example.invalid/v1")
    tools = [AgentTool(name=name, label=name, description=name,
                       parameters={"type": "object"}, execute=lambda *args: None)
             for name in ("task_start", "update_goal")]
    extra = AgentTool(name="weekly_report", label="weekly_report", description="weekly_report",
                      parameters={"type": "object"}, execute=lambda *args: None)
    live = {"extra": False}

    def resolve(*_args, **_kwargs):
        return [*tools, extra] if live["extra"] else list(tools)

    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", resolve)
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *a: tool)
    monkeypatch.setattr("openprogram.runtime.context.prompt_assembly.components.build_system_prompt", lambda *a, **k: "system")
    monkeypatch.setattr("openprogram.programs.tools.planning.goal.chat.instructions", lambda *a: "")
    request = TurnRequest("approval-goal", "calculate", "main", "web")
    request._execution_revision_id = "test-revision"
    saved = loop_runner.resolve_agent_runtime(request)[-1]
    live["extra"] = True
    resumed = loop_runner.resolve_agent_runtime(request, saved_runtime_contract=saved)[-1]
    assert {tool["name"] for tool in resumed["tools"]} == {tool["name"] for tool in saved["tools"]}
    assert "weekly_report" not in {tool["name"] for tool in resumed["tools"]}
    validate_runtime_contract(saved, resumed)


def test_live_tool_visibility_preserves_execution_contract(monkeypatch):
    from openprogram.runtime.permissions.approvals.lifecycle import wrap_live_permission
    from openprogram.runtime.permissions.authority import owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.permissions.modes import PermissionRules
    from openprogram.models.providers.types import AgentTool
    rules = PermissionRules(deny=[])
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda _: rules)
    request = TurnRequest("deny-bash", "hello", "main", "web",
                          **owner_authority("owner/install/0123456789abcdef"))
    tool = wrap_live_permission(AgentTool(name="bash", label="bash", description="shell",
                              parameters={}, execute=lambda *args: None), request, lambda _: None)
    assert tool._permission_visible()
    rules.deny = ["bash"]
    assert not tool._permission_visible()
    rules.deny = ["bash(rm *)"]
    assert tool._permission_visible()
    assert tool.name == "bash"


def test_a_goal_started_from_the_controls_runs_in_the_conversations_mode(monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.conversation.session_settings import session_config
    import openprogram.programs.tools.planning.goal as goals

    seen = {}

    class Stop(Exception):
        pass

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def admit(self, request, **_kwargs):
            seen["request"] = request
            raise Stop

    monkeypatch.setattr(goals, "load_goal", lambda _sid: {"status": "active", "text": "go", "id": "g"})
    monkeypatch.setattr(goals, "_db", lambda: SimpleNamespace(get_session=lambda _sid: {"agent_id": "main"}))
    monkeypatch.setattr(chat, "identity", lambda _goal: {"id": "g"})
    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(session_config, "conversation_permission",
                        lambda _sid: {"permission_mode": "plan", "permission_rules": None})
    with pytest.raises(Stop):
        chat.start_from_controls("s")
    assert seen["request"].permission_mode == "plan"


def test_a_goal_turn_started_from_the_controls_holds_the_run_slot(monkeypatch):
    """The Goal's turn waits for its branch's run slot, runs while it holds
    it and gives it back when it ends: clients show the conversation
    running for the whole turn and no other turn starts beside it."""
    import threading
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.conversation import production_driver
    from openprogram.runtime.conversation.session_settings import session_config
    from openprogram.runtime.conversation.run_control import run_slot
    import openprogram.programs.tools.planning.goal as goals

    events = []
    ended = threading.Event()

    class Port:
        tries = 0

        def adopt(self, session_id, owner, *, execution_id, status_version, branch_id, runner):
            self.tries += 1
            events.append(("adopt", owner, execution_id, status_version))
            return self.tries > 1

        def finish(self, session_id, owner, *, execution_id):
            events.append(("finish", owner, execution_id))
            ended.set()

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def admit(self, request, **_kwargs):
            return SimpleNamespace(execution_id="e1", status_version=3)

        async def activate(self, admission):
            events.append(("run", admission.execution_id))

        def fail_admission(self, admission, *, reason_code):
            events.append(("failed", reason_code))

    monkeypatch.setattr(run_slot, "_port", Port())
    # Nothing here says the slot was given back; the wait's resync asks again.
    monkeypatch.setattr(run_slot, "HOLD_RESYNC_SECONDS", 0.05)
    monkeypatch.setattr(goals, "load_goal", lambda _sid: {"status": "active", "text": "go", "id": "g"})
    monkeypatch.setattr(goals, "_db", lambda: SimpleNamespace(get_session=lambda _sid: {"agent_id": "main"}))
    monkeypatch.setattr(chat, "identity", lambda _goal: {"id": "g"})
    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(session_config, "conversation_permission",
                        lambda _sid: {"permission_mode": "ask", "permission_rules": None})

    started = chat.start_from_controls("s")

    assert ended.wait(10)
    owner = started["msg_id"]
    assert events == [("adopt", owner, "e1", 3), ("adopt", owner, "e1", 3),
                      ("run", "e1"), ("finish", owner, "e1")]


@pytest.fixture
def session(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import SessionDB
    import openprogram.programs.tools.planning.goal as goals
    db = SessionDB(tmp_path / "sessions")
    db.create_session("chat-goal", "main")
    monkeypatch.setattr(goals, "_db", lambda: db)
    # The todo board is per branch and reads the selected branch from the
    # same store the Goal records its branch in.
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a, **k: None)
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"total_tokens": 0, "cost_usd": 0, "cost_known": True})
    yield goals, db
    db.close()


def test_slash_goal_starts_normal_chat(session):
    goals, _ = session
    result = goals.handle_goal_command("chat-goal", "implement and verify the feature")
    assert "invoke" not in result
    assert result["send_text"]
    goal = goals.load_goal("chat-goal")
    assert goal["execution_mode"] == "chat"
    assert goal["text"] == "implement and verify the feature"
    assert "max_turns" not in goal["budget"]
    # The message that carries the objective is the one marked as setting it.
    assert (result["send_text"], result["starts_goal"]) == ("implement and verify the feature", True)


def test_goal_status_shows_the_status_and_sets_nothing(session):
    goals, _ = session
    empty = goals.handle_goal_command("chat-goal", "status")
    assert empty == goals.handle_goal_command("chat-goal", "")
    assert empty["send_text"] is None and goals.load_goal("chat-goal") is None
    goals.handle_goal_command("chat-goal", "an objective")
    shown = goals.handle_goal_command("chat-goal", " Status ")
    assert shown == goals.handle_goal_command("chat-goal", "")
    assert shown["send_text"] is None and not shown.get("starts_goal")
    assert goals.load_goal("chat-goal")["text"] == "an objective"
    assert "/goal status" in goals.handle_goal_command("chat-goal", "help")["text"]


def test_a_goal_that_cannot_be_created_answers_in_text_and_marks_nothing(session):
    goals, _ = session
    goals.handle_goal_command("chat-goal", "first objective")
    result = goals.handle_goal_command("chat-goal", "second objective")
    assert result["text"] and not result.get("send_text") and not result.get("starts_goal")


def test_resume_sends_its_continuation_unmarked(session, monkeypatch):
    goals, _ = session
    goals.handle_goal_command("chat-goal", "an objective")
    goals.apply_goal_action("chat-goal", "pause")
    from openprogram.programs.tools.planning.goal import chat
    monkeypatch.setattr(chat, "resume", lambda *_a, **_k: {})
    result = goals.handle_goal_command("chat-goal", "resume")
    assert result["send_text"] and "starts_goal" not in result


def test_the_message_that_sets_a_goal_is_written_with_its_mark(tmp_path, monkeypatch):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.conversation import turn_runtime

    db = SessionDB(tmp_path / "sessions")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr(turn_runtime, "_run_loop_blocking", lambda **_kw: ("done", {}, []))
    turn_runtime._process_turn_once(TurnRequest(
        session_id="marked", user_text="ship it", agent_id="main", source="cli", starts_goal=True))
    row = next(m for m in SessionDB(tmp_path / "sessions").get_branch("marked") if m["role"] == "user")
    assert (row["content"], row["starts_goal"]) == ("ship it", True)


def test_a_goal_mark_left_at_its_default_stays_out_of_the_stored_input():
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver.shared import (
        CanonicalAgentAdapter, normalize_agent_turn_payload)

    def stored(**fields):
        payload = CanonicalAgentAdapter.payload_for(TurnRequest("s", "hi", "main", "web", **fields))
        return normalize_agent_turn_payload(payload)["request"]

    assert "starts_goal" not in stored()
    assert stored(starts_goal=True)["starts_goal"] is True


def test_chat_goal_completion_rejects_unfinished_todos(session, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    goals, _ = session
    goal = chat.create("chat-goal", "implement and verify")
    monkeypatch.setattr(shared, "load", lambda sid, branch_id=None: [{
        "id": "1", "subject": "verify", "status": "pending",
        "goal_id": goal["goal_id"], "goal_revision": goal["revision"],
    }])
    with pytest.raises(ValueError, match="unfinished task steps"):
        chat.update("chat-goal", "complete", expected=chat.identity(goal))
    assert goals.load_goal("chat-goal")["status"] == "active"


def test_a_dropped_step_does_not_hold_the_goal_back(session, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    goals, _ = session
    goal = chat.create("chat-goal", "implement and verify")
    owned = {"goal_id": goal["goal_id"], "goal_revision": goal["revision"]}
    monkeypatch.setattr(shared, "load", lambda sid, branch_id=None: [
        {"id": "1", "subject": "implement", "status": "completed", **owned},
        {"id": "2", "subject": "old idea", "status": "dropped", **owned},
    ])
    assert chat.unfinished_todos("chat-goal", goal) == []
    assert chat.project_todos("chat-goal", goal) == [{"text": "implement", "done": True}]
    chat.update("chat-goal", "complete", expected=chat.identity(goal))
    assert goals.load_goal("chat-goal")["phase"] == "verifying"


def test_task_tools_publish_current_goal_progress(session, monkeypatch, tmp_path):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
    from openprogram.programs.tools.planning.todo.task_step.task_step import _task_step_impl
    from openprogram.state.store import _current_turn_id
    goals, _ = session
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")
    monkeypatch.setattr(shared, "current_session_id", lambda: "chat-goal")
    goal = chat.create("chat-goal", "verify")
    monkeypatch.setattr(chat, "current_identity", lambda: chat.identity(goal))
    token = _current_turn_id.set("m1_reply")
    try:
        _task_start_impl("Check the output", steps=["verify", "report", "old idea"])
        saved = goals.load_goal("chat-goal")
        assert saved["checklist"] == [{"text": "verify", "done": False}, {"text": "report", "done": False},
                                      {"text": "old idea", "done": False}]
        assert saved["progress"] == {"task": "T1", "title": "Check the output", "step": "verify", "on": 1,
                                     "done": 0, "total": 3, "anchor": "m1_reply"}
        _task_step_impl(next=True, drop=[3])
        saved = goals.load_goal("chat-goal")
        # A dropped step leaves the checklist and the total.
        assert saved["checklist"] == [{"text": "verify", "done": True}, {"text": "report", "done": False}]
        assert saved["progress"] == {"task": "T1", "title": "Check the output", "step": "report", "on": 2,
                                     "done": 1, "total": 2, "anchor": "m1_reply"}
        _task_step_impl(outcome="output checked")
    finally:
        _current_turn_id.reset(token)
    saved = goals.load_goal("chat-goal")
    assert saved["checklist"] == [{"text": "verify", "done": True}, {"text": "report", "done": True}]
    assert saved["progress"]["step"] is None and saved["progress"]["on"] is None
    assert (saved["progress"]["done"], saved["progress"]["total"]) == (2, 2)
    assert saved["status"] == "active"
    assert chat.tasks("chat-goal", saved)[0]["outcome"] == "output checked"
    goal = saved
    goal["revision"] += 1
    goals.save_goal("chat-goal", goal)
    # A new revision starts without the earlier revision's task.
    chat.refresh_todos("chat-goal")
    saved = goals.load_goal("chat-goal")
    assert saved["checklist"] == [] and "progress" not in saved


def test_todo_saved_success_survives_goal_projection_failure(session, monkeypatch, tmp_path):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.todo import shared
    from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
    from openprogram.programs.tools.planning.todo.task_step.task_step import _task_step_impl
    goals, _ = session
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")
    monkeypatch.setattr(shared, "current_session_id", lambda: "chat-goal")
    goal = chat.create("chat-goal", "verify")
    monkeypatch.setattr(chat, "current_identity", lambda: chat.identity(goal))
    refresh = chat.refresh_todos
    def unavailable(_sid):
        raise goals.GoalConflictError("busy")
    monkeypatch.setattr(chat, "refresh_todos", unavailable)
    assert "started" in _task_start_impl("Check", steps=["verify"]).content[0].text
    assert "finished" in _task_step_impl(outcome="checked").content[0].text
    assert shared.load("chat-goal")[0]["status"] == "completed"
    refresh("chat-goal")
    assert goals.load_goal("chat-goal")["checklist"] == [{"text": "verify", "done": True}]


def test_todo_refresh_preserves_legacy_goal_checklist(session):
    from openprogram.programs.tools.planning.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "verify")
    goal.update(execution_mode="workflow", checklist=[{"text": "legacy", "done": False}])
    goals.save_goal("chat-goal", goal)
    chat.refresh_todos("chat-goal")
    assert goals.load_goal("chat-goal")["checklist"] == goal["checklist"]


def test_legacy_provider_receipt_refreshes_goal_usage_without_duplicate_charge(session, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.models.usage.event import UsageEvent
    from openprogram.models.usage.recorder import run_usage_hooks
    goals, _ = session
    total = {"total_tokens": 0, "cost_usd": 0, "cost_known": True, "unknown_cost_events": 0}
    monkeypatch.setattr(goals, "goal_usage", lambda *a: dict(total))
    goal = chat.create("chat-goal", "verify")
    goal.pop("usage_mode")
    goals.reset_goal_usage_cursor("chat-goal", goal)
    goals.save_goal("chat-goal", goal)
    total.update(total_tokens=100, cost_usd=0.2)
    event = UsageEvent(session_id="chat-goal", total_tokens=100)
    run_usage_hooks(event)
    run_usage_hooks(event)
    saved = goals.load_goal("chat-goal")
    assert saved["usage"]["total_tokens"] == 100
    assert saved["usage"]["cost_usd"] == pytest.approx(0.2)
    saved["status"] = "paused"
    goals.save_goal("chat-goal", saved)
    total.update(total_tokens=200, cost_usd=0.4)
    run_usage_hooks(event)
    assert goals.load_goal("chat-goal")["usage"]["total_tokens"] == 100


def test_metering_read_failure_preserves_cursor_and_does_not_double_bill(session, monkeypatch):
    goals, _ = session
    goal = {"usage": {"total_tokens": 10, "cost_usd": 0.1, "cost_known": True},
            "usage_cursor": {"total_tokens": 100, "cost_usd": 1, "unknown_cost_events": 0}}
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"available": False})
    goals.accumulate_goal_usage("chat-goal", goal)
    assert goal["usage_cursor"]["total_tokens"] == 100
    assert goal["usage"]["cost_known"] is False
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {
        "total_tokens": 110, "cost_usd": 1.2, "unknown_cost_events": 0})
    goals.accumulate_goal_usage("chat-goal", goal)
    assert goal["usage"]["total_tokens"] == 20
    assert goal["usage"]["cost_usd"] == pytest.approx(0.3)
    assert goal["usage"]["cost_known"] is True


def test_legacy_resume_settles_failed_boundary_before_excluding_paused_usage(session, monkeypatch):
    from openprogram.programs.tools.planning.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "verify")
    goal.pop("usage_mode")
    goal.update(status="paused", usage={"total_tokens": 10, "cost_usd": 0.1},
                usage_cursor={"total_tokens": 100, "cost_usd": 1}, usage_pending_until=50.0)
    goals.save_goal("chat-goal", goal)
    def aggregate(sid, since, until=None):
        return {"total_tokens": 110 if until == 50.0 else 900,
                "cost_usd": 1.2 if until == 50.0 else 9, "unknown_cost_events": 0}
    monkeypatch.setattr(goals, "goal_usage", aggregate)
    resumed = chat.resume("chat-goal")
    assert resumed["usage"]["total_tokens"] == 20
    assert resumed["usage"]["cost_usd"] == pytest.approx(0.3)
    assert resumed["usage_cursor"]["total_tokens"] == 900
    assert "usage_pending_until" not in resumed


def test_unsettled_restart_usage_is_not_presented_as_free(session):
    goals, _ = session
    goal = goals.normalize_goal({"execution_mode": "chat", "pause_reason": "worker_restart",
                                "execution_id": "old", "usage": {"total_tokens": 0, "cost_usd": 0}})
    assert goal["usage"]["accounting_pending"]
    assert goal["usage"]["cost_known"] is False


def test_old_revision_cannot_complete_new_goal(session):
    from openprogram.programs.tools.planning.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "first objective")
    expected = chat.identity(goal)
    goal["revision"] += 1
    goal["text"] = "updated objective"
    goals.save_goal("chat-goal", goal)
    with pytest.raises(goals.GoalConflictError):
        chat.update("chat-goal", "complete", expected=expected)


def test_budget_exhausted_resume_does_not_reactivate(session):
    from openprogram.programs.tools.planning.goal import chat
    goals, _ = session
    goal = chat.create("chat-goal", "task", token_budget=10)
    goal.update(status="budget_exhausted", legacy_usage={"total_tokens": 10})
    goals.save_goal("chat-goal", goal)
    result = goals.handle_goal_command("chat-goal", "resume")
    assert not result["send_text"]
    assert "budget" in result["text"]
    assert goals.load_goal("chat-goal")["status"] == "budget_exhausted"
