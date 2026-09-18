"""Native interface commands must hold the canonical Agent Page lease."""
import pytest
from types import SimpleNamespace
from openprogram.framework.client import call
from openprogram.programs.workflow.browser.web_use_runtime import WebUseSession, WebUseSessionRegistry


@pytest.fixture
def standalone_tool_policy():
    from openprogram.programs._runtime import _allowed_tool_names
    token = _allowed_tool_names.set(None)
    try:
        yield
    finally:
        _allowed_tool_names.reset(token)


def test_framework_web_commands_require_live_owned_page_and_tool_policy(monkeypatch, standalone_tool_policy):
    from openprogram.programs._runtime import _allowed_tool_names
    registry = WebUseSessionRegistry()
    session = WebUseSession('web', 'desktop', 'binding', page_key='page', owner_id='mine')
    registry._sessions[session.id] = session
    monkeypatch.setattr('openprogram.programs.workflow.browser.web_use_runtime.get_registry', lambda: registry)
    monkeypatch.setattr('openprogram.agent.surface_context.web_use_owner_id', lambda: 'mine')
    monkeypatch.setattr('openprogram.processes.current_owner', lambda: ('chat', None, None))
    monkeypatch.setattr('openprogram.framework.client.require_owner', lambda: None)
    from openprogram.webui.ws_actions import webtab
    monkeypatch.setattr(webtab, 'binding_page_descriptor', lambda _: {
        'tab_id': 'tab', 'window_id': 'window', 'page_key': 'page', 'target_id': 'native'})
    monkeypatch.setattr(webtab, 'request_bound_tab', lambda _: {'ok': True})
    calls = []
    monkeypatch.setattr('openprogram.programs.application_client.request', lambda *args, **kw: calls.append(kw) or {'ok': True})
    args = {'window_id': 'window', 'web_session_id': 'web', 'arguments': ['tab']}
    token = _allowed_tool_names.set(frozenset({'framework'}))
    try:
        with pytest.raises(PermissionError, match='provider_disabled'):
            call('interface', 'native.webTab.reload', args)
    finally:
        _allowed_tool_names.reset(token)
    with pytest.raises(ValueError, match='observe_web_resource'):
        call('interface', 'native.webTab.reload', {**args, 'web_session_id': 'absent'})
    session.owner_id = 'other'
    with pytest.raises(PermissionError, match='owner_mismatch'):
        call('interface', 'native.webTab.reload', args)
    session.owner_id = 'mine'
    with pytest.raises(ValueError, match='page_context_stale'):
        call('interface', 'native.webTab.reload', {**args, 'arguments': ['foreign']})
    assert calls == []
    assert call('interface', 'native.webTab.reload', args)['ok']
    assert calls[0]['body']['page']['session_id'] == 'chat'
    session.closed = True
    with pytest.raises(PermissionError, match='owner_mismatch'):
        call('interface', 'native.webTab.reload', args)
