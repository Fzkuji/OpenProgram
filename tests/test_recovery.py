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
# New fix() always asks a follow-up on round 0 (forced clarify).
# Tests must register ask_user handler via set_ask_user().

def test_fix_auto_extracts_code():
    """fix() auto-extracts source code from function."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def func():
    """Fixed."""
    return "fixed"'''

    mock_call, cleanup = make_fix_mock(code)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def broken():
            """Original broken function."""
            return "broken"

        fixed_fn = fix(fn=broken, runtime=runtime)
        assert callable(fixed_fn)
        assert fixed_fn() == "fixed"
    finally:
        cleanup()


def test_fix_with_instruction():
    """fix() passes instruction to LLM."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def func():
    """Fixed with instruction."""
    return "instructed"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def original():
            """Do something."""
            return "original"

        fixed_fn = fix(fn=original, runtime=runtime, instruction="Use bullet points")
        assert fixed_fn() == "instructed"
        # Instruction should appear in clarify prompt (call 1)
        assert any("bullet points" in p for p in prompts)
    finally:
        cleanup()


def test_fix_with_error_context():
    """fix() includes error info from fn.context."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def func():
    """Fixed."""
    return "fixed"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)

    def failing_call(content, model="test", response_format=None):
        raise ValueError("some error")

    # First, create a function that fails
    runtime_fail = Runtime(call=failing_call, max_retries=1)

    @agentic_function
    def failing():
        """This fails."""
        return runtime_fail.exec(content=[{"type": "text", "text": "test"}])

    with pytest.raises(RuntimeError):
        failing()

    # Now fix it — error context should be included
    try:
        runtime_fix = Runtime(call=mock_call)
        fixed_fn = fix(fn=failing, runtime=runtime_fix)
        assert callable(fixed_fn)
        assert any("some error" in p for p in prompts)
    finally:
        cleanup()


def test_fix_follow_up_returns_question_text():
    """fix() returns question as plain text when no handler is registered."""
    def mock_call(content, model="test", response_format=None):
        return '{"ready": false, "question": "Should I use recursion or iteration?"}'

    runtime = Runtime(call=mock_call)

    @agentic_function
    def original():
        """Do something."""
        return "original"

    result = fix(fn=original, runtime=runtime)
    assert isinstance(result, str)
    assert "recursion" in result


def test_fix_clarify_prompt_omits_generation_suffix():
    """fix() keeps generation-only instructions out of clarify()."""
    from agentic.context import set_ask_user

    prompts = []

    class MysteryCallable:
        __doc__ = str.__doc__

        def __call__(self):
            return "broken"

    def mock_call(content, model="test", response_format=None):
        prompts.append(content[-1]["text"] if content else "")
        return '{"ready": false, "question": "What should change?"}'

    runtime = Runtime(call=mock_call)

    # Use ask_user returning empty string to decline answering (stops the loop)
    set_ask_user(lambda q: "")
    try:
        result = fix(
            fn=MysteryCallable(),
            runtime=runtime,
            instruction="Fix the crash when the input is empty.",
        )
    finally:
        set_ask_user(None)

    # Clarify prompt (call 1) should have instruction but not generation suffix
    assert "Fix the crash when the input is empty." in prompts[0]
    assert "Source not available for unknown" in prompts[0]
    assert "Fix the root cause" not in prompts[0]
    assert "Respond with ONLY the fixed Python code" not in prompts[0]


def test_clarify_vague_chinese_instruction():
    """clarify() flags vague Chinese instructions via LLM."""
    def mock_call(content, model="test", response_format=None):
        return '{"ready": false, "question": "请具体说明需要修改什么"}'

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


def test_clarify_treats_answered_qna_as_ready():
    """clarify() treats tasks with prior Q/A context as ready."""
    def mock_call(content, model="test", response_format=None):
        return '{"ready": true}'

    runtime = Runtime(call=mock_call)
    task = (
        "Current code:\n```python\nprint('hi')\n```\n\n"
        "Instruction: improve the prompt\n\n"
        "Q: What should I change?\n"
        "A: Just inspect the code and fix any issues."
    )

    result = clarify(task=task, runtime=runtime)
    assert result["ready"] is True


def test_clarify_treats_retry_feedback_as_ready():
    """clarify() treats tasks with retry feedback as ready (via LLM)."""
    def mock_call(content, model="test", response_format=None):
        return '{"ready": true}'

    runtime = Runtime(call=mock_call)
    task = (
        "Current code:\n```python\nprint('hi')\n```\n\n"
        "Instruction: improve the prompt\n\n"
        "── Previous attempt feedback ──\n"
        "Need clearer output format."
    )

    result = clarify(task=task, runtime=runtime)
    assert result["ready"] is True


def test_fix_produces_code_directly():
    """fix() returns compiled function through the full clarify → generate → verify flow."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''```python
