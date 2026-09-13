"""Task complexity is internal, bounded, and stable on resume."""
import importlib
from types import SimpleNamespace

import pytest

from openprogram.programs.workflow.goal.roles import prepare_roles
from openprogram.agentic_programming.function import CancelledError


def runtime(levels):
    return SimpleNamespace(provider_id="test", thinking_level="medium",
                           api_model=SimpleNamespace(id="model", provider="test", thinking_levels=levels))


def configure(current, **overrides):
    return prepare_roles(None, current, model="", effort="", timeout_s=300,
                         judge_model="", judge_timeout_s=300, prompt="prove the theorem",
                         **{"judge_effort": "", **overrides})


def test_selects_supported_effort_and_reuses_saved_roles(monkeypatch):
    calls = []
    def select(*args, **kwargs):
        calls.append(kwargs)
        return "high"
    monkeypatch.setattr(importlib.import_module("openprogram.agentic_programming.llm"), "llm", select)
    current = runtime(["low", "medium", "high"])
    saved, _ = configure(current, judge_effort="low")
    assert saved["work"]["effort"] == "high"
    assert saved["judge"]["effort"] == "low"
    assert calls[0] == {"choices": ["low", "medium", "high"], "timeout_s": 30}
    again, _ = prepare_roles(saved, current, model="", effort="", timeout_s=300,
                            judge_model="", judge_effort="", judge_timeout_s=300, prompt="different task")
    assert again == saved and len(calls) == 1


@pytest.mark.parametrize("response", ["bad output", RuntimeError("offline"), {"effort": "high"}])
def test_invalid_or_failed_selection_retains_defaults(monkeypatch, response):
    def select(*a, **k):
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(importlib.import_module("openprogram.agentic_programming.llm"), "llm", select)
    saved, _ = configure(runtime(["low", "medium", "high"]))
    assert saved["work"]["effort"] == "medium"


def test_unsupported_reasoning_skips_request_and_cancellation_propagates(monkeypatch):
    def cancel(*a, **k):
        raise CancelledError("stopped")
    monkeypatch.setattr(importlib.import_module("openprogram.agentic_programming.llm"), "llm", cancel)
    saved, _ = configure(runtime([]))
    assert saved["work"]["effort"] == "medium"
    with pytest.raises(CancelledError):
        configure(runtime(["low", "high"]))
