"""A saved setting that names a withdrawn provider is ignored, like an unknown id."""
from __future__ import annotations

import pytest

from openprogram.models.providers.catalog import enabled_models
from openprogram.models.providers.catalog.last_used import last_used_in_config

_CFG = {
    "openai-codex": {"enabled": True, "models": [{"id": "gpt-5.5", "enabled": True}]},
    "github-copilot": {"enabled": True},
}


@pytest.fixture
def saved_config(monkeypatch):
    from openprogram.models.providers.settings import config_read, storage

    monkeypatch.setattr(storage, "_read_providers_cfg", lambda: _CFG)
    monkeypatch.setattr(config_read, "read_providers_config", lambda: _CFG)
    yield
    monkeypatch.undo()
    enabled_models.reload()


def test_a_withdrawn_provider_gets_no_row(saved_config):
    from openprogram.models.listing import listing

    ids = {row["id"] for row in listing.list_providers()}
    assert not ids & {"openai-codex", "github-copilot"}


def test_its_saved_models_are_not_registered(saved_config):
    assert not [key for key in enabled_models.reload() if "codex" in str(key)]
    assert enabled_models.register_model_from_config("openai-codex", "gpt-5.5") is False


def test_a_last_used_model_on_it_reads_as_none():
    assert last_used_in_config({"last_used_model": {"provider": "openai-codex", "model": "gpt-5.5"}}) is None


def test_an_agent_model_on_it_reads_as_none():
    from openprogram.runtime.agent_registry.manager import AgentSpec

    spec = AgentSpec.from_dict({"id": "main", "model": {"provider": "openai-codex", "id": "gpt-5.5"}})
    assert (spec.model.provider, spec.model.id) == ("", "")
    kept = AgentSpec.from_dict({"id": "main", "model": {"provider": "openai", "id": "gpt-5.5"}})
    assert (kept.model.provider, kept.model.id) == ("openai", "gpt-5.5")


def test_a_session_pin_on_it_reads_as_none(monkeypatch):
    from openprogram.runtime.conversation.session_settings import session_model

    monkeypatch.setattr(session_model, "_session_pin", lambda _sid: ("openai-codex", "gpt-5.5"))
    assert session_model.read_session_chat_model("s1") == (None, None)
    monkeypatch.setattr(session_model, "_session_pin", lambda _sid: ("openai", "gpt-5.5"))
    assert session_model.read_session_chat_model("s1") == ("openai", "gpt-5.5")


def test_a_conversation_override_on_it_falls_back_to_the_last_used_model(monkeypatch):
    from openprogram.interfaces.server import runtime_management as rm

    monkeypatch.setattr(rm, "_enabled_model_keys", lambda: {("openai", "gpt-5.5")})
    monkeypatch.setattr(rm, "_last_used_chat_model", lambda: ("openai", "gpt-5.5"))
    conv = {"provider_override": "openai-codex", "model_override": "gpt-5.5"}
    assert rm._resolve_session_provider_model(conv, snapshot=True) == ("openai", "gpt-5.5")


def test_a_saved_pick_on_it_is_no_pick():
    from openprogram.models.providers.utils.failures.errors import LLMError
    from openprogram.runtime.conversation.model_tools import NO_MODEL_CONFIGURED, resolve_model

    with pytest.raises(LLMError) as raised:
        resolve_model({}, "openai-codex/gpt-5.5")
    assert NO_MODEL_CONFIGURED.split(".")[0] in str(raised.value)
