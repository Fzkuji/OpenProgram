from __future__ import annotations

import json
import os
import re
import subprocess
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
    assert {"dmg", "zip"} <= mac_targets
    assert "linux" not in build
    assert "dist:linux" not in package["scripts"]
    assert {item["to"] for item in build["extraResources"]} >= {"runtime"}
    assert package["desktopName"] == "ai.openprogram.OpenProgram.desktop"


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
    assert "OPENPROGRAM_RUNTIME_ARCHIVE" in installer
    assert "runtime-${platform}-${arch}.tar.gz" in installer
    assert "runtime-manifest.json" in installer
    assert "verify-product-runtime.py" in installer
    assert "OPENPROGRAM_WHEEL" not in installer
    assert "pypi" not in installer.lower()
    assert "pip install" not in installer
    assert "git clone" not in installer
    assert "pip install -e" not in installer
    assert "npm" not in installer


def test_release_installer_cold_starts_before_switching_current() -> None:
    installer = (ROOT / "scripts" / "install-release.sh").read_text(encoding="utf-8")
    assert 'probe_home="$release_dir/.probe-home-$$"' in installer
    assert 'HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port"' in installer
    start = installer.index('"$python_bin" -I -B -m openprogram worker start')
    health = installer.index("/healthz", start)
    stop = installer.index('"$python_bin" -I -B -m openprogram worker stop', health)
    switch = installer.index('mv -f "$next_link" "$runtime_root/current"', stop)
    assert start < health < stop < switch


