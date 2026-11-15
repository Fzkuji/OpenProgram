from __future__ import annotations

from openprogram.runtime.agent import Agent

import asyncio
import logging

import pytest

from openprogram.runtime.context.scope import _call_id
from openprogram.runtime.agent.call_state import _current_runtime, traced
from openprogram.runtime.agent.runtime import Runtime
from openprogram.state.store import _store


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


class _BadCloseRuntime:
    def close(self) -> None:
        raise RuntimeError("close failed")


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
    class _WorkAgent(Agent):
        method_options = {'work': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        def work(self, runtime=None):
            return "ok"

    work = _WorkAgent().work

    with caplog.at_level(logging.WARNING, logger="openprogram.runtime.agent.call_state"):
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
    class _WorkAgent(Agent):
        method_options = {'work': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        async def work(self, runtime=None):
            return "ok"

    work = _WorkAgent().work

    with caplog.at_level(logging.WARNING, logger="openprogram.runtime.agent.call_state"):
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
    class _WorkAgent(Agent):
        method_options = {'work': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        def work(self, runtime=None):
            raise ValueError("function error")

    work = _WorkAgent().work

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

    with caplog.at_level(logging.WARNING, logger="openprogram.runtime.agent.call_state"):
        result = _run_with_store(_FailingStore(fail_update=True), work)

    assert result == "ok"
    assert "phase=exit" in caplog.text


def test_hidden_and_no_store_remain_silent(
    runtime: Runtime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _HiddenAgent(Agent):
        method_options = {'hidden': {'expose': "hidden", 'tool': True, 'capture_io': True, 'name': 'hidden'}}

        def hidden(self, runtime=None):
            return "ok"

    hidden = _HiddenAgent().hidden

    class _StandaloneAgent(Agent):
        method_options = {'standalone': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'standalone'}}

        def standalone(self, runtime=None):
            return "ok"

    standalone = _StandaloneAgent().standalone

    with caplog.at_level(logging.WARNING, logger="openprogram.runtime.agent.call_state"):
        assert _run_with_store(_FailingStore(fail_append=True), lambda: hidden(runtime=runtime)) == "ok"
        assert standalone(runtime=runtime) == "ok"

    assert "DAG persistence failed" not in caplog.text


def test_persistence_base_exception_is_not_downgraded(runtime: Runtime) -> None:
    class _WorkAgent(Agent):
        method_options = {'work': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        def work(self, runtime=None):
            return "unreachable"

    work = _WorkAgent().work

    with pytest.raises(KeyboardInterrupt):
        _run_with_store(_AbortStore(), lambda: work(runtime=runtime))
    assert _call_id.get() is None
    assert _current_runtime.get() is None


def test_exit_base_exception_still_restores_agentic_context(runtime: Runtime) -> None:
    class _WorkAgent(Agent):
        method_options = {'work': {'tool': True, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        def work(self, runtime=None):
            return "ok"

    work = _WorkAgent().work

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


@pytest.mark.parametrize("parameter", ["runtime", "exec_runtime", "review_runtime"])
def test_owned_runtime_alias_does_not_construct_provider_before_entry_abort(
    parameter: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloseCounter:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    owned = CloseCounter()
    create_calls = []

    def create():
        create_calls.append(True)
        return owned

    monkeypatch.setattr("openprogram.runtime.agent.runtime.factory.create_runtime", create)
    namespace: dict = {}
    namespace['Agent'] = Agent
    exec(
        "class WorkAgent(Agent):\n"
        "    method_options = {'work': {'tool': False, 'capture_io': True, 'name': 'work'}}\n"
        f"    def work(self, {parameter}=None):\n"
        "        return 'unreachable'\n",
        namespace,
    )
    work = namespace['WorkAgent']().work

    with pytest.raises(KeyboardInterrupt):
        _run_with_store(_AbortStore(), work)

    assert create_calls == []
    assert owned.close_calls == 0
    assert _current_runtime.get() is None


@pytest.mark.parametrize("phase", ["entry", "exit"])
def test_close_failure_does_not_replace_persistence_base_exception(
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openprogram.runtime.agent.runtime.factory.create_runtime",
        _BadCloseRuntime,
    )

    class _WorkAgent(Agent):
        method_options = {'work': {'tool': False, 'expose': 'io', 'capture_io': True, 'name': 'work'}}

        def work(self, exec_runtime=None):
            return "ok"

    work = _WorkAgent().work

    store = _AbortStore() if phase == "entry" else _ExitAbortStore()
    with pytest.raises(KeyboardInterrupt):
        _run_with_store(store, work)
    assert _current_runtime.get() is None
    assert _call_id.get() is None


def test_ordinary_call_failure_restores_shared_scope_without_provider(monkeypatch):
    from openprogram.runtime.agent.call_scope import managed_function
    from openprogram.runtime.context.model import Context
    from types import SimpleNamespace

    nodes = {}
    updates = []

    class Writer:
        def append(self, node):
            nodes[node.id] = node

        def load(self):
            return SimpleNamespace(nodes=nodes)

        def update(self, node_id, **fields):
            updates.append((node_id, fields))

    def unexpected_provider():
        pytest.fail("An ordinary call must not construct a model provider")

    monkeypatch.setattr("openprogram.runtime.agent.runtime.factory.create_runtime", unexpected_provider)

    @managed_function
    def fail(private_argument):
        """Document the ordinary function scope."""
        assert Context.current().call_id == _call_id.get()
        raise ValueError("ordinary failure")

    previous_context = Context.current()
    previous_store = _store.get()
    with pytest.raises(ValueError, match="ordinary failure"):
        _run_with_store(Writer(), lambda: fail(object()))
    assert Context.current() is previous_context
    assert _store.get() is previous_store
    assert _call_id.get() is None
    assert _current_runtime.get() is None
    assert len(nodes) == 1
    node = next(iter(nodes.values()))
    assert node.input == {}
    assert node.metadata["doc"] == "Document the ordinary function scope."
    assert updates[0][1]["output"] == {"error": "ordinary failure"}
