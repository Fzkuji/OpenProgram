"""Behavior tests for the credential inventory and private atomic writer."""

from __future__ import annotations

import errno
import json
import os
import stat
import subprocess
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


def test_inventory_classifies_every_persisted_secret_surface() -> None:
    from openprogram.credential_files import inventory_for_path

    expected = {
        "config.json": (
            "config_api_keys",
            "redact_default",
            "openprogram.setup._write_config",
        ),
        "auth/openai/default.json": (
            "auth_store",
            "include_on_opt_in",
            "openprogram.auth.store.AuthStore",
        ),
        "profiles/work/auth/openai/default.json": (
            "profile_auth_store",
            "include_on_opt_in",
            "openprogram.auth.store.AuthStore",
        ),
        "profiles/work/.env": (
            "profile_env",
            "include_on_opt_in",
            "openprogram.auth.accounts._write_dotenv",
        ),
        "channels/slack/accounts/default/credentials.json": (
            "channel_credentials",
            "include_on_opt_in",
            "openprogram.channels.accounts.save_credentials",
        ),
        "channels/slack/accounts/default/access.json": (
            "channel_pairing_codes",
            "never_backup",
            "openprogram.channels._access._save",
        ),
        "mcp_servers.json": (
            "mcp_server_secrets",
            "redact_default",
            "openprogram.mcp.config.save_configs",
        ),
        "mcp_tokens/github.json": (
            "mcp_tokens",
            "include_on_opt_in",
            "openprogram.mcp.token_storage.FileTokenStorage",
        ),
        "web/token": (
            "web_runtime_token",
            "never_backup",
            "openprogram.webui.owner_auth._write_private_text",
        ),
    }

    for relative_path, want in expected.items():
        entries = inventory_for_path(relative_path)
        assert [
            (entry.kind, entry.backup_policy, entry.writer) for entry in entries
        ] == [want]


@pytest.mark.parametrize(
    ("registered", "kind", "too_deep", "too_shallow"),
    [
        (
            "auth/openai/default.json",
            "auth_store",
            "auth/openai/unregistered/account.json",
            "auth/openai.json",
        ),
        (
            "profiles/work/auth/openai/default.json",
            "profile_auth_store",
            "profiles/work/auth/openai/unregistered/account.json",
            "profiles/work/auth/openai.json",
        ),
        (
            "profiles/work/.env",
            "profile_env",
            "profiles/work/unregistered/.env",
            "profiles/.env",
        ),
        (
            "channels/slack/accounts/default/credentials.json",
            "channel_credentials",
            "channels/slack/accounts/default/unregistered/credentials.json",
            "channels/slack/accounts/credentials.json",
        ),
        (
            "channels/slack/accounts/default/access.json",
            "channel_pairing_codes",
            "channels/slack/accounts/default/unregistered/access.json",
            "channels/slack/accounts/access.json",
        ),
        (
            "mcp_tokens/github.json",
            "mcp_tokens",
            "mcp_tokens/unregistered/github.json",
            "github.json",
        ),
    ],
)
def test_inventory_wildcards_match_exactly_one_path_segment(
    registered: str,
    kind: str,
    too_deep: str,
    too_shallow: str,
) -> None:
    from openprogram.credential_files import inventory_for_path

    assert [entry.kind for entry in inventory_for_path(registered)] == [kind]
    assert inventory_for_path(too_deep) == ()
    assert inventory_for_path(too_shallow) == ()


def test_restore_preserves_masked_secret_values() -> None:
    from openprogram.credential_files import preserve_local_secret_bytes

    restored = json.dumps(
        {
            "theme": "archived",
            "api_keys": {
                "SHORT_KEY": "••••••••",
                "LONG_KEY": "abc…wxyz",
            },
        }
    ).encode()
    local = json.dumps(
        {
            "theme": "local",
            "api_keys": {
                "SHORT_KEY": "short-local-secret",
                "LONG_KEY": "long-local-secret",
            },
        }
    ).encode()

    merged = json.loads(preserve_local_secret_bytes("config.json", restored, local))
    assert merged == {
        "theme": "archived",
        "api_keys": {
            "SHORT_KEY": "short-local-secret",
            "LONG_KEY": "long-local-secret",
        },
    }


