"""Which conversation, and which point of it, a ``--print`` run continues.

* ``--resume ID`` continues ``ID``; a conversation that does not exist is an
  error (``No conversation ID``, exit 1), not a new conversation.
* ``--continue`` continues the most recently updated conversation of this
  folder's project (the default project's in the home folder); none is an
  error.
* ``--session-id ID`` starts a new conversation with that id; an id in use
  is an error.
* ``--at REF`` (with ``--resume`` or ``--continue``) names where the turn
  goes: a branch id, or the message at a branch's tip, runs on that branch;
  a message inside a branch starts a new branch after it, the way an edit or
  a retry does. Without it the turn goes to the branch the conversation has
  selected.

The conversation store is read here, not the Worker: both write the same
profile, and a run that names a conversation must find it whichever
transport then runs it.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path


class PrintSessionError(RuntimeError):
    """The conversation or point a run names cannot be used (exit 1)."""


@dataclass(frozen=True)
class PrintTarget:
    """Where a ``--print`` turn goes.

    ``new`` is a conversation the run creates. ``branch_id`` is the branch
    the turn runs on when ``--at`` named one; ``branch_from`` is the message
    a new branch starts after; both ``None`` for the selected branch.
    """

    session_id: str
    new: bool
    branch_id: str | None = None
    branch_from: str | None = None
    branch_head: str | None = None
    # The branch is the conversation's selected one.
    selected: bool = True


def _db():
    from openprogram.runtime.context.session_db import default_db

    return default_db()


def _exists(session_id: str) -> bool:
    db = _db()
    try:
        return bool(db.is_created(session_id)) or db.get_session(session_id) is not None
    except Exception:  # noqa: BLE001 - an unreadable conversation is not one to continue
        return False


def folder_project_ids(cwd: str | os.PathLike) -> set[str]:
    """The project ids a conversation of ``cwd`` is stored under, without
    registering anything: the project whose folder is ``cwd``, and ``""``
    (no project) for the home folder, which is the default project's."""
    from openprogram.state.store.project.project_store import list_projects

    folder = Path(cwd).expanduser().resolve()
    ids: set[str] = set()
    if folder == Path.home().expanduser().resolve():
        ids.add("")
    try:
        projects = list_projects()
    except Exception:  # noqa: BLE001 - no registry is no project
        projects = []
    for project in projects:
        try:
            if Path(project.path).expanduser().resolve() == folder:
                ids.add(project.id)
                if project.is_default:
                    ids.add("")
        except (OSError, ValueError):
            continue
    return ids


def latest_session(cwd: str | os.PathLike) -> str | None:
    """The most recently updated conversation of ``cwd``'s project.

    A conversation's project is the one whose registry entry lists it
    (``session_ids``, as ``project_for_session`` reads it): the session
    list's rows do not carry it. In the home folder (``""``) a conversation
    no project lists counts too."""
    from openprogram.state.store.project.project_store import list_projects

    wanted = folder_project_ids(cwd)
    if not wanted:
        return None
    try:
        projects = list_projects()
    except Exception:  # noqa: BLE001 - no registry is no project
        projects = []
    mine: set[str] = set()
    bound: set[str] = set()
    for project in projects:
        bound.update(project.session_ids)
        if project.id in wanted or ("" in wanted and project.is_default):
            mine.update(project.session_ids)
    try:
        rows = _db().list_sessions(limit=10**6)
    except Exception:  # noqa: BLE001 - nothing listed is nothing to continue
        return None
    for row in rows:  # newest activity first
        sid = row.get("id")
        if sid and (sid in mine or ("" in wanted and sid not in bound)):
            return str(sid)
    return None


def resolve_at(session_id: str, ref: str) -> PrintTarget:
    """Where ``--at REF`` puts the turn in ``session_id``."""
    db = _db()
    row = db.get_session(session_id) or {}
    refs = row.get("branch_refs") or {}
    selected = row.get("active_branch_id")
    if ref in refs:
        head = (refs.get(ref) or {}).get("head_id")
        return PrintTarget(session_id, False, branch_id=ref, branch_head=head,
                           selected=ref == selected or selected is None)
    for branch_id, entry in refs.items():
        if (entry or {}).get("head_id") == ref:
            return PrintTarget(session_id, False, branch_id=branch_id, branch_head=ref,
                               selected=branch_id == selected or selected is None)
    try:
        known = db.message_exists(session_id, ref)
    except Exception:  # noqa: BLE001 - read as not found
        known = False
    if not known:
        raise PrintSessionError(f"No branch or message {ref} in conversation {session_id}")
    return PrintTarget(session_id, False, branch_from=ref, selected=False)


def resolve_target(*, resume: str | None = None, continue_last: bool = False,
                   new_session_id: str | None = None, at: str | None = None,
                   cwd: str | os.PathLike | None = None) -> PrintTarget:
    """The conversation and point the run's options name (module docstring).
    Raises :class:`PrintSessionError`."""
    if resume:
        if not _exists(resume):
            raise PrintSessionError(f"No conversation {resume}")
        session_id = resume
    elif continue_last:
        found = latest_session(cwd or os.getcwd())
        if found is None:
            raise PrintSessionError("No conversation to continue in this folder")
        session_id = found
    else:
        if new_session_id:
            if _exists(new_session_id):
                raise PrintSessionError(f"Conversation {new_session_id} already exists")
            return PrintTarget(new_session_id, True)
        return PrintTarget("local_" + uuid.uuid4().hex[:10], True)
    if at:
        return resolve_at(session_id, at)
    return PrintTarget(session_id, False)


__all__ = ["PrintSessionError", "PrintTarget", "folder_project_ids", "latest_session",
           "resolve_at", "resolve_target"]
