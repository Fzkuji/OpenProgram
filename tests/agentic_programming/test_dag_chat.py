"""End-to-end chat loop: chat_turn drives multi-step LLM + tool exchanges
into a persisted DAG."""

from __future__ import annotations

import json
import shutil

import pytest

from openprogram.context.nodes import (
    Graph,
    UserMessage,
    ModelCall,
    FunctionCall,
)
from openprogram.context.storage import GraphStore, init_db
from openprogram.context.runtime import DagRuntime
from openprogram.context.chat import chat_turn, parse_tool_call


# ── Mock provider that can return tool-call requests ─────────────────


class ScriptedProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, *, messages, model, system, tools, **kwargs):
        self.calls.append({"messages": messages, "model": model, "system": system, "tools": tools})
        if not self.replies:
            return "(done)"
        return self.replies.pop(0)


# ── parse_tool_call ──────────────────────────────────────────────────


def test_parse_tool_call_bare_json():
    text = '{"call": "search", "args": {"q": "hi"}}'
    assert parse_tool_call(text) == ("search", {"q": "hi"})


def test_parse_tool_call_fenced():
    text = 'Sure, here:\n```json\n{"call":"ping","args":{"host":"x"}}\n```'
    assert parse_tool_call(text) == ("ping", {"host": "x"})


def test_parse_tool_call_no_match():
    assert parse_tool_call("just a normal reply") is None


def test_parse_tool_call_empty():
    assert parse_tool_call("") is None
    assert parse_tool_call(None) is None  # type: ignore[arg-type]


# ── Single-turn, no tool ─────────────────────────────────────────────


def test_chat_turn_plain_text_reply():
    provider = ScriptedProvider(["hello back"])
    rt = DagRuntime(provider, default_model="claude-opus")
    reply = chat_turn("hi", runtime=rt)

    assert reply == "hello back"
    # Graph has: 1 user + 1 model = 2 nodes
    types = [n.role for n in rt.graph]
    assert types == ["user", "llm"]


# ── Single tool use → final reply ────────────────────────────────────


def test_chat_turn_with_one_tool_use():
    # Round 1: model asks for tool
    # Round 2: model gives final reply
    provider = ScriptedProvider([
        json.dumps({"call": "echo", "args": {"text": "hello"}}),
        "tool said: hello",
    ])
    tools = {"echo": lambda text: text}
    rt = DagRuntime(provider, default_model="claude-opus")

    reply = chat_turn("ping", runtime=rt, tools=tools)
    assert reply == "tool said: hello"

    types = [n.role for n in rt.graph]
    # user + model(tool_use) + function_call + model(final) = 4 nodes
    assert types == ["user", "llm", "code", "llm"]

    fc = [n for n in rt.graph if n.is_code()][0]
    assert fc.function_name == "echo"
    assert fc.arguments == {"text": "hello"}
    assert fc.result == "hello"

    final = [n for n in rt.graph if n.is_llm()][-1]
    assert final.output == "tool said: hello"


# ── Multiple tool uses in one turn ───────────────────────────────────


def test_chat_turn_multiple_tool_uses():
    provider = ScriptedProvider([
        json.dumps({"call": "add", "args": {"a": 2, "b": 3}}),
        json.dumps({"call": "add", "args": {"a": 5, "b": 1}}),
        "final answer: 6",
    ])
    tools = {"add": lambda a, b: a + b}
    rt = DagRuntime(provider, default_model="claude-opus")

    reply = chat_turn("math please", runtime=rt, tools=tools)
    assert reply == "final answer: 6"

    fc_nodes = [n for n in rt.graph if n.is_code()]
    assert len(fc_nodes) == 2
    assert fc_nodes[0].result == 5
    assert fc_nodes[1].result == 6


# ── Unknown tool → treat as final reply ──────────────────────────────


def test_chat_turn_unknown_tool_treated_as_final():
    provider = ScriptedProvider([
        json.dumps({"call": "nonexistent", "args": {}}),
    ])
    rt = DagRuntime(provider, default_model="claude-opus")
    reply = chat_turn("hi", runtime=rt, tools={"echo": lambda **k: k})
    # Tool not in registry → return the reply as-is
    assert reply == json.dumps({"call": "nonexistent", "args": {}})
    types = [n.role for n in rt.graph]
    assert types == ["user", "llm"]


# ── Tool raises → error logged into FunctionCall.result, loop continues ─


def test_chat_turn_tool_error_recorded():
    def bad(**kwargs):
        raise RuntimeError("oops")
    provider = ScriptedProvider([
        json.dumps({"call": "bad", "args": {}}),
        "recovered",
    ])
    rt = DagRuntime(provider, default_model="claude-opus")
    reply = chat_turn("try", runtime=rt, tools={"bad": bad})
    assert reply == "recovered"

    fc = [n for n in rt.graph if n.is_code()][0]
    assert isinstance(fc.result, dict)
    assert "oops" in fc.result["error"]


# ── max_iterations cap ───────────────────────────────────────────────


def test_chat_turn_iteration_cap():
    # Infinite tool-use loop
    provider = ScriptedProvider([json.dumps({"call": "x", "args": {}})] * 20)
    rt = DagRuntime(provider, default_model="claude-opus")
    chat_turn("loop", runtime=rt, tools={"x": lambda: "y"}, max_iterations=3)

    # Should have stopped after 3 LLM calls
    model_calls = [n for n in rt.graph if n.is_llm()]
    assert len(model_calls) == 3
    fc_calls = [n for n in rt.graph if n.is_code()]
    assert len(fc_calls) == 3


# ── Multi-turn: prior turns are folded in subsequent turns ───────────


def test_two_turns_with_folded_history():
    provider = ScriptedProvider(["reply 1", "reply 2"])
    rt = DagRuntime(provider, default_model="claude-opus")

    chat_turn("question 1", runtime=rt)
    chat_turn("question 2", runtime=rt)

    # The second LLM call should see prior turn folded — at minimum
    # the prior user message and prior model reply.
    second_call_messages = provider.calls[1]["messages"]
    contents = [m["content"] for m in second_call_messages]
    assert "question 1" in contents
    assert "reply 1" in contents
    assert "question 2" in contents


# ── Store integration: every node persisted ──────────────────────────


def test_chat_turn_persists_through_store(tmp_path):
    db = tmp_path / "chat.sqlite"
    init_db(db)
    store = GraphStore(db, "test-session")
    store.create_session_row()

    provider = ScriptedProvider([
        json.dumps({"call": "echo", "args": {"text": "hi"}}),
        "done",
    ])
    rt = DagRuntime(provider, default_model="claude-opus", store=store)
    chat_turn("ping", runtime=rt, tools={"echo": lambda text: text})

    # Reload and check structure preserved
    g2 = store.load()
    assert [n.role for n in g2] == ["user", "llm", "code", "llm"]


# ── system prompt forwarded ──────────────────────────────────────────


def test_system_prompt_forwarded_each_iteration():
    provider = ScriptedProvider([
        json.dumps({"call": "f", "args": {}}),
        "done",
    ])
    rt = DagRuntime(provider, default_model="claude-opus")
    chat_turn("hi", runtime=rt, tools={"f": lambda: "ok"}, system="be terse")

    for call in provider.calls:
        assert call["system"] == "be terse"
