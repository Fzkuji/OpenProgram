"""Unit tests for auth store + types."""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from openprogram.auth import (
    AuthConfigError,
    AuthCorruptCredentialError,
    AuthEvent,
    AuthEventType,
    AuthStore,
    Credential,
    CredentialData,
    CredentialPool,
    set_store_for_testing,
)


def _oauth_cred(provider="openai-codex", profile="default", access="A", refresh="R") -> Credential:
    return Credential(
        provider_id=provider, profile_id=profile, kind="oauth",
        payload=CredentialData(
            kind="oauth", auth_value=access,
            data={"refresh_token": refresh, "expires_at_ms": 0, "client_id": "cid"},
        ),
    )


def _api_cred(provider="openai", profile="default", key="sk-xxx") -> Credential:
    return Credential(
        provider_id=provider, profile_id=profile, kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key), source="env_OPENAI_API_KEY",
    )


# ---- CRUD ------------------------------------------------------------------

def test_put_and_get_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    pool = s.get_pool("openai-codex", "default")
    assert len(pool.credentials) == 1
    assert pool.credentials[0].payload.auth_value == "A"


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
    assert [c.payload.auth_value for c in pool.credentials] == ["k1", "k2"]


def test_remove_credential(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c1 = _api_cred(key="a"); c2 = _api_cred(key="b")
    s.add_credential(c1); s.add_credential(c2)
    s.remove_credential("openai", "default", c1.credential_id)
    pool = s.get_pool("openai", "default")
    assert [c.payload.auth_value for c in pool.credentials] == ["b"]


def test_delete_pool_removes_file(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    assert path.exists()
    s.delete_pool("openai-codex", "default")
    assert not path.exists()
    assert s.find_pool("openai-codex", "default") is None


# ---- persistence ----------------------------------------------------------

def test_reload_after_restart(tmp_path: Path):
    s1 = AuthStore(root=tmp_path)
    s1.add_credential(_oauth_cred(access="secret123"))
    s2 = AuthStore(root=tmp_path)
    pool = s2.get_pool("openai-codex", "default")
    assert pool.credentials[0].payload.auth_value == "secret123"


def test_file_permissions_are_0600(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    # On Windows the check is a no-op (0o600 not enforced); skip there.
    if os.name == "posix":
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_corrupt_file_raises(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    path.write_text("{not: json}")
    s2 = AuthStore(root=tmp_path)
    with pytest.raises(AuthCorruptCredentialError):
        s2.get_pool("openai-codex", "default")


def test_schema_mismatch_raises(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    d = json.loads(path.read_text())
    d["credentials"][0]["v"] = 99
    path.write_text(json.dumps(d))
    s2 = AuthStore(root=tmp_path)
    with pytest.raises(AuthCorruptCredentialError):
        s2.get_pool("openai-codex", "default")


# ---- cross-process coherence ----------------------------------------------

def test_mtime_watch_reloads_on_external_write(tmp_path: Path):
    s1 = AuthStore(root=tmp_path)
    s1.add_credential(_oauth_cred(access="v1"))
    # Read once so s1 caches it.
    assert s1.get_pool("openai-codex", "default").credentials[0].payload.auth_value == "v1"

    # A "different process" writes a new version by hand. Bump mtime to
    # make sure the watch notices even on filesystems with 1s resolution.
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    d = json.loads(path.read_text())
    d["credentials"][0]["payload"]["auth_value"] = "v2"
    path.write_text(json.dumps(d))
    future = path.stat().st_mtime + 5
    os.utime(path, (future, future))

    pool = s1.get_pool("openai-codex", "default")
    assert pool.credentials[0].payload.auth_value == "v2"


def test_mtime_watch_handles_file_deletion(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    path = tmp_path / "auth" / "openai-codex" / "default.json"
    path.unlink()
    # After external delete, the store should behave as "not configured".
    assert s.find_pool("openai-codex", "default") is None


# ---- list_pools ------------------------------------------------------------

def test_list_pools_enumerates_multiple_providers(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    s.add_credential(_api_cred())
    s.add_credential(_api_cred(provider="anthropic", key="ant-k"))
    pools = s.list_pools()
    ids = sorted((p.provider_id, p.profile_id) for p in pools)
    assert ids == [("anthropic", "default"), ("openai", "default"), ("openai-codex", "default")]


# ---- events ----------------------------------------------------------------

def test_add_credential_emits_event(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    c = _oauth_cred()
    s.add_credential(c)
    assert any(e.type == AuthEventType.POOL_MEMBER_ADDED for e in events)
    last = events[-1]
    assert last.provider_id == "openai-codex"
    assert last.credential_id == c.credential_id


def test_remove_credential_emits_event(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c = _oauth_cred()
    s.add_credential(c)
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    s.remove_credential("openai-codex", "default", c.credential_id)
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


# ---- alias-aware pool keying (bailian ↔ alibaba-token-plan-cn) --------------
# A provider that was renamed (alias → canonical) must resolve to ONE pool no
# matter which name a caller uses on read or write — never a split-brain where
# the accounts list (canonical) comes back empty while the key lives under the
# old alias. `bailian → alibaba-token-plan-cn` is the live example.

def test_key_added_under_alias_lands_in_canonical_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    # write under the ALIAS id
    s.add_credential(_api_cred(provider="bailian", key="sk-alias"))
    # read back under the CANONICAL id — same pool, key present
    pool = s.get_pool("alibaba-token-plan-cn", "default")
    assert [c.payload.auth_value for c in pool.credentials] == ["sk-alias"]
    # and the stored pool reports the canonical provider_id
    assert pool.provider_id == "alibaba-token-plan-cn"


def test_alias_and_canonical_writes_share_one_pool(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(provider="bailian", key="k-alias"))
    s.add_credential(_api_cred(provider="alibaba-token-plan-cn", key="k-canon"))
    pool = s.get_pool("alibaba-token-plan-cn", "default")
    assert sorted(c.payload.auth_value for c in pool.credentials) == ["k-alias", "k-canon"]
    # exactly one pool exists for this provider (no split-brain)
    matches = [p for p in s.list_pools() if p.provider_id == "alibaba-token-plan-cn"]
    assert len(matches) == 1
    assert not any(p.provider_id == "bailian" for p in s.list_pools())


def test_list_pools_reports_legacy_alias_dir_as_canonical(tmp_path: Path):
    # Simulate a legacy on-disk dir written under the old alias id, with the
    # old provider_id baked into the file (pre-migration state).
    d = tmp_path / "auth" / "bailian"
    d.mkdir(parents=True)
    legacy = CredentialPool(
        provider_id="bailian", profile_id="default",
        credentials=[_api_cred(provider="bailian", key="sk-legacy")],
    )
    (d / "default.json").write_text(json.dumps(legacy.to_dict()), encoding="utf-8")
    s = AuthStore(root=tmp_path)
    pools = s.list_pools()
    # the legacy dir surfaces under the CANONICAL id so a canonical-filtered
    # accounts list finds it
    canon = [p for p in pools if p.provider_id == "alibaba-token-plan-cn"]
    assert len(canon) == 1
    assert canon[0].credentials[0].payload.auth_value == "sk-legacy"
    assert not any(p.provider_id == "bailian" for p in pools)


def test_delete_pool_alias_aware(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(provider="alibaba-token-plan-cn", key="k"))
    # delete under the ALIAS id — still hits the canonical pool
    s.delete_pool("bailian", "default")
    assert s.find_pool("alibaba-token-plan-cn", "default") is None
