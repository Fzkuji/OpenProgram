"""
End-to-end tests for error recovery: create → fail → fix → succeed.

Tests the complete flow of:
1. create() generates a function
2. The function fails when called
3. fix() rewrites the function
4. The fixed function succeeds
"""

import pytest
from agentic import agentic_function, Runtime
from agentic.meta_function import create, fix


class TestCreateFailFixSucceed:
    """Full create → fail → fix → succeed cycle."""

    def test_basic_recovery_flow(self):
        """create() → call fails → fix() → call succeeds."""
        call_log = []

        def mock_call(content, model="test", response_format=None):
            call_log.append("call")
            prompt_text = ""
            for block in content:
                if block["type"] == "text":
                    prompt_text += block["text"]

            # Step 1: create() asks LLM to generate code
            if "Write a Python function" in prompt_text:
                return '''@agentic_function
def divide(a, b):
    """Divide a by b."""
    return str(a / b)'''

            # Step 3: fix() asks LLM to rewrite
            if "rewrite the function" in prompt_text or "fix them" in prompt_text:
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

        # Step 1: create the function
        divide = create(description="Divide two numbers", runtime=runtime)
        assert callable(divide)

        # Step 2: call succeeds for normal input
        result = divide(a=10, b=2)
        assert "5" in result

        # Step 2b: call fails for zero division
        with pytest.raises(Exception):
            divide(a=10, b=0)

        # Step 3: fix the function
        fixed_divide = fix(
            description="Divide two numbers",
            code='def divide(a, b):\n    return str(a / b)',
            error_log="Attempt 1: ZeroDivisionError: division by zero",
            runtime=runtime,
        )

        # Step 4: fixed function handles zero
        result = fixed_divide(a=10, b=0)
        assert "Error" in result or "zero" in result.lower()

        # Step 4b: fixed function still works for normal input
        result = fixed_divide(a=10, b=2)
        assert "5" in result

    def test_runtime_retry_then_fix(self):
        """exec() retries fail → user catches → fix() rewrites."""
        attempt_count = [0]
        phase = ["generate"]  # track which phase we're in

        def mock_call(content, model="test", response_format=None):
            prompt_text = ""
            for block in content:
                if block["type"] == "text":
                    prompt_text += block["text"]

            # create() phase
            if "Write a Python function" in prompt_text:
                return '''@agentic_function
def fetch_data(url):
    """Fetch data from URL."""
    result = runtime.exec(content=[
        {"type": "text", "text": "Fetch: " + url},
    ])
    return result'''

            # fix() phase
            if "fix them" in prompt_text or "rewrite" in prompt_text:
                return '''@agentic_function
def fetch_data(url):
    """Fetch data from URL with validation."""
    if not url.startswith("http"):
        return "Error: invalid URL"
    result = runtime.exec(content=[
        {"type": "text", "text": "Fetch: " + url},
    ])
    return result'''

            # Runtime exec inside generated function
            if "Fetch:" in prompt_text:
                attempt_count[0] += 1
                if phase[0] == "broken" and attempt_count[0] <= 2:
                    raise ConnectionError("server down")
                return f"data from {prompt_text.split('Fetch: ')[-1]}"

            return "ok"

        runtime = Runtime(call=mock_call, max_retries=2)

        # Create
        fetch = create(description="Fetch data from a URL", runtime=runtime)

        # Works normally
        result = fetch(url="http://example.com")
        assert "example.com" in result

        # Now simulate persistent failure
        phase[0] = "broken"
        attempt_count[0] = 0
        with pytest.raises(RuntimeError, match="failed after"):
            fetch(url="http://failing.com")

        # Fix
        phase[0] = "fix"
        fixed_fetch = fix(
            description="Fetch data from a URL",
            code='def fetch_data(url): ...',
            error_log="ConnectionError: server down (2 attempts)",
            runtime=runtime,
        )

        # Fixed version validates input
        result = fixed_fetch(url="not-a-url")
        assert "Error" in result

    def test_fix_preserves_context_tree(self):
        """Fixed function still creates proper Context trees."""
        def mock_call(content, model="test", response_format=None):
            prompt_text = "".join(b.get("text", "") for b in content if b["type"] == "text")

            if "Write a Python function" in prompt_text:
                return '''@agentic_function
def process(data):
    """Process data."""
    return runtime.exec(content=[
        {"type": "text", "text": "Process: " + str(data)},
    ])'''

            if "fix" in prompt_text.lower() or "rewrite" in prompt_text.lower():
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

        # Create and fix
        original = create(description="Process data", runtime=runtime)
        fixed = fix(
            description="Process data",
            code="def process(data): ...",
            error_log="Error: empty data",
            runtime=runtime,
        )

        # Use fixed function inside another agentic_function
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
        assert all(c.name == "process" for c in root.children)