@agentic_function
def func():
    """Fixed."""
    return "fixed_result"
```'''

    mock_call, cleanup = make_fix_mock(code)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def original():
            """Do something."""
            return "original"

        fixed_fn = fix(fn=original, runtime=runtime)
        assert callable(fixed_fn)
        assert fixed_fn() == "fixed_result"
    finally:
        cleanup()


def test_fix_custom_name():
    """fix() can override function name."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def generated():
    """Fixed."""
    return "ok"'''

    mock_call, cleanup = make_fix_mock(code)
    try:
        runtime = Runtime(call=mock_call)

        @agentic_function
        def original():
            return "original"

        fixed_fn = fix(fn=original, runtime=runtime, name="my_fixed")
        assert fixed_fn.__name__ == "my_fixed"
    finally:
        cleanup()


def test_fix_uses_docstring_when_source_unavailable(monkeypatch):
    """fix() falls back to docstring when inspect.getsource() is unavailable."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)

    try:
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
        # Source fallback message should appear in the clarify prompt (call 1)
        assert any("Source not available for original" in p for p in prompts)
        assert any("Original docstring" in p for p in prompts)
    finally:
        cleanup()


def test_fix_omits_builtin_docstring_bloat():
    """fix() keeps built-in fallbacks concise instead of dumping API docs."""
    from tests._fix_test_helpers import make_fix_mock

    code = '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)
    try:
        runtime = Runtime(call=mock_call)
        fixed_fn = fix(fn=str, runtime=runtime)
        assert fixed_fn() == "ok"
        assert any("Source not available for str" in p for p in prompts)
        # The generate_code prompt (call 3) should not contain full builtin docs
        if len(prompts) >= 3:
            assert "Create a new string object" not in prompts[2]
    finally:
        cleanup()


def test_fix_omits_api_style_docstring_bloat():
    """fix() keeps source-less API-style callable docs out of the prompt."""
    from tests._fix_test_helpers import make_fix_mock

    class MysteryCallable:
        __doc__ = str.__doc__

        def __call__(self):
            return "broken"

    code = '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)
    try:
        runtime = Runtime(call=mock_call)
        fixed_fn = fix(fn=MysteryCallable(), runtime=runtime, instruction="improve the prompt")
        assert callable(fixed_fn)
        assert fixed_fn() == "ok"
        assert any("Source not available for unknown" in p for p in prompts)
        assert not any("Create a new string object" in p for p in prompts)
    finally:
        cleanup()


def test_fix_uses_follow_up_answer_without_reasking():
    """fix() continues after an answered follow-up without re-clarifying."""
    from agentic.context import set_ask_user

    call_count = [0]
    prompts = []

    def mock_call(content, model="test", response_format=None):
        call_count[0] += 1
        prompts.append(content[-1]["text"] if content else "")
        # Round 0 clarify — forced follow_up regardless
        if call_count[0] == 1:
            return '{"ready": false, "question": "What output format should I use?"}'
        # Round 1 clarify — ready
        if call_count[0] == 2:
            return '{"ready": true}'
        # Round 1 generate
        if call_count[0] == 3:
            return '''@agentic_function
def restored():
    """Fixed."""
    return "ok"'''
        # Round 1 verify
        if call_count[0] == 4:
            return '{"approved": true, "reasoning": "Looks good."}'
        # conclude
        return "Fix completed."

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
    # clarify(r0) + clarify(r1) + generate + verify + conclude = 5
    assert call_count[0] == 5
    # The user's answer should appear in round 1's context
    assert any("Return plain text." in p for p in prompts)


def test_fix_includes_nested_child_errors():
    """fix() includes child context errors collected from nested attempts."""
    from tests._fix_test_helpers import make_fix_mock

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

    code = '''@agentic_function
def parent():
    """Fixed parent."""
    return "fixed"'''

    prompts = []
    mock_call, cleanup = make_fix_mock(code, check_prompts=prompts)
    try:
        runtime_fix = Runtime(call=mock_call)
        fixed_fn = fix(fn=parent, runtime=runtime_fix)
        assert fixed_fn() == "fixed"
        assert any("child" in p and "boom" in p for p in prompts)
    finally:
        cleanup()
