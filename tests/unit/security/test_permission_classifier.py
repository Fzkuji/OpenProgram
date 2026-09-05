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
