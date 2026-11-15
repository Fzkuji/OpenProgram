"""Automatic compaction that keeps failing stops being tried for a while.

Three failures in a row of one conversation on one model hold automatic
compaction until five minutes have passed since the last; a summary that
installs, or a manual /compact, goes regardless.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.runtime.context.compaction.hold import (
    FAILS_MOST,
    HELD_FOR_S,
    CompactionHold,
    default_hold,
)
from openprogram.runtime.context.compaction.summarize import Summary

from tests.unit.context.test_compact_no_op import _history, engine  # noqa: F401 (fixture)

KEY = ("s1", "fake/m")


def test_three_failures_in_a_row_hold_until_the_pause_passes():
    hold = CompactionHold()
    for n in range(FAILS_MOST - 1):
        hold.failed(KEY, "429", now=float(n))
        assert hold.held(KEY, now=float(n)) is None
    hold.failed(KEY, "429", now=10.0)
    assert "3 failures" in hold.held(KEY, now=11.0)
    assert hold.held(KEY, now=10.0 + HELD_FOR_S) is None


def test_failures_far_apart_do_not_add_up():
    hold = CompactionHold()
    for n in range(FAILS_MOST):
        hold.failed(KEY, "429", now=n * (HELD_FOR_S + 1))
    assert hold.held(KEY, now=FAILS_MOST * (HELD_FOR_S + 1)) is None


def test_a_summary_that_installs_clears_the_count():
    hold = CompactionHold()
    for n in range(FAILS_MOST):
        hold.failed(KEY, "429", now=float(n))
    hold.done(KEY)
    assert hold.held(KEY, now=5.0) is None


@pytest.fixture
def failing(engine, monkeypatch):  # noqa: F811
    default_hold.clear()
    hist = _history(20)
    monkeypatch.setattr("openprogram.runtime.context.persistence.rendered_history",
                        lambda *_a, **_k: hist)
    monkeypatch.setattr(engine.summarizer, "find_cut_index", lambda *a, **k: 8)
    calls = []

    async def summarise(**_k):
        calls.append(1)
        return Summary(summary_text="", cut_idx=0, summarised_count=0, summarised_tokens=0,
                       previous_summary_used=False, duration_ms=1, error="429 Too Many Requests")

    monkeypatch.setattr(engine.summarizer, "summarise", summarise)
    yield engine, calls
    default_hold.clear()


def _compact(engine, *, user_initiated):  # noqa: F811
    return asyncio.run(engine.compact(agent=None, session_id="s1", model=None,
                                      on_event=lambda _e: None, user_initiated=user_initiated))


def test_the_engine_stops_asking_after_repeated_automatic_failures(failing):
    engine, calls = failing
    for _ in range(FAILS_MOST):
        _compact(engine, user_initiated=False)
    held = _compact(engine, user_initiated=False)
    assert len(calls) == FAILS_MOST
    assert held.no_op and "paused" in held.error

    _compact(engine, user_initiated=True)  # the user's /compact still goes
    assert len(calls) == FAILS_MOST + 1
