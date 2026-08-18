from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]


def _tracked_paths() -> tuple[str, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(raw.decode() for raw in result.stdout.split(b"\0") if raw)


def test_credential_file_helpers_live_inside_auth() -> None:
    tracked = set(_tracked_paths())

    assert {
        "packages/core/src/openprogram/auth/credentials/__init__.py",
        "packages/core/src/openprogram/auth/credentials/inventory.py",
        "packages/core/src/openprogram/auth/credentials/io.py",
    } <= tracked
    assert not any(
        path.startswith("packages/core/src/openprogram/credential_files/")
        for path in tracked
    )


def test_repository_does_not_import_the_removed_credential_files_package() -> None:
    stale_imports = []
    for relative in _tracked_paths():
        if relative == "tests/contracts/repository/test_auth_credentials_layout.py":
            continue
        path = ROOT / relative
        if path.suffix not in {".py", ".disabled"}:
            continue
        text = path.read_text(encoding="utf-8")
        if (
            "openprogram.credential_files" in text
            or "from openprogram import credential_files" in text
        ):
            stale_imports.append(relative)

    assert stale_imports == []
