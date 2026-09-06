"""Native AgentTool integration with the actual isolated runner and SQLite."""
import asyncio
import base64
import sys
from types import SimpleNamespace

import pytest

from openprogram.agent.run_control import set_current_execution_id, reset_current_execution_id
from openprogram.agentic_programming.function import _call_id
from openprogram.execution.attempts import AttemptStore
from openprogram.execution.effects import EffectStore
from openprogram.execution.model import CapabilitySet, ExecutionStatus
from openprogram.execution.store import ExecutionStore

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


@pytest.fixture
def owned(tmp_path, monkeypatch):
    from openprogram.execution import control
    store = ExecutionStore(tmp_path / "execution.db")
    revision = store.create_revision(manifest={"entrypoint": "gui-tool-fixture"})
    execution = store.create_execution(execution_id="exec", run_id="run", session_id="session", revision_id=revision.revision_id, capabilities=CapabilitySet())
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease("exec", expected_version=execution.status_version, owner_id="worker", ttl_seconds=30, attempt_id="attempt")
    attempts.activate("attempt", generation=lease.generation, expected_execution_version=reserved.status_version)
    service = SimpleNamespace(executions=store, effects=EffectStore(store), attempts=attempts)
    monkeypatch.setattr(control, "default_control_service", lambda: service)
    execution_token = set_current_execution_id("exec")
    invocation_token = _call_id.set("gui-invocation")
    yield service
    _call_id.reset(invocation_token)
    reset_current_execution_id(execution_token)


def test_native_tool_preserves_interpreter_images_and_normal_agent_adapter(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.agentic_programming.runtime import _adapt_tools
    from openprogram.programs import ToolReturn
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=")
    with GuiAgentTools() as gui:
        handle = gui.broker.register(target="owned", methods={"capture": lambda *args: ToolReturn(text="capture", images=[png])}, validate=lambda *args: None)
        assert _adapt_tools([gui.tool]) == [gui.tool]
        async def use():
            first = await gui.tool.execute("call1", {"code": "x = 41"}, None, None)
            assert not first.is_error
            second = await gui.tool.execute("call2", {"code": f"print(x + 1)\nr = await ui.call({handle!r}, 'capture')\nprint(r['effect_id'])"}, None, None)
            assert not second.is_error
            lines = second.details["json"]["stdout"].splitlines()
            assert lines[0] == "42"
            assert owned.effects.get(lines[1]).metadata["invocation_id"] == "gui-invocation"
            assert base64.b64decode(second.content[1].data) == png
            error = await gui.tool.execute("call3", {"code": "raise ValueError('owned failure')"}, None, None)
            assert error.is_error and "owned failure" in error.details["json"]["error"]
        asyncio.run(use())
        process = gui.runner._process
    assert process.poll() is not None


def test_missing_context_is_rejected():
    from openprogram.backend.gui_agent import GuiAgentTools
    token = set_current_execution_id(None)
    try:
        with pytest.raises(PermissionError): GuiAgentTools()
    finally:
        reset_current_execution_id(token)


def test_changed_execution_cannot_reuse_tool(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    with GuiAgentTools() as gui:
        token = set_current_execution_id("other")
        try:
            result = asyncio.run(gui.tool.execute("call", {"code": "print('forbidden')"}, None, None))
            assert result.is_error and "execution" in result.content[0].text
        finally:
            reset_current_execution_id(token)


def test_cancelled_coroutine_reaps_interpreter(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    with GuiAgentTools() as gui:
        async def use():
            task = asyncio.create_task(gui.tool.execute("call", {"code": "while True: pass"}, None, None))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        asyncio.run(use())
        assert gui.runner._process.poll() is not None


def test_signal_cancellation_and_stale_execution_deny_even_pure_python(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    with GuiAgentTools() as gui:
        async def use():
            signal = asyncio.Event()
            task = asyncio.create_task(gui.tool.execute("call", {"code": "while True: pass"}, signal, None))
            await asyncio.sleep(0.05)
            signal.set()
            result = await asyncio.wait_for(task, 2)
            assert result.is_error and "cancelled" in result.content[0].text
        asyncio.run(use())
        assert gui.runner._process.poll() is not None
    with GuiAgentTools() as gui:
        execution = owned.executions.get_execution("exec")
        owned.executions.transition_execution("exec", expected_version=execution.status_version, target=ExecutionStatus.CANCELLING)
        result = asyncio.run(gui.tool.execute("call", {"code": "print('forbidden')"}, None, None))
        assert result.is_error and "not running" in result.content[0].text


def test_unknown_model_arguments_never_reach_runner(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    with GuiAgentTools() as gui:
        result = asyncio.run(gui.tool.execute("call", {"code": "x = 1", "execution_id": "other"}, None, None))
        assert result.is_error and "unsupported" in result.content[0].text
        result = asyncio.run(gui.tool.execute("call2", {"code": "print('x' in globals())"}, None, None))
        assert result.details["json"]["stdout"] == "False\n"


def test_standard_agent_session_calls_gui_tool_and_sees_image(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.agent.session import AgentSession
    from openprogram.providers.types import Model, AssistantMessage, ToolCall, TextContent, EventDone
    from openprogram.programs import ToolReturn
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=")
    with GuiAgentTools() as gui:
        handle = gui.broker.register(target="owned", methods={"capture": lambda *args: ToolReturn(text="capture", images=[png])}, validate=lambda *args: None)
        seen = []
        async def provider(model, context, options):
            seen.append(context)
            if len(seen) == 1:
                content = [ToolCall(id="gui-call", name="gui_exec", arguments={"code": f"await ui.call({handle!r}, 'capture')"})]
                reason = "toolUse"
            else:
                result = [m for m in context.messages if m.role == "toolResult"][-1]
                assert not result.is_error
                assert any(getattr(c, "type", "") == "image" and base64.b64decode(c.data) == png for c in result.content)
                content, reason = [TextContent(text="observed")], "stop"
            message = AssistantMessage(content=content, api=model.api, provider=model.provider, model=model.id, stop_reason=reason, timestamp=1)
            yield EventDone(reason=reason, message=message)
        async def use():
            with AgentSession(model=Model(id="fixture", name="fixture", api="openai-completions", provider="openai", base_url="https://example.invalid"), tools=[gui.tool], stream_fn=provider, max_iterations=3) as session:
                await session.run("capture owned fixture")
                assert session.last_assistant.content[0].text == "observed"
        asyncio.run(use())
        assert len(seen) == 2


def test_execution_revocation_during_script_does_not_return_success(owned):
    from openprogram.backend.gui_agent import GuiAgentTools
    with GuiAgentTools() as gui:
        async def use():
            task = asyncio.create_task(gui.tool.execute("call", {"code": "import time\ntime.sleep(0.15)\nprint('done')"}, None, None))
            await asyncio.sleep(0.05)
            execution = owned.executions.get_execution("exec")
            owned.executions.transition_execution("exec", expected_version=execution.status_version, target=ExecutionStatus.CANCELLING)
            result = await task
            assert result.is_error and "not running" in result.content[0].text
        asyncio.run(use())
