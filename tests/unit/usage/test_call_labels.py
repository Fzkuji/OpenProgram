"""Calls that no conversation turn wraps still say where they came from."""
import asyncio

from openprogram.models.usage import usage_scope
from openprogram.models.usage.context import current_usage_context
from openprogram.runtime.agent.runtime.execution import _exec_usage_scope, _metered_async_exec


def test_a_bare_runtime_call_counts_as_exec():
    with _exec_usage_scope():
        assert current_usage_context().call_kind == "exec"
    assert current_usage_context().call_kind is None


def test_an_outer_label_is_kept():
    with usage_scope(call_kind="memory"), _exec_usage_scope():
        assert current_usage_context().call_kind == "memory"


def test_the_label_reaches_an_async_call():
    @_metered_async_exec
    async def call():
        return current_usage_context().call_kind

    assert asyncio.run(call()) == "exec"


def test_a_memory_write_is_labelled(monkeypatch):
    from openprogram.state.memory import writing

    seen = {}

    def fake(session_id, messages, **options):
        context = current_usage_context()
        seen.update(kind=context.call_kind, session=context.session_id)
        return True

    monkeypatch.setattr(writing, "_write_session", fake)
    assert writing.write_session("s1", [], token_threshold=1)
    assert seen == {"kind": "memory", "session": "s1"}
