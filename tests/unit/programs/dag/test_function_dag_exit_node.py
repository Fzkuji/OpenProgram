"""Agent method exit-time FunctionCall persistence.

Verifies:
  - When ``_store`` is installed, decorated function exits cause a
    FunctionCall to be appended.
  - When no store is installed, nothing is written — tree Context
    behaviour is preserved.
  - ``expose='hidden'`` suppresses the FunctionCall node.
  - Error path produces a node with status=error and result.error.
  - ``caller`` reflects the logical caller (enclosing
    Agent method), not chronological predecessor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.runtime.agent import Agent
from openprogram.runtime.agent.runtime import Runtime
from openprogram.state.store import SessionNodeWriter, SessionStore, _store as _store_var


@pytest.fixture
def store(tmp_path: Path):
    """SessionNodeWriter installed into ``_store`` for the test's duration."""
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = SessionNodeWriter(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


# Basic exit-time append


def test_exit_appends_function_call_node(runtime, store):
    class _AddAgent(Agent):
        method_options = {'add': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'add'}}

        def add(self, a, b, runtime=None):
            return a + b

    add = _AddAgent().add

    result = add(2, 3, runtime=runtime)
    assert result == 5

    g = store.load()
    fc_nodes = [n for n in g if n.is_code()]
    assert len(fc_nodes) == 1
    fc = fc_nodes[0]
    assert fc.function_name == "add"
    assert fc.result == 5
    # Arguments captured; runtime arg replaced with a tag string
    assert fc.arguments["a"] == 2
    assert fc.arguments["b"] == 3
    assert fc.arguments["runtime"].startswith("<")


def test_no_store_installed_means_no_dag_write(runtime, tmp_path):
    """Without an installed ``_store``, decorated functions still run
    but no DAG nodes are written."""
    class _HelloAgent(Agent):
        method_options = {'hello': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'hello'}}

        def hello(self, name, runtime=None):
            return f"hi {name}"

    hello = _HelloAgent().hello

    # No store installed — decorated function runs normally.
    result = hello("world", runtime=runtime)
    assert result == "hi world"


# expose semantics


def test_expose_hidden_skips_node(runtime, store):
    class _SecretAgent(Agent):
        method_options = {'secret': {'expose': "hidden", 'tool': True, 'capture_io': True, 'name': 'secret'}}

        def secret(self, x, runtime=None):
            return x * 10

    secret = _SecretAgent().secret

    secret(5, runtime=runtime)
    g = store.load()
    assert len([n for n in g if n.is_code()]) == 0


def test_expose_full_recorded_in_metadata(runtime, store):
    class _TransparentAgent(Agent):
        method_options = {'transparent': {'expose': "full", 'tool': True, 'capture_io': True, 'name': 'transparent'}}

        def transparent(self, x, runtime=None):
            return x

    transparent = _TransparentAgent().transparent

    transparent(42, runtime=runtime)
    g = store.load()
    fc = next(n for n in g if n.is_code())
    assert fc.metadata.get("expose") == "full"


# Error path


def test_exception_records_error_node(runtime, store):
    class _ExplodeAgent(Agent):
        method_options = {'explode': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'explode'}}

        def explode(self, runtime=None):
            raise RuntimeError("boom")

    explode = _ExplodeAgent().explode

    with pytest.raises(RuntimeError, match="boom"):
        explode(runtime=runtime)

    g = store.load()
    fc = next(n for n in g if n.is_code())
    assert fc.function_name == "explode"
    assert fc.metadata.get("status") == "error"
    assert isinstance(fc.result, dict)
    assert "boom" in fc.result["error"]


# Nested calls: caller is the logical caller


