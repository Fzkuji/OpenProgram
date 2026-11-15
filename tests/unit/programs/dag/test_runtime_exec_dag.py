"""runtime.exec → DAG: each successful LLM call appends an llm-role
Call. ``caller`` carries the enclosing ``Agent method`` pending
id (when called from inside one), or empty string at the top level.

Prompt-composition logic is untouched — these tests don't assert what
the LLM saw, only what got recorded into the DAG afterwards.
"""

from __future__ import annotations

from openprogram.runtime.agent import Agent

import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from openprogram.runtime.agent import agent, llm
from openprogram.runtime.agent.call_state import (
    _current_runtime,

)
from openprogram.runtime.agent.runtime import Runtime
from openprogram.state.store import SessionNodeWriter, SessionStore, _store as _store_var


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


# Top-level exec (no enclosing Agent method)


def test_exec_without_function_frame_appends_llm_call(store):
    rt = _FakeRuntime(reply="hello back")

    class _ChatAgent(Agent):
        method_options = {'chat': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'chat'}}

        def chat(self, prompt, runtime=None):
            # Inside the function so exec has a Context tree to attach to.
            return runtime.exec(prompt)

    chat = _ChatAgent().chat

    chat("hi there", runtime=rt)

    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "hello back"


# exec inside an Agent method — caller set


def test_exec_inside_function_stamps_caller(store):
    rt = _FakeRuntime(reply="reply")

    class _PlanAgent(Agent):
        method_options = {'plan': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'plan'}}

        def plan(self, task, runtime=None):
            return runtime.exec(f"plan: {task}")

    plan = _PlanAgent().plan

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

    class _InnerAgent(Agent):
        method_options = {'inner': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'inner'}}

        def inner(self, x, runtime=None):
            return runtime.exec(f"inner: {x}")

    inner = _InnerAgent().inner

    class _OuterAgent(Agent):
        method_options = {'outer': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'outer'}}

        def outer(self, x, runtime=None):
            # First inner runs to completion; then we exec from outer's body.
            a = inner(x, runtime=runtime)
            b = runtime.exec(f"outer: {x}")
            return a + b

    outer = _OuterAgent().outer

    outer("q", runtime=rt)
    g = store.load()
    code_by_name = {n.name: n for n in g if n.is_code()}
    outer_id = code_by_name["outer"].id
    link = next(n for n in g if n.metadata.get('child_session_id'))
    child = SessionNodeWriter(store.store, link.metadata['child_session_id']).load()
    inner = next(n for n in child if n.name == 'inner')
    assert [n.caller for n in g if n.is_llm()] == [outer_id]
    assert [n.caller for n in child if n.is_llm()] == [inner.id]
    assert not inner.caller


# No DAG side-effects when no store is installed


def test_exec_without_store_writes_nothing():
    rt = _FakeRuntime(reply="x")
    # No ``_store.set(...)`` here — standalone mode.

    class _FAgent(Agent):
        method_options = {'f': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'f'}}

        def f(self, runtime=None):
            return runtime.exec("hi")

    f = _FAgent().f

    result = f(runtime=rt)
    assert result == "x"


# llm node lifecycle: opened running, closed completed