@pytest.mark.parametrize(
    ("archived_auth", "expected_auth"),
    [
        (None, {"token": "local-token"}),
        ({"kind": "bearer"}, {"kind": "bearer", "token": "local-token"}),
        (
            {"kind": "bearer", "token": "<redacted>"},
            {"kind": "bearer", "token": "local-token"},
        ),
    ],
)
def test_restore_recreates_only_missing_mcp_secret_path(
    archived_auth: dict[str, str] | None,
    expected_auth: dict[str, str],
) -> None:
    from openprogram.credential_files import preserve_local_secret_bytes

    archived_server = {"type": "http", "label": "archived"}
    if archived_auth is not None:
        archived_server["auth"] = archived_auth
    restored = json.dumps(
        {"servers": {"remote": archived_server}, "roots": ["archived"]}
    ).encode()
    local = json.dumps(
        {
            "servers": {
                "remote": {
                    "type": "stdio",
                    "local_only": "must-not-copy",
                    "auth": {
                        "kind": "bearer",
                        "token": "local-token",
                        "local_auth_only": "must-not-copy",
                    },
                }
            },
            "roots": ["local"],
        }
    ).encode()

    merged = json.loads(
        preserve_local_secret_bytes("mcp_servers.json", restored, local)
    )

    assert merged == {
        "roots": ["archived"],
        "servers": {
            "remote": {
                "type": "http",
                "label": "archived",
                "auth": expected_auth,
            }
        },
    }


def test_restore_recreates_missing_mcp_oauth_client_secret_only() -> None:
    from openprogram.credential_files import preserve_local_secret_bytes

    restored = json.dumps(
        {
            "servers": {
                "oauth": {
                    "type": "http",
                    "auth": {"kind": "oauth", "client_id": "archived-id"},
                }
            }
        }
    ).encode()
    local = json.dumps(
        {
            "servers": {
                "oauth": {
                    "type": "stdio",
                    "auth": {
                        "kind": "oauth",
                        "client_id": "local-id",
                        "client_secret": "local-secret",
                        "token_endpoint": "local-only",
                    },
                }
            }
        }
    ).encode()

    merged = json.loads(
        preserve_local_secret_bytes("mcp_servers.json", restored, local)
    )

    assert merged == {
        "servers": {
            "oauth": {
                "type": "http",
                "auth": {
                    "kind": "oauth",
                    "client_id": "archived-id",
                    "client_secret": "local-secret",
                },
            }
        }
    }


def test_windows_acl_verification_rejects_an_extra_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    monkeypatch.setenv("USERNAME", "Alice")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "C:\\state\\backup.tar.gz Alice:(F)\n"
                "                            NT AUTHORITY\\SYSTEM:(F)\n"
                "                            BUILTIN\\Users:(F)\n"
                "Successfully processed 1 files; Failed processing 0 files\n",
                "",
            ),
        ]
    )
    monkeypatch.setattr(
        credential_files.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    with pytest.raises(OSError, match="unexpected principal"):
        credential_files._apply_windows_owner_acl(Path(r"C:\state\backup.tar.gz"))


def test_windows_acl_verification_accepts_current_user_and_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    monkeypatch.setenv("USERNAME", "Alice")
    responses = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess(
                [],
                0,
                "C:\\state\\backup.tar.gz Alice:(F)\n"
                "                            NT AUTHORITY\\SYSTEM:(F)\n"
                "Successfully processed 1 files; Failed processing 0 files\n",
                "",
            ),
        ]
    )
    monkeypatch.setattr(
        credential_files.subprocess,
        "run",
        lambda *_args, **_kwargs: next(responses),
    )

    credential_files._apply_windows_owner_acl(Path(r"C:\state\backup.tar.gz"))


