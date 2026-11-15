"""Shared recovery allowance semantics."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.models.providers.utils.errors import ExecInterrupt
from openprogram.models.providers.utils.recovery import (
    RecoveryState,
    current_recovery,
    recovery_limit_from_max_retries,
    reserve_recovery,
)
from openprogram.models.providers.utils.stream_retry import (
    ProviderStreamError,
    _sleep_unless_aborted,
    retry_stream,
)


def test_default_two_recoveries_and_pause():
    assert recovery_limit_from_max_retries(None) == 2
    assert recovery_limit_from_max_retries(0) == 0
    assert recovery_limit_from_max_retries(1) == 0
    assert recovery_limit_from_max_retries(3) == 2
    state = RecoveryState(limit=2)
    token = current_recovery.set(state)
    try:
        assert reserve_recovery("length")
        assert reserve_recovery("invalid_json")
        assert not reserve_recovery("length")
        assert state.phase == "paused"
        assert state.used == 2
    finally:
        current_recovery.reset(token)


def test_transport_retries_have_their_own_allowance():
    """A dropped connection says nothing about the output, so it must not
    spend the repair allowance, and spent repairs must not block it."""
    state = RecoveryState(limit=2, transport_limit=4)
    token = current_recovery.set(state)
    try:
        assert reserve_recovery("length")
        assert reserve_recovery("invalid_json")
        for _ in range(4):
            assert reserve_recovery("transport")
        assert not reserve_recovery("transport")
        assert (state.used, state.transport_used) == (2, 4)
        state.refund("transport", 4)
        assert (state.used, state.transport_used) == (2, 0)
    finally:
        current_recovery.reset(token)


def test_explicit_zero_disables_transport_retries_too():
    state = RecoveryState(limit=0)
    token = current_recovery.set(state)
    try:
        assert not reserve_recovery("transport")
        assert state.transport_used == 0
    finally:
        current_recovery.reset(token)


def test_a_request_that_recovers_refunds_its_transport_retries():
    """One state spans a whole Agent loop; blips that retries rode over
    must not leave later requests in the loop without recovery."""
    state = RecoveryState(limit=2, transport_limit=2)
    token = current_recovery.set(state)

    def flaky(failures):
        calls = []

        async def attempt():
            calls.append(1)
            if len(calls) <= failures:
                raise ProviderStreamError("ConnectError: ", retryable=True)

        return attempt

    async def no_sleep(*_args, **_kwargs):
        return None

    async def run():
        import openprogram.models.providers.utils.stream_retry as module
        original = module._sleep_unless_aborted, module.NETWORK_WAIT_MAX_S
        module._sleep_unless_aborted = no_sleep
        module.NETWORK_WAIT_MAX_S = 0
        try:
            for _ in range(3):  # three requests, each recovering from two blips
                await retry_stream(flaky(2), is_committed_fn=lambda: False,
                                   max_attempts=5, label="test")
            assert state.transport_used == 0 and state.phase == "active"
            # A request that never recovers still exhausts the allowance.
            with pytest.raises(ProviderStreamError) as raised:
                await retry_stream(flaky(9), is_committed_fn=lambda: False,
                                   max_attempts=10, label="test")
            assert raised.value.transport_exhausted
            assert state.transport_used == 2 and state.phase == "paused"
            assert state.used == 0
        finally:
            module._sleep_unless_aborted, module.NETWORK_WAIT_MAX_S = original

    try:
        asyncio.run(run())
    finally:
        current_recovery.reset(token)


def test_cancel_blocks_further_reserves():
    state = RecoveryState(limit=2)
    token = current_recovery.set(state)
    try:
        state.mark_cancelled()
        assert not reserve_recovery("transport")
        assert state.used == 0
    finally:
        current_recovery.reset(token)


def test_legacy_without_state_allows_provider_local_retry():
    assert current_recovery.get() is None
    assert reserve_recovery("transport") is True


def test_retry_aborts_before_second_attempt_when_cancelled():
    attempts = []

    async def attempt():
        attempts.append(1)
        raise ProviderStreamError("boom", retryable=True)

    async def run():
        with pytest.raises(ExecInterrupt):
            await retry_stream(
                attempt,
                is_committed_fn=lambda: False,
                max_attempts=5,
                label="test",
                # Cancel as soon as the first failure is observed — before
                # backoff sleep or a second network attempt.
                abort_check=lambda: len(attempts) >= 1,
            )

    asyncio.run(run())
    assert attempts == [1]


def test_sleep_unless_aborted_raises_before_waiting():
    async def run():
        with pytest.raises(ExecInterrupt):
            await _sleep_unless_aborted(10.0, lambda: True)

    asyncio.run(run())


def test_sleep_unless_aborted_respects_recovery_cancel():
    state = RecoveryState(limit=2)
    token = current_recovery.set(state)

    async def run():
        state.mark_cancelled()
        with pytest.raises(ExecInterrupt):
            await _sleep_unless_aborted(10.0, None)

    try:
        asyncio.run(run())
    finally:
        current_recovery.reset(token)


class _Clock:
    """Fake monotonic clock that the patched sleep advances."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


