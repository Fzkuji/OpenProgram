"""A project's own Memory, read beside the personal one.

What a person prefers belongs everywhere they work; what one repository
decided belongs to that repository. With ``memory.layers.project`` on, a
conversation bound to a project reads two workspaces as one: the
project's, at ``<state>/projects/<project-id>/memory``, and the personal
one (the home project's). With ``memory.layers.project_writes`` also
on, what such a conversation records lands in the project's workspace
instead of the personal one. Both settings are on by default.

The project workspace is an ordinary memory workspace with its own git
history, index and runtime directory. ``store.root()`` hands it out for the
length of a ``use`` block, so every reader and writer that already asks
``store`` for its workspace works on it unchanged.

Access follows the execution's memory policy: an execution whose memory is
off reads no project memory either, and only one allowed to write memory
writes there.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from .management.config import load_memory_config

PROJECT = "project"
PERSONAL = "personal"

_active: ContextVar[Path | None] = ContextVar("memory_project_root", default=None)
# Workspace ids whose written-turn marker also counts as written for the
# write in progress. A turn is written to one layer only: switching
# project writes on or off must not copy a conversation into the other.
_written_elsewhere: ContextVar[frozenset[str]] = ContextVar(
    "memory_written_elsewhere", default=frozenset(),
)
_WORKSPACE_ID = re.compile(r"w-[0-9a-f]{8}")
# The project a chat with no session yet belongs to: a new chat's draft
# context is built before any session exists to name it.
_selected_project: ContextVar[str | None] = ContextVar(
    "memory_selected_project", default=None,
)


def active_root() -> Path | None:
    """The project workspace a ``use`` block selected, if any."""
    return _active.get()


def written_elsewhere() -> frozenset[str]:
    return _written_elsewhere.get()


@contextmanager
def for_project(project_id: str | None) -> Iterator[None]:
    """Resolve the project layer from ``project_id`` while no session
    names one, as for a new chat whose first message is not sent yet."""
    token = _selected_project.set(project_id or None)
    try:
        yield
    finally:
        _selected_project.reset(token)


def project_root(session_id: str | None = None) -> Path | None:
    """Where the session's project keeps its Memory.

    None when the setting is off or the conversation belongs to no project
    (the home project's memory is the personal one). Without a session,
    the project a ``for_project`` block selected applies.
    """
    if not load_memory_config().project_layer:
        return None
    if not session_id:
        from openprogram.state.execution_facts import current_session_id

        session_id = current_session_id() or ""
    try:
        from openprogram.state.store.project.project_store import (
            get_project, project_for_session,
        )

        if session_id:
            project = project_for_session(session_id)
        elif _selected_project.get():
            project = get_project(_selected_project.get())
        else:
            return None
    except Exception:  # noqa: BLE001 - an unreadable registry means no project
        return None
    if project is None or project.is_default:
        return None
    return project_memory_root(project.id)


def project_memory_root(project_id: str) -> Path:
    """A project's memory workspace, whether or not it exists yet."""
    from openprogram.state.store.project.layout import memory_dir

    from . import store

    root = memory_dir(project_id).resolve()
    # Every way into a project's memory resolves it here, and an old
    # workspace's footnotes still link a sources/ copy the parser refuses.
    store.cite_chats_directly(root)
    return root


def project_roots() -> list[Path]:
    """Every registered project's Memory that exists on this machine."""
    if not load_memory_config().project_layer:
        return []
    from openprogram.state.store.project.project_store import list_projects

    roots = []
    for project in list_projects():
        if project.is_default:
            continue
        root = project_memory_root(project.id)
        if has_notes(root):
            roots.append(root)
    return roots


def has_notes(root: Path) -> bool:
    """Whether a project workspace holds memory yet.

    Opening a project's Memory page initialises an empty workspace; until
    something is written there it adds no layer.
    """
    topics = Path(root) / "topics"
    try:
        if any(path.suffix == ".md" for path in topics.rglob("*.md")):
            return True
        return any(
            (Path(root) / name).is_file()
            and bool((Path(root) / name).read_text(encoding="utf-8").strip())
            for name in ("MEMORY.md", "core.md")
        )
    except OSError:
        return False


def _authorized(*, write: bool) -> bool:
    from .policy import allowed, current

    return current() is None or allowed(write=write)


@contextmanager
def use(root: Path, *, write: bool = False) -> Iterator[Path]:
    """Make ``store.root()`` the project workspace for this block."""
    from .policy import check, current

    if current() is not None:
        check(write=write)
    token = _active.set(Path(root).resolve())
    try:
        yield Path(root).resolve()
    finally:
        _active.reset(token)


def read_layers(session_id: str | None = None, *, personal: Path | None = None):
    """The layers a read for this session spans, narrowest first.

    ``personal`` is the personal workspace when the caller already holds
    it; otherwise ``store`` provides the one this execution selects.
    """
    from . import store
    from .policy import primary_read_space
    from .retrieval.layers import Layer, LayeredMemory

    personal = Layer(PERSONAL, Path(personal) if personal else store.ensure())
    project = project_root(session_id)
    # A policy with several read spaces reads each in turn; the project is
    # read with the first of them only, so its hits are not repeated.
    # An empty project adds no layer to reading: until something is written
    # there, paths and IDs are the personal memory's, unqualified. Where a
    # new record goes meanwhile is ``first_write_root``'s answer.
    if (
        project is None
        or not has_notes(project)
        or not primary_read_space()
        or not _authorized(write=False)
    ):
        return LayeredMemory([personal])
    default = PROJECT if write_root(session_id) is not None else PERSONAL
    return LayeredMemory([Layer(PROJECT, project), personal], default=default)


def first_write_root(session_id: str | None = None) -> Path | None:
    """Where a new record goes while the project's memory is still empty.

    The background writer writes to the project whenever project writes
    apply, however empty its memory is; a record created from the chat
    lands in the same place. None when the project already holds notes
    (``read_layers`` then names it) or when project writes do not apply.
    """
    target = write_root(session_id)
    if target is None or has_notes(target):
        return None
    return target


def write_root(session_id: str | None = None) -> Path | None:
    """The project workspace this session's writes land in, if any."""
    if not load_memory_config().project_writes:
        return None
    root = project_root(session_id)
    if root is None or not _authorized(write=True):
        return None
    return root


def _existing_workspace_id(root: Path | None) -> str | None:
    if root is None:
        return None
    from .workspace_layout import runtime_dir

    try:
        value = (runtime_dir(root) / "workspace-id").read_text(
            encoding="utf-8",
        ).strip()
    except OSError:
        return None
    return value if _WORKSPACE_ID.fullmatch(value) else None


def left_workspace_ids(session_id: str) -> frozenset[str]:
    """The memory workspaces of the projects a conversation was moved out of.

    A conversation moved to another project keeps ``project_history`` on
    its meta. The turns those projects' memory already holds stay written
    there; the new project's memory takes only what comes after the move.
    """
    if not session_id:
        return frozenset()
    try:
        from openprogram.state.store.current import current_store
        from openprogram.state.store.project.layout import DEFAULT_PROJECT_ID

        history = (current_store().get_session(session_id) or {}).get("project_history") or []
    except Exception:  # noqa: BLE001 - an unreadable meta leaves nothing to skip
        return frozenset()
    found = set()
    for project_id in history:
        # The home project's memory is the personal workspace, which every
        # write target already accounts for.
        if not isinstance(project_id, str) or project_id == DEFAULT_PROJECT_ID:
            continue
        workspace = _existing_workspace_id(project_memory_root(project_id))
        if workspace:
            found.add(workspace)
    return frozenset(found)


@contextmanager
def write_target(session_id: str) -> Iterator[Path | None]:
    """Route this session's background writing to its layer.

    Yields the project workspace when project writes apply, else None and
    the personal workspace stays selected. Either way a turn already
    written to the other layer, or to the layer of a project the
    conversation was moved out of, counts as written.
    """
    from . import store

    target = write_root(session_id)
    left = left_workspace_ids(session_id)
    if target is None:
        other = _existing_workspace_id(project_root(session_id))
        token = _written_elsewhere.set(left | frozenset({other} if other else ()))
        try:
            yield None
        finally:
            _written_elsewhere.reset(token)
        return
    personal_id = store.workspace_id()
    with use(target, write=True):
        store.ensure()
        token = _written_elsewhere.set(left | frozenset({personal_id}))
        try:
            yield target
        finally:
            _written_elsewhere.reset(token)
