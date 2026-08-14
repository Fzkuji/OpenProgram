from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]


def test_child_pytest_configuration_removes_temporary_home() -> None:
    env = os.environ.copy()
    env.pop("OPENPROGRAM_TEST_REAL_HOME", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, runpy; "
                "runpy.run_path('tests/conftest.py'); "
                "print(os.environ['HOME'])"
            ),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    child_home = Path(result.stdout.strip().splitlines()[-1])

    assert child_home.name.startswith("openprogram-test-home-")
    assert not child_home.exists()
