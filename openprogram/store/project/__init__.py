"""Project entity layer + auto-commit.

Group ② of ``store/`` (see ``store/README.md`` and
``docs/design/memory/memory-v2.md`` / ``file-management.md``). Treats the user's
working directory as a git-backed *project* and records the agent's
per-turn edits there as attributable commits.

Modules:
  * ``project_store``   — Project + ProjectGit (safe auto-init,
                          agent-attributed commits / Strategy A, and the
                          reset/revert-of-a-commit primitive) + the
                          projects.json registry helpers.
  * ``project_commit``  — wires the agent's per-turn edits into the
                          project repo at turn end (rules A/B, default-on).

``from openprogram.store.project import ProjectGit, resolve_project`` etc.
"""
from .project_store import (
    Project,
    ProjectGit,
    ProjectStoreError,
    DEFAULT_PROJECT_ID,
    projects_dir,
    list_projects,
    get_project,
    get_default_project,
    resolve_project,
    bind_session,
    project_for_session,
    ensure_footprint_ignored,
)
from . import project_commit

__all__ = [
    "Project",
    "ProjectGit",
    "ProjectStoreError",
    "DEFAULT_PROJECT_ID",
    "projects_dir",
    "list_projects",
    "get_project",
    "get_default_project",
    "resolve_project",
    "bind_session",
    "project_for_session",
    "ensure_footprint_ignored",
    "project_commit",
]
