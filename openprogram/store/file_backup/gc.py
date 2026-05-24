"""GC policy for the file-backup store.

Cap the number of retained turn directories per session. Oldest are
evicted first. Bound is intentionally generous (100 turns) — the
backup files are typically hardlinks, so disk cost is low until a
turn actually modifies a unique file.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .paths import session_backup_root


MAX_TURNS = 100


def evict_old(session_dir: Path, max_turns: int = MAX_TURNS) -> int:
    """Drop the oldest turn directories beyond ``max_turns``.

    "Oldest" is by directory mtime — simple and good enough; the
    monotonic turn id would be sturdier but mtime is cheap and the
    cap is a soft hint, not a correctness invariant.

    Returns the count of directories removed.
    """
    root = session_backup_root(Path(session_dir))
    if not root.exists():
        return 0
    dirs = [p for p in root.iterdir() if p.is_dir()]
    if len(dirs) <= max_turns:
        return 0
    dirs.sort(key=lambda p: p.stat().st_mtime)
    to_remove = dirs[: len(dirs) - max_turns]
    n = 0
    for d in to_remove:
        try:
            shutil.rmtree(d)
            n += 1
        except OSError:
            continue
    return n
