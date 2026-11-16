"""The contributors script runs as a file, the way the README workflow runs it."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_runs_as_a_file_the_way_the_workflow_runs_it(tmp_path: Path) -> None:
    # `python3 scripts/publishing/update_readme_contributors.py` puts only its own folder on the path.
    scripts = ROOT / "scripts" / "publishing"
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         f"import runpy, sys; sys.path.insert(0, {str(scripts)!r}); "
         f"m = runpy.run_path({str(scripts / 'update_readme_contributors.py')!r}); "
         "print(m['is_listed_contributor']({'login': 'FuzzyKuji', 'type': 'User'}))"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
