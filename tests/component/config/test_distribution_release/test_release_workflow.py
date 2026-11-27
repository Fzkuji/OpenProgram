"""release release workflow tests."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest

from openprogram.operations.project_site import INSTALL_SCRIPT_URL

from ._support import ROOT, _desktop_package


def test_native_release_workflow_has_platform_jobs() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    matrix = (ROOT / "scripts/release/release-matrix.py").read_text()
    assert "macos-" in matrix
    assert "macos-26-intel" not in matrix
    assert "macos-15-intel" in matrix
    icon_check = (ROOT / "apps" / "desktop" / "scripts" / "check-icon.sh").read_text(
        encoding="utf-8"
    )
    assert '\"macos-15-intel\"' in icon_check
    assert "macos-26-intel" not in icon_check
    assert "ubuntu-" in matrix
    assert "ubuntu-24.04-arm" in matrix
    assert "product-runtime:" in workflow
    assert "cli-installer:" in workflow
    assert "product-runtime-${{ matrix.platform }}-${{ matrix.arch }}" in workflow
    assert "scripts/release/build-product-runtime.sh" in workflow
    assert "scripts/release/archive-product-runtime.sh" in workflow
    assert "scripts/release/prepare-desktop-runtime.sh" in workflow
    assert "scripts/release/verify-release-version.py" in workflow
    assert "scripts/release/create-release-manifest.py" in workflow
    assert "scripts/release/smoke-packaged-runtime.sh" in workflow
    assert "timeout-minutes: 90" in workflow
    assert "sha256" in workflow.lower()
    assert "electron-builder --mac --dir" in workflow
    assert "scripts/release/ci-sign-macos.py" in workflow
    assert "electron-builder --win nsis --${{ matrix.builder_arch }} --publish never" in workflow
    assert "windows-11-vs2026-arm" in matrix
    for variable in (
        "OPENPROGRAM_RELEASE_WINDOWS_RUNTIME", "OPENPROGRAM_RELEASE_WINDOWS_ARM64",
        "OPENPROGRAM_RELEASE_DESKTOP_MAC", "OPENPROGRAM_RELEASE_DESKTOP_WINDOWS",
        "OPENPROGRAM_RELEASE_DESKTOP_LINUX", "OPENPROGRAM_RELEASE_MAC_THIN",
        "OPENPROGRAM_RELEASE_CLI_SEEDS",
    ):
        assert f"vars.{variable}" in workflow
    assert "fromJSON(needs.platforms.outputs.runtime)" in workflow
    assert "fromJSON(needs.platforms.outputs.desktop)" in workflow
    assert "fromJSON(needs.platforms.outputs.seed)" in workflow
    assert workflow.count("--publish never") == 4
    assert "matrix.platform != 'win'" not in workflow
    assert "apps/desktop/dist/*.AppImage" in workflow
    assert "apps/desktop/dist/*.deb" in workflow



def test_release_workflow_publishes_structured_release_notes() -> None:
    version = _desktop_package()["version"]
    notes_path = (
        ROOT
        / ".github"
        / "release-notes"
        / f"v{version}.md"
    )
    assert notes_path.is_file()
    notes = notes_path.read_text(encoding="utf-8")
    assert notes.startswith(f"# OpenProgram {version} Release Notes\n")
    assert f"OpenProgram-{version}-mac-arm64.dmg" in notes
    assert f"OpenProgram-{version}-mac-x64.dmg" in notes
    assert f"OpenProgram-{version}-mac-arm64-unsigned.dmg" not in notes
    assert f"OpenProgram-{version}-mac-x64-unsigned.dmg" not in notes
    for section in (
        "## 🐞 Bug fixes",
        "## ✨ New features",
        "## 🚀 Improvements",
        "## 📦 Download and installation",
        "## 🔄 Upgrade guide",
    ):
        assert section in notes
    download_urls = re.findall(r"\]\((https://github\.com/Fzkuji/OpenProgram/releases/download/[^)]+)\)", notes)
    release_prefix = f"https://github.com/Fzkuji/OpenProgram/releases/download/v{version}/"
    assert download_urls
    assert all(url.startswith(release_prefix) for url in download_urls)
    for arch in ("arm64", "x64"):
        for extension in ("dmg", "zip"):
            assert f"{release_prefix}OpenProgram-{version}-mac-{arch}.{extension}" in download_urls
    for platform in ("macos", "linux"):
        for arch in ("arm64", "x86_64"):
            archive = f"{release_prefix}OpenProgram-{version}-runtime-{platform}-{arch}.tar.gz"
            assert archive in download_urls
            assert archive + ".sha256" in download_urls
    assert "#development-checkout)" in notes
    assert f"curl -fsSL {INSTALL_SCRIPT_URL} | OPENPROGRAM_VERSION={version} sh" in notes

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'notes_file=".github/release-notes/$RELEASE_TAG.md"' in workflow
    assert 'release_version="${RELEASE_TAG#v}"' in workflow
    assert 'test -s "$notes_file"' in workflow
    assert "release notes must be English" in workflow
    assert '--title "OpenProgram $release_version Release"' in workflow
    assert '--notes-file "$final_notes"' in workflow
    assert "--generate-notes" not in workflow



def test_all_versioned_release_notes_use_the_public_english_title() -> None:
    notes_dir = ROOT / ".github" / "release-notes"
    for notes_path in sorted(notes_dir.glob("v*.md")):
        version = notes_path.stem.removeprefix("v")
        notes = notes_path.read_text(encoding="utf-8")
        heading = notes.splitlines()[0]
        assert heading == f"# OpenProgram {version} Release Notes"
        assert not re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", notes), (
            f"release notes must be English: {notes_path.name}"
        )



def test_macos_desktop_matrix_maps_runtime_arch_to_electron_builder_arch() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    matrix = runpy.run_path(str(ROOT / "scripts/release/release-matrix.py"))["release_matrices"]()
    assert {(row["arch"], row["builder_arch"]) for row in matrix["desktop"]} == {
        ("x86_64", "x64"), ("arm64", "arm64"),
    }
    assert "--${{ matrix.builder_arch }} --publish never" in workflow



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



def _workflow() -> str:
    return (ROOT / ".github/workflows/release.yml").read_text()


def test_release_workflow_signs_macos_when_it_can_and_never_requires_it() -> None:
    workflow = _workflow()
    assert "scripts/release/ci-sign-macos.py sign" in workflow
    assert "scripts/release/ci-sign-macos.py adhoc" in workflow
    for secret in ("MAC_CSC_LINK", "MAC_CSC_KEY_PASSWORD", "APPLE_ID",
                   "APPLE_TEAM_ID", "APPLE_APP_SPECIFIC_PASSWORD"):
        assert "secrets." + secret in workflow
    assert "OPENPROGRAM_MAC_SIGNING_AVAILABLE" in workflow
    assert "OPENPROGRAM_WINDOWS_SIGNING_AVAILABLE" in workflow
    assert "if: always() && matrix.platform == 'mac' && matrix.signing == 'signed'" in workflow
    assert "if: always() && matrix.signing == 'signed'" in workflow
    # The signing credentials reach only the step that installs them.
    sign = workflow.index("- name: Sign and notarize macOS artifacts")
    assert "secrets." not in workflow[sign:workflow.index("- name: Remove macOS signing keychain", sign)]


def test_release_workflow_uploads_the_signed_app_as_a_shell_layer() -> None:
    workflow = _workflow()
    step = workflow.index("- name: Upload the App's shell layer")
    body = workflow[step:workflow.index("- uses: actions/upload-artifact@v7", step)]
    assert "matrix.signing == 'signed'" in body and "matrix.layout == 'thin'" in body
    assert "ditto -x -k" in body and "codesign --verify --deep --strict" in body
    assert "win-unpacked" in body
    assert "matrix.platform == 'linux'" in body and "OpenProgram-*.AppImage" in body
    assert "--seed apps/desktop/build/seed" in body, "an AppImage's seed says the Python line its launcher is for"
    assert "publish-updates.py upload-shell-layer" in body
    assert "inputs.rehearse && '--no-upload'" in body


def test_one_platform_failing_does_not_block_the_publish_job() -> None:
    workflow = _workflow()
    publish = workflow[workflow.index("  publish:\n"):]
    assert "if: ${{ !cancelled() && needs.platforms.result == 'success' && !inputs.runtime_layers_only }}" in publish
    assert "needs: [office, platforms, python, product-runtime, seed, desktop, cli-installer]" in publish
    assert "release-matrix.py assemble" in publish
    for job in ("seed", "desktop", "cli-installer"):
        body = workflow[workflow.index(f"  {job}:\n"):]
        assert "if: ${{ !cancelled() && needs.platforms.result == 'success'" in body[:200]
        assert "fail-fast: false" in body[:400]
    assert "name: cli-verified-${{ matrix.platform }}-${{ matrix.arch }}" in workflow


def test_release_checks_the_update_key_against_the_code() -> None:
    workflow = _workflow()
    assert "python scripts/release/publish-updates.py public-key --signing-key \"$key_file\"" in workflow
    assert "grep -qF \"$public_key\" openprogram/operations/self_update/official/signing.py" in workflow
    assert "grep -qF \"$public_key\" apps/desktop/main/self-update/update-service.js" in workflow
    # The release builds installers only; updates come from the update channel.
    for gone in ("create-update-assets", "update-assets-", "official-update", "update_key:"):
        assert gone not in workflow, gone


def test_seed_and_thin_builds_have_what_they_need() -> None:
    workflow = _workflow()
    seed = workflow[workflow.index("  seed:\n"):workflow.index("  desktop:\n")]
    assert "fetch-depth: 0" in seed
    assert "- name: Confirm zstd" in seed
    assert "actions/setup-python@v6" in seed
    assert "scripts/release/build-release-seed.sh" in seed
    assert "scripts/release/smoke-thin-shell.sh" in seed
    desktop = workflow[workflow.index("  desktop:\n"):workflow.index("  cli-installer:\n")]
    assert "thin-shell-config.py build/thin-shell.json build/seed" in desktop
    assert "continue-on-error: true" in desktop
    assert "scripts/release/smoke-linux-desktop.sh" in desktop
    assert "NotSigned" in desktop
    assert '$_.BaseName + "-unsigned.exe"' in desktop
    script = (ROOT / "scripts/release/build-release-seed.sh").read_text()
    assert "openprogram-launcher.exe" in script
    assert "for candidate in python3 python" in script
    assert "command -v zstd" in script
    builder = (ROOT / "scripts/release/build-seed.py").read_text()
    assert '"System32" / "tar.exe"' in builder


def _select():
    return runpy.run_path(str(ROOT / "scripts/release/release-matrix.py"))


def _matrix(**switches):
    module = _select()
    return module["release_matrices"](module["Switches"](**switches))


def _desktop(plan):
    return {(row["platform"], row["arch"]): (row["signing"], row["layout"]) for row in plan["desktop"]}


def test_no_secrets_still_builds_cli_and_unsigned_macos() -> None:
    plan = _matrix()
    assert {(row["platform"], row["arch"]) for row in plan["runtime"]} == {
        ("macos", "arm64"), ("macos", "x86_64"),
        ("linux", "arm64"), ("linux", "x86_64"), ("windows", "x86_64"),
    }
    assert _desktop(plan) == {
        ("mac", "arm64"): ("unsigned", "full"), ("mac", "x86_64"): ("unsigned", "full"),
    }
    assert plan["seed"] == []
    assert any(item.startswith("Windows App:") and "update signing key" in item for item in plan["omitted"])
    assert any(item.startswith("Linux App:") and "update signing key" in item for item in plan["omitted"])


def test_every_secret_signs_every_app_and_makes_macos_thin() -> None:
    plan = _matrix(mac_signing=True, windows_signing=True, update_key=True)
    assert _desktop(plan) == {
        ("mac", "arm64"): ("signed", "thin"), ("mac", "x86_64"): ("signed", "thin"),
        ("win", "x86_64"): ("signed", "thin"),
        ("linux", "x86_64"): ("none", "thin"), ("linux", "arm64"): ("none", "thin"),
    }
    assert {row["runner"] for row in plan["desktop"] if row["platform"] == "linux"} == {
        "ubuntu-22.04", "ubuntu-22.04-arm",
    }
    assert {(row["platform"], row["arch"]) for row in plan["seed"]} == {
        ("macos", "arm64"), ("macos", "x86_64"),
        ("windows", "x86_64"), ("linux", "x86_64"), ("linux", "arm64"),
    }
    assert all(row["cli_asset"] == "false" for row in plan["seed"])


def test_update_key_without_certificates_builds_unsigned_windows() -> None:
    assert _desktop(_matrix(update_key=True))[("win", "x86_64")] == ("unsigned", "thin")


@pytest.mark.parametrize(
    ("switch", "gone"),
    [("desktop_mac", "mac"), ("desktop_windows", "win"), ("desktop_linux", "linux")],
)
def test_a_variable_switches_one_desktop_leg_off(switch: str, gone: str) -> None:
    plan = _matrix(update_key=True, **{switch: False})
    remaining = {row["platform"] for row in plan["desktop"]}
    assert gone not in remaining and len(remaining) == 2
    assert any("switched off" in item for item in plan["omitted"])


def test_windows_runtime_switch_is_separate_from_the_app() -> None:
    plan = _matrix(windows_runtime=False, update_key=True)
    assert all(row["platform"] != "windows" for row in plan["runtime"])
    assert ("win", "x86_64") in _desktop(plan)


def test_windows_arm64_needs_its_own_switch() -> None:
    assert all(row["arch"] != "arm64" or row["platform"] != "windows" for row in _matrix(update_key=True)["runtime"])
    plan = _matrix(windows_arm64=True, update_key=True)
    assert {("windows-2025", "x86_64"), ("windows-11-vs2026-arm", "arm64")} == {
        (row["runner"], row["arch"]) for row in plan["runtime"] if row["platform"] == "windows"
    }
    assert ("win", "arm64") in _desktop(plan)


def test_thin_macos_is_the_default_with_signing_and_the_key() -> None:
    assert _desktop(_matrix(mac_signing=True, update_key=True))[("mac", "arm64")] == ("signed", "thin")
    forced = _matrix(mac_thin=False, mac_signing=True, update_key=True)
    assert _desktop(forced)[("mac", "arm64")] == ("signed", "full")
    assert all(row["platform"] != "macos" for row in forced["seed"])
    assert _desktop(_matrix(mac_thin=True))[("mac", "arm64")] == ("unsigned", "full")
    assert _desktop(_matrix(mac_thin=True, update_key=True))[("mac", "arm64")] == ("unsigned", "full")
    assert _desktop(_matrix(mac_thin=True, mac_signing=True))[("mac", "arm64")] == ("signed", "full")
    plan = _matrix(mac_thin=True, mac_signing=True, update_key=True)
    assert _desktop(plan)[("mac", "arm64")] == ("signed", "thin")
    signed = {(row["platform"], row["arch"]): row["signing"] for row in plan["seed"]}
    assert signed[("macos", "arm64")] == "signed"
    assert signed[("linux", "arm64")] == "none"


def test_command_line_seeds_are_off_until_switched_on_and_need_the_key() -> None:
    assert _matrix(cli_seeds=True)["seed"] == []
    plan = _matrix(cli_seeds=True, update_key=True)
    assert {(row["platform"], row["arch"]) for row in plan["seed"] if row["cli_asset"] == "true"} == {
        ("macos", "arm64"), ("macos", "x86_64"), ("linux", "x86_64"), ("linux", "arm64"), ("windows", "x86_64"),
    }
    assert {row["runner"] for row in plan["seed"] if row["platform"] == "linux"} == {
        "ubuntu-22.04", "ubuntu-22.04-arm",
    }


def test_environment_reads_each_switch_its_own_way() -> None:
    module = _select()
    read = module["switches_from_environment"]
    defaults = read({})
    assert defaults.desktop_mac and defaults.desktop_windows and defaults.desktop_linux and defaults.windows_runtime
    assert defaults.mac_thin
    assert not (defaults.windows_arm64 or defaults.cli_seeds)
    assert read({"OPENPROGRAM_RELEASE_MAC_THIN": "false"}).mac_thin is False
    assert not (defaults.mac_signing or defaults.windows_signing or defaults.update_key)
    assert read({"OPENPROGRAM_RELEASE_DESKTOP_LINUX": "false"}).desktop_linux is False
    assert read({"OPENPROGRAM_RELEASE_DESKTOP_LINUX": ""}).desktop_linux is True
    assert read({"OPENPROGRAM_RELEASE_CLI_SEEDS": "true"}).cli_seeds is True
    assert read({"OPENPROGRAM_MAC_SIGNING_AVAILABLE": "true"}).mac_signing is True


@pytest.mark.parametrize("switches", [
    {},
    {"mac_signing": True, "update_key": True},
    {"desktop_mac": False, "desktop_windows": False, "desktop_linux": False, "mac_thin": False,
     "windows_runtime": False, "windows_arm64": True},
])
def test_runtime_layers_cover_every_update_platform_whatever_the_installers(switches) -> None:
    from openprogram.operations.self_update.official.publish import RUNTIME_PLATFORMS

    module = _select()
    rows = module["runtime_layer_matrix"](module["Switches"](**switches))
    assert [f"{row['platform']}-{row['arch']}" for row in rows] == list(RUNTIME_PLATFORMS)
    assert {row["runner"] for row in rows if row["platform"] == "linux"} == {"ubuntu-22.04", "ubuntu-22.04-arm"}
    signing = "signed" if switches.get("mac_signing") else "none"
    assert {row["platform"]: row["signing"] for row in rows} == {
        "macos": signing, "linux": "none", "windows": "none",
    }


@pytest.mark.parametrize(("layers_only", "seeds"), [("", "0"), ("true", "0")])
def test_select_always_plans_the_runtime_layers(layers_only: str, seeds: str) -> None:
    result = subprocess.run(
        ["python3", str(ROOT / "scripts/release/release-matrix.py"), "select"],
        capture_output=True, text=True, check=True,
        env={"PATH": os.environ["PATH"], "OPENPROGRAM_RELEASE_RUNTIME_LAYERS_ONLY": layers_only},
    )
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert outputs["runtime_layers_count"] == "5" and outputs["seed_count"] == seeds
    if layers_only:
        assert outputs["runtime_count"] == "0" and outputs["desktop_count"] == "0"


def test_the_runtime_layer_job_uploads_every_layer_and_can_run_alone() -> None:
    yaml = pytest.importorskip("yaml")
    workflow = _workflow()
    jobs = yaml.safe_load(workflow)["jobs"]
    inputs = yaml.safe_load(workflow)[True]["workflow_dispatch"]["inputs"]
    assert inputs["runtime_layers_only"]["type"] == "boolean" and inputs["runtime_layers_only"]["default"] is False
    assert inputs["tag"]["required"] is False
    assert "-f runtime_layers_only=true" in workflow
    job = jobs["runtime-layer"]
    assert job["needs"] == "platforms"
    assert job["if"] == "${{ !cancelled() && needs.platforms.result == 'success' && !inputs.rehearse }}"
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.platforms.outputs.runtime_layers) }}"
    assert jobs["platforms"]["outputs"]["runtime_layers"] == "${{ steps.select.outputs.runtime_layers }}"
    steps = [step.get("name", "") for step in job["steps"]]
    named = {step["name"]: step for step in job["steps"] if "name" in step}
    # The base is built (and its macOS binaries signed) before the layer is made from it.
    assert steps.index("Build the base") < steps.index("Upload the runtime layer")
    assert "build-release-seed.sh" in named["Build the base"]["run"]
    assert "MAC_SIGNING_IDENTITY" in named["Build the base"]["env"]["OPENPROGRAM_RELEASE_SIGN_IDENTITY"]
    upload = named["Upload the runtime layer"]
    assert upload["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "publish-updates.py upload-runtime-layer" in upload["run"]
    assert '--platform "${{ matrix.platform }}-${{ matrix.arch }}"' in upload["run"]
    assert "--tailnet" in upload["run"] and "--with zstandard --with cryptography" in upload["run"]
    # Only this job uploads layers; the seed no longer depends on it or does it.
    assert workflow.count("upload-runtime-layer") == 1
    # Runtime layers alone run only platforms and this job.
    for name in ("office", "python", "product-runtime", "cli-installer", "publish"):
        assert "!inputs.runtime_layers_only" in jobs[name]["if"], name
    assert "inputs.runtime_layers_only" in jobs["platforms"]["if"]
    assert "OPENPROGRAM_RELEASE_RUNTIME_LAYERS_ONLY: ${{ inputs.runtime_layers_only }}" in workflow
    assert "(inputs.rehearse || inputs.runtime_layers_only) && github.sha || inputs.tag" in workflow


def test_select_prints_actions_outputs() -> None:
    result = subprocess.run(
        ["python3", str(ROOT / "scripts/release/release-matrix.py"), "select"],
        capture_output=True, text=True, check=True, env={"PATH": os.environ["PATH"]},
    )
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert outputs["desktop_count"] == "2" and outputs["seed_count"] == "0"
    assert len(json.loads(outputs["runtime"])["include"]) == 5
    assert "omitted" in json.loads(outputs["plan"])


def _file(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _artifacts(root: Path, version: str = "1.0.1") -> None:
    _file(root / "developer-python-distributions" / f"openprogram-{version}-py3-none-any.whl")
    _file(root / "office-assets" / "OfficeAssets-d15d12b-dc31dd9d-24d2d8e1.zip")
    for key in ("macos-arm64", "linux-x86_64"):
        _file(root / f"product-runtime-{key}" / f"OpenProgram-{version}-runtime-{key}.tar.gz")
        _file(root / f"cli-verified-{key}" / "verified.txt")
    _file(root / "seed-linux-x86_64" / "seed-linux-x86_64.tar")


def _plan(**switches) -> dict:
    return _matrix(**switches)


def test_assemble_drops_what_did_not_pass_and_says_so(tmp_path: Path) -> None:
    module = _select()
    root = tmp_path / "artifacts"
    _artifacts(root)
    _file(root / "product-runtime-linux-arm64" / "OpenProgram-1.0.1-runtime-linux-arm64.tar.gz")
    _file(root / "desktop-full-mac-arm64" / "OpenProgram-1.0.1-mac-arm64-unsigned.dmg")
    missing = module["keep_publishable"](root, _plan())
    assert not (root / "seed-linux-x86_64").exists() and not (root / "cli-verified-linux-x86_64").exists()
    assert not (root / "product-runtime-linux-arm64").exists(), "an unverified runtime must not ship"
    assert "Linux arm64 command-line runtime: it did not pass the install check." in missing
    assert "macOS Intel command-line runtime: its build failed." in missing
    assert "macOS Intel App: its build failed." in missing
    assert (root / "desktop-full-mac-arm64").is_dir()
    assert module["refusal"](root) is None


def test_a_linux_leg_without_appimage_ships_the_deb(tmp_path: Path) -> None:
    module = _select()
    root = tmp_path / "artifacts"
    _artifacts(root)
    _file(root / "desktop-thin-linux-x86_64" / "OpenProgram-1.0.1-linux-amd64.deb")
    missing = module["keep_publishable"](root, _plan(update_key=True))
    assert (root / "desktop-thin-linux-x86_64").is_dir()
    assert "Linux x86_64 AppImage: its build failed; the deb package is available." in missing


def test_publish_refuses_only_without_the_wheel_the_office_archive_or_every_macos_and_linux_runtime(
    tmp_path: Path,
) -> None:
    refusal = _select()["refusal"]
    no_wheel = tmp_path / "no-wheel"
    _artifacts(no_wheel)
    shutil.rmtree(no_wheel / "developer-python-distributions")
    assert "wheel" in refusal(no_wheel)

    no_office = tmp_path / "no-office"
    _artifacts(no_office)
    shutil.rmtree(no_office / "office-assets")
    assert refusal(no_office) == "the Office archive is missing"

    no_runtimes = tmp_path / "no-runtimes"
    _artifacts(no_runtimes)
    shutil.rmtree(no_runtimes / "product-runtime-macos-arm64")
    shutil.rmtree(no_runtimes / "product-runtime-linux-x86_64")
    assert "runtime" in refusal(no_runtimes)

    linux_only = tmp_path / "linux-only"
    _artifacts(linux_only)
    shutil.rmtree(linux_only / "product-runtime-macos-arm64")
    assert refusal(linux_only) is None


def test_each_file_is_uploaded_once_under_its_versioned_name() -> None:
    workflow = _workflow()
    # No second name for an App, and no per-leg checksum file: the release page
    # and release-manifest.json carry every file's SHA-256.
    assert "release-links" not in workflow and "--links-output" not in workflow
    assert "SHA256SUMS-" not in workflow
    assert "make_version_free_copies" not in _select()
    publish = workflow[workflow.index("- name: Publish GitHub Release"):]
    assert "mapfile -d '' files < <(find release-artifacts -type f -print0)" in publish


def test_notes_list_only_what_exists() -> None:
    write = _select()["write_notes"]
    base = "https://github.com/Fzkuji/OpenProgram/releases/download/v1.0.1/"
    text = "\n".join((
        "# OpenProgram 1.0.1 Release Notes", "",
        "| macOS | arm64 | "
        f"[DMG]({base}OpenProgram-1.0.1-mac-arm64.dmg) · [ZIP]({base}OpenProgram-1.0.1-mac-arm64.zip) |",
        f"| Windows | x64 | [Installer]({base}OpenProgram-1.0.1-win-x64.exe) |",
        f"| Linux | x86_64 | [AppImage]({base}OpenProgram-1.0.1-linux-x86_64.AppImage) · "
        f"[deb]({base}OpenProgram-1.0.1-linux-amd64.deb) |",
        f"[Guide](https://example.invalid/guide) · [Manifest]({base}release-manifest.json)",
    )) + "\n"
    names = {
        "OpenProgram-1.0.1-mac-arm64-unsigned.dmg", "OpenProgram-1.0.1-mac-arm64-unsigned.zip",
        "OpenProgram-1.0.1-linux-amd64.deb", "release-manifest.json",
    }
    notes = write(text, "1.0.1", names, ["Windows App: its build failed."])
    assert f"[DMG]({base}OpenProgram-1.0.1-mac-arm64-unsigned.dmg) · [ZIP]({base}OpenProgram-1.0.1-mac-arm64-unsigned.zip)" in notes
    assert "win-x64" not in notes
    assert f"| Linux | x86_64 | [deb]({base}OpenProgram-1.0.1-linux-amd64.deb) |" in notes
    assert "AppImage" not in notes
    assert "[Manifest]" in notes
    assert "## Not in this release\n\n- Windows App: its build failed.\n" in notes
    assert "Open Anyway" in notes
    assert "Run anyway" not in notes


def test_notes_say_nothing_extra_when_everything_is_there() -> None:
    write = _select()["write_notes"]
    notes = write("# Title\n", "1.0.1", {"release-manifest.json"}, [])
    assert notes == "# Title\n"


def test_ci_signing_refuses_without_credentials_and_cleanup_is_harmless(tmp_path: Path) -> None:
    script = str(ROOT / "scripts/release/ci-sign-macos.py")
    environment = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path)}
    setup = subprocess.run(["python3", script, "setup"], capture_output=True, text=True, env=environment)
    assert setup.returncode != 0 and "secrets are required" in setup.stderr
    assert subprocess.run(["python3", script, "cleanup"], env=environment).returncode == 0
    sign = subprocess.run(
        ["python3", script, "sign", "--app", "x", "--output", "y", "--arch", "arm64"],
        capture_output=True, text=True, env=environment,
    )
    assert sign.returncode != 0 and "keychain is missing" in sign.stderr


def test_windows_launcher_loads_visual_studio_when_cl_is_not_on_path() -> None:
    compile_script = runpy.run_path(str(ROOT / "scripts/release/build-launcher.py"))["windows_compile_script"]
    loaded = compile_script(Path("vc/vcvarsall.bat"), "x64", Path("launcher.c"), Path("out.exe"))
    assert 'call "vc/vcvarsall.bat" x64 >nul || exit /b 1' in loaded
    assert 'cl /nologo /O2 "launcher.c" /link "/out:out.exe"' in loaded
    assert "vcvarsall" not in compile_script(None, "x64", Path("launcher.c"), Path("out.exe"))


# --- publish-github.sh --tag --------------------------------------------------


def _git(repository: Path, *arguments: str, **options) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True, text=True, **options)


RETIRED_STAND_IN = "retired-example"


@pytest.fixture
def publishing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")
    repository, origin = tmp_path / "repository", tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "--quiet", "-b", "dev", str(repository)], check=True)
    monkeypatch.setenv("OPENPROGRAM_DOCS_PYTHON", sys.executable)
    _file(repository / "scripts/docs_site/check.py", "")  # the real check has its own tests
    for relative in (
        "scripts/publishing/publish-github.sh", "scripts/git-hooks/pre-push",
        "scripts/git-hooks/pre-commit", "scripts/git-hooks/install.sh",
        "scripts/publishing/retired_accounts.py", "scripts/release/release-version.py",
        "scripts/release/office-archive.py",
    ):
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    # The copied scanner also treats a made-up login as retired, so the tests
    # below can name one without naming a real account.
    scanner = repository / "scripts/publishing/retired_accounts.py"
    digest = hashlib.sha256(RETIRED_STAND_IN.encode()).hexdigest()
    scanner.write_text(
        scanner.read_text(encoding="utf-8").replace("RETIRED = frozenset({", f'RETIRED = frozenset({{\n    "{digest}",', 1),
        encoding="utf-8",
    )
    # The update publisher is tested on its own; here it records each call,
    # builds nothing, and refuses a release's plan when UPDATES_PLAN_EXIT says
    # so, and a run when UPDATES_RUN_EXIT does.
    _file(repository / "scripts/release/publish-updates.py", (
        "import os, pathlib, sys\n"
        "pathlib.Path(__file__).with_name('updates-calls.log').open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'plan' and '--release' in sys.argv:\n"
        "    sys.exit(int(os.environ.get('UPDATES_PLAN_EXIT', '0')))\n"
        "if sys.argv[1] == 'run':\n"
        "    sys.exit(int(os.environ.get('UPDATES_RUN_EXIT', '0')))\n"
    ))
    _version_places(repository, "1.2.0")
    _file(repository / "openprogram/programs/applications/office_editor/install.py", (
        'OFFICE_RELEASES = "https://github.com/Owner/Repo/releases"\n'
        f'OFFICE_ARCHIVE_NAME = "{OFFICE_NAME}"\n'
        f'OFFICE_ARCHIVE_SHA256 = "{hashlib.sha256(OFFICE_BYTES).hexdigest()}"\n'
        f'OFFICE_DOWNLOAD_BYTES = {len(OFFICE_BYTES)}\n'
    ))
    # gh answers from GH_ASSETS (the names on the newest release and the
    # tag's draft) and logs every call, so nothing reaches GitHub.
    fake_bin = tmp_path / "bin"
    _file(fake_bin / "gh", (
        '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$GH_LOG"\n'
        'case "$*" in "release view"*"--json assets"*) printf \'%s\\n\' $GH_ASSETS ;; '
        '"release view"*) exit 1 ;; esac\n'
    ))
    (fake_bin / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("GH_ASSETS", OFFICE_NAME)
    _file(repository / ".github/release-notes/v1.2.0.md", "# OpenProgram 1.2.0 Release Notes\n\nText.\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", "First", check=True)
    _git(repository, "remote", "add", "origin", str(origin), check=True)
    return repository, origin


OFFICE_NAME = "OfficeAssets-test.zip"
OFFICE_BYTES = b"office archive"


def _version_places(repository: Path, version: str, *, lock: str | None = None) -> None:
    """Every place release-version.py reads, as the real files write them."""
    _file(repository / "pyproject.toml", f'[project]\nname = "openprogram"\nversion = "{version}"\n')
    _file(repository / "apps/desktop/package.json",
          f'{{\n  "name": "openprogram-desktop",\n  "version": "{version}",\n  "private": true\n}}\n')
    _file(repository / "package-lock.json", (
        '{\n  "name": "openprogram-workspace",\n  "packages": {\n'
        f'    "apps/desktop": {{\n      "name": "openprogram-desktop",\n      "version": "{version}",\n'
        '      "license": "AGPL-3.0-or-later"\n    }\n  }\n}\n'
    ))
    _file(repository / "uv.lock", (
        'version = 1\n\n[[package]]\nname = "openprogram"\n'
        f'version = "{lock or version}"\nsource = {{ editable = "." }}\n'
    ))
    _file(repository / "scripts/release/install-release.sh",
          f'#!/usr/bin/env sh\nOPENPROGRAM_VERSION="${{OPENPROGRAM_VERSION:-{version}}}"\n')
    _file(repository / "scripts/release/install-release.ps1",
          f'$Version = if ($env:OPENPROGRAM_VERSION) {{ $env:OPENPROGRAM_VERSION }} else {{ "{version}" }}\n')


def _publish(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repository / "scripts/publishing/publish-github.sh"), *arguments],
        capture_output=True, text=True, cwd=repository,
    )


def test_tag_dry_run_shows_the_tag_and_changes_nothing(publishing) -> None:
    repository, origin = publishing
    result = _publish(repository, "-m", "Version", "--tag", "v1.2.0", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would release the new main commit as v1.2.0" in result.stdout
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode != 0
    assert _git(repository, "tag").stdout == ""
    assert not (repository / ".git/hooks/pre-push").exists(), "a dry run changes nothing"
    assert _git(origin, "for-each-ref").stdout == ""


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("v9.9.9", "does not match this source"),
        ("1.2.0", "looks like v1.2.3 or v1.2.3.4"),
        ("v1.2", "looks like v1.2.3 or v1.2.3.4"),
        ("v1.2.0.1.2", "looks like v1.2.3 or v1.2.3.4"),
    ],
)
def test_tag_refuses_wrong_versions(publishing, tag: str, message: str) -> None:
    repository, _ = publishing
    result = _publish(repository, "-m", "Version", "--tag", tag, "--dry-run")
    assert result.returncode != 0 and message in result.stderr


def test_tag_refuses_a_half_bumped_version_naming_each_place(publishing) -> None:
    repository, origin = publishing
    _version_places(repository, "1.3.0", lock="1.2.0")
    _file(repository / "scripts/release/install-release.ps1",
          '$Version = if ($env:OPENPROGRAM_VERSION) { $env:OPENPROGRAM_VERSION } else { "1.2.0" }\n')
    _file(repository / ".github/release-notes/v1.3.0.md", "# OpenProgram 1.3.0 Release Notes\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", "Half bump", check=True)
    for arguments in (("--dry-run",), ()):
        result = _publish(repository, "-m", "V", "--tag", "v1.3.0", *arguments)
        assert result.returncode != 0
        assert "uv.lock says 1.2.0" in result.stderr
        assert "scripts/release/install-release.ps1 says 1.2.0" in result.stderr
        assert "pyproject.toml" not in result.stderr.split("this source:")[1].split(". Run")[0]
        assert "release-version.py set 1.3.0" in result.stderr
    assert _git(origin, "for-each-ref").stdout == ""


def test_tag_dry_run_says_whether_the_local_office_archive_is_the_pinned_one(publishing, tmp_path: Path) -> None:
    repository, _ = publishing
    absent = _publish(repository, "-m", "V", "--tag", "v1.2.0", "--dry-run")
    assert absent.returncode == 0, absent.stderr
    assert f"Office archive: no local copy given (--office-archive /path/to/{OFFICE_NAME})" in absent.stdout
    archive = tmp_path / "copy" / OFFICE_NAME
    _file(archive)
    archive.write_bytes(OFFICE_BYTES)
    good = _publish(repository, "-m", "V", "--tag", "v1.2.0", "--dry-run", "--office-archive", str(archive))
    assert good.returncode == 0, good.stderr
    assert f"Office archive: {archive} is the pinned {OFFICE_NAME} ({len(OFFICE_BYTES)} bytes" in good.stdout
    archive.write_bytes(b"another build")
    bad = _publish(repository, "-m", "V", "--tag", "v1.2.0", "--dry-run", "--office-archive", str(archive))
    assert bad.returncode != 0 and "is not the pinned" in bad.stderr and "13 bytes, not 14" in bad.stderr
    assert not (tmp_path / "gh.log").exists(), "a dry run never asks GitHub"
    assert "needs --tag" in _publish(repository, "-m", "V", "--office-archive", str(archive)).stderr


def test_tag_without_the_office_archive_on_github_is_refused_or_seeded_first(
    publishing, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, origin = publishing
    monkeypatch.setenv("GH_ASSETS", "something-else.zip")
    refused = _publish(repository, "-m", "V", "--tag", "v1.2.0")
    assert refused.returncode != 0
    assert f"pass --office-archive /path/to/{OFFICE_NAME}" in refused.stderr
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode != 0
    assert _git(origin, "for-each-ref").stdout == ""

    archive = tmp_path / "copy" / OFFICE_NAME
    _file(archive)
    archive.write_bytes(OFFICE_BYTES)
    seeded = _publish(repository, "-m", "V", "--tag", "v1.2.0", "--office-archive", str(archive))
    assert seeded.returncode == 0, seeded.stderr
    calls = (tmp_path / "gh.log").read_text().splitlines()
    assert f"release create v1.2.0 {archive} --draft" in "\n".join(calls)
    assert _git(origin, "rev-parse", "refs/tags/v1.2.0^{commit}").returncode == 0


def test_tag_refuses_missing_notes_a_bad_heading_and_no_push(publishing) -> None:
    repository, _ = publishing
    assert "--no-push" in _publish(repository, "-m", "V", "--tag", "v1.2.0", "--no-push").stderr
    notes = repository / ".github/release-notes/v1.2.0.md"
    notes.write_text("# Wrong\n")
    _git(repository, "commit", "--quiet", "-am", "Bad notes", check=True)
    assert "must start with" in _publish(repository, "-m", "V", "--tag", "v1.2.0", "--dry-run").stderr
    _git(repository, "rm", "--quiet", str(notes), check=True)
    _git(repository, "commit", "--quiet", "-m", "No notes", check=True)
    assert "no release notes" in _publish(repository, "-m", "V", "--tag", "v1.2.0", "--dry-run").stderr


def _updates_calls(repository: Path) -> list[str]:
    log = repository / "scripts/release/updates-calls.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("plan_exit", ["0", "3"])
def test_a_feature_release_tags_and_dispatches_the_build_and_leaves_the_announcement(
    publishing, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_exit: str,
) -> None:
    """Its new base has no runtime layers until the workflow built them, so a refused plan is only shown."""
    repository, origin = publishing
    monkeypatch.setenv("UPDATES_PLAN_EXIT", plan_exit)
    source = _git(repository, "rev-parse", "dev").stdout.strip()
    result = _publish(repository, "-m", "Version", "--tag", "v1.2.0")
    assert result.returncode == 0, result.stderr
    assert "python scripts/release/publish-updates.py announce v1.2.0" in result.stdout
    main = _git(repository, "rev-parse", "refs/heads/main").stdout.strip()
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == main
    assert _git(origin, "rev-parse", "refs/tags/v1.2.0^{commit}").stdout.strip() == main
    assert _git(origin, "for-each-ref", "--format=%(refname)").stdout.split() == [
        "refs/heads/main", "refs/tags/v1.2.0",
    ]
    # announce follows by hand once the workflow published the installers and layers.
    assert _updates_calls(repository) == [f"plan --commit {source} --release v1.2.0"]
    gh = (tmp_path / "gh.log").read_text()
    assert "workflow run release.yml -R Fzkuji/OpenProgram --ref main -f tag=v1.2.0" in gh
    again = _publish(repository, "--tag", "v1.2.0")
    assert again.returncode != 0 and "never moves" in again.stderr


def _code_only(repository: Path, version: str) -> None:
    """A code-only version: only pyproject.toml and uv.lock move."""
    _file(repository / "pyproject.toml", f'[project]\nname = "openprogram"\nversion = "{version}"\n')
    _file(repository / "uv.lock", (
        'version = 1\n\n[[package]]\nname = "openprogram"\n'
        f'version = "{version}"\nsource = {{ editable = "." }}\n'
    ))
    _file(repository / f".github/release-notes/v{version}.md", f"# OpenProgram {version} Release Notes\n\nFix.\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", version, check=True)


@pytest.mark.parametrize("version", ["1.2.1", "1.2.0.1"])
def test_a_code_only_release_runs_no_workflow_and_makes_its_own_page(
    publishing, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    repository, origin = publishing
    _code_only(repository, version)
    monkeypatch.setenv("GH_ASSETS", "")  # no Office archive is needed without installers
    result = _publish(repository, "-m", "Fix", "--tag", f"v{version}")
    assert result.returncode == 0, result.stderr
    main = _git(repository, "rev-parse", "refs/heads/main").stdout.strip()
    assert _git(origin, "rev-parse", f"refs/tags/v{version}^{{commit}}").stdout.strip() == main
    assert _updates_calls(repository)[1:] == [
        f"run --commit {main} --release v{version}", f"release-page --tag v{version}",
    ]
    gh = (tmp_path / "gh.log").read_text() if (tmp_path / "gh.log").exists() else ""
    assert "workflow run" not in gh and "release create" not in gh


def test_a_release_the_publisher_refuses_moves_nothing(publishing, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, origin = publishing
    _code_only(repository, "1.2.1")
    monkeypatch.setenv("UPDATES_PLAN_EXIT", "3")
    result = _publish(repository, "-m", "Fix", "--tag", "v1.2.1")
    assert result.returncode != 0 and "the update publisher said no (exit 3)" in result.stderr
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode != 0
    assert _git(origin, "for-each-ref").stdout == ""
    assert [call.split()[0] for call in _updates_calls(repository)] == ["plan"]


def test_a_feature_release_the_publisher_fails_on_moves_nothing(publishing, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a refusal (exit 3) is expected of a feature release's plan; any other failure stops it."""
    repository, origin = publishing
    monkeypatch.setenv("UPDATES_PLAN_EXIT", "1")
    result = _publish(repository, "-m", "Version", "--tag", "v1.2.0")
    assert result.returncode != 0 and "the update publisher said no (exit 1)" in result.stderr
    assert _git(origin, "for-each-ref").stdout == ""


