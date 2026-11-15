"""Saved effort changes affect the next runtime without mutating an active one."""

from types import SimpleNamespace

from openprogram.runtime.agent_registry import manager, runtime_registry


def test_agent_runtime_cache_tracks_effort_and_inheritance(monkeypatch):
    built = []

    def build(provider, model_id):
        runtime = SimpleNamespace(thinking_level="medium")
        built.append(runtime)
        return runtime

    monkeypatch.setattr(runtime_registry, "_cache", {})
    monkeypatch.setattr(runtime_registry, "_build_runtime", build)
    agent = manager.AgentSpec(id="example", thinking_effort="high")
    high = runtime_registry.get_runtime_for(agent)
    assert high.thinking_level == "high"
    assert runtime_registry.get_runtime_for(agent) is high
    agent.thinking_effort = "low"
    low = runtime_registry.get_runtime_for(agent)
    assert low.thinking_level == "low"
    assert low is not high
    assert high.thinking_level == "high"
    agent.thinking_effort = ""
    inherited = runtime_registry.get_runtime_for(agent)
    assert inherited.thinking_level == "medium"
    assert inherited is not low
    assert len(built) == 3


def test_agent_without_a_model_runs_on_the_last_used_model(monkeypatch):
    built = []
    monkeypatch.setattr(runtime_registry, "_cache", {})
    monkeypatch.setattr(
        runtime_registry, "_build_runtime",
        lambda provider, model_id: built.append((provider, model_id)) or SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openprogram.models.providers.catalog.last_used.read_last_used_model",
        lambda: ("openai", "gpt-x"),
    )
    runtime_registry.get_runtime_for(manager.AgentSpec(id="example"))
    assert built == [("openai", "gpt-x")]


def test_no_model_used_yet_is_an_error_not_a_guess():
    import pytest

    with pytest.raises(RuntimeError, match="No model has been used yet"):
        runtime_registry._build_runtime("", "")
