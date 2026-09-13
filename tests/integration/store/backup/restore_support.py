"""Archive and state helpers for restore tests."""
from __future__ import annotations
import io
import json
import os
import tarfile
import time
from pathlib import Path


def _state(tmp_path: Path) -> Path:
    root = tmp_path / "state"
    root.mkdir()
    os.chmod(root, 0o700)
    return root


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _archive(tmp_path: Path, members: dict[str, bytes], *, manifest: bytes | None = None) -> Path:
    from openprogram.cli.commands.backup import _MANIFEST_NAME

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "archive.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(payload))
        body = manifest if manifest is not None else (
            json.dumps({"format_version": 1, "credential_opt_in": False}).encode()
        )
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(body)
        info.mode = 0o600
        tar.addfile(info, io.BytesIO(body))
    return path
