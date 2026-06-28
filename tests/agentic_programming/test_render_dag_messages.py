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


# Empty / unknown


def test_empty_reads_returns_empty_list():
    g = Graph()
    assert render_dag_messages(g, []) == []


def test_unknown_ids_are_silently_skipped():
    g = Graph()
    assert render_dag_messages(g, ["nonexistent"]) == []


# Single-role renderings


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


# code Call renders as call/result pair


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


def test_old_code_nodes_age_to_stub_beyond_tail_window():
    """Code nodes older than the last TAIL_TURNS (3) llm nodes render as
    an [aged] stub; recent ones keep full output. Mirrors tool_aging."""
    g = Graph()
    # An old tool call, then 3+ llm turns after it (pushing it out of the
    # tail window), then a recent tool call.
    old_tc = g.add(Call(role=ROLE_CODE, name="search",
                        input={"q": "old"}, output="OLD_FULL_RESULT_TEXT"))
    g.add(Call(role=ROLE_LLM, output="t1"))
    g.add(Call(role=ROLE_LLM, output="t2"))
    g.add(Call(role=ROLE_LLM, output="t3"))
    recent_llm = g.add(Call(role=ROLE_LLM, output="t4"))
    recent_tc = g.add(Call(role=ROLE_CODE, name="search",
                          input={"q": "new"}, output="NEW_FULL_RESULT_TEXT"))
    all_ids = [n.id for n in sorted(g, key=lambda x: x.seq)]
    msgs = render_dag_messages(g, all_ids)
    texts = _texts(msgs)
    blob = "\n".join(texts)
    # old tool result rendered as a one-line [aged] stub (the stub keeps a
    # short blurb of the result, so we check the stub PREFIX is present and
    # the old result occupies a single aged line, not its own full message)
    assert "[aged]" in blob
    aged_lines = [t for t in texts if t.startswith("[aged]")]
    assert len(aged_lines) == 1
    assert "search" in aged_lines[0]
    # recent tool result kept at full fidelity (no stub)
    assert "NEW_FULL_RESULT_TEXT" in blob
    assert not any("NEW_FULL_RESULT_TEXT" in t and t.startswith("[aged]") for t in texts)


def test_model_tool_use_code_node_renders_as_toolcall_toolresult():
    """A code node WITH a tool_call_id (model-emitted tool_use) round-trips
    as a real ToolCall (inside the preceding assistant turn) + a
    ToolResultMessage — not the user/assistant text pair."""
    g = Graph()
    llm = g.add(Call(role=ROLE_LLM, output="let me search"))
    tc = g.add(Call(
        role=ROLE_CODE,
        name="search",
        input={"query": "DAG"},
        output={"hits": 3},
        caller=llm.id,
        metadata={"tool_call_id": "call_abc"},
    ))
    msgs = render_dag_messages(g, [llm.id, tc.id])
    # assistant turn, then a tool-result message
    assert _roles(msgs) == ["assistant", "toolResult"]
    # the ToolCall is appended INSIDE the assistant message content
    asst = msgs[0]
    tool_calls = [c for c in asst.content if getattr(c, "type", None) == "toolCall"]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_abc"
    assert tool_calls[0].name == "search"
    assert tool_calls[0].arguments == {"query": "DAG"}
    # the ToolResultMessage carries the matching id + the result
    tr = msgs[1]
    assert tr.tool_call_id == "call_abc"
    assert "hits" in tr.content[0].text


def test_toolcall_synthesizes_assistant_when_none_precedes():
    """If a model-tool_use code node appears with no preceding assistant
    (reads started mid-turn), an empty assistant is synthesized to host
    the ToolCall — providers reject orphaned tool_use."""
    g = Graph()
    tc = g.add(Call(
        role=ROLE_CODE, name="search", input={"q": "x"}, output="ok",
        metadata={"tool_call_id": "call_xyz"},
    ))
    msgs = render_dag_messages(g, [tc.id])
    assert _roles(msgs) == ["assistant", "toolResult"]
    assert any(getattr(c, "type", None) == "toolCall" for c in msgs[0].content)


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


# Order preservation


def test_order_preserved_from_input_list():
    """Renderer doesn't reorder — caller (compute_reads) decides order."""
    g = Graph()
    a = g.add(Call(role=ROLE_USER, output="a"))
    b = g.add(Call(role=ROLE_LLM, output="b"))
    c = g.add(Call(role=ROLE_USER, output="c"))
    msgs = render_dag_messages(g, [c.id, a.id, b.id])
    assert _texts(msgs) == ["c", "a", "b"]


# End-to-end: a realistic chat history


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
