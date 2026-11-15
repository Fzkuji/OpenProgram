"""What a tool result is when it is stored and when a later request renders it.

A later request renders history from what was stored, and from nothing
else: the failure mark, the cap a node was recorded under, a tool result
the loop already bounded. Changing the running cap (or its environment
variable) never changes a conversation that already exists. The whole text
of a bounded result is kept once, by its content, in the conversation's
own directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store.session.weight import quarters, weight
from openprogram.state.store.session import spill
from openprogram.state.store.session.nodes import Call, Graph, ROLE_CODE, ROLE_LLM, ROLE_USER
from openprogram.state.store.session.session_store import SessionStore

BIG = "a line of output that is reasonably long\n" * 3000


def _tool_turn(output: str, **metadata) -> tuple[Graph, list[str]]:
    g = Graph()
    user = g.add(Call(role=ROLE_USER, output="go", predecessor=None))
    reply = g.add(Call(role=ROLE_LLM, output="done", predecessor=user.id))
    tool = g.add(Call(role=ROLE_CODE, name="probe", caller=reply.id, input={"key": "a"},
                      output=output, metadata={"expose": "full", "tool_call_id": "call_1",
                                               **metadata}))
    return g, [user.id, reply.id, tool.id]


def _tool_message(g: Graph, ids: list[str]):
    return next(m for m in render_dag_messages(g, ids, None) if m.role == "toolResult")


# --- is_error ---------------------------------------------------------


@pytest.mark.parametrize("failed", [True, False])
def test_a_failed_result_renders_as_failed(failed):
    g, ids = _tool_turn("no such key", **({"is_error": True} if failed else {}))
    assert _tool_message(g, ids).is_error is failed


# --- a bounded tool result is never cut again --------------------------


def test_a_tool_result_the_model_called_for_renders_whole():
    """``tool_search`` is the one result the loop leaves whole; it goes
    whole on every later request too."""
    g, ids = _tool_turn(BIG)
    assert _tool_message(g, ids).content[0].text == BIG


def test_a_tool_result_stamped_before_keeps_its_old_cut():
    stamp = {"path": "/tmp/x.txt", "total_lines": 3000, "total_chars": len(BIG)}
    g, ids = _tool_turn(BIG, spilled=stamp)
    text = _tool_message(g, ids).content[0].text
    assert text == spill.elide_spilled(BIG, stamp) and len(text) < len(BIG)


def test_recording_a_tool_result_writes_no_spill(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="t")
    node = Call(role=ROLE_CODE, name="tool_search", output=BIG,
                metadata={"tool_call_id": "call_1"}, seq=3)
    store.spill_large_node("s", node)
    assert "spilled" not in (node.metadata or {})
    # A person's message is never cut: sent whole now and on every later request.
    user = Call(role=ROLE_USER, output=BIG, seq=4)
    store.spill_large_node("s", user)
    assert "spilled" not in (user.metadata or {})
    # A program's return value over the cap is still spilled when recorded.
    value = Call(role=ROLE_CODE, name="program", output=BIG, seq=5)
    store.spill_large_node("s", value)
    assert value.metadata["spilled"]["cap"] == spill.NODE_RENDER_CAP


def test_a_long_user_message_renders_whole():
    g = Graph()
    user = g.add(Call(role=ROLE_USER, output=BIG, predecessor=None))
    message = render_dag_messages(g, [user.id], None)[0]
    assert message.content[0].text == BIG


# --- the render reads the recorded cap, not the running one -------------


def test_changing_the_cap_leaves_recorded_nodes_alone(monkeypatch):
    stamp = {"path": "/tmp/x.txt", "total_lines": 3000, "total_chars": len(BIG), "cap": 32_000}
    middle = "m" * 20_000
    before = (spill.elide_spilled(BIG, stamp), spill.elide_spilled(middle, None),
              spill.elide_spilled(BIG, None))
    monkeypatch.setattr(spill, "NODE_RENDER_CAP", 1_000)
    after = (spill.elide_spilled(BIG, stamp), spill.elide_spilled(middle, None),
             spill.elide_spilled(BIG, None))
    assert after == before
    assert after[1] == middle


def test_a_stamp_cuts_at_the_cap_it_recorded():
    text = "x\n" * 30_000
    assert spill.elide_spilled(text, {"path": "/tmp/y.txt", "cap": 100_000}) == text
    assert len(spill.elide_spilled(text, {"path": "/tmp/y.txt", "cap": 1_000})) < len(text)


# --- bound_text: the note is measured, nothing is cut off its end --------


def test_a_long_path_and_large_numbers_still_fit_and_name_the_exact_lines():
    lines = [f"line {i}: " + "x" * 30 for i in range(1, 200_001)]
    text = "\n".join(lines)
    path = "C:\\Users\\" + "deep\\" * 40 + "tool_results\\" + "f" * 32 + ".txt"
    assert len(path) > 200
    out = spill.bound_text(text, lambda whole: path, cap=1_000)

    assert quarters(out) <= 4_000
    assert out.endswith(lines[-1])
    call = re.search(r'read\(("(?:[^"\\]|\\.)*"), offset=(\d+), limit=(\d+)\)', out)
    assert json.loads(call.group(1)) == path        # backslashes escaped as a literal
    first, count = int(call.group(2)), int(call.group(3))
    head, tail = out.split("\n[... ")[0], out.split("...]\n\n")[1]
    assert head.split("\n")[-2] == lines[first - 2]
    assert tail.split("\n")[0] == lines[first - 1 + count]


def test_a_small_cap_gives_the_note_room_before_the_text():
    text = "\n".join("y" * 50 for _ in range(1_000))
    path = "/" + "p" * 60 + ".txt"
    out = spill.bound_text(text, lambda whole: path, cap=100)
    assert quarters(out) <= 400
    assert f'read("{path}", offset=' in out

    # A path no note of this cap can hold: a note without it, still bounded.
    out = spill.bound_text(text, lambda whole: "/" + "q" * 600, cap=100)
    assert quarters(out) <= 400 and "elided" in out


# --- the cap is a weight -----------------------------------------------


def _bound(text, **window):
    token = spill.set_window(window.get("tokens"), window.get("weight"))
    try:
        return spill.bound_text(text, lambda whole: "/tmp/full.txt")
    finally:
        spill.reset_window(token)


def test_ten_thousand_chinese_characters_fit_and_one_more_is_cut():
    assert _bound("中" * 10_000) == "中" * 10_000
    out = _bound("中" * 10_001)
    assert out != "中" * 10_001 and weight(out) <= 10_000


def test_forty_thousand_english_characters_fit():
    assert spill.result_cap() == spill.DEFAULT_TOOL_OUTPUT_WEIGHT == 10_000
    assert _bound("e" * 40_000) == "e" * 40_000
    assert _bound("e" * 40_001) != "e" * 40_001


def test_the_model_and_its_window_set_the_cap():
    token = spill.set_window(8_000)
    try:
        assert spill.result_cap() == 2_400
    finally:
        spill.reset_window(token)
    token = spill.set_window(1_000_000, 50_000)
    try:
        assert spill.result_cap() == 50_000
        assert spill.result_cap(5_000) == 5_000     # a tool's smaller cap wins
    finally:
        spill.reset_window(token)
    assert _bound("e" * 100_000, tokens=1_000_000, weight=50_000) == "e" * 100_000


# --- the whole text: by content, in the conversation -------------------


def _bind_session(session_id):
    from openprogram.runtime.execution.run_control import binding
    return binding.set_current_session_id(session_id)


def test_full_texts_are_kept_once_by_content(tmp_path):
    from openprogram.programs._host.execution import _persist_full_result

    first = _persist_full_result("call_1", "same text", results_dir=tmp_path)
    again = _persist_full_result("call_2", "same text", results_dir=tmp_path)
    other = _persist_full_result("call_1", "other text", results_dir=tmp_path)
    assert first == again != other
    assert first.read_text() == "same text" and other.read_text() == "other text"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted({first.name, other.name})


def test_full_texts_live_and_die_with_their_conversation(tmp_path, monkeypatch):
    from openprogram.programs._host.execution import _persist_full_result
    from openprogram.runtime.execution.run_control import binding

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="t")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    shared = tmp_path / "shared"
    token = _bind_session("s")
    try:
        path = _persist_full_result("call_1", "whole\ntext", fallback_dir=lambda: shared)
    finally:
        binding.reset_current_session_id(token)
    session_dir = Path(store._session_dir("s"))
    assert path.parent == session_dir / "tool_results"
    assert path.read_text() == "whole\ntext"
    assert not shared.exists()

    store.delete_session("s")
    assert not path.exists()


def test_without_a_conversation_the_shared_directory_keeps_it(tmp_path):
    from openprogram.programs._host.execution import _persist_full_result

    path = _persist_full_result("call_1", "text", fallback_dir=lambda: tmp_path / "shared")
    assert path.parent == tmp_path / "shared"


# --- weight -----------------------------------------------------------


def test_weight_is_additive_and_counts_scripts_apart():
    parts = ["hello world", "你好，世界", "カタカナ", "한국어", "ｆｕｌｌ", "", "mixed 中文 text"]
    joined = "".join(parts)
    assert quarters(joined) == sum(quarters(p) for p in parts)
    assert weight("abcd") == 1
    assert weight("中文") == 2
    assert weight("，") == 1