def test_exec_llm_node_lifecycle_running_then_completed(store):
    """One exec writes one llm node that ends up status=completed with the
    reply as output (opened running, closed on return). Status vocabulary
    is unified with the chat path (dag/overview.md decision 2):
    completed/error/cancelled, not success."""
    rt = _FakeRuntime(reply="done")

    class _PlanAgent(Agent):
        method_options = {'plan': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'plan'}}

        def plan(self, task, runtime=None):
            return runtime.exec(f"plan: {task}")

    plan = _PlanAgent().plan

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
    (exposed via runtime._active_llm_node_id), so any Agent method the
    model invokes lands under the llm node.
    """
    from openprogram.runtime.context.scope import _call_id

    class _ChildAgent(Agent):
        method_options = {'child': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'child'}}

        def child(self, x, runtime=None):
            return f"child:{x}"

    child = _ChildAgent().child

    class _ToolLoopRuntime(Runtime):
        """_call mimics a provider tool loop: it points _call_id at the
        open llm node (as _call_via_providers does) and invokes a tool."""
        def __init__(self):
            super().__init__(call=lambda *a, **kw: "final", model="dummy")

        def _call(self, content, model="default", response_format=None):
            node_id = getattr(self, "_active_llm_node_id", None)
            if node_id is not None:
                from openprogram.programs._host.runtime import _current_tool_call_id
                tool_token = _current_tool_call_id.set("dispatch-child")
                tok = _call_id.set(node_id)
                try:
                    child("v", runtime=self)
                finally:
                    _call_id.reset(tok)
                    _current_tool_call_id.reset(tool_token)
            return "final"

    rt = _ToolLoopRuntime()

    class _ParentAgent(Agent):
        method_options = {'parent': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'parent'}}

        def parent(self, task, runtime=None):
            return runtime.exec(f"parent: {task}")

    parent = _ParentAgent().parent

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
    from openprogram.models.providers.types import (
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

    class _AskAgent(Agent):
        method_options = {'ask': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'ask'}}

        def ask(self, q, runtime=None):
            return runtime.exec(f"q: {q}", stream_fn=fake_stream)

    ask = _AskAgent().ask

    result = ask("hello", runtime=rt)

    assert result == "from fake stream"
    assert captured["n_messages"] >= 1  # at least the current turn
    g = store.load()
    llm_nodes = [n for n in g if n.is_llm()]
    assert len(llm_nodes) == 1
    assert llm_nodes[0].output == "from fake stream"


def test_llm_owns_standalone_runtime(monkeypatch):
    created = []
    closed = []

    class FakeRuntime:
        def exec(self, **kwargs):
            assert _store_var.get() is not None
            assert kwargs['content'] == [{'type': 'text', 'text': 'hello'}]
            return 'standalone result'

        def close(self):
            closed.append(True)

    def create():
        created.append(True)
        return FakeRuntime()

    monkeypatch.setattr('openprogram.runtime.agent.runtime.factory.create_runtime', create)
    token = _current_runtime.set(None)
    store_token = _store_var.set(None)
    try:
        assert llm('hello') == 'standalone result'
        assert created == [True]
        assert closed == [True]
        assert _current_runtime.get() is None
        assert _store_var.get() is None
    finally:
        _store_var.reset(store_token)
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


@pytest.mark.parametrize(
    ("response_format", "raw_result", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            '{"text": "typed"}',
            {"text": "typed"},
        ),
        ({"type": "array", "items": {"type": "integer"}}, "[1, 2]", [1, 2]),
        ({"type": "integer"}, "7", 7),
        ({"type": "null"}, "null", None),
    ],
)
def test_agent_returns_validated_structured_value(
    response_format, raw_result, expected
):
    seen = []

    def call(content, model="", response_format=None):
        seen.append(response_format)
        return raw_result

    runtime = Runtime(call=call, model="session-model")
    token = _current_runtime.set(runtime)
    try:
        result = agent("return structured text", response_format=response_format)
    finally:
        _current_runtime.reset(token)
        runtime.close()

    assert seen == [response_format]
    assert result == expected


def test_agent_public_type_hints_are_resolvable():
    assert "response_format" in get_type_hints(agent)


@pytest.mark.parametrize(
    ("runtime_result", "expected"),
    [({"text": "legacy"}, "legacy"), ("plain text", "plain text")],
)
def test_agent_unstructured_call_preserves_legacy_runtime_contract(
    runtime_result, expected
):
    class LegacyRuntime:
        def exec(
            self,
            *,
            content,
            model,
            tools,
            max_iterations,
            timeout_s,
            effort,
            execution_kind,
        ):
            return runtime_result

    token = _current_runtime.set(LegacyRuntime())
    try:
        result = agent("use the legacy runtime")
    finally:
        _current_runtime.reset(token)

    assert result == expected


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
        from openprogram.runtime.agent.runtime import _current_effort

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

    class _SummarizeAgent(Agent):
        method_options = {'summarize': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'summarize'}}

        def summarize(self, runtime=None):
            return llm("summary")

    summarize = _SummarizeAgent().summarize

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
        "openprogram.runtime.agent.runtime._retry_sleep_seconds",
        lambda *_args: 0,
    )
    runtime = Runtime(call=call, model="session-model", max_retries=2)

    class _RetryOnceAgent(Agent):
        method_options = {'retry_once': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'retry_once'}}

        def retry_once(self, runtime=None):
            return llm("retry")

    retry_once = _RetryOnceAgent().retry_once

    assert retry_once(runtime=runtime) == "done"
    runtime.close()

    model_call = next(node for node in store.load() if node.is_llm())
    metadata = model_call.metadata or {}
    assert attempts == 2
    assert metadata["provider_request_count"] == 2
    assert metadata["agent_iteration_count"] == 0
