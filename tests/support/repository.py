from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def tracked_python_files(root: Path) -> tuple[Path, ...]:
    """Return tracked Python files below a repository-owned directory."""
    relative = Path(root).resolve().relative_to(ROOT)
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", relative.as_posix()],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        ROOT / path.decode()
        for path in result.stdout.split(b"\0")
        if path.endswith(b".py")
    )
