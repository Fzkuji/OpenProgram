"""Unified port probing + ownership diagnostics.

One home for "is this port taken, and by whom?" — previously scattered
across ``cli/commands/web.py`` (``_port_in_use`` / ``_backend_is_ours`` /
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

We never kill or auto-migrate off a held port: the port is pinned on
purpose (a stable UI URL), so the policy is reuse-if-ours / report-and-
refuse-if-not — same stance as openclaw.
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
    from openprogram.worker import paths
    from openprogram.worker.lifecycle import current_worker_pid

    if current_worker_pid() is None:
        return None
    try:
        worker_port = int(paths.port_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if worker_port != int(port):
        return False
    return backend_accepts_owner_challenge(
        port,
        expected_revision=expected_revision,
    )


def backend_accepts_owner_challenge(
    port: int,
    *,
    expected_revision: str | None = None,
    origin: str | None = None,
) -> bool:
    """Verify the active listener without sending the owner credential.

    ``origin`` names the exact Origin to verify, dialled as written. That
    matters before a token is handed out for it: proving *some* loopback
    listener is ours says nothing about the host a browser would actually
    open, which may be a proxy or a DNS name pointing somewhere else.
    Defaults to the internal client's own request Origin.
    """
    import base64
    import hmac
    import json
    import secrets
    import urllib.parse

    from openprogram.backend_endpoint import (
        OwnerAuthError,
        create_owner_challenge_proof,
        read_active_web_access,
        read_web_token,
        select_request_origin,
    )
    from openprogram.security.safe_http import configured_safe_client
    from openprogram.security.url_policy import OwnerURLException

    try:
        active_access = read_active_web_access()
        token = read_web_token()
    except OwnerAuthError:
        return False
    if active_access.port != int(port):
        return False

    request_origin = origin or select_request_origin(active_access)
    if request_origin not in active_access.effective_origins:
        return False
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    query = {"nonce": nonce}
    if expected_revision is not None:
        query["revision"] = expected_revision
    # Dial the Origin itself — no Host rewrite. The listener sees the same
    # authority the caller will later use, so a proof here is a proof for
    # that exact URL, TLS and all.
    try:
        with configured_safe_client(
            "runtime.local_probe",
            request_origin,
            owner_exception=OwnerURLException(
                consumer="runtime.local_probe", origin=request_origin
            ),
        ) as client:
            response = client.get(
                f"{request_origin}/api/auth/challenge",
                params=query,
                timeout=1.0,
            )
        response.raise_for_status()
        payload = response.json()
        expected_proof = create_owner_challenge_proof(
            token=token,
            nonce=nonce,
            revision=expected_revision or "",
        )
    except Exception:
        return False
    proof = payload.get("proof") if isinstance(payload, dict) else None
    return isinstance(proof, str) and hmac.compare_digest(proof, expected_proof)


def frontend_is_ours(port: int) -> Optional[bool]:
    """Probe ``/`` to tell OUR Next.js frontend from a squatter.

    True → answers like a Next.js app (``/_next/`` / ``__next`` /
    ``x-powered-by: Next.js``); False → something else; None →
    inconclusive.
    """
    from openprogram.security.safe_http import configured_safe_client
    from openprogram.security.url_policy import OwnerURLException

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
    from openprogram._compat import pids_on_port as compat_pids_on_port

    return compat_pids_on_port(port)


def process_cmdline(pid: int) -> str:
    """Best-effort command line of ``pid`` as one string. Empty on failure.

    Linux ``/proc/<pid>/cmdline`` → POSIX ``ps -p`` → Windows CIM.
    """
    from openprogram._compat import process_command_line

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
