"""Observe the default worker before releasing post-update verification."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import time
from typing import Mapping

from ..control.recovery import SYSTEM_CHECKS
from ..types import UpdateRecord

# A fixed port for tests; otherwise a target without a port is this profile's
# own worker, on the port the profile settled on (worker/lifecycle.py).
_PORT: int | None = None


def _target_port(target: "ProbeTarget") -> int:
    if target.port:
        return target.port
    if _PORT:
        return _PORT
    from openprogram.operations.worker.lifecycle import resolve_worker_port

    return resolve_worker_port()
OBSERVATION_ENTRIES = frozenset({"/api/commands", "/api/diagnostics", "/api/doctor", "/healthz", "/chat"})
# A page of the served docs site, e.g. /docs/install/upgrade.html.
DOCS_PAGE = "/docs/<path>.html"
_DOCS_PAGE = re.compile(r"/docs/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*\.html")
_OBSERVATION_BYTES = 262_144


class SystemProbeError(RuntimeError):
    """A required observation failed; never include peer text or credentials."""


@dataclass(frozen=True)
class ProbeTarget:
    """Which running instance to observe; by default this profile's own worker.

    ``state_dir`` names another instance's state directory, read for its web
    access snapshot, owner and worker lock; ``token`` overrides the token found
    there. ``doctor_baseline`` applies the regression rule: it maps each doctor
    row of the instance before the change to whether it was OK, and only a row
    that was OK and no longer is fails the probe. ``None`` keeps the strict
    rule that every row must be OK.
    """

    port: int | None = None
    state_dir: Path | None = None
    token: str | None = None
    doctor_baseline: Mapping[str, bool] | None = None


def probe_system(record: UpdateRecord) -> dict:
    """Return an identity-bound receipt only after real observations pass.

    This does not transition update state or commit an installation. The
    external supervisor owns persistence, failure recovery and finalization.
    """
    return _probe_system(record, record.request.candidate_sha,
                         min(60, record.request.created_at + record.request.timeout_seconds - time.time()),
                         require_loaded=_restarts_worker(record))


def probe_current_system(record: UpdateRecord) -> dict:
    """Observe the old live revision; the source base is not its identity."""
    return _probe_system(record, None,
                         min(60, record.request.created_at + record.request.timeout_seconds - time.time()))


def _restarts_worker(record: UpdateRecord) -> bool:
    """Whether activation restarts the worker, so it must run the new code.

    A hot swap keeps the worker process and replaces only files it reads per
    request; its startup revision then stays at the previous commit.
    """
    from ..delivery.activation import load_plan
    from ..store import SelfUpdateStore

    try:
        plan = load_plan(SelfUpdateStore().root / record.request.update_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemProbeError("system probe failed: activation plan") from exc
    return plan is None or plan.restart != "hot"


def probe_restored_system(record: UpdateRecord, revision: str) -> dict:
    """Recovery has its own bounded window even if the update timed out."""
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", revision):
        raise SystemProbeError("system probe failed: expected revision")
    return _probe_system(record, revision, 60, require_loaded=_restarts_worker(record))


def probe_committed_system(record: UpdateRecord) -> dict:
    """Verify a previously committed candidate after its acceptance window ended."""
    return _probe_system(record, record.request.candidate_sha, 60, require_loaded=_restarts_worker(record))


def probe_unchanged_system(record: UpdateRecord) -> dict:
    """Check the unchanged runtime after an aborted update's window expired."""
    gate = _probe_system(record, None, 60)
    if gate["candidate_sha"] == record.request.candidate_sha:
        raise SystemProbeError("aborted update is running the candidate")
    return gate


def request_dispatch(record: UpdateRecord, *, timeout: float = 120, target: "ProbeTarget | None" = None) -> None:
    """Ask the running worker to admit the verifier or diagnosis.

    After a hot swap that is the worker this update changed; during a trial it
    is the live worker named by ``target``, because the Job runs there.
    """
    from openprogram.host.security.backend_endpoint import read_active_web_access, read_web_token
    from openprogram.state.paths import get_active_profile
    from openprogram.host.security.safe_http import safe_client, OutboundSecurityConfig
    from openprogram.host.security.url_policy import OwnerURLException

    target = target or ProbeTarget()
    port = _target_port(target)
    origin = f"http://127.0.0.1:{port}"
    try:
        if target.state_dir is None and get_active_profile() is not None:
            raise ValueError
        access = read_active_web_access(target.state_dir)
        if access.port != port or origin not in access.effective_origins:
            raise ValueError
        token = target.token or read_web_token(target.state_dir)
        security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
        with safe_client("runtime.local_probe", configured_origin=origin, security=security,
                         timeout=timeout, overall_timeout=timeout) as client:
            response = client.post(
                f"{origin}/api/self-updates/{record.request.update_id}/dispatch",
                headers={"Authorization": f"Bearer {token}", "Origin": origin},
                timeout=timeout, follow_redirects=False,
            )
        if response.status_code != 200 or response.json() != {"dispatched": True}:
            raise ValueError
    except Exception:
        raise SystemProbeError("hot-swap dispatch failed") from None


