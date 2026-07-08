"""F2: Agent construction must not depend on any specific provider/model
being enabled.

Pre-fix, ``Agent.__init__`` seeded ``AgentState`` with
``get_model("google", "gemini-2.5-flash-lite-preview-06-17")``. After the
enabled-models migration that lookup returns ``None`` whenever the google
provider has no enabled rows, and pydantic then rejects
``AgentState(model=None)`` — crashing EVERY agent construction (and thus the
whole AgentSession path) regardless of which provider the caller actually
wants. The real model is set immediately afterward via ``initial_state``.
"""
from __future__ import annotations

import openprogram.agent.agent as agent_mod
from openprogram.agent.agent import Agent, AgentOptions
from openprogram.agent.types import AgentState


def test_agent_constructs_when_no_model_enabled(monkeypatch):
    # Simulate the post-migration state: get_model returns None for anything
    # (no provider enabled). Construction must still succeed — the seed model
    # is a transient placeholder, not a hard dependency.
    monkeypatch.setattr(agent_mod, "get_model", lambda *a, **k: None)
    a = Agent()  # must not raise ValidationError
    assert a._state.model is None


def test_agent_state_accepts_none_model():
    # The field itself tolerates a None placeholder at construction.
    st = AgentState(system_prompt="", model=None)
    assert st.model is None


def test_initial_state_model_overrides_seed(monkeypatch):
    monkeypatch.setattr(agent_mod, "get_model", lambda *a, **k: None)
    sentinel = object()
    a = Agent(AgentOptions(initial_state={"model": sentinel}))
    assert a._state.model is sentinel
