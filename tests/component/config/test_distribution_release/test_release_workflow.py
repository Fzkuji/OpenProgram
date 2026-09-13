"""release release workflow tests."""
from __future__ import annotations
from ._support import (
    ROOT,
    _desktop_package,
    re,
    runpy,
)


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
    assert "sha256" in workflow.lower()
    assert "electron-builder --mac dmg zip" in workflow
    assert "electron-builder --win nsis --${{ matrix.builder_arch }} --publish never" in workflow
    assert "windows-11-vs2026-arm" in matrix
    assert "vars.OPENPROGRAM_RELEASE_WINDOWS" in workflow
    assert "fromJSON(needs.platforms.outputs.runtime)" in workflow
    assert "fromJSON(needs.platforms.outputs.desktop)" in workflow
    assert workflow.count("--publish never") == 2
    assert "AppImage" not in workflow



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
    assert f"OpenProgram-{version}-mac-arm64-unsigned.dmg" in notes
    assert f"OpenProgram-{version}-mac-x64-unsigned.dmg" in notes
    for section in (
        "## 🐞 Bug fixes",
        "## ✨ New features",
        "## 🚀 Improvements",
        "## 📦 Download and installation",
        "## 🔄 Upgrade guide",
    ):
        assert section in notes
    assert "- **macOS**" in notes
    assert "  - **Package installation**" in notes
    assert "  - **Command-line installation**" in notes
    assert "- **Linux**" in notes
    assert "  - **Command-line / Server installation**" in notes
    assert notes.count("  - **Development installation**") == 2
    assert "share the same complete runtime and browser backend" in notes
    assert "built-in Browser Pane are available only in the macOS Desktop App" in notes
    assert "| User type |" not in notes
    assert "curl -fsSL https://openprogram.io/install | sh" in notes

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert 'notes_file=".github/release-notes/$GITHUB_REF_NAME.md"' in workflow
    assert 'release_version="${GITHUB_REF_NAME#v}"' in workflow
    assert 'test -s "$notes_file"' in workflow
    assert "release notes must be English" in workflow
    assert '--title "OpenProgram $release_version Release"' in workflow
    assert '--notes-file "$notes_file"' in workflow
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



def test_release_matrix_requires_explicit_windows_selection():
    select = runpy.run_path(str(ROOT / "scripts/release/release-matrix.py"))["release_matrices"]
    default = select()
    assert {(row["platform"], row["arch"]) for row in default["runtime"]} == {
        ("macos", "arm64"), ("macos", "x86_64"),
        ("linux", "arm64"), ("linux", "x86_64"),
    }
    assert {row["platform"] for row in default["desktop"]} == {"mac"}
    enabled = select(True)
    assert len(enabled["runtime"]) == 6
    assert {(row["platform"], row["builder_arch"]) for row in enabled["desktop"]} == {
        ("mac", "arm64"), ("mac", "x64"), ("win", "arm64"), ("win", "x64"),
    }
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert 'throw "Windows Desktop publication requires WINDOWS_CSC_LINK and WINDOWS_CSC_KEY_PASSWORD"' in workflow

