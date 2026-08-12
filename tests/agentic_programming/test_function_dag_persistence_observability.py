from __future__ import annotations

import asyncio
import logging

import pytest

from openprogram.agentic_programming.function import (
    _call_id,
    _current_runtime,
    agentic_function,
    traced,
)
from openprogram.agentic_programming.runtime import Runtime
from openprogram.store import _store


class _FailingStore:
    def __init__(self, *, fail_append: bool = False, fail_update: bool = False):
        self.fail_append = fail_append
        self.fail_update = fail_update

    def append(self, node) -> None:
        if self.fail_append:
            raise OSError("append detail")

    def update(self, node_id, **fields) -> None:
        if self.fail_update:
            raise PermissionError("update detail")


class _AbortStore(_FailingStore):
    def append(self, node) -> None:
        raise KeyboardInterrupt


class _ExitAbortStore(_FailingStore):
    def update(self, node_id, **fields) -> None:
        raise KeyboardInterrupt


@pytest.fixture
def runtime() -> Runtime:
    return Runtime(call=lambda *args, **kwargs: "", model="dummy")


def _run_with_store(store, call):
    token = _store.set(store)
    try:
        return call()
    finally:
        _store.reset(token)


def test_entry_failure_is_logged_without_changing_sync_result(
    runtime: Runtime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @agentic_function
    def work(runtime=None):
        return "ok"

    with caplog.at_level(logging.WARNING, logger="openprogram.agentic_programming.function"):
        result = _run_with_store(
            _FailingStore(fail_append=True),
            lambda: work(runtime=runtime),
        )

    assert result == "ok"
    records = [record for record in caplog.records if "phase=entry" in record.message]
    assert len(records) == 1
    assert "node_id=" in records[0].message
    assert "error_type=OSError" in caplog.text
    assert "append detail" in caplog.text


def test_exit_failure_is_logged_without_changing_async_result(
    runtime: Runtime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @agentic_function
    async def work(runtime=None):
        return "ok"

    with caplog.at_level(logging.WARNING, logger="openprogram.agentic_programming.function"):
        result = _run_with_store(
            _FailingStore(fail_update=True),
            lambda: asyncio.run(work(runtime=runtime)),
        )

    assert result == "ok"
    records = [record for record in caplog.records if "phase=exit" in record.message]
    assert len(records) == 1
    assert "node_id=" in records[0].message
    assert "error_type=PermissionError" in caplog.text
    assert "update detail" in caplog.text


def test_exit_persistence_failure_does_not_replace_function_error(
    runtime: Runtime,
) -> None:
    @agentic_function
    def work(runtime=None):
        raise ValueError("function error")

    with pytest.raises(ValueError, match="function error"):
        _run_with_store(
            _FailingStore(fail_update=True),
            lambda: work(runtime=runtime),
        )


def test_traced_uses_the_same_persistence_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @traced
    def work():
        return "ok"

    with caplog.at_level(logging.WARNING, logger="openprogram.agentic_programming.function"):
        result = _run_with_store(_FailingStore(fail_update=True), work)

    assert result == "ok"
    assert "phase=exit" in caplog.text


def test_hidden_and_no_store_remain_silent(
    runtime: Runtime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    @agentic_function(expose="hidden")
    def hidden(runtime=None):
        return "ok"

    @agentic_function
    def standalone(runtime=None):
        return "ok"

    with caplog.at_level(logging.WARNING, logger="openprogram.agentic_programming.function"):
        assert _run_with_store(_FailingStore(fail_append=True), lambda: hidden(runtime=runtime)) == "ok"
        assert standalone(runtime=runtime) == "ok"

    assert "DAG persistence failed" not in caplog.text


def test_persistence_base_exception_is_not_downgraded(runtime: Runtime) -> None:
    @agentic_function
    def work(runtime=None):
        return "unreachable"

    with pytest.raises(KeyboardInterrupt):
        _run_with_store(_AbortStore(), lambda: work(runtime=runtime))
    assert _call_id.get() is None
    assert _current_runtime.get() is None


def test_exit_base_exception_still_restores_agentic_context(runtime: Runtime) -> None:
    @agentic_function
    def work(runtime=None):
        return "ok"

    with pytest.raises(KeyboardInterrupt):
        _run_with_store(_ExitAbortStore(), lambda: work(runtime=runtime))
    assert _call_id.get() is None
    assert _current_runtime.get() is None


def test_exit_base_exception_still_restores_traced_call_id() -> None:
    @traced
    def work():
        return "ok"

    with pytest.raises(KeyboardInterrupt):
        _run_with_store(_ExitAbortStore(), work)
    assert _call_id.get() is None
