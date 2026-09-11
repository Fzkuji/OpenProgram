"""Standalone Workflow model calls lazily bind and release a Runtime."""
import pytest

from openprogram.agentic_programming import agent, agentic_function, llm
from openprogram.agentic_programming.function import _current_runtime


@pytest.mark.parametrize('operation', [llm, agent])
@pytest.mark.parametrize('fails', [False, True])
def test_standalone_workflow_owns_runtime(monkeypatch, operation, fails):
    events = []

    class FakeRuntime:
        def exec(self, **kwargs):
            assert _current_runtime.get() is self
            events.append(kwargs)
            if fails:
                raise ValueError('provider failed')
            return 'done'

        def close(self):
            events.append('closed')

    monkeypatch.setattr('openprogram.providers.registry.create_runtime', FakeRuntime)
    token = _current_runtime.set(None)
    try:
        @agentic_function
        def report(task: str):
            return operation(task)

        if fails:
            with pytest.raises(ValueError, match='provider failed'):
                report('summarize')
        else:
            assert report('summarize') == 'done'
        assert events[-1] == 'closed'
        assert _current_runtime.get() is None
    finally:
        _current_runtime.reset(token)
