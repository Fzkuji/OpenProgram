"""A release version is written into every place at once, and checked in all of them.

The Release workflow stops when any place lags: ``verify-release-version.py``
reads the project files and the installers' default, and ``uv --locked``
refuses a stale ``uv.lock``. ``publish-github.sh --tag`` refuses with the
same list before the tag leaves the machine (test_release_workflow.py).
"""
from __future__ import annotations

from pathlib import Path
import runpy
import shutil
import subprocess
import sys

import pytest

from ._support import ROOT

SCRIPT = ROOT / "scripts/release/release-version.py"


@pytest.fixture
def release_version() -> dict:
    return runpy.run_path(str(SCRIPT))


def _copy(tmp_path: Path, release_version: dict) -> Path:
    for place in release_version["PLACES"]:
        target = tmp_path / place.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / place.path, target)
    return tmp_path


def test_every_place_in_this_tree_names_one_version(release_version) -> None:
    found = release_version["versions"](release_version["_from_tree"](ROOT))
    assert None not in found.values(), found
    assert len(set(found.values())) == 1, found


def test_the_places_are_the_ones_the_release_build_enforces(release_version) -> None:
    paths = {place.path for place in release_version["PLACES"]}
    verifier = (ROOT / "scripts/release/verify-release-version.py").read_text(encoding="utf-8")
    for read in ('"pyproject.toml"', '"package.json"', '"install-release.sh"', '"install-release.ps1"'):
        assert read in verifier
    assert {"pyproject.toml", "apps/desktop/package.json", "scripts/release/install-release.sh",
            "scripts/release/install-release.ps1", "uv.lock", "package-lock.json"} == paths
    staging = (ROOT / "scripts/release/stage-release-assets.sh").read_text(encoding="utf-8")
    assert "run --isolated --locked" in staging, "uv.lock is a version place because the build runs --locked"


def test_set_moves_every_place_and_only_the_version_lines(tmp_path: Path, release_version) -> None:
    root = _copy(tmp_path, release_version)
    before = {place.path: (root / place.path).read_text(encoding="utf-8") for place in release_version["PLACES"]}
    assert release_version["set_version"]("9.8.7", root)
    assert set(release_version["versions"](release_version["_from_tree"](root)).values()) == {"9.8.7"}
    for place in release_version["PLACES"]:
        old, new = before[place.path].splitlines(), (root / place.path).read_text(encoding="utf-8").splitlines()
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        assert len(old) == len(new) and len(changed) == 1, place.path
        assert "9.8.7" in changed[0][1]
        if place.parsed is not None:
            assert place.parsed((root / place.path).read_text(encoding="utf-8")) == "9.8.7"


def test_set_changes_nothing_when_a_place_is_missing(tmp_path: Path, release_version) -> None:
    root = _copy(tmp_path, release_version)
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="uv.lock; nothing was changed"):
        release_version["set_version"]("9.8.7", root)
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == pyproject
    with pytest.raises(SystemExit, match="looks like 1.2.3"):
        release_version["set_version"]("9.8", root)


def test_check_names_each_place_that_lags(tmp_path: Path) -> None:
    current = runpy.run_path(str(SCRIPT))["versions"](runpy.run_path(str(SCRIPT))["_from_tree"](ROOT))
    version = next(iter(current.values()))
    major, minor, patch = (int(part) for part in version.split("."))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "check", f"v{major}.{minor}.{patch + 1}"], capture_output=True, text=True,
    )
    assert result.returncode == 1
    for path in current:
        assert f"{path} says {version}" in result.stderr
    assert subprocess.run([sys.executable, str(SCRIPT), "check", f"v{version}"]).returncode == 0
