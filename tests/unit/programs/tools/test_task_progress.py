"""Task progress carried on a real tool call (todo/progress.py): added to the
tools array, taken out before the tool's own arguments are checked, applied
before the call runs and applied once. No LLM requests."""
from __future__ import annotations

import asyncio
import json
import time

from openprogram.models.providers.types import (
    AgentTool, AgentToolResult, AssistantMessage, TextContent, ToolCall,
)
from openprogram.models.providers.utils.streams.event_stream import EventStream
from openprogram.programs.tools.planning.todo import progress, shared
from openprogram.runtime.agent.loop import _execute_tool_calls

from .test_todo import FIVE, _branch, _steps, board, turn  # noqa: F401 - fixture

KEY = progress.KEY


def _tool(name, fields=None, seen=None):
    fields = {"command": {"type": "string"}} if fields is None else fields

    async def execute(call_id, args, cancel, on_update):
        if seen is not None:
            seen.append((args, _steps_now()))
        return AgentToolResult(content=[TextContent(text="ok")], details={})

    return AgentTool(name=name, label=name, description=name,
                     parameters={"type": "object", "properties": fields,
                                 "required": list(fields), "additionalProperties": False},
                     execute=execute)


def _steps_now():
    return [(t["id"], t["status"]) for t in shared.load("sess1")]


def _task_tools():
    return [_tool("task_start", {"title": {"type": "string"}}), _tool("task_step", {})]


def _dispatch(tools, *calls):
    message = AssistantMessage(
        content=[ToolCall(id=cid, name=name, arguments=args) for cid, name, args in calls],
        api="openai-codex-responses", provider="openai-codex", model="test",
        stop_reason="toolUse", timestamp=int(time.time() * 1000),
    )
    result = asyncio.run(_execute_tool_calls(tools, message, None, EventStream()))
    return result["tool_results"], None


def test_every_tool_but_the_task_tools_and_the_loader_takes_it():
    bash, mcp = _tool("bash"), _tool("mcp__srv__lookup", {"q": {"type": "string"}})
    own = _tool("odd", {KEY: {"type": "string"}})
    offered = [*_task_tools(), _tool("tool_search", {"select": {"type": "string"}}), bash, mcp, own]
    sent = {t.name: t for t in progress.annotate(offered, offered)}
    assert KEY in sent["bash"].parameters["properties"] and KEY in sent["mcp__srv__lookup"].parameters["properties"]
    assert sent["bash"].parameters["required"] == ["command"]
    for name in ("task_start", "task_step", "tool_search"):
        assert KEY not in sent[name].parameters["properties"]
    # A tool with a property of that name keeps its own, and the tool itself is never changed.
    assert sent["odd"].parameters["properties"][KEY] == {"type": "string"}
    assert KEY not in bash.parameters["properties"]
    # A turn without the task tools (a channel, a sub-agent without them) sends nothing new.
    assert progress.annotate([bash], [bash])[0] is bash


def test_the_argument_survives_strict_schemas():
    from openprogram.models.providers.schema import normalize_for
    tools = [*_task_tools(), _tool("bash")]
    schema = normalize_for("openai-codex", progress.annotate(tools, tools)[2].parameters, "gpt-6-luna")
    assert KEY in schema["required"] and schema["additionalProperties"] is False
    prop = schema["properties"][KEY]
    assert "null" in (prop.get("type") if isinstance(prop.get("type"), list) else [prop.get("type")]) \
        or any(o.get("type") == "null" for o in prop.get("anyOf", []))


