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
import threading

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
        self.kwargs = _kwargs
        self.closed = False
        self.is_closed = False
        try:
            self.owner_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.owner_loop = None
        self.closed_loop = None

    async def aclose(self):
        self.closed_loop = asyncio.get_running_loop()
        self.closed = True
        self.is_closed = True


@pytest.fixture(autouse=True)
def _clean_shared_and_patch(monkeypatch):
    """Empty the module cache around each test; build fake clients (no sockets)."""
    hc._shared.clear()
    monkeypatch.setattr(hc, "build_async_client", lambda **kw: _FakeAsyncClient(**kw))
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
        # Inject an entry keyed to a different live loop.
        foreign = _FakeAsyncClient()
        foreign_loop = asyncio.new_event_loop()
        foreign_key = (
            "openai-codex",
            _SCOPE["consumer"],
            _SCOPE["configured_origin"],
            foreign_loop,
        )
        hc._shared[foreign_key] = foreign
        await hc.aclose_current_loop_clients()
        assert foreign_key in hc._shared, "foreign loop entry kept"
        assert not foreign.closed, "must not close another loop's client"
        return foreign, foreign_loop

    foreign, foreign_loop = asyncio.run(_body())
    assert not foreign.closed
    foreign_loop.close()


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


def test_direct_short_lived_loops_reap_closed_loop_clients(monkeypatch):
    """Direct async callers stay bounded without Runtime's sync bridge."""
    created = []

    def _build(**kwargs):
        client = _FakeAsyncClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(hc, "build_async_client", _build)

    for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP * 3):

        async def _one_call():
            hc.get_shared_async_client(f"short-loop-{index}", **_SCOPE)

        asyncio.run(_one_call())

    assert hc._shared == {}
    assert all(client.closed for client in created)
    assert all(client.closed_loop is client.owner_loop for client in created)


def test_concurrent_loops_reserve_process_capacity_atomically(monkeypatch):
    """Concurrent loop threads never exceed the process-wide live-client cap."""
    limit = hc._MAX_SHARED_CLIENTS_TOTAL
    total = limit + 8
    capacity_gate = threading.Barrier(total)
    ready_gate = threading.Barrier(total + 1)
    release = threading.Event()
    results_lock = threading.Lock()
    accepted = []
    errors = []

    def _build(**kwargs):
        capacity_gate.wait(timeout=10)
        return _FakeAsyncClient(**kwargs)

    monkeypatch.setattr(hc, "build_async_client", _build)

    async def _one_call(index):
        try:
            client = hc.get_shared_async_client(f"concurrent-{index}", **_SCOPE)
        except BaseException as exc:
            with results_lock:
                errors.append(exc)
            capacity_gate.wait(timeout=10)
        else:
            with results_lock:
                accepted.append(client)
        ready_gate.wait(timeout=10)
        if "client" in locals():
            await asyncio.to_thread(release.wait, 10)

    def _worker(index):
        asyncio.run(_one_call(index))

    threads = [
        threading.Thread(target=_worker, args=(index,)) for index in range(total)
    ]
    for thread in threads:
        thread.start()

    ready_gate.wait(timeout=15)
    live_cache_size = len(hc._shared)
    cleanup_task_count = len(hc._loop_cleanup_tasks)
    release.set()
    for thread in threads:
        thread.join(timeout=15)

    assert live_cache_size == limit
    assert cleanup_task_count == limit
    assert len(accepted) == limit
    assert len(errors) == total - limit
    assert all(type(exc) is RuntimeError for exc in errors)
    assert {str(exc) for exc in errors} == {
        "shared provider client cache limit exceeded"
    }
    assert all(not thread.is_alive() for thread in threads)
    assert all(client.closed_loop is client.owner_loop for client in accepted)
    assert hc._shared == {}
    assert hc._loop_cleanup_tasks == {}


def test_failed_shared_client_construction_restores_capacity(monkeypatch):
    attempts = 0

    def _build(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("construction failed")
        return _FakeAsyncClient(**kwargs)

    monkeypatch.setattr(hc, "build_async_client", _build)

    async def _body():
        with pytest.raises(RuntimeError, match="construction failed"):
            hc.get_shared_async_client("failed", **_SCOPE)
        clients = [
            hc.get_shared_async_client(f"replacement-{index}", **_SCOPE)
            for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP)
        ]
        assert len(clients) == hc._MAX_SHARED_CLIENTS_PER_LOOP
        with pytest.raises(RuntimeError, match="cache limit"):
            hc.get_shared_async_client("overflow", **_SCOPE)

    asyncio.run(_body())


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


def test_shared_cache_retires_client_when_live_owner_policy_changes(
    monkeypatch,
):
    state = {
        "security": {
            "outbound_url": {
                "policy_proxy": {
                    "url": "http://127.0.0.1:3111",
                    "enforces_target_policy": True,
                }
            }
        }
    }
    monkeypatch.setattr("openprogram.setup._read_config", lambda: state)

    async def _body():
        first = hc.get_shared_async_client("same", **_SCOPE)
        state["security"]["outbound_url"]["policy_proxy"]["url"] = (
            "http://127.0.0.1:3222"
        )
        second = hc.get_shared_async_client("same", **_SCOPE)
        await asyncio.sleep(0)

        assert first is not second
        assert first.closed
        assert second.kwargs["security"].policy_proxy.url == "http://127.0.0.1:3222"

        state["security"]["outbound_url"] = {"exceptions": []}
        third = hc.get_shared_async_client("same", **_SCOPE)
        await asyncio.sleep(0)
        assert second.closed
        assert third is not second
        assert third.kwargs["security"].policy_proxy is None

    asyncio.run(_body())


def test_shared_cache_retires_client_when_live_owner_exception_is_revoked(
    monkeypatch,
):
    exception = {
        "consumer": _SCOPE["consumer"],
        "origin": _SCOPE["configured_origin"],
    }
    state = {"security": {"outbound_url": {"exceptions": [exception]}}}
    monkeypatch.setattr("openprogram.setup._read_config", lambda: state)

    async def _body():
        authorized = hc.get_shared_async_client("same", **_SCOPE)
        state["security"]["outbound_url"]["exceptions"] = []
        revoked = hc.get_shared_async_client("same", **_SCOPE)
        await asyncio.sleep(0)

        assert authorized is not revoked
        assert authorized.closed
        assert revoked.kwargs["security"].owner_exceptions == ()

    asyncio.run(_body())


def test_shared_cache_cap_ignores_closed_entries_and_rejects_open_overflow():
    async def _body():
        loop = asyncio.get_running_loop()
        for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP):
            closed = _FakeAsyncClient()
            closed.is_closed = True
            hc._shared[("closed", index, loop)] = closed
        client = hc.get_shared_async_client("replacement", **_SCOPE)
        assert client is not None

        hc._shared.clear()
        for index in range(hc._MAX_SHARED_CLIENTS_PER_LOOP):
            hc._shared[("open", index, loop)] = _FakeAsyncClient()
        with pytest.raises(RuntimeError, match="cache limit"):
            hc.get_shared_async_client("overflow", **_SCOPE)

    asyncio.run(_body())
