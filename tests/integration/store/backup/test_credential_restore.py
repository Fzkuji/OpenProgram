"""Credential restoration and private file publication."""
from __future__ import annotations
import json
import os
import stat
from pathlib import Path
import pytest

from tests.integration.store.backup.command_support import (
    profile as profile,
    _no_running_processes as _no_running_processes,
    _write_restorable_archive,
    _seed_registered_secrets,
)


def test_default_restore_preserves_local_secrets(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    _seed_registered_secrets(profile)
    archive = create_backup()
    local_config_secret = "local-config-secret"
    local_mcp_secret = "local-mcp-secret"
    local_pairing_code = "LOCALPAIR"
    whole_file_updates = {
        profile / "auth" / "openai" / "default.json": b"local-auth-secret",
        profile / "profiles" / "work" / "auth" / "openai" / "default.json": (
            b"local-profile-auth-secret"
        ),
        profile / "profiles" / "work" / ".env": b"API_KEY=local-env-secret\n",
        profile
        / "channels"
        / "slack"
        / "accounts"
        / "default"
        / "credentials.json": b'{"bot_token":"local-channel-secret"}',
        profile / "mcp_tokens" / "github.json": b'{"token":"local-mcp-token"}',
    }
    for path, content in whole_file_updates.items():
        path.write_bytes(content)
    (profile / "config.json").write_text(
        json.dumps(
            {
                "theme": "light",
                "api_keys": {"OPENAI_API_KEY": local_config_secret},
            }
        ),
        encoding="utf-8",
    )
    mcp_path = profile / "mcp_servers.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["servers"]["local"]["env"] = {"TOKEN": local_mcp_secret}
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    access_path = (
        profile / "channels" / "slack" / "accounts" / "default" / "access.json"
    )
    access = json.loads(access_path.read_text(encoding="utf-8"))
    access["pending"] = {"local": {"code": local_pairing_code}}
    access_path.write_text(json.dumps(access), encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    restored_config = json.loads((profile / "config.json").read_text(encoding="utf-8"))
    assert restored_config == {
        "theme": "dark",
        "api_keys": {"OPENAI_API_KEY": local_config_secret},
    }
    restored_mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert restored_mcp["servers"]["local"]["env"] == {"TOKEN": local_mcp_secret}
    restored_access = json.loads(access_path.read_text(encoding="utf-8"))
    assert restored_access["pending"] == {"local": {"code": local_pairing_code}}
    for path, content in whole_file_updates.items():
        assert path.read_bytes() == content


def test_opt_in_restore_replaces_persistent_secrets(profile: Path):
    from openprogram.cli.commands.backup import _cmd_backup_restore, create_backup

    secrets = _seed_registered_secrets(profile)
    archive = create_backup(include_credentials=True)
    auth_path = profile / "auth" / "openai" / "default.json"
    env_path = profile / "profiles" / "work" / ".env"
    auth_path.write_text('{"credentials":[]}', encoding="utf-8")
    env_path.write_text("API_KEY=changed\n", encoding="utf-8")

    assert _cmd_backup_restore(archive.name, yes=True) == 0

    assert secrets["auth_store"] in auth_path.read_bytes()
    assert secrets["profile_env"] in env_path.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
def test_restore_inventory_files_are_atomically_published_owner_only(
    profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram.auth import credentials as credential_files
    from openprogram.cli.commands.backup import restore_archive

    config = profile / "config.json"
    config.write_text(
        '{"theme":"local","api_keys":{"OPENAI_API_KEY":"local-secret"}}',
        encoding="utf-8",
    )
    config.chmod(0o600)
    auth = profile / "auth" / "openai" / "default.json"
    auth.parent.mkdir(parents=True, exist_ok=True)
    auth.write_bytes(b'{"credentials":"old-auth"}')
    auth.chmod(0o600)
    archive = tmp_path / "restore.tar.gz"
    mcp_token = profile / "mcp_tokens" / "restored.json"
    observed: dict[str, tuple[int, bytes | None]] = {}
    real_replace = os.replace

    def inspect_replace(source, destination) -> None:
        destination = Path(destination)
        if destination in {config, auth, mcp_token}:
            observed[destination.name] = (
                stat.S_IMODE(Path(source).stat().st_mode),
                destination.read_bytes() if destination.exists() else None,
            )
        real_replace(source, destination)

    monkeypatch.setattr(credential_files.os, "replace", inspect_replace)
    _write_restorable_archive(
        archive,
        {
            "config.json": b'{"theme":"archived"}',
            "auth/openai/default.json": b'{"credentials":"archived-auth"}',
            "mcp_tokens/restored.json": b'{"token":"archived-mcp-token"}',
        },
    )
    restore_archive(archive, profile)

    assert json.loads(config.read_text()) == {
        "theme": "archived",
        "api_keys": {"OPENAI_API_KEY": "local-secret"},
    }
    assert auth.read_bytes() == b'{"credentials":"archived-auth"}'
    assert mcp_token.read_bytes() == b'{"token":"archived-mcp-token"}'
    assert observed == {
        "config.json": (
            0o600,
            b'{"theme":"local","api_keys":{"OPENAI_API_KEY":"local-secret"}}',
        ),
        "default.json": (0o600, b'{"credentials":"old-auth"}'),
        "restored.json": (0o600, None),
    }
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert stat.S_IMODE(auth.stat().st_mode) == 0o600
        assert stat.S_IMODE(mcp_token.stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_restore_inventory_failure_preserves_old_file_and_cleans_temp(
    profile: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from openprogram.auth import credentials as credential_files
    from openprogram.cli.commands.backup import restore_archive

    target = profile / "auth" / "openai" / "default.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"credentials":"old-auth"}')
    target.chmod(0o600)
    archive = tmp_path / "restore-failure.tar.gz"
    real_fdopen = os.fdopen

    if failure == "write":

        class FailingWrite:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def write(self, _payload):
                raise OSError("write failed")

            def __getattr__(self, name):
                return getattr(self.handle, name)

        monkeypatch.setattr(
            credential_files.os,
            "fdopen",
            lambda fd, mode: FailingWrite(real_fdopen(fd, mode)),
        )
    elif failure == "fsync":
        monkeypatch.setattr(
            credential_files.os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")),
        )
    else:
        monkeypatch.setattr(
            credential_files.os,
            "replace",
            lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
        )

    _write_restorable_archive(
        archive,
        {"auth/openai/default.json": b'{"credentials":"archived-auth"}'},
    )
    from openprogram.cli.commands.backup import RestoreRollbackCompletedError

    with pytest.raises(RestoreRollbackCompletedError) as exc:
        restore_archive(archive, profile)

    assert isinstance(exc.value.__cause__, credential_files.PrivateAtomicWriteError)
    assert exc.value.__cause__.code == failure
    assert exc.value.__cause__.committed is False
    assert target.read_bytes() == b'{"credentials":"old-auth"}'
    assert list(target.parent.glob(".default.json.*.tmp")) == []