def test_the_state_rides_on_real_calls_and_is_taken_out(board):
    seen = []
    tools = [*_task_tools(), _tool("bash", seen=seen), _tool("read", {"file_path": {"type": "string"}}, seen)]
    opening = {"task": "Fix it", "steps": FIVE[:3], "step": 1}
    with turn("m1_reply"):
        results, _ = _dispatch(tools, ("c1", "bash", {"command": "pytest", KEY: opening}))
        # The tool got its own arguments only, and the task was open before it ran.
        assert seen[0] == ({"command": "pytest"}, [("1", "in_progress"), ("2", "pending"), ("3", "pending")])
        assert results[0].content[0].text == "ok"
        # The same state again changes nothing.
        _dispatch(tools, ("c2", "read", {"file_path": "a.py", KEY: opening}))
        assert seen[1][1] == seen[0][1]
        _dispatch(tools, ("c3", "read", {"file_path": "a.py", KEY: {**opening, "step": 2, "result": "two bugs"}}))
        assert seen[2][1] == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]
    task = _branch(board)["tasks"][0]
    # The calls that moved it, by occurrence: c2 repeated the state and moved nothing.
    assert len(task["calls"]) == 2 and all(c.startswith("occ_") for c in task["calls"])
    assert task["advanced"] is True
    assert _branch(board)["todos"][0]["result"] == "two bugs"


def test_a_task_can_open_on_a_later_step(board):
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:3], "step": 2}}))
    assert _steps(board) == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]


def test_a_call_dispatched_again_is_applied_once(board):
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:3]}}))
        _dispatch(tools, ("c2", "bash", {"command": "b", KEY: {"step": 2}}))
        _dispatch(tools, ("c3", "bash", {"command": "b", KEY: {"step": 3}}))
        _dispatch(tools, ("c2", "bash", {"command": "b", KEY: {"step": 2}}))
    assert len(_branch(board)["tasks"]) == 1
    assert _steps(board) == [("1", "completed"), ("2", "completed"), ("3", "in_progress")]


def test_progress_that_cannot_apply_says_so_and_the_call_still_runs(board):
    seen = []
    tools = [*_task_tools(), _tool("bash", seen=seen)]
    with turn("m1_reply"):
        results, _ = _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"step": 2}}))
        assert seen and seen[0][0] == {"command": "a"}
        assert "ok" in results[0].content[0].text and "no task is open" in results[0].content[-1].text
        results, _ = _dispatch(tools, ("c2", "bash", {"command": "b", KEY: "next"}))
        assert not results[0].is_error and f"{KEY} ignored" in results[0].content[-1].text
        _dispatch(tools, ("c3", "bash", {"command": "b", KEY: {"task": "Fix it", "steps": FIVE[:2]}}))
        results, _ = _dispatch(tools, ("c4", "bash", {"command": "b", KEY: {"step": 4}}))
        assert "steps 1 to 2" in results[0].content[-1].text
        # An empty or null annotation is no progress and says nothing.
        results, _ = _dispatch(tools, ("c5", "bash", {"command": "c", KEY: None}))
        assert [c.text for c in results[0].content] == ["ok"]


def test_without_the_task_tools_the_argument_is_dropped_unapplied(board):
    seen = []
    with turn("m1_reply"):
        results, _ = _dispatch([_tool("bash", seen=seen)], ("c1", "bash", {"command": "a", KEY: {"task": "X"}}))
    assert seen[0][0] == {"command": "a"} and not results[0].is_error
    assert not board.exists() or not _branch(board)["tasks"]


def test_the_card_keeps_the_call_as_sent_and_carries_the_effect(board, monkeypatch):
    from openprogram.runtime.agent.loop import tool_dispatch
    pushed = []
    monkeypatch.setattr(EventStream, "push", lambda self, event: pushed.append(event))
    tools = [*_task_tools(), _tool("bash")]
    args = {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:2], "step": 1}}
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", args))
    start = next(e for e in pushed if isinstance(e, tool_dispatch.AgentEventToolStart))
    assert start.args == args
    assert start.task_effect == {"task": "T1", "step": "1"}
    from openprogram.runtime.conversation.event_parsing import agent_event_to_envelope
    from types import SimpleNamespace
    env = agent_event_to_envelope(start, SimpleNamespace(session_id="sess1", user_msg_id="u1"))
    assert env["data"]["event"]["task_effect"] == {"task": "T1", "step": "1"}
    assert json.loads(env["data"]["event"]["input"]) == args


