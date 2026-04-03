"""
Tests for meta.create() — generating agentic functions from descriptions.
"""

import pytest
from agentic import Runtime
from agentic.meta import create, _extract_code, _make_safe_builtins


# ── _extract_code tests ────────────────────────────────────────

def test_extract_code_markdown():
    """Extracts code from markdown fences."""
    response = '```python\n@agentic_function\ndef greet(name):\n    return name\n```'
    code = _extract_code(response)
    assert "@agentic_function" in code
    assert "def greet" in code


def test_extract_code_bare():
    """Extracts code without markdown fences."""
    response = '@agentic_function\ndef greet(name):\n    return name'
    code = _extract_code(response)
    assert "def greet" in code


def test_extract_code_with_explanation():
    """Extracts code when LLM adds explanation before/after."""
    response = 'Here is the function:\n\n```python\n@agentic_function\ndef greet(name):\n    return name\n```\n\nThis function greets.'
    code = _extract_code(response)
    assert "def greet" in code
    assert "Here is" not in code
    assert "This function" not in code


# ── Safety tests ───────────────────────────────────────────────

def test_safe_builtins_blocks_import():
    """Safe builtins block __import__."""
    safe = _make_safe_builtins()
    with pytest.raises(ImportError, match="not allowed"):
        safe["__import__"]("os")


def test_safe_builtins_allows_basics():
    """Safe builtins include common operations."""
    safe = _make_safe_builtins()
    assert safe["len"]([1, 2, 3]) == 3
    assert safe["str"](42) == "42"
    assert safe["int"]("5") == 5


# ── create() with mock LLM ────────────────────────────────────

def test_create_simple_function():
    """create() generates a working agentic function."""
    # Mock LLM that returns a hardcoded function
    def mock_call(content, model="test", response_format=None):
        return '''@agentic_function
def add_numbers(a, b):
    """Add two numbers together."""
    return str(int(a) + int(b))'''

    runtime = Runtime(call=mock_call)
    fn = create(description="Add two numbers", runtime=runtime)

    assert callable(fn)
    result = fn(a="3", b="5")
    assert result == "8"


def test_create_function_with_runtime():
    """create() generates a function that uses runtime.exec()."""
    call_count = [0]

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: generate the function code
            return '''@agentic_function
def summarize(text):
    """Summarize the given text into a short sentence."""
    return runtime.exec(content=[
        {"type": "text", "text": "Summarize in one sentence: " + text},
    ])'''
        else:
            # Subsequent calls: the generated function calling runtime
            return "This is a summary."

    runtime = Runtime(call=mock_call)
    fn = create(description="Summarize text", runtime=runtime)

    result = fn(text="Long article about AI...")
    assert result == "This is a summary."
    assert call_count[0] == 2  # 1 for create, 1 for the function call


def test_create_invalid_code():
    """create() raises SyntaxError for invalid code."""
    def mock_call(content, model="test", response_format=None):
        return "def broken(:\n    pass"

    runtime = Runtime(call=mock_call)
    with pytest.raises(SyntaxError):
        create(description="broken", runtime=runtime)


def test_create_no_function():
    """create() raises ValueError if no @agentic_function found."""
    def mock_call(content, model="test", response_format=None):
        return "x = 42"

    runtime = Runtime(call=mock_call)
    with pytest.raises(ValueError, match="does not contain"):
        create(description="nothing", runtime=runtime)


def test_create_blocks_import():
    """create() blocks generated code that tries to import."""
    def mock_call(content, model="test", response_format=None):
        return '''import os
@agentic_function
def evil():
    """Evil function."""
    return os.getcwd()'''

    runtime = Runtime(call=mock_call)
    with pytest.raises(ValueError, match="import"):
        create(description="evil", runtime=runtime)


def test_create_custom_name():
    """create() can override the function name."""
    def mock_call(content, model="test", response_format=None):
        return '''@agentic_function
def generated():
    """Do something."""
    return "ok"'''

    runtime = Runtime(call=mock_call)
    fn = create(description="do something", runtime=runtime, name="my_custom_fn")

    assert fn.__name__ == "my_custom_fn"
    assert fn() == "ok"
