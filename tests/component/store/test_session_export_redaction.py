"""Shareable session exports redact labels and structured arguments."""
import json

import pytest

from openprogram.store import SessionStore
from openprogram.store.session.export import export_session


@pytest.mark.parametrize('fmt', ['md', 'html'])
@pytest.mark.parametrize('field', ['title', 'tool_name', 'arguments', 'json_arguments'])
def test_export_redacts_all_public_fields(tmp_path, fmt, field):
    secret = 'sk-SYNTHETIC_TEST_12345678' if field in {'title', 'tool_name'} else 'SYNTHETIC_PASSWORD_123'
    store = SessionStore(tmp_path / 'sessions')
    try:
        store.create_session('export', 'main', title=secret if field == 'title' else 'Safe title')
        store.append_message('export', {'id': 'user', 'role': 'user', 'content': 'Safe content'})
        arguments = {'account': {'password': secret, 'label': 'safe-label'}}
        if field == 'json_arguments':
            arguments = json.dumps(arguments)
        store.append_message('export', {
            'id': 'tool', 'role': 'tool', 'content': 'Safe result', 'caller': 'user',
            'function': secret if field == 'tool_name' else 'safe_tool',
            'extra': {'tool_use': {'arguments': arguments if field in {'arguments', 'json_arguments'} else {}}},
        })
        output = export_session('export', fmt, store=store)
        assert secret not in output
        assert '[secret removed]' in output
        assert 'Safe content' in output
        assert 'Safe result' in output
        if field in {'arguments', 'json_arguments'}:
            assert 'safe-label' in output
        # Export only transforms its result; source data remains available.
        if field == 'title':
            assert store.get_session('export')['title'] == secret
    finally:
        store.close()