def post_worker(path: str, target: ProbeTarget, *, timeout: float = 120) -> dict:
    """POST to the owner route of a running worker and return its JSON."""
    from openprogram.host.security.backend_endpoint import read_web_token
    from openprogram.host.security.safe_http import safe_client, OutboundSecurityConfig
    from openprogram.host.security.url_policy import OwnerURLException

    origin = f"http://127.0.0.1:{_target_port(target)}"
    try:
        token = target.token or read_web_token(target.state_dir)
        security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
        with safe_client("runtime.local_probe", configured_origin=origin, security=security,
                         timeout=timeout, overall_timeout=timeout) as client:
            response = client.post(f"{origin}{path}", headers={"Authorization": f"Bearer {token}", "Origin": origin},
                                   timeout=timeout, follow_redirects=False)
        response.raise_for_status()
        return response.json()
    except Exception:
        raise SystemProbeError(f"worker request failed: {path}") from None


def observable(entry) -> bool:
    """A reviewed read-only HTTP entry: a fixed route or a docs site page."""
    return isinstance(entry, str) and (entry in OBSERVATION_ENTRIES or (
        _DOCS_PAGE.fullmatch(entry) is not None and ".." not in entry))


def trial_target(record: UpdateRecord) -> ProbeTarget | None:
    """The instance a verifier observes while the update is in its trial."""
    from ..types import UpdatePhase

    if record.state.phase is not UpdatePhase.TRIAL:
        return None
    detail = record.state.detail.get("trial")
    if not isinstance(detail, dict) or type(detail.get("port")) is not int or not isinstance(detail.get("state_dir"), str):
        raise SystemProbeError("system probe failed: trial target")
    return ProbeTarget(port=detail["port"], state_dir=Path(detail["state_dir"]))


def observe_system(record: UpdateRecord, entry: str, *, timeout_seconds: float = 60,
                   max_output_bytes: int = _OBSERVATION_BYTES) -> dict:
    """Read a reviewed local entry with identity checks before and after I/O."""
    if not observable(entry):
        raise ValueError("unsupported read-only observation entry")
    target = trial_target(record)
    return _probe_system(record, record.request.candidate_sha,
                         min(60, timeout_seconds, record.request.created_at + record.request.timeout_seconds - time.time()),
                         entry, max_output_bytes, require_loaded=target is not None or _restarts_worker(record),
                         target=target)


def probe_instance(record: UpdateRecord, target: ProbeTarget, *, revision: str | None = None,
                   timeout: float = 60, require_loaded: bool = True) -> dict:
    """Observe another instance by port and state directory."""
    return _probe_system(record, revision, timeout, require_loaded=require_loaded, target=target)


def doctor_baseline(record: UpdateRecord, target: ProbeTarget, *, timeout: float = 60) -> dict[str, bool]:
    """Doctor rows of an instance before a change, for the regression rule.

    An instance that cannot be observed gives no baseline, so no row can
    regress from it.
    """
    try:
        probe = _probe_system(record, None, timeout, "/api/doctor", 1_048_576,
                              target=replace(target, doctor_baseline={}))
        rows = json.loads(probe["observation"]["body"]).get("results")
        return {row["id"]: row.get("ok") is True for row in rows
                if isinstance(row, dict) and isinstance(row.get("id"), str)}
    except (SystemProbeError, ValueError, TypeError, AttributeError, KeyError):
        return {}


_BROWSER_PROBE = """
import sys
url = sys.stdin.read().strip()
try:
    from playwright.sync_api import sync_playwright
    driver = sync_playwright().start()
    try:  # the full browser; a runtime may ship without the headless shell
        browser = driver.chromium.launch(headless=True, channel="chromium")
    except Exception:
        browser = driver.chromium.launch(headless=True)
except Exception:
    raise SystemExit(3)
page = browser.new_page()
page.goto(url, wait_until="load", timeout=30000)
page.wait_for_selector('[contenteditable="true"]:visible, textarea:visible, .center-empty-btn:visible', timeout=25000)
browser.close()
driver.stop()
print("BROWSER_PROBE_OK")
"""


