"""Each canonical file responsibility must import without the facade first."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


MODULES = (
    "openprogram.interfaces.server.ws_actions.files.shared",
    "openprogram.interfaces.server.ws_actions.files.query",
    "openprogram.interfaces.server.ws_actions.files.mutations",
    "openprogram.interfaces.server.ws_actions.files.ws",
    "openprogram.interfaces.server.ws_actions.files",
    "openprogram.interfaces.server.ws_actions.turn_files.shared",
    "openprogram.interfaces.server.ws_actions.turn_files.diff_shared",
    "openprogram.interfaces.server.ws_actions.turn_files.scope",
    "openprogram.interfaces.server.ws_actions.turn_files.diff",
    "openprogram.interfaces.server.ws_actions.turn_files.history",
    "openprogram.interfaces.server.ws_actions.turn_files",
)


def test_file_responsibility_modules_import_in_clean_processes() -> None:
    repo = Path(__file__).resolve().parents[4]
    environment = os.environ.copy()
    paths = [str(repo / "vendor"), str(repo)]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    for module in MODULES:
        completed = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=repo,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, (
            f"independent import failed for {module}: {completed.stderr}"
        )
