"""Dispatcher setup failures release a successfully entered turn."""
from contextvars import Context
from types import SimpleNamespace

import pytest

from openprogram.agent import dispatcher
from openprogram.agent.dispatcher.types import TurnRequest
from openprogram.agent.run_control import get_current_execution_id, get_current_session_id
from openprogram.store import _current_turn_id


@pytest.mark.parametrize("boundary", ["stream", "attachments", "placeholder"])
def test_setup_failure_restores_turn_context(monkeypatch, boundary):
    db = SimpleNamespace(
        get_session=lambda _: {}, message_exists=lambda *_: True,
        update_session=lambda *_a, **_kw: None,
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.context.persistence.rendered_history", lambda *_a, **_kw: [])
    monkeypatch.setattr("openprogram.store.SessionNodeWriter", lambda *_: object())
    monkeypatch.setattr("openprogram.providers.registry.create_runtime", lambda: None)
    monkeypatch.setattr("openprogram.worktree.manager.get_manager", lambda: None)
    monkeypatch.setattr("openprogram.store.project.project_commit.snapshot_baseline", lambda _: None)
    monkeypatch.setattr(dispatcher, "prepare_turn", lambda **_: ({}, []))

    def fail(*_args, **_kwargs):
        raise RuntimeError("setup failed")

    targets = {
        "stream": "openprogram.agent.dispatcher.make_stream_tap",
        "attachments": "openprogram.programs.tools.interaction.send_file.begin_turn",
        "placeholder": "openprogram.agent.dispatcher.turn_writer.TurnWriter.open_placeholder",
    }
    monkeypatch.setattr(targets[boundary], fail)

    def scenario():
        req = TurnRequest(session_id="setup-session", agent_id="main", user_text="test", source="test")
        with pytest.raises(RuntimeError, match="setup failed"):
            if boundary == "stream":
                dispatcher.process_agent_continuation(SimpleNamespace(
                    request=req, assistant_message_id="reply",
                    state=SimpleNamespace(payload={"turn": {"user_message_id": "user"}}),
                ))
            else:
                dispatcher._process_turn_once(req)
        assert _current_turn_id.get() is None
        assert get_current_execution_id() is None
        assert get_current_session_id() is None
    Context().run(scenario)
