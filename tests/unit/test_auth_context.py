"""Tests for auth.context — ContextVar-based ambient scope."""
from __future__ import annotations

import asyncio
import threading

import pytest

from openprogram.auth.context import (
    auth_scope,
    auth_scope_async,
    capture,
    get_active_profile_id,
    get_active_provider_hint,
    get_credential_override,
    get_subprocess_env,
)
from openprogram.auth.types import ApiKeyPayload, Credential


def _fake_cred(provider_id: str) -> Credential:
    return Credential(
        provider_id=provider_id,
        profile_id="default",
        kind="api_key",
        payload=ApiKeyPayload(api_key="k"),
    )


def test_default_profile_when_no_scope():
    assert get_active_profile_id() == "default"
    assert get_active_provider_hint() == ""
    assert get_credential_override("openai") is None
    assert get_subprocess_env() is None


def test_auth_scope_sets_and_resets():
    with auth_scope(profile_id="work", provider_hint="openai"):
        assert get_active_profile_id() == "work"
        assert get_active_provider_hint() == "openai"
    # Restored.
    assert get_active_profile_id() == "default"
    assert get_active_provider_hint() == ""


def test_auth_scope_nested_restores_outer():
    with auth_scope(profile_id="outer"):
        with auth_scope(profile_id="inner"):
            assert get_active_profile_id() == "inner"
        assert get_active_profile_id() == "outer"
    assert get_active_profile_id() == "default"


def test_auth_scope_restores_on_exception():
    with pytest.raises(RuntimeError):
        with auth_scope(profile_id="explode"):
            assert get_active_profile_id() == "explode"
            raise RuntimeError("boom")
    assert get_active_profile_id() == "default"


def test_credential_override_only_within_scope():
    cred = _fake_cred("openai")
    with auth_scope(credential_overrides={"openai": cred}):
        assert get_credential_override("openai") is cred
        assert get_credential_override("anthropic") is None
    assert get_credential_override("openai") is None


def test_subprocess_env_hook_called_each_time():
    calls = []

    def hook():
        calls.append(1)
        return {"FOO": "bar"}

    with auth_scope(subprocess_env_hook=hook):
        assert get_subprocess_env() == {"FOO": "bar"}
        assert get_subprocess_env() == {"FOO": "bar"}
    assert len(calls) == 2
    assert get_subprocess_env() is None


def test_async_scope_propagates_into_created_tasks():
    async def inner():
        return get_active_profile_id()

    async def main():
        async with auth_scope_async(profile_id="worker"):
            # asyncio.create_task copies the context at creation time.
            t = asyncio.create_task(inner())
            return await t

    assert asyncio.run(main()) == "worker"


def test_async_scope_does_not_leak_across_tasks():
    async def task_a(barrier):
        async with auth_scope_async(profile_id="A"):
            barrier.a_entered.set()
            await barrier.b_done.wait()
            return get_active_profile_id()

    async def task_b(barrier):
        await barrier.a_entered.wait()
        # Inside task B — task A's scope must NOT be visible here.
        async with auth_scope_async(profile_id="B"):
            got = get_active_profile_id()
        barrier.b_done.set()
        return got

    class Barrier:
        def __init__(self):
            self.a_entered = asyncio.Event()
            self.b_done = asyncio.Event()

    async def main():
        barrier = Barrier()
        results = await asyncio.gather(task_a(barrier), task_b(barrier))
        return results

    a, b = asyncio.run(main())
    assert a == "A"
    assert b == "B"


def test_capture_replays_context_in_thread():
    seen: list[str] = []
    done = threading.Event()

    def worker(ctx):
        ctx.run(lambda: seen.append(get_active_profile_id()))
        done.set()

    with auth_scope(profile_id="threadscope"):
        ctx = capture()

    t = threading.Thread(target=worker, args=(ctx,))
    t.start()
    done.wait(timeout=2)
    t.join()
    assert seen == ["threadscope"]
    # Main thread is back to default — the thread's context replay
    # didn't touch the main context.
    assert get_active_profile_id() == "default"
