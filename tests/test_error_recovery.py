"""
End-to-end tests for error recovery: create → fail → fix → succeed.
"""

import pytest
from agentic import agentic_function, Runtime
from agentic.meta_function import create, fix


class TestCreateFailFixSucceed:
    """Full create → fail → fix → succeed cycle."""

    def test_basic_recovery_flow(self):
        """create() → call fails → fix(fn=...) → call succeeds."""
        def mock_call(content, model="test", response_format=None):
            prompt_text = "".join(b.get("text", "") for b in content if b["type"] == "text")

            if "Write a Python function" in prompt_text:
                return '''@agentic_function
def divide(a, b):
    """Divide a by b."""
    return str(a / b)'''

            if "Fix" in prompt_text or "fix" in prompt_text:
                return '''@agentic_function
def divide(a, b):
    """Divide a by b safely."""
    a_num = float(a)
    b_num = float(b)
    if b_num == 0:
        return "Error: division by zero"
    return str(a_num / b_num)'''

            return "ok"

        runtime = Runtime(call=mock_call)

        # Create
        divide = create(description="Divide two numbers", runtime=runtime)

        # Works for normal input
        result = divide(a=10, b=2)
        assert "5" in result

        # Fails for zero
        with pytest.raises(Exception):
            divide(a=10, b=0)

        # Fix (new API: pass fn directly)
        fixed_divide = fix(fn=divide, runtime=runtime)

        # Fixed handles zero
        result = fixed_divide(a=10, b=0)
        assert "Error" in result or "zero" in result.lower()

    def test_fix_preserves_context_tree(self):
        """Fixed function creates proper Context trees."""
        def mock_call(content, model="test", response_format=None):
            prompt_text = "".join(b.get("text", "") for b in content if b["type"] == "text")

            if "Write a Python function" in prompt_text:
                return '''@agentic_function
def process(data):
    """Process data."""
    return runtime.exec(content=[
        {"type": "text", "text": "Process: " + str(data)},
    ])'''

            if "Fix" in prompt_text or "fix" in prompt_text:
                return '''@agentic_function
def process(data):
    """Process data with validation."""
    if not data:
        return "empty"
    return runtime.exec(content=[
        {"type": "text", "text": "Process: " + str(data)},
    ])'''

            return f"processed: {prompt_text}"

        runtime = Runtime(call=mock_call)

        original = create(description="Process data", runtime=runtime)
        fixed = fix(fn=original, runtime=runtime)

        @agentic_function
        def pipeline(items):
            """Pipeline."""
            results = []
            for item in items:
                results.append(fixed(data=item))
            return results

        pipeline(items=["a", "b"])
        root = pipeline.context
        assert root.status == "success"
        assert len(root.children) == 2


class TestRetryMechanics:
    """Detailed tests for exec() retry behavior."""

    def test_retry_count_matches_max_retries(self):
        call_count = [0]

        def counting_call(content, model="test", response_format=None):
            call_count[0] += 1
            raise Exception("fail")

        for max_retries in [1, 2, 3]:
            call_count[0] = 0
            runtime = Runtime(call=counting_call, max_retries=max_retries)

            @agentic_function
            def func():
                return runtime.exec(content=[{"type": "text", "text": "test"}])

            with pytest.raises(RuntimeError):
                func()

            assert call_count[0] == max_retries

    def test_no_retry_on_type_error(self):
        call_count = [0]

        def type_error_call(content, model="test", response_format=None):
            call_count[0] += 1
            raise TypeError("wrong type")

        runtime = Runtime(call=type_error_call, max_retries=3)

        @agentic_function
        def func():
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(TypeError):
            func()

        assert call_count[0] == 1

    def test_successful_retry_records_reply(self):
        attempt = [0]

        def flaky(content, model="test", response_format=None):
            attempt[0] += 1
            if attempt[0] == 1:
                raise ConnectionError("transient")
            return "recovered"

        runtime = Runtime(call=flaky, max_retries=2)

        @agentic_function
        def func():
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        result = func()
        assert result == "recovered"
        assert func.context.raw_reply == "recovered"
