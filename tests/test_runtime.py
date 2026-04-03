"""
Tests for Runtime class.
"""

import pytest
from agentic import agentic_function, Runtime, get_root_context


def mock_call(content, model="test", response_format=None):
    """Mock LLM: returns a summary of content types received."""
    types = [b["type"] for b in content]
    texts = [b.get("text", "")[:50] for b in content if b["type"] == "text"]
    return f"types={types}, texts={len(texts)}"


def echo_call(content, model="test", response_format=None):
    """Echo the last text block."""
    for block in reversed(content):
        if block["type"] == "text":
            return block["text"]
    return ""


def test_runtime_basic():
    """Runtime.exec() calls the provider and returns reply."""
    rt = Runtime(call=echo_call)

    @agentic_function
    def simple():
        """Simple function."""
        return rt.exec(content=[
            {"type": "text", "text": "hello world"},
        ])

    result = simple()
    assert result == "hello world"


def test_runtime_records_raw_reply():
    """Runtime records raw_reply on Context."""
    rt = Runtime(call=echo_call)

    @agentic_function
    def func():
        """Test."""
        return rt.exec(content=[
            {"type": "text", "text": "test reply"},
        ])

    func()
    root = get_root_context()
    assert root.raw_reply == "test reply"


def test_runtime_context_injection():
    """Runtime prepends execution context to content."""
    received = []

    def capture_call(content, model="test", response_format=None):
        received.extend(content)
        return "ok"

    rt = Runtime(call=capture_call)

    @agentic_function
    def parent():
        """Parent function."""
        return child()

    @agentic_function
    def child():
        """Child function."""
        return rt.exec(content=[
            {"type": "text", "text": "user prompt"},
        ])

    parent()
    # First block should be context (auto-generated), last should be user prompt
    assert len(received) >= 2
    assert received[0]["type"] == "text"
    assert "Execution Context" in received[0]["text"]
    assert received[-1]["text"] == "user prompt"


def test_runtime_no_context_outside_function():
    """Runtime works outside @agentic_function without context."""
    received = []

    def capture_call(content, model="test", response_format=None):
        received.extend(content)
        return "ok"

    rt = Runtime(call=capture_call)
    result = rt.exec(content=[{"type": "text", "text": "bare call"}])
    assert result == "ok"
    assert len(received) == 1  # no context prepended
    assert received[0]["text"] == "bare call"


def test_runtime_double_exec_raises():
    """Calling exec() twice in one function raises RuntimeError."""
    rt = Runtime(call=echo_call)

    @agentic_function
    def double():
        """Bad function."""
        rt.exec(content=[{"type": "text", "text": "first"}])
        rt.exec(content=[{"type": "text", "text": "second"}])

    with pytest.raises(RuntimeError, match="exec.*twice"):
        double()


def test_runtime_model_override():
    """Model can be overridden per-call."""
    models_used = []

    def track_model(content, model="default", response_format=None):
        models_used.append(model)
        return "ok"

    rt = Runtime(call=track_model, model="base-model")

    @agentic_function
    def func():
        return rt.exec(content=[{"type": "text", "text": "test"}], model="override-model")

    func()
    assert models_used[-1] == "override-model"


def test_runtime_default_model():
    """Default model from constructor is used."""
    models_used = []

    def track_model(content, model="default", response_format=None):
        models_used.append(model)
        return "ok"

    rt = Runtime(call=track_model, model="my-model")

    @agentic_function
    def func():
        return rt.exec(content=[{"type": "text", "text": "test"}])

    func()
    assert models_used[-1] == "my-model"


def test_runtime_response_format_passed():
    """response_format is passed to _call."""
    formats_received = []

    def track_format(content, model="test", response_format=None):
        formats_received.append(response_format)
        return '{"ok": true}'

    rt = Runtime(call=track_format)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    @agentic_function
    def func():
        return rt.exec(
            content=[{"type": "text", "text": "test"}],
            response_format=schema,
        )

    func()
    assert formats_received[-1] == schema


def test_runtime_no_call_raises():
    """Runtime without call function raises NotImplementedError."""
    rt = Runtime()

    @agentic_function
    def func():
        return rt.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(NotImplementedError):
        func()


def test_runtime_subclass():
    """Runtime can be subclassed with custom _call."""
    class CustomRuntime(Runtime):
        def _call(self, content, model="default", response_format=None):
            return "custom reply"

    rt = CustomRuntime()

    @agentic_function
    def func():
        return rt.exec(content=[{"type": "text", "text": "test"}])

    result = func()
    assert result == "custom reply"


def test_multiple_runtimes():
    """Multiple Runtime instances can coexist."""
    rt1 = Runtime(call=lambda c, **kw: "from rt1", model="model-1")
    rt2 = Runtime(call=lambda c, **kw: "from rt2", model="model-2")

    @agentic_function
    def parent():
        a = func_a()
        b = func_b()
        return f"{a}, {b}"

    @agentic_function
    def func_a():
        return rt1.exec(content=[{"type": "text", "text": "a"}])

    @agentic_function
    def func_b():
        return rt2.exec(content=[{"type": "text", "text": "b"}])

    result = parent()
    assert "from rt1" in result
    assert "from rt2" in result


def test_content_types():
    """Different content types are passed through."""
    received = []

    def capture(content, model="test", response_format=None):
        received.extend(content)
        return "ok"

    rt = Runtime(call=capture)

    @agentic_function
    def func():
        return rt.exec(content=[
            {"type": "text", "text": "analyze this"},
            {"type": "image", "path": "screenshot.png"},
            {"type": "audio", "path": "recording.wav"},
            {"type": "file", "path": "data.csv"},
        ])

    func()
    # All user content blocks should be present (after the context block)
    all_types = [b["type"] for b in received]
    assert all_types.count("text") >= 2  # context + user text
    assert "image" in all_types
    assert "audio" in all_types
    assert "file" in all_types
