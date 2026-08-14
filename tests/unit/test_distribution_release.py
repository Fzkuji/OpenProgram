from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _desktop_package() -> dict:
    return json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))


def test_desktop_targets_and_embedded_runtime_are_declared() -> None:
    package = _desktop_package()
    build = package["build"]
    mac_targets = {
        target if isinstance(target, str) else target["target"]
        for target in build["mac"]["target"]
    }
    linux_targets = {
        target if isinstance(target, str) else target["target"]
        for target in build["linux"]["target"]
    }
    assert {"dmg", "zip"} <= mac_targets
    assert "AppImage" in linux_targets
    assert {item["to"] for item in build["extraResources"]} >= {"runtime"}
    assert package["desktopName"] == "ai.openprogram.OpenProgram.desktop"
    assert build["linux"]["syncDesktopName"] is True


def test_core_agentic_functions_are_not_excluded_from_wheel() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["openprogram.programs.agentic_functions.*"]' not in pyproject


def test_packaged_worker_uses_isolated_embedded_python() -> None:
    helper = (ROOT / "desktop" / "packaged-runtime.js").read_text(encoding="utf-8")
    main = (ROOT / "desktop" / "main.js").read_text(encoding="utf-8")
    assert '"-I", "-B", "-m", "openprogram", "worker", "start"' in helper
    assert "process.resourcesPath" in main
    assert "app.getVersion()" in main
    packaged_branch = re.search(
        r"if \(app\.isPackaged\)(.*?)(?:\n\s*else|\n\s*})",
        main,
        re.DOTALL,
    )
    assert packaged_branch is not None
    assert 'start("openprogram"' not in packaged_branch.group(1)
    assert "/opt/miniconda3" not in main
    assert 'env.OPENPROGRAM_IMMUTABLE_RUNTIME = "1"' in main


def test_detached_worker_preserves_packaged_python_flags() -> None:
    from openprogram.worker.lifecycle import _detached_worker_command

    command = _detached_worker_command(
        SimpleNamespace(isolated=1, dont_write_bytecode=1)
    )
    assert command[1:] == [
        "-I",
        "-B",
        "-u",
        "-m",
        "openprogram",
        "worker",
        "run",
    ]


def test_linux_worker_process_probe_treats_zombie_as_stopped(monkeypatch) -> None:
    from openprogram.worker import lifecycle

    monkeypatch.setattr(lifecycle.sys, "platform", "linux")
    monkeypatch.setattr(
        lifecycle.Path,
        "read_text",
        lambda self, **kwargs: "123 (openprogram) Z 1 2 3",
    )
    assert lifecycle._process_alive(123) is False


def test_packaged_runtime_rejects_program_mutation(monkeypatch, capsys) -> None:
    from openprogram._cli_cmds.programs import _cmd_install, _cmd_uninstall

    monkeypatch.setenv("OPENPROGRAM_IMMUTABLE_RUNTIME", "1")
    with pytest.raises(SystemExit) as install_exit:
        _cmd_install("research")
    assert install_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out

    with pytest.raises(SystemExit) as uninstall_exit:
        _cmd_uninstall("research")
    assert uninstall_exit.value.code == 1
    assert "disabled in the packaged desktop runtime" in capsys.readouterr().out


def test_release_installer_is_versioned_and_source_free() -> None:
    installer = (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8")
    assert "UV_VERSION=" in installer
    assert "PYTHON_VERSION=" in installer
    assert 'openprogram==${OPENPROGRAM_VERSION}' in installer
    assert "OPENPROGRAM_WHEEL" in installer
    assert "--no-bin" in installer
    assert "--break-system-packages" in installer
    assert "git clone" not in installer
    assert "pip install -e" not in installer
    assert "npm" not in installer


def test_release_installer_cold_starts_before_switching_current() -> None:
    installer = (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8")
    start = installer.index('"$python_bin" -I -B -m openprogram worker start')
    health = installer.index("/healthz", start)
    stop = installer.index('"$python_bin" -I -B -m openprogram worker stop', health)
    switch = installer.index('mv -f "$next_link" "$runtime_root/current"', stop)
    assert start < health < stop < switch


def test_cli_exposes_distribution_version(capsys) -> None:
    from openprogram.cli import build_parser

    with pytest.raises(SystemExit) as version_exit:
        build_parser().parse_args(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.startswith("openprogram ")


def test_desktop_runtime_removes_absolute_python_aliases() -> None:
    staging = (ROOT / "scripts" / "prepare-desktop-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert 'readlink "$python_alias"' in staging
    assert 'unlink "$python_alias"' in staging


def test_native_release_workflow_has_platform_jobs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "macos-" in workflow
    assert "ubuntu-" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "cli-installer:" in workflow
    assert "scripts/prepare-desktop-runtime.sh" in workflow
    assert "scripts/verify-release-version.py" in workflow
    assert "scripts/create-release-manifest.py" in workflow
    assert "scripts/smoke-packaged-runtime.sh" in workflow
    assert "sha256" in workflow.lower()
    assert workflow.count("--publish never") == 2


def test_linux_smoke_workflow_is_runnable_without_release_credentials() -> None:
    workflow = (ROOT / ".github" / "workflows" / "linux-release-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "environment: release" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "scripts/smoke-packaged-runtime.sh linux" in workflow
    assert "scripts/install-release.sh" in workflow
    assert "--publish never" in workflow
    assert "debian:bullseye-slim" in workflow
    assert "--network none" in workflow


def test_distribution_workflows_use_node24_action_releases() -> None:
    workflows = [
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in ("release.yml", "linux-release-smoke.yml")
    ]
    for workflow in workflows:
        assert "actions/checkout@v7" in workflow
        assert "actions/setup-node@v7" in workflow
        assert "astral-sh/setup-uv@v10" in workflow
        assert "actions/upload-artifact@v7" in workflow
        assert "actions/download-artifact@v8" in workflow


def test_linux_packaged_smoke_launches_public_appimage_entry() -> None:
    smoke = (ROOT / "scripts" / "smoke-packaged-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert "APPIMAGE_EXTRACT_AND_RUN=1" in smoke
    assert '"$appimage"' in smoke
    assert "app_pid=$!" in smoke
    assert "python3 -c" not in smoke
    assert "StartupWMClass=ai.openprogram.OpenProgram" in smoke


def test_release_workflow_notarizes_the_distributed_dmg() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "xcrun notarytool submit" in workflow
    assert 'xcrun stapler staple "$dmg_path"' in workflow
    assert 'xcrun stapler validate "$dmg_path"' in workflow


def test_public_docs_follow_the_release_platform_policy() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "reference" / "cli.md",
        ROOT / "docs" / "reference" / "cli.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT
        / "docs"
        / "superpowers"
        / "specs"
        / "2026-08-11-framework-adoption-homepage-design.md",
    ]
    forbidden = [
        "Any platform",
        "任意平台",
        "Native macOS / Linux / Windows",
        "Cross-platform (macOS / Linux / Windows)",
        "跨平台（macOS / Linux / Windows）",
        "macOS/Linux/Windows",
    ]
    for path in public_docs:
        contents = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in contents, f"{path.relative_to(ROOT)}: {phrase}"


def test_release_manifest_records_hashes(tmp_path: Path) -> None:
    import subprocess

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "OpenProgram-0.6.1-mac-arm64.dmg").write_bytes(b"artifact")
    output = artifacts / "release-manifest.json"
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "create-release-manifest.py"),
            str(artifacts),
            "--version",
            "v0.6.1",
            "--output",
            str(output),
        ],
        check=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.1"
    assert manifest["files"][0]["sha256"] == (
        "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"
    )
