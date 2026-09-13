"""Shared invoke/timeout helper used by @function and @agentic_function."""
from __future__ import annotations

import asyncio
import threading

import pytest

from openprogram.programs._execution_common import (
    invoke_callable,
    timeout_tool_result,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_invoke_async_and_sync() -> None:
    async def add(x, y):
        return x + y

    assert _run(invoke_callable(add, {"x": 1, "y": 2}, is_async=True)) == 3
    assert _run(invoke_callable(lambda x: x * 2, {"x": 4}, is_async=False)) == 8


def test_invoke_timeout_raises() -> None:
    async def hang():
        await asyncio.Event().wait()

    with pytest.raises(asyncio.TimeoutError):
        _run(invoke_callable(hang, {}, timeout=0.01, is_async=True))


def test_agentic_no_timeout_sync_stays_on_loop_thread() -> None:
    here = threading.get_ident()

    def body():
        return threading.get_ident()

    assert _run(invoke_callable(
        body, {}, is_async=False, run_sync_in_executor=False,
    )) == here


def test_timeout_tool_result_text() -> None:
    result = timeout_tool_result("probe", 1.5, details={"timeout": True})
    assert result.is_error is True
    assert result.content[0].text == "[error] function probe timed out after 1.5s"
    assert result.details == {"timeout": True}
