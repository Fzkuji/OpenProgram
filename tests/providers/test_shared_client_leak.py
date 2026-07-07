"""Regression: shared httpx clients must not leak across throwaway loops.

``Runtime.exec``'s sync bridge (M3 / GPT / openai-codex) runs each provider
call under a fresh ``asyncio.run`` loop. Providers keep-alive-cache their
client via ``get_shared_async_client``, keyed by ``(name, loop_id)``. When the
loop is torn down the cache entry becomes dead weight — unusable (httpx forbids
cross-loop reuse) yet never evicted — leaking one connection pool per call so
process memory + fd count climb with call volume.

These tests pin the fix: ``aclose_current_loop_clients`` closes + evicts only
the current loop's entries, and ``_run_async`` reaps them so ``_shared`` does
not grow without bound across repeated calls.
"""

import asyncio

import pytest

from openprogram.providers.utils import http_client as hc


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that records aclose() without any I/O."""

    def __init__(self, **_kwargs):
        self.closed = False
        self.is_closed = False

    async def aclose(self):
        self.closed = True
        self.is_closed = True


@pytest.fixture(autouse=True)
def _clean_shared_and_patch(monkeypatch):
    """Empty the module cache around each test; build fake clients (no sockets)."""
    hc._shared.clear()
    monkeypatch.setattr(hc, "build_async_client",
                        lambda **kw: _FakeAsyncClient(**kw))
    yield
    hc._shared.clear()


def test_same_loop_reuses_client():
    async def _body():
        a = hc.get_shared_async_client("openai-codex")
        b = hc.get_shared_async_client("openai-codex")
        assert a is b, "same loop + key must reuse the cached client"
        assert len(hc._shared) == 1

    asyncio.run(_body())


def test_current_loop_reap_closes_and_evicts():
    captured = {}

    async def _body():
        client = hc.get_shared_async_client("openai-codex")
        captured["client"] = client
        assert len(hc._shared) == 1
        await hc.aclose_current_loop_clients()
        assert hc._shared == {}, "reaped entry must be evicted"

    asyncio.run(_body())
    assert captured["client"].closed, "reaped client must be aclose()d"


def test_reap_leaves_other_loops_untouched():
    """Only the running loop's entries are evicted; a foreign entry survives."""
    async def _body():
        hc.get_shared_async_client("openai-codex")
        # Inject an entry keyed to a different (fake) loop id.
        foreign = _FakeAsyncClient()
        hc._shared[("openai-codex", -1)] = foreign
        await hc.aclose_current_loop_clients()
        assert ("openai-codex", -1) in hc._shared, "foreign loop entry kept"
        assert not foreign.closed, "must not close another loop's client"
        return foreign

    foreign = asyncio.run(_body())
    assert not foreign.closed


def test_run_async_does_not_accumulate_dead_entries():
    """Repeated _run_async calls (one throwaway loop each) leave no residue."""
    from openprogram.agentic_programming.runtime import _run_async

    async def _one_exec():
        # Simulate a provider grabbing its shared client mid-call.
        hc.get_shared_async_client("openai-codex")
        return "ok"

    for _ in range(5):
        assert _run_async(_one_exec()) == "ok"

    assert hc._shared == {}, (
        f"_shared leaked {len(hc._shared)} dead entries across 5 exec()s"
    )
