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


def test_code_selected_context_excludes_session_and_prior_subcalls(tmp_path):
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.context.nodes import Call, ROLE_USER
    from openprogram.store import SessionNodeWriter, SessionStore, _store

    captured = []

    class Probe(Runtime):
        def _call(self, content, **kwargs):
            messages = self._render_history_messages(content)
            captured.append(' '.join(block.text for message in messages for block in message.content if hasattr(block, 'text')))
            return 'PRIOR_MEMBER_RESULT'

    store = SessionStore(tmp_path / 'sessions')
    store.create_session('s1', agent_id='main')
    writer = SessionNodeWriter(store, 's1')
    writer.append(Call(role=ROLE_USER, output='OTHER_GROUP_SECRET'))
    runtime = Probe(call=lambda *args, **kwargs: '', model='fake')
    store_token = _store.set(writer)
    runtime_token = _current_runtime.set(runtime)
    try:
        @agentic_function(render_range={'callers': 0, 'subcalls': 0})
        def report(task: str):
            llm('member A selected material')
            return llm('member B selected material')

        report('EXCLUDED_TASK_SECRET')
        assert len(captured) == 2
        assert 'member A selected material' in captured[0]
        assert 'member B selected material' in captured[1]
        assert all(secret not in text for text in captured for secret in ('OTHER_GROUP_SECRET', 'EXCLUDED_TASK_SECRET', 'PRIOR_MEMBER_RESULT'))
    finally:
        _current_runtime.reset(runtime_token)
        _store.reset(store_token)
        runtime.close()
        store.close()
