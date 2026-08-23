"""engine.compact short-circuits when nothing is left to fold."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from openprogram.context.engine import DefaultContextEngine
from openprogram.context.summarize import Summary
from openprogram.context.types import CompactResult


def _history(n: int) -> list[dict]:
    return [
        {"id": f"m{i}", "role": "user" if i % 2 == 0 else "llm",
         "content": f"turn {i} " * 8}
        for i in range(n)
    ]


@pytest.fixture
def engine(monkeypatch):
    eng = DefaultContextEngine()
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db",
        lambda: type("DB", (), {
            "get_session": staticmethod(lambda _sid: {}),
            "update_session": staticmethod(lambda *_a, **_k: None),
        })(),
    )
    monkeypatch.setattr(eng, "_occupancy_tokens", lambda sid, hist: 40_737)
    return eng


def test_already_folded_history_skips_the_summariser(engine, monkeypatch):
    hist = _history(12)
    monkeypatch.setattr(
        "openprogram.context.persistence.rendered_history",
        lambda *_a, **_k: hist,
    )
    monkeypatch.setattr(engine.summarizer, "find_cut_index", lambda *a, **k: 0)
    llm = AsyncMock(side_effect=AssertionError("must not call the summariser"))
    monkeypatch.setattr(engine.summarizer, "summarise", llm)

    events: list[dict] = []
    result = asyncio.run(engine.compact(
        agent=None, session_id="s1", model=None,
        on_event=lambda env: events.append(env),
        user_initiated=True,
    ))

    assert result.no_op is True
    assert result.tokens_before == result.tokens_after == 40_737
    llm.assert_not_awaited()
    types = [e["data"]["type"] for e in events]
    assert "compaction_started" not in types
    assert types == ["compaction_finished"]
    assert events[0]["data"]["no_op"] is True


def test_empty_summary_is_no_op_not_success(engine, monkeypatch):
    hist = _history(20)
    monkeypatch.setattr(
        "openprogram.context.persistence.rendered_history",
        lambda *_a, **_k: hist,
    )
    monkeypatch.setattr(engine.summarizer, "find_cut_index", lambda *a, **k: 8)

    async def _empty(**_k):
        return Summary(
            summary_text="", cut_idx=0, summarised_count=0,
            summarised_tokens=0, previous_summary_used=False, duration_ms=1,
        )

    monkeypatch.setattr(engine.summarizer, "summarise", _empty)
    monkeypatch.setattr(
        engine.persister, "insert_summary_node",
        lambda **_k: (_ for _ in ()).throw(AssertionError("no insert")),
    )

    events: list[dict] = []
    result = asyncio.run(engine.compact(
        agent=None, session_id="s1", model=None,
        on_event=lambda env: events.append(env),
        user_initiated=True,
    ))

    assert isinstance(result, CompactResult)
    assert result.no_op is True
    types = [e["data"]["type"] for e in events]
    assert "compaction_started" in types
    finished = [e["data"] for e in events if e["data"]["type"] == "compaction_finished"]
    assert finished and finished[0]["no_op"] is True
    assert finished[0].get("summarised_count", 0) == 0
