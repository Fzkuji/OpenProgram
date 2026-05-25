"""@agentic_function exit-time FunctionCall persistence.

Verifies:
  - When ``_store`` is installed, decorated function exits cause a
    FunctionCall to be appended.
  - When no store is installed, nothing is written — tree Context
    behaviour is preserved.
  - ``expose='hidden'`` suppresses the FunctionCall node.
  - Error path produces a node with status=error and result.error.
  - ``called_by`` reflects the logical caller (enclosing
    @agentic_function), not chronological predecessor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.store import GraphStoreShim, SessionStore, _store as _store_var


@pytest.fixture
def store(tmp_path: Path):
    """GraphStoreShim installed into ``_store`` for the test's duration."""
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session("s1", agent_id="main")
    s = GraphStoreShim(store, "s1")
    token = _store_var.set(s)
    try:
        yield s
    finally:
        _store_var.reset(token)


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


# ── Basic exit-time append ───────────────────────────────────────


def test_exit_appends_function_call_node(runtime, store):
    @agentic_function
    def add(a, b, runtime=None):
        return a + b

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
    @agentic_function
    def hello(name, runtime=None):
        return f"hi {name}"

    # No store installed — decorated function runs normally.
    result = hello("world", runtime=runtime)
    assert result == "hi world"


# ── expose semantics ─────────────────────────────────────────────


def test_expose_hidden_skips_node(runtime, store):
    @agentic_function(expose="hidden")
    def secret(x, runtime=None):
        return x * 10

    secret(5, runtime=runtime)
    g = store.load()
    assert len([n for n in g if n.is_code()]) == 0


def test_expose_full_recorded_in_metadata(runtime, store):
    @agentic_function(expose="full")
    def transparent(x, runtime=None):
        return x

    transparent(42, runtime=runtime)
    g = store.load()
    fc = next(n for n in g if n.is_code())
    assert fc.metadata.get("expose") == "full"


# ── Error path ────────────────────────────────────────────────────


def test_exception_records_error_node(runtime, store):
    @agentic_function
    def explode(runtime=None):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        explode(runtime=runtime)

    g = store.load()
    fc = next(n for n in g if n.is_code())
    assert fc.function_name == "explode"
    assert fc.metadata.get("status") == "error"
    assert isinstance(fc.result, dict)
    assert "boom" in fc.result["error"]


# ── Nested calls: called_by is the logical caller ────────────────


def test_nested_agentic_functions_chain_in_dag(runtime, store):
    @agentic_function
    def inner(x, runtime=None):
        return x + 1

    @agentic_function
    def outer(x, runtime=None):
        a = inner(x, runtime=runtime)
        b = inner(a, runtime=runtime)
        return b

    result = outer(10, runtime=runtime)
    assert result == 12

    g = store.load()
    fcs = [n for n in g if n.is_code()]
    # Two inner + one outer = three nodes
    names = [n.function_name for n in fcs]
    assert names.count("inner") == 2
    assert names.count("outer") == 1

    outer_fc = next(n for n in fcs if n.function_name == "outer")
    # Top-level call has no enclosing function
    assert outer_fc.called_by == ""

    # Both inner calls are made from within outer's body → their
    # logical caller is outer, regardless of chronological order.
    inner_fcs = [n for n in fcs if n.function_name == "inner"]
    for fc in inner_fcs:
        assert fc.called_by == outer_fc.id


# ── Entry-append / exit-update lifecycle ────────────────────────


def test_entry_appends_running_node_visible_mid_execution(runtime, store):
    """While the function is running, its placeholder should already be
    in the DAG with output=None / status='running' — observers can see
    in-flight calls."""
    seen_during_call: list = []

    @agentic_function
    def slow(runtime=None):
        # Inside the body: capture DAG. Wrapper already appended a
        # placeholder for `slow`, so we should see it here.
        g = store.load()
        slow_nodes = [n for n in g if n.is_code() and n.name == "slow"]
        seen_during_call.extend(slow_nodes)
        return "ok"

    slow(runtime=runtime)

    assert len(seen_during_call) == 1
    placeholder = seen_during_call[0]
    assert placeholder.output is None
    assert placeholder.metadata.get("status") == "running"


def test_exit_updates_output_in_place(runtime, store):
    """After the function returns, the same node's output gets filled
    (no second node) and status flips to 'success'."""
    @agentic_function
    def double(x, runtime=None):
        return x * 2

    double(7, runtime=runtime)
    g = store.load()
    code_nodes = [n for n in g if n.is_code()]
    assert len(code_nodes) == 1                        # NOT 2 (entry+exit)
    n = code_nodes[0]
    assert n.output == 14
    assert n.metadata.get("status") == "success"
    assert n.metadata.get("duration_seconds") is not None


def test_exception_updates_to_error_in_place(runtime, store):
    @agentic_function
    def explode(runtime=None):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        explode(runtime=runtime)

    g = store.load()
    code_nodes = [n for n in g if n.is_code()]
    assert len(code_nodes) == 1
    n = code_nodes[0]
    assert isinstance(n.output, dict)
    assert "boom" in n.output["error"]
    assert n.metadata.get("status") == "error"


# ── Multi-call chronological ordering ────────────────────────────


def test_top_level_sibling_calls_have_empty_called_by(runtime, store):
    """Two sibling top-level calls both have called_by="" because
    neither has an enclosing @agentic_function on the call stack."""
    @agentic_function
    def one(runtime=None):
        return 1

    @agentic_function
    def two(runtime=None):
        return 2

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
    assert fcs[0].called_by == ""
    assert fcs[1].called_by == ""
