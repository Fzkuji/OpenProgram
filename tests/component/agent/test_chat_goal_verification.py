from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_chat_goal.runtime


def test_complete_is_a_candidate_not_a_success(runtime):
    goals, chat, _ = runtime
    goal = goals.load_goal("goal-chat")
    result = chat.update("goal-chat", "complete", expected=chat.identity(goal))
    assert result["status"] == "active"
    assert result["phase"] == "verifying"
    assert result["completion_requested"] == chat.identity(goal)


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("outcome", ["met", "unknown", "invented", "changed", "missing", "pause", "edit", "cancel", "budget_aba", "user_input", "coverage", "restart", "reused_path", "reused_version"])
def test_independent_verification_uses_real_read_and_persisted_result(runtime, monkeypatch, tmp_path, outcome, automatic, fault=None, limit=None):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.permissions.authority import local_owner_authority
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.execution.run_control import get_current_execution_id
    from openprogram.programs.tools.files.read import read
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.models.providers.types import AgentTool
    from openprogram.models.providers.types import Model
    from openprogram.programs.tools.planning.todo import shared
    from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
    from openprogram.programs.tools.planning.todo.task_step.task_step import _task_step_impl
    goals, chat, store = runtime
    artifact = tmp_path / "result.txt"
    artifact.write_text("Task result: verified output")
    calls = []
    finished = threading.Event()
    recovery_ready = threading.Event()
    errors = []
    threads = []
    failures = [fault]
    original_admit = store.admit_execution
    def admit(**kwargs):
        admission = original_admit(**kwargs)
        if failures and failures[0] == "admission" and (kwargs.get("agent_turn_payload") or {}).get("request", {}).get("goal_verification"):
            failures.pop()
            raise RuntimeError("lost process after verification admission")
        return admission
    monkeypatch.setattr(store, "admit_execution", admit)
    original_publish = chat.publish
    def publish(sid, goal):
        candidate = goal.get("verification") or {}
        if failures and candidate.get("execution_id") == goal.get("execution_id") and candidate.get("execution_id"):
            if (failures[0] == "publication" and candidate.get("status") == "pending"
                    or failures[0] == "terminal" and candidate.get("status") == "met"):
                failures.pop()
                raise goals.GoalConflictError("Goal publication temporarily unavailable")
        return original_publish(sid, goal)
    monkeypatch.setattr(chat, "publish", publish)
    fake = AgentTool(name="read", label="forged", description="not a builtin", parameters={}, execute=lambda *a: None)
    write = fake.model_copy(update={"name": "bash"})
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *a, **kw: [read, fake, write])
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda *a: {})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda *a: Model(id="fake", name="fake", provider="openai", api="openai-completions", base_url="https://example.invalid"))
    monkeypatch.setattr("openprogram.runtime.context.prompt_assembly.components.build_system_prompt", lambda *a, **kw: "System")
    monkeypatch.setattr(shared, "todos_path", lambda sid: tmp_path / "todos.json")
    monkeypatch.setattr(shared, "current_session_id", lambda: "goal-chat")

    def persist(request, text):
        source = store.get_execution_input(get_current_execution_id())
        db = goals._db()
        branch = db.get_branch(request.session_id)
        db.append_message(request.session_id, {
            "id": source.assistant_message_id, "role": "assistant", "content": text,
            "predecessor": branch[-1]["id"] if branch else None,
        })

    def runner(*, request, cancel_event):
        calls.append(request)
        try:
            if len(calls) == 1:
                persist(request, "I claim the task is complete.")
                if automatic:
                    assert "started" in _task_start_impl("Inspect", steps=["Inspect output"]).content[0].text
                    assert "finished" in _task_step_impl(outcome="inspected").content[0].text
                else:
                    chat.update(request.session_id, "complete", expected=chat.current_identity())
                assert goals.load_goal(request.session_id)["status"] == "active"
            elif request.goal_verification:
                assert get_current_execution_id() != first.execution_id
                if outcome == "restart":
                    from openprogram.runtime.execution.recovery.effects import EffectClassification, EffectStatus, EffectStore
                    current = store.get_execution(get_current_execution_id())
                    effects = EffectStore(store)
                    effect = effects.register(effect_id="interrupted-read", execution_id=current.execution_id,
                        attempt_id=current.current_attempt_id, action_id="read", classification=EffectClassification.NONREPEATABLE,
                        idempotency_key=None, metadata={"kind": "tool.before", "payload": {"tool_name": "read"}})
                    effects.mark_dispatched(effect.effect_id, expected_status=EffectStatus.PLANNED)
                    raise BaseException("worker lost during verification")
                _, tools, _, prompt, _, contract = loop_runner.resolve_agent_runtime(request)
                assert [tool.name for tool in tools] == ["read"]
                assert "independent completion verifier" in prompt
                assert [tool["name"] for tool in contract["tools"]] == ["read"]
                result = asyncio.run(tools[0].execute(
                    "inspect-result", {"file_path": str(artifact)}, None, None))
                assert not result.is_error
                goal = goals.load_goal(request.session_id)
                refs = [key for key in goal["verification"]["evidence"] if key.startswith("read:")]
                assert refs
                if outcome in {"reused_path", "reused_version"}:
                    other = tmp_path / "other.txt" if outcome == "reused_path" else artifact
                    other.write_text("A different observation")
                    second = asyncio.run(tools[0].execute("inspect-result", {"file_path": str(other)}, None, None))
                    assert not second.is_error
                    if outcome == "reused_path":
                        artifact.write_text("Original artifact changed after inspection")
                    # The model cites the FIRST returned evidence ID, not a
                    # later observation that happens to reuse its tool-call ID.
                if outcome == "changed":
                    artifact.write_text("Changed after read")
                if outcome in {"pause", "edit", "cancel"}:
                    goals.apply_goal_action(request.session_id, outcome, prompt="New objective")
                if outcome == "budget_aba":
                    goals.apply_goal_action(request.session_id, "budget", max_tokens=500)
                    goals.apply_goal_action(request.session_id, "budget", max_tokens=0)
                if outcome == "user_input":
                    branch = goals._db().get_branch(request.session_id)
                    goals._db().append_message(request.session_id, {"id": "new-instructions", "role": "user", "content": "Also test the changed output", "predecessor": branch[-1]["id"]})
                report = {"requirements": [{"id": item["id"], "verdict": "unknown" if outcome == "unknown" else "met",
                           "reason": "Inspected the requested result", "evidence": ["made-up"] if outcome == "invented" else refs}
                           for item in goal["verification"]["requirements"] if outcome != "coverage"],
                          "reason": "Independent assessment"}
                if outcome != "missing":
                    persist(request, json.dumps(report))
            else:
                assert len(calls) == 3  # Unmet/unknown evidence returns to ordinary work once.
                goals.apply_goal_action(request.session_id, "pause")
        except Exception as exc:
            errors.append(exc)
            raise
        return SimpleNamespace(failed=False)

    original_activate = CanonicalAgentAdapter.activate
    async def activate(self, admission, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            threads.append(threading.current_thread())
        try:
            return await original_activate(self, admission, **kwargs)
        finally:
            if outcome == "restart" and store.get_execution(admission.execution_id).status.value == "reconciliation_required":
                recovery_ready.set()
    original_notify = chat.after_terminal
    def notify(executions, execution):
        if threading.current_thread() is not threading.main_thread():
            threads.append(threading.current_thread())
        result = original_notify(executions, execution)
        goal = goals.load_goal("goal-chat")
        if goal["status"] != "active" and goal.get("accounted_execution_id") == execution.execution_id:
            finished.set()
        return result
    monkeypatch.setattr(chat, "after_terminal", notify)
    monkeypatch.setattr(CanonicalAgentAdapter, "activate", activate)
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=runner, **kw))
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    if limit:
        # A limit reached after ``limit`` accounted turns.
        monkeypatch.setattr(goals, "budget_exhausted",
                            lambda goal, **_: "tokens" if int(goal.get("run_turns") or 0) >= limit else "")
    authority = local_owner_authority()
    request = TurnRequest("goal-chat", "work", "main", "web", permission_mode="bypass", model_override="openai/fake", **authority)
    first = adapter.admit(request, trusted_actor=authority, user_message_id="work-u", assistant_message_id="work-a",
                          config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(first))
    if outcome == "restart":
        from openprogram.runtime.execution import default_control_service
        from openprogram.runtime.execution.recovery import restart
        assert recovery_ready.wait(30)
        restart.reconcile(SimpleNamespace(_execution_store=store, _execution_control=default_control_service()))
    assert finished.wait(30), goals.load_goal("goal-chat")
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert not errors, errors
    goal = goals.load_goal("goal-chat")
    assert len(calls) == (2 if limit or outcome in {"met", "pause", "edit", "cancel"} else 3)
    assert (goal["status"] == "achieved") is (outcome == "met")
    if outcome == "met":
        assert goal["verification"]["status"] == "met"
        assert goal["verification"]["result_sha256"]
        from openprogram.programs.tools.planning.goal.presentation import annotate_messages
        rows = goals._db().get_messages("goal-chat")
        projected = annotate_messages("goal-chat", rows)
        result_id = goal["verification"]["result_message_id"]
        original = next(row for row in rows if row["id"] == result_id)
        shown = next(row for row in projected if row["id"] == result_id)
        assert shown["goal_verification"]["status"] == "met"
        assert shown["goal_verification"]["requirements"][0]["text"]
        assert shown["content"] == original["content"]
        assert "goal_verification" not in original
        work = next(row for row in projected if row["id"] == "work-a")
        assert "goal_verification" not in work
        from openprogram.state.store.session.graph.builder import build_session_graph
        graph = build_session_graph("goal-chat", messages=rows, include_layout=False)
        assert next(row for row in graph if row["id"] == result_id)["goal_verification"] == shown["goal_verification"]
        changed = [dict(row, content="changed") if row["id"] == result_id else row for row in rows]
        assert next(row for row in annotate_messages("goal-chat", changed) if row["id"] == result_id)["goal_verification"]["status"] == "unavailable"
    elif limit:
        assert goal["status"] == "budget_exhausted"
    chat.after_terminal(store, store.get_execution(first.execution_id))
    assert len(calls) <= 3


