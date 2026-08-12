"""Registered secret writers share the private atomic file contract."""

from __future__ import annotations

import json
import os
import subprocess
import stat
import sys
import time
from pathlib import Path

import pytest


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _symlink_target(root: Path, relative: str) -> tuple[Path, Path]:
    outside = root.parent / (Path(relative).name + ".outside")
    outside.write_text("outside", encoding="utf-8")
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    return target, outside


def test_config_writer_rejects_stale_external_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram import setup
    from openprogram.credential_files import PrivateAtomicWriteError

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    path = root / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: path)
    first = setup._write_config({"api_keys": {"OPENAI_API_KEY": "one"}})
    path.write_text('{"api_keys":{"OPENAI_API_KEY":"external"}}\n', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(PrivateAtomicWriteError) as exc:
        setup._write_config(
            {"api_keys": {"OPENAI_API_KEY": "ours"}},
            expected_revision=first.revision,
        )

    assert exc.value.code == "conflict"
    assert "external" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="POSIX publication contract")
def test_config_writer_propagates_committed_not_durable_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram import credential_files, setup

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    path = root / "config.json"
    monkeypatch.setattr(setup, "get_config_path", lambda: path)
    setup._write_config({"api_keys": {"OPENAI_API_KEY": "old"}})
    real_fsync = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(credential_files.os, "fsync", fail_directory_fsync)
    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        setup._write_config({"api_keys": {"OPENAI_API_KEY": "new"}})

    assert exc.value.code == "committed_not_durable"
    assert exc.value.committed is True
    assert json.loads(path.read_text(encoding="utf-8"))["api_keys"] == {
        "OPENAI_API_KEY": "new"
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync contract")
def test_web_start_propagates_committed_token_without_deleting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram import credential_files
    from openprogram.webui.owner_auth import OwnerAuthState

    real_fsync = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(credential_files.os, "fsync", fail_directory_fsync)
    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        OwnerAuthState.start(
            state_dir=tmp_path,
            bind_host="127.0.0.1",
            port=18100,
            allowed_origins=(),
            raw_token=bytes(range(32)),
            owner_principal_id="owner/install/0123456789abcdef",
        )

    assert exc.value.code == "committed_not_durable"
    assert exc.value.committed is True
    assert (tmp_path / "web" / "token").is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlink contract")
def test_dotenv_writer_rejects_symlink_target(tmp_path: Path) -> None:
    from openprogram.auth.accounts import _write_dotenv
    from openprogram.credential_files import PrivateAtomicWriteError

    root = tmp_path / "profiles"
    root.mkdir(mode=0o700)
    target, outside = _symlink_target(root, "default/.env")

    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _write_dotenv(target, {"API_KEY": "secret"}, root=root)
    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="symlink contract")
def test_channel_credential_and_access_writers_reject_symlink_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.channels import _access, accounts
    from openprogram.credential_files import PrivateAtomicWriteError

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    credential, credential_outside = _symlink_target(
        root, "channels/telegram/accounts/default/credentials.json"
    )
    monkeypatch.setattr(
        accounts, "account_credentials_path", lambda _channel, _account: credential
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: root)
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        accounts.save_credentials("telegram", "default", {"bot_token": "secret"})
    assert credential_outside.read_text(encoding="utf-8") == "outside"

    access, access_outside = _symlink_target(
        root, "channels/telegram/accounts/default/access.json"
    )
    monkeypatch.setattr(_access, "access_path", lambda _channel, _account: access)
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _access._save("telegram", "default", {"pending": {"u": {"code": "123"}}})
    assert access_outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="symlink contract")
def test_mcp_config_and_token_writers_reject_symlink_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.credential_files import PrivateAtomicWriteError
    from openprogram.mcp import config, token_storage

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    config_path, config_outside = _symlink_target(root, "mcp_servers.json")
    monkeypatch.setattr(config, "get_config_path", lambda: config_path)
    monkeypatch.setattr(config._paths, "get_state_dir", lambda: root)
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        config.save_configs([])
    assert config_outside.read_text(encoding="utf-8") == "outside"

    token_path, token_outside = _symlink_target(root, "mcp_tokens/server.json")
    monkeypatch.setattr(token_storage, "get_tokens_dir", lambda: token_path.parent)
    storage = token_storage.FileTokenStorage("server")
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        storage._write({"tokens": {"access_token": "secret"}})
    assert token_outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="symlink contract")
def test_auth_store_and_web_token_writers_reject_symlink_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import Credential, CredentialData, CredentialPool
    from openprogram.credential_files import PrivateAtomicWriteError
    from openprogram.webui.owner_auth import _write_private_text

    auth_root = tmp_path / "auth-state"
    auth_root.mkdir(mode=0o700)
    auth_target, auth_outside = _symlink_target(auth_root, "auth/openai/default.json")
    pool = CredentialPool(provider_id="openai", account_id="default")
    pool.credentials.append(
        Credential(
            credential_id="one",
            provider_id="openai",
            account_id="default",
            kind="api_key",
            source="manual",
            payload=CredentialData(kind="api_key", auth_value="secret"),
        )
    )
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        AuthStore(root=auth_root).put_pool(pool)
    assert auth_target.is_symlink()
    assert auth_outside.read_text(encoding="utf-8") == "outside"

    web_root = tmp_path / "web-state"
    web_root.mkdir(mode=0o700)
    web_target, web_outside = _symlink_target(web_root, "web/token")
    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _write_private_text(web_target, "secret")
    assert web_outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink contract")
