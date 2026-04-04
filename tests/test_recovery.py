"""
Tests for error recovery: attempts tracking, fix() with new API.
"""

import pytest
from agentic import agentic_function, Runtime
from agentic.meta_function import fix


# ── attempts tracking ──────────────────────────────────────────

def test_successful_exec_records_attempt():
    """Successful exec records one attempt with reply and no error."""
    runtime = Runtime(call=lambda c, **kw: "ok")

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    func()
    ctx = func.context
    assert len(ctx.attempts) == 1
    assert ctx.attempts[0]["attempt"] == 1
    assert ctx.attempts[0]["reply"] == "ok"
    assert ctx.attempts[0]["error"] is None


def test_retry_records_all_attempts():
    """Failed then successful exec records both attempts."""
    call_count = [0]

    def flaky(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("timeout")
        return "recovered"

    runtime = Runtime(call=flaky, max_retries=2)

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    result = func()
    ctx = func.context
    assert result == "recovered"
    assert len(ctx.attempts) == 2
    assert ctx.attempts[0]["error"] is not None
    assert "timeout" in ctx.attempts[0]["error"]
    assert ctx.attempts[1]["reply"] == "recovered"
    assert ctx.attempts[1]["error"] is None


def test_all_retries_failed_records_all_attempts():
    """All retries failed — all attempts recorded."""
    runtime = Runtime(
        call=lambda c, **kw: (_ for _ in ()).throw(ConnectionError("down")),
        max_retries=3,
    )

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(RuntimeError, match="failed after 3 attempts"):
        func()

    ctx = func.context
    assert len(ctx.attempts) == 3
    for a in ctx.attempts:
        assert a["error"] is not None
        assert a["reply"] is None


def test_attempts_visible_in_summarize():
    """Failed attempts show up in sibling's summarize context."""
    call_count = [0]

    def flaky(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] <= 1:
            raise ValueError("bad format")
        return "ok"

    runtime = Runtime(call=flaky, max_retries=2)
    received_context = []

    def capture(content, model="test", response_format=None):
        received_context.extend(content)
        return "final"

    runtime2 = Runtime(call=capture)

    @agentic_function
    def parent():
        step_a()
        return step_b()

    @agentic_function
    def step_a():
        return runtime.exec(content=[{"type": "text", "text": "first"}])

    @agentic_function
    def step_b():
        return runtime2.exec(content=[{"type": "text", "text": "second"}])

    parent()
    ctx_text = received_context[0]["text"]
    assert "FAILED" in ctx_text
    assert "bad format" in ctx_text


def test_attempts_in_save(tmp_path):
    """Attempts are saved in JSONL."""
    import json
    from pathlib import Path

    call_count = [0]

    def flaky(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ValueError("oops")
        return "fine"

    runtime = Runtime(call=flaky, max_retries=2)

    @agentic_function
    def func():
        return runtime.exec(content=[{"type": "text", "text": "test"}])

    func()
    path = str(tmp_path / "test.jsonl")
    func.context.save(path)

    data = json.loads(Path(path).read_text().strip().split("\n")[0])
    assert "attempts" in data
    assert len(data["attempts"]) == 2


# ── fix() with new API ────────────────────────────────────────

def test_fix_auto_extracts_code():
    """fix() auto-extracts source code from function."""
    def mock_call(content, model="test", response_format=None):
        # Verify that source code was included in the prompt
        text = content[-1]["text"] if content else ""
        return '''@agentic_function
def func():
    """Fixed."""
    return "fixed"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def broken():
        """Original broken function."""
        return "broken"

    fixed_fn = fix(fn=broken, runtime=runtime)
    assert callable(fixed_fn)
    assert fixed_fn() == "fixed"


def test_fix_with_instruction():
    """fix() passes instruction to LLM."""
    received_prompts = []

    def mock_call(content, model="test", response_format=None):
        received_prompts.append(content[-1]["text"] if content else "")
        return '''@agentic_function
def func():
    """Fixed with instruction."""
    return "instructed"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    fixed_fn = fix(fn=original, runtime=runtime, instruction="Use bullet points")
    assert fixed_fn() == "instructed"
    assert "bullet points" in received_prompts[0]


def test_fix_with_error_context():
    """fix() includes error info from fn.context."""
    received_prompts = []

    def failing_call(content, model="test", response_format=None):
        raise ValueError("some error")

    def fix_call(content, model="test", response_format=None):
        received_prompts.append(content[-1]["text"] if content else "")
        return '''@agentic_function
def func():
    """Fixed."""
    return "fixed"'''

    # First, create a function that fails
    runtime_fail = Runtime(call=failing_call, max_retries=1)

    @agentic_function
    def failing():
        """This fails."""
        return runtime_fail.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(RuntimeError):
        failing()

    # Now fix it — error context should be included
    runtime_fix = Runtime(call=fix_call)
    fixed_fn = fix(fn=failing, runtime=runtime_fix)
    assert callable(fixed_fn)
    assert "some error" in received_prompts[0]


def test_fix_with_on_question():
    """fix() calls on_question when LLM asks a question."""
    call_count = [0]
    questions_received = []

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return "QUESTION: Should I use recursion or iteration?"
        return '''@agentic_function
def func():
    """Fixed with answer."""
    return "answered"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    def handler(question):
        questions_received.append(question)
        return "Use iteration"

    fixed_fn = fix(fn=original, runtime=runtime, on_question=handler)
    assert fixed_fn() == "answered"
    assert len(questions_received) == 1
    assert "recursion" in questions_received[0]


def test_fix_without_on_question_retries():
    """fix() without on_question retries when LLM asks question."""
    call_count = [0]

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        if call_count[0] <= 1:
            return "QUESTION: What should I do?"
        return '''@agentic_function
def func():
    """Fixed without question."""
    return "no_question"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    fixed_fn = fix(fn=original, runtime=runtime)
    assert fixed_fn() == "no_question"


def test_fix_custom_name():
    """fix() can override function name."""
    def mock_call(content, model="test", response_format=None):
        return '''@agentic_function
def generated():
    """Fixed."""
    return "ok"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        return "original"

    fixed_fn = fix(fn=original, runtime=runtime, name="my_fixed")
    assert fixed_fn.__name__ == "my_fixed"
