"""Unified port probing + ownership diagnostics.

One home for "is this port taken, and by whom?" — previously scattered
across ``_cli_cmds/web.py`` (``_port_in_use`` / ``_backend_is_ours`` /
``_frontend_is_ours``), ``worker/web.py`` (``_pids_on_port`` /
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

We never kill or auto-migrate off a held port: the port is pinned on
purpose (a stable UI URL), so the policy is reuse-if-ours / report-and-
refuse-if-not — same stance as openclaw.
"""
from __future__ import annotations

import socket
import subprocess
import sys
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


# identity probes (HTTP signature)


def backend_is_ours(port: int) -> Optional[bool]:
    """Probe ``/healthz`` to tell OUR backend from a squatter.

    True → the port answers with openprogram's health JSON (a running
    instance — reuse it / point the user at the UI); False → it answers
    like something else; None → inconclusive (no/garbled response).
    """
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=1.0
        ) as resp:
            body = resp.read(4096)
    except Exception:
        return None
    try:
        data = json.loads(body)
    except Exception:
        return False
    # openprogram's /healthz always carries these distinctive keys
    # (see webui/routes/misc.py).
    if isinstance(data, dict) and "uptime_seconds" in data and "status" in data:
        return True
    return False


def frontend_is_ours(port: int) -> Optional[bool]:
    """Probe ``/`` to tell OUR Next.js frontend from a squatter.

    True → answers like a Next.js app (``/_next/`` / ``__next`` /
    ``x-powered-by: Next.js``); False → something else; None →
    inconclusive.
    """
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            powered = (resp.headers.get("x-powered-by") or "").lower()
            body = resp.read(4096).decode("utf-8", "replace")
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
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in (res.stdout or "").splitlines():
            parts = line.split()
            # "  TCP    0.0.0.0:3000   0.0.0.0:0   LISTENING   1234"
            if len(parts) < 5 or parts[3] != "LISTENING":
                continue
            if not parts[1].endswith(needle):
                continue
            try:
                pids.append(int(parts[4]))
            except ValueError:
                pass
        return pids

    try:
        out = subprocess.run(
            ["lsof", "-iTCP:%d" % port, "-sTCP:LISTEN", "-nP", "-Fp"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(line[1:]) for line in out.stdout.splitlines() if line.startswith("p")]


def process_cmdline(pid: int) -> str:
    """Best-effort command line of ``pid`` as one string. Empty on failure.

    Linux ``/proc/<pid>/cmdline`` → POSIX ``ps -p`` → Windows ``wmic``.
    """
    if sys.platform != "win32":
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        except OSError:
            pass
        try:
            ps = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=2,
            )
            return ps.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    try:
        wm = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}",
             "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in (wm.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("CommandLine="):
            return line[len("CommandLine="):].strip()
    return ""


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
