from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import sys

import openprogram

from openprogram.cli import ink
from openprogram.cli.commands import rescue
from openprogram.cli.commands import web as cli_web
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


def test_web_command_reports_the_apps_workspace_when_node_is_missing(
    tmp_path, monkeypatch, capsys
) -> None:
    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    monkeypatch.delenv("OPENPROGRAM_WEB_NO_FRONTEND", raising=False)
    monkeypatch.setattr(cli_web, "_find_web_dir", lambda: web)
    monkeypatch.setattr(cli_web, "_port_in_use", lambda _port: False)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert cli_web._start_frontend(backend_port=18100, web_port=18101) is None

    output = capsys.readouterr().out
    assert "npm --prefix apps/web run dev" in output
    assert "cd web" not in output


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


def test_server_application_assembly_is_owned_by_apps_workspace() -> None:
    from openprogram_server import server as canonical_server
    from openprogram.webui import server as compatibility_server

    assert canonical_server is compatibility_server
    assert canonical_server.__file__ is not None
    assert Path(canonical_server.__file__).resolve() == (
        ROOT / "apps/server/openprogram_server/server.py"
    )
    assert (ROOT / "openprogram/webui/server.py").read_text(
        encoding="utf-8"
    ).count("sys.modules[__name__] = _server") == 1


def test_source_checkout_server_wins_over_an_older_installed_package(
    tmp_path,
) -> None:
    stale_package = tmp_path / "openprogram_server"
    stale_package.mkdir()
    (stale_package / "__init__.py").write_text("", encoding="utf-8")
    (stale_package / "server.py").write_text("STALE = True\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    code = """
import pathlib
import openprogram_server.server as stale
assert stale.STALE is True
import openprogram.webui.server as legacy
import openprogram_server.server as canonical
assert canonical is legacy
assert not hasattr(canonical, 'STALE')
assert pathlib.Path(canonical.__file__).resolve() == pathlib.Path(
    'apps/server/openprogram_server/server.py'
).resolve()
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


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
        "docs/reference/design/ui/send-queue-reliability.html",
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