def test_nested_agent_methods_chain_in_dag(runtime, store):
    class _InnerAgent(Agent):
        method_options = {'inner': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'inner'}}

        def inner(self, x, runtime=None):
            return x + 1

    inner = _InnerAgent().inner

    class _OuterAgent(Agent):
        method_options = {'outer': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'outer'}}

        def outer(self, x, runtime=None):
            a = inner(x, runtime=runtime)
            b = inner(a, runtime=runtime)
            return b

    outer = _OuterAgent().outer

    result = outer(10, runtime=runtime)
    assert result == 12

    g = store.load()
    fcs = [n for n in g if n.is_code()]
    outer_fc = next(n for n in fcs if n.function_name == "outer")
    links = [n for n in fcs if n.metadata.get('child_session_id')]
    assert len(links) == 2
    assert not any(n.function_name == 'inner' for n in fcs)
    assert outer_fc.caller == ''
    for link in links:
        assert link.caller == outer_fc.id
        child = SessionNodeWriter(store.store, link.metadata['child_session_id']).load()
        inner_fc = next(n for n in child if n.function_name == 'inner')
        assert not inner_fc.caller
        assert inner_fc.output == link.output


# Entry-append / exit-update lifecycle


def test_entry_appends_running_node_visible_mid_execution(runtime, store):
    """While the function is running, its placeholder should already be
    in the DAG with output=None / status='running' — observers can see
    in-flight calls."""
    seen_during_call: list = []

    class _SlowAgent(Agent):
        method_options = {'slow': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'slow'}}

        def slow(self, runtime=None):
            # Inside the body: capture DAG. Wrapper already appended a
            # placeholder for `slow`, so we should see it here.
            g = store.load()
            slow_nodes = [n for n in g if n.is_code() and n.name == "slow"]
            seen_during_call.extend(slow_nodes)
            return "ok"

    slow = _SlowAgent().slow

    slow(runtime=runtime)

    assert len(seen_during_call) == 1
    placeholder = seen_during_call[0]
    assert placeholder.output is None
    assert placeholder.metadata.get("status") == "running"


def test_exit_updates_output_in_place(runtime, store):
    """After the function returns, the same node's output gets filled
    (no second node) and status flips to 'completed' (unified vocabulary,
    dag/overview.md decision 2)."""
    class _DoubleAgent(Agent):
        method_options = {'double': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'double'}}

        def double(self, x, runtime=None):
            return x * 2

    double = _DoubleAgent().double

    double(7, runtime=runtime)
    g = store.load()
    code_nodes = [n for n in g if n.is_code()]
    assert len(code_nodes) == 1                        # NOT 2 (entry+exit)
    n = code_nodes[0]
    assert n.output == 14
    assert n.metadata.get("status") == "completed"
    assert n.metadata.get("duration_seconds") is not None


def test_exception_updates_to_error_in_place(runtime, store):
    class _ExplodeAgent(Agent):
        method_options = {'explode': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'explode'}}

        def explode(self, runtime=None):
            raise RuntimeError("boom")

    explode = _ExplodeAgent().explode

    with pytest.raises(RuntimeError):
        explode(runtime=runtime)

    g = store.load()
    code_nodes = [n for n in g if n.is_code()]
    assert len(code_nodes) == 1
    n = code_nodes[0]
    assert isinstance(n.output, dict)
    assert "boom" in n.output["error"]
    assert n.metadata.get("status") == "error"


# Multi-call chronological ordering


def test_top_level_sibling_calls_have_empty_caller(runtime, store):
    """Two sibling top-level calls both have caller="" because
    neither has an enclosing Agent method on the call stack."""
    class _OneAgent(Agent):
        method_options = {'one': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'one'}}

        def one(self, runtime=None):
            return 1

    one = _OneAgent().one

    class _TwoAgent(Agent):
        method_options = {'two': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'two'}}

        def two(self, runtime=None):
            return 2

    two = _TwoAgent().two

    one(runtime=runtime)
    two(runtime=runtime)

    g = store.load()
    fcs = sorted(
        (n for n in g if n.is_code()),
        key=lambda n: n.created_at,
    )
    assert fcs[0].function_name == "one"
    assert fcs[1].function_name == "two"
    # Both top-level → neither has a caller
    assert fcs[0].caller == ""
    assert fcs[1].caller == ""
