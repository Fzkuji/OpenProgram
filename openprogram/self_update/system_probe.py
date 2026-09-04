"""Observe the default worker before releasing post-update verification."""
from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import time

from .recovery import SYSTEM_CHECKS
from .types import UpdateRecord

_PORT = 18100
OBSERVATION_ENTRIES = frozenset({"/api/commands", "/api/diagnostics", "/api/doctor", "/healthz", "/chat"})
_OBSERVATION_BYTES = 262_144


class SystemProbeError(RuntimeError):
    """A required observation failed; never include peer text or credentials."""


def probe_system(record: UpdateRecord) -> dict:
    """Return an identity-bound receipt only after real observations pass.

    This does not transition update state or commit an installation. The
    external supervisor owns persistence, failure recovery and finalization.
    """
    return _probe_system(record, record.request.candidate_sha,
                         min(60, record.request.created_at + record.request.timeout_seconds - time.time()))


def probe_current_system(record: UpdateRecord) -> dict:
    """Observe the old live revision; the source base is not its identity."""
    return _probe_system(record, None,
                         min(60, record.request.created_at + record.request.timeout_seconds - time.time()))


def probe_restored_system(record: UpdateRecord, revision: str) -> dict:
    """Recovery has its own bounded window even if the update timed out."""
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", revision):
        raise SystemProbeError("system probe failed: expected revision")
    return _probe_system(record, revision, 60)


def probe_committed_system(record: UpdateRecord) -> dict:
    """Verify a previously committed candidate after its acceptance window ended."""
    return _probe_system(record, record.request.candidate_sha, 60)


def observe_system(record: UpdateRecord, entry: str) -> dict:
    """Read a reviewed local entry with identity checks before and after I/O."""
    if entry not in OBSERVATION_ENTRIES:
        raise ValueError("unsupported read-only observation entry")
    return _probe_system(record, record.request.candidate_sha,
                         min(60, record.request.created_at + record.request.timeout_seconds - time.time()), entry)


def _probe_system(record: UpdateRecord, revision: str | None, timeout: float, observation_entry: str | None = None) -> dict:
    import httpx
    from openprogram.agent.authority import owner_principal_id
    from openprogram.backend_endpoint import read_active_web_access, read_web_token, create_owner_challenge_proof
    from openprogram.paths import get_active_profile
    from openprogram.security.safe_http import safe_client, OutboundSecurityConfig
    from openprogram.security.url_policy import OwnerURLException
    from openprogram.worker.lifecycle import current_worker_pid
    from openprogram.cli.commands.doctor import CHECKS
    from websockets.sync.client import connect

    deadline = time.monotonic() + timeout
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
            headers = {"Authorization": f"Bearer {token}", "Origin": origin, "Accept-Encoding": "identity"}
            def bounded_get(path, *, authenticated=True, params=None, limit=1_048_576):
                request_headers = headers if authenticated else {"Accept-Encoding": "identity"}
                with client.stream("GET", origin + path, headers=request_headers, params=params,
                                   timeout=min(20, remaining()), follow_redirects=False) as response:
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        remaining()
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ValueError
                    if token.encode() in body or any(token in value for value in response.headers.values()):
                        raise ValueError  # Never retain or expose a peer-echoed owner credential.
                    return httpx.Response(response.status_code, headers=response.headers, content=bytes(body), request=response.request)
            nonce = secrets.token_urlsafe(32)
            # Legacy/dirty old revisions use the same owner proof followed by
            # strict authenticated diagnostics. Candidate proof stays SHA-bound.
            challenge_revision = revision if revision and re.fullmatch(r"[0-9a-f]{40}", revision) else ""
            proof = bounded_get("/api/auth/challenge", authenticated=False,
                                params={"nonce": nonce, "revision": challenge_revision}, limit=4096)
            proof.raise_for_status()
            expected = create_owner_challenge_proof(token=token, nonce=nonce, revision=challenge_revision)
            value = proof.json().get("proof")
            if not isinstance(value, str) or not hmac.compare_digest(value, expected) or read_active_web_access() != access:
                raise ValueError

            def get(path):
                response = bounded_get(path)
                if response.status_code != 200 or str(response.url) != origin + path:
                    raise ValueError
                return response

            def identity():
                before = time.time()
                data = get("/api/diagnostics").json()
                pid = data.get("worker_pid")
                observed = data.get("checked_at")
                observed_revision = data.get("revision")
                if (
                    data.get("status") != "ok" or data.get("database_ok") is not True
                    or not isinstance(observed_revision, str)
                    or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", observed_revision)
                    or (revision is not None and observed_revision != revision)
                    or data.get("principal_id") != owner_principal_id()
                    or type(pid) is not int or pid <= 0 or pid != current_worker_pid()
                    or type(data.get("registered_tool_count")) is not int or data["registered_tool_count"] <= 0
                    or isinstance(observed, bool) or not isinstance(observed, (float, int))
                    or not before <= observed <= time.time()
                ):
                    raise ValueError
                return pid, observed_revision

            stage = "identity"
            pid, observed_revision = identity()
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
            observation = None
            if observation_entry is not None:
                stage = "observation"
                response = bounded_get(observation_entry, limit=_OBSERVATION_BYTES)
                if str(response.url) != origin + observation_entry:
                    raise ValueError
                observation = {"entry": observation_entry, "status": response.status_code,
                               "content_type": response.headers.get("content-type", "")[:256],
                               "body": response.content.decode("utf-8", errors="replace"), "observed_at": time.time()}
            stage = "identity"
            if identity() != (pid, observed_revision) or read_active_web_access() != access:
                raise ValueError
            remaining()
        gate = {"schema": 1, "candidate_sha": observed_revision, "attempt": record.state.attempt,
                "worker_pid": pid, "verified_at": time.time(), "checks": {name: True for name in SYSTEM_CHECKS}}
        return gate if observation_entry is None else {"system_gate": gate, "observation": observation}
    except Exception:
        raise SystemProbeError(f"system probe failed: {stage}") from None
