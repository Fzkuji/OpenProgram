"""Where memory lives on disk.

The workspace keeps the location the previous memory layer used, so an
existing installation finds its memory in the same place. What is inside
it changed: ``sources/`` and ``topics/`` in place of ``journal/`` and
``wiki/``, with ``core.md`` unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def root() -> Path:
    """The memory workspace, created on first use."""
    from openprogram.paths import get_state_dir

    path = get_state_dir() / "memory"
    path.mkdir(parents=True, exist_ok=True)
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
    from .scriptorium.workspace_layout import runtime_dir

    path = runtime_dir(root())
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def ensure() -> Path:
    """Create the workspace skeleton if it is not there yet."""
    base = root()
    _set_aside_superseded(base)
    for name in ("topics", "sources"):
        (base / name).mkdir(parents=True, exist_ok=True)
    core_file = base / "core.md"
    if not core_file.exists():
        core_file.write_text("# Core\n", encoding="utf-8")
    return base
