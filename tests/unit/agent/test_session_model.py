"""Session chat model is the only source of truth for a turn."""
from __future__ import annotations

import pytest

from openprogram.runtime.conversation import session_model as sm


def test_read_pin_from_memory(monkeypatch):
    import openprogram.interfaces.server.server as server

    monkeypatch.setattr(
        server,
        "_sessions",
        {"s1": {"provider_override": "openai-codex", "model_override": "gpt-5.6-sol"}},
        raising=False,
    )
    monkeypatch.setattr(sm, "new_session_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
    assert sm.read_session_chat_model("s1") == ("openai-codex", "gpt-5.6-sol")
    assert sm.ensure_session_chat_model("s1") == ("openai-codex", "gpt-5.6-sol")


def test_a_branch_without_its_own_model_follows_the_agent_and_is_never_pinned(monkeypatch):
    import openprogram.interfaces.server.server as server

    conv = {}
    monkeypatch.setattr(server, "_sessions", {"s2": conv}, raising=False)
    monkeypatch.setattr(sm, "new_session_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
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
    # Nothing is written: the next turn reads the Agent's model again.
    assert writes == [] and conv == {}
    monkeypatch.setattr(sm, "new_session_chat_model", lambda agent_id=None: ("anthropic", "claude-y"))
    assert sm.ensure_session_chat_model("s2") == ("anthropic", "claude-y")


def test_sol_pin_never_falls_back_to_grok(monkeypatch):
    import openprogram.interfaces.server.server as server

    monkeypatch.setattr(
        server,
        "_sessions",
        {"s3": {"provider_override": "openai-codex", "model_override": "gpt-5.6-sol"}},
        raising=False,
    )
    monkeypatch.setattr(sm, "new_session_chat_model", lambda agent_id=None: ("xai-subscription", "grok-4.6"))
    assert sm.ensure_session_chat_model("s3") == ("openai-codex", "gpt-5.6-sol")
    assert sm.override_string(*sm.ensure_session_chat_model("s3")) == "openai-codex/gpt-5.6-sol"


def test_write_pin_raises_when_persist_fails(monkeypatch):
    import openprogram.interfaces.server.server as server

    monkeypatch.setattr(server, "_sessions", {"s4": {}}, raising=False)

    class DB:
        def update_session(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    import openprogram.runtime.context.session_db as session_db
    monkeypatch.setattr(session_db, "default_db", lambda: DB())
    with pytest.raises(RuntimeError, match="db down"):
        sm.write_session_chat_model("s4", "openai-codex", "gpt-5.6-sol")


def test_new_session_uses_agent_model_then_last_used_then_nothing(monkeypatch):
    from types import SimpleNamespace
    from openprogram.runtime.agent_registry import manager
    from openprogram.models.providers.catalog import last_used

    agent_model = SimpleNamespace(provider="", id="")
    monkeypatch.setattr(manager, "get", lambda agent_id: SimpleNamespace(model=agent_model))
    used = []
    monkeypatch.setattr(last_used, "read_last_used_model", lambda: used[0] if used else None)

    assert sm.new_session_chat_model("main") == (None, None)
    used.append(("openai-codex", "gpt-6.1-sol"))
    assert sm.new_session_chat_model("main") == ("openai-codex", "gpt-6.1-sol")
    agent_model.provider, agent_model.id = "anthropic", "claude-x"
    assert sm.new_session_chat_model("bot") == ("anthropic", "claude-x")
