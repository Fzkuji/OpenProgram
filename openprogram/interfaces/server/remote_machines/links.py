"""One connection per remote OpenProgram machine: its lists and its state.

For every switched-on machine with an ``openprogram`` connection this
Worker keeps one WebSocket to that computer's Worker, signed in with the
device credential from pairing. It asks for that computer's conversations
and projects, holds the answers in memory while connected, and asks again
whenever that computer says something changed. Nothing is written to disk.

Lists here are this computer's items followed by each connected machine's:
a conversation row carries the ``machine_id`` it lives on; a project's id
becomes ``@<machine>/<id>`` so it never meets one of this computer's, and
its ``machine_id`` is that machine, so the sidebar groups and marks it as
it does a project on an SSH host. ``owner_of`` answers which machine a
conversation id lives on, for ``forward``.

A Worker that does not name the ``remote_machine`` capability is not
connected: the machine shows that it needs an update and nothing is
forwarded to it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

_log = logging.getLogger(__name__)

#: Seconds between attempts while a machine cannot be reached.
RETRY_SECONDS = 5.0
#: Seconds a burst of change notices waits before the lists are asked for again.
REFRESH_DELAY_SECONDS = 0.4
#: Frames from the other computer that mean its lists changed.
CHANGE_FRAMES = frozenset({
    "sessions_list", "projects_changed", "session_updated", "session_alias_changed",
    "running_task", "running_task_clear", "chat_ack",
})
NEEDS_UPDATE = "The other computer's OpenProgram needs an update before it can be used from here."

_lock = threading.Lock()
# machine id -> rows / projects as that computer sent them
_sessions: dict[str, list[dict]] = {}
_projects: dict[str, list[dict]] = {}
# conversation id -> machine id, for conversations listed or created there
_owner: dict[str, str] = {}
# machine id -> the address that answered last
_route: dict[str, str] = {}
_tasks: dict[str, asyncio.Task] = {}
_wakeups: dict[str, asyncio.Event] = {}
_stop: asyncio.Event | None = None


def project_key(machine_id: str, project_id: str) -> str:
    return f"@{machine_id}/{project_id}"


def split_project_key(value: Any) -> tuple[str, str] | None:
    """``(machine id, project id)`` for a project id from another machine, else None."""
    text = str(value or "")
    if not text.startswith("@") or "/" not in text:
        return None
    machine_id, project_id = text[1:].split("/", 1)
    return (machine_id, project_id) if machine_id and project_id else None


def owner_of(session_id: Any) -> str | None:
    """The machine a conversation lives on, when it is not this computer."""
    with _lock:
        return _owner.get(str(session_id or ""))


def claim(session_id: str, machine_id: str) -> None:
    """A conversation created on ``machine_id`` from here lives there from its first frame."""
    if session_id:
        with _lock:
            _owner.setdefault(session_id, machine_id)


def route_of(machine_id: str) -> str:
    with _lock:
        return _route.get(machine_id, "")


def remote_session_rows(local_ids: set[str]) -> list[dict]:
    """Every connected machine's conversation rows, marked with the machine; a local id wins."""
    with _lock:
        snapshot = {machine_id: list(rows) for machine_id, rows in _sessions.items()}
    rows: list[dict] = []
    for machine_id, items in snapshot.items():
        for row in items:
            if isinstance(row, dict) and row.get("id") and row["id"] not in local_ids:
                rows.append({**row, "machine_id": machine_id})
    return rows


def remote_projects(local_session_ids: set[str]) -> list[dict]:
    """Every connected machine's projects, under ids of their own and marked with the machine."""
    with _lock:
        snapshot = {machine_id: list(items) for machine_id, items in _projects.items()}
    projects: list[dict] = []
    for machine_id, items in snapshot.items():
        for project in items:
            if not isinstance(project, dict) or not project.get("id"):
                continue
            ids = [sid for sid in project.get("session_ids") or [] if sid not in local_session_ids]
            projects.append({
                **project,
                "id": project_key(machine_id, str(project["id"])),
                "machine_id": machine_id,
                "is_default": False,
                "session_ids": ids,
                "session_count": len(ids),
                "remote": True,
            })
    return projects


def remote_project_for(session_id: str) -> str | None:
    machine_id = owner_of(session_id)
    if machine_id is None:
        return None
    with _lock:
        items = list(_projects.get(machine_id) or [])
    for project in items:
        if session_id in (project.get("session_ids") or []):
            return project_key(machine_id, str(project["id"]))
    default = next((p for p in items if p.get("is_default")), None)
    return project_key(machine_id, str(default["id"])) if default else None


# ---- what pages are told -------------------------------------------------------


def _lists_changed() -> None:
    """Every page lists again: the merged conversations now, the projects on request."""
    try:
        from openprogram.interfaces.server import server as _s
        from openprogram.interfaces.server.ws_actions.session.listing import broadcast_sessions_list
        broadcast_sessions_list()
        _s._broadcast(json.dumps({"type": "projects_changed", "data": {}}))
    except Exception:
        _log.debug("remote list broadcast failed", exc_info=True)


def _forget(machine_id: str) -> None:
    with _lock:
        had = machine_id in _sessions or machine_id in _projects
        _sessions.pop(machine_id, None)
        _projects.pop(machine_id, None)
        _route.pop(machine_id, None)
        for sid in [sid for sid, owner in _owner.items() if owner == machine_id]:
            _owner.pop(sid, None)
    if had:
        _lists_changed()


def _store(machine_id: str, kind: str, items: list[dict]) -> None:
    with _lock:
        if kind == "sessions":
            _sessions[machine_id] = items
            for row in items:
                if isinstance(row, dict) and row.get("id"):
                    _owner[str(row["id"])] = machine_id
        else:
            _projects[machine_id] = items
    _lists_changed()


