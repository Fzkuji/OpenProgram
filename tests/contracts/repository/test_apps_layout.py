from __future__ import annotations

from pathlib import Path
import re

import openprogram

from openprogram.cli import ink
from openprogram.cli.commands import rescue
from openprogram.worker import web as worker_web


ROOT = Path(__file__).resolve().parents[3]


def test_ink_cli_is_owned_by_apps_workspace() -> None:
    assert (ROOT / "apps/cli/package.json").is_file()
    assert (ROOT / "apps/cli/src/index.tsx").is_file()
    assert not (ROOT / "cli/package.json").exists()


def test_python_cli_resolves_the_apps_cli_bundle(tmp_path, monkeypatch) -> None:
    launcher_path = tmp_path / "openprogram" / "cli" / "ink.py"
    bundle = tmp_path / "apps" / "cli" / "dist" / "index.js"
    launcher_path.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    launcher_path.touch()
    bundle.touch()
    monkeypatch.setattr(ink, "__file__", str(launcher_path))

    assert ink._resolve_cli_entry() == bundle


def test_rescue_probe_resolves_the_apps_cli_bundle(tmp_path, monkeypatch) -> None:
    package_init = tmp_path / "openprogram" / "__init__.py"
    bundle = tmp_path / "apps" / "cli" / "dist" / "index.js"
    package_init.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    package_init.touch()
    bundle.touch()
    monkeypatch.setattr(openprogram, "__file__", str(package_init))

    finding = rescue._probe_tui_bundle()

    assert finding.level == "OK"
    assert finding.detail == str(bundle)


def test_rescue_probe_reports_the_apps_web_bundle_path(tmp_path, monkeypatch) -> None:
    package_init = tmp_path / "openprogram" / "__init__.py"
    package_init.parent.mkdir(parents=True)
    package_init.touch()
    monkeypatch.setattr(openprogram, "__file__", str(package_init))

    finding = rescue._probe_web_bundle()

    assert finding.level == "WARN"
    assert str(tmp_path / "apps" / "web" / ".next") in finding.detail
    assert finding.fix == (
        "Auto-built on first `openprogram web` launch. Or manually: "
        "npm --prefix apps/web install && npm --prefix apps/web run build"
    )


def test_web_frontend_is_owned_by_apps_workspace() -> None:
    assert (ROOT / "apps/web/package.json").is_file()
    assert (ROOT / "apps/web/app").is_dir()
    assert not (ROOT / "web/package.json").exists()


def test_worker_resolves_the_apps_web_workspace() -> None:
    assert worker_web.web_dir() == ROOT / "apps/web"


def test_desktop_is_owned_by_apps_workspace() -> None:
    assert (ROOT / "apps/desktop/package.json").is_file()
    assert (ROOT / "apps/desktop/main.js").is_file()
    assert not (ROOT / "desktop/package.json").exists()


def test_current_developer_commands_use_apps_workspaces() -> None:
    current_docs = (
        "AGENTS.md",
        "CONTRIBUTING.md",
        "apps/desktop/README.md",
        "tools/docs_site/README.md",
        "docs/reference/design/integrations/web-use-implementation.html",
        "docs/reference/design/ui/browser-extensions.html",
        "docs/reference/design/ui/theme-system.html",
        "docs/reference/design/ui/center-tabs-and-split-layout.html",
        "docs/reference/design/distribution/installation-packaging.html",
    )
    removed_commands = (
        r"npm --prefix (?:web|desktop)\s",
        r"cd (?:web|desktop)\s",
        r"(?<!apps/)web/node_modules/\.bin/tsc",
        r"(?<!apps/)desktop/node_modules/electron",
    )

    stale = [
        f"{relative}: {pattern}"
        for relative in current_docs
        for pattern in removed_commands
        if re.search(pattern, (ROOT / relative).read_text(encoding="utf-8"))
    ]

    assert stale == []
