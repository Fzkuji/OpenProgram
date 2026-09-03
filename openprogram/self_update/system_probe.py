"""Observe the default worker before releasing post-update verification."""
from __future__ import annotations

import hmac
import json
import secrets
import socket
import time

from .recovery import SYSTEM_CHECKS
from .types import UpdateRecord

_PORT = 18100


class SystemProbeError(RuntimeError):
    """A required observation failed; never include peer text or credentials."""


def probe_system(record: UpdateRecord) -> dict:
    """Return an identity-bound receipt only after real observations pass.

    This does not transition update state or commit an installation. The
    external supervisor owns persistence, failure recovery and finalization.
    """
    from openprogram.agent.authority import owner_principal_id
    from openprogram.backend_endpoint import read_active_web_access, read_web_token, create_owner_challenge_proof
    from openprogram.paths import get_active_profile
    from openprogram.security.safe_http import safe_client, OutboundSecurityConfig
    from openprogram.security.url_policy import OwnerURLException
    from openprogram.worker.lifecycle import current_worker_pid
    from openprogram.cli.commands.doctor import CHECKS
    from websockets.sync.client import connect

    started = time.time()
    deadline = time.monotonic() + min(60, record.request.created_at + record.request.timeout_seconds - started)
    stage = "owner_auth"
    origin = f"http://127.0.0.1:{_PORT}"

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError
        return seconds

    try:
        remaining()
        if get_active_profile() is not None:
            raise ValueError
        access = read_active_web_access()
        if access.port != _PORT or origin not in access.effective_origins:
            raise ValueError
        token = read_web_token()
        security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
        with safe_client("runtime.local_probe", configured_origin=origin, security=security,
                         timeout=min(20, remaining()), overall_timeout=remaining()) as client:
            headers = {"Authorization": f"Bearer {token}", "Origin": origin}
            nonce = secrets.token_urlsafe(32)
            proof = client.get(origin + "/api/auth/challenge", params={"nonce": nonce, "revision": record.request.candidate_sha}, timeout=min(5, remaining()))
            proof.raise_for_status()
            expected = create_owner_challenge_proof(token=token, nonce=nonce, revision=record.request.candidate_sha)
            value = proof.json().get("proof")
            if not isinstance(value, str) or not hmac.compare_digest(value, expected) or read_active_web_access() != access:
                raise ValueError

            def get(path):
                response = client.get(origin + path, headers=headers, timeout=min(20, remaining()))
                if response.status_code != 200 or str(response.url) != origin + path:
                    raise ValueError
                return response

            def identity():
                before = time.time()
                data = get("/api/diagnostics").json()
                pid = data.get("worker_pid")
                observed = data.get("checked_at")
                if (
                    data.get("status") != "ok" or data.get("database_ok") is not True
                    or data.get("revision") != record.request.candidate_sha
                    or data.get("principal_id") != owner_principal_id()
                    or type(pid) is not int or pid <= 0 or pid != current_worker_pid()
                    or type(data.get("registered_tool_count")) is not int or data["registered_tool_count"] <= 0
                    or isinstance(observed, bool) or not isinstance(observed, (float, int))
                    or not before <= observed <= time.time()
                ):
                    raise ValueError
                return pid

            stage = "identity"
            pid = identity()
            stage = "health"
            if get("/healthz").json() != {"status": "ok"}:
                raise ValueError
            stage = "web"
            web = get("/chat")
            if not web.headers.get("content-type", "").startswith("text/html") or "/_next/" not in web.text:
                raise ValueError
            stage = "doctor"
            doctor = get("/api/doctor").json()
            rows = doctor.get("results")
            if doctor.get("all_ok") is not True or not isinstance(rows, list) or not rows:
                raise ValueError
            ids = [row.get("id") for row in rows if isinstance(row, dict)]
            if (
                len(ids) != len(rows) or not all(isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids) or not {fn.__name__ for fn in CHECKS}.issubset(ids)
                or not all(row.get("ok") is True for row in rows)
            ):
                raise ValueError
            stage = "websocket"
            # A preconnected numeric loopback socket prevents proxy/DNS routing;
            # the existing library owns protocol framing and bounded close.
            with socket.create_connection(("127.0.0.1", _PORT), timeout=min(5, remaining())) as sock:
                with connect(f"ws://127.0.0.1:{_PORT}/ws", sock=sock, origin=origin,
                             additional_headers={"Authorization": headers["Authorization"]},
                             open_timeout=min(5, remaining()), close_timeout=1, max_size=1_048_576) as ws:
                    ws.send("ping")
                    while json.loads(ws.recv(timeout=min(5, remaining()))).get("type") != "pong":
                        remaining()
            stage = "identity"
            if identity() != pid or read_active_web_access() != access:
                raise ValueError
            remaining()
        return {"schema": 1, "candidate_sha": record.request.candidate_sha, "attempt": record.state.attempt,
                "worker_pid": pid, "verified_at": time.time(), "checks": {name: True for name in SYSTEM_CHECKS}}
    except Exception:
        raise SystemProbeError(f"system probe failed: {stage}") from None
