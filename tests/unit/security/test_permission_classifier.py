"""Auto permission requires an explicit boolean approval from its classifier.

The review runs on the conversation's own model, then on the last used
model. There is no separate or hard-coded classifier model.
"""
import asyncio
import json
import importlib
from types import SimpleNamespace

import pytest


def _candidates(monkeypatch, *, session=(None, None), last=None, models=None):
    """Route candidate lookup to fakes; ``models`` maps "provider/id" → model."""
    models = models if models is not None else {}
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_settings.session_model.read_session_chat_model", lambda _sid: session,
    )
    monkeypatch.setattr("openprogram.models.providers.catalog.last_used.read_last_used_model", lambda: last)

    def resolve(_profile, override):
        if override not in models:
            raise RuntimeError(f"unavailable {override}")
        return models[override]

    monkeypatch.setattr("openprogram.runtime.conversation.model_tools.resolve_model", resolve)


def _any_model(monkeypatch):
    _candidates(monkeypatch, session=("p", "m"), models={"p/m": object()})


@pytest.mark.parametrize("safe", [True, False, "true", "false", 1, 0, None, [], {}])
def test_classifier_only_accepts_json_boolean_true(monkeypatch, safe):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool

    _any_model(monkeypatch)

    async def classify(*_args):
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
            "safe": safe, "reason": "classified",
        }))])

    monkeypatch.setattr(importlib.import_module("openprogram.models.providers.streaming.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert blocked is (safe is not True)
    assert reason == "classified"


def test_classifier_runs_on_the_conversation_model(monkeypatch):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool

    sol, flash = object(), object()
    _candidates(
        monkeypatch,
        session=("openai", "gpt-6.1-sol"),
        last=("deepseek", "deepseek-flash"),
        models={"openai/gpt-6.1-sol": sol, "deepseek/deepseek-flash": flash},
    )

    async def classify(model, *_args):
        assert model is sol
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true, "reason": "ok"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.models.providers.streaming.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}, session_id="s1"))
    assert blocked is False
    assert reason == "ok"


def test_classifier_falls_back_to_last_used_model(monkeypatch):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool

    sol, flash = object(), object()
    _candidates(
        monkeypatch,
        session=("openai", "gpt-6.1-sol"),
        last=("deepseek", "deepseek-flash"),
        models={"openai/gpt-6.1-sol": sol, "deepseek/deepseek-flash": flash},
    )

    async def classify(model, *_args):
        if model is sol:
            raise RuntimeError("expired")
        assert model is flash
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true, "reason": "ok"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.models.providers.streaming.stream"), "complete_simple", classify)
    blocked, reason = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}, session_id="s1"))
    assert blocked is False
    assert reason == "ok"


def test_classifier_without_any_model_blocks(monkeypatch):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool

    _candidates(monkeypatch)
    verdict = asyncio.run(auto_classify_tool("write", {"path": "example.txt"}))
    assert verdict.blocked and verdict.code == "AUTO_CLASSIFIER_UNAVAILABLE"


def test_safe_auto_allowlist_names_real_tools():
    """A renamed tool must not silently fall out of the allowlist."""
    from openprogram import programs
    from openprogram.runtime.permissions.auto_mode.classifier import SAFE_AUTO_ALLOWLIST

    registered = set(programs.list_registered_agent_tools())
    assert sorted(SAFE_AUTO_ALLOWLIST - registered) == []
    assert {"task_start", "task_step", "task_list"} <= SAFE_AUTO_ALLOWLIST


def test_classifier_checks_complete_arguments(monkeypatch):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool
    _any_model(monkeypatch)
    command = "echo " + "x" * 1200 + "; rm -rf /important"

    async def classify(_model, context, _options):
        assert command in context.messages[0].content
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": false, "reason": "destructive suffix"}')])

    monkeypatch.setattr(importlib.import_module("openprogram.models.providers.streaming.stream"), "complete_simple", classify)
    assert tuple(asyncio.run(auto_classify_tool("bash", {"command": command}))) == (True, "destructive suffix")


def test_classifier_never_approves_partial_oversized_input(monkeypatch):
    from openprogram.runtime.permissions.auto_mode.classifier import auto_classify_tool
    calls = []
    _any_model(monkeypatch)

    async def classify(*args):
        calls.append(args)
        return SimpleNamespace(content=[SimpleNamespace(text='{"safe": true}')])

    monkeypatch.setattr(importlib.import_module("openprogram.models.providers.streaming.stream"), "complete_simple", classify)
    blocked, _ = asyncio.run(auto_classify_tool("bash", {"command": "x" * 100000}))
    assert blocked
    assert not calls


def test_attaching_a_file_to_the_reply_needs_no_review_and_is_not_a_network_send():
    """Auto mode once sent send_file to the classifier, which denied a deck
    as exfiltration; the model then only pasted paths for the rest of the chat."""
    from openprogram.runtime.permissions.auto_mode.classifier import safe_call
    from openprogram.runtime.permissions.tool_authority import capability_for_tool, decide_tool_authority

    assert safe_call("send_file", {"path": "/abs/plan.pptx"})
    assert capability_for_tool("send_file") == "fs.read"
    assert decide_tool_authority({"authority_tier": "owner"}, "send_file").allowed
    # A paired chat-app speaker may not read the owner's files, so may not be sent them.
    assert not decide_tool_authority({"authority_tier": "paired"}, "send_file").allowed