def probe_browser(target: ProbeTarget, *, python: str, browsers: Path | None, timeout: float = 70) -> str:
    """Load the signed-in Web app in Chromium until the message box appears.

    A fresh browser has no open tab, so there the empty page's New chat button
    counts: clicking it would leave a conversation in the instance. Only visible
    elements count, because the page also holds hidden text fields.

    Returns ``passed``, or ``skipped`` when this runtime has no Chromium to
    drive, which says nothing about the instance.
    """
    from openprogram.host.security.backend_endpoint import build_owner_auth_url
    from openprogram.host.security.backend_endpoint import read_active_web_access, read_web_token

    origin = f"http://127.0.0.1:{_target_port(target)}"
    try:
        access = read_active_web_access(target.state_dir)
        token = target.token or read_web_token(target.state_dir)
        url = build_owner_auth_url(origin, token=token, effective_origins=access.effective_origins)
    except Exception:
        raise SystemProbeError("browser probe failed: owner_auth") from None
    environment = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    if browsers is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    try:
        result = subprocess.run([python, "-I", "-B", "-c", _BROWSER_PROBE], input=url, capture_output=True,
                                text=True, timeout=timeout, env=environment)
    except subprocess.TimeoutExpired:
        raise SystemProbeError("browser probe failed: timeout") from None
    if result.returncode == 3:
        return "skipped"
    if result.returncode != 0 or result.stdout.strip() != "BROWSER_PROBE_OK":
        raise SystemProbeError("browser probe failed: page")
    return "passed"


def _instance_owner(target: ProbeTarget) -> str:
    from openprogram.runtime.permissions.authority import owner_principal_id

    if target.state_dir is None:
        return owner_principal_id()
    value = json.loads((Path(target.state_dir) / "owner.json").read_text(encoding="utf-8"))
    return value["principal_id"]


def _instance_worker_pid(target: ProbeTarget) -> int | None:
    from openprogram.operations.worker.lifecycle import current_worker_pid

    if target.state_dir is None:
        return current_worker_pid()
    raw = (Path(target.state_dir) / "worker.lock").read_text(encoding="utf-8").strip()
    return int(raw) if raw else None


