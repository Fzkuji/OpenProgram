"""Bridge: run the desktop-side node check harnesses as part of pytest.

Both scripts are real harnesses, but nothing used to run them — so
check-webtab-navigation.js sat broken on a clean checkout for a long time
without anyone noticing. Running them here (and from `npm run check` in
desktop/package.json) means they fail loudly instead of rotting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

DESKTOP = Path(__file__).resolve().parents[2] / "desktop"
SCRIPTS = ("check-webtab-navigation.js", "check-tab-transfer-store.js")


@pytest.mark.parametrize("script", SCRIPTS)
def test_desktop_check_script_passes(script: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, f"scripts/{script}"],
        cwd=DESKTOP,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        f"desktop/scripts/{script} failed:\n{result.stdout}\n{result.stderr}"
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
        assert name, f"desktop/package.json has no check:* script running {script}"
        assert name in aggregate, f"`npm run check` does not run {name}"
