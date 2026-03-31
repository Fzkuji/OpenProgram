"""
Tests for the Context class.
"""

import pytest
from harness.context import Context, Frame, LogEntry


def test_context_dict_compatible():
    """Context works like a dict."""
    ctx = Context(task="test task")
    assert ctx["task"] == "test task"
    ctx["result"] = {"data": 123}
    assert ctx["result"]["data"] == 123
    assert "result" in ctx
    assert "missing" not in ctx
    assert ctx.get("missing", "default") == "default"


def test_context_call_stack():
    """Push and pop frames like Python's call stack."""
    ctx = Context(task="test")

    assert ctx.depth == 0
    assert ctx.current_frame is None

    ctx.push("programmer", "observe", reason="check screen")
    assert ctx.depth == 1
    assert ctx.current_frame.function == "observe"
    assert ctx.current_frame.caller == "programmer"

    ctx.push("programmer", "learn", reason="plan action")
    assert ctx.depth == 2
    assert ctx.current_frame.function == "learn"

    entry = ctx.pop(status="success", output={"action": "click"})
    assert ctx.depth == 1
    assert entry.function == "learn"
    assert entry.status == "success"

    ctx.pop(status="success")
    assert ctx.depth == 0


def test_context_pop_empty_raises():
    """Popping empty stack raises RuntimeError."""
    ctx = Context(task="test")
    with pytest.raises(RuntimeError):
        ctx.pop()


def test_context_log():
    """Each pop creates a log entry."""
    ctx = Context(task="test")

    ctx.push("programmer", "observe", reason="look")
    ctx.pop(status="success", output={"state": "homepage"})

    ctx.push("programmer", "act", reason="click")
    ctx.pop(status="error", error="element not found")

    assert len(ctx.log) == 2
    assert ctx.log[0].function == "observe"
    assert ctx.log[0].status == "success"
    assert ctx.log[1].function == "act"
    assert ctx.log[1].status == "error"
    assert ctx.log[1].error == "element not found"


def test_context_scope_for():
    """scope_for returns only declared params."""
    ctx = Context(task="click login")
    ctx["observe"] = {"state": "homepage"}
    ctx["secret"] = "should not leak"

    scoped = ctx.scope_for(params=["task", "observe"])
    assert "task" in scoped
    assert "observe" in scoped
    assert "secret" not in scoped


def test_context_scope_for_none_returns_all():
    """scope_for(None) returns everything."""
    ctx = Context(task="test")
    ctx["data"] = 123
    scoped = ctx.scope_for(params=None)
    assert "task" in scoped
    assert "data" in scoped


def test_context_scope_includes_call_stack():
    """Scoped context includes call stack summary."""
    ctx = Context(task="test")
    ctx.push("programmer", "observe", reason="look around")
    scoped = ctx.scope_for(params=["task"])
    assert "_call_stack" in scoped
    assert "programmer → observe()" in scoped["_call_stack"][0]


def test_context_to_dict():
    """to_dict exports raw data."""
    ctx = Context(task="test")
    ctx["result"] = "ok"
    d = ctx.to_dict()
    assert isinstance(d, dict)
    assert d["task"] == "test"
    assert d["result"] == "ok"


def test_log_entry_str():
    """LogEntry has readable string representation."""
    entry = LogEntry(
        function="observe",
        caller="programmer",
        reason="check screen",
        depth=0,
        status="success",
        duration_ms=150.5,
    )
    s = str(entry)
    assert "observe()" in s
    assert "150ms" in s
    assert "programmer" in s
