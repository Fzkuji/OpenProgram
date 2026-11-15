"""The public edit route must preserve uploaded files through canonical admission."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from openprogram.interfaces.server.routes import chat_branches as routes


@pytest.mark.parametrize('payload', ['bad', [None], [{'data': 1}], [{}] * 21])
def test_edit_rejects_invalid_attachments(monkeypatch, payload):
    monkeypatch.setattr(routes, '_fork_user_turn_and_run', lambda *a, **kw: {})
    response = asyncio.run(routes.post_chat_edit({'session_id': 's', 'msg_id': 'u', 'content': 'x', 'attachments': payload}))
    assert response.status_code == 400


def test_edit_forwards_attachment_batch(monkeypatch):
    captured = {}
    def fork(*args, **kwargs):
        captured.update(kwargs)
        return {'msg_id': 'new'}
    monkeypatch.setattr(routes, '_fork_user_turn_and_run', fork)
    batch = [{'type': 'document', 'data': 'YQ==', 'filename': 'a.txt'}]
    response = asyncio.run(routes.post_chat_edit({'session_id': 's', 'msg_id': 'u', 'content': '', 'attachments': batch}))
    assert response.status_code == 200
    assert captured['attachments'] == batch


def test_edit_persists_then_dispatches_images_only(monkeypatch, tmp_path):
    from openprogram.interfaces.server import server
    import openprogram.runtime.context.session_db as db
    import openprogram.runtime.conversation.production_driver as driver
    import openprogram.runtime.conversation.workdir as workdir
    from openprogram.runtime.conversation.session_config import SessionRunConfig
    import openprogram.runtime.conversation.session_config as config
    import openprogram.runtime.permissions.rule as rules
    user = {'id': 'u', 'role': 'user', 'content': 'original', 'predecessor': 'ROOT'}
    conv = {'id': 's', 'messages': [user], 'agent_id': 'main'}
    captured = []
    class Adapter:
        def __init__(self, **kw): pass
        def admit(self, request, **kw):
            captured.append(request)
            return SimpleNamespace(execution_id='exec', status_version=0)
    monkeypatch.setattr(server, '_sessions', {'s': conv})
    monkeypatch.setattr(server, '_is_selected_run_active', lambda sid: False)
    monkeypatch.setattr(server, 'reserve_operation', lambda *a, **kw: None)
    monkeypatch.setattr(server, '_activate_run_reservation', lambda *a: True)
    monkeypatch.setattr(server, '_emit_running_task_event', lambda *a, **kw: None)
    monkeypatch.setattr(server, '_save_session', lambda *a: None)
    monkeypatch.setattr(server, '_append_msg', lambda c, m, **_kw: c['messages'].append(m))
    # The edited message's pictures are kept in the session folder, so later
    # requests send them again (store.session.media).
    session_dir = tmp_path / 'session'
    monkeypatch.setattr(db, 'default_db', lambda: SimpleNamespace(
        get_messages=lambda sid: [user], _session_dir=lambda sid: session_dir))
    monkeypatch.setattr(driver, 'CanonicalAgentAdapter', Adapter)
    monkeypatch.setattr(config, 'load_session_run_config', lambda sid: SessionRunConfig())
    monkeypatch.setattr(config, 'project_defaults', lambda sid: {})
    monkeypatch.setattr(rules, 'load_merged_rules', lambda sid: [])
    monkeypatch.setattr(workdir, 'session_workdir_for', lambda sid: tmp_path)
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread= lambda **kw: SimpleNamespace(start=lambda: None)))
    batch = [{'type': 'document', 'filename': 'a.txt', 'media_type': 'text/plain', 'data': base64.b64encode(b'document').decode()},
             {'type': 'image', 'filename': 'image.png', 'media_type': 'image/png', 'data': base64.b64encode(b'image').decode()}]
    response = asyncio.run(routes.post_chat_edit({'session_id': 's', 'msg_id': 'u', 'content': 'edited', 'attachments': batch}))
    assert response.status_code == 200, response.body
    assert (tmp_path / 'attachments/a.txt').read_bytes() == b'document'
    assert captured[0].attachments == [batch[1]]
    assert 'a.txt' in captured[0].user_text
    assert json.dumps(str(tmp_path))[1:-1] in captured[0].user_text, captured[0].user_text
    assert conv['messages'][-1]['content'] == captured[0].user_text
    assert len(conv['messages'][-1]['media']) == 1
    assert user['content'] == 'original'
