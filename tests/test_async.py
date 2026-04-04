"""
Tests for async agentic_function, async_exec, and asyncio.gather parallelism.
"""

import asyncio
import time

import pytest
from agentic import agentic_function, Runtime
from agentic.context import _current_ctx


# ── Helpers ──────────────────────────────────────────────────

def sync_echo(content, model="test", response_format=None):
    """Sync mock: echo last text block."""
    for block in reversed(content):
        if block["type"] == "text" and "Execution Context" not in block.get("text", ""):
            return block["text"]
    return "ok"


async def async_echo(content, model="test", response_format=None):
    """Async mock: echo last text block with a small delay."""
    await asyncio.sleep(0.01)
    for block in reversed(content):
        if block["type"] == "text" and "Execution Context" not in block.get("text", ""):
            return block["text"]
    return "ok"


# ══════════════════════════════════════════════════════════════
# Basic async agentic_function
# ══════════════════════════════════════════════════════════════

class TestAsyncAgenticFunction:
    """Tests for async @agentic_function decorator."""

    def test_basic_async_function(self):
        """Async decorated function executes and returns normally."""
        @agentic_function
        async def greet(name):
            """Say hello."""
            return f"Hello, {name}!"

        result = asyncio.run(greet(name="Alice"))
        assert result == "Hello, Alice!"

    def test_async_context_tree(self):
        """Async function creates proper Context tree."""
        @agentic_function
        async def outer():
            """Outer."""
            await inner()
            return "done"

        @agentic_function
        async def inner():
            """Inner."""
            return "inner done"

        asyncio.run(outer())
        root = outer.context
        assert root.name == "outer"
        assert root.status == "success"
        assert root.output == "done"
        assert len(root.children) == 1
        assert root.children[0].name == "inner"

    def test_async_params_recorded(self):
        """Parameters are recorded for async functions."""
        @agentic_function
        async def task(x, y=10):
            """Task."""
            return x + y

        asyncio.run(task(x=3, y=7))
        assert task.context.params == {"x": 3, "y": 7}

    def test_async_error_recorded(self):
        """Errors are recorded for async functions."""
        @agentic_function
        async def failing():
            """Will fail."""
            raise ValueError("async boom")

        with pytest.raises(ValueError, match="async boom"):
            asyncio.run(failing())

        assert failing.context.status == "error"
        assert "async boom" in failing.context.error

    def test_async_timing(self):
        """Duration is recorded for async functions."""
        @agentic_function
        async def timed():
            """Timed."""
            await asyncio.sleep(0.01)
            return "done"

        asyncio.run(timed())
        assert timed.context.duration_ms >= 10

    def test_async_context_cleared_after_top_level(self):
        """Context is cleared after top-level async function completes."""
        @agentic_function
        async def top():
            return "done"

        asyncio.run(top())
        assert _current_ctx.get(None) is None

    def test_async_nested_three_levels(self):
        """Three levels of async nesting."""
        @agentic_function
        async def l1():
            """Level 1."""
            return await l2()

        @agentic_function
        async def l2():
            """Level 2."""
            return await l3()

        @agentic_function
        async def l3():
            """Level 3."""
            return "deep"

        result = asyncio.run(l1())
        assert result == "deep"
        root = l1.context
        assert root.children[0].children[0].name == "l3"


# ══════════════════════════════════════════════════════════════
# async_exec tests
# ══════════════════════════════════════════════════════════════

