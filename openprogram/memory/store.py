"""Where memory lives on disk.

The workspace keeps the location the previous memory layer used, so an
existing installation finds its memory in the same place. What is inside
it changed: ``sources/`` and ``topics/`` in place of ``journal/`` and
``wiki/``. ``core.md`` kept its place at the root, and is now
rendered from ``topics/core.md`` rather than written.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def root() -> Path:
    """The memory workspace, created on first use."""
    from openprogram.paths import _restrict_to_owner, get_state_dir

    state = get_state_dir()
    path = state / "memory"
    path.mkdir(parents=True, exist_ok=True)
    # ``get_state_dir`` protects the profile root when it creates it, but
    # this is the call that brings it into being on a fresh install — the
    # mkdir above makes both levels — so the mode is set here too rather
    # than left to whichever call happened to come first.
    _restrict_to_owner(state, 0o700)
    _restrict_to_owner(path, 0o700)
    return path


def sources_dir() -> Path:
    return root() / "sources"


def topics_dir() -> Path:
    return root() / "topics"


def timeline_dir() -> Path:
    return root() / "timeline"


def core() -> Path:
    return root() / "core.md"


def state_dir() -> Path:
    """Bookkeeping that is about memory but is not memory.

    Inside the runtime directory, which the workspace revision ignores.
    A file that changed on every poll would otherwise look like a
    concurrent write to anything holding a revision.
    """
    from .workspace_layout import ensure_runtime_dir

    return ensure_runtime_dir(root())


_WORKSPACE_ID = re.compile(r"w-[0-9a-f]{8}")


def workspace_id() -> str:
    """Stable identity for markers written by this memory workspace."""
    path = state_dir() / "workspace-id"
    try:
        current = path.read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if _WORKSPACE_ID.fullmatch(current):
        return current

    from openprogram.store.session.git_session import atomic_write_text
    from .management.transaction import workspace_write_lock

    with workspace_write_lock(root()):
        try:
            current = path.read_text(encoding="utf-8").strip()
        except OSError:
            current = ""
        if not _WORKSPACE_ID.fullmatch(current):
            current = f"w-{uuid.uuid4().hex[:8]}"
            atomic_write_text(path, current + "\n")
    return current


# What the previous memory layer kept at the workspace root. None of it
# means anything to the current one, and left in place it would show up
# in every listing — one installation had 3934 wiki files.
_SUPERSEDED = ("journal", "wiki", ".state", "index.sqlite")


def _set_aside_superseded(base: Path) -> Path | None:
    """Move the previous layer's files out of the workspace, once.

    Moved rather than deleted, and to a sibling directory rather than a
    subdirectory: inside the workspace it would still be listed, and
    deleting someone's notes to make room for a new format is not a
    migration.
    """
    present = [name for name in _SUPERSEDED if (base / name).exists()]
    if not present:
        return None
    archive = base.parent / f"{base.name}-superseded"
    archive.mkdir(parents=True, exist_ok=True)
    for name in present:
        target = archive / name
        if target.exists():
            # A previous pass already saved a copy; leave it as the
            # record and drop the leftover rather than merging blindly.
            continue
        (base / name).rename(target)
    logger.info(
        "memory: moved the previous layout (%s) to %s",
        ", ".join(present), archive,
    )
    return archive


_MEMORY_FILE_MODE = 0o600
_MEMORY_DIR_MODE = 0o700
_permissions_migrated: set[Path] = set()


def restrict_workspace_permissions(base: Path) -> int:
    """Make an existing workspace owner-only. Returns files changed.

    A workspace created before memory files were owner-only is still full
    of 0644 transcripts, and nothing rewrites a file that is never edited
    again — so the modes have to be corrected in place rather than waiting
    for the next write to do it.

    Symlinks are skipped rather than followed: ``chmod`` through one
    changes whatever it points at, and a link planted in the workspace
    would be a way to have this walk re-mode a file outside it.
    """
    changed = 0
    for path in [base, *base.rglob("*")]:
        try:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode & 0o777
            wanted = _MEMORY_DIR_MODE if path.is_dir() else _MEMORY_FILE_MODE
            if mode != wanted:
                path.chmod(wanted)
                changed += 1
        except OSError:
            # One unreadable file does not justify leaving the rest of the
            # workspace world-readable.
            continue
    return changed


def _migrate_permissions_once(base: Path) -> None:
    if base in _permissions_migrated:
        return
    _permissions_migrated.add(base)
    changed = restrict_workspace_permissions(base)
    if changed:
        logger.info(
            "memory: restricted %d workspace path(s) to owner-only", changed,
        )


def ensure() -> Path:
    """Create the workspace skeleton if it is not there yet."""
    base = root()
    _set_aside_superseded(base)
    for name in ("topics", "sources"):
        (base / name).mkdir(parents=True, exist_ok=True)
    _migrate_permissions_once(base)
    # Nothing seeds the always-on block. It is rendered from
    # ``topics/core.md``, so an empty workspace has no block, and a
    # placeholder written here would look like a master to the render and
    # let it replace a hand-written ``core.md`` that was never moved.
    return base
