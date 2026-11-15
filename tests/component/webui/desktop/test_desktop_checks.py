"""Bridge: run the desktop-side node check harnesses as part of pytest.

Both scripts are real harnesses, but nothing used to run them — so
check-webtab-navigation.js sat broken on a clean checkout for a long time
without anyone noticing. Running them here (and from `npm run check` in
apps/desktop/package.json) means they fail loudly instead of rotting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[4] / "apps" / "desktop"
SCRIPTS = (
    "check-webtab-navigation.js",
    "check-tab-transfer-store.js",
    "check-window-state.js",
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_desktop_check_script_passes(script: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, f"tests/{script}"],
        cwd=DESKTOP,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"apps/desktop/tests/{script} failed:\n{result.stdout}\n{result.stderr}"
    )


def test_check_scripts_are_wired_into_npm_check():
    """The harnesses must stay reachable from `npm run check` too, so a
    desktop-only workflow catches them without going through pytest."""
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]
    aggregate = scripts.get("check", "")
    for script in SCRIPTS:
        name = next(
            (key for key, value in scripts.items() if script in value and key != "check"),
            None,
        )
        assert name, f"apps/desktop/package.json has no check:* script running {script}"
        assert name in aggregate, f"`npm run check` does not run {name}"


def _packaged_files() -> set[str]:
    """build.files expanded: one file, or a whole "folder/**" (dotfiles included)."""
    files: set[str] = set()
    for entry in json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["build"]["files"]:
        if entry.endswith("/**"):
            files.update(
                path.relative_to(DESKTOP).as_posix()
                for path in (DESKTOP / entry[:-3]).rglob("*")
                if path.is_file()
            )
        else:
            files.add(entry)
    return files


def _heredoc(script: str, opener: str, terminator: str) -> str:
    text = (DESKTOP.parents[1] / script).read_text(encoding="utf-8")
    start = text.index(opener) + len(opener)
    return text[start : text.index(f"\n{terminator}\n", start)]


def test_packaging_scripts_list_exactly_the_packaged_files():
    """The file lists both asar repackers build are the build.files expansion."""
    package = str(DESKTOP / "package.json")
    lister = _heredoc(
        "apps/desktop/scripts/package-incremental-app.sh",
        """<<'NODE' >"$work_dir/desktop-files.txt"\n""",
        "NODE",
    )
    node = shutil.which("node")
    if node is not None:
        result = subprocess.run(
            [node, "-", package], input=lister, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr
        assert set(result.stdout.split()) == _packaged_files()
    lister = _heredoc(
        "scripts/release/pack-desktop-archive.sh",
        """"$local_python" - "$repo_root/apps/desktop/package.json" <<'PY'\n""",
        "PY",
    )
    result = subprocess.run(
        [sys.executable, "-", package], input=lister, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == _packaged_files()