class TestAsyncExec:
    """Tests for Runtime.async_exec()."""

    def test_async_exec_with_async_call(self):
        """async_exec with async call function."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "hello async"},
            ])

        result = asyncio.run(func())
        assert result == "hello async"

    def test_async_exec_with_sync_call(self):
        """async_exec with sync call function auto-adapts."""
        runtime = Runtime(call=sync_echo)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "hello sync"},
            ])

        result = asyncio.run(func())
        assert result == "hello sync"

    def test_async_exec_records_raw_reply(self):
        """async_exec records raw_reply on Context."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "reply_data"},
            ])

        asyncio.run(func())
        assert func.context.raw_reply == "reply_data"

    def test_async_exec_context_injection(self):
        """async_exec prepends execution context."""
        received = []

        async def capture(content, model="test", response_format=None):
            received.extend(content)
            return "ok"

        runtime = Runtime(call=capture)

        @agentic_function
        async def parent():
            """Parent."""
            return await child()

        @agentic_function
        async def child():
            """Child."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "user prompt"},
            ])

        asyncio.run(parent())
        assert len(received) >= 2
        assert "Execution Context" in received[0]["text"]

    def test_async_exec_double_call_raises(self):
        """Calling async_exec twice in one function raises."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def double():
            """Bad."""
            await runtime.async_exec(content=[{"type": "text", "text": "first"}])
            await runtime.async_exec(content=[{"type": "text", "text": "second"}])

        with pytest.raises(RuntimeError, match="twice"):
            asyncio.run(double())

    def test_async_exec_retry_on_failure(self):
        """async_exec retries on transient failure."""
        call_count = [0]

        async def flaky(content, model="test", response_format=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("network error")
            return "recovered"

        runtime = Runtime(call=flaky, max_retries=2)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        result = asyncio.run(func())
        assert result == "recovered"
        assert call_count[0] == 2

    def test_async_exec_retry_exhausted(self):
        """async_exec raises after all retries exhausted."""
        async def always_fail(content, model="test", response_format=None):
            raise ConnectionError("down")

        runtime = Runtime(call=always_fail, max_retries=3)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            asyncio.run(func())

    def test_async_exec_no_provider_raises(self):
        """async_exec without provider raises NotImplementedError."""
        runtime = Runtime()

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        with pytest.raises(NotImplementedError):
            asyncio.run(func())


# ══════════════════════════════════════════════════════════════
# Mixed sync/async
# ══════════════════════════════════════════════════════════════

class TestMixedSyncAsync:
    """Tests for mixing sync and async agentic functions."""

    def test_sync_child_in_async_parent(self):
        """Sync agentic_function called from async parent."""
        @agentic_function
        async def async_parent():
            """Async parent."""
            result = sync_child()
            return f"parent: {result}"

        @agentic_function
        def sync_child():
            """Sync child."""
            return "sync_result"

        result = asyncio.run(async_parent())
        assert result == "parent: sync_result"
        assert async_parent.context.children[0].name == "sync_child"

    def test_multiple_sync_children_in_async(self):
        """Multiple sync children under async parent."""
        @agentic_function
        async def parent():
            """Parent."""
            a = step_a()
            b = step_b()
            return f"{a},{b}"

        @agentic_function
        def step_a():
            return "a"

        @agentic_function
        def step_b():
            return "b"

        result = asyncio.run(parent())
        assert result == "a,b"
        assert len(parent.context.children) == 2


# ══════════════════════════════════════════════════════════════
# asyncio.gather parallelism
# ══════════════════════════════════════════════════════════════

class TestAsyncGather:
    """Tests for parallel execution with asyncio.gather."""

    def test_gather_basic(self):
        """Multiple async functions run in parallel via gather."""
        results_order = []

        @agentic_function
        async def fast():
            """Fast task."""
            await asyncio.sleep(0.01)
            results_order.append("fast")
            return "fast_done"

        @agentic_function
        async def slow():
            """Slow task."""
            await asyncio.sleep(0.02)
            results_order.append("slow")
            return "slow_done"

        async def main():
            return await asyncio.gather(fast(), slow())

        results = asyncio.run(main())
        assert results == ["fast_done", "slow_done"]
        # fast should complete before slow
        assert results_order == ["fast", "slow"]

    def test_gather_all_create_context(self):
        """All gathered functions create separate context trees."""
        @agentic_function
        async def task_a():
            """A."""
            return "a"

        @agentic_function
        async def task_b():
            """B."""
            return "b"

        async def main():
            return await asyncio.gather(task_a(), task_b())

        results = asyncio.run(main())
        assert results == ["a", "b"]
        # Each is a top-level call, so each has its own context
        assert task_a.context.name == "task_a"
        assert task_b.context.name == "task_b"

    def test_gather_with_async_exec(self):
        """Gathered functions with async_exec work correctly."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def query_a():
            """Query A."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "result_a"},
            ])

        @agentic_function
        async def query_b():
            """Query B."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "result_b"},
            ])

        async def main():
            return await asyncio.gather(query_a(), query_b())

        results = asyncio.run(main())
        assert "result_a" in results
        assert "result_b" in results

    def test_gather_speedup(self):
        """Parallel execution is faster than sequential."""
        async def slow_call(content, model="test", response_format=None):
            await asyncio.sleep(0.05)
            return "done"

        runtime = Runtime(call=slow_call)

        @agentic_function
        async def task():
            """Task."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        async def parallel():
            start = time.time()
            await asyncio.gather(task(), task(), task())
            return time.time() - start

        async def sequential():
            start = time.time()
            await task()
            await task()
            await task()
            return time.time() - start

        par_time = asyncio.run(parallel())
        seq_time = asyncio.run(sequential())
        # Parallel should be significantly faster than sequential
        # (3 * 50ms vs ~50ms)
        assert par_time < seq_time * 0.7

    def test_gather_with_error(self):
        """Gather propagates errors from any task."""
        @agentic_function
        async def good():
            """Good."""
            return "ok"

        @agentic_function
        async def bad():
            """Bad."""
            raise ValueError("boom")

        async def main():
            return await asyncio.gather(good(), bad())

        with pytest.raises(ValueError, match="boom"):
            asyncio.run(main())
