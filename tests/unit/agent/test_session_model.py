"""Session chat model is the only source of truth for a turn."""
from __future__ import annotations

import pytest

from openprogram.agent import session_model as sm


def test_read_pin_from_memory(monkeypatch):
    import openprogram.webui.server as server

    monkeypatch.setattr(
        server,
        "_sessions",
        {"s1": {"provider_override": "openai-codex", "model_override": "gpt-5.6-sol"}},
        raising=False,
    )
    monkeypatch.setattr(sm, "default_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
    assert sm.read_session_chat_model("s1") == ("openai-codex", "gpt-5.6-sol")
    assert sm.ensure_session_chat_model("s1") == ("openai-codex", "gpt-5.6-sol")


def test_missing_pin_freezes_default_once(monkeypatch):
    import openprogram.webui.server as server

    conv = {}
    monkeypatch.setattr(server, "_sessions", {"s2": conv}, raising=False)
    monkeypatch.setattr(sm, "default_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
    monkeypatch.setattr(sm, "_read_db_pin", lambda sid: (None, None))
    writes = []
    monkeypatch.setattr(
        sm,
        "write_session_chat_model",
        lambda sid, p, m: writes.append((sid, p, m)) or conv.update(
            provider_override=p, model_override=m
        ),
    )
    assert sm.ensure_session_chat_model("s2") == ("xai-subscription", "grok-4.6")
    assert writes == [("s2", "xai-subscription", "grok-4.6")]


def test_sol_pin_never_falls_back_to_grok(monkeypatch):
    import openprogram.webui.server as server

    monkeypatch.setattr(
        server,
        "_sessions",
        {"s3": {"provider_override": "openai-codex", "model_override": "gpt-5.6-sol"}},
        raising=False,
    )
    monkeypatch.setattr(sm, "default_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
    assert sm.ensure_session_chat_model("s3") == ("openai-codex", "gpt-5.6-sol")
    assert sm.override_string(*sm.ensure_session_chat_model("s3")) == "openai-codex/gpt-5.6-sol"


def test_write_pin_raises_when_persist_fails(monkeypatch):
    import openprogram.webui.server as server

    monkeypatch.setattr(server, "_sessions", {"s4": {}}, raising=False)

    class DB:
        def update_session(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    import openprogram.agent.session_db as session_db
    monkeypatch.setattr(session_db, "default_db", lambda: DB())
    with pytest.raises(RuntimeError, match="db down"):
        sm.write_session_chat_model("s4", "openai-codex", "gpt-5.6-sol")
