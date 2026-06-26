"""Tests for cache-aware Microcompact (Context Editing API integration)."""
from openprogram.context.cache_aware_microcompact import (
    increment_tool_calls,
    should_trigger,
    build_cache_edits,
    reset,
    INITIAL_TRIGGER,
    SUBSEQUENT_INTERVAL,
    KEEP_RECENT,
)


def setup_function():
    reset()


def test_no_trigger_before_threshold():
    for _ in range(INITIAL_TRIGGER - 1):
        increment_tool_calls()
    assert not should_trigger()
    assert build_cache_edits() is None


def test_trigger_at_threshold():
    for _ in range(INITIAL_TRIGGER):
        increment_tool_calls()
    assert should_trigger()


def test_build_cache_edits_returns_params():
    for _ in range(INITIAL_TRIGGER):
        increment_tool_calls()
    result = build_cache_edits()
    assert result is not None
    assert "cache_edits" in result
    assert result["cache_edits"]["strategy"] == "clear_tool_uses"
    assert result["cache_edits"]["keep_recent"] == KEEP_RECENT


def test_trigger_marks_last_and_waits_interval():
    for _ in range(INITIAL_TRIGGER):
        increment_tool_calls()
    build_cache_edits()  # fires, sets last_trigger_at
    assert not should_trigger()  # just fired

    for _ in range(SUBSEQUENT_INTERVAL - 1):
        increment_tool_calls()
    assert not should_trigger()

    increment_tool_calls()
    assert should_trigger()


def test_subsequent_trigger_fires():
    for _ in range(INITIAL_TRIGGER):
        increment_tool_calls()
    build_cache_edits()

    for _ in range(SUBSEQUENT_INTERVAL):
        increment_tool_calls()
    result = build_cache_edits()
    assert result is not None


def test_increment_batch():
    count = increment_tool_calls(INITIAL_TRIGGER)
    assert count == INITIAL_TRIGGER
    assert should_trigger()


def test_reset():
    for _ in range(INITIAL_TRIGGER):
        increment_tool_calls()
    assert should_trigger()
    reset()
    assert not should_trigger()
