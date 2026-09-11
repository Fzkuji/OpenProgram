"""Authoritative session placement under the application state root.

New bound conversations live at
``<state>/sessions/projects/<project-id>/<session-id>/``.
Default and pre-existing home-root sessions stay at
``<state>/sessions/<session-id>/``. Legacy workdir copies remain readable
until journaled migration publishes them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

from openprogram.store.session.git_session import atomic_write_text, read_text_with_retry


RESERVED_ROOT_NAMES = frozenset({
    "projects",
    "index.json",
    "locations.json",
    ".locks",
    ".migration",
    ".deleted",
    ".file-recovery",
    ".session-locks",
})


def nested_session_dir(root: Path, project_id: str, session_id: str) -> Path:
    return Path(root) / "projects" / project_id / session_id


def default_session_dir(root: Path, session_id: str) -> Path:
    return Path(root) / session_id


def legacy_project_session_dir(project_path: str | Path, session_id: str) -> Path:
    return Path(project_path).expanduser() / ".openprogram" / "sessions" / session_id


def external_recovery_dir(session_dir: Path) -> Path:
    session_dir = Path(session_dir)
    return session_dir.parent / ".file-recovery" / session_dir.name


def delete_intent_dir(root: Path) -> Path:
    return Path(root) / ".deleted"


def delete_intent_path(root: Path, session_id: str) -> Path:
    return delete_intent_dir(root) / session_id


def session_looks_present(path: Path) -> bool:
    path = Path(path)
    return (path / "history").is_dir() or (path / "meta.json").is_file()


def is_deleted(root: Path, session_id: str) -> bool:
    return delete_intent_path(root, session_id).is_file()


def record_delete_intent(root: Path, session_id: str, payload: dict) -> None:
    path = delete_intent_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def clear_delete_intent(root: Path, session_id: str) -> None:
    path = delete_intent_path(root, session_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def load_delete_intent(root: Path, session_id: str) -> dict | None:
    path = delete_intent_path(root, session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(read_text_with_retry(path))
        return data if isinstance(data, dict) else {"session_id": session_id}
    except (OSError, json.JSONDecodeError):
        return {"session_id": session_id}


def iter_session_dirs(root: Path) -> Iterator[tuple[str, Path]]:
    """Discover session repositories in both default and nested layouts."""
    root = Path(root)
    if not root.is_dir():
        return
    seen: set[str] = set()
    for child in root.iterdir():
        if not child.is_dir() or child.name in RESERVED_ROOT_NAMES:
            continue
        if child.name.startswith("."):
            continue
        if session_looks_present(child) and child.name not in seen:
            seen.add(child.name)
            yield child.name, child
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for child in project_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if session_looks_present(child) and child.name not in seen:
                seen.add(child.name)
                yield child.name, child


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
    if project_id and not is_default:
        candidates.append(nested_session_dir(root, project_id, session_id))
    candidates.append(default_session_dir(root, session_id))
    if locations and session_id in locations:
        candidates.append(Path(locations[session_id]))
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
