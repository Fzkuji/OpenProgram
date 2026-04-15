"""
Tests for error recovery: attempts tracking, fix() with new API.
"""

import pytest
from agentic import agentic_function, Runtime
from agentic.meta_functions import fix
from agentic.meta_functions._helpers import clarify


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
    all_text = " ".join(b.get("text", "") for b in received_context)
    assert "FAILED" in all_text
    assert "bad format" in all_text


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


def test_fix_follow_up():
    """fix() returns follow_up when clarify says info is insufficient."""
    def mock_call(content, model="test", response_format=None):
        # clarify returns not-ready with a question
        return '{"ready": false, "question": "Should I use recursion or iteration?"}'

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    result = fix(fn=original, runtime=runtime)
    assert isinstance(result, dict)
    assert result["type"] == "follow_up"
    assert "recursion" in result["question"]


def test_fix_clarify_prompt_omits_generation_suffix():
    """fix() keeps generation-only instructions out of clarify()."""
    prompts = []

    class MysteryCallable:
        __doc__ = str.__doc__

        def __call__(self):
            return "broken"

    def mock_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        return '{"ready": false, "question": "What should change?"}'

    runtime = Runtime(call=mock_call)

    result = fix(
        fn=MysteryCallable(),
        runtime=runtime,
        instruction="Fix the crash when the input is empty.",
    )

    assert isinstance(result, dict)
    assert result["type"] == "follow_up"
    assert "Fix the crash when the input is empty." in prompts[0]
    assert "Source not available for unknown" in prompts[0]
    assert "Fix the root cause" not in prompts[0]
    assert "Respond with ONLY the fixed Python code" not in prompts[0]


def test_clarify_flags_vague_chinese_instruction_without_call():
    """clarify() should short-circuit clearly vague Chinese instructions."""
    call_count = [0]

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        return '{"ready": true}'

    runtime = Runtime(call=mock_call)
    task = (
        "Function: fixed\n\n"
        "Current code:\n```python\nprint('hi')\n```\n\n"
        "Instruction:\n"
        "跟我讨论一下这个"
    )

    result = clarify(task=task, runtime=runtime)
    assert result["ready"] is False
    assert result["question"]
    assert call_count[0] == 0


def test_clarify_treats_answered_qna_as_ready():
    """clarify() should not re-ask once a Q/A clarification block exists."""
    call_count = [0]

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        return '{"ready": false, "question": "Should never be used."}'

    runtime = Runtime(call=mock_call)
    task = (
        "Current code:\n```python\nprint('hi')\n```\n\n"
        "Instruction: improve the prompt\n\n"
        "Q: What should I change?\n"
        "A: Just inspect the code and fix any issues."
    )

    result = clarify(task=task, runtime=runtime)
    assert result == {"ready": True}
    assert call_count[0] == 0


def test_clarify_treats_retry_feedback_as_ready():
    """clarify() should not re-ask when retry feedback is already present."""
    call_count = [0]

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        return '{"ready": false, "question": "Should never be used."}'

    runtime = Runtime(call=mock_call)
    task = (
        "Current code:\n```python\nprint('hi')\n```\n\n"
        "Instruction: improve the prompt\n\n"
        "── Previous attempt feedback ──\n"
        "Need clearer output format."
    )

    result = clarify(task=task, runtime=runtime)
    assert result == {"ready": True}
    assert call_count[0] == 0


def test_fix_produces_code_directly():
    """fix() returns compiled function when LLM produces code."""
    def mock_call(content, model="test", response_format=None):
        return '''```python
@agentic_function
def func():
    """Fixed."""
    return "fixed_result"
```'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    fixed_fn = fix(fn=original, runtime=runtime)
    assert callable(fixed_fn)
    assert fixed_fn() == "fixed_result"


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


def test_fix_uses_docstring_when_source_unavailable(monkeypatch):
    """fix() falls back to docstring when inspect.getsource() is unavailable."""
    prompts = []

    def mock_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        return '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Original docstring."""
        return "original"

    import inspect
    original_getsource = inspect.getsource

    def raising_getsource(fn):
        if fn is original:
            raise OSError("source unavailable")
        return original_getsource(fn)

    monkeypatch.setattr(inspect, "getsource", raising_getsource)

    fixed_fn = fix(fn=original, runtime=runtime)
    assert fixed_fn() == "ok"
    assert "Source not available for original" in prompts[0]
    assert "Original docstring" in prompts[0]


def test_fix_omits_builtin_docstring_bloat():
    """fix() keeps built-in fallbacks concise instead of dumping API docs."""
    prompts = []

    def mock_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        return '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    runtime = Runtime(call=mock_call)

    fixed_fn = fix(fn=str, runtime=runtime)
    assert fixed_fn() == "ok"
    assert "Source not available for str" in prompts[0]
    assert "Create a new string object" not in prompts[0]


def test_fix_omits_api_style_docstring_bloat():
    """fix() keeps source-less API-style callable docs out of the prompt."""
    prompts = []

    class MysteryCallable:
        __doc__ = str.__doc__

        def __call__(self):
            return "broken"

    def mock_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        if len(prompts) == 1:
            return '{"ready": true}'
        return '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    runtime = Runtime(call=mock_call)

    fixed_fn = fix(fn=MysteryCallable(), runtime=runtime, instruction="improve the prompt")
    assert callable(fixed_fn)
    assert fixed_fn() == "ok"
    assert "Source not available for unknown" in prompts[0]
    assert "Create a new string object" not in prompts[0]


def test_fix_uses_follow_up_answer_without_reasking():
    """fix() should continue after an answered follow-up without re-clarifying."""
    from agentic.context import set_ask_user

    call_count = [0]
    prompts = []

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        prompts.append(content[-1]["text"] if content else "")
        if call_count[0] == 1:
            return '{"ready": false, "question": "What output format should I use?"}'
        return '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    set_ask_user(lambda question: "Return plain text.")
    try:
        fixed_fn = fix(fn=original, runtime=runtime)
    finally:
        set_ask_user(None)

    assert callable(fixed_fn)
    assert fixed_fn() == "ok"
    assert call_count[0] == 4  # clarify, generate, verify, conclude
    assert "Q: What output format should I use?" in prompts[1]
    assert "A: Return plain text." in prompts[1]


def test_fix_includes_nested_child_errors():
    """fix() includes child context errors collected from nested attempts."""
    prompts = []

    def flaky(content, model="test", response_format=None):
        raise ValueError("child boom")

    runtime_fail = Runtime(call=flaky, max_retries=1)

    @agentic_function
    def child():
        return runtime_fail.exec(content=[{"type": "text", "text": "hi"}])

    @agentic_function
    def parent():
        return child()

    with pytest.raises(RuntimeError):
        parent()

    def fix_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        return '''@agentic_function
def parent():
    """Fixed parent."""
    return "fixed"'''

    runtime_fix = Runtime(call=fix_call)
    fixed_fn = fix(fn=parent, runtime=runtime_fix)
    assert fixed_fn() == "fixed"
    assert "child attempt 1: FAILED" in prompts[0]
    assert "ValueError: child boom" in prompts[0]
