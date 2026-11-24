"""Project & working-directory WS actions — the topbar project picker.

Exposes the project entity layer (``store.project_store``) plus the
per-session working-directory set over the WebSocket, so the web UI can
mirror Claude's composer chips:

  * a **main project** chip — which project this conversation belongs to
    (decides where the session repo is stored: ``<project>/.openprogram
    /sessions/<id>/``). One per session, and **frozen once the session
    has a turn**.
  * **additional directory** chips — extra folders the agent may read /
    write in this session, beyond the main project. Freely added and
    removed at any point in the session's life; each change takes effect
    from the next turn.

Actions:
    list_projects          → all registered projects + current session's
                             project_id
    create_project         → bind a folder on a machine (``machine_id``,
                             this computer by default) as a project;
                             returns the project
    list_machine_folder    → one folder's subfolders on a machine whose
                             shell keeps its own files, to choose a
                             project folder there
    set_session_project    → set the session's MAIN project (label +
                             reverse index). Rejected once the session
                             has turns and the bind names a different
                             project — see ``FROZEN_ERROR``.
    move_session_to_project → move a conversation, turns and all, into
                             another project: the repository moves with
                             it and a ``project/move`` node records it.
                             Refused while the conversation is running.
    relocate_project       → repair a project whose directory moved on
                             disk; records a ``project/relocate`` node
    list_session_workdirs  → the session's working-directory set
    add_session_workdir    → add an additional directory
    remove_session_workdir → drop an additional directory

State / git work lives in ``store.project_store`` / the session store;
these handlers only marshal requests and shape JSON.
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import threading
import time

from openprogram.state.store.session.conversation_work import run_for_conversation

_log = logging.getLogger(__name__)


def _project_dict(p, alive: set[str] | None = None, unarchived: set[str] | None = None) -> dict:
    # session_ids/session_count 只含**存活**会话，不裸信可能含孤立引用的
    # p.session_ids。count 与 ids 同源；count 保留是老前端兼容。
    sids = list(p.session_ids or [])
    if alive is not None:
        sids = [s for s in sids if s in alive]
    visible = [sid for sid in sids if unarchived is None or sid in unarchived]
    # The location the registry records, never a look at the folder: a
    # listing answers from memory. ``_reconcile_projects`` checks the folders
    # behind the reply and has every page list again when a state changed.
    location_state = "available" if p.is_default else (
        getattr(p, "location_state", "") or "available")
    path_missing = location_state in {"missing", "replaced"}
    return {
        "id": p.id,
        "name": p.name,
        "path": p.path,
        "is_default": p.is_default,
        # The machine the folder is on and the project's conversations run on.
        "machine_id": "local" if p.is_default else (getattr(p, "machine_id", "") or "local"),
        # The composer chip warns on a project whose folder is gone and
        # offers the relocate repair. Sent from here so the frontend never
        # guesses from the path string.
        "path_missing": path_missing,
        "path_replaced": location_state == "replaced",
        "location_state": location_state,
        "location_revision": int(getattr(p, "location_revision", 0) or 0),
        "migration_error": getattr(p, "migration_error", "") or "",
        "session_count": len(sids),
        "unarchived_session_count": len(visible),
        "session_ids": sids,
        "status": p.status,
        "hidden": getattr(p, "hidden", False),
        "icon": getattr(p, "icon", ""),
        "description": getattr(p, "description", ""),
        "source_folders": list(getattr(p, "source_folders", []) or []),
    }


def _alive_session_ids() -> set[str]:
    """当前真实存在的会话 id 集（SessionDB 有内容的）。"""
    try:
        from openprogram.runtime.context.session_db import default_db
        return {r.get("id") for r in default_db().list_sessions(limit=100_000, include_archived=True) if r.get("id")}
    except Exception:
        return set()


def _session_meta(session_id: str) -> dict:
    try:
        from openprogram.runtime.context.session_db import default_db
        return default_db().get_session(session_id) or {}
    except Exception:
        return {}


# Projects


def _projects_snapshot(session_id: str | None, *, local_only: bool = False) -> dict:
    """The registry as it stands, read only and from memory: the parsed
    registry file and the conversation index. Nothing per project touches
    the disk (no folder check, no marker read), no scans, no writes, no
    registry lock.

    Each file-system call gives up the interpreter lock, and getting it back
    takes a full switch interval whenever another thread is computing, so a
    listing that made hundreds of small calls took seconds beside one busy
    thread while it took milliseconds alone."""
    try:
        from openprogram.state.store.project import project_store as _projects
        _projects.get_default_project()  # ensure the default label exists
        alive = _alive_session_ids()
        unarchived = set()
        try:
            from openprogram.runtime.context.session_db import default_db
            unarchived = {r.get("id") for r in default_db().list_sessions(limit=100_000) if r.get("id")}
        except Exception:
            unarchived = alive
        projects = [_project_dict(p, alive, unarchived) for p in _projects.list_projects()]
        current_project_id = None
        if session_id:
            cur = _projects.project_for_session(session_id)
            current_project_id = cur.id if cur else None
        if not local_only:
            # Each connected remote OpenProgram machine's projects, under ids of their own.
            from openprogram.interfaces.server.remote_machines import links
            projects += links.remote_projects(alive)
            if session_id and session_id not in alive:
                current_project_id = links.remote_project_for(session_id) or current_project_id
        return {"projects": projects, "current_project_id": current_project_id,
                "status": "ready", "error_code": None, "error": None}
    except Exception:
        # An unavailable registry is not an authoritative empty snapshot.
        # Keep the request context so clients can retry without clearing
        # project-scoped state for projects that still exist.
        return {"projects": None, "current_project_id": None, "status": "error",
                "error_code": "PROJECT_REGISTRY_UNAVAILABLE",
                "error": "Project registry is unavailable."}


#: Listing projects is an access, so it also reconciles them (finds a moved
#: or deleted folder, drops references to deleted conversations). That work
#: reads every project's folder and may wait on a session migration, so it
#: runs after the reply, one at a time, at most this often. A listing inside
#: the interval is followed by one run at the interval's end, so what a
#: listing showed is always checked against the folders afterwards.
RECONCILE_INTERVAL_S = 10.0
_reconcile_lock = threading.Lock()
_reconcile_running = False
_reconcile_started_at = float("-inf")
#: When the run queued behind the interval is due; in the past when none is.
_reconcile_queued_for = float("-inf")


def _claim_reconcile() -> float | None:
    """Seconds until this listing's reconciliation may start (0 = now);
    ``None`` when one is already running or queued."""
    global _reconcile_running, _reconcile_queued_for, _reconcile_started_at
    with _reconcile_lock:
        now = time.monotonic()
        if _reconcile_running or now < _reconcile_queued_for:
            return None
        wait = _reconcile_started_at + RECONCILE_INTERVAL_S - now
        if wait > 0:
            if wait == float("inf"):
                return None
            _reconcile_queued_for = now + wait
            return wait
        _reconcile_running, _reconcile_started_at = True, now
        return 0.0


def _start_queued_reconcile(loop) -> None:
    global _reconcile_running, _reconcile_queued_for, _reconcile_started_at
    with _reconcile_lock:
        _reconcile_queued_for = float("-inf")
        if _reconcile_running:
            return
        _reconcile_running, _reconcile_started_at = True, time.monotonic()
    loop.run_in_executor(None, _reconcile_projects)


def _visible_locations() -> dict:
    """What a listing shows of each project's location: the recorded one."""
    from openprogram.state.store.project import project_store as _projects
    return {p.id: (p.path, p.location_state) for p in _projects.list_projects()}


