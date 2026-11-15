"""Authoritative session placement under the application state root.

Every conversation lives in its project's folder:
``<state>/projects/<project-id>/chats/<session-id>/``; conversations that
belong to no project are in the home project's (``default``). The store
root is ``<state>/projects`` (see :mod:`openprogram.state.store.project.layout`);
its bookkeeping sits in hidden entries there. Legacy workdir copies remain
readable until journaled migration publishes them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from openprogram.state.store.project.layout import CHATS, DEFAULT_PROJECT_ID
from openprogram.state.store.session.atomic_file import atomic_write_text

#: The store's summary index and durable location map, at the store root.
INDEX_FILE = ".chats-index.json"
LOCATIONS_FILE = ".chats-locations.json"

RESERVED_ROOT_NAMES = frozenset({
    INDEX_FILE,
    LOCATIONS_FILE,
    ".locks",
    ".migration",
    ".deleted",
    ".quarantine",
    ".file-recovery",
    ".session-locks",
})


def nested_session_dir(root: Path, project_id: str, session_id: str) -> Path:
    return Path(root) / project_id / CHATS / session_id


def default_session_dir(root: Path, session_id: str) -> Path:
    return nested_session_dir(root, DEFAULT_PROJECT_ID, session_id)


def legacy_project_session_dir(project_path: str | Path, session_id: str) -> Path:
    return Path(project_path).expanduser() / ".openprogram" / "sessions" / session_id


def external_recovery_dir(session_dir: Path) -> Path:
    session_dir = Path(session_dir)
    return session_dir.parent / ".file-recovery" / session_dir.name


def quarantine_dir(root: Path) -> Path:
    """Where startup cleanup moves log-less shells instead of deleting them."""
    return Path(root) / ".quarantine"


def delete_intent_dir(root: Path) -> Path:
    return Path(root) / ".deleted"


def delete_intent_path(root: Path, session_id: str) -> Path:
    return delete_intent_dir(root) / session_id


def session_looks_present(path: Path) -> bool:
    path = Path(path)
    return (path / "main.jsonl").exists() or (path / "agents").is_dir()


def is_deleted(root: Path, session_id: str) -> bool:
    return delete_intent_path(root, session_id).is_file()


def record_delete_intent(root: Path, session_id: str, payload: dict) -> None:
    path = delete_intent_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def rooted_location(root: Path, recorded: str | Path) -> Path:
    """Where a recorded location of ``root``'s location map is now.

    The map holds absolute paths. A conversation folder of a store,
    ``…/<project>/chats/<session>``, recorded outside ``root`` was written
    before this state directory was copied or moved, so it means the folder
    of the same name under ``root``: a copy must never read or write the
    conversations of the state it was copied from. A workdir copy
    (``<project>/.openprogram/sessions/<session>``) stays where it is.
    """
    path = Path(recorded).expanduser()
    root = Path(root)
    if path.is_relative_to(root):
        return path
    parts = path.parts
    if len(parts) >= 3 and parts[-2] == CHATS:
        return nested_session_dir(root, parts[-3], parts[-1])
    return path


def iter_session_dirs(root: Path) -> Iterator[tuple[str, Path]]:
    """Discover session directories in every project's ``chats/``."""
    root = Path(root)
    if not root.is_dir():
        return
    seen: set[str] = set()
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        chats = project_dir / CHATS
        if not chats.is_dir():
            continue
        for child in sorted(chats.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if session_looks_present(child) and child.name not in seen:
                seen.add(child.name)
                yield child.name, child


class SessionFolderBusy(PermissionError):
    """Another program held a conversation folder for longer than the retry."""


def move_session_folder(source: Path, target: Path) -> None:
    """Rename a conversation folder, or leave it exactly where it was.

    Windows refuses to rename a directory while any file inside it is open:
    a reader folding the log in another process, an indexer, an antivirus
    scan, or a command still running in the conversation's work folder.
    This process keeps no file inside a conversation folder open between
    operations, so there is nothing of its own to close first. The refusal
    is retried for the platform seam's bound and then raised as
    :class:`SessionFolderBusy`. A rename either happens whole or not at
    all, and the folder is never copied instead.
    """
    from openprogram.host._platform.filesystem import (
        filesystem_path, is_sharing_violation, retry_sharing_violation,
    )
    import os

    native_source, native_target = filesystem_path(source), filesystem_path(target)
    try:
        retry_sharing_violation(lambda: os.rename(native_source, native_target))
    except OSError as error:
        if not is_sharing_violation(error):
            raise
        raise SessionFolderBusy(
            f"the conversation folder {source} is in use by another program and was not moved; "
            "close what has files in it open, then try again"
        ) from error


def target_dir_for_project(
    root: Path, session_id: str, *, project_id: str | None, is_default: bool,
) -> Path:
    if project_id and not is_default:
        return nested_session_dir(root, project_id, session_id)
    return default_session_dir(root, session_id)


def resolve_existing_dir(
    root: Path,
    session_id: str,
    *,
    locations: dict[str, str] | None = None,
    project_id: str | None = None,
    is_default: bool = True,
    project_path: str | None = None,
) -> Path | None:
    """Return the current readable repository path, or None.

    Never resurrects a session with a durable delete intent. Does not
    rewrite placement; callers that need a create path use
    :func:`target_dir_for_project`.
    """
    root = Path(root)
    if is_deleted(root, session_id):
        return None
    candidates: list[Path] = []
    # locations.json is the durable authority during migration. A published
    # destination may exist before that record is durable; never expose it as
    # writable until the authority points there.
    if locations and session_id in locations:
        candidates.append(Path(locations[session_id]))
    if project_id and not is_default:
        candidates.append(nested_session_dir(root, project_id, session_id))
    candidates.append(default_session_dir(root, session_id))
    if project_path and not is_default:
        candidates.append(legacy_project_session_dir(project_path, session_id))
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path if path.exists() else path
        except OSError:
            continue
        key = resolved
        if key in seen:
            continue
        seen.add(key)
        if session_looks_present(path):
            return path
    return None