def test_a_refused_daily_update_names_the_runtime_layers_workflow(publishing, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, origin = publishing
    _file(repository / "notes.txt", "A day's work.\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", "Day", check=True)
    monkeypatch.setenv("UPDATES_RUN_EXIT", "3")
    result = _publish(repository, "-m", "Today's work")
    assert result.returncode == 3
    assert "gh workflow run release.yml -R Fzkuji/OpenProgram --ref main -f runtime_layers_only=true" in result.stderr
    # main is on GitHub, so the workflow can build its layers from it.
    assert _git(origin, "rev-parse", "refs/heads/main").returncode == 0


def test_office_archive_is_only_for_a_feature_release(publishing, tmp_path: Path) -> None:
    repository, _ = publishing
    _code_only(repository, "1.2.1")
    result = _publish(repository, "-m", "Fix", "--tag", "v1.2.1", "--dry-run", "--office-archive", str(tmp_path / "x.zip"))
    assert result.returncode == 2 and "for a feature release (vX.Y.0)" in result.stderr


def test_a_daily_sync_pushes_dev_to_main_without_a_tag(publishing) -> None:
    """A plain sync publishes latest.json only: no workflow, no channel, no tag."""
    repository, origin = publishing
    _file(repository / "notes.txt", "A day's work.\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", "Day", check=True)
    result = _publish(repository, "-m", "Today's work")
    assert result.returncode == 0, result.stderr
    main = _git(repository, "rev-parse", "refs/heads/main").stdout.strip()
    assert _git(repository, "rev-parse", "main^{tree}").stdout == _git(repository, "rev-parse", "dev^{tree}").stdout
    assert _git(origin, "for-each-ref", "--format=%(refname) %(objectname)").stdout.split() == ["refs/heads/main", main]
    assert "nothing to sync" in _publish(repository, "-m", "Again").stdout
    # Each sync without a tag publishes main's latest.json; a run with nothing to
    # sync tries again (one call when it is already up), so a failed upload heals.
    assert _updates_calls(repository) == [f"run --commit {main}"] * 2


def test_hook_lets_only_the_scripts_tag_through(publishing) -> None:
    repository, _ = publishing
    assert _publish(repository, "-m", "Version", "--tag", "v1.2.0").returncode == 0
    _git(repository, "tag", "manual", check=True)
    refused = _git(repository, "push", "origin", "refs/tags/manual:refs/tags/manual")
    assert refused.returncode != 0 and "publish-github.sh --tag" in refused.stderr
    _git(repository, "tag", "offmain", "dev", check=True)
    environment = {**os.environ, "OPENPROGRAM_GITHUB_PUBLISH": "1"}
    other = subprocess.run(
        ["git", "-C", str(repository), "push", "origin", "refs/tags/offmain:refs/tags/offmain"],
        capture_output=True, text=True, env=environment,
    )
    assert other.returncode != 0, "the script only tags main"
    assert _git(repository, "push", "origin", ":refs/tags/v1.2.0").returncode == 0


# --- publish-github.sh: retired accounts ----------------------------------------


def test_a_source_naming_a_retired_account_is_refused_before_anything_moves(publishing) -> None:
    repository, origin = publishing
    _file(repository / "docs/credits.md", f"Thanks to https://github.com/{RETIRED_STAND_IN}/Repo.\n")
    _git(repository, "add", ".", check=True)
    _git(repository, "commit", "--quiet", "-m", "Credits", check=True)
    # Only the commit counts: the checkout no longer holds the line, and the refusal stands.
    (repository / "docs/credits.md").write_text("Thanks.\n", encoding="utf-8")
    for arguments in (("--dry-run",), ()):
        result = _publish(repository, "-m", "Version", "--tag", "v1.2.0", *arguments)
        assert result.returncode != 0
        assert "names a retired account" in result.stderr and "docs/credits.md:1" in result.stderr
    assert _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/main").returncode != 0
    assert _git(origin, "for-each-ref").stdout == ""


def test_a_summary_naming_a_retired_account_is_refused(publishing) -> None:
    repository, _ = publishing
    result = _publish(repository, "-m", f"Thanks {RETIRED_STAND_IN.upper()}", "--dry-run")
    assert result.returncode != 0 and "the summary names a retired account" in result.stderr


def test_main_whose_history_names_a_retired_account_is_not_pushed(publishing) -> None:
    repository, origin = publishing
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "FuzzyKuji", "GIT_AUTHOR_EMAIL": "9404270+FuzzyKuji@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "FuzzyKuji", "GIT_COMMITTER_EMAIL": "9404270+FuzzyKuji@users.noreply.github.com",
    }
    old = _git(
        repository, "commit-tree", "dev^{tree}", "-m", f"Earlier version by {RETIRED_STAND_IN}",
        env=identity, check=True,
    ).stdout.strip()
    _git(repository, "update-ref", "refs/heads/main", old, check=True)
    result = _publish(repository)
    assert result.returncode != 0 and "the history being uploaded names a retired account" in result.stderr
    assert _git(origin, "for-each-ref").stdout == ""


