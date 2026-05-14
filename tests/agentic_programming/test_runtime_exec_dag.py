"""runtime.exec → DAG: each successful LLM call appends an llm-role
Call. ``called_by`` carries the enclosing ``@agentic_function`` pending
id (when called from inside one), or empty string at the top level.

Prompt-composition logic is untouched — these tests don't assert what
the LLM saw, only what got recorded into the DAG afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.storage import GraphStore, init_db


class _FakeRuntime(Runtime):
    """Skip the provider/model machinery — we only test DAG side-effects."""

    def __init__(self, reply: str = "ok"):
        super().__init__(call=lambda *a, **kw: reply, model="dummy")
        self._fake_reply = reply

    # Override the actual LLM call.
    def _call(self, content, model="default", response_format=None):
        return self._fake_reply

    # Pretend we're not on the "legacy call" path. The default exec()
    # has a legacy-text-merge branch that uses parent_ctx; bypass it
    # by claiming we use a non-legacy call.
    def _uses_legacy_call(self) -> bool:
        return True


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db = tmp_path / "x.sqlite"
    init_db(db)
    s = GraphStore(db, "s1")
    s.create_session_row()
    return s


# ── Top-level exec (no enclosing @agentic_function) ────────────────


def test_exec_without_function_frame_appends_llm_call(store):
    rt = _FakeRuntime(reply="hello back")
    rt.attach_store(store)

    @agentic_function
    def chat(prompt, runtime=None):
        # Inside the function so exec has a Context tree to attach to.
        return runtime.exec(prompt)

    chat("hi there", runtime=rt)

    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "hello back"


# ── exec inside an @agentic_function — called_by set ─────────────


def test_exec_inside_function_stamps_called_by(store):
    rt = _FakeRuntime(reply="reply")
    rt.attach_store(store)

    @agentic_function
    def plan(task, runtime=None):
        return runtime.exec(f"plan: {task}")

    plan("write a haiku", runtime=rt)

    g = store.load()
    code_nodes = [n for n in g if n.is_code() and n.name == "plan"]
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(code_nodes) == 1
    assert len(llm_nodes) == 1
    # ModelCall's called_by points at the code Call's id
    assert llm_nodes[0].called_by == code_nodes[0].id


def test_exec_nested_calls_stamp_correct_frame(store):
    rt = _FakeRuntime(reply="r")
    rt.attach_store(store)

    @agentic_function
    def inner(x, runtime=None):
        return runtime.exec(f"inner: {x}")

    @agentic_function
    def outer(x, runtime=None):
        # First inner runs to completion; then we exec from outer's body.
        a = inner(x, runtime=runtime)
        b = runtime.exec(f"outer: {x}")
        return a + b

    outer("q", runtime=rt)
    g = store.load()
    code_by_name = {n.name: n for n in g if n.is_code()}
    inner_id = code_by_name["inner"].id
    outer_id = code_by_name["outer"].id

    # Two LLM calls expected: one inside inner, one inside outer's body.
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 2
    callers = sorted(n.called_by for n in llm_nodes)
    assert callers == sorted([inner_id, outer_id])


# ── No DAG side-effects when store isn't attached ─────────────────


def test_exec_without_store_writes_nothing():
    rt = _FakeRuntime(reply="x")
    # No attach_store — standalone mode.

    @agentic_function
    def f(runtime=None):
        return runtime.exec("hi")

    result = f(runtime=rt)
    assert result == "x"
    # Nothing to verify on disk because there is no store, but
    # head_id should also not have been bumped.
    assert rt.head_id is None
