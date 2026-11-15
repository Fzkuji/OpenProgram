"""context.render.render_dag_messages — DAG nodes → provider messages.

Pure function. Tests use small hand-built graphs and assert the
resulting message list role/content shape.
"""

from __future__ import annotations


from openprogram.state.store.session.nodes import Call, Graph, ROLE_USER, ROLE_LLM, ROLE_CODE
from openprogram.runtime.context.render import render_dag_messages


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


def test_old_code_nodes_keep_their_whole_output():
    """An old tool result renders as its turn sent it, however many llm
    turns follow it: there is no aging step, only compaction replaces it."""
    g = Graph()
    g.add(Call(role=ROLE_CODE, name="search",
                        input={"q": "old"}, output="OLD_FULL_RESULT_TEXT"))
    for i in range(4):
        g.add(Call(role=ROLE_LLM, output=f"t{i}"))
    g.add(Call(role=ROLE_CODE, name="search",
                          input={"q": "new"}, output="NEW_FULL_RESULT_TEXT"))
    all_ids = [n.id for n in sorted(g, key=lambda x: x.seq)]
    blob = "\n".join(_texts(render_dag_messages(g, all_ids)))
    assert "[aged]" not in blob
    assert "OLD_FULL_RESULT_TEXT" in blob
    assert "NEW_FULL_RESULT_TEXT" in blob


def test_model_tool_use_code_node_renders_as_toolcall_toolresult():
    """A code node WITH a tool_call_id (model-emitted tool_use) round-trips
    as a real ToolCall in an assistant message of its own + a
    ToolResultMessage — not the user/assistant text pair — and the reply's
    text follows its calls, as the turn sent them."""
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
    assert _roles(msgs) == ["assistant", "toolResult", "assistant"]
    call_message = msgs[0]
    tool_calls = [c for c in call_message.content if getattr(c, "type", None) == "toolCall"]
    assert len(tool_calls) == 1
    assert tool_calls[0].id == "call_abc"
    assert tool_calls[0].name == "search"
    assert tool_calls[0].arguments == {"query": "DAG"}
    # the ToolResultMessage carries the matching id + the result
    tr = msgs[1]
    assert tr.tool_call_id == "call_abc"
    assert "hits" in tr.content[0].text
    assert msgs[2].content[0].text == "let me search"


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


def test_failed_program_predecessor_is_labeled_do_not_continue():
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="auto_workflow",
        input={"task": "submit weekly report"},
        output=None,
        metadata={"status": "error"},
    ))
    msgs = render_dag_messages(g, [c.id])
    assert _roles(msgs) == ["user", "assistant"]
    blob = "\n".join(_texts(msgs))
    assert "ended in error" in blob
    assert "unless the user explicitly asks" in blob
    assert "auto_workflow" in blob


def test_cancelled_program_predecessor_is_labeled_do_not_continue():
    g = Graph()
    c = g.add(Call(
        role=ROLE_CODE,
        name="auto_workflow",
        input={"task": "submit weekly report"},
        output={"partial": True},
        metadata={"status": "cancelled"},
    ))
    msgs = render_dag_messages(g, [c.id])
    user_text = _texts(msgs)[0]
    assert "was cancelled" in user_text
    assert "unless the user explicitly asks" in user_text


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


# Sub-agent reports (attach nodes)


def _attach(g, output, **attach):
    return g.add(Call(role=ROLE_LLM, output=output,
                      metadata={"function": "attach", "display": "runtime", "attach": attach}))


def test_background_report_names_its_sender_and_transcript_address():
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="check the logs"))
    a = _attach(g, "the log is clean", session_id="s1", head_id="h9", label="scout",
                prompt="read the worker log", status="completed")
    msgs = render_dag_messages(g, [u.id, a.id])
    assert _roles(msgs) == ["user", "user"]
    report = _texts(msgs)[1]
    assert report.startswith('[Report from sub-agent "scout"]')
    assert "Task: read the worker log" in report
    assert "Final reply (completed):\nthe log is clean" in report
    assert 'read_conversation(session_id="s1", head_id="h9")' in report


def test_running_report_says_so_and_sync_report_is_left_to_the_tool_result():
    g = Graph()
    running = _attach(g, "", session_id="s1", label="scout", status="running")
    sync = _attach(g, "done", session_id="s1", head_id="h1", delivery="tool_result", status="completed")
    msgs = render_dag_messages(g, [running.id, sync.id])
    assert len(msgs) == 1
    assert "Still running" in _texts(msgs)[0] and "read_conversation" not in _texts(msgs)[0]


def test_a_running_helpers_card_renders_the_same_bytes_when_it_ends():
    """C3: the caller's prompt only grows by appending. The card's live state
    (status, reply, usage, head) stays on the node; the result is the notice."""
    import hashlib
    import json

    def prefix(**attach):
        g = Graph()
        u = g.add(Call(role=ROLE_USER, output="check the logs"))
        card = _attach(g, attach.pop("output", "(running)"), **attach)
        msgs = render_dag_messages(g, [u.id, card.id])
        return hashlib.sha256(json.dumps(_texts(msgs)).encode()).hexdigest(), _texts(msgs)

    started = dict(session_id="s1", label="scout", prompt="read the worker log", job_id="job_1",
                   result_in="notice")
    before, texts = prefix(status="running", head_id=None, **started)
    after, _ = prefix(status="completed", output="the log is clean", head_id="h9",
                      usage={"tokens": 1200, "cost_usd": "0.01"}, execution_id="job_1",
                      status_version=4, **started)
    assert before == after
    assert texts[1].startswith('[Sub-agent "scout" started]')
    assert 'read_conversation(session_id="job_1")' in texts[1]
    assert "the log is clean" not in texts[1]


def test_a_card_from_before_results_moved_into_the_notice_keeps_its_report():
    """Older conversations: their notice said the report is "attached above",
    so a card without ``result_in`` still renders its final reply."""
    g = Graph()
    u = g.add(Call(role=ROLE_USER, output="check the logs"))
    card = _attach(g, "the log is clean", session_id="s1", head_id="h9", label="scout",
                   prompt="read the worker log", status="completed", job_id="job_1")
    report = _texts(render_dag_messages(g, [u.id, card.id]))[1]
    assert report.startswith('[Report from sub-agent "scout"]')
    assert "Final reply (completed):\nthe log is clean" in report
