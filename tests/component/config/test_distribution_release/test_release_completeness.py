"""Every platform the release plans for ends up in the release, or the run says so.

The artifact trees here are what the Release workflow's jobs upload, with the
file names electron-builder and the workflow give them. They are written out
literally, so a new matrix row with no installer name fails here first.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from ._support import ROOT

SCRIPT = ROOT / "scripts/release/release-matrix.py"
MANIFEST_SCRIPT = ROOT / "scripts/release/create-release-manifest.py"
OFFICE_SCRIPT = ROOT / "scripts/release/office-archive.py"
NOTES = ROOT / ".github/release-notes"

# (desktop platform, arch) -> installer suffixes and the suffix users install with.
INSTALLERS = {
    ("mac", "arm64"): (["mac-arm64.dmg", "mac-arm64.zip"], "mac-arm64.dmg"),
    ("mac", "x86_64"): (["mac-x64.dmg", "mac-x64.zip"], "mac-x64.dmg"),
    ("win", "x86_64"): (["win-x64.exe"], "win-x64.exe"),
    ("win", "arm64"): (["win-arm64.exe"], "win-arm64.exe"),
    ("linux", "x86_64"): (["linux-amd64.deb", "linux-x86_64.AppImage"], "linux-amd64.deb"),
    ("linux", "arm64"): (["linux-arm64.deb", "linux-arm64.AppImage"], "linux-arm64.deb"),
}
EVERYTHING = {
    "OPENPROGRAM_MAC_SIGNING_AVAILABLE": "true",
    "OPENPROGRAM_WINDOWS_SIGNING_AVAILABLE": "true",
    "OPENPROGRAM_UPDATE_KEY_TRUSTED": "true",
}


def _latest_notes() -> tuple[str, Path]:
    versions = sorted(
        tuple(int(part) for part in path.stem[1:].split("."))
        for path in NOTES.glob("v*.md") if re.fullmatch(r"v\d+\.\d+\.\d+", path.stem)
    )
    version = ".".join(str(part) for part in versions[-1])
    return version, NOTES / f"v{version}.md"


def _plan(environment: dict[str, str]) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "select"], capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin", **environment},
    )
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines())
    return json.loads(outputs["plan"])


def _file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(path.name, encoding="utf-8")


def _installer_name(version: str, suffix: str, signing: str) -> str:
    stem, _, extension = suffix.rpartition(".")
    unsigned = "-unsigned" if signing == "unsigned" and extension in {"dmg", "zip", "exe"} else ""
    return f"OpenProgram-{version}-{stem}{unsigned}.{extension}"


def _desktop_directory(row: dict) -> str:
    return f"desktop-{row['layout']}-{row['platform']}-{row['arch']}"


def _office_name() -> str:
    return subprocess.run([sys.executable, str(OFFICE_SCRIPT), "name"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _built_tree(root: Path, plan: dict, version: str) -> None:
    """What ``actions/download-artifact`` leaves when every job succeeded."""
    _file(root / "developer-python-distributions" / f"openprogram-{version}-py3-none-any.whl")
    _file(root / "office-assets" / _office_name())
    update_platforms = {}
    for row in plan["runtime"]:
        key = f"{row['platform']}-{row['arch']}"
        extension = "zip" if row["platform"] == "windows" else "tar.gz"
        archive = f"OpenProgram-{version}-runtime-{key}.{extension}"
        _file(root / f"product-runtime-{key}" / archive)
        _file(root / f"product-runtime-{key}" / f"{archive}.sha256")
        _file(root / f"cli-verified-{key}" / "verified.txt")
        # With a trusted key every runtime leg builds update files, whatever reads them.
        if plan["update_files"]:
            update_platforms[key] = {}
            for part in ("version", "base", "toolchain"):
                _file(root / "official-update" / f"{part}-{key}.tar.zst")
    for row in plan["seed"]:
        key = f"{row['platform']}-{row['arch']}"
        _file(root / f"seed-{key}" / f"seed-{key}.tar")
    for row in plan["desktop"]:
        directory = root / _desktop_directory(row)
        for suffix in INSTALLERS[(row["platform"], row["arch"])][0]:
            _file(directory / _installer_name(version, suffix, row["signing"]))
    if update_platforms:
        (root / "official-update" / "update-manifest.json").write_text(
            json.dumps({"platforms": update_platforms}), encoding="utf-8",
        )
        _file(root / "official-update" / "update-manifest.json.sig")
        _file(root / "official-update" / "SHA256SUMS")


def _assemble(root: Path, plan: dict, version: str, work: Path, notes: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "assemble", str(root), "--version", f"v{version}",
         "--plan", json.dumps(plan), "--notes", str(notes),
         "--notes-output", str(work / "notes.md"),
         "--incomplete-output", str(work / "incomplete.txt")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    subprocess.run(
        [sys.executable, str(MANIFEST_SCRIPT), str(root), "--version", f"v{version}",
         "--output", str(root / "release-manifest.json")],
        check=True,
    )
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    return {
        "notes": (work / "notes.md").read_text(encoding="utf-8"),
        "incomplete": (work / "incomplete.txt").read_text(encoding="utf-8").splitlines(),
        "published": {path.name for path in root.rglob("*") if path.is_file()},
        "manifest": {Path(entry["path"]).name for entry in manifest["files"]},
    }


@pytest.mark.parametrize(
    "environment",
    [
        EVERYTHING,
        {"OPENPROGRAM_UPDATE_KEY_TRUSTED": "true"},
        {**EVERYTHING, "OPENPROGRAM_RELEASE_WINDOWS_ARM64": "true"},
    ],
    ids=["signed", "no-certificates", "with-windows-arm64"],
)
def test_a_release_whose_jobs_all_passed_carries_an_installer_for_every_planned_platform(
    environment: dict[str, str], tmp_path: Path,
) -> None:
    version, notes = _latest_notes()
    plan = _plan(environment)
    assert {(row["platform"], row["arch"]) for row in plan["desktop"]} >= {
        ("mac", "arm64"), ("mac", "x86_64"), ("win", "x86_64"), ("linux", "x86_64"), ("linux", "arm64"),
    }
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    release = _assemble(root, plan, version, tmp_path, notes)

    assert release["incomplete"] == []
    for row in plan["desktop"]:
        key = (row["platform"], row["arch"])
        assert key in INSTALLERS, f"the matrix builds {key}; name its installer in INSTALLERS"
        suffixes, main = INSTALLERS[key]
        for suffix in suffixes:
            name = _installer_name(version, suffix, row["signing"])
            assert name in release["published"], f"{key} has no {suffix} in the release"
            assert name in release["manifest"]
        if row["layout"] == "thin":
            assert f"version-{row['runtime_platform']}-{row['arch']}.tar.zst" in release["published"]
        assert _installer_name(version, main, row["signing"]) in release["published"]
    for row in plan["runtime"]:
        extension = "zip" if row["platform"] == "windows" else "tar.gz"
        assert f"OpenProgram-{version}-runtime-{row['platform']}-{row['arch']}.{extension}" in release["published"]
    # One file per download: no version-free copy and no per-platform checksum
    # file; release-manifest.json carries every file's SHA-256.
    assert not [name for name in release["published"]
                if name.startswith("SHA256SUMS-") or re.fullmatch(r"OpenProgram-[a-z]+-[A-Za-z0-9_]+\.\w+", name)]
    assert release["published"] - {"release-manifest.json"} == release["manifest"]
    # The App installs Office from this file of its own version's release.
    assert [name for name in release["published"] if name.startswith("OfficeAssets-")] == [_office_name()]

    # Every download the notes offer exists, the Windows installer among them.
    offered = re.findall(rf"/releases/download/v{re.escape(version)}/([^)\s]+)\)", release["notes"])
    assert set(offered) <= release["published"] | {"release-manifest.json"}
    windows = _installer_name(version, "win-x64.exe", dict(
        ((row["platform"], row["arch"]), row["signing"]) for row in plan["desktop"]
    )[("win", "x86_64")])
    assert windows in offered and not any(name.startswith("SHA256SUMS-") for name in offered)
    assert ("Run anyway" in release["notes"]) == windows.endswith("-unsigned.exe")
    _, _, left_out = release["notes"].partition("## Not in this release\n\n")
    left_out = left_out.partition("\n## ")[0]
    assert [line[2:] for line in left_out.splitlines() if line.startswith("- ")] == plan["omitted"]


def _break(root: Path, row: dict, how: str) -> None:
    directory = root / _desktop_directory(row)
    if how == "job failed":
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
    elif how == "no installer uploaded":
        # The leg uploaded something, but not the file users install with.
        installer = INSTALLERS[(row["platform"], row["arch"])][1].rpartition(".")[2]
        for path in directory.iterdir():
            if path.suffix == f".{installer}":
                path.unlink()
    else:
        manifest = root / "official-update" / "update-manifest.json"
        content = json.loads(manifest.read_text(encoding="utf-8"))
        del content["platforms"][f"{row['runtime_platform']}-{row['arch']}"]
        manifest.write_text(json.dumps(content), encoding="utf-8")


@pytest.mark.parametrize("how", ["job failed", "no installer uploaded", "update files missing"])
def test_a_planned_platform_without_an_installer_is_named(how: str, tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan({**EVERYTHING, "OPENPROGRAM_RELEASE_WINDOWS_ARM64": "true"})
    labels = {
        ("mac", "arm64"): "macOS Apple Silicon App", ("mac", "x86_64"): "macOS Intel App",
        ("win", "x86_64"): "Windows x64 App", ("win", "arm64"): "Windows on Arm App",
        ("linux", "x86_64"): "Linux x86_64 App", ("linux", "arm64"): "Linux arm64 App",
    }
    for index, row in enumerate(plan["desktop"]):
        if how == "update files missing" and row["layout"] != "thin":
            continue
        work = tmp_path / str(index)
        root = work / "release-artifacts"
        _built_tree(root, plan, version)
        _break(root, row, how)
        release = _assemble(root, plan, version, work, notes)
        key = (row["platform"], row["arch"])
        assert len(release["incomplete"]) == 1, release["incomplete"]
        assert release["incomplete"][0].startswith(f"{labels[key]}: ")
        assert release["incomplete"][0] in release["notes"]
        # Nothing of a platform that has no installer is published, and the rest is untouched.
        assert not (root / _desktop_directory(row)).exists()
        for other in plan["desktop"]:
            if other is not row:
                assert (root / _desktop_directory(other)).is_dir()


def test_a_runtime_job_that_uploaded_no_archive_is_named(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan(EVERYTHING)
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    (root / "product-runtime-windows-x86_64" / f"OpenProgram-{version}-runtime-windows-x86_64.zip").unlink()
    release = _assemble(root, plan, version, tmp_path, notes)
    assert release["incomplete"] == ["Windows x64 command-line runtime: its build left no archive."]
    assert not (root / "product-runtime-windows-x86_64").exists()


def _drop_update_platform(root: Path, key: str) -> None:
    manifest = root / "official-update" / "update-manifest.json"
    content = json.loads(manifest.read_text(encoding="utf-8"))
    del content["platforms"][key]
    manifest.write_text(json.dumps(content), encoding="utf-8")


def test_update_files_missing_for_a_platform_whose_app_does_not_read_them_are_named(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan(EVERYTHING)
    assert plan["update_files"] is True
    full = [row for row in plan["desktop"] if row["layout"] == "full"]
    assert {(row["platform"], row["arch"]) for row in full} == {("mac", "arm64"), ("mac", "x86_64")}
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    _drop_update_platform(root, "macos-arm64")
    release = _assemble(root, plan, version, tmp_path, notes)
    assert release["incomplete"] == ["macOS Apple Silicon update files: could not be built."]
    assert release["incomplete"][0] in release["notes"]
    # The App and the runtime do not depend on them, so both still ship.
    assert f"OpenProgram-{version}-mac-arm64.dmg" in release["published"]
    assert f"OpenProgram-{version}-runtime-macos-arm64.tar.gz" in release["published"]


def test_update_files_that_could_not_be_signed_are_named_for_every_platform(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan(EVERYTHING)
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    for path in (root / "official-update").iterdir():
        path.unlink()
    (root / "official-update").rmdir()
    release = _assemble(root, plan, version, tmp_path, notes)
    # One line per platform: the thin App's line already says why.
    assert sorted(release["incomplete"]) == sorted([
        "macOS Apple Silicon update files: could not be built.",
        "macOS Intel update files: could not be built.",
        "Windows x64 App: its update files could not be built, so it could never update.",
        "Linux x86_64 App: its update files could not be built, so it could never update.",
        "Linux arm64 App: its update files could not be built, so it could never update.",
    ])


def test_no_trusted_update_key_plans_no_update_files(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan({})
    assert plan["update_files"] is False
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    assert not (root / "official-update").exists()
    assert _assemble(root, plan, version, tmp_path, notes)["incomplete"] == []


def test_what_a_switch_or_a_missing_key_leaves_out_is_not_an_incomplete_release(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan({"OPENPROGRAM_RELEASE_DESKTOP_LINUX": "false"})
    assert any(item.startswith("Windows App:") for item in plan["omitted"])
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    release = _assemble(root, plan, version, tmp_path, notes)
    assert release["incomplete"] == []
    assert "## Not in this release" in release["notes"]
    assert "win-x64" not in release["notes"]


def test_the_publish_job_goes_red_after_publishing_an_incomplete_release() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    publish = workflow[workflow.index("  publish:\n"):]
    assemble = publish.index("release-matrix.py assemble")
    assert '--incomplete-output "$RUNNER_TEMP/release-incomplete.txt"' in publish[assemble:assemble + 500]
    released = publish.index("- name: Publish GitHub Release")
    verified = publish.index("- name: Verify public latest Release entry")
    check = publish.index("- name: Check every planned package is in the release")
    assert assemble < released < verified < check, "the check must not stop the other platforms from publishing"
    body = publish[check:]
    assert 'if [ -s "$incomplete" ]; then' in body and "exit 1" in body
    assert "continue-on-error" not in body and "if: " not in body
    # Every file an installer leg makes is in the upload, so the tree above matches the jobs.
    desktop = workflow[workflow.index("  desktop:\n"):workflow.index("  cli-installer:\n")]
    for pattern in ("*.dmg", "*.zip", "OpenProgram-*.exe", "*.AppImage", "*.deb"):
        assert f"apps/desktop/dist/{pattern}\n" in desktop
    assert "name: desktop-${{ matrix.layout }}-${{ matrix.platform }}-${{ matrix.arch }}" in desktop
    # Each file is uploaded once: no per-platform checksum file, no version-free copy.
    assert "SHA256SUMS-" not in workflow and "release-links" not in workflow


def test_the_windows_install_check_names_a_release_without_windows_files() -> None:
    smoke = (ROOT / ".github/workflows/windows-install-smoke.yml").read_text(encoding="utf-8")
    download = smoke[smoke.index("- name: Download the release files"):smoke.index("- name: Windows PowerShell 5.1")]
    # The patterns are the names the release gives its Windows files, signed or not.
    assert '--pattern "OpenProgram-*-runtime-windows-x86_64.zip*" --pattern "OpenProgram-*-win-x64*.exe"' in download
    assert 'foreach ($Kind in "zip", "exe")' in download
    assert "throw \"$env:TAG has no Windows $Kind" in download


def test_a_rehearsal_builds_and_checks_everything_and_publishes_nothing() -> None:
    yaml = pytest.importorskip("yaml")
    text = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    inputs = workflow[True]["workflow_dispatch"]["inputs"]  # YAML reads the key `on` as true
    assert inputs["rehearse"]["type"] == "boolean" and inputs["rehearse"]["default"] is False
    # A tag push has no inputs, so it builds the tag; a rehearsal builds the commit it started on.
    assert workflow["env"]["RELEASE_REF"] == "${{ inputs.rehearse && github.sha || inputs.tag || github.ref_name }}"
    assert workflow["env"]["RELEASE_TAG"] == "${{ inputs.tag || github.ref_name }}"
    checkouts = [
        step for job in workflow["jobs"].values() for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == len(workflow["jobs"])
    assert all(step["with"]["ref"] == "${{ env.RELEASE_REF }}" for step in checkouts)

    steps = {step["name"]: step for step in workflow["jobs"]["publish"]["steps"] if "name" in step}
    public = {"Publish GitHub Release", "Verify public latest Release entry"}
    for name, step in steps.items():
        if name in public:
            assert step["if"] == "${{ !inputs.rehearse }}", name
        elif name == "List what a release of this commit would carry":
            assert step["if"] == "${{ inputs.rehearse }}"
        else:
            assert "if" not in step, f"a rehearsal must also run: {name}"
    assert "gh release" not in "".join(
        str(step.get("run", "")) for name, step in steps.items() if name not in public
    )
    # Before publishing, a release is only read: the Office job downloads the
    # copy the owner put on the tag's draft.
    assert re.findall(r"gh release (\w+)", text[:text.index("  publish:\n")]) == ["download"]


def test_the_windows_install_check_can_take_a_rehearsal_runs_files() -> None:
    yaml = pytest.importorskip("yaml")
    smoke = yaml.safe_load((ROOT / ".github/workflows/windows-install-smoke.yml").read_text(encoding="utf-8"))
    assert "run_id" in smoke[True]["workflow_dispatch"]["inputs"]
    job = smoke["jobs"]["release-install"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert job["env"]["RUN_ID"] == "${{ inputs.run_id }}"
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    download = steps["Download the release files"]["run"]
    assert "gh run download $env:RUN_ID" in download
    # The artifact names are the ones the Release workflow uploads for Windows x64.
    assert "--name product-runtime-windows-x86_64 --name desktop-thin-win-x86_64" in download
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "name: product-runtime-${{ matrix.platform }}-${{ matrix.arch }}" in release
    assert "name: desktop-${{ matrix.layout }}-${{ matrix.platform }}-${{ matrix.arch }}" in release
    assert steps["Windows PowerShell 5.1 under Restricted runs the piped bootstrap"]["if"] == "inputs.run_id == ''"
    for name in ("The release ZIP installs and starts a healthy worker",
                 "Silent installer, first launch to a healthy worker, older then newer"):
        assert "if" not in steps[name]


def test_a_release_without_the_office_archive_is_refused(tmp_path: Path) -> None:
    version, notes = _latest_notes()
    plan = _plan(EVERYTHING)
    root = tmp_path / "release-artifacts"
    _built_tree(root, plan, version)
    (root / "office-assets" / _office_name()).unlink()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "assemble", str(root), "--version", f"v{version}",
         "--plan", json.dumps(plan), "--notes", str(notes), "--notes-output", str(tmp_path / "notes.md")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and "the Office archive is missing" in result.stderr
