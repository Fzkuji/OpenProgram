"""Control one execution through the default worker."""
from __future__ import annotations

import json
import sys
import uuid
from typing import Any, Optional

from openprogram.interfaces.cli import worker_client


_EXECUTION_CONTROL_PATHS = {
    "pause": "/api/execution/pause",
    "continue": "/api/execution/continue",
    "step": "/api/execution/step",
    "steer": "/api/execution/steer",
    "cancel": "/api/execution/cancel",
    "fork": "/api/execution/fork",
    "retry": "/api/execution/retry",
    "wait_answer": "/api/execution/wait/answer",
    "wait_decline": "/api/execution/wait/decline",
}


_require_backend_endpoint = worker_client.require_backend_endpoint


def _worker_request(method: str, path: str,
                    body: Optional[dict] = None) -> tuple[int, Any]:
    return worker_client.request(_require_backend_endpoint(), method, path, body)


def _cmd_execution_control(
    operation: str,
    execution_id: str,
    *,
    expected_version: int,
    command_id: str | None,
    payload: Optional[dict] = None,
) -> int:
    action = "execution.wait.answer" if operation == "wait_answer" else (
        "execution.wait.decline" if operation == "wait_decline" else f"execution.{operation}"
    )
    status, payload = _worker_request(
        "POST",
        _EXECUTION_CONTROL_PATHS[operation],
        {
            "type": "execution.command",
            "action": action,
            "command_id": command_id or f"cli-{operation}-{uuid.uuid4().hex}",
            "execution_id": execution_id,
            "expected_version": expected_version,
            "payload": payload or {},
        },
    )
    record = payload.get("execution") if isinstance(payload, dict) else None
    command = payload.get("command") if isinstance(payload, dict) else None
    if status == 404 or (isinstance(command, dict) and command.get("rejection_code") == "not_found"):
        print(f"execution {execution_id}: not found")
        return 1
    if isinstance(command, dict) and command.get("status") == "rejected":
        latest = command.get("latest_snapshot")
        version = latest.get("status_version") if isinstance(latest, dict) else None
        print(f"execution {execution_id}: rejected ({command.get('rejection_code')}); latest_version={version}")
        return 1
    if not isinstance(record, dict):
        detail = payload.get("error") if isinstance(payload, dict) else payload
        print(f"execution {execution_id}: {detail or f'worker returned {status}'}")
        return 1
    print(f"status={record.get('status')} reason_code={record.get('reason_code')}")
    return 0 if status < 400 else 1


def _cmd_execution_wait(
    operation: str, execution_id: str, *, expected_version: int,
    command_id: str | None, wait_id: str, generation: int,
    value: str | None = None,
) -> int:
    if operation == "wait_answer":
        try:
            payload = {"wait_id": wait_id, "generation": generation, "answer": json.loads(value or "null")}
        except json.JSONDecodeError:
            print("answer must be valid JSON", file=sys.stderr)
            return 2
    else:
        payload = {"wait_id": wait_id, "generation": generation}
        if value is not None:
            payload["reason"] = value
    return _cmd_execution_control(
        operation, execution_id, expected_version=expected_version,
        command_id=command_id, payload=payload,
    )
