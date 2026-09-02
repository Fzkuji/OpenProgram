"""Canonical review/history WS handlers and action dispatch."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from .turn_files_scope import *
from .turn_files_diff import *
async def _run(fn) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


async def handle_turn_operation_status(ws, cmd: dict) -> None:
    """Read a turn rewind receipt without replaying the mutation payload."""
    session_id = (cmd.get("session_id") or "").strip()
    operation_action = cmd.get("operation_action")
    msg_id = (cmd.get("msg_id") or "").strip()
    key = cmd.get("idempotency_key")
    supplied_operation_id = cmd.get("operation_id")
    payload = {
        "session_id": session_id,
        "operation_action": operation_action,
        "msg_id": msg_id,
        "idempotency_key": key,
        "request_id": cmd.get("request_id"),
        "action": "turn_operation_status",
    }
    if (not session_id or not _valid_turn_id(msg_id)
            or operation_action not in {"revert_turn", "reapply_turn"}
            or not isinstance(key, str) or not key):
        payload.update({"status": "error", "error_code": "INVALID_REQUEST"})
    else:
        try:
            from openprogram.store.session.session_store import default_store
            from openprogram.store.snapshot.checkpoint import CheckpointStore
            journal = CheckpointStore(default_store()._session_dir(session_id))
            direction = "revert" if operation_action == "revert_turn" else "reapply"
            intent = journal.read_history_intent(msg_id, direction, key)
            if intent is None:
                intent = journal.read_rewind_intent(f"turn-closure:{direction}:{key}")
            if intent is None or intent.get("idempotency_key") not in {
                key, f"turn-closure:{direction}:{key}"
            }:
                payload.update({
                    "status": "error",
                    "error_code": "RECEIPT_UNAVAILABLE",
                })
            else:
                expected_target = str(intent.get("target_msg_id") or "")
                if (intent.get("turn_id") not in {None, msg_id}
                    or intent.get("direction") not in {None, direction}
                    or (
                    expected_target and expected_target != f"{direction}:{msg_id}"
                    )):
                    payload.update({"status": "recovery_required", "error_code": "RECOVERY_REQUIRED"})
                else:
                    receipt_id = intent.get("transaction_id")
                    if not isinstance(receipt_id, str) or not receipt_id:
                        payload.update({"status": "error", "error_code": "RECEIPT_UNAVAILABLE"})
                    elif isinstance(supplied_operation_id, str) and supplied_operation_id != receipt_id:
                        payload.update({
                            "status": "recovery_required",
                            "error_code": "OPERATION_ID_MISMATCH",
                            "operation_id": supplied_operation_id,
                        })
                    else:
                        intent_status = intent.get("status")
                        receipt_status = (
                            "in_progress" if intent_status in {"prepared", "applying"}
                            else "ready" if intent_status == "committed"
                            else "recovery_required" if intent_status == "recovery_required"
                            else "error"
                        )
                        payload.update({
                            "status": receipt_status,
                            "operation_id": receipt_id,
                            "durable_receipt": True,
                            "error_code": intent.get("error_code"),
                            "error": intent.get("error"),
                        })
        except Exception:
            payload.update({"status": "error", "error_code": "RECEIPT_UNAVAILABLE"})
    await ws.send_text(json.dumps({
        "type": "turn_operation_status_result", "data": payload,
    }, default=str))


def _stable_file_result(result: dict) -> dict:
    """Normalize turn-file replies without exposing exception text as a code."""
    result = dict(result)
    error = result.get("error")
    if error:
        text = str(error).lower()
        if "stale_snapshot" in text or "stale snapshot" in text:
            result["status"] = "stale"
            result["error_code"] = "STALE_SNAPSHOT"
        elif "stale_cursor" in text or "stale cursor" in text:
            result["status"] = "stale"
            result["error_code"] = "STALE_CURSOR"
        else:
            result.setdefault("status", "error")
            result.setdefault("error_code", "INVALID_REQUEST" if "required" in text
                          or "invalid" in text or "unsafe" in text
                          or "unknown review" in text
                          else "NOT_FOUND" if "unknown" in text
                          or "not found" in text or "not recorded" in text
                          else "CONFLICT" if "stale" in text or "run_active" in text
                          else "IO_ERROR")
    elif result.get("status") not in {"blocked", "stale"}:
        result.setdefault("status", "ready")
    if result.get("status") == "stale":
        result.setdefault("error_code", "STALE_SNAPSHOT")
    return result


def _review_request_values(cmd: dict, *, include_path: bool = False) -> tuple[dict, str | None]:
    errors: list[str] = []

    def text(name: str, default: str = "", *, strip: bool = True) -> str:
        value = cmd.get(name, default)
        if value is None:
            value = default
        if not isinstance(value, str):
            errors.append(f"{name} must be a string")
            return default
        if len(value.encode("utf-8")) > _MAX_REVIEW_TEXT_BYTES:
            errors.append(f"{name} exceeds review request limit")
            return default
        return value.strip() if strip else value

    def limit() -> int:
        value = cmd.get("limit", _SCOPE_PAGE_SIZE)
        if value is None:
            return _SCOPE_PAGE_SIZE
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append("limit must be an integer")
            return _SCOPE_PAGE_SIZE
        if not 1 <= value <= _SCOPE_PAGE_SIZE:
            errors.append(f"limit must be between 1 and {_SCOPE_PAGE_SIZE}")
            return _SCOPE_PAGE_SIZE
        return value

    values = {
        "session_id": text("session_id"),
        "scope": text("scope", "turn"),
        "assistant_msg_id": text("assistant_msg_id"),
        "cursor": text("cursor"),
        "category": text("category", "All"),
        "query": text("query", "", strip=False),
        "sort": text("sort", "path"),
        "snapshot_id": text("snapshot_id"),
        "request_id": cmd.get("request_id"),
    }
    if "request_id" in cmd and values["request_id"] is not None:
        if not isinstance(values["request_id"], str):
            errors.append("request_id must be a string")
            values["request_id"] = None
        elif len(values["request_id"].encode("utf-8")) > _MAX_REVIEW_TEXT_BYTES:
            errors.append("request_id exceeds review request limit")
            values["request_id"] = None
    if cmd.get("cursor") is not None and values["cursor"] and not values["cursor"].startswith("rc_"):
        errors.append("cursor must be an opaque review token")
    values["limit"] = limit()
    if include_path:
        values["path"] = text("path")
    return values, "; ".join(errors) if errors else None


async def handle_review_scope(ws, cmd: dict) -> None:
    values, validation_error = _review_request_values(cmd)
    session_id = values["session_id"]
    scope = values["scope"]
    turn_id = values["assistant_msg_id"]
    cursor = values["cursor"]
    limit = values["limit"]
    category = values["category"]
    query = values["query"]
    sort = values["sort"]
    snapshot_id = values["snapshot_id"]
    if validation_error:
        result = {"status": "error", "error": validation_error}
    elif category not in _REVIEW_CATEGORIES:
        result = {"status": "error", "error": f"unknown review category {category!r}"}
    elif sort not in _REVIEW_SORTS:
        result = {"status": "error", "error": f"unknown review sort {sort!r}"}
    elif not session_id:
        result = {"status": "error", "error": "session_id is required"}
    else:
        try:
            if scope == "turn":
                result = await _run(lambda: _turn_scope(
                    session_id, turn_id, category=category, query=query, sort=sort,
                ))
            elif scope == "branch":
                result = await _run(lambda: _branch_scope(
                    session_id, category=category, query=query, sort=sort,
                ))
            elif scope == "workspace":
                result = await _run(lambda: _workspace_scope(
                    session_id, category=category, query=query, sort=sort,
                ))
            else:
                result = {"status": "error", "error": f"unknown review scope {scope!r}"}
        except Exception as exc:
            result = {"status": "error", "error": str(exc)}
    result = _page_scope(result, cursor, limit, snapshot_id)
    result = _stable_file_result(result)
    # Every response, including validation and stale/error responses, carries
    # the request context so clients can safely correlate terminal states.
    result = {
        **result,
        "scope": scope,
        "category": category,
        "query": query,
        "sort": sort,
        "snapshot_id": result.get("snapshot_id") or snapshot_id or None,
    }
    await ws.send_text(json.dumps({
        "type": "review_scope_result",
        "data": {
            "session_id": session_id,
            "assistant_msg_id": turn_id,
            "request_id": values["request_id"],
            **result,
            "action": "review_scope",
        },
    }, default=str))


async def handle_review_file_diff(ws, cmd: dict) -> None:
    values, validation_error = _review_request_values(cmd, include_path=True)
    session_id = values["session_id"]
    scope = values["scope"]
    turn_id = values["assistant_msg_id"]
    path = values["path"]
    snapshot_id = values["snapshot_id"]
    category = values["category"]
    query = values["query"]
    sort = values["sort"]
    cursor = values["cursor"]
    if validation_error:
        result = {"diff": "", "diff_state": "unavailable", "error": validation_error}
    elif category not in _REVIEW_CATEGORIES or sort not in _REVIEW_SORTS:
        result = {"diff": "", "diff_state": "unavailable", "error": "invalid review filter"}
    elif scope not in _REVIEW_SCOPES:
        result = {"diff": "", "diff_state": "unavailable", "error": f"unknown review scope {scope!r}"}
    elif not session_id or not path:
        result = {"diff": "", "diff_state": "unavailable", "error": "session_id and path are required"}
    else:
        try:
            current_scope = (
                await _run(lambda: _turn_scope(
                    session_id, turn_id, category=category, query=query, sort=sort,
                ))
                if scope == "turn"
                else await _run(lambda: _branch_scope(
                    session_id, category=category, query=query, sort=sort,
                ))
                if scope == "branch"
                else await _run(lambda: _workspace_scope(
                    session_id, category=category, query=query, sort=sort,
                ))
                if scope == "workspace"
                else {"status": "error", "error": f"unknown review scope {scope!r}"}
            )
            saved_snapshot = _get_review_snapshot(snapshot_id)
            member = next(
                (row for row in current_scope.get("files", []) if row.get("path") == path),
                None,
            )
            if (
                current_scope.get("status") != "ready"
                or not snapshot_id
                or current_scope.get("snapshot_id") != snapshot_id
                or saved_snapshot is None
                or saved_snapshot.get("scope") != scope
                or saved_snapshot.get("owner", {}).get("session_id") != session_id
            ):
                result = {"diff": "", "diff_state": "unavailable", "error": "STALE_SNAPSHOT"}
            elif member is None or saved_snapshot is None:
                result = {"diff": "", "diff_state": "unavailable", "error": "path is not in review scope"}
            else:
                valid_cursor, diff_offset = _resolve_diff_cursor(
                    cursor, snapshot_id, saved_snapshot.get("epoch"), path, member,
                    _MAX_DIFF_LINES,
                )
                if not valid_cursor:
                    result = {
                        "diff": "", "diff_state": "unavailable",
                        "error": "STALE_CURSOR",
                    }
                elif scope == "turn":
                    result = await _run(lambda: _review_turn_file_diff(
                        session_id, member, path, diff_offset,
                    ))
                elif scope == "branch":
                    result = await _run(lambda: _branch_file_diff(
                        session_id, path, diff_offset,
                    ))
                else:
                    result = await _run(lambda: _workspace_file_diff(
                        session_id, path, snapshot_id, diff_offset,
                    ))
                result = _bind_diff_page(
                    result, cursor, snapshot_id, path, member, _MAX_DIFF_LINES,
                    saved_snapshot.get("epoch"),
                )
        except Exception as exc:
            result = {"diff": "", "diff_state": "unavailable", "error": str(exc)}
    result = _stable_file_result(result)
    await ws.send_text(json.dumps({
        "type": "review_file_diff_result",
        "data": {
            "session_id": session_id, "assistant_msg_id": turn_id,
            "request_id": values["request_id"],
            "scope": scope, "path": path, "snapshot_id": snapshot_id,
            "category": category, "query": query, "sort": sort,
            **result, "action": "review_file_diff",
        },
    }, default=str))


async def handle_turn_history_state(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    from openprogram.webui import server as server
    if not session_id or not turn_id:
        result = {"status": "error", "action": None, "error": "session_id and assistant_msg_id are required"}
    elif server._is_run_active(session_id):
        result = {"status": "blocked", "action": None, "error": "run_active"}
    else:
        result = await _run(lambda: _history_eligibility(session_id, turn_id))
    result = _stable_file_result(result)
    await ws.send_text(json.dumps({
        "type": "turn_history_state_result",
        "data": {
            "session_id": session_id,
            "assistant_msg_id": turn_id,
            "request_id": cmd.get("request_id"),
            **result, "action": "turn_history_state",
        },
    }, default=str))


async def handle_revert_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()
    from openprogram.webui import server as server
    if session_id and server._is_run_active(session_id):
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [], "error": "run_active"}
    else:
        from openprogram.agent.internals._revert import revert_turn
        result = await _run(lambda: revert_turn(
            session_id, msg_id,
            idempotency_key=(cmd.get("idempotency_key") or None),
        ))
    result = _stable_file_result(result)
    await ws.send_text(json.dumps({
        "type": "revert_turn_result",
        "data": {
            "session_id": session_id, "msg_id": msg_id,
            "request_id": cmd.get("request_id"),
            "action": "revert_turn",
            "reverted_paths": result.get("restored_paths") or [],
            "status": result.get("status"),
            "operation_id": result.get("transaction_id"),
            "transaction_id": result.get("transaction_id"),
            "conflicts": result.get("conflicts") or [],
            "unavailable": result.get("unavailable") or [],
            "errors": (["IDEMPOTENCY_KEY_CONFLICT"]
                       if result.get("status") == "idempotency_conflict"
                       else [result["error"]] if result.get("error") else []),
        },
    }, default=str))


async def handle_reapply_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()
    from openprogram.webui import server as server
    if session_id and server._is_run_active(session_id):
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [], "error": "run_active"}
    else:
        from openprogram.agent.internals._revert import reapply_turn
        result = await _run(lambda: reapply_turn(
            session_id, msg_id,
            idempotency_key=(cmd.get("idempotency_key") or None),
        ))
    result = _stable_file_result(result)
    await ws.send_text(json.dumps({
        "type": "reapply_turn_result",
        "data": {
            "session_id": session_id, "msg_id": msg_id,
            "request_id": cmd.get("request_id"),
            "action": "reapply_turn",
            "reapplied_paths": result.get("restored_paths") or [],
            "status": result.get("status"),
            "operation_id": result.get("transaction_id"),
            "transaction_id": result.get("transaction_id"),
            "conflicts": result.get("conflicts") or [],
            "unavailable": result.get("unavailable") or [],
            "errors": (["IDEMPOTENCY_KEY_CONFLICT"]
                       if result.get("status") == "idempotency_conflict"
                       else [result["error"]] if result.get("error") else []),
        },
    }, default=str))


ACTIONS = {
    "review_scope": handle_review_scope,
    "review_file_diff": handle_review_file_diff,
    "turn_history_state": handle_turn_history_state,
    "turn_operation_status": handle_turn_operation_status,
    "revert_turn": handle_revert_turn,
    "reapply_turn": handle_reapply_turn,
}

