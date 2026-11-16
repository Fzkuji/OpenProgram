"""What a model asked to deliver a file is sent about ``send_file``.

Read from the body the Codex adapter builds for a real web turn with the
default Agent's tools: ``send_file`` is a function of the tools array, the
deferred list does not name it, the prompt says it needs no search, and a
search for it anyway is answered with words the model receives. On this
wire a search's result travels as a ``tool_search_output`` of definitions
only, so an answer left in the result text alone never reaches the model:
an empty list reads as "no such tool" and the reply says the file cannot
be sent.
"""
from __future__ import annotations

import pytest

from openprogram.models.providers.types import TextContent
from tests.support.request_bytes import call, install_bench, reply


@pytest.fixture
def bench(monkeypatch, tmp_path):
    from openprogram.runtime.conversation import turn_runtime as D
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    resolve = loop_runner._configure_browser_tool
    bench = install_bench(monkeypatch, tmp_path, "codex", [])
    # The Agent's own tools, as a chat in the App resolves them.
    monkeypatch.setattr(loop_runner, "_configure_browser_tool", resolve)
    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "you are helpful",
        "tools": {"mode": "automatic", "web_search": True}})
    return bench


def _names(body: dict) -> list[str]:
    return [tool.get("name") or tool["type"] for tool in body["tools"]]


def _texts(body: dict) -> list[str]:
    return [part["text"] for item in body["input"] if item.get("role") == "user"
            for part in item["content"] if part.get("type") == "input_text"]


def _deferred_names(body: dict) -> list[str]:
    (note,) = [text for text in _texts(body) if "<deferred-tools>" in text]
    listed = note.split("<deferred-tools>")[1].split("</deferred-tools>")[0]
    return [line[2:].split(":")[0] for line in listed.splitlines() if line.startswith("- ")]


def test_send_file_is_a_function_of_the_request_and_not_a_deferred_tool(bench):
    bench.turn("Write hello.txt and send it to the chat.",
               reply(bench.model, TextContent(text="done")), source="web")
    body = bench.requests[0].body

    (send_file,) = [tool for tool in body["tools"] if tool.get("name") == "send_file"]
    assert send_file["type"] == "function"
    # Strict mode lists every property; task_progress is the runtime's (todo/progress.py).
    assert send_file["parameters"]["required"] == ["path", "task_progress"]
    assert "tool_search" in _names(body)
    deferred = _deferred_names(body)
    assert deferred and "send_file" not in deferred
    # No tool is both a function of the array and named as needing a load.
    assert not set(deferred) & set(_names(body))


def test_the_request_says_which_tools_need_a_search(bench):
    bench.turn("Write hello.txt and send it to the chat.",
               reply(bench.model, TextContent(text="done")), source="web")
    body = bench.requests[0].body

    (note,) = [text for text in _texts(body) if "<deferred-tools>" in text]
    assert "Only these tools need loading" in note
    assert "Every tool already in your tools list is callable as it is; never search for one." in note
    # Not "before a tool's first call", which reads as every tool.
    assert "Before a tool's first call" not in note
    (loader,) = [tool for tool in body["tools"] if tool["type"] == "tool_search"]
    assert "A tool already in your tools list is callable as it is" in loader["description"]
    assert "names and JSON Schemas are not sent" not in loader["description"]


@pytest.mark.parametrize("source", ["web", "wechat"])
def test_the_prompt_says_to_call_send_file_with_no_search(bench, source):
    bench.turn("Write hello.txt and send it to the chat.",
               reply(bench.model, TextContent(text="done")), source=source)
    body = bench.requests[0].body
    prompt = body["instructions"]

    assert "send_file" in _names(body)
    (rule,) = [line for line in prompt.splitlines() if "call it for each deliverable" in line]
    assert "When `send_file` is in your tools list" in rule and "no search first" in rule
    assert "when you have it" not in prompt


def test_the_prompt_lets_a_real_refusal_be_reported(bench):
    """``send_file`` does refuse (a home file in a chat bound to a platform,
    a file over the limit) and a ``.txt`` named in a reply is not attached
    by itself, so the prompt cannot promise delivery or forbid saying a
    file was not sent: it limits that to a refusal the tool returned."""
    bench.turn("Write hello.txt and send it to the chat.",
               reply(bench.model, TextContent(text="done")), source="wechat")
    prompt = bench.requests[0].body["instructions"]

    (rule,) = [line for line in prompt.splitlines() if "was not sent" in line]
    assert "only when a `send_file` call refused it" in rule
    assert "tool search" in rule
    assert "Never say a file could not be sent" not in prompt
    assert "with or without a `send_file` call" not in prompt


def test_a_search_for_send_file_is_answered_with_call_it_now(bench):
    model = bench.model
    bench.turn("Write hello.txt and send it to the chat.",
               call(model, 1, "tool_search", select="select:send_file"),
               reply(model, TextContent(text="done")), source="web")
    first, second = (request.body for request in bench.requests)

    assert second["tools"] == first["tools"]
    assert "send_file" in _names(second)
    search, output, note = second["input"][len(first["input"]):]
    assert search["type"] == "tool_search_call"
    assert output["type"] == "tool_search_output" and output["tools"] == []
    text = note["content"][0]["text"]
    assert note["role"] == "user"
    assert "`send_file` is already callable: it is in your tools list with its full schema." in text
    assert "Call it now" in text
    assert "available" not in text and "not found" not in text


def test_the_default_tool_set_carries_the_loader_of_its_deferred_tools():
    from openprogram.programs import DEFAULT_TOOLS, DEFERRED_DEFAULT_TOOLS, agent_tools

    assert DEFERRED_DEFAULT_TOOLS <= set(DEFAULT_TOOLS)
    assert "tool_search" in DEFAULT_TOOLS
    assert "tool_search" in {tool.name for tool in agent_tools(source="web")}


def test_a_turn_without_the_loader_defers_nothing(bench, monkeypatch):
    """With ``tool_search`` disabled nothing could load a deferred tool:
    each goes out with its schema instead of being named or dropped."""
    from openprogram.runtime.conversation import turn_runtime as D

    monkeypatch.setattr(D, "_load_agent_profile", lambda agent_id: {
        "id": agent_id, "system_prompt": "you are helpful",
        "tools": {"mode": "automatic", "disabled": ["tool_search"]}})
    bench.turn("Write hello.txt and send it to the chat.",
               reply(bench.model, TextContent(text="done")), source="web")
    body = bench.requests[0].body

    assert "tool_search" not in _names(body) and "send_file" in _names(body)
    assert not [text for text in _texts(body) if "<deferred-tools>" in text]
    from openprogram.programs import DEFERRED_DEFAULT_TOOLS
    assert {"scheduler", "skill"} <= DEFERRED_DEFAULT_TOOLS
    assert {"scheduler", "skill"} <= set(_names(body))
