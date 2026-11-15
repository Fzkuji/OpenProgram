"""Public tool admission must precede any owner credential or backend access."""
import asyncio
from types import SimpleNamespace


def test_tool_discovery_calls_backend_and_denies_paired_or_sandbox(monkeypatch):
    from openprogram.programs.tools.runtime.framework import framework
    from openprogram.programs._authoring import application_client
    from openprogram.runtime.agent import turn_request as agent_turn_request
    from openprogram.host import sandbox
    calls = []
    monkeypatch.setattr(application_client, 'request', lambda path, **kw: calls.append(path) or {'operations': []})
    monkeypatch.setattr('openprogram.host.backend.get_active_backend', lambda: SimpleNamespace(backend_id='local'))
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: None)
    def invoke():
        return asyncio.run(framework.execute('framework-test', {'action': 'describe', 'operation': 'settings'}, None, None))
    token = agent_turn_request.set_turn_request(SimpleNamespace(authority_tier='owner'))
    try:
        result = invoke()
        assert not result.is_error
        assert calls == ['/api/framework/operations?query=settings&offset=0']
    finally:
        agent_turn_request.reset_turn_request(token)
    token = agent_turn_request.set_turn_request(SimpleNamespace(authority_tier='paired'))
    try:
        assert invoke().is_error
        assert len(calls) == 1
    finally:
        agent_turn_request.reset_turn_request(token)
    monkeypatch.setattr(sandbox, 'resolve_policy', lambda: object())
    assert invoke().is_error
    assert len(calls) == 1
