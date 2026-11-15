"""Nested tool runtimes honour the foreground turn's model selection."""

from types import SimpleNamespace
from contextlib import nullcontext
import asyncio

import pytest

from openprogram.runtime.conversation.turn_runtime.turn_context import TurnBindings
from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.context.session_db import SessionDB
from openprogram.runtime.agent.call_state import _current_runtime


@pytest.mark.parametrize("selection", ["override", "session", "snapshot", "profile"])
def test_tool_runtime_uses_chat_model(tmp_path, monkeypatch, selection):
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation import session_model
    from openprogram.runtime.agent.runtime import factory as registry

    db = SessionDB(tmp_path / "sessions")
    db.create_session("chat", "main", source="test")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    profile = {"model": "chat-provider/profile-model"}
    monkeypatch.setattr(dispatcher, "_load_agent_profile", lambda _id: profile)
    # The first config read in a fresh test home adopts the newest
    # conversation's model once (last_used._adopt_existing_install), which
    # reads a session pin through the same function; keep that one-time
    # migration out of the reads this test counts.
    monkeypatch.setattr(
        "openprogram.models.providers.catalog.last_used._adopt_existing_install",
        lambda: None,
    )
    reads = []

    def session_choice(*args):
        reads.append(args)
        return (
            (None, None)
            if selection == "profile"
            else ("chat-provider", "session-model")
        )

    monkeypatch.setattr(session_model, "ensure_session_chat_model", session_choice)
    monkeypatch.setattr(session_model, "read_session_chat_model", session_choice)
    resolutions = []

    def resolve(prof, override):
        resolutions.append((prof, override))
        provider, model_id = (override or prof["model"]).split("/", 1)
        return SimpleNamespace(provider=provider, id=model_id)

    monkeypatch.setattr(dispatcher, "_resolve_model", resolve)
    calls = []
    runtime = SimpleNamespace()

    def create(**kwargs):
        calls.append(kwargs)
        return runtime

    monkeypatch.setattr(registry, "create_runtime", create)
    req = TurnRequest(
        session_id="chat",
        user_text="hi",
        agent_id="main",
        source="test",
        model_override="chat-provider/override-model"
        if selection == "override"
        else None,
        profile_snapshot=profile if selection == "snapshot" else None,
    )
    outer = SimpleNamespace()
    token = _current_runtime.set(outer)
    try:
        binding = TurnBindings.bind(
            req=req,
            assistant_msg_id="reply",
            db=db,
            snapshot_project_baseline=False,
        )
        try:
            expected = {
                "override": "override-model",
                "session": "session-model",
                "snapshot": "session-model",
                "profile": "profile-model",
            }[selection]
            assert calls == [{"provider": "chat-provider", "model": expected}]
            assert _current_runtime.get() is runtime
            assert resolutions == [
                (
                    profile,
                    None if selection == "profile" else f"chat-provider/{expected}",
                )
            ]
            assert reads == (
                []
                if selection == "override"
                else [("chat",)]
                if selection == "snapshot"
                else [("chat", "main")]
            )
        finally:
            binding.release()
        assert _current_runtime.get() is outer
    finally:
        _current_runtime.reset(token)


@pytest.fixture
def selected_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("OPENPROGRAM_CONFIG_DIR", str(tmp_path / ".openprogram"))
    from openprogram.runtime.conversation import turn_runtime as dispatcher
    from openprogram.runtime.conversation import session_model
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.agent.turn_request import TurnRequest

    db = SessionDB(tmp_path / "sessions")
    db.create_session("chat", "main", source="test")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    monkeypatch.setattr(
        dispatcher, "_load_agent_profile", lambda _: {"model": "selected/chat-model"}
    )
    monkeypatch.setattr(
        session_model,
        "ensure_session_chat_model",
        lambda *_: ("selected", "chat-model"),
    )
    monkeypatch.setattr(
        dispatcher,
        "_resolve_model",
        lambda *args: SimpleNamespace(provider="selected", id="chat-model"),
    )
    return db, TurnRequest(
        session_id="chat",
        user_text="hi",
        agent_id="main",
        source="test",
        model_override="selected/chat-model",
    )


