"""Warn when the installed git hooks lag the versioned ones.

Hooks live in ``.git/hooks``, outside the repository, and are synced by
``scripts/git-hooks/install.sh`` on every deploy and publish.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import subprocess

HOOKS = ("pre-commit", "pre-push", "post-merge")


def stale_hooks(root: Path) -> list[str]:
    """Names of the hooks whose installed copy differs from the repository's."""
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return []
    stale = []
    for hook in HOOKS:
        versioned = root / "scripts" / "git-hooks" / hook
        installed = Path(common) / "hooks" / hook
        if not versioned.is_file():
            continue
        if not installed.is_file() or installed.read_bytes() != versioned.read_bytes():
            stale.append(hook)
    return stale


def check(corpus) -> Iterable:
    from scripts.docs_site.governance.model import Finding
    from scripts.testing.affected_tests import is_linked_worktree

    # A branch's hooks differ until it lands; only the main checkout can say they lag.
    if is_linked_worktree(corpus.scope.root):
        return
    for hook in stale_hooks(corpus.scope.root):
        yield Finding(
            rule="wiring.stale-hook",
            severity="warning",
            path=f"scripts/git-hooks/{hook}",
            line=0,
            message=f"the installed {hook} hook differs from this version.",
            fix="run scripts/git-hooks/install.sh",
        )
