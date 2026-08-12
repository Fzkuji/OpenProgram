from __future__ import annotations

import asyncio
import logging

import pytest

from openprogram.agentic_programming.function import agentic_function, traced
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
    assert "phase=entry" in caplog.text
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
    assert "phase=exit" in caplog.text
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
