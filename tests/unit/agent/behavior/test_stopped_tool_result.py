"""What a call that was running when its turn stopped leaves for the model.

Every call gets a result (Codex ``aborted by user after {secs}s``), with the
output it had produced first (OpenCode), and no conversation id.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from openprogram.host.cancellation import CancelledError
from openprogram.models.providers.types import AgentTool, AgentToolResult, AssistantMessage, TextContent, ToolCall
from openprogram.models.providers.utils.errors import ExecInterrupt
from openprogram.runtime.agent.loop import _execute_tool_calls


class _Events:
    def __init__(self) -> None:
        self.pushed: list = []

    def push(self, event) -> None:
        self.pushed.append(event)


def _ended_result(stop: BaseException) -> str:
    async def execute(call_id, args, cancel, on_update):
        on_update(AgentToolResult(content=[TextContent(text="building…\n")], details={}))
        raise stop

    tool = AgentTool(name="build", description="", parameters={"type": "object", "properties": {}},
                     label="build", execute=execute)
    message = AssistantMessage(content=[ToolCall(id="c1", name="build", arguments={})],
                               api="openai-completions", provider="openai", model="fake",
                               stop_reason="toolUse", timestamp=int(time.time() * 1000))
    events = _Events()
    with pytest.raises(type(stop)):
        asyncio.run(_execute_tool_calls([tool], message, None, events))
    ended = [e for e in events.pushed if getattr(e, "type", None) == "tool_execution_end"]
    assert ended and ended[-1].outcome == "cancelled"
    return ended[-1].result.content[0].text


def test_a_stopped_call_keeps_its_output_and_says_how_long_it_ran() -> None:
    text = _ended_result(CancelledError("Execution stopped (cancel.user)"))

    assert text.startswith("building…\n\nStopped by the user after ")
    assert text.endswith("s.")
    assert "conv=" not in text


def test_a_call_cut_by_a_worker_restart_says_so() -> None:
    text = _ended_result(ExecInterrupt("worker_stopping"))

    assert "Interrupted after " in text and "because the service restarted." in text


def test_the_stop_reason_names_what_stopped_the_call() -> None:
    from openprogram.runtime.execution.run_control.stop_reasons import NOT_RUN_TEXT, stopped_tool_text

    assert stopped_tool_text("cancel.parent", 3.24) == (
        "Stopped because the task that started it was stopped after 3.2s.")
    assert stopped_tool_text("time_limit", 1) == "Stopped by its time limit after 1.0s."
    assert stopped_tool_text("error.worker_lost", 2) == "Interrupted after 2.0s because the service restarted."
    assert NOT_RUN_TEXT == "Not run: the turn was stopped before this call started."
