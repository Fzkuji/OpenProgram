"""
Tests for async agentic_function, async_exec, and asyncio.gather parallelism.
"""

import asyncio
import time

import pytest
from openprogram import agentic_function, Runtime
from openprogram.agentic_programming.context import _current_ctx


# ── Helpers ──────────────────────────────────────────────────

_call_counter = [0]

def sync_echo(content, model="test", response_format=None):
    """Sync mock: returns a fixed reply."""
    _call_counter[0] += 1
    return f"sync_reply_{_call_counter[0]}"


async def async_echo(content, model="test", response_format=None):
    """Async mock: returns a fixed reply with a small delay."""
    await asyncio.sleep(0.01)
    _call_counter[0] += 1
    return f"async_reply_{_call_counter[0]}"


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
        assert "async_reply" in result

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
        assert "sync_reply" in result

    def test_async_exec_records_raw_reply(self):
        """async_exec records raw_reply on exec node and parent."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def func():
            """Test."""
            return await runtime.async_exec(content=[
                {"type": "text", "text": "reply_data"},
            ])

        asyncio.run(func())
        assert func.context.raw_reply is not None
        exec_node = func.context.children[0]
        assert exec_node.node_type == "exec"
        assert exec_node.raw_reply is not None

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
        texts = [b.get("text", "") for b in received if b["type"] == "text"]
        assert any("Parent." in t for t in texts)

    def test_async_exec_multiple_calls(self):
        """Multiple async_exec calls in one function work."""
        runtime = Runtime(call=async_echo)

        @agentic_function
        async def multi():
            """Multiple calls."""
            r1 = await runtime.async_exec(content=[{"type": "text", "text": "first"}])
            r2 = await runtime.async_exec(content=[{"type": "text", "text": "second"}])
            return f"{r1}+{r2}"

        result = asyncio.run(multi())
        assert "+" in result  # two replies joined
        exec_nodes = [c for c in multi.context.children if c.node_type == "exec"]
        assert len(exec_nodes) == 2

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

    def test_async_exec_records_failed_attempts(self):
        """async_exec stores full attempt history on the Context node."""
        call_count = [0]

        async def flaky(content, model="test", response_format=None):
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionError(f"transient-{call_count[0]}")
            return "ok"

        runtime = Runtime(call=flaky, max_retries=3)

        @agentic_function
        async def func():
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        result = asyncio.run(func())
        assert result == "ok"
        # Attempts are on the exec child node
        exec_node = func.context.children[0]
        assert exec_node.node_type == "exec"
        assert len(exec_node.attempts) == 3
        assert "ConnectionError: transient-1" == exec_node.attempts[0]["error"]
        assert "ConnectionError: transient-2" == exec_node.attempts[1]["error"]
        assert exec_node.attempts[2]["reply"] == "ok"
        assert exec_node.attempts[2]["error"] is None

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

    def test_async_exec_no_retry_on_not_implemented_error(self):
        """NotImplementedError should bypass retry in async_exec too."""
        call_count = [0]

        async def not_implemented(content, model="test", response_format=None):
            call_count[0] += 1
            raise NotImplementedError("provider stub")

        runtime = Runtime(call=not_implemented, max_retries=3)

        @agentic_function
        async def func():
            return await runtime.async_exec(content=[
                {"type": "text", "text": "test"},
            ])

        with pytest.raises(NotImplementedError, match="provider stub"):
            asyncio.run(func())

        assert call_count[0] == 1

    def test_async_exec_retry_error_report_outside_function(self):
        """Retry errors outside @agentic_function still include attempt history."""
        async def always_fail(content, model="test", response_format=None):
            raise ConnectionError("offline")

        runtime = Runtime(call=always_fail, max_retries=2)

        with pytest.raises(RuntimeError, match="Attempt 1: ConnectionError: offline") as exc_info:
            asyncio.run(runtime.async_exec(content=[{"type": "text", "text": "bare call"}]))

        assert "Attempt 2: ConnectionError: offline" in str(exc_info.value)


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
        assert len(results) == 2
        # Both should get replies (exact values depend on call counter)
        assert all(r is not None for r in results)

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
