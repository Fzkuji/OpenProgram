"""Stop must interrupt silent provider/tool awaits through the public command."""
import pytest
from tests.component.agent.execution.test_agent_continuation_real import _chat, _command, _wait
from tests.component.agent.execution import test_agent_continuation_real as _test_agent_continuation_real
from tests.component.providers.scripted_provider import ScriptedText, ScriptedToolCall

# Shared pytest fixtures, bound at module level so pytest collects them here.
real_agent_chat = _test_agent_continuation_real.real_agent_chat

@pytest.mark.parametrize("blocked", ["provider", "tool"])
def test_stop_interrupts_operation_without_waiting_for_another_event(real_agent_chat, blocked):
    h = real_agent_chat
    if blocked == "provider":
        h.provider.add_response(ScriptedText("must not finish"))
        h.provider.block_calls.add(0)
    else:
        h.provider.add_response(ScriptedToolCall("first", {}, "first-call"))
        h.tools.blocked.add("first")
    execution = _chat(h)
    try:
        _wait(h.provider.entered.is_set if blocked == "provider" else lambda: "first" in h.tools.calls)
        current = h.store.get_execution(execution.execution_id)
        result = _command(h, "execution.cancel", current, "cancel-silent-operation")
        assert result["status"] in {"accepted", "applying", "applied"}
        _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "cancelled", timeout=2, detail=lambda: {"execution": h.store.get_execution(execution.execution_id).to_dict(), "outcomes": str(h.outcomes), "errors": str(h.activation_errors)})
        assert not h.provider.release.is_set()
        assert h.provider.call_count <= 1
    finally:
        h.provider.release.set()
        for event in h.tools.release.values():
            event.set()


def test_stop_does_not_wait_for_provider_cleanup(real_agent_chat, monkeypatch):
    import asyncio
    from openprogram.models.providers.utils.event_stream import EventStream
    h = real_agent_chat
    cleaned = []
    def stream_simple(model, context, options=None):
        stream = EventStream()
        async def produce():
            h.provider.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                try:
                    await asyncio.sleep(60)
                finally:
                    cleaned.append(True)
        stream.attach_producer(asyncio.create_task(produce()))
        return stream
    monkeypatch.setattr(h.provider, "stream_simple", stream_simple)
    execution = _chat(h)
    _wait(h.provider.entered.is_set)
    current = h.store.get_execution(execution.execution_id)
    _command(h, "execution.cancel", current, "cancel-cleanup")
    _wait(lambda: h.store.get_execution(execution.execution_id).status.value == "cancelled", timeout=2)
    _wait(lambda: bool(cleaned), timeout=2)
