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

from openprogram.agent.dispatcher import _resolve_model
from openprogram.providers.models import get_model


def test_dict_model_normalizes_to_string() -> None:
    """Profile model = {"provider": "chatgpt-subscription", "id": "gpt-5.5"}
    must resolve to a real Model, not a dict-id stub."""
    m = _resolve_model({
        "model": {"provider": "chatgpt-subscription", "id": "gpt-5.5"},
    })
    assert isinstance(m.id, str)
    assert m.id == "gpt-5.5"
    assert m.provider == "chatgpt-subscription"


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


def test_missing_model_falls_back_to_stub_string() -> None:
    """No model field anywhere → stub Model with str id, not None /
    dict — otherwise pydantic raises before the caller can give the
    user a useful error."""
    m = _resolve_model({})
    assert isinstance(m.id, str)
    assert m.id == "stub"


def test_partial_dict_falls_through_safely() -> None:
    """``{"id": "x"}`` with no provider should still produce a string
    id, even if the registered model registry doesn't know it."""
    m = _resolve_model({"model": {"id": "mystery-model"}})
    assert isinstance(m.id, str)
    assert m.id == "mystery-model"


def test_codex_55_exposes_full_thinking_levels() -> None:
    """Runtime-injected Codex models must keep the full abstract picker set."""
    import openprogram.providers.openai_codex.runtime  # noqa: F401

    m = get_model("chatgpt-subscription", "gpt-5.5")
    assert m is not None
    assert m.thinking_levels == ["minimal", "low", "medium", "high", "xhigh"]
    assert m.default_thinking_level == "xhigh"
