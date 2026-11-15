"""Direct edits the launcher set aside before a version started.

``bootstrap/restore_version.py`` writes ``unapplied/<stamp>/`` with the changed
files, a patch with repository-relative paths and a ``manifest.json``. The
worker tells the owner once, and the patch stays there for the Agent to bring
into a worktree and through the normal lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Callable

from .layout import Layout, valid_id
from openprogram.host._platform import filesystem

DIRECTORY = "unapplied"
MANIFEST = "manifest.json"
PATCH = "changes.patch"


@dataclass(frozen=True)
class Capture:
    stamp: str
    path: Path
    version: str
    changed: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    reported: bool

    @property
    def files(self) -> int:
        return len(self.changed) + len(self.added) + len(self.removed)

    @property
    def patch(self) -> Path:
        return self.path / PATCH


def _strings(value: object) -> tuple[str, ...]:
    return tuple(item for item in value if isinstance(item, str)) if isinstance(value, list) else ()


def _read(entry: Path) -> Capture | None:
    try:
        data = json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("version"), str):
        return None
    return Capture(
        stamp=entry.name, path=entry, version=data["version"],
        changed=_strings(data.get("changed")), added=_strings(data.get("added")),
        removed=_strings(data.get("removed")), reported=data.get("reported") is True,
    )


def all_captures(layout: Layout) -> list[Capture]:
    """Every saved direct edit, newest first."""
    directory = layout.root / DIRECTORY
    if not directory.is_dir():
        return []
    entries = (entry for entry in directory.iterdir() if entry.is_dir() and valid_id(entry.name))
    captures = [capture for capture in map(_read, entries) if capture is not None]
    return sorted(captures, key=lambda capture: capture.stamp, reverse=True)


def pending(layout: Layout) -> list[Capture]:
    """Saved direct edits the owner has not been told about."""
    return [capture for capture in all_captures(layout) if not capture.reported]


def notice_text(files: int) -> str:
    if files == 1:
        return "1 file was changed directly in the running version. It is saved and not applied. The version started clean."
    return f"{files} files were changed directly in the running version. They are saved and not applied. The version started clean."


def mark_reported(capture: Capture) -> None:
    path = capture.path / MANIFEST
    data = json.loads(path.read_text(encoding="utf-8"))
    data["reported"] = True
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        filesystem.replace_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def report_pending(layout: Layout, tell: Callable[[str, str], bool]) -> int:
    """Tell the owner about new direct edits, once. Returns how many files.

    A capture is marked only after ``tell`` succeeded, and ``tell`` is
    idempotent on its id, so a crash between the two cannot say it twice.
    """
    captures = pending(layout)
    files = sum(capture.files for capture in captures)
    if not captures or files == 0:
        for capture in captures:
            mark_reported(capture)
        return 0
    patches = ", ".join(str(capture.patch) for capture in captures)
    text = (
        f"{notice_text(files)} To use them, apply {patches} with `git apply` in a "
        "self_update worktree and take it through the normal update."
    )
    if not tell("-".join(capture.stamp for capture in captures), text):
        return 0
    for capture in captures:
        mark_reported(capture)
    return files
