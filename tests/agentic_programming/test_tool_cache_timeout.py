"""@agentic_function cache= / timeout= execute-wrapper semantics.

Both kwargs were copied from @function during the function-calling
unification but the agentic execute wrapper never used them until the
2026-06 re-wiring. Mirrors @function behaviour: memoize on (name,
args); hard-kill after ``timeout`` seconds with an is_error-style
result instead of raising.
"""

from __future__ import annotations

import asyncio
import time

from openprogram.agentic_programming.function import agentic_function


def test_cache_memoizes_on_name_and_args():
    counter = {"n": 0}

    @agentic_function(cache=True, register_globally=False)
    def cached_probe_fn(x: str, runtime=None) -> str:
        """Cached test function."""
        counter["n"] += 1
        return f"r{counter['n']}"

    tool = cached_probe_fn._agent_tool
    r1 = asyncio.run(tool.execute("cid1", {"x": "a"}, None, None))
    r2 = asyncio.run(tool.execute("cid2", {"x": "a"}, None, None))
    assert counter["n"] == 1
    assert r1.content[0].text == r2.content[0].text

    asyncio.run(tool.execute("cid3", {"x": "b"}, None, None))
    assert counter["n"] == 2


def test_no_cache_runs_every_time():
    counter = {"n": 0}

    @agentic_function(register_globally=False)
    def uncached_probe_fn(x: str, runtime=None) -> str:
        """Uncached test function."""
        counter["n"] += 1
        return "ok"

    tool = uncached_probe_fn._agent_tool
    asyncio.run(tool.execute("cid1", {"x": "a"}, None, None))
    asyncio.run(tool.execute("cid2", {"x": "a"}, None, None))
    assert counter["n"] == 2


def test_timeout_returns_error_result_for_sync_body():
    @agentic_function(timeout=0.2, register_globally=False)
    def slow_sync_fn(x: str, runtime=None) -> str:
        """Slow sync test function."""
        time.sleep(2.0)
        return "never"

    tool = slow_sync_fn._agent_tool

    # Time the execute itself inside the loop — asyncio.run's shutdown
    # joins the (uncancellable) executor thread, so timing the whole
    # run would measure the sleeping thread, not the caller's wait.
    async def _go():
        started = time.monotonic()
        result = await tool.execute("cid", {"x": "a"}, None, None)
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(_go())
    assert "timed out" in result.content[0].text
    assert elapsed < 1.5


def test_timeout_returns_error_result_for_async_body():
    @agentic_function(timeout=0.2, register_globally=False)
    async def slow_async_fn(x: str, runtime=None) -> str:
        """Slow async test function."""
        await asyncio.sleep(2.0)
        return "never"

    tool = slow_async_fn._agent_tool
    result = asyncio.run(tool.execute("cid", {"x": "a"}, None, None))
    assert "timed out" in result.content[0].text


def test_fast_body_unaffected_by_timeout():
    @agentic_function(timeout=5.0, register_globally=False)
    def fast_fn(x: str, runtime=None) -> str:
        """Fast test function."""
        return f"got {x}"

    tool = fast_fn._agent_tool
    result = asyncio.run(tool.execute("cid", {"x": "a"}, None, None))
    assert "got a" in result.content[0].text
