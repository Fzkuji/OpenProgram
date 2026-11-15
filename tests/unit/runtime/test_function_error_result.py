"""Failed browser/tool results must remain failed in the persisted call."""
import asyncio

import pytest

from openprogram.runtime.agent import Agent
from openprogram.models.providers.types import AgentToolResult
from openprogram.programs._host.runtime import ToolReturn
from openprogram.models.providers.types import TextContent
from openprogram.state.store import SessionNodeWriter, SessionStore, _store


@pytest.mark.parametrize('async_body', [False, True])
@pytest.mark.parametrize('result_type', ['return', 'result'])
def test_failed_function_result_persists_error(tmp_path, monkeypatch, async_body, result_type):
    db = SessionStore(tmp_path / 'sessions')
    db.create_session('s', 'main')
    monkeypatch.setattr('openprogram.runtime.context.session_db.default_db', lambda: db)
    outcome = (ToolReturn(text='browser unavailable', is_error=True) if result_type == 'return'
               else AgentToolResult(content=[TextContent(text='browser unavailable')], is_error=True))

    class ProbeAgent(Agent):
        method_options = {
            'sync_probe': {'tool': True, 'register_globally': False, 'name': 'sync_probe'},
            'async_probe': {'tool': True, 'register_globally': False, 'name': 'async_probe'},
        }

        def sync_probe(self):
            return outcome

        async def async_probe(self):
            return outcome

    instance = ProbeAgent()
    probe = instance.async_probe if async_body else instance.sync_probe
    token = _store.set(SessionNodeWriter(db, 's'))
    try:
        returned = asyncio.run(probe()) if async_body else probe()
        assert returned is outcome
        nodes = [n for n in db.get_nodes('s') if n.is_code()]
        assert len(nodes) == 1
        assert nodes[0].metadata['status'] == 'error'
    finally:
        _store.reset(token)
        db.close()