def _network_test_env(monkeypatch, *, wait_max_s=1800.0):
    import openprogram.models.providers.utils.stream_retry as module

    clock = _Clock()
    sleeps = []

    async def fake_sleep(sleep_s, abort_check):
        if abort_check is not None and abort_check():
            raise ExecInterrupt("cancelled")
        sleeps.append(sleep_s)
        clock.now += sleep_s

    monkeypatch.setattr(module, "_sleep_unless_aborted", fake_sleep)
    monkeypatch.setattr(module, "NETWORK_WAIT_MAX_S", wait_max_s)
    monkeypatch.setattr(module, "_now", clock.monotonic)
    return module, clock, sleeps


class ConnectError(Exception):
    """Stand-in for httpx.ConnectError (matched by class name)."""


def _offline_then(online_after, *, then=None):
    calls = []

    async def attempt():
        calls.append(1)
        if len(calls) <= online_after:
            raise ConnectError("[Errno 8] nodename nor servname provided")
        if then is not None and len(calls) == online_after + 1:
            raise then

    return attempt, calls


def test_unreachable_endpoint_waits_for_the_network_then_continues(monkeypatch):
    module, clock, sleeps = _network_test_env(monkeypatch)
    state = RecoveryState(limit=2)
    token = current_recovery.set(state)
    notices = []
    attempt, calls = _offline_then(12)
    try:
        asyncio.run(retry_stream(
            attempt, is_committed_fn=lambda: False, max_attempts=5,
            label="test", on_retry=notices.append,
        ))
    finally:
        current_recovery.reset(token)
    # 5 normal attempts (4 retries), then 7 network probes fail, the 8th succeeds.
    assert len(calls) == 13
    assert [n["reason"] for n in notices[:4]] == ["transport"] * 4
    waits = [n for n in notices if n["reason"] == "network_wait"]
    assert [n["attempt"] for n in waits] == list(range(1, 9))
    assert max(n["delay_ms"] for n in waits) == 30_000
    # The outage's retries are refunded once the network answers.
    assert state.transport_used == 0 and state.used == 0


def test_network_wait_gives_up_at_its_limit(monkeypatch):
    module, clock, sleeps = _network_test_env(monkeypatch, wait_max_s=60)
    attempt, calls = _offline_then(10_000)
    with pytest.raises(ProviderStreamError) as raised:
        asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False,
                                 max_attempts=2, label="test"))
    assert raised.value.transport_exhausted
    network_sleeps = sleeps[1:]
    assert network_sleeps and sum(network_sleeps) <= 60


def test_network_wait_stops_on_cancel(monkeypatch):
    _network_test_env(monkeypatch)
    attempt, calls = _offline_then(10_000)
    with pytest.raises(ExecInterrupt):
        asyncio.run(retry_stream(
            attempt, is_committed_fn=lambda: False, max_attempts=2, label="test",
            abort_check=lambda: len(calls) >= 4,
        ))
    assert len(calls) == 4


def test_a_server_error_never_waits_for_the_network(monkeypatch):
    _, _, sleeps = _network_test_env(monkeypatch)
    calls = []

    async def attempt():
        calls.append(1)
        raise ProviderStreamError("HTTP 502", retryable=True, http_status=502)

    with pytest.raises(ProviderStreamError):
        asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False,
                                 max_attempts=3, label="test"))
    assert len(calls) == 3


def test_committed_output_never_waits_for_the_network(monkeypatch):
    _network_test_env(monkeypatch)
    attempt, calls = _offline_then(10_000)
    with pytest.raises(ProviderStreamError):
        asyncio.run(retry_stream(attempt, is_committed_fn=lambda: True,
                                 max_attempts=3, label="test"))
    assert len(calls) == 1


def test_network_back_with_a_server_error_gets_fresh_retries(monkeypatch):
    _network_test_env(monkeypatch)
    state = RecoveryState(limit=2, transport_limit=2)
    token = current_recovery.set(state)
    attempt, calls = _offline_then(
        5, then=ProviderStreamError("HTTP 503", retryable=True, http_status=503),
    )
    try:
        asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False,
                                 max_attempts=3, label="test"))
    finally:
        current_recovery.reset(token)
    # 3 attempts offline, 2 probes offline, probe 3 returns 503, then a fresh
    # attempt succeeds.
    assert len(calls) == 7
    assert state.transport_used == 0


@pytest.mark.parametrize("limit,max_attempts", [(0, 5), (2, 1)])
def test_disabled_recovery_never_waits_for_the_network(monkeypatch, limit, max_attempts):
    _network_test_env(monkeypatch)
    state = RecoveryState(limit=limit)
    token = current_recovery.set(state)
    attempt, calls = _offline_then(10_000)
    try:
        with pytest.raises(ProviderStreamError):
            asyncio.run(retry_stream(attempt, is_committed_fn=lambda: False,
                                     max_attempts=max_attempts, label="test"))
    finally:
        current_recovery.reset(token)
    assert len(calls) == 1
