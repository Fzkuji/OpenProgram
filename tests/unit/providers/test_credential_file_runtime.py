from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openprogram.cli.commands.doctor import _cmd_doctor_credentials
from openprogram.credential_files import (
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


def test_doctor_credentials_reports_disabled(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _cmd_doctor_credentials(repair=True, as_json=True) == 0
    assert json.loads(capsys.readouterr().out) == {
        "enabled": False,
        "findings": [],
    }