def test_a_clean_source_still_publishes(publishing) -> None:
    repository, origin = publishing
    result = _publish(repository, "-m", "Version")
    assert result.returncode == 0, result.stderr
    assert _git(origin, "rev-parse", "refs/heads/main").stdout.strip() == _git(
        repository, "rev-parse", "refs/heads/main",
    ).stdout.strip()


def test_history_github_already_has_is_not_checked_again(publishing) -> None:
    """GitHub's main starts with years of commits by many authors; a version
    is added after them, and only the version is checked."""
    repository, origin = publishing
    other = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Someone Else", "GIT_AUTHOR_EMAIL": "else@example.com",
        "GIT_COMMITTER_NAME": "Someone Else", "GIT_COMMITTER_EMAIL": "else@example.com",
    }
    empty = _git(repository, "hash-object", "-t", "tree", os.devnull, check=True).stdout.strip()
    old = _git(repository, "commit-tree", empty, "-m", "Before 1.0.0", env=other, check=True).stdout.strip()
    _git(repository, "push", "--quiet", str(origin), f"{old}:refs/heads/main",
         env={**os.environ, "OPENPROGRAM_GITHUB_PUBLISH": "1"}, check=True)
    _git(repository, "update-ref", "refs/heads/main", old, check=True)
    result = _publish(repository, "-m", "Version")
    assert result.returncode == 0, result.stderr
    pushed = _git(origin, "log", "--format=%ae", "refs/heads/main", check=True).stdout.split()
    assert pushed == ["9404270+FuzzyKuji@users.noreply.github.com", "else@example.com"]
