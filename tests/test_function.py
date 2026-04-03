"""
Tests for @agentic_function decorator.
"""

import pytest
from agentic import agentic_function, get_root_context
from agentic.context import _current_ctx


def test_basic_function():
    """Decorated function executes and returns normally."""
    @agentic_function
    def greet(name):
        """Say hello."""
        return f"Hello, {name}!"

    result = greet(name="Alice")
    assert result == "Hello, Alice!"


def test_context_tree():
    """Decorated function creates a Context tree."""
    @agentic_function
    def outer():
        """Outer function."""
        inner()
        return "done"

    @agentic_function
    def inner():
        """Inner function."""
        return "inner done"

    outer()
    root = get_root_context()
    assert root.name == "outer"
    assert root.status == "success"
    assert root.output == "done"
    assert len(root.children) == 1
    assert root.children[0].name == "inner"
    assert root.children[0].output == "inner done"


def test_params_recorded():
    """Function parameters are recorded in Context."""
    @agentic_function
    def observe(task, detail=True):
        """Observe the screen."""
        return "observed"

    observe(task="find button", detail=False)
    root = get_root_context()
    assert root.params == {"task": "find button", "detail": False}


def test_error_recorded():
    """Errors are recorded in Context."""
    @agentic_function
    def failing():
        """This will fail."""
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        failing()

    root = get_root_context()
    assert root.status == "error"
    assert "test error" in root.error


def test_timing():
    """Duration is recorded."""
    @agentic_function
    def quick():
        """Quick function."""
        return "fast"

    quick()
    root = get_root_context()
    assert root.duration_ms >= 0


def test_nested_three_levels():
    """Three levels of nesting work correctly."""
    @agentic_function
    def level1():
        """Level 1."""
        return level2()

    @agentic_function
    def level2():
        """Level 2."""
        return level3()

    @agentic_function
    def level3():
        """Level 3."""
        return "deep"

    result = level1()
    assert result == "deep"
    root = get_root_context()
    assert root.name == "level1"
    assert root.children[0].name == "level2"
    assert root.children[0].children[0].name == "level3"


def test_multiple_children():
    """Multiple children are recorded in order."""
    @agentic_function
    def parent():
        """Parent."""
        child_a()
        child_b()
        child_c()
        return "done"

    @agentic_function
    def child_a():
        return "a"

    @agentic_function
    def child_b():
        return "b"

    @agentic_function
    def child_c():
        return "c"

    parent()
    root = get_root_context()
    assert len(root.children) == 3
    assert [c.name for c in root.children] == ["child_a", "child_b", "child_c"]
    assert [c.output for c in root.children] == ["a", "b", "c"]


def test_compress_flag():
    """compress=True is stored on the Context node."""
    @agentic_function(compress=True)
    def compressed():
        """Compressed function."""
        return "done"

    compressed()
    root = get_root_context()
    assert root.compress is True


def test_docstring_as_prompt():
    """Docstring is stored as prompt."""
    @agentic_function
    def my_func():
        """This is the prompt."""
        return "ok"

    my_func()
    root = get_root_context()
    assert root.prompt == "This is the prompt."


def test_no_docstring():
    """Function without docstring works."""
    @agentic_function
    def no_doc():
        return "ok"

    no_doc()
    root = get_root_context()
    assert root.prompt == ""


def test_context_cleared_after_top_level():
    """Context is cleared after top-level function completes."""
    @agentic_function
    def top():
        return "done"

    top()
    assert _current_ctx.get(None) is None


def test_separate_trees():
    """Each top-level call creates a separate tree."""
    @agentic_function
    def task_a():
        return "a"

    @agentic_function
    def task_b():
        return "b"

    task_a()
    root_a = get_root_context()

    task_b()
    root_b = get_root_context()

    assert root_a.name == "task_a"
    assert root_b.name == "task_b"
