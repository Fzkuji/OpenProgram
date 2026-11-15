"""A branch that follows its Agent onto a smaller window is compacted with
the model the history was written with (context/compaction.html, "Model
downshift"; Codex's ModelDownshift).

- The earlier, larger model summarises; the turn then goes to the new one.
- When the earlier model fails, the turn's model summarises once.
- A model that is not smaller takes no part.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from openprogram.runtime.conversation.turn_runtime import loop_runner

BIG = SimpleNamespace(id="big", provider="p", context_window=200_000, max_tokens=8_000)
SMALL = SimpleNamespace(id="small", provider="p", context_window=32_000, max_tokens=4_000)


class _Engine:
    def __init__(self, fail_with: str | None = None):
        self.models: list = []
        self.triggers: list = []
        self.fail_with = fail_with

    def should_auto_compact(self, _prep):
        return True

    async def compact(self, **kwargs):
        self.models.append(kwargs["model"].id)
        self.triggers.append(kwargs.get("trigger"))
        if self.fail_with == kwargs["model"].id:
            return SimpleNamespace(summary_id=None, error="no credentials for big")
        return SimpleNamespace(summary_id="S", error=None)

    def prepare(self, **_kwargs):
        return SimpleNamespace(agent_messages=[])


def _run(monkeypatch, engine, previous):
    monkeypatch.setattr(loop_runner, "_downshift_model", lambda _req, _profile: previous)
    monkeypatch.setattr("openprogram.runtime.context.persistence.rendered_history",
                        lambda *_a, **_k: [{"id": "x"}])
    db = SimpleNamespace(get_session=lambda _sid: {})
    req = SimpleNamespace(session_id="c", history_override=None, background=False)
    loop_runner._auto_compact_new_turn(
        engine, req, db=db, on_event=lambda _e: None, agent_profile={}, model=SMALL, tools=[],
        system_prompt="", session={}, history=[], prep=SimpleNamespace(), head_id="a1")


@pytest.fixture(autouse=True)
def _windows(monkeypatch):
    monkeypatch.setattr("openprogram.runtime.context.tokens.real_context_window",
                        lambda model: getattr(model, "context_window", 0))


def test_the_earlier_larger_model_summarises(monkeypatch):
    engine = _Engine()
    _run(monkeypatch, engine, BIG)
    assert engine.models == ["big"] and engine.triggers == ["model_downshift"]


def test_when_the_earlier_model_fails_the_turns_model_summarises(monkeypatch):
    engine = _Engine(fail_with="big")
    _run(monkeypatch, engine, BIG)
    assert engine.models == ["big", "small"]


def test_a_model_that_is_not_smaller_takes_no_part(monkeypatch):
    engine = _Engine()
    # The turn's model is SMALL; an earlier model with a smaller or equal
    # window is not used.
    _run(monkeypatch, engine, SimpleNamespace(id="tiny", provider="p", context_window=16_000))
    assert engine.models == ["small"] and engine.triggers == [None]


def test_without_a_model_change_the_turns_model_summarises(monkeypatch):
    engine = _Engine()
    _run(monkeypatch, engine, None)
    assert engine.models == ["small"]