def test_public_credential_writers_do_not_create_through_parent_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import CredentialPool
    from openprogram.channels import accounts
    from openprogram.credential_files import PrivateAtomicWriteError

    channel_root = tmp_path / "channel-state"
    channel_root.mkdir(mode=0o700)
    channel_outside = tmp_path / "channel-outside"
    channel_outside.mkdir()
    (channel_root / "channels").symlink_to(channel_outside)
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: channel_root)
    with pytest.raises(PrivateAtomicWriteError):
        accounts.save_credentials("telegram", "default", {"token": "secret"})
    assert list(channel_outside.iterdir()) == []

    auth_root = tmp_path / "auth-state"
    auth_root.mkdir(mode=0o700)
    auth_outside = tmp_path / "auth-outside"
    auth_outside.mkdir()
    (auth_root / "auth").symlink_to(auth_outside)
    with pytest.raises(PrivateAtomicWriteError):
        AuthStore(root=auth_root).put_pool(
            CredentialPool(provider_id="openai", account_id="default")
        )
    assert list(auth_outside.iterdir()) == []


def test_auth_store_detects_external_edit_before_republishing_cached_pool(
    tmp_path: Path,
) -> None:
    from openprogram.auth.store import AuthStore
    from openprogram.auth.types import Credential, CredentialData
    from openprogram.credential_files import PrivateAtomicWriteError

    store = AuthStore(root=tmp_path)
    credential = Credential(
        credential_id="one",
        provider_id="openai",
        account_id="default",
        kind="api_key",
        source="manual",
        payload=CredentialData(kind="api_key", auth_value="secret"),
    )
    pool = store.add_credential(credential)
    path = tmp_path / "auth" / "openai" / "default.json"
    path.write_bytes(path.read_bytes() + b"\n")
    path.chmod(0o600)

    with pytest.raises(PrivateAtomicWriteError) as exc:
        store.put_pool(pool)
    assert exc.value.code == "conflict"


@pytest.mark.skipif(os.name == "nt", reason="symlink contract")
def test_legacy_auth_pool_migration_rejects_symlink_target(tmp_path: Path) -> None:
    from openprogram.auth._migrate_payload import _migrate_file
    from openprogram.credential_files import PrivateAtomicWriteError

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target, outside = _symlink_target(root, "auth/openai/default.json")
    outside.write_text(
        json.dumps(
            {
                "credentials": [
                    {
                        "v": 1,
                        "payload": {
                            "__type__": "ApiKeyPayload",
                            "api_key": "secret",
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _migrate_file(target, root=root)
    assert target.is_symlink()
    assert "__type__" in outside.read_text(encoding="utf-8")


def test_channel_create_does_not_overwrite_concurrent_process_credentials(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ready = tmp_path / "create-ready"
    attempted = tmp_path / "save-attempted"
    release = tmp_path / "release"
    env = {**os.environ, "HOME": os.fspath(home)}
    create_script = """
import sys, time
from pathlib import Path
from openprogram import credential_files
from openprogram.channels import accounts
ready, release = map(Path, sys.argv[1:])
real_write = credential_files._private_atomic_write
def paused_write(*args, **kwargs):
    ready.write_text('ready')
    while not release.exists():
        time.sleep(0.01)
    return real_write(*args, **kwargs)
credential_files._private_atomic_write = paused_write
accounts.create('telegram', 'default')
"""
    save_script = """
import sys
from pathlib import Path
from openprogram.channels import accounts
attempted = Path(sys.argv[1])
attempted.write_text('attempted')
accounts.save_credentials('telegram', 'default', {'bot_token': 'secret'})
"""
    creator = subprocess.Popen(
        [sys.executable, "-c", create_script, os.fspath(ready), os.fspath(release)],
        cwd=Path(__file__).parents[2],
        env=env,
    )
    _wait_for_path(ready)
    saver = subprocess.Popen(
        [sys.executable, "-c", save_script, os.fspath(attempted)],
        cwd=Path(__file__).parents[2],
        env=env,
    )
    _wait_for_path(attempted)
    time.sleep(0.2)
    release.write_text("go", encoding="utf-8")

    assert creator.wait(timeout=10) == 0
    assert saver.wait(timeout=10) == 0
    stored = (
        home / ".openprogram" / "channels/telegram/accounts/default/credentials.json"
    )
    assert json.loads(stored.read_text(encoding="utf-8")) == {"bot_token": "secret"}
