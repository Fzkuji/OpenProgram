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


def test_mcp_server_lives_inside_the_mcp_domain() -> None:
    tracked = set(_tracked_paths())

    assert {
        "openprogram/mcp/server/__init__.py",
        "openprogram/mcp/server/contracts.py",
        "openprogram/mcp/server/server.py",
        "openprogram/mcp/server/service.py",
    } <= tracked
    assert not any(path.startswith("openprogram/mcp_server/") for path in tracked)


def test_repository_does_not_import_the_removed_mcp_server_package() -> None:
    stale_imports = []
    for relative in _tracked_paths():
        if relative == "tests/contracts/repository/test_mcp_package_layout.py":
            continue
        if relative.startswith("docs/_site/"):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".md", ".html", ".disabled"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "openprogram.mcp_server" in text or "mcp_server/" in text:
            stale_imports.append(relative)

    assert stale_imports == []
