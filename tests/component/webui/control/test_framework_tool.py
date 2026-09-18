"""Public tool admission must precede any owner credential or backend access."""
import asyncio
from types import SimpleNamespace


def test_tool_discovery_calls_backend_and_denies_paired_or_sandbox(monkeypatch):
    from openprogram.programs.tools.runtime.framework import framework
    from openprogram.programs import application_client
    from openprogram.agent import turn_request_context
    from openprogram import sandbox
    from openprogram.backend import get_active_backend
    calls = []
    monkeypatch.setattr(application_client, 'request', lambda path, **kw: calls.append(path) or {'operations': []})
    monkeypatch.setattr('openprogram.backend.get_active_backend', lambda: SimpleNamespace(backend_id='local'))
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: None)
    def invoke():
        return asyncio.run(framework.execute('framework-test', {'action': 'describe', 'operation': 'settings'}, None, None))
    token = turn_request_context.set_turn_request(SimpleNamespace(authority_tier='owner'))
    try:
        result = invoke()
        assert not result.is_error
        assert calls == ['/api/framework/operations?query=settings&offset=0']
    finally:
        turn_request_context.reset_turn_request(token)
    token = turn_request_context.set_turn_request(SimpleNamespace(authority_tier='paired'))
    try:
        assert invoke().is_error
        assert len(calls) == 1
    finally:
        turn_request_context.reset_turn_request(token)
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: object())
    assert invoke().is_error
    assert len(calls) == 1
