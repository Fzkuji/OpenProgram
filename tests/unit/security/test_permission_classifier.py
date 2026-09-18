"""Auto permission requires an explicit boolean approval from its classifier."""
import asyncio
import json
import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("safe", [True, False, "true", "false", 1, 0, None, [], {}])
def test_classifier_only_accepts_json_boolean_true(monkeypatch, safe):
    from openprogram.agent.permissions.classifier import auto_classify_tool

    monkeypatch.setattr("openprogram.providers.models.get_model", lambda *_: object())

    async def classify(*_args):
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
            "safe": safe, "reason": "classified",
        }))])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert blocked is (safe is not True)
    assert reason == "classified"


def test_classifier_uses_configured_main_model(monkeypatch):
    from openprogram.agent.permissions.classifier import auto_classify_tool

    chosen = object()
    monkeypatch.setattr("openprogram.providers.models.get_model", lambda *_: None)
    monkeypatch.setattr(
        "openprogram.providers.default_llm._read_default_model", lambda: None,
    )
    monkeypatch.setattr(
        "openprogram.agent.internals._model_tools.load_agent_profile",
        lambda _agent_id: {"model": {"provider": "openai-codex", "id": "gpt-5.6-sol"}},
    )
    monkeypatch.setattr(
        "openprogram.agent.internals._model_tools.resolve_model",
        lambda _profile, _override: chosen,
    )

    async def classify(model, *_args):
        assert model is chosen
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true, "reason": "ok"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert blocked is False
    assert reason == "ok"


def test_classifier_prefers_grok_46_when_available(monkeypatch):
    from openprogram.agent.permissions.classifier import auto_classify_tool

    grok = object()
    monkeypatch.setattr(
        "openprogram.providers.models.get_model",
        lambda provider, model: grok
        if (provider, model) == ("xai-subscription", "grok-4.6") else None,
    )

    async def classify(model, *_args):
        assert model is grok
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true, "reason": "ok"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    blocked, _ = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert blocked is False


def test_classifier_falls_back_after_grok_failure(monkeypatch):
    from openprogram.agent.permissions.classifier import auto_classify_tool

    grok, sol = object(), object()
    monkeypatch.setattr(
        "openprogram.providers.models.get_model",
        lambda provider, model: (
            grok if (provider, model) == ("xai-subscription", "grok-4.6")
            else sol if (provider, model) == ("openai-codex", "gpt-5.6-sol")
            else None
        ),
    )
    monkeypatch.setattr(
        "openprogram.providers.default_llm._read_default_model",
        lambda: ("openai-codex", "gpt-5.6-sol"),
    )
    monkeypatch.setattr(
        "openprogram.agent.internals._model_tools.resolve_model",
        lambda *_: None,
    )

    async def classify(model, *_args):
        if model is grok:
            raise RuntimeError("expired")
        assert model is sol
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true, "reason": "ok"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert blocked is False
    assert reason == "ok"


def test_classifier_checks_complete_arguments(monkeypatch):
    from openprogram.agent.permissions.classifier import auto_classify_tool
    monkeypatch.setattr("openprogram.providers.models.get_model", lambda *_: object())
    command = "echo " + "x" * 1200 + "; rm -rf /important"

    async def classify(_model, context, _options):
        assert command in context.messages[0].content
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": false, "reason": "destructive suffix"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    assert tuple(asyncio.run(auto_classify_tool("bash", {"command": command}))) == (True, "destructive suffix")


def test_classifier_never_approves_partial_oversized_input(monkeypatch):
    from openprogram.agent.permissions.classifier import auto_classify_tool
    calls = []
    monkeypatch.setattr("openprogram.providers.models.get_model", lambda *_: object())

    async def classify(*args):
        calls.append(args)
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true}')])

    monkeypatch.setattr(importlib.import_module("openprogram.providers.stream"), "complete_simple", classify)
    blocked, _ = asyncio.run(auto_classify_tool("bash", {"command": "x" * 100000}))
    assert blocked
    assert not calls
