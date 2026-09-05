"""Each canonical file responsibility must import without the facade first."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


MODULES = (
    "openprogram.webui.ws_actions.files_shared",
    "openprogram.webui.ws_actions.files_query",
    "openprogram.webui.ws_actions.files_mutations",
    "openprogram.webui.ws_actions.files_ws",
    "openprogram.webui.ws_actions.files",
    "openprogram.webui.ws_actions.turn_files_shared",
    "openprogram.webui.ws_actions.turn_files_diff_shared",
    "openprogram.webui.ws_actions.turn_files_scope",
    "openprogram.webui.ws_actions.turn_files_diff",
    "openprogram.webui.ws_actions.turn_files_history",
    "openprogram.webui.ws_actions.turn_files",
)


def test_file_responsibility_modules_import_in_clean_processes() -> None:
    repo = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    paths = [str(repo / "vendor"), str(repo), str(repo / "apps/server")]
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
