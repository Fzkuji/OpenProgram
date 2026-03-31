"""
Tests for async and parallel execution.
"""

import asyncio
import pytest
from pydantic import BaseModel
from harness.function import Function, FunctionError
from harness.session import Session
from harness.runtime import Runtime


class MockSession(Session):
    def __init__(self, reply: str):
        self._reply = reply

    def send(self, message) -> str:
        return self._reply


class SimpleResult(BaseModel):
    status: str
    value: str


def test_execute_async():
    """Async execution returns same result as sync."""
    runtime = Runtime(
        session_factory=lambda: MockSession('{"status": "ok", "value": "async"}')
    )
    fn = Function("test", "Test", "Do it", SimpleResult)

    result = asyncio.run(runtime.execute_async(fn, {"task": "test"}))
    assert isinstance(result, SimpleResult)
    assert result.value == "async"


def test_execute_parallel():
    """Parallel execution runs multiple Functions concurrently."""
    call_count = {"n": 0}

    def factory():
        call_count["n"] += 1
        return MockSession('{"status": "ok", "value": "parallel"}')

    runtime = Runtime(session_factory=factory)
    fn = Function("test", "Test", "Do it", SimpleResult)

    calls = [
        (fn, {"task": "a"}),
        (fn, {"task": "b"}),
        (fn, {"task": "c"}),
    ]

    results = asyncio.run(runtime.execute_parallel(calls))

    assert len(results) == 3
    assert call_count["n"] == 3  # 3 separate sessions
    for r in results:
        assert isinstance(r, SimpleResult)
        assert r.value == "parallel"


def test_execute_parallel_with_failure():
    """Parallel execution handles individual failures."""
    replies = iter([
        '{"status": "ok", "value": "good"}',
        "not valid json",
        '{"status": "ok", "value": "good"}',
    ])

    def factory():
        return MockSession(next(replies))

    runtime = Runtime(session_factory=factory)
    fn = Function("test", "Test", "Do it", SimpleResult, max_retries=1)

    calls = [
        (fn, {"task": "a"}),
        (fn, {"task": "b"}),  # this one will fail
        (fn, {"task": "c"}),
    ]

    results = asyncio.run(runtime.execute_parallel(calls))

    assert len(results) == 3
    assert isinstance(results[0], SimpleResult)
    assert isinstance(results[1], FunctionError)  # failed
    assert isinstance(results[2], SimpleResult)
