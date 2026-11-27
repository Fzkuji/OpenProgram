"""Regression tests for ``dispatcher._resolve_model``.

Background: agent.json now stores ``model`` as a ``{"provider", "id"}``
dict (cli/chat.py and setup.py both write that shape). The dispatcher
historically only handled the legacy string form, so a dict reached
``Model(id=requested, name=requested, ...)`` and pydantic blew up the
moment a channels-routed message arrived ("Input should be a valid
string"). These tests pin the dict-tolerant resolver behavior so the
regression can't sneak back in.
"""
from __future__ import annotations

import pytest

from openprogram.runtime.conversation.turn_runtime import _resolve_model
from openprogram.models.providers.utils.failures.errors import ErrorReason, LLMError

from tests.component.providers._registry_fixture import install_registry


@pytest.fixture(autouse=True)
def _seed_registry(monkeypatch):
    # The runtime registry now holds only the user's enabled models, so these
    # resolver tests must seed the exact ids they reference (empty config in CI
    # would otherwise leave get_model returning None).
    install_registry(monkeypatch, {
        "openai": {"models": [
            {"id": "gpt-4o", "name": "GPT-4o", "api": "openai-responses"},
        ]},
        "anthropic": {"models": [
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "api": "anthropic-messages"},
        ]},
    })


def test_dict_model_normalizes_to_string() -> None:
    """Profile model = {"provider": "anthropic", "id": "claude-sonnet-4-6"}
    must resolve to a real Model, not a dict-id stub."""
    m = _resolve_model({
        "model": {"provider": "anthropic", "id": "claude-sonnet-4-6"},
    })
    assert isinstance(m.id, str)
    assert m.id == "claude-sonnet-4-6"
    assert m.provider == "anthropic"


def test_bare_string_model_still_works() -> None:
    """Legacy ``"<id>"`` string form keeps probing known providers."""
    m = _resolve_model({"model": "gpt-4o"})
    assert isinstance(m.id, str)
    assert m.id == "gpt-4o"


def test_slash_provider_string_keeps_working() -> None:
    """``"<provider>/<id>"`` resolves directly via that provider."""
    m = _resolve_model({"model": "openai/gpt-4o"})
    assert m.id == "gpt-4o"
    assert m.provider == "openai"


def test_missing_model_raises_clear_error() -> None:
    """No model configured anywhere → LLMError telling the user to pick
    one, NOT a stub that silently fires requests at api.openai.com."""
    with pytest.raises(LLMError) as exc:
        _resolve_model({})
    assert exc.value.reason == ErrorReason.INVALID_REQUEST
    assert "No model is configured" in exc.value.message


def test_unknown_model_raises_instead_of_swapping() -> None:
    """An explicit pick the registry can't satisfy must fail honestly —
    never route through another provider or a stub default ('I switched
    to the free model but it answered as GPT')."""
    with pytest.raises(LLMError) as exc:
        _resolve_model({"model": {"provider": "openrouter",
                                  "id": "gone/after-refetch:free"}})
    assert exc.value.reason == ErrorReason.INVALID_REQUEST
    assert "openrouter/gone/after-refetch:free" in exc.value.message


def test_unknown_bare_id_raises() -> None:
    """A bare id (no provider) that no provider knows also fails clearly."""
    with pytest.raises(LLMError):
        _resolve_model({"model": {"id": "mystery-model"}})

