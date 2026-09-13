from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.cli.commands.doctor import _cmd_doctor_credentials
from openprogram.auth.credentials import (
    _private_atomic_write,
    _read_private_bytes,
    audit_credentials,
)


def test_runtime_uses_ordinary_file_io(tmp_path: Path) -> None:
    target = tmp_path / "credentials.json"
    target.write_bytes(b'{"token":"plain"}')
    if os.name != "nt":
        target.chmod(0o644)

    assert _read_private_bytes(target, root=tmp_path) == b'{"token":"plain"}'
    assert audit_credentials(root=tmp_path) == []


@pytest.mark.skipif(os.name == "nt", reason="symlink setup differs on Windows")
def test_runtime_follows_credential_symlink(tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_bytes(b'{"token":"linked"}')
    link = tmp_path / "link.json"
    link.symlink_to(actual)

    assert _read_private_bytes(link, root=tmp_path) == b'{"token":"linked"}'


def test_runtime_write_does_not_enforce_private_mode(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "credentials.json"
    _private_atomic_write(
        target,
        lambda handle: handle.write(json.dumps({"token": "value"}).encode()),
        root=tmp_path,
    )

    assert json.loads(target.read_text()) == {"token": "value"}


def test_atomic_write_fsync_reopens_temp_writable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durability flush reopens the staged temp file with a writable
    handle.

    Regression: it used ``O_RDONLY`` — POSIX fsync tolerates a read-only
    descriptor, but Windows ``os.fsync`` (``_commit``) raises EBADF there,
    which crashed every credential write on that platform.
    """
    import os

    target = tmp_path / "credentials.json"
    real_open = os.open
    tmp_flags: list[int] = []

    def spy_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if str(path).endswith(".tmp"):
            tmp_flags.append(flags)
        return fd

    monkeypatch.setattr(os, "open", spy_open)

    _private_atomic_write(
        target,
        lambda handle: handle.write(b'{"token":"value"}'),
        root=tmp_path,
    )

    assert json.loads(target.read_text()) == {"token": "value"}
    assert tmp_flags, "atomic write never reopened its temp file"
    # mkstemp's own O_RDWR|O_CREAT|O_EXCL open comes first; the LAST
    # .tmp open is the pre-replace fsync reopen.
    assert tmp_flags[-1] & os.O_RDWR, (
        "the fsync reopen must be writable — O_RDONLY fsync raises EBADF "
        "on Windows"
    )


def test_doctor_credentials_reports_disabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _cmd_doctor_credentials(repair=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == {
        "enabled": False,
        "findings": [],
    }
