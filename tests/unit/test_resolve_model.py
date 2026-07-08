"""Regression tests for ``dispatcher._resolve_model``.

Background: agent.json now stores ``model`` as a ``{"provider", "id"}``
dict (cli_chat.py and setup.py both write that shape). The dispatcher
historically only handled the legacy string form, so a dict reached
``Model(id=requested, name=requested, ...)`` and pydantic blew up the
moment a channels-routed message arrived ("Input should be a valid
string"). These tests pin the dict-tolerant resolver behavior so the
regression can't sneak back in.
"""
from __future__ import annotations

import pytest

from openprogram.agent.dispatcher import _resolve_model
from openprogram.providers.models import get_model
from openprogram.providers.utils.errors import ErrorReason, LLMError


def test_dict_model_normalizes_to_string() -> None:
    """Profile model = {"provider": "openai-codex", "id": "gpt-5.5"}
    must resolve to a real Model, not a dict-id stub."""
    m = _resolve_model({
        "model": {"provider": "openai-codex", "id": "gpt-5.5"},
    })
    assert isinstance(m.id, str)
    assert m.id == "gpt-5.5"
    assert m.provider == "openai-codex"


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


def test_codex_55_exposes_full_thinking_levels() -> None:
    """Runtime-injected Codex models keep the abstract picker set.

    gpt-5.5 dropped ``minimal`` (the API 400s on it), so its picker is
    low/medium/high/xhigh/max — see ``thinking_spec.supports_minimal_effort``.
    """
    import openprogram.providers.openai_codex.runtime  # noqa: F401

    m = get_model("openai-codex", "gpt-5.5")
    assert m is not None
    assert m.thinking_levels == ["low", "medium", "high", "xhigh", "max"]
    assert m.default_thinking_level == "xhigh"