def test_windows_acl_command_failure_is_an_os_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    monkeypatch.setenv("USERNAME", "Alice")
    monkeypatch.setattr(
        credential_files.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("icacls", 10)
        ),
    )

    with pytest.raises(OSError, match="Windows ACL command failed"):
        credential_files._apply_windows_owner_acl(Path(r"C:\state\backup.tar.gz"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
@pytest.mark.parametrize("mask", [0o000, 0o022])
def test_private_atomic_write_is_private_from_first_visibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mask: int,
) -> None:
    from openprogram import credential_files

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "backup.tar.gz"
    real_replace = os.replace
    observed: list[tuple[int, bool]] = []

    def inspect_replace(
        source: str | os.PathLike[str], destination: str | os.PathLike[str]
    ):
        observed.append(
            (stat.S_IMODE(os.stat(source).st_mode), Path(destination).exists())
        )
        real_replace(source, destination)
        assert stat.S_IMODE(Path(destination).stat().st_mode) == 0o600

    monkeypatch.setattr(credential_files.os, "replace", inspect_replace)
    previous = os.umask(mask)
    try:
        credential_files._private_atomic_write(
            target,
            lambda handle: handle.write(b"archive"),
            root=root,
        )
    finally:
        os.umask(previous)

    assert observed == [(0o600, False)]
    assert target.read_bytes() == b"archive"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX path contract")
def test_private_atomic_write_rejects_symlink_and_non_regular_targets(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import (
        PrivateAtomicWriteError,
        _private_atomic_write,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    symlink = root / "backup.tar.gz"
    symlink.symlink_to(outside)

    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _private_atomic_write(symlink, lambda handle: handle.write(b"new"), root=root)
    assert outside.read_bytes() == b"outside"

    symlink.unlink()
    symlink.mkdir()
    with pytest.raises(PrivateAtomicWriteError, match="regular file"):
        _private_atomic_write(symlink, lambda handle: handle.write(b"new"), root=root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX path contract")
def test_private_atomic_write_rejects_symlink_path_component(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import (
        PrivateAtomicWriteError,
        _private_atomic_write,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "backups").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PrivateAtomicWriteError, match="symlink"):
        _private_atomic_write(
            root / "backups" / "backup.tar.gz",
            lambda handle: handle.write(b"new"),
            root=root,
        )
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner contract")
def test_private_atomic_write_rejects_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(credential_files.os, "geteuid", lambda: root.stat().st_uid + 1)

    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        credential_files._private_atomic_write(
            root / "backup.tar.gz",
            lambda handle: handle.write(b"new"),
            root=root,
        )
    assert exc.value.code == "foreign_owner"


def test_private_atomic_write_uses_no_fixed_temporary_name(tmp_path: Path) -> None:
    from openprogram.credential_files import _private_atomic_write

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "backup.tar.gz"
    planted = root / "backup.tar.gz.partial"
    planted.write_bytes(b"attacker")

    _private_atomic_write(target, lambda handle: handle.write(b"archive"), root=root)

    assert target.read_bytes() == b"archive"
    assert planted.read_bytes() == b"attacker"
    assert list(root.glob(".*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
def test_private_atomic_write_replaces_historical_wide_file_privately(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import _private_atomic_write

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "backup.tar.gz"
    target.write_bytes(b"old")
    target.chmod(0o644)

    _private_atomic_write(target, lambda handle: handle.write(b"new"), root=root)

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_private_atomic_write_preserves_old_file_before_commit_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "backup.tar.gz"
    target.write_bytes(b"old")

    monkeypatch.setattr(
        credential_files.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("file fsync failed")),
    )
    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        credential_files._private_atomic_write(
            target,
            lambda handle: handle.write(b"new"),
            root=root,
        )
    assert exc.value.code == "fsync"
    assert exc.value.committed is False
    assert target.read_bytes() == b"old"

    monkeypatch.undo()
    monkeypatch.setattr(
        credential_files.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(
            OSError(errno.EXDEV, "cross-device replace")
        ),
    )
    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        credential_files._private_atomic_write(
            target,
            lambda handle: handle.write(b"new"),
            root=root,
        )
    assert exc.value.code == "replace"
    assert exc.value.committed is False
    assert target.read_bytes() == b"old"


def test_private_atomic_write_wraps_callback_value_error_and_cleans_temp(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import (
        PrivateAtomicWriteError,
        _private_atomic_write,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    target.write_bytes(b"old")
    target.chmod(0o600)

    def invalid_serialization(_handle) -> None:
        raise ValueError("cannot serialize")

    with pytest.raises(PrivateAtomicWriteError) as exc:
        _private_atomic_write(target, invalid_serialization, root=root)

    assert exc.value.code == "serialization"
    assert exc.value.committed is False
    assert isinstance(exc.value.__cause__, ValueError)
    assert target.read_bytes() == b"old"
    assert list(root.glob(".config.json.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory fsync contract")
def test_private_atomic_write_reports_committed_not_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openprogram import credential_files

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "backup.tar.gz"
    target.write_bytes(b"old")
    real_fsync = os.fsync

    def fail_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(credential_files.os, "fsync", fail_directory_fsync)
    with pytest.raises(credential_files.PrivateAtomicWriteError) as exc:
        credential_files._private_atomic_write(
            target,
            lambda handle: handle.write(b"new"),
            root=root,
        )

    assert exc.value.code == "committed_not_durable"
    assert exc.value.committed is True
    assert target.read_bytes() == b"new"


def test_private_atomic_write_revision_fingerprints_exact_bytes_and_missing(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import (
        _private_atomic_write,
        private_file_revision,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    missing = private_file_revision(target, root=root)

    first = _private_atomic_write(
        target,
        lambda handle: handle.write(b'{"value":1}\n'),
        root=root,
        expected_revision=missing,
    )
    second_revision = private_file_revision(target, root=root)
    target.write_bytes(b'{"value":1} \n')
    target.chmod(0o600)

    assert missing.startswith("missing:sha256:")
    assert first.revision == second_revision
    assert private_file_revision(target, root=root) != second_revision


def test_private_atomic_write_rejects_stale_revision_before_publication(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import (
        PrivateAtomicWriteError,
        _private_atomic_write,
        private_file_revision,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    target.write_bytes(b"initial")
    target.chmod(0o600)
    revision = private_file_revision(target, root=root)
    target.write_bytes(b"external-edit")

    with pytest.raises(PrivateAtomicWriteError) as exc:
        _private_atomic_write(
            target,
            lambda handle: handle.write(b"ours"),
            root=root,
            expected_revision=revision,
        )

    assert exc.value.code == "conflict"
    assert exc.value.committed is False
    assert target.read_bytes() == b"external-edit"


def test_private_atomic_update_serializes_two_processes(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    script = """
import json, sys, time
from pathlib import Path
from openprogram.credential_files import _private_atomic_update
root, target, key = map(Path, sys.argv[1:])
def update(raw):
    value = json.loads((raw or b'{}').decode())
    time.sleep(0.15)
    value[key.name] = key.name
    return json.dumps(value, sort_keys=True).encode()
_private_atomic_update(target, update, root=root)
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, os.fspath(root), os.fspath(target), key],
            cwd=Path(__file__).parents[2],
        )
        for key in ("first", "second")
    ]

    assert [process.wait(timeout=10) for process in processes] == [0, 0]
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "first": "first",
        "second": "second",
    }


def test_private_atomic_write_lock_timeout_is_structured(tmp_path: Path) -> None:
    from openprogram.credential_files import (
        PrivateAtomicWriteError,
        _private_atomic_write,
    )

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    ready = tmp_path / "ready"
    script = """
import sys, time
from pathlib import Path
from openprogram.credential_files import _private_file_lock
root, target, ready = map(Path, sys.argv[1:])
with _private_file_lock(target, root=root, timeout=2):
    ready.write_text('ready')
    time.sleep(1)
"""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            os.fspath(root),
            os.fspath(target),
            os.fspath(ready),
        ],
        cwd=Path(__file__).parents[2],
    )
    try:
        _wait_for_path(ready)
        with pytest.raises(PrivateAtomicWriteError) as exc:
            _private_atomic_write(
                target,
                lambda handle: handle.write(b"new"),
                root=root,
                lock_timeout=0.05,
            )
        assert exc.value.code == "lock_timeout"
        assert exc.value.committed is False
    finally:
        holder.terminate()
        holder.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX lock-file contract")
def test_private_lock_is_owner_only_and_rejects_unsafe_existing_lock(
    tmp_path: Path,
) -> None:
    from openprogram.credential_files import PrivateAtomicWriteError, _private_file_lock

    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    target = root / "config.json"
    lock = root / "config.json.lock"
    with _private_file_lock(target, root=root):
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600

    lock.unlink()
    lock.mkdir()
    with pytest.raises(PrivateAtomicWriteError) as exc:
        with _private_file_lock(target, root=root):
            pass
    assert exc.value.code == "lock"