class TestRetryMechanics:
    """Detailed tests for exec() retry behavior."""

    def test_retry_count_matches_max_retries(self):
        """exec() retries exactly max_retries times."""
        call_count = [0]

        def counting_call(content, model="test", response_format=None):
            call_count[0] += 1
            raise Exception("fail")

        for max_retries in [1, 2, 3, 5]:
            call_count[0] = 0
            runtime = Runtime(call=counting_call, max_retries=max_retries)

            @agentic_function
            def func():
                """Test."""
                return runtime.exec(content=[{"type": "text", "text": "test"}])

            with pytest.raises(RuntimeError):
                func()

            assert call_count[0] == max_retries

    def test_retry_error_report_has_all_attempts(self):
        """Error report includes details from every attempt."""
        attempt = [0]

        def varied_errors(content, model="test", response_format=None):
            attempt[0] += 1
            if attempt[0] == 1:
                raise ValueError("bad value")
            if attempt[0] == 2:
                raise TimeoutError("timed out")
            raise ConnectionError("disconnected")

        runtime = Runtime(call=varied_errors, max_retries=3)

        @agentic_function
        def func():
            """Test."""
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(RuntimeError) as exc_info:
            func()

        error_msg = str(exc_info.value)
        assert "bad value" in error_msg
        assert "timed out" in error_msg
        assert "disconnected" in error_msg
        assert "Attempt 1" in error_msg
        assert "Attempt 2" in error_msg
        assert "Attempt 3" in error_msg

    def test_no_retry_on_type_error(self):
        """TypeError is not retried (programming error)."""
        call_count = [0]

        def type_error_call(content, model="test", response_format=None):
            call_count[0] += 1
            raise TypeError("wrong argument type")

        runtime = Runtime(call=type_error_call, max_retries=3)

        @agentic_function
        def func():
            """Test."""
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(TypeError, match="wrong argument"):
            func()

        assert call_count[0] == 1  # No retry for TypeError

    def test_no_retry_on_not_implemented(self):
        """NotImplementedError is not retried."""
        call_count = [0]

        def not_impl_call(content, model="test", response_format=None):
            call_count[0] += 1
            raise NotImplementedError("not supported")

        runtime = Runtime(call=not_impl_call, max_retries=3)

        @agentic_function
        def func():
            """Test."""
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        with pytest.raises(NotImplementedError):
            func()

        assert call_count[0] == 1

    def test_successful_retry_records_reply(self):
        """When retry succeeds, raw_reply is set correctly."""
        attempt = [0]

        def flaky(content, model="test", response_format=None):
            attempt[0] += 1
            if attempt[0] == 1:
                raise ConnectionError("transient")
            return "recovered_reply"

        runtime = Runtime(call=flaky, max_retries=2)

        @agentic_function
        def func():
            """Test."""
            return runtime.exec(content=[{"type": "text", "text": "test"}])

        result = func()
        assert result == "recovered_reply"
        assert func.context.raw_reply == "recovered_reply"
