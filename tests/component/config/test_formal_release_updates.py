from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_immutable_runtime_takes_precedence_over_checkout(monkeypatch):
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    monkeypatch.setattr(detect, "repo_root", lambda: object())
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: True)

    assert detect.detect_install_method() is detect.InstallMethod.MANAGED_RELEASE


def test_source_checkout_is_not_a_managed_release(monkeypatch):
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    monkeypatch.setattr(detect, "managed_runtime_root", lambda: None)
    monkeypatch.setattr(detect, "repo_root", lambda: object())
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: False)

    assert detect.detect_install_method() is detect.InstallMethod.SOURCE_CHECKOUT


def test_wheel_inside_another_git_repo_is_not_a_source_checkout(
    monkeypatch, tmp_path
):
    from openprogram.operations.self_update.github_release import detect

    foreign_repo = tmp_path / "foreign"
    package = foreign_repo / ".venv" / "site-packages" / "openprogram"
    package.mkdir(parents=True)
    (foreign_repo / ".git").mkdir()
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    monkeypatch.setattr(detect, "managed_runtime_root", lambda: None)
    monkeypatch.setattr(detect, "package_root", lambda: package)
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: False)

    assert detect.repo_root() is None
    assert detect.detect_install_method() is detect.InstallMethod.UNKNOWN


def test_bundled_python_is_a_managed_release_without_environment_marker(
    monkeypatch, tmp_path
):
    from openprogram.operations.self_update.github_release import detect

    runtime = tmp_path / "runtime"
    python = runtime / "python" / "cpython" / "python.exe"
    package = python.parent / "Lib" / "site-packages" / "openprogram"
    package.mkdir(parents=True)
    python.write_bytes(b"")
    (runtime / "bin").mkdir()
    (runtime / "bin" / "verify-product-runtime.py").write_text(
        "# verifier\n", encoding="utf-8"
    )
    (runtime / "product-runtime.json").write_text("{}\n", encoding="utf-8")
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"schema": 2, "python": "python/cpython/python.exe"}),
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENPROGRAM_IMMUTABLE_RUNTIME", raising=False)
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(detect, "package_root", lambda: package)
    monkeypatch.setattr(detect, "is_pyinstaller_binary", lambda: False)
    monkeypatch.setattr(detect, "repo_root", lambda: None)

    assert detect.managed_runtime_root() == runtime
    assert detect.detect_install_method() is detect.InstallMethod.MANAGED_RELEASE


def test_unrelated_runtime_manifest_does_not_mark_a_wheel_managed(
    monkeypatch, tmp_path
):
    from openprogram.operations.self_update.github_release import detect

    runtime = tmp_path / "runtime"
    python = runtime / "python" / "python.exe"
    package = runtime / "site-packages" / "openprogram"
    package.mkdir(parents=True)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"schema": 2, "python": "python/python.exe"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr(detect, "package_root", lambda: package)

    assert detect.managed_runtime_root() is None


def test_worker_start_does_not_apply_product_updates():
    from pathlib import Path
    import openprogram.operations.worker.runner as runner

    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "background_check_and_apply" not in source


def test_release_wheel_probe_runs_outside_the_checkout():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'probe_dir="$(mktemp -d)"' in workflow
    assert '(cd "$probe_dir"' in workflow
    assert "python -P -c" in workflow
    assert "import openprogram.interfaces.server.frontend as frontend" in workflow
    assert "openprogram/interfaces/server/_frontend/index.html" in workflow
    assert "openprogram/interfaces/server/routes/files/tree.py" in workflow
    assert "openprogram/interfaces/server/ws_actions/webtab.py" in workflow
    assert "openprogram/interfaces/cli/application.py" in workflow
    assert 'openprogram.interfaces.cli.build_parser().prog == "openprogram"' in workflow
    assert "is_relative_to(checkout)" in workflow
    assert 'uv run --with "$wheel_path" --no-project -- openprogram --version' in workflow
    assert 'python -P -m openprogram --version' in workflow
    assert 'python -P -m openprogram.interfaces.cli --version' in workflow


