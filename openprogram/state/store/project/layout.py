"""Where a project's things live on disk.

Every project, the home project included, owns one folder under the
state directory::

    <state>/projects/
      projects.json              the project registry
      <project-id>/
        settings.json            project defaults
        chats/<session-id>/      its conversations
        memory/                  its memory workspace

The home project (id ``default``) holds the conversations that belong to
no project, and its memory is the personal memory every project reads
beside its own.

The conversation store keeps its bookkeeping (summary index, location map,
locks, tombstones) in hidden entries of the same ``projects/`` folder, so
a listing of it shows only the registry and the project folders.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_PROJECT_ID = "default"
CHATS = "chats"
MEMORY = "memory"
SETTINGS = "settings.json"


def _state(state: Path | str | None) -> Path:
    if state is not None:
        return Path(state)
    from openprogram.state.paths import get_state_dir

    return Path(get_state_dir())


def projects_root(state: Path | str | None = None) -> Path:
    return _state(state) / "projects"


def project_dir(project_id: str | None, state: Path | str | None = None) -> Path:
    return projects_root(state) / (project_id or DEFAULT_PROJECT_ID)


def chats_dir(project_id: str | None, state: Path | str | None = None) -> Path:
    return project_dir(project_id, state) / CHATS


def memory_dir(project_id: str | None, state: Path | str | None = None) -> Path:
    return project_dir(project_id, state) / MEMORY


def settings_path(project_id: str | None, state: Path | str | None = None) -> Path:
    return project_dir(project_id, state) / SETTINGS


__all__ = [
    "DEFAULT_PROJECT_ID", "CHATS", "MEMORY", "SETTINGS",
    "projects_root", "project_dir", "chats_dir", "memory_dir", "settings_path",
]