@pytest.mark.parametrize("has_outer", [False, True])
@pytest.mark.parametrize("model_id", ["chat-model", "qwen:latest"])
@pytest.mark.parametrize("async_call", [False, True])
def test_failed_selected_runtime_preserves_error_without_global_fallback(
    selected_turn, monkeypatch, has_outer, model_id, async_call
):
    from openprogram.runtime.agent.runtime import factory as registry
    from openprogram import Agent
    from openprogram.runtime.agent.runtime import Runtime
    from openprogram.runtime.conversation import turn_runtime as dispatcher

    monkeypatch.setattr(
        dispatcher,
        "_resolve_model",
        lambda *_: SimpleNamespace(provider="selected", id=model_id),
    )
    calls = []
    original = RuntimeError("selected provider auth unavailable")

    def create(**kw):
        calls.append(kw)
        raise original

    monkeypatch.setattr(registry, "create_runtime", create)

    class WorkflowAgent(Agent):
        method_options = {
            "workflow": {
                "as_tool": False,
                "expose": "hidden",
                "name": "workflow",
                "tool": True,
            },
        }

        def workflow(self, runtime=None):
            return (
                asyncio.run(runtime.async_exec(content="test"))
                if async_call
                else runtime.exec(content="test")
            )

    workflow = WorkflowAgent().workflow
    db, req = selected_turn
    outer = (
        Runtime(call=lambda *_args, **_kw: "wrong-provider", model="test")
        if has_outer
        else None
    )
    outer_token = _current_runtime.set(outer)
    binding = TurnBindings.bind(
        req=req, assistant_msg_id="reply", db=db, snapshot_project_baseline=False
    )
    try:
        with pytest.raises(RuntimeError) as caught:
            workflow()
        assert caught.value is original
        assert calls == [{"provider": "selected", "model": model_id}]
    finally:
        binding.release()
        assert _current_runtime.get(None) is outer
        _current_runtime.reset(outer_token)
        if outer is not None:
            outer.close()


def test_gui_public_wrapper_propagates_selected_runtime_to_subprocess(
    selected_turn, monkeypatch
):
    from openprogram.runtime.agent.runtime import factory as registry
    from openprogram.runtime.conversation.turn_runtime.turn_context import TurnBindings
    from openprogram.runtime.conversation.turn_runtime.runtime_attach import _wrap_agentic_runtime_block
    from openprogram.models.providers.types import AgentTool
    from openprogram.runtime.agent.call_state import _current_runtime
    from openprogram.runtime.conversation import process_runner

    db, req = selected_turn
    selected = SimpleNamespace(provider_id="selected", model="selected:chat-model")
    monkeypatch.setattr(registry, "create_runtime", lambda **kw: selected)
    monkeypatch.setattr(
        "openprogram.runtime.execution.dag.live_progress", lambda *_a, **_kw: nullcontext()
    )
    monkeypatch.setattr(
        "openprogram.runtime.execution.default_store",
        lambda: SimpleNamespace(get_execution=lambda _: None),
    )
    captured = []

    def run(**kw):
        captured.append(kw)
        return {"text": "done"}

    monkeypatch.setattr(process_runner, "run_agent_method_in_subprocess", run)

    async def raw_execute(*args):
        raise AssertionError("Expected subprocess branch")

    tool = AgentTool(
        name="gui_agent",
        description="test GUI",
        label="GUI",
        parameters={"type": "object"},
        execute=raw_execute,
    )
    object.__setattr__(tool, "_is_agent_method", True)
    outer_token = _current_runtime.set(None)
    binding = TurnBindings.bind(
        req=req, assistant_msg_id="reply", db=db, snapshot_project_baseline=False
    )
    from openprogram.state.worktree.context import set_worktree, reset_worktree

    work_token = set_worktree("/tmp/selected-project")
    try:
        wrapped = _wrap_agentic_runtime_block(tool, req, lambda _: None, "reply")
        asyncio.run(
            wrapped.execute(
                "tool-call", {"task": "test", "surface": "desktop"}, None, None
            )
        )
        assert [(kw.get("provider"), kw.get("model")) for kw in captured] == [
            ("selected", "chat-model")
        ]
        assert [kw.get("work_dir") for kw in captured] == ["/tmp/selected-project"]
    finally:
        reset_worktree(work_token)
        binding.release()
        _current_runtime.reset(outer_token)
