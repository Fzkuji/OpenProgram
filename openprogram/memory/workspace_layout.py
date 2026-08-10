"""The names the runtime owns inside a memory workspace.

A workspace holds the memory — `core.md`, `topics/`, `sources/` and the
derived views — and beside it a small runtime area: cursors, the write lock,
staged backups, retrieval caches. A workspace kept under version control also
holds that tool's own directory. Neither is memory: both are hidden from
listings, left out of the revision, and never writable by a patch.

The runtime area is top level. A file inside `topics/` is authored memory
whatever it is called, so it lists and it counts toward the revision.

Workspaces built before the project took its current name carry the runtime
directory under its former name. They keep it: a stored run's hash covers
every byte of its workspace, so renaming a directory inside one would
invalidate the record it was published with. Anything opened for writing uses
whichever name that workspace already has, and a new workspace gets the
current one.
"""

from __future__ import annotations

from pathlib import Path

# Persisted directory names. Both values are historical — they date from
# earlier names the subsystem carried — and they stay as written: a stored
# run's hash covers every byte of its workspace, so renaming a directory
# inside one would invalidate the record it was published with.
RUNTIME_DIR = ".scriptorium"
LEGACY_RUNTIME_DIRS = (".nativemem",)
RUNTIME_DIR_NAMES = (RUNTIME_DIR, *LEGACY_RUNTIME_DIRS)
STATE_FILE = "runtime.json"
# A workspace may be a repository of its own; `--git-commit on` expects that.
VERSION_CONTROL_DIRS = (".git",)

TEMPORARY_PREFIX = "scriptorium-"  # historical value; see RUNTIME_DIR above


def is_runtime_name(name: str) -> bool:
    """True for the runtime directory and anything it stages beside itself."""
    return any(
        name == known or name.startswith(f"{known}-")
        for known in RUNTIME_DIR_NAMES
    )


def is_internal_path(relative: Path) -> bool:
    """True for a workspace-relative path that holds no memory."""
    parts = relative.parts
    if not parts:
        return False
    return is_runtime_name(parts[0]) or parts[0] in VERSION_CONTROL_DIRS


def is_state_file(relative: Path) -> bool:
    """True for the cursor file, the one runtime file a revision counts.

    A moved cursor is a change in what has been written, so it belongs to the
    revision even though the rest of the runtime area does not.
    """
    parts = relative.parts
    return (
        len(parts) == 2
        and parts[0] in RUNTIME_DIR_NAMES
        and parts[1] == STATE_FILE
    )


def resolve_within(root: Path | str, relative: str) -> Path | None:
    """Resolve ``relative`` inside ``root``, or None if it lands outside.

    A shared string prefix is not containment: ``topics-private/`` begins
    with the same characters as ``topics/`` and is a different directory.
    Resolving first is what collapses ``..`` and follows symlinks, so the
    answer is about where the path actually lands.
    """
    base = Path(root).resolve()
    target = (base / relative).resolve()
    return target if target.is_relative_to(base) else None


def runtime_dir(memory_dir: Path | str) -> Path:
    """This workspace's runtime directory, keeping the name it already has."""
    root = Path(memory_dir)
    for legacy in LEGACY_RUNTIME_DIRS:
        if (root / legacy).is_dir():
            return root / legacy
    return root / RUNTIME_DIR


def has_runtime_dir(memory_dir: Path | str) -> bool:
    """True when this directory already carries a runtime area."""
    root = Path(memory_dir)
    return any((root / name).is_dir() for name in RUNTIME_DIR_NAMES)
