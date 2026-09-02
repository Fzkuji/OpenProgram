"""Cancel one execution through the default worker."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional


def _require_backend_endpoint():
    from openprogram.backend_endpoint import (
        OwnerAuthError,
        resolve_backend_endpoint,
    )

    try:
        return resolve_backend_endpoint()
    except OwnerAuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Start it with: openprogram worker start", file=sys.stderr)
        sys.exit(1)


def _worker_request(method: str, path: str,
                    body: Optional[dict] = None) -> tuple[int, Any]:
    endpoint = _require_backend_endpoint()
    url = endpoint.base_url + path
    data: Optional[bytes] = None
    headers = {
        "Accept": "application/json",
        "Authorization": endpoint.authorization_header,
        "Host": endpoint.host,
        "X-Forwarded-Proto": endpoint.scheme,
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method,
                                  headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=120) as resp:
            text = resp.read().decode("utf-8")
            payload = json.loads(text) if text else None
            return resp.status, payload
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except Exception:
            payload = {"detail": text}
        return e.code, payload
    except urllib.error.URLError as e:
        print(f"Error contacting worker at {url}: {e}", file=sys.stderr)
        sys.exit(1)


def _cmd_execution_cancel(
    execution_id: str,
    *,
    expected_version: int,
    command_id: str | None,
) -> int:
    status, payload = _worker_request(
        "POST",
        "/api/execution/cancel",
        {
            "command_id": command_id or f"cli-cancel-{uuid.uuid4().hex}",
            "execution_id": execution_id,
            "expected_version": expected_version,
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
    print(
        f"status={record.get('status')} "
        f"reason_code={record.get('reason_code')}"
    )
    return 0 if status < 400 else 1
