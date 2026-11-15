"""Which Topic files the Runtime installed, and which came from outside.

Every install records, in ``<runtime-dir>/installed.json``, each Topic
file's hash and the block IDs it holds, with its origin:

- ``runtime``: written by one of our transactions (a writer, the
  organizer, a chat edit, a save in Settings), or edited elsewhere since
  in a way that kept every block ID we installed — a person's edit;
- ``outside``: a file that appeared without us, or one changed elsewhere
  so that block IDs we installed are gone — another tool's file.

A file our transaction did not touch keeps the origin it had.

``outside_paths`` answers which files are outside now. An outside file is
read and recalled like any other, but no writer, normalization step or
organizer changes, moves or links into it; a person saving it in
Settings is our transaction touching it, so it becomes ``runtime``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from ..markdown.parser import read_topic_file, topic_files
from ..workspace_layout import ensure_runtime_dir

logger = logging.getLogger(__name__)

INSTALLED_FILE = "installed.json"
RUNTIME = "runtime"
OUTSIDE = "outside"


def _path(memory_dir: Path) -> Path:
    return ensure_runtime_dir(memory_dir) / INSTALLED_FILE


def snapshot(topics: Path) -> dict[str, dict[str, Any]]:
    """Each Topic file under ``topics``: its hash, its own block IDs, and
    whether every paragraph in it cites a chat message."""
    found: dict[str, dict[str, Any]] = {}
    for path in topic_files(topics):
        relative = path.relative_to(topics).as_posix()
        try:
            data = path.read_bytes()
        except OSError:
            continue
        units, _problem = read_topic_file(path, topics, data=data)
        found[relative] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "ids": sorted({u.memory_id for u in units if not u.derived_id}),
            # Every paragraph cites a chat message: the format our writers
            # leave (older files cite under legacy ``mem_`` labels).
            "cited": bool(units) and all(u.source_refs for u in units),
        }
    return found


def load(memory_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(_path(Path(memory_dir)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    files = payload.get("files") if isinstance(payload, dict) else None
    return {
        str(name): entry for name, entry in (files or {}).items()
        if isinstance(entry, dict) and isinstance(entry.get("sha256"), str)
    }


def _origin_before(
    relative: str,
    current: dict[str, Any],
    recorded: dict[str, Any] | None,
) -> str:
    """The origin of a file as it stood before this install."""
    if recorded is None:
        # Never recorded: a workspace from before this record keeps its
        # own files, the ones in the format its writer leaves; anything
        # else came from outside.
        return RUNTIME if current["cited"] else OUTSIDE
    if recorded.get("origin") == OUTSIDE:
        return OUTSIDE
    if recorded["sha256"] == current["sha256"]:
        return RUNTIME
    kept = set(recorded.get("ids") or []) <= set(current["ids"])
    return RUNTIME if kept else OUTSIDE


def record_install(
    memory_dir: Path,
    before: dict[str, dict[str, Any]],
) -> None:
    """Record the Topic files an install just left, given ``before``, the
    snapshot of the committed files it replaced."""
    memory_dir = Path(memory_dir)
    try:
        recorded = {path_key(name): entry for name, entry in load(memory_dir).items()}
        after = snapshot(memory_dir / "topics")
        files = {}
        for relative, entry in after.items():
            previous = before.get(relative)
            if previous is None or previous["sha256"] != entry["sha256"]:
                origin = RUNTIME
            else:
                origin = _origin_before(
                    relative, previous, recorded.get(path_key(relative)),
                )
            files[relative] = {
                "sha256": entry["sha256"], "ids": entry["ids"], "origin": origin,
            }
        from openprogram.state.store.session.atomic_file import atomic_write_text

        atomic_write_text(
            _path(memory_dir),
            json.dumps({"version": 1, "files": files}, indent=2, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 - a record never fails an install
        logger.debug("memory: installed-file record not written: %s", exc)


# Where file names differ only in case name the same file.
_CASE_INSENSITIVE = sys.platform in ("darwin", "win32")


def path_key(relative: str) -> str:
    """How a Topic path is compared: case folded where the file system
    ignores case."""
    return relative.casefold() if _CASE_INSENSITIVE else relative


def outside_paths(memory_dir: Path) -> set[str]:
    """The Topic files (paths under ``topics/``) written outside
    OpenProgram, as they stand on disk now.

    A file never recorded is outside unless every paragraph in it is a
    cited block, the format our writers leave; a recorded file keeps its
    origin until its bytes change, and then stays ours only if it kept
    every block ID we installed.
    """
    memory_dir = Path(memory_dir)
    recorded = {path_key(name): entry for name, entry in load(memory_dir).items()}
    found: set[str] = set()
    for relative, current in snapshot(memory_dir / "topics").items():
        if _origin_before(relative, current, recorded.get(path_key(relative))) == OUTSIDE:
            found.add(relative)
    return found


def contains(paths: set[str], relative: str) -> bool:
    """Whether ``relative`` is one of ``paths``, by ``path_key``."""
    key = path_key(relative)
    return any(path_key(path) == key for path in paths)


__all__ = [
    "INSTALLED_FILE", "OUTSIDE", "RUNTIME", "contains", "load",
    "outside_paths", "path_key", "record_install", "snapshot",
]
