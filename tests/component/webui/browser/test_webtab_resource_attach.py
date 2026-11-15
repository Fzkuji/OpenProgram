"""Human Resources attachment keeps the existing native Page and associations."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openprogram.interfaces.server.ws_actions import webtab
from openprogram.interfaces.server.routes.execution import processes
from openprogram.programs.applications.browser.pages import BrowserResourceStore


@pytest.fixture
def setup_attach(tmp_path, monkeypatch):
    from openprogram.runtime.permissions.authority import local_owner_authority
    monkeypatch.setattr('openprogram.state.paths.get_state_dir', lambda: tmp_path)
    from openprogram.state.store.session.session_store import SessionStore
    db = SessionStore(tmp_path / 'sessions')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    monkeypatch.setattr('openprogram.runtime.execution.default_store', lambda: None)
    # Authorization is exercised separately; avoid depending on an unrelated session DB fixture.
    monkeypatch.setattr(processes, '_authorize', lambda *args, **kwargs: None)
    class Socket:
        scope = {'state': {'authority': local_owner_authority()}}
    ws = Socket()
    generation = webtab.ensure_connection_revision(ws)
    webtab._desktop_windows[ws] = 'win'
    replies = []
    def inventory(socket, command, **kwargs):
        assert socket is ws
        assert command['session_id'] == 'a'
        replies.append(command)
        return {'ok': True, 'pages': [{'tab_id': 'b', 'target_id': 'native-b', 'url': 'https://example.test/path?secret=1', 'title': 'B'}]}
    monkeypatch.setattr(webtab, 'request_on_ws', inventory)
    app = FastAPI()
    processes.register(app)
    with TestClient(app) as client:
        yield client, ws, generation, replies
    webtab.release_connection(ws)


def test_attach_same_page_is_idempotent_and_preserves_previous_association(setup_attach):
    client, ws, generation, replies = setup_attach
    binding = webtab.register_binding(ws, 'win', 'b', 'native-b', allow_background=True)
    key = webtab.binding_page_key(binding)
    store = BrowserResourceStore()
    store.retain(page_key=key, window_id='win', tab_id='b', connection_generation=generation,
                 session_id='old', conversation_session_id='old')
    for _ in range(2):
        response = client.post('/api/session/a/resources/attach-web', json={'window_id':'win','tab_id':'b'})
        assert response.status_code == 200, response.text
        row = response.json()['items'][0]
        assert row['resource_id'] == key
        assert row['tab_id'] == 'b'
        assert row['conversation_session_id'] == 'a'
        assert 'secret=' not in row['target']
    assert len([a for a in store.associations_for_page(key) if a['session_id']=='a']) == 1
    assert any(a['session_id']=='old' for a in store.associations_for_page(key))


@pytest.mark.parametrize('body', [{'window_id':'other','tab_id':'b'}, {'window_id':'win','tab_id':'missing'}, {'window_id':'win','tab_id':''}])
def test_attach_rejects_missing_identity(setup_attach, body):
    client, *_ = setup_attach
    assert client.post('/api/session/a/resources/attach-web', json=body).status_code in (400,404)


def test_attach_rejects_connection_change(setup_attach, monkeypatch):
    client, ws, *_ = setup_attach
    def stale(*args, **kwargs):
        webtab.release_connection(ws)
        return {'ok':True,'pages':[{'tab_id':'b','target_id':'native-b'}]}
    monkeypatch.setattr(webtab,'request_on_ws',stale)
    response=client.post('/api/session/a/resources/attach-web',json={'window_id':'win','tab_id':'b'})
    assert response.status_code == 409


def test_attach_requires_control_authority_before_native_lookup(setup_attach, monkeypatch):
    from openprogram.runtime.execution.authorization import ExecutionAuthorizationError
    client, _, _, replies = setup_attach
    def deny(*args, **kwargs):
        assert kwargs['stop'] is True
        raise ExecutionAuthorizationError('not_found')
    monkeypatch.setattr(processes,'_authorize',deny)
    assert client.post('/api/session/a/resources/attach-web',json={'window_id':'win','tab_id':'b'}).status_code == 404
    assert replies == []


def test_reattach_already_used_page_does_not_add_a_second_row(setup_attach):
    client, ws, generation, _ = setup_attach
    binding = webtab.register_binding(ws, 'win', 'b', 'native-b', allow_background=True)
    key = webtab.binding_page_key(binding)
    store = BrowserResourceStore()
    store.retain(page_key=key, window_id='win', tab_id='b', connection_generation=generation,
                 session_id='a', conversation_session_id='a', execution_id='exec-a')
    response = client.post('/api/session/a/resources/attach-web', json={'window_id':'win','tab_id':'b'})
    assert response.status_code == 200
    assert len(response.json()['items']) == 1
    assert response.json()['items'][0]['execution_id'] == 'exec-a'
    assert len([a for a in store.associations_for_page(key) if a['conversation_session_id']=='a']) == 1


def test_attachment_response_uses_same_branch_identity_as_resource_snapshot(setup_attach, tmp_path, monkeypatch):
    from openprogram.state.store.session.nodes import Call
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.session_store import SessionStore
    client, ws, generation, _ = setup_attach
    db = SessionStore(tmp_path / 'sessions')
    writer = SessionNodeWriter(db, 'a')
    writer.append(Call(id='u1', role='user', predecessor='ROOT', seq=1))
    writer.append(Call(id='a1', role='llm', predecessor='u1', seq=2))
    db.set_head('a', 'a1')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    monkeypatch.setattr('openprogram.runtime.execution.default_store', lambda: None)
    binding = webtab.register_binding(ws, 'win', 'b', 'native-b', allow_background=True)
    key = webtab.binding_page_key(binding)
    BrowserResourceStore().retain(page_key=key, window_id='win', tab_id='b', connection_generation=generation,
        session_id='a', conversation_session_id='a', user_message_id='u1', assistant_message_id='a1')
    attached = client.post('/api/session/a/resources/attach-web', json={'window_id':'win','tab_id':'b'}).json()['items']
    from openprogram.programs.applications.browser.pages import project_conversation_resources
    snapshot, *_ = project_conversation_resources('a')
    assert snapshot[0]['branch_id'] is not None
    assert [(r['id'], r['branch_id']) for r in attached] == [(r['id'], r['branch_id']) for r in snapshot]
