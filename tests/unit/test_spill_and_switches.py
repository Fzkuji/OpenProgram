"""Large-node spilling happens on the WRITE path, plus the ablation switches.

Spilling used to run inside the renderer, which made "build a prompt" a
side-effecting operation: a read-only consumer (a token count, the
Context tab, a replay of an old turn) wrote files into the session
directory just by looking. Recording is the one moment a node's text is
new, so that is where the spill belongs — and rendering becomes a pure
read of the stamp left behind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.context import spill
from openprogram.context.nodes import Call, Graph, ROLE_LLM, ROLE_USER
from openprogram.context.render import render_dag_messages
from openprogram.store.session.session_store import SessionStore


BIG = ("a line of output that is reasonably long\n" * 3000)


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions")


# --- write path owns the spill -------------------------------------


def test_recording_a_large_node_writes_the_file_and_stamps_it(store):
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": BIG})

    node = {m["id"]: m for m in store.get_messages("s")}["u1"]
    stamp = node.get("spilled")
    assert stamp, "an over-cap node must be stamped at record time"
    assert Path(stamp["path"]).exists()
    assert Path(stamp["path"]).read_text() == BIG
    assert stamp["total_chars"] == len(BIG)


def test_small_nodes_are_not_spilled(store):
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": "hi"})
    node = {m["id"]: m for m in store.get_messages("s")}["u1"]
    assert not node.get("spilled")


def test_rendering_writes_nothing(store, tmp_path):
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": BIG})

    large = tmp_path / "sessions" / "s" / "large_nodes"
    before = sorted(p.name for p in large.iterdir())

    from openprogram.store.session.graphstore_shim import GraphStoreShim
    g = GraphStoreShim(store, "s").load()
    for _ in range(3):
        render_dag_messages(g, list(g.nodes), None)

    after = sorted(p.name for p in large.iterdir())
    assert after == before, "the render path must not create spill files"


def test_render_cites_the_spilled_path():
    g = Graph()
    n = g.add(Call(role=ROLE_USER, output=BIG,
                   metadata={"spilled": {"path": "/tmp/x.txt",
                                         "total_lines": 3000,
                                         "total_chars": len(BIG)}}))
    out = render_dag_messages(g, [n.id], None)
    text = out[0].content[0].text
    assert "/tmp/x.txt" in text
    assert len(text) < len(BIG)


def test_an_unspilled_over_cap_node_is_still_truncated():
    """No stamp (spilling off, or the write failed) must not mean the
    whole payload goes to the model."""
    g = Graph()
    n = g.add(Call(role=ROLE_USER, output=BIG))
    out = render_dag_messages(g, [n.id], None)
    text = out[0].content[0].text
    assert len(text) < len(BIG)
    assert "elided" in text


# --- ablation switch: OPENPROGRAM_NODE_SPILL -----------------------


def test_spill_switch_off_writes_no_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(spill, "SPILL_ENABLED", False)
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u1", "role": "user", "content": BIG})

    node = {m["id"]: m for m in store.get_messages("s")}["u1"]
    assert not node.get("spilled")
    assert not (tmp_path / "sessions" / "s" / "large_nodes").exists()

    # …and the render falls back to char truncation, not full text.
    from openprogram.store.session.graphstore_shim import GraphStoreShim
    g = GraphStoreShim(store, "s").load()
    text = render_dag_messages(g, list(g.nodes), None)[0].content[0].text
    assert len(text) < len(BIG)
    assert "chars elided" in text


# --- ablation switch: OPENPROGRAM_EXPOSE_DEFAULT -------------------


def test_expose_default_reads_the_env(monkeypatch):
    from openprogram.agentic_programming.function import default_expose

    monkeypatch.delenv("OPENPROGRAM_EXPOSE_DEFAULT", raising=False)
    assert default_expose() == "io"
    for val in ("llm", "full", "hidden", "io"):
        monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", val)
        assert default_expose() == val
    # A typo must not break every import in the process.
    monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", "nonsense")
    assert default_expose() == "io"


def test_explicit_expose_still_wins_over_the_default(monkeypatch):
    from openprogram.agentic_programming.function import agentic_function

    monkeypatch.setenv("OPENPROGRAM_EXPOSE_DEFAULT", "full")

    @agentic_function(expose="llm", register_globally=False)
    def explicit() -> str:
        """Doc."""
        return "x"

    @agentic_function(register_globally=False)
    def implicit() -> str:
        """Doc."""
        return "x"

    assert explicit.expose == "llm"
    assert implicit.expose == "full"
