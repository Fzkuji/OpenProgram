"""DagRuntime — exec() builds messages from reads, records ModelCall to graph."""

from __future__ import annotations

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


# ── Mock provider ────────────────────────────────────────────────────


class MockProvider:
    """Capture every call's args; reply with canned outputs."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, *, messages, model, system, tools, **kwargs):
        self.calls.append({
            "messages": messages,
            "model": model,
            "system": system,
            "tools": tools,
            **kwargs,
        })
        if not self._replies:
            return "(no more replies)"
        return self._replies.pop(0)


# ── exec records to graph ────────────────────────────────────────────


def test_exec_records_model_call_in_graph():
    provider = MockProvider(["hello back"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u = rt.add_user_message("hi")

    reply = rt.exec(
        content=[{"type": "text", "text": "say hi back"}],
        reads=[u.id],
    )
    assert reply == "hello back"
    assert len(rt.graph) == 2
    m = [n for n in rt.graph if n.is_llm()][0]
    assert m.reads == [u.id]
    assert m.output == "hello back"
    assert m.model == "claude-opus"


def test_exec_messages_built_from_reads_then_content():
    provider = MockProvider(["ok"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u = rt.add_user_message("first message")
    rt.exec(
        content=[{"type": "text", "text": "follow up"}],
        reads=[u.id],
    )
    assembled = provider.calls[0]["messages"]
    # Expect: [user "first message"] + [user "follow up"]
    assert assembled == [
        {"role": "user", "content": "first message"},
        {"role": "user", "content": "follow up"},
    ]


def test_exec_assembles_mixed_history():
    provider = MockProvider(["assistant 1", "assistant 2"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u1 = rt.add_user_message("first")
    rt.exec(
        content=[{"type": "text", "text": "what do you think?"}],
        reads=[u1.id],
    )
    model_1_id = rt.last_node_id()  # capture before adding more nodes
    u2 = rt.add_user_message("follow up")
    rt.exec(
        content=[{"type": "text", "text": "now your turn"}],
        reads=[u1.id, model_1_id, u2.id],
    )
    # Second call should have the assistant turn embedded
    msgs = provider.calls[1]["messages"]
    roles = [m["role"] for m in msgs]
    # u1 + first model + u2 + per-call content
    assert roles == ["user", "assistant", "user", "user"]
    assert msgs[1]["content"] == "assistant 1"


def test_exec_rejects_unknown_read_id():
    provider = MockProvider(["ignored"])
    rt = DagRuntime(provider, default_model="claude-opus")
    rt.add_user_message("hi")
    with pytest.raises(ValueError, match="unknown node ids"):
        rt.exec(
            content=[{"type": "text", "text": "x"}],
            reads=["does-not-exist"],
        )


def test_exec_requires_model():
    provider = MockProvider(["x"])
    rt = DagRuntime(provider)  # no default_model
    rt.add_user_message("hi")
    with pytest.raises(ValueError, match="model"):
        rt.exec(content=[], reads=[])


def test_record_function_call_adds_node():
    provider = MockProvider(["I want to call ping"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u = rt.add_user_message("ping 127.0.0.1")
    m = rt.exec(
        content=[{"type": "text", "text": ""}],
        reads=[u.id],
    )
    last_model = [n for n in rt.graph if n.is_llm()][-1]
    fc = rt.record_function_call(
        function_name="ping",
        arguments={"host": "127.0.0.1"},
        called_by=last_model.id,
        result="alive",
    )
    assert fc.is_code()
    assert fc.called_by == last_model.id
    assert fc.result == "alive"
    assert fc.called_by == last_model.id  # auto-linked exec edge


def test_system_prompt_forwarded_and_recorded():
    provider = MockProvider(["fine"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u = rt.add_user_message("hi")
    rt.exec(
        content=[{"type": "text", "text": "respond"}],
        reads=[u.id],
        system="be terse",
    )
    assert provider.calls[0]["system"] == "be terse"
    m = [n for n in rt.graph if n.is_llm()][0]
    assert m.system_prompt == "be terse"


def test_tools_forwarded():
    provider = MockProvider(["sure"])
    rt = DagRuntime(provider, default_model="claude-opus")
    u = rt.add_user_message("hi")
    rt.exec(
        content=[{"type": "text", "text": "."}],
        reads=[u.id],
        tools=[{"name": "search", "parameters": {}}],
    )
    assert provider.calls[0]["tools"] == [{"name": "search", "parameters": {}}]


# ── store integration ────────────────────────────────────────────────


def test_store_persists_each_append(tmp_path):
    db = tmp_path / "chat.sqlite"
    init_db(db)
    store = GraphStore(db, "test-session")
    store.create_session_row()
    provider = MockProvider(["world"])
    rt = DagRuntime(provider, default_model="claude-opus", store=store)
    rt.add_user_message("hello")
    rt.exec(content=[{"type": "text", "text": ""}], reads=[rt.last_node_id()])
    # Reload from DB and check
    g2 = store.load()
    assert len(g2) == 2


# ── default model and overrides ──────────────────────────────────────


def test_per_call_model_overrides_default():
    provider = MockProvider(["x"])
    rt = DagRuntime(provider, default_model="claude-opus")
    rt.add_user_message("hi")
    rt.exec(
        content=[{"type": "text", "text": "."}],
        reads=[rt.last_node_id()],
        model="gpt-4.1",
    )
    assert provider.calls[0]["model"] == "gpt-4.1"
    last_mc = [n for n in rt.graph if n.is_llm()][-1]
    assert last_mc.model == "gpt-4.1"


# ── multi-turn rebuilds reads correctly ──────────────────────────────


def test_multi_turn_with_fold_history():
    """Confirm DagRuntime composes nicely with fold_history helper."""
    from openprogram.context.nodes import fold_history

    provider = MockProvider(["first reply", "second reply"])
    rt = DagRuntime(provider, default_model="claude-opus")

    # Turn 1
    u1 = rt.add_user_message("turn1 question")
    rt.exec(
        content=[{"type": "text", "text": ""}],
        reads=fold_history(u1.id, rt.graph),
    )

    # Turn 2: fold prior turn (u1 + final reply) + current user input
    u2 = rt.add_user_message("turn2 question")
    rt.exec(
        content=[{"type": "text", "text": ""}],
        reads=fold_history(u2.id, rt.graph),
    )

    # Inspect the second call's prompt — it should include u1, first
    # reply, u2 (current turn opener), and the per-call empty content.
    msgs = provider.calls[1]["messages"]
    roles = [m["role"] for m in msgs]
    # u1, assistant1, u2 → 3 messages (per-call content is empty so
    # nothing appended)
    assert roles == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "turn1 question"
    assert msgs[1]["content"] == "first reply"
    assert msgs[2]["content"] == "turn2 question"