def test_the_task_a_task_start_call_opened_is_the_same_task(board):
    from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
    from openprogram.programs.tools.planning.todo.task_step.task_step import _task_step_impl
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _task_start_impl("Fix it", "", FIVE[:3])
        _task_step_impl(next=True)
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "fix it", "steps": FIVE[:3], "step": 2}}))
    assert [t["status"] for t in _branch(board)["tasks"]] == ["open"]
    assert _steps(board) == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]


def test_a_step_past_the_last_keeps_the_last_going_and_the_end_closes_it(board):
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:2], "step": 1}}))
        _dispatch(tools, ("c2", "bash", {"command": "b", KEY: {"step": 2}}))
        results, _ = _dispatch(tools, ("c3", "bash", {"command": "c", KEY: {"step": 3, "result": "all green"}}))
        assert [c.text for c in results[0].content] == ["ok"]
        _dispatch(tools, ("c4", "bash", {"command": "c", KEY: {"step": 2, "result": "all green"}}))
        assert _steps(board) == [("1", "completed"), ("2", "in_progress")]
        assert _branch(board)["todos"][1]["result"] == "all green"
        shared.close_at_turn_end("sess1", "m1_reply")
    assert _steps(board) == [("1", "completed"), ("2", "completed")]
    assert _branch(board)["tasks"][0]["status"] == "done"


def test_a_new_task_finishes_the_last_step_of_the_one_it_replaces(board):
    from openprogram.programs.tools.planning.todo.task_start.task_start import _task_start_impl
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:2], "step": 1}}))
        _dispatch(tools, ("c2", "bash", {"command": "b", KEY: {"step": 2}}))
        # The same title again starts nothing.
        assert "already open" in _task_start_impl("fix it")
        _dispatch(tools, ("c3", "bash", {"command": "c", KEY: {"task": "Verify", "steps": FIVE[:2], "step": 1}}))
    first, second = _branch(board)["tasks"]
    assert first["status"] == "done" and second["status"] == "open"
    assert _steps(board)[:2] == [("1", "completed"), ("2", "completed")]


def test_a_call_id_the_provider_repeats_in_every_response_still_moves_the_task(board):
    # Gemini names a call ``call_<index>_<name>``, the same in every response.
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("call_0_bash", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:3], "step": 1}}))
        _dispatch(tools, ("call_0_bash", "bash", {"command": "b", KEY: {"step": 2}}))
        _dispatch(tools, ("call_0_bash", "bash", {"command": "c", KEY: {"step": 3}}))
    assert _steps(board) == [("1", "completed"), ("2", "completed"), ("3", "in_progress")]


def test_a_parallel_call_on_a_finished_step_moves_nothing_back(board):
    tools = [*_task_tools(), _tool("bash")]
    with turn("m1_reply"):
        _dispatch(tools, ("c1", "bash", {"command": "a", KEY: {"task": "Fix it", "steps": FIVE[:3], "step": 1}}))
        results, _ = _dispatch(tools, ("c2", "bash", {"command": "b", KEY: {"step": 2}}),
                               ("c3", "bash", {"command": "c", KEY: {"step": 1}}))
        assert [c.text for c in results[1].content] == ["ok"]
        assert _steps(board) == [("1", "completed"), ("2", "in_progress"), ("3", "pending")]
        task_step = __import__("openprogram.programs.tools.planning.todo.task_step.task_step",
                               fromlist=["_task_step_impl"])._task_step_impl
        task_step(drop=[3])
        _dispatch(tools, ("c4", "bash", {"command": "d", KEY: {"step": 3}}))
    # A dropped step is not taken up again either.
    assert _steps(board) == [("1", "completed"), ("2", "in_progress"), ("3", "dropped")]
