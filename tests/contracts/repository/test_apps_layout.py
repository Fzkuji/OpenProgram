from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_ink_cli_is_owned_by_apps_workspace() -> None:
    assert (ROOT / "apps/cli/package.json").is_file()
    assert (ROOT / "apps/cli/src/index.tsx").is_file()
    assert not (ROOT / "cli/package.json").exists()


def test_python_cli_resolves_the_apps_cli_bundle() -> None:
    launcher = (ROOT / "openprogram/cli/ink.py").read_text(encoding="utf-8")

    assert 'project_root / "apps" / "cli"' in launcher
    assert 'project_root / "cli"' not in launcher