def test_release_workflow_builds_and_signs_official_update_assets():
    workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "create-update-assets.py platform" in workflow
    assert "--previous" not in workflow, "every release carries its base, for machines that skipped some"
    trust = workflow[workflow.index("id: update-key") : workflow.index("name: Select release platforms")]
    assert "official/assets.py" in trust and "self-update/update-service.js" in trust
    assert "name: update-assets-${{ matrix.platform }}-${{ matrix.arch }}" in workflow
    assert "create-update-assets.py finalize" in workflow
    assert "secrets.OPENPROGRAM_UPDATE_SIGNING_KEY" in workflow
    sign = workflow.index("name: Sign official update assets")
    publish = workflow.index("name: Publish GitHub Release")
    manifest = workflow.index("name: Create release manifest")
    assert sign < manifest < publish
    move = workflow.index('mv "$directory"/* "$RUNNER_TEMP/official-update/"')
    guard = workflow.index('if [ -z "$UPDATE_SIGNING_KEY" ]; then')
    assert move < guard, "unsigned update assets must leave release-artifacts before the guard"


def test_upgrade_docs_describe_replay_of_own_commits():
    for name, phrase in (
        ("docs/install/upgrade.html", "unreplayable/<release>/<commit>.patch"),
        ("docs/install/upgrade.zh.html", "unreplayable/<release>/<commit>.patch"),
    ):
        assert phrase in html.unescape(Path(name).read_text(encoding="utf-8"))
    for name in (
        "docs/reference/design/distribution/automatic-updates.html",
        "docs/reference/design/distribution/automatic-updates.zh.html",
    ):
        assert 'id="official-replay"' in Path(name).read_text(encoding="utf-8")


def test_system_version_reports_managed_release(monkeypatch):
    from openprogram.operations.self_update.github_release.detect import InstallMethod
    from openprogram.interfaces.server.routes.settings.config import register

    monkeypatch.setattr(
        "openprogram.operations.self_update.github_release.detect.detect_install_method",
        lambda: InstallMethod.MANAGED_RELEASE,
    )
    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda package: "0.6.7" if package == "openprogram" else "unexpected",
    )
    app = FastAPI()
    register(app)

    response = TestClient(app).get("/api/system/version")

    assert response.status_code == 200
    assert response.json() == {
        "currentVersion": "0.6.7",
        "installType": "managed_release",
        "updateRoute": "command_line_release",
        "latestVersion": None,
        "updateAvailable": False,
        "latestCheckedAt": None,
    }


