"""Unit tests for auth store + types."""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from openprogram.auth import (
    ApiKeyPayload,
    AuthConfigError,
    AuthCorruptCredentialError,
    AuthEvent,
    AuthEventType,
    AuthStore,
    Credential,
    CredentialPool,
    OAuthPayload,
    set_store_for_testing,
)


def _oauth_cred(provider="chatgpt-subscription", profile="default", access="A", refresh="R") -> Credential:
    return Credential(
        provider_id=provider, profile_id=profile, kind="oauth",
        payload=OAuthPayload(
            access_token=access, refresh_token=refresh,
            expires_at_ms=0, client_id="cid",
        ),
    )


def _api_cred(provider="openai", profile="default", key="sk-xxx") -> Credential:
    return Credential(
        provider_id=provider, profile_id=profile, kind="api_key",
        payload=ApiKeyPayload(api_key=key), source="env_OPENAI_API_KEY",
    )


# ---- CRUD ------------------------------------------------------------------

def test_put_and_get_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    pool = s.get_pool("chatgpt-subscription", "default")
    assert len(pool.credentials) == 1
    assert pool.credentials[0].payload.access_token == "A"


def test_get_missing_raises_config_error(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    with pytest.raises(AuthConfigError):
        s.get_pool("unknown", "default")


def test_find_pool_missing_returns_none(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    assert s.find_pool("unknown", "default") is None


def test_add_multiple_credentials_same_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(key="k1"))
    s.add_credential(_api_cred(key="k2"))
    pool = s.get_pool("openai", "default")
    assert [c.payload.api_key for c in pool.credentials] == ["k1", "k2"]


def test_remove_credential(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c1 = _api_cred(key="a"); c2 = _api_cred(key="b")
    s.add_credential(c1); s.add_credential(c2)
    s.remove_credential("openai", "default", c1.credential_id)
    pool = s.get_pool("openai", "default")
    assert [c.payload.api_key for c in pool.credentials] == ["b"]


def test_delete_pool_removes_file(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    assert path.exists()
    s.delete_pool("chatgpt-subscription", "default")
    assert not path.exists()
    assert s.find_pool("chatgpt-subscription", "default") is None


# ---- persistence ----------------------------------------------------------

def test_reload_after_restart(tmp_path: Path):
    s1 = AuthStore(root=tmp_path)
    s1.add_credential(_oauth_cred(access="secret123"))
    s2 = AuthStore(root=tmp_path)
    pool = s2.get_pool("chatgpt-subscription", "default")
    assert pool.credentials[0].payload.access_token == "secret123"


def test_file_permissions_are_0600(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    # On Windows the check is a no-op (0o600 not enforced); skip there.
    if os.name == "posix":
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_corrupt_file_raises(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    path.write_text("{not: json}")
    s2 = AuthStore(root=tmp_path)
    with pytest.raises(AuthCorruptCredentialError):
        s2.get_pool("chatgpt-subscription", "default")


def test_schema_mismatch_raises(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    d = json.loads(path.read_text())
    d["credentials"][0]["v"] = 99
    path.write_text(json.dumps(d))
    s2 = AuthStore(root=tmp_path)
    with pytest.raises(AuthCorruptCredentialError):
        s2.get_pool("chatgpt-subscription", "default")


# ---- cross-process coherence ----------------------------------------------

def test_mtime_watch_reloads_on_external_write(tmp_path: Path):
    s1 = AuthStore(root=tmp_path)
    s1.add_credential(_oauth_cred(access="v1"))
    # Read once so s1 caches it.
    assert s1.get_pool("chatgpt-subscription", "default").credentials[0].payload.access_token == "v1"

    # A "different process" writes a new version by hand. Bump mtime to
    # make sure the watch notices even on filesystems with 1s resolution.
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    d = json.loads(path.read_text())
    d["credentials"][0]["payload"]["access_token"] = "v2"
    path.write_text(json.dumps(d))
    future = path.stat().st_mtime + 5
    os.utime(path, (future, future))

    pool = s1.get_pool("chatgpt-subscription", "default")
    assert pool.credentials[0].payload.access_token == "v2"


def test_mtime_watch_handles_file_deletion(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "chatgpt-subscription" / "default.json"
    path.unlink()
    # After external delete, the store should behave as "not configured".
    assert s.find_pool("chatgpt-subscription", "default") is None


# ---- list_pools ------------------------------------------------------------

def test_list_pools_enumerates_multiple_providers(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    s.add_credential(_api_cred())
    s.add_credential(_api_cred(provider="anthropic", key="ant-k"))
    pools = s.list_pools()
    ids = sorted((p.provider_id, p.profile_id) for p in pools)
    assert ids == [("anthropic", "default"), ("openai", "default"), ("chatgpt-subscription", "default")]


# ---- events ----------------------------------------------------------------

def test_add_credential_emits_event(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    c = _oauth_cred()
    s.add_credential(c)
    assert any(e.type == AuthEventType.POOL_MEMBER_ADDED for e in events)
    last = events[-1]
    assert last.provider_id == "chatgpt-subscription"
    assert last.credential_id == c.credential_id


def test_remove_credential_emits_event(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c = _oauth_cred()
    s.add_credential(c)
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    s.remove_credential("chatgpt-subscription", "default", c.credential_id)
    assert any(e.type == AuthEventType.POOL_MEMBER_REMOVED for e in events)


def test_listener_exception_does_not_break_broadcast(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    hits = []
    s.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("bad")))
    s.subscribe(hits.append)
    s.add_credential(_oauth_cred())
    assert len(hits) == 1


# ---- locks -----------------------------------------------------------------

def test_async_lock_is_per_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    a = s.async_lock("x", "default")
    b = s.async_lock("x", "default")
    c = s.async_lock("x", "other")
    assert a is b
    assert a is not c


def test_async_lock_serializes(tmp_path: Path):
    s = AuthStore(root=tmp_path)

    async def run():
        lock = s.async_lock("p", "d")
        timeline = []

        async def worker(name: str, hold: float):
            async with lock:
                timeline.append(f"enter-{name}")
                await asyncio.sleep(hold)
                timeline.append(f"exit-{name}")

        await asyncio.gather(worker("a", 0.05), worker("b", 0.0))
        return timeline

    timeline = asyncio.run(run())
    # Whoever entered first completes their exit before the other enters.
    assert timeline.index("exit-a") < timeline.index("enter-b") or \
           timeline.index("exit-b") < timeline.index("enter-a")


# ---- singleton -------------------------------------------------------------

def test_set_store_for_testing_override(tmp_path: Path):
    custom = AuthStore(root=tmp_path)
    set_store_for_testing(custom)
    from openprogram.auth import get_store
    assert get_store() is custom
    set_store_for_testing(None)