def _probe_system(record: UpdateRecord, revision: str | None, timeout: float, observation_entry: str | None = None,
                  observation_bytes: int = _OBSERVATION_BYTES, *, require_loaded: bool = False,
                  target: ProbeTarget | None = None, through_health: bool = False) -> dict:
    """Probe the worker; its identity is the installed revision it serves.

    ``require_loaded`` additionally requires the worker's startup revision to
    equal the installed one, i.e. the process restarted onto these files.
    ``through_health`` stops after identity and ``/healthz``, for a question
    that is only whether this worker is up and who it is.
    """
    import httpx
    from openprogram.host.security.backend_endpoint import read_active_web_access, read_web_token, create_owner_challenge_proof
    from openprogram.state.paths import get_active_profile
    from openprogram.host.security.safe_http import safe_client, OutboundSecurityConfig
    from openprogram.host.security.url_policy import OwnerURLException
    from openprogram.operations.diagnostics.checks import CHECKS
    from websockets.sync.client import connect

    target = target or ProbeTarget()
    port = _target_port(target)
    deadline = time.monotonic() + timeout
    stage = "owner_auth"
    origin = f"http://127.0.0.1:{port}"

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError
        return seconds

    try:
        remaining()
        if target.state_dir is None and get_active_profile() is not None:
            raise ValueError
        access = read_active_web_access(target.state_dir)
        if access.port != port or origin not in access.effective_origins:
            raise ValueError
        token = target.token or read_web_token(target.state_dir)
        security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
        with safe_client("runtime.local_probe", configured_origin=origin, security=security,
                         timeout=min(20, remaining()), overall_timeout=remaining()) as client:
            headers = {"Authorization": f"Bearer {token}", "Origin": origin}
            def bounded_get(path, *, authenticated=True, params=None, limit=1_048_576):
                request_headers = headers if authenticated else {}
                with client.stream("GET", origin + path, headers=request_headers, params=params,
                                   timeout=min(20, remaining()), follow_redirects=False) as response:
                    # The managed transport asks for gzip/deflate; the limit
                    # below bounds the decoded body either way.
                    if response.headers.get("content-encoding", "identity").lower() not in {
                        "identity", "gzip", "deflate",
                    }:
                        raise ValueError
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        remaining()
                        body.extend(chunk)
                        if len(body) > limit:
                            raise ValueError
                    if token.encode() in body or any(token in value for value in response.headers.values()):
                        raise ValueError  # Never retain or expose a peer-echoed owner credential.
                    decoded = [(name, value) for name, value in response.headers.items()
                               if name.lower() not in {"content-encoding", "content-length"}]
                    return httpx.Response(response.status_code, headers=decoded, content=bytes(body), request=response.request)
            nonce = secrets.token_urlsafe(32)
            # Legacy/dirty old revisions use the same owner proof followed by
            # strict authenticated diagnostics. Candidate proof stays SHA-bound.
            challenge_revision = (revision if require_loaded and revision and re.fullmatch(r"[0-9a-f]{40}", revision)
                                  else "")
            proof = bounded_get("/api/auth/challenge", authenticated=False,
                                params={"nonce": nonce, "revision": challenge_revision}, limit=4096)
            proof.raise_for_status()
            expected = create_owner_challenge_proof(token=token, nonce=nonce, revision=challenge_revision)
            value = proof.json().get("proof")
            if not isinstance(value, str) or not hmac.compare_digest(value, expected) or read_active_web_access(target.state_dir) != access:
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
                loaded_revision = data.get("revision")
                # Workers from before hot swaps report only their startup revision.
                observed_revision = data.get("installed_revision", loaded_revision)
                if (
                    data.get("status") != "ok" or data.get("database_ok") is not True
                    or not isinstance(observed_revision, str)
                    or not re.fullmatch(r"[0-9a-f]{40}(?:-dirty)?", observed_revision)
                    or (revision is not None and observed_revision != revision)
                    or (require_loaded and loaded_revision != observed_revision)
                    or data.get("principal_id") != _instance_owner(target)
                    or type(pid) is not int or pid <= 0 or pid != _instance_worker_pid(target)
                    or type(data.get("registered_tool_count")) is not int or data["registered_tool_count"] <= 0
                    or isinstance(observed, bool) or not isinstance(observed, (float, int))
                    or not before <= observed <= time.time()
                ):
                    raise ValueError
                return pid, observed_revision

            stage = "identity"
            pid, observed_revision = identity()
            stage = "health"
            if get("/healthz").json().get("status") != "ok":
                raise ValueError
            if not through_health:
                stage = "web"
                web = get("/chat")
                if not web.headers.get("content-type", "").startswith("text/html") or "/_next/" not in web.text:
                    raise ValueError
                stage = "doctor"
                doctor = get("/api/doctor").json()
                rows = doctor.get("results")
                if not isinstance(rows, list) or not rows:
                    raise ValueError
                if target.doctor_baseline is not None:
                    current = {row.get("id"): row.get("ok") is True for row in rows if isinstance(row, dict)}
                    if any(was_ok and current.get(row_id) is not True
                           for row_id, was_ok in target.doctor_baseline.items()):
                        raise ValueError
                else:
                    if doctor.get("all_ok") is not True:
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
                with socket.create_connection(("127.0.0.1", port), timeout=min(5, remaining())) as sock:
                    with connect(f"ws://127.0.0.1:{port}/ws", sock=sock, origin=origin,
                                 additional_headers={"Authorization": headers["Authorization"]},
                                 open_timeout=min(5, remaining()), close_timeout=1, max_size=1_048_576) as ws:
                        ws.send("ping")
                        while json.loads(ws.recv(timeout=min(5, remaining()))).get("type") != "pong":
                            remaining()
            observation = None
            if observation_entry is not None:
                stage = "observation"
                response = bounded_get(observation_entry, limit=observation_bytes)
                if str(response.url) != origin + observation_entry:
                    raise ValueError
                observation = {"entry": observation_entry, "status": response.status_code,
                               "content_type": response.headers.get("content-type", "")[:256],
                               "body": response.content.decode("utf-8", errors="replace"), "observed_at": time.time()}
            stage = "identity"
            if identity() != (pid, observed_revision) or read_active_web_access(target.state_dir) != access:
                raise ValueError
            remaining()
        gate = {"schema": 1, "candidate_sha": observed_revision, "attempt": record.state.attempt,
                "worker_pid": pid, "verified_at": time.time(),
                "checks": {name: not through_health or name in {"runtime_revision", "owner_auth", "health"}
                           for name in SYSTEM_CHECKS}}
        return gate if observation_entry is None else {"system_gate": gate, "observation": observation}
    except Exception:
        raise SystemProbeError(f"system probe failed: {stage}") from None
