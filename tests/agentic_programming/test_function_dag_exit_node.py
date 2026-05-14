"""@agentic_function exit-time FunctionCall persistence.

Verifies:
  - When Runtime has a GraphStore attached, decorated function exits
    cause a FunctionCall to be appended.
  - When no store is attached (default), nothing is written — tree
    Context behaviour is preserved.
  - expose='hidden' suppresses the FunctionCall node.
  - error path produces a node with status=error and result.error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime
from openprogram.context.nodes import FunctionCall
from openprogram.context.storage import GraphStore, init_db


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db = tmp_path / "x.sqlite"
    init_db(db)
    s = GraphStore(db, "s1")
    s.create_session_row()
    return s


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *a, **kw: "", model="dummy")


# ── Basic exit-time append ───────────────────────────────────────


def test_exit_appends_function_call_node(runtime, store):
    runtime.attach_store(store)

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


def test_no_store_attached_means_no_dag_write(runtime):
    @agentic_function
    def hello(name, runtime=None):
        return f"hi {name}"

    # No attach_store → standalone mode
    result = hello("world", runtime=runtime)
    assert result == "hi world"
    # runtime.head_id stays None
    assert runtime.head_id is None
    assert runtime.store is None


# ── expose semantics ─────────────────────────────────────────────


def test_expose_hidden_skips_node(runtime, store):
    runtime.attach_store(store)

    @agentic_function(expose="hidden")
    def secret(x, runtime=None):
        return x * 10

    secret(5, runtime=runtime)
    g = store.load()
    assert len([n for n in g if n.is_code()]) == 0


def test_expose_full_recorded_in_metadata(runtime, store):
    runtime.attach_store(store)

    @agentic_function(expose="full")
    def transparent(x, runtime=None):
        return x

    transparent(42, runtime=runtime)
    g = store.load()
    fc = next(n for n in g if n.is_code())
    assert fc.metadata.get("expose") == "full"


# ── Error path ────────────────────────────────────────────────────


def test_exception_records_error_node(runtime, store):
    runtime.attach_store(store)

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


# ── Nested calls: chain is preserved ─────────────────────────────


def test_nested_agentic_functions_chain_in_dag(runtime, store):
    runtime.attach_store(store)

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

    # outer's FunctionCall called_by is whatever head_id was when
    # outer was entered — should be empty (no prior nodes).
    outer_fc = next(n for n in fcs if n.function_name == "outer")
    assert outer_fc.called_by == ""

    # Each inner's called_by is the head right before it started —
    # the first inner had no prior nodes either (outer hadn't appended
    # yet), but the second inner sees the first inner's FunctionCall id.
    inner_fcs = [n for n in fcs if n.function_name == "inner"]
    second_inner = inner_fcs[-1]  # appended last among inners
    first_inner = inner_fcs[0]
    assert second_inner.called_by == first_inner.id


# ── Entry-append / exit-update lifecycle ────────────────────────


def test_entry_appends_running_node_visible_mid_execution(runtime, store):
    """While the function is running, its placeholder should already be
    in the DAG with output=None / status='running' — observers can see
    in-flight calls."""
    runtime.attach_store(store)
    seen_during_call: list = []

    @agentic_function
    def slow(runtime=None):
        # Inside the body: snapshot DAG. Wrapper already appended a
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
    runtime.attach_store(store)

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
    runtime.attach_store(store)

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


def test_predecessor_chain_chronological(runtime, store):
    runtime.attach_store(store)

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
    # Second's predecessor points at first.
    assert fcs[1].called_by == fcs[0].id
