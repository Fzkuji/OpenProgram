"""runtime.exec → DAG: each successful LLM call appends an llm-role
Call. ``caller`` carries the enclosing ``@agentic_function`` pending
id (when called from inside one), or empty string at the top level.

Prompt-composition logic is untouched — these tests don't assert what
the LLM saw, only what got recorded into the DAG afterwards.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from openprogram.agentic_programming import agent, llm
from openprogram.agentic_programming.function import (
    _current_runtime,
    agentic_function,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.store import SessionNodeWriter, SessionStore, _store as _store_var


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
    s = SessionNodeWriter(store, "s1")
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


# exec inside an @agentic_function — caller set


def test_exec_inside_function_stamps_caller(store):
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
    # ModelCall's caller points at the code Call's id
    assert llm_nodes[0].caller == code_nodes[0].id


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
    callers = sorted(n.caller for n in llm_nodes)
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
    is unified with the chat path (dag/overview.md decision 2):
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
    ``caller`` = the llm node (code → llm → code chain), not the
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
    assert llm_node.caller == parent_node.id
    # The child the model called during the tool loop is a child of the
    # llm node — NOT a direct sibling under parent. This is the code → llm
    # → code chain the unification fixes.
    assert child_node.caller == llm_node.id


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


def test_llm_requires_ambient_runtime():
    token = _current_runtime.set(None)
    try:
        with pytest.raises(
            RuntimeError,
            match=r"llm\(\) requires an ambient Runtime",
        ):
            llm("hello")
    finally:
        _current_runtime.reset(token)


def test_llm_public_signature_excludes_tool_loop_parameters():
    signature = inspect.signature(llm)

    assert list(signature.parameters) == [
        "prompt",
        "model",
        "effort",
        "response_format",
        "choices",
        "web_search",
        "timeout_s",
    ]
    assert all(
        name not in signature.parameters
        for name in ("runtime", "tools", "toolset", "max_iterations", "tool_choice")
    )


def test_agent_returns_validated_structured_value():
    response_format = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    seen = []

    def call(content, model="", response_format=None):
        seen.append(response_format)
        return '{"text": "typed"}'

    runtime = Runtime(call=call, model="session-model")
    token = _current_runtime.set(runtime)
    try:
        result = agent("return structured text", response_format=response_format)
    finally:
        _current_runtime.reset(token)
        runtime.close()

    assert seen == [response_format]
    assert result == {"text": "typed"}


def test_llm_string_is_one_text_block_and_one_request():
    calls = []

    def call(content, model="", response_format=None):
        calls.append((content, model, response_format))
        return "reply"

    runtime = Runtime(call=call, model="session-model")
    token = _current_runtime.set(runtime)
    try:
        assert llm("hello") == "reply"
    finally:
        _current_runtime.reset(token)
        runtime.close()

    assert calls == [
        ([{"type": "text", "text": "hello"}], "session-model", None)
    ]


def test_llm_content_blocks_reach_callable_unchanged():
    prompt = [
        {"type": "text", "text": "locate the button"},
        {
            "type": "image",
            "data": "aW1hZ2U=",
            "mime_type": "image/png",
        },
    ]
    seen = []

    def call(content, model="", response_format=None):
        seen.append(content)
        return "done"

    runtime = Runtime(call=call, model="session-model")
    token = _current_runtime.set(runtime)
    try:
        assert llm(prompt) == "done"
    finally:
        _current_runtime.reset(token)
        runtime.close()

    assert seen == [prompt]


def test_llm_model_and_effort_overrides_are_per_call():
    calls = []

    def call(content, model="", response_format=None):
        from openprogram.agentic_programming.runtime import _current_effort

        calls.append((model, _current_effort.get(None)))
        return "done"

    runtime = Runtime(call=call, model="session-model")
    runtime.thinking_level = "medium"
    token = _current_runtime.set(runtime)
    try:
        assert llm("first", model="override-model", effort="high") == "done"
        assert llm("second") == "done"
    finally:
        _current_runtime.reset(token)
        runtime.close()

    assert calls == [("override-model", "high"), ("session-model", None)]
    assert runtime.model == "session-model"
    assert runtime.thinking_level == "medium"


def test_llm_does_not_create_session_branch_and_records_observability(store):
    runtime = Runtime(call=lambda *_args, **_kwargs: "done", model="session-model")
    session_count = len(store.store.list_sessions())

    @agentic_function
    def summarize(runtime=None):
        return llm("summary")

    assert summarize(runtime=runtime) == "done"
    runtime.close()

    assert len(store.store.list_sessions()) == session_count
    graph = store.load()
    model_call = next(node for node in graph if node.is_llm())
    metadata = model_call.metadata or {}
    assert metadata["execution_kind"] == "llm"
    assert metadata["provider_request_count"] == 1
    assert metadata["agent_iteration_count"] == 0


def test_llm_transport_retry_is_not_an_agent_iteration(store, monkeypatch):
    attempts = 0

    def call(_content, model="", response_format=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary provider failure")
        return "done"

    monkeypatch.setattr(
        "openprogram.agentic_programming.runtime._retry_sleep_seconds",
        lambda *_args: 0,
    )
    runtime = Runtime(call=call, model="session-model", max_retries=2)

    @agentic_function
    def retry_once(runtime=None):
        return llm("retry")

    assert retry_once(runtime=runtime) == "done"
    runtime.close()

    model_call = next(node for node in store.load() if node.is_llm())
    metadata = model_call.metadata or {}
    assert attempts == 2
    assert metadata["provider_request_count"] == 2
    assert metadata["agent_iteration_count"] == 0