def _reconcile_projects() -> None:
    """Prune dead session references and refresh project locations; tell
    every client to list again when what a listing shows changed: a folder
    found moved, deleted or back, or a migration that settled (migrating →
    available)."""
    global _reconcile_running
    try:
        from openprogram.state.store.project import project_store as _projects
        from openprogram.state.store.project.location import reconcile_registered_projects
        before = _visible_locations()
        _projects.prune_sessions(_alive_session_ids())
        reconcile_registered_projects()
        if _visible_locations() != before:
            _projects_changed()
    except Exception:
        _log.warning("project reconciliation after listing failed", exc_info=True)
    finally:
        with _reconcile_lock:
            _reconcile_running = False


async def handle_list_projects(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip() or None
    snapshot = await asyncio.to_thread(_projects_snapshot, session_id,
                                       local_only=cmd.get("local_only") is True)
    await ws.send_text(json.dumps({
        "type": "projects_list",
        "data": {**snapshot, "session_id": session_id},
    }, default=str))
    if snapshot["status"] != "ready":
        return
    wait = _claim_reconcile()
    if wait is None:
        return
    loop = asyncio.get_running_loop()
    if wait:
        loop.call_later(wait, _start_queued_reconcile, loop)
    else:
        loop.run_in_executor(None, _reconcile_projects)


def _folder_on_this_disk(machine_id: str) -> bool:
    """A folder for ``machine_id`` is one on this computer's disk (this computer, or a machine that mounts it)."""
    if machine_id in ("", "local"):
        return True
    from openprogram.host.machines import get_machine
    machine = get_machine(machine_id)
    return machine is not None and not machine.remote_files


async def handle_create_project(ws, cmd: dict):
    from openprogram.state.store.project import project_store as _projects
    path = (cmd.get("path") or "").strip()
    name = (cmd.get("name") or "").strip() or None
    machine_id = (cmd.get("machine_id") or "").strip() or "local"
    ok, error, proj_dict = False, None, None
    if not path:
        error = "path is required"
    elif _folder_on_this_disk(machine_id) and not os.path.isdir(os.path.expanduser(path)):
        error = f"not a directory: {path}"
    else:
        try:
            # A folder on another machine is checked there, over its shell: off the loop.
            proj = await asyncio.to_thread(_projects.resolve_project, path, name=name, machine_id=machine_id)
            proj_dict = _project_dict(proj)
            ok = True
        except _projects.ProjectStoreError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
    if ok:
        _projects_changed(proj_dict["id"])
    await ws.send_text(json.dumps({
        "type": "project_created",
        "data": {"ok": ok, "project": proj_dict, "error": error},
    }, default=str))
    if ok:
        await handle_list_projects(ws, {"session_id": cmd.get("session_id")})


def _list_machine_folder(machine_id: str, path: str) -> dict:
    from openprogram.host.machines import get_environment
    from openprogram.host.machines.remote import list_folders
    machine = get_environment(machine_id)
    if machine is None or not machine.remote_files:
        return {"ok": False, "error": f"no folders to list on {machine_id!r}"}
    try:
        folder, names = list_folders(machine, path)
    except ConnectionError as exc:
        return {"ok": False, "error": str(exc)}
    if folder is None:
        return {"ok": False, "error": f"not a folder on {machine.name}: {path}"}
    parent = None if folder == "/" else (folder.rstrip("/").rsplit("/", 1)[0] or "/")
    return {"ok": True, "path": folder, "parent": parent, "folders": names}


async def handle_list_machine_folder(ws, cmd: dict):
    """One folder's subfolders on a machine that keeps its own files, read through its shell."""
    machine_id = (cmd.get("machine_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    data = await asyncio.to_thread(_list_machine_folder, machine_id, path)
    await ws.send_text(json.dumps({
        "type": "machine_folder",
        "data": {**data, "machine_id": machine_id, "request_id": cmd.get("request_id")},
    }, default=str))


def _projects_changed(project_id: str | None = None) -> None:
    """Tell every open page to list projects again: a project made,
    renamed, moved or hidden from one connection shows in all of them.

    Handlers call this as soon as the change is stored, before anything
    is sent back to the requesting socket: a sender that hangs up after
    its reply (the CLI, a script, an agent) must not take the other
    pages' notice with it."""
    from openprogram.interfaces.server import server
    data = {"project_id": project_id} if project_id else {}
    server._broadcast(json.dumps({"type": "projects_changed", "data": data}))


#: Rejection reason when a session that already has turns tries to rebind.
FROZEN_ERROR = "project is frozen after the first turn"


def session_has_turns(session_id: str) -> bool:
    """True once the session has recorded at least one conversational
    node — the moment its main project freezes."""
    try:
        from openprogram.runtime.context.session_db import default_db
        return bool(default_db().get_messages(session_id, limit=1))
    except Exception:
        return False


def _binding_is_frozen(session_id: str, project_id: str) -> bool:
    """True when this bind would CHANGE a frozen session's project.

    Re-binding a session to the project it already has stays legal at
    any age: the composer sends the picker's choice with the first chat
    frame (which creates the repo inside the project) and follows it
    with an idempotent ``set_session_project`` that lands after the turn
    is already committed. Only a bind naming a DIFFERENT project than
    the frozen one is a rebind, and that is what gets rejected."""
    if not session_has_turns(session_id):
        return False
    try:
        from openprogram.state.store.project import project_store as _projects
        current = _projects.project_for_session(session_id)
        if current is None:
            current = _projects.get_default_project()
        return current is None or current.id != project_id
    except Exception:
        return True


def _set_session_project(session_id: str, project_id: str) -> tuple[bool, str | None]:
    if _binding_is_frozen(session_id, project_id):
        return False, FROZEN_ERROR
    try:
        from openprogram.state.store.project import project_store as _projects
        from openprogram.runtime.context.session_db import default_db
        if _projects.get_project(project_id) is None:
            return False, "unknown project"
        default_db().assign_project(session_id, project_id)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def handle_set_session_project(ws, cmd: dict):
    """Bind the session's MAIN project. Legal only while the session has
    no turns yet: the main working directory freezes when the first real
    message commits, so this rejects a rebind on a non-empty session
    (session/operations.md, "Main project binding"). A conversation that
    already has turns changes project through `move_session_to_project`,
    which moves its repository and records the move."""
    session_id = (cmd.get("session_id") or "").strip()
    project_id = (cmd.get("project_id") or "").strip()
    if not session_id or not project_id:
        ok, error = False, "session_id and project_id are required"
    else:
        # Reads and writes the conversation under its lock: off the loop.
        ok, error = await run_for_conversation(
            session_id, _set_session_project, session_id, project_id)
    if ok:
        # Other open pages regroup the conversation; sent before the reply so
        # a caller that hangs up does not take their notice with it.
        _projects_changed(project_id)
        from openprogram.interfaces.server.ws_actions.session.listing import broadcast_sessions_list
        broadcast_sessions_list()
    await ws.send_text(json.dumps({
        "type": "session_project_set",
        "data": {"ok": ok, "session_id": session_id, "project_id": project_id, "error": error},
    }, default=str))


#: Rejection reason when a conversation is moved while a turn runs on it.
RUNNING_ERROR = "the conversation is running; wait for it to finish"


def _move_session_to_project(session_id: str, project_id: str):
    """``(ok, error, from_project_id, node_id)`` of moving the conversation."""
    from_project_id, node_id = None, None
    try:
        from openprogram.runtime.conversation.run_control import is_turn_running
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store.project import project_store as _projects
        from openprogram.state.store.project.relocate_node import record_move
        target = _projects.get_project(project_id)
        if target is None:
            return False, "unknown project", None, None
        if is_turn_running(session_id):
            return False, RUNNING_ERROR, None, None
        db = default_db()
        before = _projects.project_for_session(session_id) or _projects.get_default_project()
        result = db.move_session(session_id, project_id)
        from_project_id = result.get("from_project_id")
        if result.get("moved"):
            node_id = record_move(
                db, session_id,
                old_project_id=from_project_id or "", new_project_id=project_id,
                old_name=before.name if before else "", new_name=target.name)
        return True, None, from_project_id, node_id
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}", from_project_id, node_id


async def handle_move_session_to_project(ws, cmd: dict):
    """Move a conversation into another project, turns and all.

    The one way to change a frozen session's project: its repository moves
    to the project's ``chats/`` folder, later turns run in the project's
    folder and write the project's memory, and the move is recorded as a
    ``project/move`` node on ``caller=ROOT`` (head stays where it was). A
    running conversation is refused, since its turn is working in the old
    folder."""
    session_id = (cmd.get("session_id") or "").strip()
    project_id = (cmd.get("project_id") or "").strip()
    if not session_id or not project_id:
        ok, error, from_project_id, node_id = False, "session_id and project_id are required", None, None
    else:
        # Moves the conversation under its lock: off the loop.
        ok, error, from_project_id, node_id = await run_for_conversation(
            session_id, _move_session_to_project, session_id, project_id)
    if ok:
        _projects_changed(project_id)
        from openprogram.interfaces.server.ws_actions.session.listing import broadcast_sessions_list
        broadcast_sessions_list()
    await ws.send_text(json.dumps({
        "type": "session_moved",
        "data": {"ok": ok, "session_id": session_id, "from_project_id": from_project_id,
                 "project_id": project_id, "node_id": node_id, "error": error},
    }, default=str))
    if ok:
        await handle_list_projects(ws, {"session_id": session_id})


async def handle_relocate_project(ws, cmd: dict):
    """Repair a main project whose directory is gone: point the project
    at ``path`` and record the move in the session graph.

    The one sanctioned way to change a frozen session's main working
    directory. It changes the PROJECT's path (every session bound to it
    follows), not the session→project binding, so the freeze from
    ``set_session_project`` stays intact. The move lands as a
    ``project/relocate`` node on ``caller=ROOT``, which leaves head
    where it was. When a missing project folds into the project that
    already owns ``path``, the reply's ``project_id`` names that owner."""
    session_id = (cmd.get("session_id") or "").strip()
    project_id = (cmd.get("project_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    expected_path = cmd.get("expected_path")
    expected_revision = cmd.get("expected_revision")
    replace_identity = bool(cmd.get("replace_identity"))
    ok, error, old_path, node_id = False, None, None, None
    resolved_id = project_id
    from openprogram.state.store.project import project_store as _projects
    if not project_id or not path:
        error = "project_id and path are required"
    elif not _projects.folder_is_here(_projects.get_project(project_id)):
        error = "a project on another machine keeps its folder; open the new folder as a project"
    elif not os.path.isdir(os.path.expanduser(path)):
        error = f"not a directory: {path}"
    else:
        try:
            before = _projects.get_project(project_id)
            old_path = before.path if before else None
            if expected_path is None and before is not None:
                expected_path = before.path
            if expected_revision is None and before is not None:
                expected_revision = getattr(before, "location_revision", 0)
            resolved_id = _projects.relocate_project(
                project_id, path,
                expected_path=expected_path,
                expected_revision=expected_revision,
                replace_identity=replace_identity,
            ).id
            ok = True
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
    if ok and session_id and resolved_id == project_id:
        try:
            from openprogram.runtime.context.session_db import default_db
            from openprogram.state.store.project.relocate_node import record_relocate
            node_id = record_relocate(
                default_db(), session_id, project_id=project_id,
                old_path=old_path or "", new_path=path)
        except Exception:
            node_id = None
    if ok:
        _projects_changed(resolved_id)
    await ws.send_text(json.dumps({
        "type": "project_relocated",
        "data": {"ok": ok, "session_id": session_id, "project_id": resolved_id,
                 "old_path": old_path, "path": path, "node_id": node_id,
                 "error": error},
    }, default=str))
    if ok:
        await handle_list_projects(ws, {"session_id": session_id or None})


# Additional working directories


def _resolve_workdirs(session_id: str) -> list[str]:
    """The session's working-directory set: main project path first
    (if any), then the explicit additional dirs from meta.workdirs."""
    meta = _session_meta(session_id)
    dirs: list[str] = []
    # main project path
    try:
        from openprogram.state.store.project import project_store as _projects
        proj = _projects.project_for_session(session_id)
        if proj and proj.path:
            dirs.append(proj.path)
    except Exception:
        # No project: only the explicit workdirs are listed.
        _log.debug("project lookup failed for session %s", session_id, exc_info=True)
    for d in (meta.get("workdirs") or []):
        if d and d not in dirs:
            dirs.append(d)
    return dirs


async def handle_list_session_workdirs(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    await ws.send_text(json.dumps({
        "type": "session_workdirs",
        "data": {"session_id": session_id,
                 "workdirs": await run_for_conversation(session_id, _resolve_workdirs, session_id)},
    }, default=str))


def _session_remote_machine(session_id: str):
    """The machine a session's project folder is on when it keeps its own files, else None."""
    from openprogram.state.store.project import project_store as _projects
    project = _projects.project_for_session(session_id)
    if project is None or _projects.folder_is_here(project):
        return None
    return _projects.project_machine(project)


def _workdir_problem(session_id: str, path: str) -> tuple[str | None, str]:
    """``(error, folder)`` for an extra working folder, checked where the session's project is."""
    try:
        machine = _session_remote_machine(session_id)
    except Exception as e:  # noqa: BLE001 - the project's machine is gone or off
        return str(e), path
    if machine is None:
        return (None if os.path.isdir(os.path.expanduser(path)) else f"not a directory: {path}"), path
    from openprogram.host.machines.remote import resolve_folder
    try:
        found = resolve_folder(machine, path)
    except ConnectionError as exc:
        return str(exc), path
    return (None, found) if found else (f"not a folder on {machine.name}: {path}", path)


def _edit_workdirs(session_id: str, path: str, add: bool) -> tuple[bool, str | None]:
    """Add or remove one extra working directory; runs off the loop (the
    conversation's record is read and written under its lock). A folder on
    the project's remote machine is kept as that machine names it."""
    try:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
        meta = db.get_session(session_id) or {}
        remote = path.startswith("/") and _session_remote_machine(session_id) is not None
        ap = path if remote else os.path.abspath(os.path.expanduser(path))
        if add:
            dirs = list(meta.get("workdirs") or [])
            if ap not in dirs:
                dirs.append(ap)
        else:
            dirs = [d for d in (meta.get("workdirs") or []) if d not in (path, ap)]
        db.update_session(session_id, workdirs=dirs)
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


async def handle_add_session_workdir(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    ok, error = False, None
    if not session_id or not path:
        error = "session_id and path are required"
    else:
        error, path = await asyncio.to_thread(_workdir_problem, session_id, path)
    if error is None:
        ok, error = await run_for_conversation(session_id, _edit_workdirs, session_id, path, True)
    await ws.send_text(json.dumps({
        "type": "session_workdir_added",
        "data": {"ok": ok, "session_id": session_id, "path": path, "error": error},
    }, default=str))
    if ok:
        await handle_list_session_workdirs(ws, {"session_id": session_id})


async def handle_remove_session_workdir(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip()
    path = (cmd.get("path") or "").strip()
    ok, error = await run_for_conversation(session_id, _edit_workdirs, session_id, path, False)
    await ws.send_text(json.dumps({
        "type": "session_workdir_removed",
        "data": {"ok": ok, "session_id": session_id, "path": path, "error": error},
    }, default=str))
    if ok:
        await handle_list_session_workdirs(ws, {"session_id": session_id})


# 项目级默认设置（新会话回落值）。只留已接通生效的键——见
# session_config.project_defaults 的读取方。
_PROJECT_CONFIG_KEYS = ("permission_mode", "thinking_effort")


async def handle_get_project_config(ws, cmd: dict):
    """返回项目级默认配置（作为新会话默认值）。存 project settings.json。"""
    project_id = (cmd.get("project_id") or "").strip()
    cfg: dict = {}
    try:
        from openprogram.state.store.project import project_store as _projects
        s = _projects.load_project_settings(project_id)
        cfg = {k: s.get(k) for k in _PROJECT_CONFIG_KEYS if s.get(k) is not None}
    except Exception:
        cfg = {}
    await ws.send_text(json.dumps({
        "type": "project_config",
        "data": {"project_id": project_id, "config": cfg},
    }, default=str))


async def handle_set_project_config(ws, cmd: dict):
    """设一个项目级配置字段（key ∈ _PROJECT_CONFIG_KEYS）。value=None 清除。"""
    project_id = (cmd.get("project_id") or "").strip()
    key = cmd.get("key")
    value = cmd.get("value")
    if project_id and key in _PROJECT_CONFIG_KEYS:
        try:
            from openprogram.state.store.project import project_store as _projects
            s = _projects.load_project_settings(project_id)
            if value in (None, "", "inherit"):
                s.pop(key, None)
            else:
                s[key] = value
            _projects.save_project_settings(project_id, s)
        except Exception:
            # The reply below shows the unchanged config.
            _log.warning(
                "project %s setting %s not saved", project_id, key, exc_info=True)
    await handle_get_project_config(ws, {"project_id": project_id})


async def handle_list_project_sessions(ws, cmd: dict):
    """返回某项目下的**存活**会话摘要（id/title/created_at），供项目详情页
    的 Sessions tab 列出并跳转。孤立引用（已删会话）自动过滤。"""
    project_id = (cmd.get("project_id") or "").strip()
    sessions: list[dict] = []
    try:
        from openprogram.state.store.project import project_store as _projects
        from openprogram.runtime.context.session_db import default_db
        proj = _projects.get_project(project_id)
        db = default_db()
        rows = {r.get("id"): r
                for r in db.list_sessions(limit=100_000, include_archived=True)}
        for sid in (proj.session_ids if proj else []):
            row = rows.get(sid)
            if not row:
                continue  # 孤立引用，跳过
            title = (row.get("title") or "").strip()
            preview = (row.get("preview") or "").strip()
            if title in ("", "New conversation", "Untitled"):
                title = preview or sid
            sessions.append({
                "id": sid,
                "title": title,
                "created_at": row.get("created_at") or 0,
                "preview": preview or None,
            })
        sessions.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
    except Exception:
        sessions = []
    await ws.send_text(json.dumps({
        "type": "project_sessions",
        "data": {"project_id": project_id, "sessions": sessions},
    }, default=str))


async def handle_update_project(ws, cmd: dict):
    from openprogram.state.store.project import project_store as projects
    try:
        project = projects.update_project(cmd.get("project_id"), cmd.get("patch"))
        result = {"ok": True, "project": _project_dict(project)}
    except (ValueError, OSError) as exc:
        result = {"ok": False, "error": str(exc)}
    if result["ok"]:
        _projects_changed(project.id)
    await ws.send_text(json.dumps({"type": "project_updated", "data": result}))


ACTIONS = {
    "update_project": handle_update_project,
    "list_projects": handle_list_projects,
    "list_project_sessions": handle_list_project_sessions,
    "get_project_config": handle_get_project_config,
    "set_project_config": handle_set_project_config,
    "create_project": handle_create_project,
    "list_machine_folder": handle_list_machine_folder,
    "set_session_project": handle_set_session_project,
    "move_session_to_project": handle_move_session_to_project,
    "relocate_project": handle_relocate_project,
    "list_session_workdirs": handle_list_session_workdirs,
    "add_session_workdir": handle_add_session_workdir,
    "remove_session_workdir": handle_remove_session_workdir,
}


# A worktree being checked out, by project, so Stop can reach it.
_worktree_cancels: dict = {}


async def handle_cancel_project_worktree(ws, cmd: dict):
    """Stop a create_project_worktree whose files are still being checked out."""
    cancel = _worktree_cancels.get(cmd.get("project_id"))
    if cancel is not None:
        cancel.set()


async def handle_project_operation(ws, cmd: dict):
    """Project menu mutations, with persisted results before success events."""
    import asyncio
    from openprogram.state.store.project import project_store as projects
    from openprogram.interfaces.server import server
    action = cmd.get("action")
    project_id = cmd.get("project_id")
    result = {"ok": False, "project_id": project_id}
    try:
        if not isinstance(project_id, str) or not projects.get_project(project_id):
            raise ValueError("unknown project")
        if action in ("remove_project", "restore_project"):
            project = projects.set_project_hidden(project_id, action == "remove_project")
            result.update(ok=True, project=_project_dict(project))
        elif action == "create_project_worktree":
            import threading
            loop = asyncio.get_running_loop()
            cancel = _worktree_cancels.setdefault(project_id, threading.Event())
            cancel.clear()

            def progress(stage, percent):
                event = {"type": "create_project_worktree_progress",
                         "data": {"project_id": project_id, "stage": stage, "percent": percent}}
                asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(event)), loop)

            try:
                project = await asyncio.to_thread(
                    projects.create_project_worktree, project_id, cmd.get("path"), cmd.get("branch"),
                    progress=progress, cancel=cancel)
            except projects.WorktreeStopped:
                result["stopped"] = True
                raise
            finally:
                _worktree_cancels.pop(project_id, None)
            result.update(ok=True, project=_project_dict(project))
        elif action == "archive_project_chats":
            from openprogram.runtime.context.session_db import default_db
            db = default_db()
            project = projects.get_project(project_id)
            archived = []
            result["session_ids"] = archived
            session_ids = list(project.session_ids)
            if project.is_default:
                claimed = {sid for p in projects.list_projects() for sid in p.session_ids}
                session_ids.extend(row["id"] for row in db.list_sessions(limit=100_000, include_archived=True)
                                   if row.get("id") and row["id"] not in claimed)
            def archive(sid):
                # Under the conversation's lock: run in its lane.
                row = db.get_session(sid)
                if not row:
                    return None
                was = bool(row.get("archived"))
                db.update_session(sid, archived=True)
                return was

            for sid in session_ids:
                was_archived = await run_for_conversation(sid, archive, sid)
                if was_archived is None:
                    continue
                archived.append(sid)
                if not was_archived:
                    from openprogram.runtime.conversation.always_on.runtime import archive_changed
                    await asyncio.to_thread(archive_changed, sid, True)
                from openprogram.programs.applications.browser.tabs import close_conversation_tabs_soon
                close_conversation_tabs_soon(sid)
                with server._sessions_lock:
                    if sid in server._sessions:
                        server._sessions[sid]["archived"] = True
                server._broadcast(json.dumps({"type": "session_updated", "data": {"id": sid, "archived": True}}))
            result["ok"] = True
        else:
            raise ValueError("unknown project operation")
    except Exception as exc:
        result["error"] = str(exc)
    if result["ok"]:
        _projects_changed(project_id)
    await ws.send_text(json.dumps({"type": f"{action}_result", "data": result}))


ACTIONS.update({name: handle_project_operation for name in (
    "remove_project", "restore_project", "create_project_worktree", "archive_project_chats",
)})
ACTIONS["cancel_project_worktree"] = handle_cancel_project_worktree
