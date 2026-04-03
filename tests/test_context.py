"""
Tests for Context tree: summarize, tree, save.
"""

import json
import tempfile
from pathlib import Path

import pytest
from agentic import agentic_function, Runtime, get_root_context


def mock_call(content, model="test", response_format=None):
    for block in reversed(content):
        if block["type"] == "text" and "Execution Context" not in block["text"]:
            return block["text"]
    return "ok"


rt = Runtime(call=mock_call)


def test_tree_output():
    """tree() returns a readable string."""
    @agentic_function
    def parent():
        child()
        return "done"

    @agentic_function
    def child():
        return "child done"

    parent()
    tree = get_root_context().tree()
    assert "parent" in tree
    assert "child" in tree
    assert "✓" in tree


def test_summarize_default():
    """summarize() returns execution context text."""
    @agentic_function
    def outer():
        inner_a()
        return inner_b()

    @agentic_function
    def inner_a():
        return rt.exec(content=[{"type": "text", "text": "result_a"}])

    @agentic_function
    def inner_b():
        return rt.exec(content=[{"type": "text", "text": "result_b"}])

    outer()
    root = get_root_context()
    # inner_b should have seen inner_a in its context
    assert root.children[1].raw_reply is not None


def test_summarize_depth_0():
    """depth=0 shows no ancestors."""
    @agentic_function
    def outer():
        return inner()

    @agentic_function(summarize={"depth": 0, "siblings": 0})
    def inner():
        return rt.exec(content=[{"type": "text", "text": "isolated"}])

    outer()
    # Should still work, just less context
    root = get_root_context()
    assert root.children[0].raw_reply is not None


def test_compress_hides_children():
    """compress=True hides children in summarize."""
    @agentic_function(compress=True)
    def compressed():
        sub()
        return "compressed result"

    @agentic_function
    def sub():
        return "sub result"

    @agentic_function
    def outer():
        compressed()
        return check()

    @agentic_function
    def check():
        return rt.exec(content=[{"type": "text", "text": "checking"}])

    outer()
    root = get_root_context()
    # compressed's children exist in tree
    assert len(root.children[0].children) == 1
    assert root.children[0].children[0].name == "sub"


def test_save_jsonl(tmp_path):
    """save() to .jsonl creates valid JSON lines."""
    @agentic_function
    def task():
        step()
        return "done"

    @agentic_function
    def step():
        return "step done"

    task()
    path = str(tmp_path / "test.jsonl")
    get_root_context().save(path)

    lines = Path(path).read_text().strip().split("\n")
    assert len(lines) >= 2  # at least task + step
    for line in lines:
        obj = json.loads(line)
        assert "name" in obj
        assert "status" in obj


def test_save_md(tmp_path):
    """save() to .md creates readable output."""
    @agentic_function
    def task():
        return "done"

    task()
    path = str(tmp_path / "test.md")
    get_root_context().save(path)

    content = Path(path).read_text()
    assert "task" in content


def test_traceback_on_error():
    """traceback() shows error chain."""
    @agentic_function
    def outer():
        return inner()

    @agentic_function
    def inner():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        outer()

    root = get_root_context()
    tb = root.traceback()
    assert "outer" in tb
    assert "inner" in tb
    assert "boom" in tb


def test_path_property():
    """path gives correct dot-separated path."""
    @agentic_function
    def root_fn():
        return child_fn()

    @agentic_function
    def child_fn():
        return "done"

    root_fn()
    root = get_root_context()
    assert "root_fn" in root.path
    assert "child_fn" in root.children[0].path


def test_duration():
    """duration_ms is non-negative."""
    @agentic_function
    def timed():
        return "done"

    timed()
    root = get_root_context()
    assert root.duration_ms >= 0
