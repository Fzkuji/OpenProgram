"""Unified port probing + ownership diagnostics.

One home for "is this port taken, and by whom?" — previously scattered
across ``cli/commands/services/web.py`` (``_port_in_use`` / ``_backend_is_ours`` /
``_frontend_is_ours``), ``webui/frontend.py`` (``_pids_on_port`` /
``_process_cmdline``) and ``worker/lifecycle.py`` (``_probe_tcp_listening``).

Mirrors the three things openclaw does around its fixed gateway port
(``src/infra/gateway-lock.ts``, ``server/http-listen.ts``,
``src/infra/ports.ts``):

  * liveness probe of the port (``port_in_use``);
  * identity probe — is the holder *ours*? (``backend_is_ours`` /
    ``frontend_is_ours``, by HTTP signature rather than a lock file);
  * owner diagnostic — *who* holds it, by PID + command line
    (``describe_port_owner`` / ``port_owner_hint``), so a "port in use"
    error can name the squatter instead of saying "another process".

We never kill a holder and never attach to one that is not ours. A port
the profile chose for itself (``web-port``) moves to the next free one when
something else holds it at start; a port the user set (``ui.web_port`` or
``OPENPROGRAM_WEB_PORT``) is refused with the holder named.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Optional


# liveness probe


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    """True when something is accepting connections on ``host:port``.

    A bare TCP connect: succeeds → in use; refused/timeout/error → free.
    This only answers "is something there", not "is it ours" (see
    ``backend_is_ours`` / ``frontend_is_ours``) nor "who is it"
    (see ``describe_port_owner``).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def port_bindable(port: int, host: str = "127.0.0.1") -> bool:
    """True iff this process could listen on ``host:port`` right now.

    POSIX sets ``SO_REUSEADDR`` so a port left in ``TIME_WAIT`` by a worker
    that just stopped counts as free; it never lets two listeners share a
    port there. On Windows ``SO_REUSEADDR`` would let the probe bind over a
    live listener and report a held port as free, so the probe asks for
    exclusive use instead (a stopped listener's port binds again at once).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


# The ports a profile picks from when it chooses its own (the development
# instance's 18200 is outside them).
FIRST_WEB_PORT = 18100
WEB_PORT_SPAN = 100


def first_free_web_port(exclude: frozenset[int] = frozenset()) -> int:
    """The lowest port from 18100 up that nothing listens on."""
    for port in range(FIRST_WEB_PORT, FIRST_WEB_PORT + WEB_PORT_SPAN):
        if port not in exclude and port_bindable(port):
            return port
    raise OSError(
        f"no free port between {FIRST_WEB_PORT} and {FIRST_WEB_PORT + WEB_PORT_SPAN - 1}"
    )


# identity probes (HTTP signature)


def backend_is_ours(
    port: int,
    *,
    expected_revision: str | None = None,
) -> Optional[bool]:
    """Verify a listener with the worker files and a token-HMAC challenge.

    The request sends only a random nonce. The owner token remains local and is
    used to verify the response, so an unrelated process holding the port
    cannot obtain the credential from this identity probe.
    """
    from openprogram.host.worker import paths
    from openprogram.host.worker.process import current_worker_pid

    if current_worker_pid() is None:
        return None
    try:
        worker_port = int(paths.port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if worker_port != int(port):
        return False
    from openprogram.host.security import backend_endpoint

    return backend_endpoint.backend_accepts_owner_challenge(
        port,
        expected_revision=expected_revision,
    )


def frontend_is_ours(port: int) -> Optional[bool]:
    """Probe ``/`` to tell OUR Next.js frontend from a squatter.

    True → answers like a Next.js app (``/_next/`` / ``__next`` /
    ``x-powered-by: Next.js``); False → something else; None →
    inconclusive.
    """
    from openprogram.host.security.safe_http import configured_safe_client
    from openprogram.host.security.url_policy import OwnerURLException

    origin = f"http://127.0.0.1:{port}"
    try:
        with configured_safe_client(
            "runtime.local_probe",
            origin,
            owner_exception=OwnerURLException(
                consumer="runtime.local_probe", origin=origin
            ),
        ) as client:
            resp = client.get(origin + "/", timeout=1.0)
        powered = (resp.headers.get("x-powered-by") or "").lower()
        body = resp.text[:4096]
    except Exception:
        return None
    if "next" in powered or "/_next/" in body or "__next" in body:
        return True
    return False


# owner diagnostic (PID + command line)


def pids_on_port(port: int) -> list[int]:
    """PIDs listening on TCP ``port``. Empty on any error / no match.

    POSIX: ``lsof -iTCP:<port> -sTCP:LISTEN -nP -Fp``.
    Windows: ``netstat -ano -p TCP`` → LISTENING rows, last column is PID.
    """
    from openprogram.host._platform.processes import pids_on_port as compat_pids_on_port

    return compat_pids_on_port(port)


def process_cmdline(pid: int) -> str:
    """Best-effort command line of ``pid`` as one string. Empty on failure.

    Linux ``/proc/<pid>/cmdline`` → POSIX ``ps -p`` → Windows CIM.
    """
    from openprogram.host._platform.processes import process_command_line

    return process_command_line(pid)


@dataclass
class PortOwner:
    """Who holds a port. ``kind`` classifies the holder; ``detail`` is a
    human ``PID nnn: <cmdline>`` summary for error messages."""
    pids: list[int]
    kind: str  # "openprogram" | "next" | "node" | "other"
    detail: str

    @property
    def is_ours(self) -> bool:
        return self.kind in ("openprogram", "next")


def with_port_owner_note(key: str, result: dict) -> dict:
    """Add ``note`` to a ``set_setting`` result when ``ui.web_port`` names a taken port.

    The settings editor and ``openprogram config set`` warn "that port is
    taken by <who>" right after saving, the way ``openprogram ports`` does.
    """
    if key != "ui.web_port" or result.get("error"):
        return result
    try:
        owner = describe_port_owner(int(result.get("value")))
    except (TypeError, ValueError, OSError):
        return result
    if owner is not None and not owner.is_ours:
        result["note"] = f"port {result['value']} is currently held by {owner.detail}"
    return result


def describe_port_owner(port: int) -> Optional[PortOwner]:
    """Who is listening on ``port``? None when free / undeterminable.

    Classifies by command line so a "port in use" message can say whether
    it's our own backend/frontend or a foreign program — openclaw's
    ``describePortOwner`` (``src/infra/ports.ts``), via lsof/netstat.
    """
    pids = pids_on_port(port)
    if not pids:
        return None
    kind = "other"
    parts: list[str] = []
    for pid in pids:
        cmd = process_cmdline(pid)
        low = cmd.lower()
        if "openprogram" in low or "uvicorn" in low or "webui" in low:
            kind = "openprogram"
        elif "next-server" in low or "next/dist/bin/next" in low or "next dev" in low:
            if kind != "openprogram":
                kind = "next"
        elif "node" in low and kind == "other":
            kind = "node"
        short = (cmd[:140] + "…") if len(cmd) > 140 else cmd
        parts.append(f"PID {pid}: {short or '(command line unavailable)'}")
    return PortOwner(pids=pids, kind=kind, detail="; ".join(parts))


def port_owner_hint(port: int) -> str:
    """One-line "held by …" suffix for error messages, or "" if we can't
    tell (lsof/netstat unavailable, or nothing listening)."""
    owner = describe_port_owner(port)
    if owner is None:
        return ""
    return f"  Held by — {owner.detail}"
