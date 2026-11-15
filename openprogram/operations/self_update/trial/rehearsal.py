"""Stop the candidate and bring the old version up on what the candidate left.

Booting proves little: the old version has to read a migrated execution and a
conversation, because after a rollback that is what it will do first.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

from ..types import UpdateRecord
from ..verification.system_probe import probe_instance
from .instance import TrialError, TrialInstance

_READ_SECONDS = 30


def instance_endpoint(state_dir: Path):
    """How to reach a trial's worker, which the system probe has already challenged."""
    from openprogram.host.security.backend_endpoint import (
        BackendEndpoint, read_active_web_access, read_web_token, select_request_origin,
    )

    return BackendEndpoint(origin=select_request_origin(read_active_web_access(state_dir)),
                           token=read_web_token(state_dir))


def _text_of(message: Mapping[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, default=str)


def load_conversation(endpoint, session_id: str, *, seconds: float = _READ_SECONDS) -> list[dict]:
    """The transcript the running version serves, over its own WebSocket."""
    from websockets.sync.client import connect

    request_id = uuid.uuid4().hex
    deadline = time.monotonic() + seconds
    with connect(endpoint.websocket_url, additional_headers={"Authorization": endpoint.authorization_header,
                                                             "Origin": endpoint.origin},
                 open_timeout=min(10, seconds), max_size=None) as socket:
        socket.send(json.dumps({"action": "load_session", "session_id": session_id, "request_id": request_id}))
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TrialError("the conversation did not load in time")
            frame = json.loads(socket.recv(timeout=left))
            data = frame.get("data") if isinstance(frame, dict) else None
            if not isinstance(data, dict) or data.get("request_id") != request_id:
                continue
            if frame.get("type") == "session_loaded":
                return [item for item in data.get("messages") or [] if isinstance(item, dict)]
            if frame.get("type") in {"operation_error", "action_error", "error"}:
                raise TrialError(f"the conversation could not be loaded: {frame.get('type')}")


def _loopback_client(origin: str, *, seconds: float):
    from openprogram.host.security.safe_http import OutboundSecurityConfig, safe_client
    from openprogram.host.security.url_policy import OwnerURLException

    security = OutboundSecurityConfig(owner_exceptions=(OwnerURLException(consumer="runtime.local_probe", origin=origin),))
    return safe_client("runtime.local_probe", configured_origin=origin, security=security,
                       timeout=seconds, overall_timeout=seconds)


def read_execution(endpoint, execution_id: str) -> dict:
    with _loopback_client(endpoint.origin, seconds=_READ_SECONDS) as client:
        response = client.get(f"{endpoint.origin}/api/execution/{execution_id}",
                              headers={"Authorization": endpoint.authorization_header, "Origin": endpoint.origin},
                              follow_redirects=False)
    if response.status_code != 200:
        raise TrialError(f"the execution could not be read: HTTP {response.status_code}")
    return response.json()


def run_turn(endpoint, session_id: str, text: str, *, seconds: float) -> str:
    """Send one chat turn and wait for its execution to end; returns the execution id."""
    from websockets.sync.client import connect

    request_id = uuid.uuid4().hex
    deadline = time.monotonic() + seconds
    with connect(endpoint.websocket_url, additional_headers={"Authorization": endpoint.authorization_header,
                                                             "Origin": endpoint.origin},
                 open_timeout=min(10, seconds), max_size=None) as socket:
        socket.send(json.dumps({"action": "chat", "text": text, "session_id": session_id, "client_request_id": request_id}))
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TrialError("the agent turn was not accepted in time")
            frame = json.loads(socket.recv(timeout=left))
            data = frame.get("data") if isinstance(frame, dict) and isinstance(frame.get("data"), dict) else {}
            if frame.get("type") == "chat_ack" and data.get("client_request_id") == request_id:
                execution_id = data.get("execution_id")
                break
            if frame.get("type") in {"action_error", "operation_error"}:
                raise TrialError(f"the worker refused the turn: {data.get('message') or frame.get('message')}")
    if not isinstance(execution_id, str) or not execution_id:
        raise TrialError("the worker did not name the turn's execution")
    while time.monotonic() < deadline:
        snapshot = read_execution(endpoint, execution_id).get("snapshot") or {}
        status = snapshot.get("status")
        if status == "completed":
            return execution_id
        if status in {"failed", "cancelled", "interrupted"}:
            raise TrialError(f"the agent turn ended as {status}: {snapshot.get('reason_code')}")
        time.sleep(0.25)
    raise TrialError("the agent turn did not finish in time")


def rehearse(old: TrialInstance, record: UpdateRecord, *, baseline: Mapping[str, bool],
             session_id: str, execution_id: str | None, reply: str) -> dict:
    """``old`` must already be serving the migrated data; ``execution_id`` is the candidate's turn."""
    probe_instance(record, old.target(doctor_baseline=baseline), timeout=60, require_loaded=False)
    endpoint = instance_endpoint(old.workspace.state_dir)
    if execution_id is None:
        raise TrialError("the candidate's turn left no execution for the old version to read")
    snapshot = read_execution(endpoint, execution_id)
    messages = load_conversation(endpoint, session_id)
    if not any(reply in _text_of(message) for message in messages):
        raise TrialError("the old version could not read the conversation the candidate wrote")
    status = (snapshot.get("snapshot") or {}).get("status")
    return {"execution_id": execution_id, "execution_status": status, "messages": len(messages)}
