"""A native interface command on a web tab acts on a tab the conversation may operate."""
from types import SimpleNamespace

import pytest

from openprogram.programs._host.framework.client import call
from openprogram.programs.applications.browser import tabs


@pytest.fixture
def standalone_tool_policy():
    from openprogram.programs._host.runtime import _allowed_tool_names
    token = _allowed_tool_names.set(None)
    try:
        yield
    finally:
        _allowed_tool_names.reset(token)


def test_framework_web_commands_name_a_tab_of_the_browser_target(monkeypatch, standalone_tool_policy):
    from openprogram.programs._host.runtime import _allowed_tool_names
    from openprogram.interfaces.server.ws_actions import webtab

    tab = tabs.Tab("tk3fa", "window", "tab", binding_id="binding", page_key="page")

    class Driver:
        def tab(self, name, session):
            if name not in (None, "", "tk3fa"):
                raise tabs.BrowserError("tab_not_found", f"there is no tab {name}")
            return tab

    monkeypatch.setattr(tabs, "driver", lambda: Driver())
    monkeypatch.setattr('openprogram.host.processes.current_owner', lambda: ('chat', None, None))
    monkeypatch.setattr('openprogram.programs._host.framework.client.require_owner', lambda: None)
    live = SimpleNamespace(ok=True)
    monkeypatch.setattr(webtab, 'binding_page_descriptor', lambda _: {
        'tab_id': 'tab', 'window_id': 'window', 'page_key': 'page', 'target_id': 'native'})
    monkeypatch.setattr(webtab, 'request_bound_tab', lambda _: {'ok': live.ok})
    calls = []
    monkeypatch.setattr('openprogram.programs._authoring.application_client.request',
                        lambda *args, **kw: calls.append(kw) or {'ok': True})
    args = {'window_id': 'window', 'tab': 'tk3fa', 'arguments': ['tk3fa']}
    token = _allowed_tool_names.set(frozenset({'framework'}))
    try:
        with pytest.raises(PermissionError, match='provider_disabled'):
            call('interface', 'native.webTab.reload', args)
    finally:
        _allowed_tool_names.reset(token)
    with pytest.raises(ValueError, match='there is no tab'):
        call('interface', 'native.webTab.reload', {**args, 'tab': 'absent'})
    with pytest.raises(ValueError, match='first argument is the tab'):
        call('interface', 'native.webTab.reload', {**args, 'arguments': ['foreign']})
    with pytest.raises(ValueError, match='page_context_stale'):
        call('interface', 'native.webTab.reload', {**args, 'window_id': 'another'})
    live.ok = False
    with pytest.raises(ValueError, match='page_context_stale'):
        call('interface', 'native.webTab.reload', args)
    live.ok = True
    assert calls == []
    assert call('interface', 'native.webTab.reload', args)['ok']
    body = calls[0]['body']
    # The App receives its own tab id, whichever way the caller named the tab.
    assert body['arguments'] == ['tab'] and body['page']['session_id'] == 'chat'


def test_a_run_with_no_window_does_not_command_the_users_window(monkeypatch):
    """A scheduled run names no window; the one open window is the user's and stays as it is."""
    from openprogram.runtime.agent.turn_request import TurnRequest, reset_turn_request, set_turn_request
    sent = []
    monkeypatch.setattr('openprogram.programs._host.framework.client.require_owner', lambda: None)
    monkeypatch.setattr('openprogram.programs._authoring.application_client.request',
                        lambda path, **kwargs: sent.append((path, kwargs)) or {'ok': True})
    token = set_turn_request(TurnRequest('s', '', 'main', 'scheduler'))
    try:
        with pytest.raises(ValueError, match='no_window_for_this_run'):
            call('interface', 'sidebar.toggle', {'arguments': []})
        assert sent == []
        call('interface', 'sidebar.toggle', {'arguments': [], 'window_id': 'w1'})
    finally:
        reset_turn_request(token)
    assert [kwargs['body']['window_id'] for _, kwargs in sent] == ['w1']
    call('interface', 'sidebar.toggle', {'arguments': []})
    assert len(sent) == 2
