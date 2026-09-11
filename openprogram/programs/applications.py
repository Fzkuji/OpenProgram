"""Use installed application operations from Programs and local CLI clients.

Requests go to the authenticated worker, which owns execution after this
client exits. Only operations explicitly exposed to Programs are callable here.
"""
from __future__ import annotations

from urllib.parse import quote
import uuid


def request(path: str, *, method: str = "GET", body=None):
    from openprogram.backend_endpoint import resolve_backend_endpoint
    import httpx
    endpoint = resolve_backend_endpoint()
    with httpx.Client(timeout=360, follow_redirects=False, trust_env=False) as client:
        response = client.request(method, endpoint.base_url + path, json=body,
                                  headers={"Authorization": endpoint.authorization_header, "Origin": endpoint.origin})
    value = response.json()
    if response.is_error:
        raise ValueError(value.get("error", value.get("detail", "Application request failed")))
    return value


def list_applications() -> list[dict]:
    """List installed applications and their operation schemas."""
    return request("/api/applications")["applications"]


def run(app_id: str, operation: str, input: dict, *, project_id: str = "", request_key: str | None = None) -> dict:
    """Start a declared Agent-visible operation; return its durable run ID."""
    opened = request(f"/api/applications/{quote(app_id, safe='')}/open", method="POST", body={"project_id": project_id})
    return request(f"/api/application-instances/{opened['instance_id']}/operations/{quote(operation, safe='')}", method="POST", body={
        "digest": opened["application"]["digest"], "input": input,
        "request_key": request_key or uuid.uuid4().hex, "agent": True,
    })


def status(run_id: str, *, after: int = 0) -> dict:
    return request(f"/api/application-runs/{quote(run_id, safe='')}?after={int(after)}")


def cancel(run_id: str) -> dict:
    return request(f"/api/application-runs/{quote(run_id, safe='')}/cancel", method="POST", body={})
