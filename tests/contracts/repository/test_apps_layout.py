from __future__ import annotations

from pathlib import Path
import re
from types import SimpleNamespace
import subprocess
import sys

import openprogram

from openprogram.interfaces.cli import ink
from openprogram.interfaces.cli.commands import rescue
from openprogram.operations.worker import web as worker_web


ROOT = Path(__file__).resolve().parents[3]


def test_ink_cli_is_owned_by_apps_workspace() -> None:
    assert (ROOT / "apps/cli/package.json").is_file()
    assert (ROOT / "apps/cli/src/index.tsx").is_file()
    assert not (ROOT / "cli/package.json").exists()


def test_ink_cold_build_always_verifies_root_workspace(tmp_path, monkeypatch) -> None:
    cli_dir = tmp_path / "apps" / "cli"
    cli_dir.mkdir(parents=True)
    (tmp_path / "node_modules").mkdir()
    calls: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(ink.shutil, "which", lambda _name: "/usr/bin/npm")
    monkeypatch.setattr(ink, "_tty_write", lambda _message: None)

    def fake_run(command, *, cwd, **_kwargs):
        calls.append((command, Path(cwd)))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ink.subprocess, "run", fake_run)
    ink._build_ink_bundle(cli_dir, cli_dir / "dist" / "index.js")

    assert calls[0][0][1:3] == ["install", "--no-audit"]
    assert calls[0][1] == tmp_path
    assert calls[1][0][-3:] == ["build", "--workspace", "apps/cli"]
    assert calls[1][1] == tmp_path


def test_python_cli_application_lives_in_the_core_package() -> None:
    app = ROOT / "openprogram/interfaces/cli"
    assert (app / "__init__.py").is_file()
    assert (app / "__main__.py").is_file()
    assert (app / "application.py").is_file()
    assert (app / "commands").is_dir()
    assert (app / "repl").is_dir()
    assert (app / "setup_sections").is_dir()


def test_apps_holds_only_the_non_python_clients() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "apps"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert {path.split("/")[1] for path in tracked} == {"cli", "desktop", "web"}
    assert [path for path in tracked if path.endswith(".py")] == []


def test_core_package_has_no_server_or_cli_compatibility_namespace() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "openprogram/cli", "openprogram/webui"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert tracked == []
    for directory in ("cli", "webui"):
        assert not (ROOT / "openprogram" / directory / "__init__.py").exists()


def test_every_python_cli_entry_runs_the_application_main() -> None:
    import openprogram.interfaces.cli
    from openprogram.interfaces.cli import application

    assert openprogram.interfaces.cli.main is application.main
    assert openprogram.interfaces.cli.build_parser is application.build_parser
    for entry in ("openprogram/__main__.py", "openprogram/interfaces/cli/__main__.py"):
        source = (ROOT / entry).read_text(encoding="utf-8")
        assert re.search(r"^from openprogram.interfaces.cli(\.application)? import main$", source, re.M)


def test_python_cli_process_detection_includes_every_module_entry(monkeypatch) -> None:
    from openprogram.interfaces.cli import application as cli

    for parent in ("openprogram", "openprogram/interfaces/cli"):
        monkeypatch.setattr(sys, "argv", [f"/tmp/{parent}/__main__.py"])
        assert cli._is_cli_process()
    monkeypatch.setattr(sys, "argv", ["/tmp/python/__main__.py"])
    assert not cli._is_cli_process()


def test_python_cli_resolves_the_apps_cli_bundle(tmp_path, monkeypatch) -> None:
    package_init = tmp_path / "openprogram" / "__init__.py"
    bundle = tmp_path / "apps" / "cli" / "dist" / "index.js"
    package_init.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    package_init.touch()
    bundle.touch()
    monkeypatch.setattr(openprogram, "__file__", str(package_init))

    assert ink._resolve_cli_entry() == bundle


def _source_checkout(tmp_path, monkeypatch) -> None:
    package_init = tmp_path / "openprogram" / "__init__.py"
    package_init.parent.mkdir(parents=True)
    package_init.touch()
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "package.json").touch()
    monkeypatch.setattr(openprogram, "__file__", str(package_init))
    monkeypatch.setattr(ink, "packaged_tui", lambda: None)


def test_rescue_probe_resolves_the_apps_cli_bundle(tmp_path, monkeypatch) -> None:
    _source_checkout(tmp_path, monkeypatch)
    bundle = tmp_path / "apps" / "cli" / "dist" / "index.js"
    bundle.parent.mkdir(parents=True)
    bundle.touch()

    finding = rescue._probe_tui_bundle()

    assert finding.level == "OK"
    assert finding.detail == str(bundle)


def test_rescue_probe_reports_the_apps_web_export_path(tmp_path, monkeypatch) -> None:
    from openprogram.interfaces.server import frontend

    _source_checkout(tmp_path, monkeypatch)
    out = tmp_path / "apps" / "web" / "out"
    monkeypatch.setattr(frontend, "out_dir", lambda: out)

    finding = rescue._probe_web_bundle()

    assert finding.level == "WARN"
    assert str(out) in finding.detail
    assert finding.fix == (
        "Built when the worker starts. "
        "Or build it now: npm install && npm run build --workspace apps/web"
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


def test_server_application_assembly_lives_in_the_core_package() -> None:
    from openprogram.interfaces.server import server

    assert server.__file__ is not None
    assert Path(server.__file__).resolve() == (
        ROOT / "openprogram/interfaces/server/server.py"
    )


def test_server_transport_implementation_lives_in_the_core_package() -> None:
    implementation = ROOT / "openprogram/interfaces/server"

    assert (implementation / "routes/files/tree.py").is_file()
    assert (implementation / "ws_actions/webtab.py").is_file()
    assert (implementation / "frontend.py").is_file()
    assert (implementation / "owner_auth.py").is_file()


def test_server_transport_imports_load_the_core_package_sources() -> None:
    from openprogram.interfaces.server import frontend, owner_auth
    from openprogram.interfaces.server.routes.files import tree
    from openprogram.interfaces.server.ws_actions import webtab

    implementation = ROOT / "openprogram/interfaces/server"
    for module in (frontend, owner_auth, tree, webtab):
        assert module.__file__ is not None
        assert Path(module.__file__).resolve().is_relative_to(implementation)


def test_mutable_profile_state_is_not_tracked_in_the_core_package() -> None:
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "--", "openprogram/webui"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    )
    for relative in (
        "openprogram/webui/functions_meta.json",
        "openprogram/webui/programs_meta.json",
    ):
        assert relative not in tracked
        subprocess.run(
            ["git", "check-ignore", "--no-index", "--", relative],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )


def test_current_developer_commands_use_apps_workspaces() -> None:
    current_docs = (
        "AGENTS.md",
        ".github/CONTRIBUTING.md",
        "apps/desktop/README.md",
        "scripts/docs_site/README.md",
        "docs/reference/design/ui/browser/browser-extensions.html",
        "docs/reference/design/ui/visual-system/theme-system.html",
        "docs/reference/design/ui/workspace/center-tabs-and-split-layout.html",
        "docs/reference/design/ui/turn-lifecycle/send-queue-reliability.html",
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
