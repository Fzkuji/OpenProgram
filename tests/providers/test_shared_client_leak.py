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
from openprogram.security.url_policy import OwnerURLException


_SCOPE = {
    "consumer": "provider.openai.sdk",
    "configured_origin": "https://api.openai.com",
}


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
        a = hc.get_shared_async_client("openai-codex", **_SCOPE)
        b = hc.get_shared_async_client("openai-codex", **_SCOPE)
        assert a is b, "same loop + key must reuse the cached client"
        assert len(hc._shared) == 1

    asyncio.run(_body())


def test_current_loop_reap_closes_and_evicts():
    captured = {}

    async def _body():
        client = hc.get_shared_async_client("openai-codex", **_SCOPE)
        captured["client"] = client
        assert len(hc._shared) == 1
        await hc.aclose_current_loop_clients()
        assert hc._shared == {}, "reaped entry must be evicted"

    asyncio.run(_body())
    assert captured["client"].closed, "reaped client must be aclose()d"


def test_reap_leaves_other_loops_untouched():
    """Only the running loop's entries are evicted; a foreign entry survives."""
    async def _body():
        hc.get_shared_async_client("openai-codex", **_SCOPE)
        # Inject an entry keyed to a different (fake) loop id.
        foreign = _FakeAsyncClient()
        foreign_key = (
            "openai-codex",
            _SCOPE["consumer"],
            _SCOPE["configured_origin"],
            -1,
        )
        hc._shared[foreign_key] = foreign
        await hc.aclose_current_loop_clients()
        assert foreign_key in hc._shared, "foreign loop entry kept"
        assert not foreign.closed, "must not close another loop's client"
        return foreign

    foreign = asyncio.run(_body())
    assert not foreign.closed


def test_run_async_does_not_accumulate_dead_entries():
    """Repeated _run_async calls (one throwaway loop each) leave no residue."""
    from openprogram.agentic_programming.runtime import _run_async

    async def _one_exec():
        # Simulate a provider grabbing its shared client mid-call.
        hc.get_shared_async_client("openai-codex", **_SCOPE)
        return "ok"

    for _ in range(5):
        assert _run_async(_one_exec()) == "ok"

    assert hc._shared == {}, (
        f"_shared leaked {len(hc._shared)} dead entries across 5 exec()s"
    )


def test_shared_cache_does_not_inherit_owner_authorization():
    async def _body():
        authorized = hc.get_shared_async_client(
            "same",
            **_SCOPE,
            owner_exception=OwnerURLException(
                consumer=_SCOPE["consumer"], origin=_SCOPE["configured_origin"]
            ),
        )
        ordinary = hc.get_shared_async_client("same", **_SCOPE)
        assert authorized is not ordinary

    asyncio.run(_body())


def test_shared_cache_separates_effective_transport_configuration():
    async def _body():
        normal = hc.get_shared_async_client("same", **_SCOPE, force_ipv4=False)
        ipv4 = hc.get_shared_async_client("same", **_SCOPE, force_ipv4=True)
        assert normal is not ipv4

    asyncio.run(_body())


def test_shared_cache_cap_ignores_closed_entries_and_rejects_open_overflow():
    async def _body():
        loop_id = id(asyncio.get_running_loop())
        for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP):
            closed = _FakeAsyncClient()
            closed.is_closed = True
            hc._shared[("closed", index, loop_id)] = closed
        client = hc.get_shared_async_client("replacement", **_SCOPE)
        assert client is not None

        hc._shared.clear()
        for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP):
            hc._shared[("open", index, loop_id)] = _FakeAsyncClient()
        with pytest.raises(RuntimeError, match="cache limit"):
            hc.get_shared_async_client("overflow", **_SCOPE)

    asyncio.run(_body())
