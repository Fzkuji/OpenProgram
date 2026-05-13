"""
Tests for meta.create() — generating agentic functions from descriptions.
"""

import pytest
from openprogram import agentic_function, Runtime
from openprogram.programs.functions.meta import create, edit
from openprogram.programs.functions.meta.validation.sandbox import _make_safe_builtins
from openprogram.programs.functions.meta.generation.extract_code import extract_code as _extract_code


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


def test_extract_code_keeps_leading_imports():
    """Extracted code keeps allowed imports that appear before the decorator."""
    response = 'Sure — use json.\n\nimport json\n\n@agentic_function\ndef greet(name):\n    return json.dumps({"name": name})'
    code = _extract_code(response)
    assert code.startswith("import json")
    assert "@agentic_function" in code
    assert "json.dumps" in code


# ── Safety tests ───────────────────────────────────────────────

def test_safe_builtins_blocks_import():
    """Safe builtins block disallowed imports."""
    safe = _make_safe_builtins()
    with pytest.raises(ImportError, match="not allowed"):
        safe["__import__"]("subprocess")


def test_safe_builtins_allows_basics():
    """Safe builtins include common operations."""
    safe = _make_safe_builtins()
    assert safe["len"]([1, 2, 3]) == 3
    assert safe["str"](42) == "42"
    assert safe["int"]("5") == 5


def test_safe_builtins_allow_whitelisted_stdlib_imports():
    """Safe builtins still allow a curated set of stdlib imports."""
    safe = _make_safe_builtins()
    json_mod = safe["__import__"]("json")
    assert json_mod.loads('{"ok": true}') == {"ok": True}


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
            # First call: clarify — return ready
            return '{"ready": true}'
        elif call_count[0] == 2:
            # Second call: generate the function code
            return '''@agentic_function
def summarize(text):
    """Summarize the given text into a short sentence."""
    return runtime.exec(content=[
        {"type": "text", "text": "Summarize in one sentence: " + text},
    ])'''
        else:
            # Subsequent calls: the generated function calling runtime
            return "This is a summary."

    # create() only runs clarify when an ask_user handler is reachable
    # (otherwise the clarify follow-up would bounce off None). Register
    # a no-op handler so clarify fires and consumes mock call #1 the
    # way this test expects.
    from openprogram.programs.functions.buildin.ask_user import set_ask_user
    set_ask_user(lambda question: "")
    try:
        runtime = Runtime(call=mock_call)
        fn = create(description="Summarize text", runtime=runtime)

        result = fn(text="Long article about AI...")
        assert result == "This is a summary."
        assert call_count[0] == 3  # 1 for clarify, 1 for create, 1 for the function call
    finally:
        set_ask_user(None)


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
    """create() blocks disallowed imports."""
    def mock_call(content, model="test", response_format=None):
        return '''import subprocess
@agentic_function
def evil():
    """Evil function."""
    return subprocess.check_output("whoami")'''

    runtime = Runtime(call=mock_call)
    with pytest.raises(ValueError, match="not allowed"):
        create(description="evil", runtime=runtime)


def test_create_allows_whitelisted_stdlib_imports():
    """create() may use explicitly allowed standard-library imports."""
    def mock_call(content, model="test", response_format=None):
        return '''import json

@agentic_function
def as_json(value):
    """Return a tiny JSON object."""
    return json.dumps({"value": value})'''

    runtime = Runtime(call=mock_call)
    fn = create(description="Return a JSON object", runtime=runtime)

    assert fn(value="x") == '{"value": "x"}'


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


# ── edit() tests (new API: fn-based) ────────────────────────────

def test_edit_rewrites_function():
    """edit() auto-extracts code and fixes the function."""
    from tests._edit_test_helpers import make_edit_mock

    code = '''@agentic_function
def fixed_add(a, b):
    """Add two numbers correctly."""
    return str(int(a) + int(b))'''

    mock_call, cleanup = make_edit_mock(code)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def broken(a, b):
            """Add two numbers."""
            return 1 / 0  # broken

        fn = edit(fn=broken, runtime=runtime)
        assert callable(fn)
        assert fn(a="2", b="3") == "5"
    finally:
        cleanup()


def test_edit_with_custom_name():
    """edit() can override the function name."""
    from tests._edit_test_helpers import make_edit_mock

    code = '''@agentic_function
def repaired():
    """Fixed function."""
    return "fixed"'''

    mock_call, cleanup = make_edit_mock(code)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def broken():
            """Do something."""
            raise Exception()

        fn = edit(fn=broken, runtime=runtime, name="my_fixed_fn")
        assert fn.__name__ == "my_fixed_fn"
        assert fn() == "fixed"
    finally:
        cleanup()


# ── retry tests ──────────────────────────────────────────────

def test_runtime_retry_succeeds_on_second_attempt():
    """Runtime retries once and succeeds."""
    call_count = [0]

    def flaky_call(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("network error")
        return "success on retry"

    runtime = Runtime(call=flaky_call, max_retries=2)

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    result = func()
    assert result == "success on retry"
    assert call_count[0] == 2


def test_runtime_retry_exhausted_raises():
    """Runtime raises after all retries exhausted."""
    def always_fail(content, model="test", response_format=None):
        raise ConnectionError("network error")

    runtime = Runtime(call=always_fail, max_retries=2)

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        func()


def test_runtime_retry_error_report():
    """Retry error report contains all attempt details."""
    attempt = [0]

    def varied_fail(content, model="test", response_format=None):
        attempt[0] += 1
        if attempt[0] == 1:
            raise ValueError("bad input")
        raise TimeoutError("too slow")

    runtime = Runtime(call=varied_fail, max_retries=2)

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(RuntimeError, match="bad input") as exc_info:
        func()
    assert "too slow" in str(exc_info.value)
