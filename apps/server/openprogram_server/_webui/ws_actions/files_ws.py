"""Canonical project-file WebSocket envelopes and action dispatch."""
from __future__ import annotations

import asyncio
import json

from .files_shared import (
    _durable_file_action, _normalise_file_result, _normalise_mutation_result,
    _request_id,
)
from .files_mutations import (
    _copy_entry, _create_entry, _delete_entry, _read_file, _rename_entry,
    _reveal_entry, _write_file,
)
from .files_query import _query_error, _search_query, _tree_query

async def handle_project_file_tree(ws, cmd: dict) -> None:
    raw_project_id = cmd.get("project_id")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    # No .strip(): filenames with leading/trailing whitespace must
    # round-trip so the echoed ``path`` matches the request.
    path = cmd["path"] if "path" in cmd else ""
    loop = asyncio.get_event_loop()
    if "root" in cmd:
        result = _query_error(
            project_id, "", code="INVALID_REQUEST",
            message="root is not accepted; use project_id and relative path",
        )
        await ws.send_text(json.dumps({
            "type": "project_file_tree_result", "data": {
                **result, "action": "project_file_tree", "request_id": _request_id(cmd),
            },
        }, default=str))
        return
    result = await loop.run_in_executor(
        None, lambda: _tree_query(
            project_id, path, cmd.get("page_size"), cmd.get("cursor"),
            cmd.get("snapshot_id"), cmd.get("sort"),
        ),
    )
    await ws.send_text(json.dumps({
        "type": "project_file_tree_result",
        "data": {**result, "action": "project_file_tree", "request_id": _request_id(cmd)},
    }, default=str))


async def handle_project_file_search(ws, cmd: dict) -> None:
    raw_project_id = cmd.get("project_id")
    project_id = raw_project_id.strip() if isinstance(raw_project_id, str) else ""
    path = cmd["path"] if "path" in cmd else ""
    loop = asyncio.get_event_loop()
    if "root" in cmd:
        result = _query_error(
            project_id, "", code="INVALID_REQUEST",
            message="root is not accepted; use project_id and relative path",
            kind="search",
        )
        await ws.send_text(json.dumps({
            "type": "project_file_search_result", "data": {
                **result, "action": "project_file_search", "request_id": _request_id(cmd),
            },
        }, default=str))
        return
    result = await loop.run_in_executor(
        None, lambda: _search_query(
            project_id, path, cmd.get("query"), cmd.get("mode"),
            cmd.get("type"), cmd.get("page_size"), cmd.get("cursor"),
            cmd.get("snapshot_id"), cmd.get("sort"),
        ),
    )
    await ws.send_text(json.dumps({
        "type": "project_file_search_result",
        "data": {**result, "action": "project_file_search", "request_id": _request_id(cmd)},
    }, default=str))


async def handle_project_file_read(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = ({"error_code": "INVALID_REQUEST", "error": "project_id and path are required"}
              if not project_id or not isinstance(cmd.get("path"), str)
              else await loop.run_in_executor(None, lambda: _read_file(project_id, path)))
    payload = {"project_id": project_id, "path": path,
               "action": "project_file_read", "request_id": _request_id(cmd)}
    payload.update(_normalise_file_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_read_result",
        "data": payload,
    }, default=str))


async def handle_project_file_operation_status(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    operation_action = cmd.get("operation_action")
    key = cmd.get("idempotency_key")
    operation_id = cmd.get("operation_id")
    result: dict
    if (not project_id or not isinstance(operation_action, str)
            or not isinstance(key, str) or not key):
        result = {"status": "error", "error_code": "INVALID_REQUEST",
                  "error": "project_id, operation_action, and idempotency_key are required"}
    elif operation_action not in {
        "project_file_write", "project_file_create", "project_file_rename",
        "project_file_copy", "project_file_delete",
    }:
        result = {"status": "error", "error_code": "INVALID_REQUEST",
                  "error": "operation_action is not a project file mutation"}
    else:
        from openprogram.store.file_operations import default_file_operation_store
        store = default_file_operation_store()
        row = store.find(project_id, operation_action, key)
        if row is None:
            result = {"status": "recovery_required", "error_code": "RECOVERY_REQUIRED",
                      "error": "durable file operation receipt is unavailable"}
        else:
            result = store.replay(row)
            # This marker distinguishes a journaled terminal operation from
            # an error produced while handling the status query itself.
            result["durable_receipt"] = True
            result.setdefault("status", "in_progress")
            if row.get("status") == "in_flight":
                result["status"] = "in_progress"
            if isinstance(operation_id, str) and operation_id != row.get("operation_id"):
                result["error_code"] = "OPERATION_ID_MISMATCH"
                result["status"] = "recovery_required"
    payload = {
        "project_id": project_id,
        "operation_action": operation_action,
        "idempotency_key": key,
        "action": "project_file_operation_status",
        "request_id": _request_id(cmd),
        **result,
    }
    await ws.send_text(json.dumps({
        "type": "project_file_operation_status_result", "data": payload,
    }, default=str))


async def handle_project_file_write(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    content = cmd.get("content")
    expected_mtime = cmd.get("expected_mtime")
    if not isinstance(expected_mtime, (int, float)):
        expected_mtime = None
    expected_revision = cmd.get("baseline_revision")
    if not isinstance(expected_revision, str) or not expected_revision:
        expected_revision = None
    if not isinstance(content, str):
        result: dict = {"error": "content must be a string"}
    else:
        loop = asyncio.get_event_loop()
        mutation_payload = {"path": path, "content": content,
                            "expected_mtime": expected_mtime}
        if expected_revision is not None:
            mutation_payload["baseline_revision"] = expected_revision
        result = await loop.run_in_executor(
            None, lambda: _durable_file_action(
                project_id, "project_file_write", cmd.get("idempotency_key"),
                mutation_payload,
                lambda: _write_file(
                    project_id, path, content, expected_mtime, expected_revision,
                ),
            ),
        )
    payload = {"project_id": project_id, "path": path,
               "action": "project_file_write", "request_id": _request_id(cmd)}
    payload.update(_normalise_mutation_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_write_result",
        "data": payload,
    }, default=str))


