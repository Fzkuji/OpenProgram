"""User package discovery through a fresh public CLI process."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def _workflow(root: Path, name: str, *, commit: bool) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "from pathlib import Path\n"
        "Path(__file__).with_name('imported.flag').touch()\n"
        f"from .workflow import {name}\n"
        f"PROGRAM_ENTRIES = [{name}]\n",
        encoding="utf-8",
    )
    (package / "workflow.py").write_text(
        f"def {name}(task: str) -> str:\n"
        '    """Prepare a CLI report."""\n'
        "    raise AssertionError('listing must not execute the workflow')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=package, check=True)
    if commit:
        subprocess.run(["git", "add", "--all"], cwd=package, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-qm", "First version"],
            cwd=package, check=True,
        )
    return package


def test_programs_list_loads_committed_user_packages_only(tmp_path: Path):
    root = tmp_path / ".openprogram" / "programs"
    committed = _workflow(root, "cli_report", commit=True)
    pending = _workflow(root, "cli_pending_report", commit=False)
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    env.pop("OPENPROGRAM_PROFILE", None)
    result = subprocess.run(
        [sys.executable, "-m", "openprogram", "programs", "list", "--json"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    # The package runs from its checked copy, not from the working directory.
    assert list((tmp_path / ".openprogram" / "programs-cache" / "cli_report").glob("*/imported.flag"))
    assert not (committed / "imported.flag").exists()
    assert not (pending / "imported.flag").exists()
    assert not (tmp_path / ".openprogram" / "programs-cache" / "cli_pending_report").exists()
    listing = json.loads(result.stdout)
    assert listing["root"] == str(root)
    packages = {row["name"]: row for row in listing["programs"] if row["kind"] == "package"}
    assert packages["cli_report"]["state"] == "loaded"
    assert packages["cli_report"]["entries"] == ["cli_report"]
    assert packages["cli_pending_report"]["state"] == "pending"
    assert packages["cli_pending_report"]["entries"] == []
    assert "openprogram programs check" in packages["cli_pending_report"]["note"]
    kinds = {row["kind"] for row in listing["programs"]}
    assert {"tool", "application", "package"} <= kinds

    text = subprocess.run(
        [sys.executable, "-m", "openprogram", "programs", "list"],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert text.returncode == 0, text.stderr
    assert "cli_report" in text.stdout and "cli_pending_report" in text.stdout