def test_automatic_update_design_is_one_accessible_review_page():
    from scripts.docs_site.nav import build_tabs, discover

    design = Path(
        "docs/reference/design/distribution/automatic-updates.html"
    ).read_text(encoding="utf-8")

    for contract in (
        'id="architecture"',
        '<title>Stable release automatic updates</title>',
        'role="img"',
        'id="desktop-states"',
        'id="trust-boundaries"',
        'id="implementation-evidence"',
        'https://github.com/FuzzyKuji/OpenProgram/blob/main/apps/desktop/main/self-update/update-service.js',
        'https://github.com/FuzzyKuji/OpenProgram/blob/main/openprogram/interfaces/cli/commands/upgrade.py',
    ):
        assert contract in design

    assert not Path(
        "docs/reference/design/plans/2026-08-15-formal-release-updates.md"
    ).exists()

    source_design = Path(
        "docs/server/upgrading.html"
    ).read_text(encoding="utf-8")
    source_contract = " ".join(re.sub(r"<[^>]+>", "", source_design).split())
    faq = " ".join(re.sub(r"<[^>]+>", "", Path("docs/start/faq.html").read_text(encoding="utf-8")).split())
    assert '<a href="../install/upgrade.html">Upgrade</a>' in source_design
    assert "stable, which here tracks origin/main" in source_contract
    assert "It is not the stable GitHub Release" in source_contract
    assert "For Desktop and managed releases" in source_contract
    assert "Stable installations move only to a published version" in faq
    assert "openprogram upgrade, which picks the path from the installation type" in faq
    assert "An explicit --channel is still persisted" in source_contract
    assert "No automatic rollback; run the printed command" in source_contract
    assert 'class="diagram-mobile" aria-hidden' not in design

    design_index = Path("docs/reference/design/README.html").read_text(encoding="utf-8")
    design_index_zh = Path("docs/reference/design/README.zh.html").read_text(
        encoding="utf-8"
    )
    assert 'href="distribution/automatic-updates.html"' in design_index
    assert 'href="distribution/automatic-updates.zh.html"' in design_index_zh

    docs_root = Path("docs")
    design_tab = next(
        tab for tab in build_tabs(docs_root, discover(docs_root))
        if tab.key == "design"
    )
    assert design_tab.landing == Path("reference/design/README.html")
    for section_title, relative_path in (
        ("Distribution", "reference/design/distribution/automatic-updates.html"),
        ("Programs and workflows", "reference/design/runtime/self-update.html"),
    ):
        section = next(
            section for section in design_tab.sections
            if section.title == section_title
        )
        page = next(page for page in section.pages if page.rel == Path(relative_path))
        assert page.src.is_file()
        assert page.out == Path(relative_path)
        assert page.zh_src is not None and page.zh_src.is_file()
        assert page.zh_out == Path(relative_path).with_suffix(".zh.html")