@pytest.mark.parametrize("fault", ["admission", "publication", "terminal"])
def test_verification_durable_retries_do_not_create_another_verifier(runtime, monkeypatch, tmp_path, fault):
    test_independent_verification_uses_real_read_and_persisted_result(
        runtime, monkeypatch, tmp_path, "met", False, fault=fault)


@pytest.mark.parametrize("outcome", ["met", "unknown", "missing", "invented"])
def test_last_admitted_verification_can_finish_but_cannot_exceed_budget(runtime, monkeypatch, tmp_path, outcome):
    test_independent_verification_uses_real_read_and_persisted_result(
        runtime, monkeypatch, tmp_path, outcome, False, limit=2)


@pytest.mark.parametrize("limit", ["budget", "unknown_effect", "child"])
def test_completion_prerequisites_prevent_verifier_admission(runtime, monkeypatch, limit):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    from tests.component.agent.security.test_recovery_permissions import orphan
    goals, chat, store = runtime
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: continuations.append(a))
    if limit == "budget":
        monkeypatch.setattr(goals, "budget_exhausted",
                            lambda goal, **_: "tokens" if int(goal.get("run_turns") or 0) >= 1 else "")
    elif limit == "unknown_effect":
        orphan(store, session="goal-chat")

    def runner(*, request, cancel_event):
        if limit == "child":
            from openprogram.runtime.execution.run_control import (
                get_current_execution_id,
            )
            revision = store.create_revision(manifest={})
            store.create_execution(session_id="goal-child", revision_id=revision.revision_id,
                                   parent_execution_id=get_current_execution_id())
        chat.update(request.session_id, "complete", expected=chat.current_identity())
        return SimpleNamespace(failed=False)
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
        trusted_actor={}, user_message_id="work", assistant_message_id="result", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    goal = goals.load_goal("goal-chat")
    assert goal["status"] == ("budget_exhausted" if limit == "budget" else "paused_recoverable")
    assert not continuations
    assert not goal.get("verification")


def test_the_goal_lock_is_waited_for_and_given_up_when_it_stays_held(tmp_path, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace

    import pytest

    from openprogram.programs.tools.planning.goal import chat
    from openprogram.programs.tools.planning.goal.ownership import goal_owner

    store = SimpleNamespace(root_path=tmp_path, get_session=lambda _sid: None)
    monkeypatch.setattr(chat.goals, "_db", lambda: store)
    held, release = threading.Event(), threading.Event()

    def holder():
        with goal_owner(store, "s") as acquired:
            assert acquired
            held.set()
            release.wait(30)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(30)
    try:
        monkeypatch.setattr(chat, "GOAL_LOCK_WAIT_S", 0.05)
        with pytest.raises(chat.goals.GoalBusy):
            with chat.locked("s"):
                pass
        assert chat.turn_step("s", lambda: "ran") == "ran"  # a running turn's step is not failed
        monkeypatch.setattr(chat, "GOAL_LOCK_WAIT_S", 30.0)
        threading.Timer(0.1, release.set).start()
        started = time.monotonic()
        with chat.locked("s"):
            assert time.monotonic() - started < 30
    finally:
        release.set()
        thread.join(30)
