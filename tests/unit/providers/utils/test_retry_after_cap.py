"""A server-asked wait longer than the cap is not sat through.

A Retry-After of hours (a plan's quota resting) ends the retry at once and
goes up still carrying the hint; a short one is honored as before.
"""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.utils import stream_retry
from openprogram.models.providers.utils.stream_retry import ProviderStreamError, retry_stream


def _limited(retry_after_s: float):
    calls = []

    async def attempt():
        calls.append(1)
        if len(calls) == 1:
            raise ProviderStreamError("429", http_status=429, retry_after_s=retry_after_s, retryable=True)

    return attempt, calls


def test_an_hours_long_wait_goes_up_at_once():
    attempt, calls = _limited(3 * 3600)
    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False, max_attempts=3))
    assert calls == [1]
    assert caught.value.retry_after_s == 3 * 3600
    assert getattr(caught.value, "transport_exhausted", False)


def test_a_short_wait_is_honored(monkeypatch):
    slept = []

    async def no_sleep(seconds, _abort):
        slept.append(seconds)

    monkeypatch.setattr(stream_retry, "_sleep_unless_aborted", no_sleep)
    attempt, calls = _limited(2)
    asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False, max_attempts=3))
    assert calls == [1, 1] and slept and slept[0] >= 2


@pytest.mark.parametrize("hint, beyond", [(None, False), (0, False), (60, False), (301, True)])
def test_the_cap(hint, beyond):
    assert stream_retry.retry_after_beyond_cap(hint) is beyond
