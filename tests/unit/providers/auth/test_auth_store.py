"""The credential store: one ordered list per provider in
``<state>/auth/<provider>/credentials.json`` (openprogram/models/auth/store.py)."""
from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from openprogram.models.auth import (
    AuthCorruptCredentialError,
    AuthEvent,
    AuthEventType,
    AuthStore,
    Credential,
    CredentialData,
    set_store_for_testing,
)


def _oauth_cred(provider="openai-codex", access="A", refresh="R", email="") -> Credential:
    return Credential(
        provider_id=provider, kind="oauth",
        identity={"email": email} if email else {},
        payload=CredentialData(
            kind="oauth", auth_value=access,
            data={"refresh_token": refresh, "expires_at_ms": 0, "client_id": "cid"},
        ),
    )


def _api_cred(provider="openai", key="sk-xxx") -> Credential:
    return Credential(
        provider_id=provider, kind="api_key",
        payload=CredentialData(kind="api_key", auth_value=key), source="cli",
    )


def _file(root: Path, provider="openai-codex") -> Path:
    return root / "auth" / provider / "credentials.json"


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


# ---- the list ----------------------------------------------------------------

def test_add_and_read(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    pool = s.get("openai-codex")
    assert [c.payload.auth_value for c in pool.credentials] == ["A"]
    assert pool.strategy == "in_order"


def test_an_unknown_provider_has_an_empty_list(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    assert s.get("unknown").credentials == []
    assert not _file(tmp_path, "unknown").exists()


def test_keys_keep_the_order_they_were_added(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(key="k1"))
    s.add_credential(_api_cred(key="k2"))
    assert [c.payload.auth_value for c in s.get("openai").credentials] == ["k1", "k2"]


def test_the_same_key_again_is_one_row(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    first = s.add_credential(_api_cred(key="k1"))
    again = s.add_credential(_api_cred(key="k1"))
    assert again.id == first.id and len(s.get("openai").credentials) == 1


def test_signing_in_again_renews_the_row_in_place(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    first = s.add_credential(_oauth_cred(access="old", email="me@example.com"))
    s.add_credential(_oauth_cred(access="other", email="you@example.com"))
    s.update("openai-codex", first.id, lambda c: setattr(c, "label", "Mine"))
    s.add_credential(_oauth_cred(access="new", email="me@example.com"))
    rows = s.get("openai-codex").credentials
    assert [(c.id == first.id, c.label, c.payload.auth_value) for c in rows] == \
        [(True, "Mine", "new"), (False, "", "other")]


def test_remove_one(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c1 = s.add_credential(_api_cred(key="a"))
    s.add_credential(_api_cred(key="b"))
    assert s.remove_credential("openai", c1.id) is True
    assert [c.payload.auth_value for c in s.get("openai").credentials] == ["b"]
    assert s.remove_credential("openai", c1.id) is False


def test_delete_provider_removes_the_file(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    assert _file(tmp_path).exists()
    s.delete_provider("openai-codex")
    assert not _file(tmp_path).exists()
    assert s.get("openai-codex").credentials == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_delete_provider_unlinks_a_symlink_without_deleting_its_target(tmp_path: Path):
    store = AuthStore(root=tmp_path)
    store.add_credential(_oauth_cred())
    path = _file(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("outside")
    path.unlink()
    path.symlink_to(outside)

    store.delete_provider("openai-codex")

    assert outside.read_text() == "outside"
    assert not path.exists()


def test_delete_provider_does_not_report_success_when_unlink_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from openprogram.models.auth.credentials import PrivateAtomicWriteError
    from openprogram.models.auth.credentials import io

    store = AuthStore(root=tmp_path)
    store.add_credential(_oauth_cred())
    monkeypatch.setattr(io.os, "unlink", lambda _path: (_ for _ in ()).throw(OSError("unlink denied")))

    with pytest.raises(PrivateAtomicWriteError) as caught:
        store.delete_provider("openai-codex")

    assert caught.value.code == "delete"
    assert caught.value.committed is False
    assert _file(tmp_path).exists()


def test_a_set_variable_is_a_read_only_row_after_the_stored_ones(tmp_path: Path, monkeypatch):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(key="sk-stored"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    rows = s.get("openai").credentials
    assert [(c.id, c.source, c.read_only) for c in rows][1] == ("env:OPENAI_API_KEY", "env", True)
    assert "sk-from-env" not in _file(tmp_path, "openai").read_text()
    assert [c.id for c in s.stored("openai").credentials] == [rows[0].id]


def test_an_environment_row_keeps_its_place_once_moved(tmp_path: Path, monkeypatch):
    s = AuthStore(root=tmp_path)
    stored = s.add_credential(_api_cred(key="sk-stored"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    s.mutate("openai", lambda pool: pool.credentials.reverse())
    assert [c.id for c in s.get("openai").credentials] == ["env:OPENAI_API_KEY", stored.id]
    monkeypatch.delenv("OPENAI_API_KEY")
    assert [c.id for c in s.get("openai").credentials] == [stored.id]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert [c.id for c in s.get("openai").credentials] == ["env:OPENAI_API_KEY", stored.id]


# ---- on disk ------------------------------------------------------------------

def test_reload_after_restart(tmp_path: Path):
    AuthStore(root=tmp_path).add_credential(_oauth_cred(access="secret123"))
    pool = AuthStore(root=tmp_path).get("openai-codex")
    assert pool.credentials[0].payload.auth_value == "secret123"


def test_the_file_holds_strategy_list_and_cursor(tmp_path: Path):
    AuthStore(root=tmp_path).add_credential(_oauth_cred())
    doc = json.loads(_file(tmp_path).read_text())
    assert {"v", "provider_id", "strategy", "cursor", "credentials"} <= set(doc)
    assert doc["provider_id"] == "openai-codex"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_wide_permission_file_remains_readable(tmp_path: Path):
    AuthStore(root=tmp_path).add_credential(_oauth_cred(access="SECRET-WIDE"))
    path = _file(tmp_path)
    os.chmod(path, 0o644)

    pool = AuthStore(root=tmp_path).get("openai-codex")
    assert pool.credentials[0].payload.auth_value == "SECRET-WIDE"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_corrupt_file_without_a_good_copy_raises(tmp_path: Path):
    path = _file(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not: json}")
    with pytest.raises(AuthCorruptCredentialError):
        AuthStore(root=tmp_path).get("openai-codex")


def test_schema_mismatch_raises(tmp_path: Path):
    AuthStore(root=tmp_path).add_credential(_oauth_cred())
    path = _file(tmp_path)
    d = json.loads(path.read_text())
    d["v"] = 99
    path.write_text(json.dumps(d))
    with pytest.raises(AuthCorruptCredentialError):
        AuthStore(root=tmp_path).get("openai-codex")


# ---- another process --------------------------------------------------------

def test_a_write_by_another_process_is_read(tmp_path: Path):
    s1 = AuthStore(root=tmp_path)
    s1.add_credential(_oauth_cred(access="v1"))
    assert s1.get("openai-codex").credentials[0].payload.auth_value == "v1"

    path = _file(tmp_path)
    d = json.loads(path.read_text())
    d["credentials"][0]["payload"]["auth_value"] = "v2"
    path.write_text(json.dumps(d))
    future = path.stat().st_mtime + 5
    os.utime(path, (future, future))

    assert s1.get("openai-codex").credentials[0].payload.auth_value == "v2"


def test_a_file_removed_by_another_process_is_an_empty_list(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    _file(tmp_path).unlink()
    assert s.get("openai-codex").credentials == []


# ---- every provider -----------------------------------------------------------

def test_list_pools_enumerates_providers(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_oauth_cred())
    s.add_credential(_api_cred())
    s.add_credential(_api_cred(provider="anthropic", key="ant-k"))
    assert sorted(p.provider_id for p in s.list_pools()) == ["anthropic", "openai", "openai-codex"]


# ---- events -------------------------------------------------------------------

def test_add_credential_emits_events(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    c = s.add_credential(_oauth_cred())
    added = [e for e in events if e.type == AuthEventType.POOL_MEMBER_ADDED]
    assert added and added[-1].credential_id == c.id and added[-1].provider_id == "openai-codex"
    assert events[-1].type == AuthEventType.CREDENTIALS_CHANGED


def test_remove_credential_emits_event(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    c = s.add_credential(_oauth_cred())
    events: list[AuthEvent] = []
    s.subscribe(events.append)
    s.remove_credential("openai-codex", c.id)
    assert any(e.type == AuthEventType.POOL_MEMBER_REMOVED for e in events)


def test_listener_exception_does_not_break_broadcast(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    hits = []
    s.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("bad")))
    s.subscribe(hits.append)
    s.add_credential(_oauth_cred())
    assert any(e.type == AuthEventType.POOL_MEMBER_ADDED for e in hits)


# ---- locks --------------------------------------------------------------------

def test_async_lock_is_per_provider(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    assert s.async_lock("x") is s.async_lock("x")
    assert s.async_lock("x") is not s.async_lock("y")


def test_async_lock_serializes(tmp_path: Path):
    s = AuthStore(root=tmp_path)

    async def run():
        lock = s.async_lock("p")
        timeline = []
        first_entered = asyncio.Event()
        release_first = asyncio.Event()

        async def first():
            async with lock:
                timeline.append("enter-a")
                first_entered.set()
                await release_first.wait()
                timeline.append("exit-a")

        async def second():
            async with lock:
                timeline.append("enter-b")
                timeline.append("exit-b")

        first_task = asyncio.create_task(first())
        await first_entered.wait()
        second_task = asyncio.create_task(second())
        await asyncio.sleep(0)
        assert timeline == ["enter-a"]
        release_first.set()
        await asyncio.gather(first_task, second_task)
        return timeline

    assert asyncio.run(run()) == ["enter-a", "exit-a", "enter-b", "exit-b"]


def test_set_store_for_testing_override(tmp_path: Path):
    custom = AuthStore(root=tmp_path)
    set_store_for_testing(custom)
    from openprogram.models.auth import get_store
    assert get_store() is custom
    set_store_for_testing(None)


# ---- a renamed provider (bailian -> alibaba-token-plan-cn) ----------------------

def test_a_key_added_under_an_old_name_lands_in_the_current_list(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    s.add_credential(_api_cred(provider="bailian", key="k-alias"))
    s.add_credential(_api_cred(provider="alibaba-token-plan-cn", key="k-canon"))
    pool = s.get("alibaba-token-plan-cn")
    assert pool.provider_id == "alibaba-token-plan-cn"
    assert [c.payload.auth_value for c in pool.credentials] == ["k-alias", "k-canon"]
    assert not (tmp_path / "auth" / "bailian").exists()
    s.delete_provider("bailian")
    assert s.get("alibaba-token-plan-cn").credentials == []


def test_an_old_file_under_an_old_name_carries_over_to_the_current_one(tmp_path: Path):
    legacy = tmp_path / "auth" / "bailian" / "default.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"provider_id": "bailian", "account_id": "default", "credentials": [
        {"credential_id": "c1", "kind": "api_key", "payload": {"kind": "api_key", "auth_value": "sk-legacy"}}]}))
    s = AuthStore(root=tmp_path)
    assert [p.provider_id for p in s.list_pools()] == ["alibaba-token-plan-cn"]
    assert s.get("alibaba-token-plan-cn").credentials[0].payload.auth_value == "sk-legacy"


# ---- several writers -----------------------------------------------------------

def test_a_change_applies_to_the_file_as_it_is_now(tmp_path: Path):
    worker, cli = AuthStore(root=tmp_path), AuthStore(root=tmp_path)
    first = worker.add_credential(_api_cred(key="sk-first-000000001"))
    worker.get("openai")
    cli.add_credential(_api_cred(key="sk-second-00000002"))
    worker.update("openai", first.id, lambda row: setattr(row, "enabled", False))
    worker.mutate("openai", lambda pool: setattr(pool, "strategy", "evenly"))
    pool = AuthStore(root=tmp_path).get("openai")
    assert len(pool.credentials) == 2 and pool.strategy == "evenly"


def test_a_refreshed_token_survives_a_use_recorded_at_the_same_time(tmp_path: Path):
    a, b = AuthStore(root=tmp_path), AuthStore(root=tmp_path)
    cred = a.add_credential(_oauth_cred(access="ACCESS-OLD", refresh="RT-OLD"))
    b.get("openai-codex")

    def refresh(row: Credential) -> None:
        b.record_use("openai-codex", cred.id, 1)
        row.payload = CredentialData(kind="oauth", auth_value="ACCESS-NEW",
                                     data={"refresh_token": "RT-NEW"})

    a.update("openai-codex", cred.id, refresh)
    row = AuthStore(root=tmp_path).find("openai-codex", cred.id)
    assert (row.payload.auth_value, row.payload.data["refresh_token"]) == ("ACCESS-NEW", "RT-NEW")
    assert row.use_count == 1


# ---- what stays on disk ----------------------------------------------------------

def _secret_on_disk(root: Path, secret: str) -> list[str]:
    return [str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and secret.encode() in p.read_bytes()]


def test_a_removed_key_leaves_no_copy_on_disk(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    gone = s.add_credential(_api_cred(key="sk-FFFF-removed-0"))
    s.add_credential(_api_cred(key="sk-KEEP-kept-00001"))
    s.update("openai", gone.id, lambda row: setattr(row, "label", "x"))
    path = _file(tmp_path, "openai")
    path.with_name(path.name + ".bad-20260101T000000Z-abc").write_text("sk-FFFF-removed-0")
    assert _secret_on_disk(tmp_path, "sk-FFFF-removed-0")
    assert s.remove_credential("openai", gone.id)
    assert _secret_on_disk(tmp_path, "sk-FFFF-removed-0") == []


def test_clearing_a_provider_removes_every_copy_and_its_folder(tmp_path: Path):
    s = AuthStore(root=tmp_path)
    cred = s.add_credential(_api_cred(key="sk-KEEP-kept-00001"))
    s.update("openai", cred.id, lambda row: setattr(row, "label", "x"))
    s.record_use("openai", cred.id, 1)
    s.delete_provider("openai")
    assert _secret_on_disk(tmp_path, "sk-KEEP-kept-00001") == []
    assert not (tmp_path / "auth" / "openai").exists()


def test_a_replaced_key_leaves_no_copy_on_disk(tmp_path: Path, monkeypatch):
    from openprogram.models.auth import manage
    s = AuthStore(root=tmp_path)
    set_store_for_testing(s)
    try:
        cred = s.add_credential(_api_cred(key="sk-OLD-0000000001"))
        s.update("openai", cred.id, lambda row: setattr(row, "label", "x"))
        monkeypatch.setattr(manage, "_probe_or_none", lambda *a, **k: None)
        monkeypatch.setattr("openprogram.models.usage.quota_fetchers.after_key_added", lambda p: None)
        manage.replace("openai", cred.id, "sk-NEW-0000000002")
        assert _secret_on_disk(tmp_path, "sk-OLD-0000000001") == []
        assert s.find("openai", cred.id).payload.auth_value == "sk-NEW-0000000002"
    finally:
        set_store_for_testing(None)


def test_a_row_saved_without_an_id_gets_a_steady_one(tmp_path: Path):
    path = _file(tmp_path, "openai")
    path.parent.mkdir(parents=True)
    row = _api_cred(key="sk-aaaa-00000001").to_dict()
    other = _api_cred(key="sk-bbbb-00000002").to_dict()
    row["id"] = other["id"] = None
    path.write_text(json.dumps({"v": 4, "provider_id": "openai", "strategy": "in_order",
                                "credentials": [row, other]}))
    s = AuthStore(root=tmp_path)
    ids = [c.id for c in s.get("openai").credentials]
    assert len(set(ids)) == 2 and all(ids)
    assert ids == [c.id for c in AuthStore(root=tmp_path).get("openai").credentials]
    assert s.remove_credential("openai", ids[0])
    assert [c.payload.auth_value for c in s.get("openai").credentials] == ["sk-bbbb-00000002"]
