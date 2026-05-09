"""Git-checkout updater: ``git fetch`` then ``git pull`` if behind upstream.

Returns enough info for callers to decide whether to print a banner.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _git(repo: Path, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    if shutil.which("git") is None:
        return 127, "git not found"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except OSError as e:
        return 1, str(e)
    return out.returncode, (out.stdout + out.stderr).strip()


def current_branch(repo: Path) -> Optional[str]:
    rc, msg = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not msg or msg == "HEAD":
        return None
    return msg


def working_tree_clean(repo: Path) -> bool:
    """True if the working tree has no uncommitted changes.

    We refuse to auto-pull a dirty tree — pulling on top of local edits
    can produce merge conflicts that the user has no good way to
    resolve from a startup background task.
    """
    rc, msg = _git(repo, "status", "--porcelain")
    if rc != 0:
        return False
    return msg == ""


def upstream_ref(repo: Path) -> Optional[str]:
    """Return the configured upstream (e.g. ``origin/main``), or None."""
    rc, msg = _git(repo, "rev-parse", "--abbrev-ref", "@{upstream}")
    if rc != 0 or not msg:
        return None
    return msg


def commits_behind_ahead(repo: Path, upstream: str) -> Optional[tuple[int, int]]:
    """Return (behind, ahead) relative to upstream, or None on error."""
    rc, msg = _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    if rc != 0:
        return None
    parts = msg.split()
    if len(parts) != 2:
        return None
    try:
        ahead = int(parts[0])
        behind = int(parts[1])
        return behind, ahead
    except ValueError:
        return None


def head_commit(repo: Path) -> Optional[str]:
    rc, msg = _git(repo, "rev-parse", "HEAD")
    if rc != 0 or not msg:
        return None
    return msg


def fetch(repo: Path) -> bool:
    rc, _ = _git(repo, "fetch", "--quiet", timeout=60.0)
    return rc == 0


def pull(repo: Path) -> tuple[bool, str]:
    """Run ``git pull --ff-only``. Returns (ok, message)."""
    rc, msg = _git(repo, "pull", "--ff-only", "--quiet", timeout=120.0)
    return rc == 0, msg
