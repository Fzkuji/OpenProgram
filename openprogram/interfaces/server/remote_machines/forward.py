"""Forward a page's actions on another machine's conversations to that machine.

An action that names a conversation living on a remote OpenProgram machine
(``links.owner_of``), or a project from one (``@<machine>/<id>``), goes to
that computer's Worker unchanged but for the project id, which loses its
prefix. Each page that does so gets its own connection to that Worker, so
every reply and every frame of the conversations it opened there reach that
page and no other, as if the page were open on that computer.

What comes back is passed to the page as it is, except frames about the
other Worker itself (its functions, providers, build, its own lists): the
page is this computer's, and the lists it sees are the merged ones from
``links``. A frame that names one of this computer's conversations is
dropped. A conversation first seen in a reply (a new one started there)
belongs to that machine from then on.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openprogram.interfaces.server.remote_machines import links

_log = logging.getLogger(__name__)

#: Actions answered here even when they name a remote conversation: the merged lists.
ANSWERED_HERE = frozenset({"list_sessions", "list_projects", "list_machines"})
#: Frames about the other Worker itself, never shown to this computer's page.
WORKER_FRAMES = frozenset({
    "functions_list", "provider_info", "frontend_build", "worker_capabilities", "pong",
    "sessions_list", "projects_list", "projects_changed", "machines", "provider_changed",
    "agent_settings_changed", "always_on_update",
})


def _frame_session(frame: dict) -> str:
    data = frame.get("data")
    for source in (data if isinstance(data, dict) else {}, frame):
        value = source.get("session_id")
        if isinstance(value, str) and value:
            return value
    return ""


def target(cmd: dict) -> tuple[str, dict] | None:
    """``(machine id, command to send there)`` when ``cmd`` acts on another machine, else None."""
    action = cmd.get("action")
    if not isinstance(action, str) or action in ANSWERED_HERE:
        return None
    machine_id = links.owner_of(cmd.get("session_id"))
    sent = dict(cmd)
    project = links.split_project_key(cmd.get("project_id"))
    if project is not None:
        if machine_id not in (None, project[0]):
            return None
        machine_id = machine_id or project[0]
        sent["project_id"] = project[1]
    ids = cmd.get("session_ids")
    if machine_id is None and isinstance(ids, list) and ids:
        owners = {links.owner_of(sid) for sid in ids}
        if len(owners) == 1 and None not in owners:
            machine_id = owners.pop()
    if machine_id is None:
        return None
    if isinstance(cmd.get("session_id"), str):
        links.claim(cmd["session_id"], machine_id)
    return machine_id, sent


class Upstream:
    """One page's connection to one remote machine's Worker."""

    def __init__(self, page: Any, machine_id: str) -> None:
        self.page = page
        self.machine_id = machine_id
        self.socket = None
        self.reader: asyncio.Task | None = None
        self.opening = asyncio.Lock()

    async def send(self, text: str) -> bool:
        async with self.opening:
            if self.socket is None:
                if not await self._open():
                    return False
        try:
            await self.socket.send(text)
            return True
        except Exception:
            await self.close()
            return False

    async def _open(self) -> bool:
        from openprogram.host.machines import get_machine
        from openprogram.host.machines.peer import credential, is_paired

        machine = get_machine(self.machine_id)
        if machine is None or not machine.enabled or not is_paired(machine):
            return False
        routes, token = credential(machine)
        known = links.route_of(self.machine_id)
        if known in routes:
            routes = [known] + [route for route in routes if route != known]
        for route in routes:
            try:
                self.socket = await links.open_socket(route, token)
            except Exception:
                continue
            self.reader = asyncio.create_task(self._read(), name=f"remote-forward-{self.machine_id}")
            return True
        return False

    async def _read(self) -> None:
        socket = self.socket
        try:
            async for text in socket:
                await self._relay(text)
        except Exception:
            _log.debug("forwarded connection to %s ended", self.machine_id, exc_info=True)
        finally:
            if self.socket is socket:
                self.socket = None

    async def _relay(self, text: Any) -> None:
        if not isinstance(text, str):
            return
        try:
            frame = json.loads(text)
        except ValueError:
            return
        if not isinstance(frame, dict):
            return
        if frame.get("type") in WORKER_FRAMES:
            return
        session_id = _frame_session(frame)
        if session_id:
            owner = links.owner_of(session_id)
            if owner is None:
                if _is_local(session_id):
                    return
                links.claim(session_id, self.machine_id)
            elif owner != self.machine_id:
                return
        try:
            await self.page.send_text(text)
        except Exception:
            await self.close()

    async def close(self) -> None:
        socket, self.socket = self.socket, None
        reader, self.reader = self.reader, None
        if socket is not None:
            try:
                await socket.close()
            except Exception:
                pass
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()


def _is_local(session_id: str) -> bool:
    try:
        from openprogram.runtime.context.session_db import default_db
        return default_db().get_session(session_id) is not None
    except Exception:
        return False


def _upstreams(page: Any) -> dict[str, Upstream]:
    found = getattr(page, "_remote_upstreams", None)
    if found is None:
        found = {}
        try:
            page._remote_upstreams = found
        except AttributeError:
            pass
    return found


async def forward(page: Any, cmd: dict) -> bool:
    """Send ``cmd`` to the machine it acts on; True when it went there (or nowhere: that machine is out of reach)."""
    found = target(cmd)
    if found is None:
        return False
    machine_id, sent = found
    upstreams = _upstreams(page)
    upstream = upstreams.get(machine_id)
    if upstream is None:
        upstream = upstreams[machine_id] = Upstream(page, machine_id)
    if not await upstream.send(json.dumps(sent)):
        from openprogram.interfaces.server import server as _s
        await _s._send_operation_error(page, cmd, code="machine_unreachable", scope="system")
    return True


async def release(page: Any) -> None:
    """The page went away: close its connections to other machines."""
    upstreams = getattr(page, "_remote_upstreams", None) or {}
    for upstream in list(upstreams.values()):
        await upstream.close()
    upstreams.clear()


__all__ = ["ANSWERED_HERE", "WORKER_FRAMES", "Upstream", "forward", "release", "target"]