def test_upgrade_help_discloses_channel_persistence_during_read_only_actions():
    result = subprocess.run(
        [sys.executable, "-m", "openprogram.interfaces.cli", "upgrade", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    status_result = subprocess.run(
        [sys.executable, "-m", "openprogram.interfaces.cli", "upgrade", "status", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    help_text = " ".join(result.stdout.split())

    assert "A source checkout still persists an explicit --channel." in help_text
    assert "Read-only; source checkouts persist an explicit --channel." in help_text
    assert "For a source checkout, report against and persist" in status_result.stdout


def _latest_release(version="0.6.7"):
    runtime = f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz"
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "release-manifest.json", "size": 500},
            {"name": runtime, "size": 100},
            {"name": f"{runtime}.sha256", "size": 64},
        ],
    }


def _release_manifest(version="0.6.7"):
    runtime = f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz"
    return {
        "schema": 1,
        "version": version,
        "files": [
            {"path": f"runtime/{runtime}", "bytes": 100, "sha256": "a" * 64},
            {"path": f"runtime/{runtime}.sha256", "bytes": 64, "sha256": "b" * 64},
        ],
    }


def test_managed_release_check_is_read_only(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up

    monkeypatch.setattr(up, "_installed_version", lambda: "0.6.6")
    monkeypatch.setattr(up, "_platform_runtime_names", lambda version: (
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz",
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz.sha256",
    ))
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.latest_release", _latest_release)
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.release_manifest", _release_manifest)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("check must not install")))

    assert up.run_managed_release_upgrade(check_only=True, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current_version"] == "0.6.6"
    assert payload["latest_version"] == "0.6.7"
    assert payload["update_available"] is True


def test_managed_release_dry_run_never_reads_or_executes_installer(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up

    monkeypatch.setattr(up, "_managed_release_status", lambda: {
        "current_version": "0.6.6",
        "latest_version": "0.6.7",
        "update_available": True,
        "archive": "OpenProgram-0.6.7-runtime-linux-x86_64.tar.gz",
    })
    monkeypatch.setattr(
        "openprogram.operations.self_update.github_release.github.release_installer",
        lambda *_args: (_ for _ in ()).throw(AssertionError("dry-run must not read installer")),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run must not execute")),
    )

    assert up.run_managed_release_upgrade(
        check_only=False,
        as_json=True,
        dry_run=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["planned"][-1] == "activate-current"


def test_managed_release_upgrade_sanitizes_installer_environment(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up

    monkeypatch.setenv("OPENPROGRAM_REPOSITORY", "attacker/repo")
    monkeypatch.setenv("OPENPROGRAM_RUNTIME_ARCHIVE", "/tmp/untrusted.tar.gz")
    monkeypatch.setenv("OPENPROGRAM_RUNTIME_SHA256", "bad")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-installer")
    monkeypatch.setattr(up, "_installed_version", lambda: "0.6.6")
    monkeypatch.setattr(up, "_platform_runtime_names", lambda version: (
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz",
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz.sha256",
    ))
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.latest_release", _latest_release)
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.release_manifest", _release_manifest)
    monkeypatch.setattr(
        "openprogram.operations.self_update.github_release.github.release_installer",
        lambda version: b"#!/bin/sh\nexit 0\n",
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "openprogram.operations.worker.restart_worker",
        lambda: (_ for _ in ()).throw(AssertionError("managed upgrade must not restart worker")),
        raising=False,
    )

    assert up.run_managed_release_upgrade(check_only=False, as_json=False) == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == "sh"
    assert kwargs["env"]["OPENPROGRAM_REPOSITORY"] == "FuzzyKuji/OpenProgram"
    assert kwargs["env"]["OPENPROGRAM_VERSION"] == "0.6.7"
    assert "OPENPROGRAM_RUNTIME_ARCHIVE" not in kwargs["env"]
    assert "OPENPROGRAM_RUNTIME_SHA256" not in kwargs["env"]
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "openprogram worker restart" in capsys.readouterr().out


def test_managed_release_rejects_missing_manifest_asset(monkeypatch):
    from openprogram.interfaces.cli.commands import upgrade as up

    release = _latest_release()
    release["assets"] = release["assets"][1:]
    monkeypatch.setattr(up, "_installed_version", lambda: "0.6.6")
    monkeypatch.setattr(up, "_platform_runtime_names", lambda version: (
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz",
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz.sha256",
    ))
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.latest_release", lambda: release)

    assert up.run_managed_release_upgrade(check_only=True, as_json=True) == 1


def test_managed_release_rejects_duplicate_assets(monkeypatch):
    from openprogram.interfaces.cli.commands import upgrade as up

    release = _latest_release()
    release["assets"].append(dict(release["assets"][1]))
    monkeypatch.setattr(up, "_installed_version", lambda: "0.6.6")
    monkeypatch.setattr(up, "_platform_runtime_names", lambda version: (
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz",
        f"OpenProgram-{version}-runtime-linux-x86_64.tar.gz.sha256",
    ))
    monkeypatch.setattr("openprogram.operations.self_update.github_release.github.latest_release", lambda: release)

    assert up.run_managed_release_upgrade(check_only=True, as_json=True) == 1


def test_cmd_managed_dry_run_is_forwarded(monkeypatch):
    from openprogram.interfaces.cli.commands import upgrade as up
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(
        detect,
        "detect_install_method",
        lambda: detect.InstallMethod.MANAGED_RELEASE,
    )
    received = {}
    monkeypatch.setattr(
        up,
        "run_managed_release_upgrade",
        lambda **kwargs: received.update(kwargs) or 0,
    )

    assert up._cmd_upgrade(SimpleNamespace(
        channel=None,
        check=False,
        upgrade_verb=None,
        json=False,
        dry_run=True,
    )) == 0
    assert received == {"check_only": False, "as_json": False, "dry_run": True}


def test_cmd_managed_errors_remain_json(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(
        detect,
        "detect_install_method",
        lambda: detect.InstallMethod.MANAGED_RELEASE,
    )
    assert up._cmd_upgrade(SimpleNamespace(
        channel="beta",
        check=False,
        upgrade_verb=None,
        json=True,
        dry_run=False,
    )) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unknown-channel"


def test_cmd_unknown_install_error_remains_json(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(
        detect,
        "detect_install_method",
        lambda: detect.InstallMethod.UNKNOWN,
    )
    assert up._cmd_upgrade(SimpleNamespace(
        channel=None,
        check=False,
        upgrade_verb=None,
        json=True,
        dry_run=False,
    )) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unknown-install"


def test_managed_release_json_is_a_single_document(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up

    monkeypatch.setattr(up, "_managed_release_status", lambda: {
        "current_version": "0.6.6",
        "latest_version": "0.6.7",
        "update_available": True,
        "archive": "OpenProgram-0.6.7-runtime-linux-x86_64.tar.gz",
    })
    monkeypatch.setattr(
        "openprogram.operations.self_update.github_release.github.release_installer",
        lambda _version: b"#!/usr/bin/env sh\nprintf 'installer output\\n'\n",
    )

    assert up.run_managed_release_upgrade(check_only=False, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["activated"] == "0.6.7"


def test_managed_release_execution_error_remains_json(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up

    monkeypatch.setattr(up, "_managed_release_status", lambda: {
        "current_version": "0.6.6",
        "latest_version": "0.6.7",
        "update_available": True,
        "archive": "OpenProgram-0.6.7-runtime-linux-x86_64.tar.gz",
    })
    monkeypatch.setattr(
        "openprogram.operations.self_update.github_release.github.release_installer",
        lambda _version: b"#!/usr/bin/env sh\nexit 0\n",
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )

    assert up.run_managed_release_upgrade(check_only=False, as_json=True) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "installer-execution-failed"


def test_source_unknown_channel_error_remains_json(monkeypatch, capsys):
    from openprogram.interfaces.cli.commands import upgrade as up
    from openprogram.operations.self_update.github_release import detect

    monkeypatch.setattr(
        detect,
        "detect_install_method",
        lambda: detect.InstallMethod.SOURCE_CHECKOUT,
    )

    assert up._cmd_upgrade(SimpleNamespace(
        channel="missing",
        check=False,
        upgrade_verb=None,
        json=True,
        dry_run=False,
    )) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "unknown-channel"


def test_release_installer_reads_only_the_immutable_tag(monkeypatch):
    from openprogram.operations.self_update.github_release import github

    installer = b"#!/usr/bin/env sh\nset -eu\n"
    seen = []
    monkeypatch.setattr(
        github,
        "_curl_release_bytes",
        lambda url: seen.append(url) or installer,
    )

    assert github.release_installer("0.6.7") == installer
    assert seen == [
        "https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/v0.6.7/scripts/install-release.sh"
    ]


def test_release_download_validates_each_redirect_before_request(monkeypatch):
    from openprogram.operations.self_update.github_release import github

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"")
        return subprocess.CompletedProcess(
            command,
            0,
            b"HTTP/1.1 302 Found\r\nLocation: https://example.com/file\r\n\r\n",
            b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert github._curl_release_bytes(
        "https://raw.githubusercontent.com/FuzzyKuji/OpenProgram/v0.6.7/scripts/install-release.sh"
    ) is None
    assert len(calls) == 1
    assert calls[0][1] == "--disable"
    assert "--location" not in calls[0]


def test_release_download_accepts_only_allowed_redirect_chain(monkeypatch):
    from openprogram.operations.self_update.github_release import github

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        if len(calls) == 1:
            output.write_bytes(b"")
            stdout = (
                b"HTTP/1.1 302 Found\r\n"
                b"Location: https://release-assets.githubusercontent.com/file\r\n\r\n"
            )
        else:
            output.write_bytes(b'{"schema": 1}')
            stdout = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
        return subprocess.CompletedProcess(command, 0, stdout, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert github._curl_release_bytes(
        "https://github.com/FuzzyKuji/OpenProgram/releases/download/v0.6.7/release-manifest.json"
    ) == b'{"schema": 1}'
    assert len(calls) == 2
