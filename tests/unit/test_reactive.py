"""Tests for Tier 5 Reactive compaction (context/reactive.py)."""
from __future__ import annotations

import pytest

from openprogram.context.reactive import is_overflow_error


class _FakeExc(Exception):
    pass


@pytest.mark.parametrize("msg", [
    "prompt is too long: 250000 tokens > 200000 max",
    "input is too long for requested model",
    "exceeds the context window of 200000 tokens",
    "maximum context length is 128000 tokens",
    "context_length_exceeded",
    "too many tokens",
    "400 (no body)",
    "413 (no body)",
])
def test_is_overflow_error_matches(msg):
    assert is_overflow_error(_FakeExc(msg))


@pytest.mark.parametrize("msg", [
    "rate limit exceeded",
    "overloaded",
    "500 internal server error",
    "connection refused",
    "",
])
def test_is_overflow_error_no_match(msg):
    assert not is_overflow_error(_FakeExc(msg))