# ---- the connection --------------------------------------------------------------


def ws_url(route: str) -> str:
    return ("wss://" + route[len("https://"):] if route.startswith("https://")
            else "ws://" + route[len("http://"):]) + "/ws"


async def open_socket(route: str, token: str):
    """A WebSocket to another computer's Worker, signed in as this paired computer."""
    from websockets.asyncio.client import connect

    return await connect(ws_url(route), additional_headers={"Authorization": f"Bearer {token}"},
                         open_timeout=8, max_size=None, proxy=None, ping_interval=20)


def _machine(machine_id: str):
    from openprogram.host.machines import get_machine
    machine = get_machine(machine_id)
    if machine is None or not machine.enabled or machine.connection("openprogram") is None:
        return None
    return machine


def _note(machine, ok: bool, message: str = "") -> None:
    from openprogram.host.machines.status import note
    note(machine, ok, message)


async def _session(machine_id: str, socket) -> None:
    """Read one connection until it closes: lists in, change notices answered with a new ask."""
    from openprogram.interfaces.server.handshake import REMOTE_CAPABILITY

    pending: asyncio.TimerHandle | None = None
    loop = asyncio.get_running_loop()
    ready = False

    async def ask() -> None:
        await socket.send(json.dumps({"action": "list_sessions", "local_only": True}))
        await socket.send(json.dumps({"action": "list_projects", "local_only": True}))

    def ask_later() -> None:
        nonlocal pending
        if pending is None:
            def fire() -> None:
                nonlocal pending
                pending = None
                asyncio.ensure_future(ask())
            pending = loop.call_later(REFRESH_DELAY_SECONDS, fire)

    try:
        async for text in socket:
            try:
                frame = json.loads(text)
            except (TypeError, ValueError):
                continue
            kind = frame.get("type") if isinstance(frame, dict) else None
            if kind == "worker_capabilities":
                capabilities = (frame.get("server") or {}).get("capabilities") or []
                machine = _machine(machine_id)
                if REMOTE_CAPABILITY not in capabilities:
                    if machine is not None:
                        _note(machine, False, NEEDS_UPDATE)
                    return
                ready = True
                if machine is not None:
                    _note(machine, True, route_of(machine_id))
                await ask()
            elif not ready:
                continue
            elif kind == "sessions_list" and isinstance(frame.get("data"), list):
                _store(machine_id, "sessions", [row for row in frame["data"] if isinstance(row, dict)])
            elif kind == "projects_list":
                projects = (frame.get("data") or {}).get("projects")
                if isinstance(projects, list):
                    _store(machine_id, "projects", [p for p in projects if isinstance(p, dict)])
            elif kind in CHANGE_FRAMES:
                ask_later()
    finally:
        if pending is not None:
            pending.cancel()


async def _run(machine_id: str, stop: asyncio.Event, wake: asyncio.Event) -> None:
    from openprogram.host.machines import secret
    from openprogram.host.machines.peer import routes_of

    while not stop.is_set():
        machine = _machine(machine_id)
        if machine is None:
            return
        token = secret(machine, "openprogram", "token")
        reason = "not paired"
        for route in routes_of(machine.connection("openprogram").settings) if token else []:
            try:
                socket = await open_socket(route, token)
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                reason = ("the other computer revoked this one; pair again" if status in (401, 403)
                          else f"{route}: {exc.__class__.__name__}")
                continue
            with _lock:
                _route[machine_id] = route
            try:
                async with socket:
                    await _session(machine_id, socket)
                reason = "the connection closed"
            except Exception as exc:
                reason = f"{route}: {exc.__class__.__name__}"
            break
        machine = _machine(machine_id)
        _forget(machine_id)
        if machine is None:
            return
        _note(machine, False, reason)
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), RETRY_SECONDS)
        except asyncio.TimeoutError:
            pass
    _forget(machine_id)


def sync() -> None:
    """Start a connection for each remote machine that has none; end those whose machine is gone.

    Safe from any thread; runs on the server loop.
    """
    from openprogram.interfaces.server import server as _s
    loop = getattr(_s, "_loop", None)
    if loop is None or loop.is_closed() or _stop is None:
        return
    loop.call_soon_threadsafe(_sync_on_loop)


def _sync_on_loop() -> None:
    from openprogram.host.machines import list_machines
    if _stop is None or _stop.is_set():
        return
    wanted = {machine.id for machine in list_machines()
              if machine.enabled and machine.connection("openprogram") is not None}
    for machine_id, task in list(_tasks.items()):
        if task.done() or machine_id not in wanted:
            task.cancel()
            _tasks.pop(machine_id, None)
            _wakeups.pop(machine_id, None)
            _forget(machine_id)
    for machine_id in wanted:
        if machine_id in _tasks:
            _wakeups[machine_id].set()  # an edited machine tries again now
            continue
        wake = asyncio.Event()
        _wakeups[machine_id] = wake
        _tasks[machine_id] = asyncio.create_task(_run(machine_id, _stop, wake),
                                                 name=f"remote-machine-{machine_id}")


async def run_remote_machines(stop: asyncio.Event) -> None:
    """Keep the connections for as long as the Worker runs."""
    global _stop
    _stop = stop
    try:
        _sync_on_loop()
        await stop.wait()
    finally:
        for task in list(_tasks.values()):
            task.cancel()
        if _tasks:
            await asyncio.gather(*_tasks.values(), return_exceptions=True)
        _tasks.clear()
        _wakeups.clear()
        for machine_id in list(_sessions) + list(_projects):
            _forget(machine_id)


__all__ = ["claim", "open_socket", "owner_of", "project_key", "remote_project_for", "remote_projects",
           "remote_session_rows", "route_of", "run_remote_machines", "split_project_key", "sync", "ws_url"]
