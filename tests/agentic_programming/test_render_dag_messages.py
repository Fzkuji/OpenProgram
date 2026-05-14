"""context.render.render_dag_messages — DAG nodes → provider messages.

Pure function. Tests use small hand-built graphs and assert the
resulting message list role/content shape.
"""

from __future__ import annotations

import pytest

from openprogram.context.nodes import (
    Call,
    Graph,
    ROLE_USER,
    ROLE_LLM,
    ROLE_CODE,
)
from openprogram.context.render import render_dag_messages


def _texts(messages) -> list[str]:
    """Helper: extract concatenated text content of each message."""
    out: list[str] = []
    for m in messages:
        chunks = [c.text for c in m.content if hasattr(c, "text")]
        out.append("".join(chunks))
    return out


def _roles(messages) -> list[str]:
    return [m.role for m in messages]


# ── Empty / unknown ────────────────────────────────────────────────


def test_empty_reads_returns_empty_list():
    g = Graph()
    assert render_dag_messages(g, []) == []


def test_unknown_ids_are_silently_skipped():
    g = Graph()
    assert render_dag_messages(g, ["nonexistent"]) == []


# ── Single-role renderings ─────────────────────────────────────────


def test_user_renders_to_user_message():
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="hello"))
    msgs = render_dag_messages(g, [u.id])
    assert _roles(msgs) == ["user"]
    assert _texts(msgs) == ["hello"]


def test_llm_renders_to_assistant_message():
    g = Graph()
    m = g.add(Call(role=ROLE_LLM, output="hi back"))
    msgs = render_dag_messages(g, [m.id])
    assert _roles(msgs) == ["assistant"]
    assert _texts(msgs) == ["hi back"]


def test_user_then_llm_two_messages():
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="q"))
    m = g.add(Call(role=ROLE_LLM, output="a"))
    msgs = render_dag_messages(g, [u.id, m.id])
    assert _roles(msgs) == ["user", "assistant"]
    assert _texts(msgs) == ["q", "a"]


# ── code Call renders as call/result pair ──────────────────────────


def test_code_call_renders_as_user_assistant_pair():
    """A completed function call becomes a synthetic user→assistant
    exchange so the model sees "called X with Y, got Z"."""
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="search",
        input={"query": "DAG"},
        output={"hits": 3},
    ))
    msgs = render_dag_messages(g, [c.id])
    assert _roles(msgs) == ["user", "assistant"]
    user_text, assistant_text = _texts(msgs)
    assert "search" in user_text
    assert "DAG" in user_text                # arg shows up
    assert "hits" in assistant_text          # result shows up


def test_code_call_running_skips_assistant():
    """When the function hasn't returned yet (output=None, status=running),
    only the call signature is rendered; no fake assistant message."""
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="slow",
        input={"x": 1},
        output=None,
        metadata={"status": "running"},
    ))
    msgs = render_dag_messages(g, [c.id])
    assert _roles(msgs) == ["user"]          # only the call sig


def test_hidden_code_call_skipped_even_if_in_reads():
    """expose='hidden' should never produce messages, even if a buggy
    caller hands the id to the renderer."""
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="secret",
        input={},
        output="x",
        metadata={"expose": "hidden"},
    ))
    assert render_dag_messages(g, [c.id]) == []


def test_code_call_error_result_shown_with_prefix():
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="bad",
        input={},
        output={"error": "boom"},
    ))
    msgs = render_dag_messages(g, [c.id])
    assistant_text = _texts(msgs)[1]
    assert "error" in assistant_text
    assert "boom" in assistant_text


# ── Order preservation ────────────────────────────────────────────


def test_order_preserved_from_input_list():
    """Renderer doesn't reorder — caller (compute_reads) decides order."""
    g = Graph()
    a = g.add(Call(role=ROLE_USER, output="a"))
    b = g.add(Call(role=ROLE_LLM, output="b"))
    c = g.add(Call(role=ROLE_USER, output="c"))
    msgs = render_dag_messages(g, [c.id, a.id, b.id])
    assert _texts(msgs) == ["c", "a", "b"]


# ── End-to-end: a realistic chat history ──────────────────────────


def test_full_turn_user_assistant_tool_assistant():
    """A turn with one tool round: user asks → assistant + tool call →
    tool returns → assistant final reply. Renderer hands out the
    expected role sequence."""
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="weather today?"))
    a1 = g.add(Call(role=ROLE_LLM, output="let me check"))
    tool = g.add(Call(
        role=ROLE_CODE, name="weather",
        input={"city": "Beijing"},
        output={"temp": 25, "rain": False},
    ))
    a2 = g.add(Call(role=ROLE_LLM, output="It's 25°C, no rain."))
    msgs = render_dag_messages(g, [u.id, a1.id, tool.id, a2.id])
    # u + a1 + (tool call/result pair) + a2 = 5 messages
    assert _roles(msgs) == [
        "user", "assistant",                # user + first llm
        "user", "assistant",                # code Call's synthesized pair
        "assistant",                        # final llm
    ]
