"""Steer/stop retain the current turn through the public chat/control entries."""
import asyncio
import threading

import pytest

from tests.component.agent.execution.test_agent_continuation_real import (
    _chat,
    _command,
    _wait,
    _WebSocket,
)
from tests.component.agent.execution import test_agent_continuation_real as _test_agent_continuation_real
from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText, ScriptedThinking, ScriptedToolCall

# Shared pytest fixtures, bound at module level so pytest collects them here.
real_agent_chat = _test_agent_continuation_real.real_agent_chat


def _steer(h, execution, command_id, message):
    from openprogram.interfaces.server.ws_actions import runtime
    ws = _WebSocket()
    asyncio.run(runtime.ACTIONS["execution.steer"](ws, {
        "type": "execution.command", "action": "execution.steer",
        "command_id": command_id, "execution_id": execution.execution_id,
        "expected_version": execution.status_version, "payload": {"message": message},
    }))
    assert h.store.get_command(command_id).status.value in {"accepted", "applying", "applied"}


@pytest.mark.parametrize("phase", ["text", "thinking", "start"])
def test_steer_interrupts_silent_text_stream_and_continues_once(real_agent_chat, monkeypatch, phase):
    h = real_agent_chat
    provider = ScriptedProvider()
    provider.add_response(ScriptedThinking("partial thought") if phase == "thinking" else ScriptedText("partial answer"))
    provider.add_response(ScriptedText("answer with correction"))
    entered, closed = threading.Event(), threading.Event()
    contexts = []

    async def streaming(model, context, options=None):
        index = len(contexts)
        contexts.append(context.model_copy(deep=True))
        try:
            async for event in provider.stream_simple(model, context, options):
                yield event
                if index == 0 and event.type == {"text": "text_delta", "thinking": "thinking_delta", "start": "start"}[phase]:
                    entered.set()
                    while not h.provider.release.is_set():
                        await asyncio.sleep(0)
        finally:
            if index == 0:
                closed.set()

    monkeypatch.setattr(h.provider, "stream_simple", streaming)
    execution = _chat(h)
    try:
        _wait(entered.is_set)
        _steer(h, h.store.get_execution(execution.execution_id), "steer-text", "use the correction")
        _wait(lambda: len(contexts) == 2, timeout=3)
        assert closed.is_set()
        assert not h.provider.release.is_set()
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "completed")
        assert h.store.get_command("steer-text").status.value == "applied"
        serialized = str([message.model_dump() for message in contexts[1].messages])
        if phase != "start":
            assert ("partial thought" if phase == "thinking" else "partial answer") in serialized
        assert serialized.count("use the correction") == 1
        if phase == "thinking":
            from openprogram.models.providers.shared.transform_messages import transform_messages
            from openprogram.models.providers.types import ThinkingContent
            projected = transform_messages(contexts[1].messages, h.model)
            assert not any(isinstance(block, ThinkingContent)
                           for msg in projected if msg.role == "assistant" for block in msg.content)
            assert "partial thought" in serialized  # Original display/checkpoint remains intact.
        from openprogram.state.store.session.session_store import SessionStore
        cold = SessionStore(h.sessions.root_path)
        branch = cold.get_branch(h.session_id)
        assert sum("use the correction" in str(m.get("content")) for m in branch) == 1
        if phase == "text":
            assert "partial answer" in str(branch)
        assert "answer with correction" in str(branch)
    finally:
        h.provider.release.set()


