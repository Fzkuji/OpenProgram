from __future__ import annotations

from pathlib import Path
import os
import re
from types import SimpleNamespace
import subprocess
import sys
import textwrap

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


def test_python_cli_application_is_owned_by_apps_workspace() -> None:
    app = ROOT / "apps/cli/python/openprogram_cli"
    assert (app / "__init__.py").is_file()
    assert (app / "__main__.py").is_file()
    assert (app / "_impl/application.py").is_file()
    assert (app / "_impl/commands").is_dir()
    assert (app / "_impl/repl").is_dir()
    assert (app / "_impl/setup_sections").is_dir()

    compatibility_files = {
        path.removeprefix("openprogram/cli/")
        for path in subprocess.run(
            ["git", "ls-files", "--", "openprogram/cli"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    assert compatibility_files == {"README.md", "__init__.py", "__main__.py"}


def test_python_cli_canonical_entry_matches_the_compatibility_entry() -> None:
    import openprogram_cli
    import openprogram.cli as compatibility_cli

    assert openprogram_cli.main is compatibility_cli.main
    assert openprogram_cli.build_parser is compatibility_cli.build_parser


def test_python_cli_rejects_an_already_loaded_foreign_canonical_package(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "openprogram_cli"
    stale.mkdir()
    (stale / "__init__.py").write_text("STALE = True\n", encoding="utf-8")
    script = textwrap.dedent(
        f"""
        import sys
        import openprogram_cli as stale
        sys.path.insert(0, {str(ROOT)!r})
        try:
            import openprogram.cli
        except ImportError as exc:
            assert 'foreign openprogram_cli package' in str(exc)
        else:
            raise AssertionError('foreign package was accepted')
        assert sys.modules['openprogram_cli'] is stale
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_python_cli_process_detection_includes_every_module_entry(monkeypatch) -> None:
    import openprogram.cli as cli

    for parent in ("openprogram", "cli", "openprogram_cli"):
        monkeypatch.setattr(sys, "argv", [f"/tmp/{parent}/__main__.py"])
        assert cli._is_cli_process()


def test_python_cli_resolves_the_apps_cli_bundle(tmp_path, monkeypatch) -> None:
    package_init = tmp_path / "openprogram" / "__init__.py"
    bundle = tmp_path / "apps" / "cli" / "dist" / "index.js"
    package_init.parent.mkdir(parents=True)
    bundle.parent.mkdir(parents=True)
    package_init.touch()
    bundle.touch()
    monkeypatch.setattr(openprogram, "__file__", str(package_init))

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
        "npm install && npm run build --workspace apps/web"
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


def test_server_transport_implementation_is_owned_by_apps_workspace() -> None:
    implementation = ROOT / "apps/server/openprogram_server/_webui"

    assert (implementation / "routes/tree.py").is_file()
    assert (implementation / "ws_actions/webtab.py").is_file()
    assert (implementation / "frontend.py").is_file()
    assert (implementation / "owner_auth.py").is_file()


def test_legacy_server_transport_imports_load_the_apps_sources() -> None:
    from openprogram.webui import frontend, owner_auth
    from openprogram.webui.routes import tree
    from openprogram.webui.ws_actions import webtab

    implementation = ROOT / "apps/server/openprogram_server/_webui"
    for module in (frontend, owner_auth, tree, webtab):
        assert module.__file__ is not None
        assert Path(module.__file__).resolve().is_relative_to(implementation)


def test_removed_legacy_static_ui_does_not_return() -> None:
    assert not any(
        path.is_file() for path in (ROOT / "openprogram/webui/static").rglob("*")
    )
    assert (ROOT / "apps/web/app").is_dir()


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


def test_legacy_server_rejects_an_already_loaded_foreign_package(tmp_path) -> None:
    stale_package = tmp_path / "openprogram_server"
    stale_package.mkdir()
    (stale_package / "__init__.py").write_text("", encoding="utf-8")
    (stale_package / "server.py").write_text("STALE = True\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    code = """
import sys
import openprogram_server.server as stale
try:
    import openprogram.webui.server
except ImportError as exc:
    assert 'already imported from a different location' in str(exc)
else:
    raise AssertionError('foreign package was silently replaced')
assert sys.modules['openprogram_server.server'] is stale
assert stale.STALE is True
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


def test_legacy_server_rejects_an_already_loaded_namespace_package(tmp_path) -> None:
    stale_package = tmp_path / "openprogram_server"
    stale_package.mkdir()
    (stale_package / "server.py").write_text("STALE = True\n", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    code = """
import importlib.machinery
import importlib.util
import sys
# Restrict namespace discovery to the fixture. A regular installed package
# otherwise takes precedence over namespace portions on PYTHONPATH.
spec = importlib.machinery.PathFinder.find_spec('openprogram_server', [sys.argv[1]])
assert spec is not None and spec.loader is None
parent = importlib.util.module_from_spec(spec)
sys.modules['openprogram_server'] = parent
import openprogram_server.server as stale
assert parent.__file__ is None
try:
    import openprogram.webui.server
except ImportError as exc:
    assert 'already imported from an unknown location' in str(exc)
else:
    raise AssertionError('foreign namespace package was silently replaced')
assert sys.modules['openprogram_server'] is parent
assert sys.modules['openprogram_server.server'] is stale
assert stale.STALE is True
"""

    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
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
        ".github/CONTRIBUTING.md",
        "apps/desktop/README.md",
        "scripts/docs_site/README.md",
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
