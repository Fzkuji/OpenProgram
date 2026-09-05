"""Live Agent continuation contracts through chat, registry, and driver."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.execution.driver import DriverRegistry
from openprogram.execution.model import CommandStatus, ExecutionStatus


class _WebSocket:
    def __init__(self) -> None:
        from openprogram.agent.authority import owner_authority

        self.frames: list[dict] = []
        self.scope = {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

    async def send_text(self, value: str) -> None:
        self.frames.append(json.loads(value))


class _Provider:
    requires_credentials = False

    def __init__(self) -> None:
        self.responses: list[tuple] = []
        self.call_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_calls: set[int] = set()

    def add_response(self, *events) -> None:
        self.responses.append(events)

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        call_index = self.call_count
        if call_index in self.block_calls:
            self.entered.set()
            while not self.release.is_set():
                await asyncio.sleep(0)
        if call_index >= len(self.responses):
            raise AssertionError(f"unexpected provider call {call_index}")
        from tests.component.providers.scripted_provider import ScriptedProvider

        scripted = ScriptedProvider()
        scripted.add_response(*self.responses[call_index])
        self.call_count += 1
        async for event in scripted.stream_simple(model, context, options):
            yield event


class _Tools:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started: dict[str, threading.Event] = {}
        self.release: dict[str, threading.Event] = {}
        self.blocked: set[str] = set()
        self.wait_kind: str | None = None
        self.wait_timeout = 5.0
        self.wait_started = threading.Event()
        self.wait_finished = threading.Event()
        self.schema_variant = "initial"
        self.permission_variant = False
        self.implementation_variant = "initial"

    def tool(self, name: str):
        from openprogram.agent.types import AgentTool, AgentToolResult
        from openprogram.providers.types import TextContent

        self.started.setdefault(name, threading.Event())
        self.release.setdefault(name, threading.Event())

        async def execute(_call_id, _args, _cancel_event, _on_update):
            self.calls.append(name)
            self.started[name].set()
            if name == "first" and self.wait_kind is not None:
                self.wait_started.set()
                try:
                    if self.wait_kind == "ask":
                        from openprogram.agentic_programming.runtime import Runtime

                        Runtime().ask(
                            "Which answer?", options=["answer"], timeout=self.wait_timeout,
                        )
                    elif self.wait_kind == "approval":
                        from openprogram.agent.dispatcher.types import TurnRequest
                        from openprogram.agent.internals._approval import (
                            await_user_approval,
                        )

                        await await_user_approval(
                            req=TurnRequest(
                                session_id="agent-continuation",
                                user_text="continue safely",
                                agent_id="main", source="web",
                                permission_mode="ask",
                            ),
                            tool_name="first", args={}, on_event=lambda _event: None,
                            timeout=self.wait_timeout,
                        )
                finally:
                    self.wait_finished.set()
            while name in self.blocked and not self.release[name].is_set():
                await asyncio.sleep(0)
            return AgentToolResult(content=[TextContent(text=f"{name}:ok")])

        result = AgentTool(
            name=name, label=name, description=name,
            parameters={
                "type": "object",
                "properties": (
                    {"changed": {"type": "string"}}
                    if self.schema_variant != "initial" else {}
                ),
            }, execute=execute,
        )
        result._runtime_implementation = {
            "module": __name__, "qualname": f"_Tools.tool:{name}",
            "code_sha256": hashlib.sha256(
                self.implementation_variant.encode("utf-8")
            ).hexdigest(),
        }
        result._requires_approval = self.permission_variant
        if name == "first" and self.wait_kind is not None:
            if self.wait_kind == "ask":
                manifest = {
                    "kind": "ask", "prompt": "Which answer?",
                    "options": ["answer"], "multi": False,
                    "allow_custom": True, "detail": "", "schema": {},
                    "questions": [], "request_metadata": {},
                }
            else:
                manifest = {
                    "kind": "approval", "prompt": "Allow?",
                    "options": ["允许", "拒绝"], "multi": False,
                    "allow_custom": False, "detail": "first",
                    "schema": {}, "questions": [],
                    "request_metadata": {
                        "tool": "first", "tool_call_id": "",
                    },
                }
            manifest["policy_snapshot"] = {
                "version": 1, "kind": self.wait_kind,
                "on_answer": "continue", "on_decline": "fail",
                "on_timeout": "fail",
            }
            manifest["timeout"] = self.wait_timeout
            object.__setattr__(
                result, "_interaction_manifest",
                lambda call_id, _args, value=manifest: {
                    **value,
                    "request_metadata": {
                        **value["request_metadata"],
                        "tool_call_id": str(call_id),
                    },
                },
            )
        return result


def _wait(predicate, timeout: float = 4.0, detail=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        threading.Event().wait(0.01)
    suffix = f": {detail()}" if detail is not None else ""
    raise AssertionError(f"condition did not become true{suffix}")


@pytest.fixture
def real_agent_chat(tmp_path, monkeypatch):
    """Use registered provider/tool callbacks, never a synthetic safe point."""
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import server
    from openprogram.webui.ws_actions import chat as chat_actions
    from openprogram.webui.ws_actions import session as session_actions
    import openprogram.agent.dispatcher as dispatcher
    import openprogram.agent.dispatcher.loop_runner as loop_runner
    import openprogram.agent.questions as questions
    import openprogram.agent.session_config as session_config
    import openprogram.agent.session_db as session_db
    import openprogram.providers.api_registry as api_registry
    import openprogram.store.session.session_store as session_store_module
    from openprogram.providers.api_registry import get_api_provider, register_api_provider
    from openprogram.providers.types import Model
    from openprogram.agent.production_driver import AgentProductionDriver

    store = ExecutionStore(tmp_path / "execution.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: control)
    sessions = SessionStore(tmp_path / "sessions-git")
    session_id = "agent-continuation"
    sessions.create_session(session_id, "main")
    monkeypatch.setattr(session_db, "default_db", lambda: sessions)
    monkeypatch.setattr(session_store_module, "_default_store", sessions)
    monkeypatch.setattr(server, "_get_or_create_session", lambda *_args, **_kwargs: {"id": session_id, "messages": []})
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda _session_id: "main")
    monkeypatch.setattr(server, "_emit_running_task_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(server, "_broadcast", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("openprogram.agent.workspace_alignment.get_workspace_alignment", lambda _session_id: {"status": "aligned"})

    provider = _Provider()
    api_name = "continuation-real-api"
    previous_provider = get_api_provider(api_name)
    register_api_provider(api_name, provider)
    model = Model(
        id="continuation-real-model", name="continuation-real-model",
        api=api_name, provider="continuation-real-provider",
        base_url="http://continuation.invalid",
    )
    tools = _Tools()
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda _id: {"id": "main", "model": "continuation-real-provider/continuation-real-model"})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda _profile, _override: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *_args, **_kwargs: [tools.tool("first"), tools.tool("second")])
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", lambda tool, *_args: tool)
    registry = questions.QuestionRegistry()
    monkeypatch.setattr(questions, "get_question_registry", lambda: registry)
    monkeypatch.setattr(
        session_config, "save_session_run_config",
        lambda *_args, **kwargs: SimpleNamespace(
            tools_enabled=True, tools_override=None, web_search=False, toolset=None,
            thinking_effort="medium", permission_mode=kwargs.get("permission_mode"),
            permission_rules=None, sandbox_enabled=None, additional_working_dirs=[],
        ),
    )
    monkeypatch.setattr(session_config, "permission_from_config", lambda config, default=None: config.permission_mode or default or "bypass")
    monkeypatch.setattr(session_config, "project_defaults", lambda _session_id: {"permission_mode": "bypass"})

    outcomes: list[object] = []
    activation_errors: list[str] = []
    original_run_attempt = AgentProductionDriver._run_attempt

    async def observed_run_attempt(self, *args, **kwargs):
        result = await original_run_attempt(self, *args, **kwargs)
        outcomes.append(result)
        return result

    monkeypatch.setattr(AgentProductionDriver, "_run_attempt", observed_run_attempt)

    async def activate(attempt, activation):
        driver = AgentProductionDriver(store, control_service=control, question_registry=registry)
        try:
            binding = await driver.activate(attempt, activation)
        except Exception as exc:
            activation_errors.append(f"{type(exc).__name__}: {exc}")
            raise
        return binding

    control.activator = activate
    harness = SimpleNamespace(
        store=store, control=control, sessions=sessions, session_id=session_id,
        provider=provider, model=model, tools=tools, server=server, outcomes=outcomes,
        activation_errors=activation_errors, registry=registry, execution_id=None,
    )
    try:
        yield harness
    finally:
        provider.release.set()
        for gate in tools.release.values():
            gate.set()
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and server._is_run_active(session_id):
            threading.Event().wait(0.01)
        with server._running_tasks_lock:
            running = server._running_tasks.get(session_id)
            thread = running.get("thread") if isinstance(running, dict) else None
        if thread is not None:
            thread.join(timeout=4.0)
        with server._running_tasks_lock:
            server._running_tasks.pop(session_id, None)
        if previous_provider is None:
            api_registry._registry.pop(api_name, None)
            api_registry._original_registry.pop(api_name, None)
        else:
            register_api_provider(api_name, previous_provider)


def _chat(harness):
    from openprogram.webui.ws_actions.chat import handle_chat

    ws = _WebSocket()
    asyncio.run(handle_chat(ws, {
        "text": "continue safely", "session_id": harness.session_id,
        "permission_mode": "bypass",
    }))
    ack = next(frame for frame in ws.frames if frame.get("type") == "chat_ack")
    execution_id = ack["data"]["execution_id"]
    harness.execution_id = execution_id
    return _wait(lambda: (
        item
        if (item := harness.store.get_execution(execution_id)).status is not ExecutionStatus.QUEUED
        else None
    ))


def _command(harness, action: str, execution, command_id: str):
    from openprogram.webui.ws_actions import runtime

    ws = _WebSocket()
    asyncio.run(runtime.ACTIONS[action](ws, {
        "type": "execution.command", "action": action, "command_id": command_id,
        "execution_id": execution.execution_id,
        "expected_version": execution.status_version,
        "payload": {},
    }))
    frame = next(frame for frame in ws.frames if frame.get("type") == "execution.command.updated")
    return frame.get("command") or frame["data"]["command"]


def _pending_question(harness, *, kind: str):
    return _wait(
        lambda: next(
            (question for question in harness.registry.list_pending(harness.session_id)
             if question.kind == kind),
            None,
        ),
        detail=lambda: {
            "execution": harness.store.get_execution(harness.execution_id).to_dict()
            if harness.execution_id else None,
            "pending": [question.kind for question in harness.registry.list_pending()],
        },
    )


def _question_action(harness, action: str, question_id: str, **payload):
    from openprogram.webui.ws_actions import runtime

    ws = _WebSocket()
    question = next(
        question for question in harness.registry.list_pending(harness.session_id)
        if question.id == question_id
    )
    execution = harness.store.get_execution(question.execution_id)
    assert execution is not None
    if action == "question_reply":
        command_action = "execution.wait.answer"
        answer = payload.get("answer")
        if question.kind == "approval":
            answer = {"answer": answer, "scope": "once"}
        command_payload = {
            "wait_id": question.id,
            "generation": question.wait_generation,
            "answer": answer,
        }
    elif action == "question_reject":
        command_action = "execution.wait.decline"
        command_payload = {
            "wait_id": question.id,
            "generation": question.wait_generation,
            "reason": None,
        }
    else:
        raise AssertionError(f"unsupported question action: {action}")
    asyncio.run(runtime.ACTIONS[command_action](ws, {
        "type": "execution.command", "action": command_action,
        "command_id": f"{command_action}:{question.id}",
        "execution_id": execution.execution_id,
        "expected_version": execution.status_version,
        "payload": command_payload,
    }))


def test_provider_pause_resume_reuses_saved_terminal_answer(real_agent_chat):
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("saved answer"))
    real_agent_chat.provider.block_calls.add(0)
    execution = _chat(real_agent_chat)
    _wait(real_agent_chat.provider.entered.is_set)
    assert _command(real_agent_chat, "execution.pause", execution, "pause-provider")["status"] in {"accepted", "applying", "applied"}
    real_agent_chat.provider.release.set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ))
    assert paused.checkpoint_head_id is not None
    first_attempt = next(
        event.payload["attempt"]
        for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "attempt.active"
    )
    resumed = _command(real_agent_chat, "execution.continue", paused, "continue-provider")
    assert resumed["status"] in {"accepted", "applying", "applied"}
    completed = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
        } else None
    ), detail=lambda: {
        "execution": real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
        "events": [
            {"kind": event.kind, "payload": event.payload}
            for event in real_agent_chat.store.list_events(execution.execution_id)
        ],
        "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
        "activation_errors": real_agent_chat.activation_errors,
    })
    assert completed.status is ExecutionStatus.COMPLETED, real_agent_chat.store.list_events(execution.execution_id)
    assert completed.current_attempt_id is None
    resumed_attempts = [
        event.payload["attempt"]
        for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "attempt.active"
    ]
    assert resumed_attempts[-1]["attempt_id"] != first_attempt["attempt_id"]
    assert resumed_attempts[-1]["generation"] > first_attempt["generation"]
    assert real_agent_chat.provider.call_count == 1
    branch = real_agent_chat.sessions.get_branch(real_agent_chat.session_id)
    users = [item for item in branch if item["role"] == "user"]
    replies = [item for item in branch if item.get("id", "").endswith("_reply")]
    assert len(users) == 1 and len(replies) == 1
    assert replies[0]["id"] == f"{users[0]['id']}_reply"
    assert replies[0]["content"] == "saved answer"


@pytest.mark.parametrize("mutation", ["schema", "permission", "implementation"])
def test_continue_rejects_changed_tool_runtime_contract(real_agent_chat, mutation):
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("saved answer"))
    real_agent_chat.provider.block_calls.add(0)
    execution = _chat(real_agent_chat)
    _wait(real_agent_chat.provider.entered.is_set)
    assert _command(real_agent_chat, "execution.pause", execution, "pause-contract")["status"] in {
        "accepted", "applying", "applied",
    }
    real_agent_chat.provider.release.set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ))
    checkpoint_id = paused.checkpoint_head_id
    if mutation == "schema":
        real_agent_chat.tools.schema_variant = "changed"
    elif mutation == "permission":
        real_agent_chat.tools.permission_variant = True
    else:
        real_agent_chat.tools.implementation_variant = "changed"

    command = _command(real_agent_chat, "execution.continue", paused, f"continue-{mutation}")
    current = real_agent_chat.store.get_execution(execution.execution_id)
    assert command["status"] == "rejected"
    assert command["rejection_code"] == "continuation_contract_mismatch"
    assert current is not None and current.status is ExecutionStatus.PAUSED
    assert current.checkpoint_head_id == checkpoint_id
    assert real_agent_chat.provider.call_count == 1
    assert real_agent_chat.tools.calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", "changed-provider"), ("base_url", "http://changed.invalid")],
)
def test_continue_rejects_same_model_id_with_changed_provider_endpoint(
    real_agent_chat, field, value,
):
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("saved answer"))
    real_agent_chat.provider.block_calls.add(0)
    execution = _chat(real_agent_chat)
    _wait(real_agent_chat.provider.entered.is_set)
    _command(real_agent_chat, "execution.pause", execution, "pause-model-contract")
    real_agent_chat.provider.release.set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ))
    setattr(real_agent_chat.model, field, value)

    command = _command(real_agent_chat, "execution.continue", paused, f"continue-{field}")
    current = real_agent_chat.store.get_execution(execution.execution_id)
    assert command["status"] == "rejected"
    assert command["rejection_code"] == "continuation_contract_mismatch"
    assert current is not None and current.status is ExecutionStatus.PAUSED
    assert current.checkpoint_head_id == paused.checkpoint_head_id
    assert real_agent_chat.provider.call_count == 1


def test_continue_rejects_changed_session_project_workdir(real_agent_chat, tmp_path):
    from openprogram.store.project import project_store
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("saved answer"))
    real_agent_chat.provider.block_calls.add(0)
    execution = _chat(real_agent_chat)
    _wait(real_agent_chat.provider.entered.is_set)
    _command(real_agent_chat, "execution.pause", execution, "pause-project")
    real_agent_chat.provider.release.set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ))
    checkpoint_id = paused.checkpoint_head_id

    changed_path = tmp_path / "changed-project"
    changed_path.mkdir()
    changed_project = project_store.resolve_project(changed_path)
    project_store.unbind_session(real_agent_chat.session_id)
    project_store.bind_session(real_agent_chat.session_id, changed_project.id)

    command = _command(real_agent_chat, "execution.continue", paused, "continue-project")
    current = real_agent_chat.store.get_execution(execution.execution_id)
    assert command["status"] == "rejected"
    assert command["rejection_code"] == "continuation_contract_mismatch"
    assert current is not None and current.status is ExecutionStatus.PAUSED
    assert current.checkpoint_head_id == checkpoint_id
    assert real_agent_chat.provider.call_count == 1
    assert real_agent_chat.tools.calls == []


def test_continue_accepts_an_unchanged_runtime_contract(real_agent_chat):
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("saved answer"))
    real_agent_chat.provider.block_calls.add(0)
    execution = _chat(real_agent_chat)
    _wait(real_agent_chat.provider.entered.is_set)
    _command(real_agent_chat, "execution.pause", execution, "pause-unchanged")
    real_agent_chat.provider.release.set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ))
    command = _command(real_agent_chat, "execution.continue", paused, "continue-unchanged")
    assert command["status"] in {"accepted", "applying", "applied"}
    completed = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
        } else None
    ))
    assert completed.status is ExecutionStatus.COMPLETED
    assert real_agent_chat.provider.call_count == 1


def test_after_tool_continue_runs_only_the_unfinished_tool(real_agent_chat):
    from tests.component.providers.scripted_provider import ScriptedToolCall

    real_agent_chat.provider.add_response(
        ScriptedToolCall("first", {}, "call-first"),
        ScriptedToolCall("second", {}, "call-second"),
    )
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedText("final answer"))
    real_agent_chat.tools.blocked.add("first")
    execution = _chat(real_agent_chat)
    _wait(
        lambda: real_agent_chat.tools.started.get("first") and real_agent_chat.tools.started["first"].is_set(),
        detail=lambda: {
            "provider_calls": real_agent_chat.provider.call_count,
            "tool_calls": real_agent_chat.tools.calls,
            "tool_started": list(real_agent_chat.tools.started),
            "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
            "execution": real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
        },
    )
    pause = _command(real_agent_chat, "execution.pause", execution, "pause-first")
    assert pause["status"] in {"accepted", "applying", "applied"}, pause
    real_agent_chat.tools.release["first"].set()
    paused = _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.PAUSED else None
    ), detail=lambda: {
        "execution": real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
        "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
        "commands": [command.to_dict() for command in real_agent_chat.store.list_commands(execution.execution_id)],
    })
    assert paused.safe_point["phase"] == "after_tool"
    assert real_agent_chat.tools.calls == ["first"]
    first_attempt = next(
        event.payload["attempt"]
        for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "attempt.active"
    )
    step = _command(real_agent_chat, "execution.step", paused, "step-second")
    duplicate = _command(real_agent_chat, "execution.step", paused, "step-second")
    assert step["status"] in {"accepted", "applying", "applied"}, step
    assert duplicate["command_id"] == step["command_id"]
    paused_after_step = _wait(
        lambda: (
            item
            if (
                (item := real_agent_chat.store.get_execution(execution.execution_id)).status
                is ExecutionStatus.PAUSED
                and item.status_version > paused.status_version
                and real_agent_chat.store.get_command("step-second").status
                is CommandStatus.APPLIED
            )
            else None
        ),
        timeout=10.0,
        detail=lambda: {
            "execution": real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
            "step_command": real_agent_chat.store.get_command("step-second").to_dict(),
            "calls": real_agent_chat.tools.calls,
            "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
        },
    )
    assert paused_after_step.safe_point["phase"] == "after_tool"
    assert real_agent_chat.tools.calls == ["first", "second"]
    assert real_agent_chat.store.get_command("step-second").result_json["managed_action_id"]
    step_attempts = [
        event.payload["attempt"]
        for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "attempt.active"
    ]
    assert step_attempts[-1]["attempt_id"] != first_attempt["attempt_id"]
    assert step_attempts[-1]["generation"] > first_attempt["generation"]
    assert _command(real_agent_chat, "execution.continue", paused_after_step, "continue-final")["status"] in {"accepted", "applying", "applied"}
    _wait(lambda: (
        item if (item := real_agent_chat.store.get_execution(execution.execution_id)).status is ExecutionStatus.COMPLETED else None
    ))
    assert real_agent_chat.tools.calls == ["first", "second"]
    assert real_agent_chat.provider.call_count == 2
    completed_events = [
        event for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "agent.action.completed"
    ]
    assert len(completed_events) == 1
    assert real_agent_chat.store.get_command("step-second").status is CommandStatus.APPLIED


@pytest.mark.parametrize(
    ("wait_kind", "resolution"),
    [
        ("ask", "reply"),
        ("ask", "reject"),
        ("ask", "timeout"),
        ("approval", "reply"),
        ("approval", "reject"),
        ("approval", "timeout"),
    ],
)
def test_wait_is_a_durable_safe_point_before_tool_dispatch(
    real_agent_chat, wait_kind, resolution,
):
    """A declared wait is durable before tool dispatch and owns continuation."""

    real_agent_chat.tools.wait_kind = wait_kind
    if resolution == "timeout":
        real_agent_chat.tools.wait_timeout = 0.3
    from tests.component.providers.scripted_provider import ScriptedToolCall
    from tests.component.providers.scripted_provider import ScriptedText

    real_agent_chat.provider.add_response(ScriptedToolCall("first", {}, "call-wait"))
    if resolution == "reply":
        real_agent_chat.provider.add_response(ScriptedText("final answer"))
    execution = _chat(real_agent_chat)
    question = _pending_question(real_agent_chat, kind=wait_kind)
    execution = _wait(lambda: (
        item
        if (item := real_agent_chat.store.get_execution(execution.execution_id)).status
        is ExecutionStatus.PAUSED
        else None
    ))
    assert execution.status is ExecutionStatus.PAUSED
    assert execution.checkpoint_head_id is not None
    checkpoint = real_agent_chat.control.checkpoints.get(execution.checkpoint_head_id)
    assert checkpoint is not None
    assert checkpoint.safe_point["phase"] == "after_provider"
    assert checkpoint.safe_point["step_id"].startswith("wait:")
    active_events = [
        event.payload["attempt"]
        for event in real_agent_chat.store.list_events(execution.execution_id)
        if event.kind == "attempt.active"
    ]
    assert len(active_events) == 1
    assert real_agent_chat.tools.wait_finished.is_set() is False

    if resolution == "reply":
        _question_action(
            real_agent_chat,
            "question_reply",
            question.id,
            answer="answer" if wait_kind == "ask" else "允许",
        )
    elif resolution == "reject":
        _question_action(real_agent_chat, "question_reject", question.id)
    else:
        from openprogram.execution.waits import DurableWaitStore
        wait = DurableWaitStore(real_agent_chat.store).get_wait(question.id)
        assert wait is not None
        _wait(lambda: time.time() >= wait.expires_at, timeout=2.0)
        DurableWaitStore(real_agent_chat.store).expire_due()
        asyncio.run(real_agent_chat.control.recover_wait_outcomes())

    final = _wait(
        lambda: (
            item
            if (item := real_agent_chat.store.get_execution(execution.execution_id)).status in {
                ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
            }
            else None
        ),
        detail=lambda: real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
    )
    if resolution == "reply":
        assert final.status is ExecutionStatus.COMPLETED, {
            "execution": final.to_dict(),
            "events": [{"kind": event.kind, "payload": event.payload} for event in real_agent_chat.store.list_events(execution.execution_id)],
            "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
            "activation_errors": real_agent_chat.activation_errors,
        }
        assert real_agent_chat.tools.calls == ["first"]
        assert real_agent_chat.tools.wait_finished.is_set()
        assert len([
            event for event in real_agent_chat.store.list_events(execution.execution_id)
            if event.kind == "attempt.active"
        ]) == 2
    else:
        assert final.status is ExecutionStatus.FAILED
        assert real_agent_chat.tools.calls == []


def test_cancel_wakes_real_question_wait_with_exact_reason(real_agent_chat):
    from tests.component.providers.scripted_provider import ScriptedToolCall

    real_agent_chat.tools.wait_kind = "ask"
    real_agent_chat.provider.add_response(ScriptedToolCall("first", {}, "call-cancel-wait"))
    execution = _chat(real_agent_chat)
    question = _pending_question(real_agent_chat, kind="ask")

    from openprogram.webui.ws_actions import runtime

    current = real_agent_chat.store.get_execution(execution.execution_id)
    assert current is not None
    asyncio.run(runtime.ACTIONS["execution.cancel"](
        _WebSocket(), {
            "type": "execution.command", "action": "execution.cancel",
            "command_id": "cancel-real-question-wait",
            "execution_id": execution.execution_id,
            "expected_version": current.status_version,
            "payload": {},
        },
    ))
    final = _wait(lambda: (
        item
        if (item := real_agent_chat.store.get_execution(execution.execution_id)).status
        in {ExecutionStatus.CANCELLED, ExecutionStatus.RECONCILIATION_REQUIRED}
        else None
    ), detail=lambda: {
        "execution": real_agent_chat.store.get_execution(execution.execution_id).to_dict(),
        "commands": [command.to_dict() for command in real_agent_chat.store.list_commands(execution.execution_id)],
        "outcomes": [getattr(item, "error", repr(item)) for item in real_agent_chat.outcomes],
    })
    cancel_command = next(
        command for command in real_agent_chat.store.list_commands(execution.execution_id)
        if command.kind.value == "execution.cancel"
    )
    assert cancel_command.payload["reason_code"] == "cancel.user"
    assert real_agent_chat.registry.list_pending(real_agent_chat.session_id) == []
    assert real_agent_chat.tools.calls == []
    assert final.checkpoint_head_id is not None
    assert question.execution_id == execution.execution_id


def test_durable_wait_registration_publishes_checkpoint_before_tool_dispatch(
    real_agent_chat,
):
    from tests.component.providers.scripted_provider import ScriptedToolCall

    real_agent_chat.tools.wait_kind = "ask"
    real_agent_chat.provider.add_response(ScriptedToolCall("first", {}, "call-crash-wait"))

    execution = _chat(real_agent_chat)
    question = _pending_question(real_agent_chat, kind="ask")
    paused = _wait(lambda: (
        item
        if (item := real_agent_chat.store.get_execution(execution.execution_id)).status
        is ExecutionStatus.PAUSED
        else None
    ), detail=lambda: real_agent_chat.store.get_execution(execution.execution_id).to_dict())
    assert paused.checkpoint_head_id is not None
    assert paused.owner_lease == {}
    assert paused.current_attempt_id is None
    assert real_agent_chat.control.checkpoints.get(paused.checkpoint_head_id) is not None
    assert real_agent_chat.tools.calls == []
    # Resolve it through the public canonical wait command.
    _question_action(real_agent_chat, "question_reject", question.id)


def test_permission_change_resumes_real_approval_without_repeating_provider(real_agent_chat, monkeypatch):
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall
    from openprogram.agent.authority import local_owner_authority
    from openprogram.agent.internals._approval import wrap_with_approval
    from openprogram.agent.permissions import update_permission, reconcile_permission_waits
    import openprogram.agent.dispatcher.loop_runner as loop_runner

    frames = []
    monkeypatch.setattr("openprogram.events.emit_ws_frame", frames.append)
    h = real_agent_chat
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", wrap_with_approval)
    actor = local_owner_authority()
    update_permission(h.session_id, "ask", 0, actor)
    h.provider.add_response(ScriptedToolCall("first", {}, "permission-first"), ScriptedToolCall("second", {}, "permission-second"))
    h.provider.add_response(ScriptedText("done"))
    execution = _chat(h)
    question = _pending_question(h, kind="approval")
    assert h.tools.calls == []
    assert h.provider.call_count == 1
    update_permission(h.session_id, "bypass", 1, actor)
    asyncio.run(reconcile_permission_waits(h.session_id, service=h.control))
    completed = _wait(lambda: (
        item if (item := h.store.get_execution(execution.execution_id)).status in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
        } else None
    ), detail=lambda: {"errors": h.activation_errors, "outcomes": [str(x) for x in h.outcomes]})
    assert completed.status is ExecutionStatus.COMPLETED, (h.activation_errors, [getattr(x, "error", str(x)) for x in h.outcomes])
    assert h.tools.calls == ["first", "second"]
    assert h.provider.call_count == 2
    from openprogram.execution.waits import DurableWaitStore, WaitStatus
    assert DurableWaitStore(h.store).get_wait(question.id).status is WaitStatus.RESOLVED
    assert any(frame["type"] == "question.replied" and frame["data"]["id"] == question.id for frame in frames)


def test_permission_change_before_tool_check_applies_to_running_turn(real_agent_chat, monkeypatch):
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall
    from openprogram.agent.authority import local_owner_authority
    from openprogram.agent.internals._approval import wrap_with_approval
    from openprogram.agent.permissions import update_permission
    import openprogram.agent.dispatcher.loop_runner as loop_runner

    h = real_agent_chat
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", wrap_with_approval)
    actor = local_owner_authority()
    h.provider.block_calls.add(0)
    h.provider.add_response(ScriptedToolCall("first", {}, "permission-first"))
    h.provider.add_response(ScriptedText("done"))
    execution = _chat(h)
    _wait(h.provider.entered.is_set)
    update_permission(h.session_id, "ask", 0, actor)
    h.provider.release.set()
    question = _pending_question(h, kind="approval")
    assert question.execution_id == execution.execution_id
    assert h.tools.calls == []
    _question_action(h, "question_reply", question.id, answer="approve")
    _wait(lambda: h.store.get_execution(execution.execution_id).status in {
        ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
    })
    assert h.tools.calls == ["first"]


def test_permission_update_during_wait_publication_is_not_lost(real_agent_chat, monkeypatch):
    from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall
    from openprogram.agent.authority import local_owner_authority
    from openprogram.agent.internals._approval import wrap_with_approval
    from openprogram.agent.permissions import update_permission
    import openprogram.agent.dispatcher.loop_runner as loop_runner
    h = real_agent_chat
    actor = local_owner_authority()
    monkeypatch.setattr(loop_runner, "_wrap_with_approval", wrap_with_approval)
    update_permission(h.session_id, "ask", 0, actor)
    original = h.control.open_wait_at_safe_point
    def open_after_update(**kwargs):
        update_permission(h.session_id, "bypass", 1, actor)
        return original(**kwargs)
    monkeypatch.setattr(h.control, "open_wait_at_safe_point", open_after_update)
    h.provider.add_response(ScriptedToolCall("first", {}, "publication-first"))
    h.provider.add_response(ScriptedText("done"))
    execution = _chat(h)
    completed = _wait(lambda: (
        item if (item := h.store.get_execution(execution.execution_id)).status in {
            ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
        } else None
    ), detail=lambda: {"errors": h.activation_errors, "outcomes": [str(x) for x in h.outcomes]})
    assert completed.status is ExecutionStatus.COMPLETED, (h.activation_errors, [getattr(x, "error", str(x)) for x in h.outcomes])
    assert h.tools.calls == ["first"]
