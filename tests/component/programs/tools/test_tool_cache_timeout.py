"""Agent method cache= / timeout= execute-wrapper semantics.

Both kwargs were copied from @function during the function-calling
unification but the agentic execute wrapper never used them until the
2026-06 re-wiring. Mirrors @function behaviour: memoize on (name,
args); hard-kill after ``timeout`` seconds with an is_error-style
result instead of raising.
"""

from __future__ import annotations

import asyncio
import time

from openprogram import Agent


def test_cache_memoizes_on_name_and_args():
    counter = {"n": 0}

    class CachedProbeFnAgent(Agent):
        method_options = {
            "cached_probe_fn": {
                "cache": True,
                "register_globally": False,
                "name": "cached_probe_fn",
                "tool": True,
            },
        }

        def cached_probe_fn(self, x: str, runtime=None) -> str:
            """Cached test function."""
            counter["n"] += 1
            return f"r{counter['n']}"

    cached_probe_fn = CachedProbeFnAgent().cached_probe_fn

    tool = cached_probe_fn._agent_tool
    r1 = asyncio.run(tool.execute("cid1", {"x": "a"}, None, None))
    r2 = asyncio.run(tool.execute("cid2", {"x": "a"}, None, None))
    assert counter["n"] == 1
    assert r1.content[0].text == r2.content[0].text

    asyncio.run(tool.execute("cid3", {"x": "b"}, None, None))
    assert counter["n"] == 2


def test_no_cache_runs_every_time():
    counter = {"n": 0}

    class UncachedProbeFnAgent(Agent):
        method_options = {
            "uncached_probe_fn": {
                "register_globally": False,
                "name": "uncached_probe_fn",
                "tool": True,
            },
        }

        def uncached_probe_fn(self, x: str, runtime=None) -> str:
            """Uncached test function."""
            counter["n"] += 1
            return "ok"

    uncached_probe_fn = UncachedProbeFnAgent().uncached_probe_fn

    tool = uncached_probe_fn._agent_tool
    asyncio.run(tool.execute("cid1", {"x": "a"}, None, None))
    asyncio.run(tool.execute("cid2", {"x": "a"}, None, None))
    assert counter["n"] == 2


def test_timeout_returns_error_result_for_sync_body():
    class SlowSyncFnAgent(Agent):
        method_options = {
            "slow_sync_fn": {
                "timeout": 0.2,
                "register_globally": False,
                "name": "slow_sync_fn",
                "tool": True,
            },
        }

        def slow_sync_fn(self, x: str, runtime=None) -> str:
            """Slow sync test function."""
            time.sleep(2.0)
            return "never"

    slow_sync_fn = SlowSyncFnAgent().slow_sync_fn

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
    assert result.is_error is True
    assert elapsed < 1.5


def test_timeout_returns_error_result_for_async_body():
    class SlowAsyncFnAgent(Agent):
        method_options = {
            "slow_async_fn": {
                "timeout": 0.2,
                "register_globally": False,
                "name": "slow_async_fn",
                "tool": True,
            },
        }

        async def slow_async_fn(self, x: str, runtime=None) -> str:
            """Slow async test function."""
            await asyncio.sleep(2.0)
            return "never"

    slow_async_fn = SlowAsyncFnAgent().slow_async_fn

    tool = slow_async_fn._agent_tool
    result = asyncio.run(tool.execute("cid", {"x": "a"}, None, None))
    assert "timed out" in result.content[0].text
    assert result.is_error is True


def test_fast_body_unaffected_by_timeout():
    class FastFnAgent(Agent):
        method_options = {
            "fast_fn": {
                "timeout": 5.0,
                "register_globally": False,
                "name": "fast_fn",
                "tool": True,
            },
        }

        def fast_fn(self, x: str, runtime=None) -> str:
            """Fast test function."""
            return f"got {x}"

    fast_fn = FastFnAgent().fast_fn

    tool = fast_fn._agent_tool
    result = asyncio.run(tool.execute("cid", {"x": "a"}, None, None))
    assert "got a" in result.content[0].text
