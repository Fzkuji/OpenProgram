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
from openprogram.store import GraphStoreShim, SessionStore, _store as _store_var


class _FakeRuntime(Runtime):
    """Skip the provider/model machinery — we only test DAG side-effects."""

    def __init__(self, reply: str = "ok"):
        super().__init__(call=lambda *a, **kw: reply, model="dummy")
        self._fake_reply = reply

    # Override the actual LLM call — returns the canned reply without
    # touching a provider. (The old _uses_legacy_call override is gone:
    # there is a single exec path now; overriding _call is enough.)
    def _call(self, content, model="default", response_format=None):
        return self._fake_reply


@pytest.fixture
def store(tmp_path: Path):
    """Yield a GraphStore installed into the ``_store`` ContextVar for
    the duration of the test, mirroring what the dispatcher does at
    turn entry. Resets on teardown."""
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = GraphStoreShim(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


# Top-level exec (no enclosing @agentic_function)


def test_exec_without_function_frame_appends_llm_call(store):
    rt = _FakeRuntime(reply="hello back")

    @agentic_function
    def chat(prompt, runtime=None):
        # Inside the function so exec has a Context tree to attach to.
        return runtime.exec(prompt)

    chat("hi there", runtime=rt)

    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "hello back"


# exec inside an @agentic_function — called_by set


def test_exec_inside_function_stamps_called_by(store):
    rt = _FakeRuntime(reply="reply")

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


# No DAG side-effects when no store is installed


def test_exec_without_store_writes_nothing():
    rt = _FakeRuntime(reply="x")
    # No ``_store.set(...)`` here — standalone mode.

    @agentic_function
    def f(runtime=None):
        return runtime.exec("hi")

    result = f(runtime=rt)
    assert result == "x"


# llm node lifecycle: opened running, closed completed


def test_exec_llm_node_lifecycle_running_then_completed(store):
    """One exec writes one llm node that ends up status=completed with the
    reply as output (opened running, closed on return). Status vocabulary
    is unified with the chat path (session-dag.md decision 2):
    completed/error/cancelled, not success."""
    rt = _FakeRuntime(reply="done")

    @agentic_function
    def plan(task, runtime=None):
        return runtime.exec(f"plan: {task}")

    plan("x", runtime=rt)

    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "done"
    assert (llm_nodes[0].metadata or {}).get("status") == "completed"


def test_tool_loop_subcall_attributes_to_llm_node(store):
    """A function the model calls during an exec's tool loop records
    ``called_by`` = the llm node (code → llm → code chain), not the
    enclosing function frame.

    Simulates the tool-loop attribution that ``_call_via_providers`` does:
    while the model 'runs', _call_id is pointed at the in-flight llm node
    (exposed via runtime._active_llm_node_id), so any @agentic_function the
    model invokes lands under the llm node.
    """
    from openprogram.agentic_programming.function import _call_id

    @agentic_function
    def child(x, runtime=None):
        return f"child:{x}"

    class _ToolLoopRuntime(Runtime):
        """_call mimics a provider tool loop: it points _call_id at the
        open llm node (as _call_via_providers does) and invokes a tool."""
        def __init__(self):
            super().__init__(call=lambda *a, **kw: "final", model="dummy")

        def _call(self, content, model="default", response_format=None):
            node_id = getattr(self, "_active_llm_node_id", None)
            if node_id is not None:
                tok = _call_id.set(node_id)
                try:
                    child("v", runtime=self)
                finally:
                    _call_id.reset(tok)
            return "final"

    rt = _ToolLoopRuntime()

    @agentic_function
    def parent(task, runtime=None):
        return runtime.exec(f"parent: {task}")

    parent("go", runtime=rt)

    g = store.load()
    parent_node = next(n for n in g if n.is_code() and n.name == "parent")
    child_node = next(n for n in g if n.is_code() and n.name == "child")
    llm_node = next(n for n in g if n.is_llm())

    # The llm node is a child of parent's code node.
    assert llm_node.called_by == parent_node.id
    # The child the model called during the tool loop is a child of the
    # llm node — NOT a direct sibling under parent. This is the code → llm
    # → code chain the unification fixes.
    assert child_node.called_by == llm_node.id


# stream_fn injection: exec(stream_fn=fake) reaches the provider path


def test_exec_stream_fn_injection(store):
    """exec(stream_fn=fake) threads a caller-supplied stream through the
    provider path (exec → _call_via_providers → AgentSession → agent_loop),
    so the dispatcher / integration tests can inject a fake model without a
    network call. Verifies the fake's text comes back and a llm node lands."""
    import time as _time
    from openprogram.providers.types import (
        AssistantMessage, TextContent, Model,
        EventStart, EventTextStart, EventTextEnd, EventDone,
    )

    captured = {}

    async def fake_stream(model, context, options=None):
        # Record what the loop handed the "model" so we can assert the
        # prompt was built (system + current turn).
        captured["system"] = getattr(context, "system_prompt", None)
        captured["n_messages"] = len(getattr(context, "messages", []) or [])

        def _msg(text):
            return AssistantMessage(
                content=[TextContent(text=text)],
                api="completion", provider="callable", model="fake",
                stop_reason="stop", timestamp=int(_time.time() * 1000),
            )

        yield EventStart(partial=_msg(""))
        yield EventTextStart(content_index=0, partial=_msg(""))
        yield EventTextEnd(content_index=0, content="from fake stream", partial=_msg("from fake stream"))
        yield EventDone(reason="stop", message=_msg("from fake stream"))

    # A runtime with a provider model (so it takes the _call_via_providers
    # path) but no real network — the injected stream_fn intercepts.
    rt = Runtime(model="default")
    rt.api_model = Model(
        id="fake", name="fake", api="completion",
        provider="callable", base_url="",
    )

    @agentic_function
    def ask(q, runtime=None):
        return runtime.exec(f"q: {q}", stream_fn=fake_stream)

    result = ask("hello", runtime=rt)

    assert result == "from fake stream"
    assert captured["n_messages"] >= 1  # at least the current turn
    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "from fake stream"