async def handle_project_file_create(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    kind = cmd.get("kind") or "file"
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _durable_file_action(
            project_id, "project_file_create", cmd.get("idempotency_key"),
            {"path": path, "kind": kind},
            lambda: _create_entry(project_id, path, kind),
        ),
    )
    payload = {"project_id": project_id, "path": path, "kind": kind,
               "action": "project_file_create", "request_id": _request_id(cmd)}
    payload.update(_normalise_mutation_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_create_result",
        "data": payload,
    }, default=str))


async def handle_project_file_rename(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    new_path = cmd.get("new_path") or ""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _durable_file_action(
            project_id, "project_file_rename", cmd.get("idempotency_key"),
            {"path": path, "new_path": new_path},
            lambda: _rename_entry(project_id, path, new_path),
        ),
    )
    payload = {"project_id": project_id, "path": path, "new_path": new_path,
               "action": "project_file_rename", "request_id": _request_id(cmd)}
    payload.update(_normalise_mutation_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_rename_result",
        "data": payload,
    }, default=str))


async def handle_project_file_copy(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    new_path = cmd.get("new_path") or ""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _durable_file_action(
            project_id, "project_file_copy", cmd.get("idempotency_key"),
            {"path": path, "new_path": new_path},
            lambda: _copy_entry(project_id, path, new_path),
        ),
    )
    payload = {"project_id": project_id, "path": path, "new_path": new_path,
               "action": "project_file_copy", "request_id": _request_id(cmd)}
    payload.update(_normalise_mutation_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_copy_result",
        "data": payload,
    }, default=str))


async def handle_project_file_delete(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: _durable_file_action(
            project_id, "project_file_delete", cmd.get("idempotency_key"),
            {"path": path}, lambda: _delete_entry(project_id, path),
        ),
    )
    payload = {"project_id": project_id, "path": path,
               "action": "project_file_delete", "request_id": _request_id(cmd)}
    payload.update(_normalise_mutation_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_delete_result",
        "data": payload,
    }, default=str))


async def handle_project_file_reveal(ws, cmd: dict) -> None:
    project_id = (cmd.get("project_id") or "").strip()
    path = cmd.get("path") or ""  # no .strip() — see handle_project_file_tree
    loop = asyncio.get_event_loop()
    result = ({"error_code": "INVALID_REQUEST", "error": "project_id and path are required"}
              if not project_id or not isinstance(cmd.get("path"), str)
              else await loop.run_in_executor(None, lambda: _reveal_entry(project_id, path)))
    payload = {"project_id": project_id, "path": path,
               "action": "project_file_reveal", "request_id": _request_id(cmd)}
    payload.update(_normalise_file_result(result))
    await ws.send_text(json.dumps({
        "type": "project_file_reveal_result",
        "data": payload,
    }, default=str))


ACTIONS = {
    "project_file_tree": handle_project_file_tree,
    "project_file_search": handle_project_file_search,
    "project_file_read": handle_project_file_read,
    "project_file_operation_status": handle_project_file_operation_status,
    "project_file_write": handle_project_file_write,
    "project_file_create": handle_project_file_create,
    "project_file_rename": handle_project_file_rename,
    "project_file_copy": handle_project_file_copy,
    "project_file_delete": handle_project_file_delete,
    "project_file_reveal": handle_project_file_reveal,
}
