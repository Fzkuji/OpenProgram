"""The one-time move of per-account credential files into one list per
provider (openprogram/models/auth/carry_over.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openprogram.models.auth import carry_over as co
from openprogram.models.auth.store import AuthStore


def _cred(cid: str, value: str, **extra) -> dict:
    return {"credential_id": cid, "kind": "api_key", "source": "web",
            "payload": {"kind": "api_key", "auth_value": value}, **extra}


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _old_layout(root: Path) -> Path:
    auth = root / "auth"
    _write(auth / "openai" / "default.json", {
        "provider_id": "openai", "account_id": "default", "active_credential_id": "k2",
        "credentials": [_cred("k1", "sk-one-00000001"), _cred("k2", "sk-two-00000002")]})
    _write(auth / "openai" / "work.json", {
        "provider_id": "openai", "account_id": "work",
        "credentials": [_cred("k3", "sk-work-0000003", status="rate_limited",
                              cooldown_until_ms=1)]})
    _write(auth / "deepseek" / "default.json", {
        "provider_id": "deepseek", "account_id": "default",
        "credentials": [{"credential_id": "d1", "kind": "api_key",
                         "payload": {"__type__": "ApiKeyPayload", "api_key": "sk-ds-00000001"}}]})
    _write(auth / "openai-codex" / "default.json", {
        "provider_id": "openai-codex", "account_id": "default",
        "credentials": [{"credential_id": "c1", "kind": "cli_delegated", "source": "codex_cli",
                         "read_only": True, "metadata": {"email": "a@example.com"},
                         "payload": {"kind": "cli_delegated",
                                     "data": {"store_path": "/nowhere/auth.json"}}}]})
    _write(auth / "_account_priority.json", {"openai": ["work", "default"]})
    _write(auth / "_rotation.json", {"openai": {"enabled": True, "strategy": "round_robin"}})
    _write(auth / "_rotation_excluded.json", {})
    _write(auth / "_active.json", {"openai": "default"})
    return auth


def _secrets(auth: Path) -> list[str]:
    return sorted(c["payload"]["auth_value"] for d in auth.iterdir() if (d / "credentials.json").exists()
                  for c in json.loads((d / "credentials.json").read_text())["credentials"])


def test_every_credential_arrives_in_one_list_per_provider(tmp_path):
    auth = _old_layout(tmp_path)
    report = co.carry_over(auth, state_root=tmp_path)
    assert report.ran
    assert report.before == report.after == {"deepseek": 1, "openai": 3, "openai-codex": 1}
    openai = json.loads((auth / "openai" / "credentials.json").read_text())
    # account priority (work first), then each account's active credential first
    assert [c["id"] for c in openai["credentials"]] == ["k3", "k2", "k1"]
    assert openai["strategy"] == "evenly"
    assert openai["credentials"][0]["label"] == "work"
    assert openai["credentials"][1]["label"] == ""
    codex = json.loads((auth / "openai-codex" / "credentials.json").read_text())["credentials"][0]
    assert (codex["kind"], codex["source"], codex["identity"]) == ("import", "import", {"email": "a@example.com"})
    assert codex["payload"]["kind"] == "import"
    assert "sk-ds-00000001" in _secrets(auth)


def test_the_old_files_are_removed_and_a_whole_backup_is_kept(tmp_path):
    auth = _old_layout(tmp_path)
    before = sorted(p.relative_to(auth) for p in auth.rglob("*.json"))
    report = co.carry_over(auth, state_root=tmp_path)
    # Inside auth/, which the sandbox never lets a tool read.
    assert report.backup == auth / co.BACKUP_DIR
    assert sorted(p.relative_to(report.backup) for p in report.backup.rglob("*.json")) == before
    left = sorted(str(p.relative_to(auth)) for p in auth.rglob("*.json")
                  if co.BACKUP_DIR not in p.parts)
    assert left == ["deepseek/credentials.json", "openai-codex/credentials.json", "openai/credentials.json"]


def test_running_again_changes_nothing(tmp_path):
    auth = _old_layout(tmp_path)
    co.carry_over(auth, state_root=tmp_path)
    first = {p: p.read_bytes() for p in auth.rglob("credentials.json")}
    again = co.carry_over(auth, state_root=tmp_path)
    assert not again.ran
    assert {p: p.read_bytes() for p in auth.rglob("credentials.json")} == first
    assert [p.name for p in auth.iterdir() if p.name.startswith(".carry")] == [co.BACKUP_DIR]


def test_a_partial_run_does_not_add_a_credential_twice(tmp_path):
    auth = _old_layout(tmp_path)
    co.carry_over(auth, state_root=tmp_path)
    # an old file that came back (a removal that did not finish)
    _write(auth / "openai" / "default.json", {
        "provider_id": "openai", "account_id": "default",
        "credentials": [_cred("k1", "sk-one-00000001")]})
    co.carry_over(auth, state_root=tmp_path)
    ids = [c["id"] for c in json.loads((auth / "openai" / "credentials.json").read_text())["credentials"]]
    assert sorted(ids) == ["k1", "k2", "k3"]


def test_a_provider_that_fails_keeps_its_old_files_and_holds_up_no_other(tmp_path, monkeypatch):
    auth = _old_layout(tmp_path)
    codex_old = (auth / "openai-codex" / "default.json").read_bytes()
    real = co._flatten

    def broken(provider, pools, side, now_ms):
        if provider == "openai-codex":
            raise RuntimeError("disk full")
        return real(provider, pools, side, now_ms)

    monkeypatch.setattr(co, "_flatten", broken)
    report = co.carry_over(auth, state_root=tmp_path)
    assert report.after == {"deepseek": 1, "openai": 3}
    assert (auth / "openai-codex" / "default.json").read_bytes() == codex_old
    assert not (auth / "openai-codex" / "credentials.json").exists()
    assert (auth / "_rotation.json").exists()  # side files wait for every provider
    assert not (auth / "openai" / "default.json").exists()

    monkeypatch.setattr(co, "_flatten", real)
    again = co.carry_over(auth, state_root=tmp_path)
    assert again.after == {"openai-codex": 1}
    assert not (auth / "_rotation.json").exists()
    assert [p.name for p in auth.iterdir() if p.name.startswith(".carry")] == [co.BACKUP_DIR]


def test_a_file_that_is_not_an_account_file_is_left_alone(tmp_path):
    auth = _old_layout(tmp_path)
    _write(auth / "myproxy" / "settings.json", {"base_url": "http://x", "note": "hand made"})
    _write(auth / "openai" / "notes.json", [1, 2])
    co.carry_over(auth, state_root=tmp_path)
    assert json.loads((auth / "myproxy" / "settings.json").read_text())["note"] == "hand made"
    assert not (auth / "myproxy" / "credentials.json").exists()
    assert (auth / "openai" / "notes.json").exists()
    assert "myproxy" not in AuthStore(root=tmp_path).providers()
    # Nothing left to carry: the next start neither runs nor backs up again.
    assert not co.needs_carry_over(auth)


def test_an_unreadable_file_is_skipped_without_blocking_or_repeating(tmp_path):
    auth = _old_layout(tmp_path)
    (auth / "zhipu").mkdir()
    (auth / "zhipu" / "default.json").write_text('{"provider_id": "zhipu", "credentials": [', encoding="utf-8")
    for _ in range(3):
        AuthStore(root=tmp_path)
    assert len(json.loads((auth / "openai" / "credentials.json").read_text())["credentials"]) == 3
    assert (auth / "zhipu" / "default.json").exists()
    assert [p.name for p in auth.iterdir() if p.name.startswith(".carry")] == [co.BACKUP_DIR]
    assert not co.needs_carry_over(auth)


def test_a_file_without_read_permission_does_not_stop_the_backup(tmp_path):
    import os
    auth = _old_layout(tmp_path)
    locked = auth / "zhipu" / "default.json"
    _write(locked, {"provider_id": "zhipu", "account_id": "default", "credentials": [_cred("z1", "sk-z")]})
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with permission to read every file")
        for _ in range(2):
            AuthStore(root=tmp_path)
        assert (auth / "openai" / "credentials.json").exists()
        assert not (auth / "openai" / "default.json").exists()
        assert (auth / co.BACKUP_DIR / ".complete").exists()
        assert locked.exists()
    finally:
        locked.chmod(0o600)


def test_a_damaged_new_file_is_read_through_its_last_good_copy(tmp_path):
    auth = _old_layout(tmp_path)
    co.carry_over(auth, state_root=tmp_path)
    target = auth / "openai" / "credentials.json"
    target.with_name("credentials.json.bak").write_bytes(target.read_bytes())
    target.write_text('{"v": 2, "credentials": [', encoding="utf-8")
    _write(auth / "openai" / "extra.json", {"provider_id": "openai", "account_id": "extra",
                                            "credentials": [_cred("k9", "sk-nine-0000009")]})
    co.carry_over(auth, state_root=tmp_path)
    ids = [c["id"] for c in json.loads(target.read_text())["credentials"]]
    assert sorted(ids) == ["k1", "k2", "k3", "k9"]


def test_a_partial_backup_is_replaced_and_a_complete_one_reused(tmp_path):
    auth = _old_layout(tmp_path)
    partial = auth / co.BACKUP_DIR
    (partial / "openai").mkdir(parents=True)
    report = co.carry_over(auth, state_root=tmp_path)
    assert (report.backup / "deepseek" / "default.json").exists()
    assert (report.backup / ".complete").exists()


def test_credentials_without_an_id_each_get_their_own(tmp_path):
    auth = tmp_path / "auth"
    keyless = {"kind": "api_key", "payload": {"kind": "api_key", "auth_value": "sk-aaaa-00000001"}}
    other = {"kind": "api_key", "payload": {"kind": "api_key", "auth_value": "sk-bbbb-00000002"}}
    _write(auth / "openai" / "default.json", {"provider_id": "openai", "account_id": "default",
                                              "credentials": [keyless, other]})
    _write(auth / "openai" / "copy.json", {"provider_id": "openai", "account_id": "copy",
                                           "credentials": [_cred("k1", "sk-one-00000001"),
                                                           _cred("k1", "sk-dup-00000009")]})
    store = AuthStore(root=tmp_path)
    ids = [c.id for c in store.get("openai").credentials]
    assert len(ids) == 4 and len(set(ids)) == 4 and all(ids)
    assert store.remove_credential("openai", ids[0]) is True
    assert len(store.get("openai").credentials) == 3


def test_the_store_carries_over_before_it_first_reads(tmp_path):
    _old_layout(tmp_path)
    store = AuthStore(root=tmp_path)
    assert [c.id for c in store.get("openai").credentials] == ["k3", "k2", "k1"]
    assert store.get("deepseek").credentials[0].payload.auth_value == "sk-ds-00000001"


def test_a_read_only_store_leaves_another_instance_alone(tmp_path):
    auth = _old_layout(tmp_path)
    before = {p: p.read_bytes() for p in auth.rglob("*.json")}
    store = AuthStore(root=tmp_path, carry_over=False)
    assert store.providers() == []
    assert {p: p.read_bytes() for p in auth.rglob("*.json")} == before


def test_a_key_the_list_already_holds_is_not_added_again(tmp_path):
    """An older install elsewhere keeps writing the old layout from its
    environment variable; every start would add the same key once more."""
    auth = tmp_path / "auth"
    stored = {"id": "cred_first", "provider_id": "opencode-go", "kind": "api_key", "source": "cli",
              "use_count": 2, "payload": {"kind": "api_key", "auth_value": "sk-go-00000001"}}
    repeat = {**stored, "id": "cred_again", "use_count": 0}
    other = {**stored, "id": "cred_other", "payload": {"kind": "api_key", "auth_value": "sk-go-00000002"}}
    _write(auth / "opencode-go" / "credentials.json", {
        "v": 4, "provider_id": "opencode-go", "strategy": "in_order", "cursor": 0,
        "credentials": [stored, repeat, other]})
    _write(auth / "opencode-go" / "default.json", {
        "provider_id": "opencode-go", "profile_id": "default",
        "credentials": [{"credential_id": "old1", "kind": "api_key", "source": "env", "read_only": True,
                         "payload": {"__type__": "ApiKeyPayload", "api_key": "sk-go-00000001"}}]})
    co.carry_over(auth, state_root=tmp_path)
    rows = json.loads((auth / "opencode-go" / "credentials.json").read_text())["credentials"]
    assert [r["id"] for r in rows] == ["cred_first", "cred_other"]
    assert rows[0]["use_count"] == 2
    assert not (auth / "opencode-go" / "default.json").exists()
    assert _secrets(auth) == ["sk-go-00000001", "sk-go-00000002"]