def test_stop_with_pending_steer_keeps_reply_in_cold_history(real_agent_chat):
    h = real_agent_chat
    h.provider.add_response(ScriptedText("already generated"), ScriptedToolCall("first", {}, "first-call"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    try:
        _wait(lambda: "first" in h.tools.calls)
        source = h.store.get_execution_input(execution.execution_id)
        _steer(h, h.store.get_execution(execution.execution_id), "pending-steer", "not yet applied")
        # Interrupted owners can leave HEAD on the admitted user node, as in
        # the reported session. Terminal projection must finish this exact turn.
        h.sessions.set_head(h.session_id, source.user_message_id)
        _command(h, "execution.cancel", h.store.get_execution(execution.execution_id), "stop-pending")
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "cancelled")
        _wait(lambda: h.sessions.get_session(h.session_id)["status"] == "idle")
        from openprogram.state.store.session.session_store import SessionStore
        cold = SessionStore(h.sessions.root_path)
        branch = cold.get_branch(h.session_id)
        assert branch[-1]["id"] == source.assistant_message_id
        assert "already generated" in str(branch)
        assert h.store.get_command("pending-steer").rejection_code == "superseded_by_cancel"
        # A subsequent explicit send must link to the retained reply.
        conv = {"id": h.session_id, "head_id": source.user_message_id, "messages": []}
        h.server._append_msg(conv, {"id": "next-user", "role": "user", "content": "continue"})
        branch = SessionStore(h.sessions.root_path).get_branch(h.session_id)
        assert [m["id"] for m in branch][-2:] == [source.assistant_message_id, "next-user"]
        from openprogram.runtime.execution.event_effects import ExecutionEventEffects
        ExecutionEventEffects(h.store).project_cancelled_assistant(
            h.store.get_execution(execution.execution_id),
        )
        assert SessionStore(h.sessions.root_path).get_session(h.session_id)["head_id"] == "next-user"
    finally:
        h.provider.release.set()
        h.tools.release["first"].set()


def test_multiple_steers_replayed_during_tool_batch_keep_order_and_tools(real_agent_chat, monkeypatch):
    h = real_agent_chat
    h.provider.add_response(
        ScriptedText("before tools"),
        ScriptedToolCall("first", {}, "first-call"),
        ScriptedToolCall("second", {}, "second-call"),
    )
    h.provider.add_response(ScriptedText("after both instructions"))
    h.tools.blocked.add("first")
    contexts = []
    original = h.provider.stream_simple

    async def capture(model, context, options=None):
        contexts.append(context.model_copy(deep=True))
        async for event in original(model, context, options):
            yield event

    monkeypatch.setattr(h.provider, "stream_simple", capture)
    execution = _chat(h)
    try:
        _wait(lambda: "first" in h.tools.calls)
        snapshot = h.store.get_execution(execution.execution_id)
        _steer(h, snapshot, "batch-first", "FIRST CORRECTION")
        _steer(h, snapshot, "batch-first", "FIRST CORRECTION")  # Lost-ACK replay.
        _steer(h, snapshot, "batch-second", "SECOND CORRECTION")
        assert len(contexts) == 1
        assert h.store.get_command("batch-first").status.value == "accepted"
        h.tools.release["first"].set()
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "completed")
        assert h.tools.calls == ["first", "second"]
        assert len(contexts) == 2
        messages = str([message.model_dump() for message in contexts[1].messages])
        assert messages.count("FIRST CORRECTION") == 1
        assert messages.count("SECOND CORRECTION") == 1
        assert messages.index("FIRST CORRECTION") < messages.index("SECOND CORRECTION")
        assert "first:ok" in messages and "second:ok" in messages
        from openprogram.state.store.session.session_store import SessionStore
        branch = SessionStore(h.sessions.root_path).get_branch(h.session_id)
        assert sum("FIRST CORRECTION" in str(msg.get("content")) for msg in branch) == 1
        assert sum("SECOND CORRECTION" in str(msg.get("content")) for msg in branch) == 1
        assert "after both instructions" in str(branch)
        assert all(h.store.get_command(cid).status.value == "applied"
                   for cid in ("batch-first", "batch-second"))
    finally:
        h.tools.release["first"].set()
        h.provider.release.set()


def test_steer_interrupts_a_tool_call_while_its_arguments_stream(real_agent_chat, monkeypatch):
    """Instant interrupt (Codex parity): a call that is still being written
    was never dispatched, so steering drops it instead of waiting."""
    h = real_agent_chat
    provider = ScriptedProvider()
    provider.add_response(ScriptedText("let me check"), ScriptedToolCall("first", {"x": 1}, "half-call"))
    provider.add_response(ScriptedText("answer with correction"))
    entered = threading.Event()
    contexts = []

    async def streaming(model, context, options=None):
        index = len(contexts)
        contexts.append(context.model_copy(deep=True))
        async for event in provider.stream_simple(model, context, options):
            yield event
            if index == 0 and event.type == "toolcall_delta":
                entered.set()
                while not h.provider.release.is_set():
                    await asyncio.sleep(0)

    monkeypatch.setattr(h.provider, "stream_simple", streaming)
    execution = _chat(h)
    try:
        _wait(entered.is_set)
        _steer(h, h.store.get_execution(execution.execution_id), "steer-tool", "use the correction")
        _wait(lambda: len(contexts) == 2, timeout=3)
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "completed")
        assert "first" not in h.tools.calls
        assert h.store.get_command("steer-tool").status.value == "applied"
        serialized = str([message.model_dump() for message in contexts[1].messages])
        assert "let me check" in serialized
        assert "half-call" not in serialized
        assert serialized.count("use the correction") == 1
    finally:
        h.provider.release.set()


def test_instant_steer_setting_off_waits_for_the_response(real_agent_chat, monkeypatch):
    h = real_agent_chat
    import sys
    import openprogram.runtime.agent.loop  # noqa: F401  (the package re-exports a same-named function)
    monkeypatch.setattr(sys.modules["openprogram.runtime.agent.loop.streaming"], "_instant_steer_enabled", lambda: False)
    provider = ScriptedProvider()
    provider.add_response(ScriptedText("full answer"))
    provider.add_response(ScriptedText("answer with correction"))
    entered = threading.Event()
    contexts = []

    async def streaming(model, context, options=None):
        index = len(contexts)
        contexts.append(context.model_copy(deep=True))
        async for event in provider.stream_simple(model, context, options):
            yield event
            if index == 0 and event.type == "text_delta":
                entered.set()
                while not h.provider.release.is_set():
                    await asyncio.sleep(0)

    monkeypatch.setattr(h.provider, "stream_simple", streaming)
    execution = _chat(h)
    try:
        _wait(entered.is_set)
        _steer(h, h.store.get_execution(execution.execution_id), "steer-late", "use the correction")
        import time
        time.sleep(0.4)
        assert len(contexts) == 1  # Not interrupted: still waiting on the first response.
        h.provider.release.set()
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "completed")
        assert h.store.get_command("steer-late").status.value == "applied"
    finally:
        h.provider.release.set()
