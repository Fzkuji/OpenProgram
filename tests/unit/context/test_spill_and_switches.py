"""Large-node spilling happens on the WRITE path, plus the ablation switches.

Spilling used to run inside the renderer, which made "build a prompt" a
side-effecting operation: a read-only consumer (a token count, the
Context tab, a replay of an old turn) wrote files into the session
directory just by looking. Recording is the one moment a node's text is
new, so that is where the spill belongs — and rendering becomes a pure
read of the stamp left behind.
"""

from __future__ import annotations

from openprogram.runtime.agent import Agent

from pathlib import Path

import pytest

from openprogram.state.store.session.weight import weight
from openprogram.state.store.session import spill
from openprogram.state.store.session.nodes import Call, Graph, ROLE_CODE, ROLE_USER
from openprogram.runtime.context.render import render_dag_messages
from openprogram.state.store.session.session_store import SessionStore


BIG = ("a line of output that is reasonably long\n" * 3000)


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


# --- write path owns the spill -------------------------------------


def _program_value(seq: int = 3) -> Call:
    """A program's return value: a node the loop did not bound when it was
    made, so recording spills it when it is over the cap."""
    return Call(role=ROLE_CODE, name="program", output=BIG, seq=seq)


def test_recording_a_large_node_writes_the_file_and_stamps_it(store):
    store.create_session("s", "main", title="t")
    node = _program_value()
    store.spill_large_node("s", node)
    stamp = node.metadata.get("spilled")
    assert stamp, "an over-cap node must be stamped at record time"
    assert Path(stamp["path"]).exists()
    assert Path(stamp["path"]).read_text() == BIG
    assert stamp["total_chars"] == len(BIG)


def test_a_persons_long_message_is_recorded_whole(store):
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": BIG})
    node = {m["id"]: m for m in store.get_messages("s")}["u1"]
    assert not node.get("spilled") and node["content"] == BIG


def test_small_nodes_are_not_spilled(store):
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": "hi"})
    node = {m["id"]: m for m in store.get_messages("s")}["u1"]
    assert not node.get("spilled")


def test_rendering_writes_nothing(store, tmp_path):
    store.create_session("s", "main", title="t")
    node = _program_value()
    store.spill_large_node("s", node)

    large = tmp_path / "sessions" / "default" / "chats" / "s" / "large_nodes"
    before = sorted(p.name for p in large.iterdir())

    g = Graph()
    g.add(node)
    for _ in range(3):
        render_dag_messages(g, [node.id], None)

    after = sorted(p.name for p in large.iterdir())
    assert after == before, "the render path must not create spill files"


def test_render_cites_the_spilled_path():
    """A node stamped before messages were never cut keeps its old cut."""
    g = Graph()
    n = g.add(Call(role=ROLE_USER, output=BIG,
                   metadata={"spilled": {"path": "/tmp/x.txt",
                                         "total_lines": 3000,
                                         "total_chars": len(BIG)}}))
    out = render_dag_messages(g, [n.id], None)
    text = out[0].content[0].text
    assert "/tmp/x.txt" in text
    assert len(text) < len(BIG)


def _rendered(node: Call) -> str:
    g = Graph()
    g.add(node)
    return "\n".join(getattr(b, "text", "") for m in render_dag_messages(g, [node.id], None)
                     for b in m.content)


def test_an_unspilled_over_cap_node_is_still_truncated():
    """No stamp (spilling off, or the write failed) must not mean the
    whole payload goes to the model."""
    text = _rendered(Call(role=ROLE_CODE, name="program", output=BIG))
    assert len(text) < len(BIG)
    assert "elided" in text


def test_an_unstamped_long_user_message_renders_whole():
    assert _rendered(Call(role=ROLE_USER, output=BIG)) == BIG


# --- ablation switch: OPENPROGRAM_NODE_SPILL -----------------------


def test_spill_switch_off_writes_no_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(spill, "SPILL_ENABLED", False)
    store.create_session("s", "main", title="t")
    node = _program_value()
    store.spill_large_node("s", node)

    assert not (node.metadata or {}).get("spilled")
    assert not (tmp_path / "sessions" / "default" / "chats" / "s" / "large_nodes").exists()

    # …and the render falls back to char truncation, not full text.
    text = _rendered(node)
    assert len(text) < len(BIG)
    assert "chars elided" in text


# --- ablation switch: OPENPROGRAM_EXPOSE_DEFAULT -------------------


def test_expose_default_reads_the_env(monkeypatch):
    from openprogram.runtime.context.scope import default_expose

    monkeypatch.delenv("OPENPROGRAM_EXPOSE_DEFAULT", raising=False)
    assert default_expose() == "io"
    for val in ("llm", "full", "hidden", "io"):
        monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", val)
        assert default_expose() == val
    # A typo must not break every import in the process.
    monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", "nonsense")
    assert default_expose() == "io"


def test_explicit_expose_still_wins_over_the_default(monkeypatch):

    monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", "full")

    class _ExplicitAgent(Agent):
        method_options = {'explicit': {'expose': "llm", 'register_globally': False, 'tool': True, 'capture_io': True, 'name': 'explicit'}}

        def explicit(self, ) -> str:
            """Doc."""
            return "x"

    explicit = _ExplicitAgent().explicit

    class _ImplicitAgent(Agent):
        method_options = {'implicit': {'register_globally': False, 'tool': True, 'capture_io': True, 'name': 'implicit'}}

        def implicit(self, ) -> str:
            """Doc."""
            return "x"

    implicit = _ImplicitAgent().implicit

    assert explicit.expose == "llm"
    assert implicit.expose == "full"


# --- a text bounded when it is made ---------------------------------


def test_a_bounded_text_fits_the_cap_and_names_the_lines_to_read(tmp_path):
    saved = tmp_path / "whole.txt"

    def save(text):
        saved.write_text(text)
        return saved

    lines = [f"line {i}: " + "x" * 50 for i in range(1, 3001)]
    text = "\n".join(lines)
    out = spill.bound_text(text, save)

    assert weight(out) <= spill.result_cap() < weight(text)
    assert saved.read_text() == text
    assert out.startswith("line 1: ") and out.endswith(lines[-1])
    # The read hint covers exactly the lines left out.
    import re
    hint = re.search(r'offset=(\d+), limit=(\d+)', out)
    first, count = int(hint.group(1)), int(hint.group(2))
    shown = out.split("\n[... ")[0].split("\n")
    assert shown[-2] == lines[first - 2] and first - 1 == len(shown) - 1
    assert out.split("...]\n\n")[1].split("\n")[0] == lines[first - 1 + count]


def test_a_one_line_text_is_bounded_too(tmp_path):
    text = '{"steps": [' + ", ".join('{"n": %d}' % i for i in range(20_000)) + "]}"
    assert "\n" not in text and weight(text) > spill.result_cap()
    out = spill.bound_text(text, lambda whole: tmp_path / "w.txt")
    assert weight(out) <= spill.result_cap()
    assert str(tmp_path / "w.txt") in out


def test_a_text_under_the_cap_is_not_saved():
    calls = []
    assert spill.bound_text("short", calls.append) == "short"
    assert calls == []