def test_short_public_installer_resolves_latest_and_accepts_a_pin(
    tmp_path: Path,
) -> None:
    bootstrap = ROOT / "docs" / "_static_root" / "install.sh"
    assert bootstrap.is_file()

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_installer = tmp_path / "tagged-installer.sh"
    fake_installer.write_text(
        "#!/bin/sh\n"
        "printf '%s|%s\\n' \"$OPENPROGRAM_VERSION\" "
        '"$OPENPROGRAM_REPOSITORY" > "$FAKE_RESULT"\n',
        encoding="utf-8",
    )
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        """#!/bin/sh
set -eu
output=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) output="$2"; shift 2 ;;
    -w) shift 2 ;;
    https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
printf '%s\n' "$url" >> "$FAKE_CURL_LOG"
case "$url" in
  */releases/latest)
    printf 'https://github.com/Fzkuji/OpenProgram/releases/tag/v0.6.1'
    ;;
  */v*/scripts/install-release.sh)
    cp "$FAKE_INSTALLER" "$output"
    ;;
  *)
    printf 'unexpected URL: %s\n' "$url" >&2
    exit 1
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    result = tmp_path / "result"
    curl_log = tmp_path / "curl.log"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "FAKE_INSTALLER": str(fake_installer),
        "FAKE_RESULT": str(result),
        "FAKE_CURL_LOG": str(curl_log),
    }
    subprocess.run(["sh", str(bootstrap)], check=True, env=env)
    assert result.read_text(encoding="utf-8") == "0.6.1|Fzkuji/OpenProgram\n"
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://github.com/Fzkuji/OpenProgram/releases/latest",
        "https://raw.githubusercontent.com/Fzkuji/OpenProgram/v0.6.1/scripts/install-release.sh",
    ]

    result.unlink()
    curl_log.unlink()
    subprocess.run(
        ["sh", str(bootstrap)],
        check=True,
        env=env | {"OPENPROGRAM_VERSION": "1.2.3"},
    )
    assert result.read_text(encoding="utf-8") == "1.2.3|Fzkuji/OpenProgram\n"
    assert curl_log.read_text(encoding="utf-8").splitlines() == [
        "https://raw.githubusercontent.com/Fzkuji/OpenProgram/v1.2.3/scripts/install-release.sh"
    ]


def test_docs_publish_short_installer_at_the_domain_root() -> None:
    workflow = (ROOT / ".github" / "workflows" / "docs-pages.yml").read_text(
        encoding="utf-8"
    )
    assert "mv _publish/docs/install.sh _publish/install" in workflow


def test_normal_user_docs_use_the_short_release_installer() -> None:
    short_command = "curl -fsSL https://openprogram.io/install | sh"
    for relative in (
        "README.md",
        "docs/README.md",
        "docs/README.zh.md",
        "docs/install/install.md",
        "docs/install/install.zh.md",
        "docs/install/upgrade.md",
        "docs/install/upgrade.zh.md",
        "docs/start/GETTING_STARTED.md",
        "docs/start/GETTING_STARTED.zh.md",
        "site/index.html",
    ):
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert short_command in contents, relative
        assert "v0.6.1/scripts/install-release.sh" not in contents, relative


def test_cli_exposes_distribution_version(capsys) -> None:
    from openprogram.cli import build_parser

    with pytest.raises(SystemExit) as version_exit:
        build_parser().parse_args(["--version"])
    assert version_exit.value.code == 0
    assert capsys.readouterr().out.startswith("openprogram ")


def test_desktop_runtime_removes_absolute_python_aliases() -> None:
    staging = (ROOT / "scripts" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    assert 'readlink "$python_alias"' in staging
    assert 'unlink "$python_alias"' in staging


def test_product_runtime_installs_complete_default_capabilities() -> None:
    staging = (ROOT / "scripts" / "build-product-runtime.sh").read_text(
        encoding="utf-8"
    )
    verifier = (ROOT / "scripts" / "verify-product-runtime.py").read_text(
        encoding="utf-8"
    )
    product_config = (ROOT / "config" / "product-runtime.json").read_text(
        encoding="utf-8"
    )
    assert "--frozen --no-dev" in staging
    assert "--extra all --extra search" in staging
    assert "--require-hashes" in staging
    assert '--no-deps "$wheel"' in staging
    assert "playwright.sync_api" in verifier
    assert "playwright install chromium" in staging
    assert "easyocr" in staging
    assert '"${program_dir}[pdf]"' in staging
    assert "https://download.pytorch.org/whl/cpu" in staging
    assert '"opencv-python==$opencv_version"' in staging
    assert staging.count('--constraint "$program_constraints"') == 3
    assert 'importlib.metadata.version(distribution).split("+", 1)[0]' in verifier
    assert "Salesforce/GPA-GUI-Detector" in product_config
    assert "GUI-Agent-Harness" in product_config
    assert "Research-Agent-Harness" in product_config
    assert "Wiki-Agent-Harness" in product_config


def test_search_runtime_dependency_supports_macos_x64() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'search = ["semble>=0.5.3"]' in pyproject
    assert "tree-sitter-language-pack" not in lock
    assert re.search(
        r"semble_grammars-[^-]+-py3-none-macosx_[^-]+_x86_64\.whl",
        lock,
    )


def test_product_manifest_requires_one_complete_capability_set() -> None:
    manifest = json.loads(
        (ROOT / "config" / "product-runtime.json").read_text(encoding="utf-8")
    )
    assert manifest["schema"] == 1
    assert set(manifest["capabilities"]) == {
        "web",
        "providers",
        "mcp",
        "memory",
        "channels",
        "search",
        "browser.playwright",
        "ocr.default",
        "model.gpa_detector",
        "program.gui",
        "program.research",
        "program.wiki",
    }
    assert set(manifest["programs"]) == {"gui", "research", "wiki"}
    assert manifest["programs"]["gui"]["numpy"] == "1.26.4"
    assert manifest["programs"]["gui"]["opencv"] == "4.11.0.86"
    assert manifest["programs"]["gui"]["torch"] == "2.2.2"
    assert manifest["programs"]["gui"]["torchvision"] == "0.17.2"
    for program in manifest["programs"].values():
        assert re.fullmatch(r"[0-9a-f]{40}", program["commit"])


def test_source_development_installer_adds_to_complete_product() -> None:
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert 'PIP install -e "$HOST_ROOT[all,search]"' in installer
    assert '"$PY" -m playwright install chromium' in installer
    assert '"$PY" -m openprogram programs install all' in installer
    assert 'bash "$gui_installer" --no-host --python "$PY"' in installer
    assert 'PIP install -e "$applications/research_harness[pdf]"' in installer
    assert "prompt_programs_menu" not in installer
    assert "--minimal was removed" in installer
    assert "WITH_STEALTH" in installer
    assert "WITH_AGENT_BROWSER" in installer


def test_native_release_workflow_has_platform_jobs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "macos-" in workflow
    assert "ubuntu-" in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "product-runtime:" in workflow
    assert "cli-installer:" in workflow
    assert "product-runtime-${{ matrix.platform }}-${{ matrix.arch }}" in workflow
    assert "scripts/build-product-runtime.sh" in workflow
    assert "scripts/archive-product-runtime.sh" in workflow
    assert "scripts/prepare-desktop-runtime.sh" in workflow
    assert "scripts/verify-release-version.py" in workflow
    assert "scripts/create-release-manifest.py" in workflow
    assert "scripts/smoke-packaged-runtime.sh" in workflow
    assert "sha256" in workflow.lower()
    assert workflow.count("--publish never") == 1
    assert "AppImage" not in workflow


def test_macos_desktop_matrix_maps_runtime_arch_to_electron_builder_arch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "arch: x86_64\n            builder_arch: x64" in workflow
    assert "arch: arm64\n            builder_arch: arm64" in workflow
    assert "--${{ matrix.builder_arch }} --publish never" in workflow


def test_linux_complete_runtime_smoke_is_runnable_without_release_credentials() -> None:
    workflow = (ROOT / ".github" / "workflows" / "linux-release-smoke.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "environment: release" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "scripts/install-release.sh" in workflow
    assert "AppImage" not in workflow
    assert "electron-builder" not in workflow


def test_distribution_workflows_use_node24_action_releases() -> None:
    workflows = [
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        for name in ("release.yml", "linux-release-smoke.yml")
    ]
    for workflow in workflows:
        assert "actions/checkout@v7" in workflow
        assert "actions/setup-node@v7" in workflow
        assert "astral-sh/setup-uv@v10.0.1" in workflow
        assert "actions/upload-artifact@v7" in workflow
        assert "actions/download-artifact@v8" in workflow


def test_packaged_smoke_rejects_unreleased_linux_desktop() -> None:
    smoke = (ROOT / "scripts" / "smoke-packaged-runtime.sh").read_text(encoding="utf-8")
    assert "AppImage" not in smoke
    assert "linux)" not in smoke
    assert "python3 -c" not in smoke


def test_packaged_smoke_reads_formatted_runtime_manifest(tmp_path: Path) -> None:
    runtime = (
        tmp_path
        / "dist"
        / "mac-arm64"
        / "OpenProgram.app"
        / "Contents"
        / "Resources"
        / "runtime"
    )
    runtime.mkdir(parents=True)
    (runtime / "runtime-manifest.json").write_text(
        json.dumps({"python": "python/bin/python3"}, indent=2) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            str(ROOT / "scripts" / "smoke-packaged-runtime.sh"),
            "mac",
            str(tmp_path / "dist"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "managed Python is not executable" in result.stderr
    assert "managed Python path missing" not in result.stderr


def test_release_workflow_builds_explicitly_unsigned_macos_artifacts() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "unsigned" in workflow.lower()
    assert 'CSC_IDENTITY_AUTO_DISCOVERY: "false"' in workflow
    for forbidden in (
        "APPLE_API_KEY",
        "APPLE_API_ISSUER",
        "APPLE_TEAM_ID",
        "MAC_CSC_LINK",
        "notarytool",
        "stapler",
        "gh-action-pypi-publish",
    ):
        assert forbidden not in workflow


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


def test_linux_install_docs_do_not_claim_a_desktop_artifact() -> None:
    expectations = {
        "docs/install/install.md": "no reduced Linux desktop artifact is published",
        "docs/install/install.zh.md": "不发布精简的 Linux 桌面产物",
    }
    for relative, expected in expectations.items():
        contents = (ROOT / relative).read_text(encoding="utf-8")
        assert "linux-x86_64.AppImage" not in contents
        assert expected in contents


def test_public_docs_describe_one_complete_release_product() -> None:
    public_docs = [
        ROOT / "README.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.zh.md",
        ROOT / "docs" / "install" / "install.md",
        ROOT / "docs" / "install" / "install.zh.md",
        ROOT / "docs" / "install" / "upgrade.md",
        ROOT / "docs" / "install" / "upgrade.zh.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.md",
        ROOT / "docs" / "capabilities" / "installing-harnesses.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.md",
        ROOT / "docs" / "capabilities" / "workflows" / "README.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "gui-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "research-agent.zh.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.md",
        ROOT / "docs" / "capabilities" / "workflows" / "wiki-agent.zh.md",
        ROOT / "docs" / "capabilities" / "tools.md",
        ROOT / "docs" / "capabilities" / "tools.zh.md",
        ROOT / "docs" / "capabilities" / "README.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.md",
        ROOT
        / "docs"
        / "capabilities"
        / "agentic-programming"
        / "embedding-in-your-own-stack.zh.md",
        ROOT / "docs" / "integrations" / "channels.md",
        ROOT / "docs" / "integrations" / "channels.zh.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.md",
        ROOT / "docs" / "start" / "GETTING_STARTED.zh.md",
        ROOT / "docs" / "start" / "faq.md",
        ROOT / "docs" / "start" / "faq.zh.md",
        ROOT / "docs" / "slides" / "openprogram-intro.html",
        ROOT / "docs" / "reference" / "design" / "feature-matrix.html",
    ]
    forbidden = (
        "openprogram programs install gui",
        "openprogram programs install research",
        "openprogram programs install wiki",
        "Agent programs are not part",
        "agent Program 不属于",
        "notarized DMG",
        "exact wheel",
        "精确 wheel",
        "pip install 'openprogram[search]'",
        "pip install openprogram[channels]",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)
    for phrase in forbidden:
        assert phrase not in combined
    assert "same complete product capabilities" in combined
    assert "相同的完整产品能力" in combined


def test_release_manifest_records_hashes(tmp_path: Path) -> None:
    import subprocess

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "OpenProgram-0.6.4-mac-arm64.dmg").write_bytes(b"artifact")
    output = artifacts / "release-manifest.json"
    subprocess.run(
        [
            "python",
            str(ROOT / "scripts" / "create-release-manifest.py"),
            str(artifacts),
            "--version",
            "v0.6.4",
            "--output",
            str(output),
        ],
        check=True,
    )
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.4"
    assert manifest["files"][0]["sha256"] == (
        "c7c5c1d70c5dec4416ab6158afd0b223ef40c29b1dc1f97ed9428b94d4cadb1c"
    )
