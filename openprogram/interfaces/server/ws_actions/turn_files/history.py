"""Canonical review/history WS handlers and action dispatch."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from openprogram.state.store import operation_receipts

from .shared import _MAX_DIFF_LINES
from .shared import _MAX_REVIEW_TEXT_BYTES
from .shared import _REVIEW_CATEGORIES
from .shared import _REVIEW_SCOPES
from .shared import _REVIEW_SORTS
from .shared import _SCOPE_PAGE_SIZE
from .shared import _WORKSPACE_SCOPES
from .shared import _valid_turn_id
from .scope import _branch_scope
from .scope import _get_review_snapshot
from .scope import _history_eligibility
from .scope import _page_scope
from .scope import _turn_scope
from .diff import _bind_diff_page
from .diff import _branch_file_diff
from .diff import _resolve_diff_cursor
from .diff import _review_turn_file_diff
from .diff import _render_workspace_content_diff
from .diff import _workspace_file_diff
from .diff import _workspace_scope
async def _run(fn) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)


def _current_scope(session_id: str, scope: str, turn_id: str, *,
                   category: str, query: str, sort: str) -> dict:
    """The review scope as it is now, by its name."""
    if scope == "turn":
        return _turn_scope(session_id, turn_id, category=category, query=query, sort=sort)
    if scope == "branch":
        return _branch_scope(session_id, category=category, query=query, sort=sort)
    if scope in _WORKSPACE_SCOPES:
        return _workspace_scope(session_id, segment=scope, category=category, query=query, sort=sort)
    return {"status": "error", "error": f"unknown review scope {scope!r}"}


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
            from openprogram.state.store.session.session_store import default_store
            from openprogram.state.store.snapshot.checkpoint import CheckpointStore
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
            result = await _run(lambda: _current_scope(
                session_id, scope, turn_id, category=category, query=query, sort=sort,
            ))
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
            current_scope = await _run(lambda: _current_scope(
                session_id, scope, turn_id, category=category, query=query, sort=sort,
            ))
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


def _turn_branch_blocker(session_id: str, turn_id: str) -> dict | None:
    """The running branch whose line holds this turn, if any. A turn on a
    common ancestor belongs to every branch below it; a turn on any other
    branch does not block its file history."""
    from openprogram.interfaces.server import server
    return server.running_branch_among(session_id, server.branches_through(session_id, [turn_id]))


def _hold_turn_branches(session_id: str, turn_id: str, direction: str = "revert",
                        idempotency_key: str | None = None) -> tuple[dict | None, list[str]]:
    """Hold the branches whose line holds this turn while its files change.

    A repeat with the idempotency key of the revert or reapply holding them
    waits for it; the store then returns that one's outcome."""
    import uuid as _uuid
    from openprogram.interfaces.server import server
    key = (f"turn-closure:{direction}:{turn_id}:{idempotency_key}"
           if isinstance(idempotency_key, str) and idempotency_key else None)
    return server.reserve_branches_operation(
        session_id, server.branches_through(session_id, [turn_id]), "branch",
        f"turn-files-{_uuid.uuid4().hex}", key=key)


async def handle_turn_history_state(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    turn_id = (cmd.get("assistant_msg_id") or "").strip()
    from openprogram.interfaces.server import server as server
    if not session_id or not turn_id:
        result = {"status": "error", "action": None, "error": "session_id and assistant_msg_id are required"}
    elif (blocker := await _run(lambda: _turn_branch_blocker(session_id, turn_id))) is not None:
        result = {"status": "blocked", "action": None, "reason": "run_active", "error": "run_active",
                  "blocking": blocker}
    else:
        result = await _run(lambda: _history_eligibility(session_id, turn_id))
    result = _stable_file_result(result)
    # Eligibility names the available operation under "action", which the
    # frame envelope reuses for the ws action name; ship it as "operation".
    await ws.send_text(json.dumps({
        "type": "turn_history_state_result",
        "data": {
            "session_id": session_id,
            "assistant_msg_id": turn_id,
            "request_id": cmd.get("request_id"),
            **result,
            "operation": result.get("action"),
            "action": "turn_history_state",
        },
    }, default=str))


async def handle_revert_turn(ws, cmd: dict) -> None:
    session_id = (cmd.get("session_id") or "").strip()
    msg_id = (cmd.get("msg_id") or "").strip()
    from openprogram.interfaces.server import server as server
    refusal, owners = (await _run(lambda: _hold_turn_branches(
        session_id, msg_id, "revert", cmd.get("idempotency_key") or None))
                       if session_id else (None, []))
    if refusal is not None:
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [],
                  "error": "run_active", "blocking": refusal.get("blocking")}
    else:
        from openprogram.runtime.conversation.rewinds.revert import revert_turn
        try:
            result = await _run(lambda: revert_turn(
                session_id, msg_id,
                idempotency_key=(cmd.get("idempotency_key") or None),
            ))
        finally:
            await _run(lambda: server.release_branches_operation(session_id, owners))
            # A window whose answer is lost asks for the receipt when told.
            operation_receipts.changed(cmd.get("idempotency_key"))
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
    from openprogram.interfaces.server import server as server
    refusal, owners = (await _run(lambda: _hold_turn_branches(
        session_id, msg_id, "reapply", cmd.get("idempotency_key") or None))
                       if session_id else (None, []))
    if refusal is not None:
        result = {"status": "blocked", "restored_paths": [], "conflicts": [], "unavailable": [],
                  "error": "run_active", "blocking": refusal.get("blocking")}
    else:
        from openprogram.runtime.conversation.rewinds.revert import reapply_turn
        try:
            result = await _run(lambda: reapply_turn(
                session_id, msg_id,
                idempotency_key=(cmd.get("idempotency_key") or None),
            ))
        finally:
            await _run(lambda: server.release_branches_operation(session_id, owners))
            # A window whose answer is lost asks for the receipt when told.
            operation_receipts.changed(cmd.get("idempotency_key"))
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


async def handle_review_revert_hunk(ws, cmd: dict) -> None:
    """Put one hunk of a Review diff back to its old side in the working file.

    Only a file in the review scope the client is showing can be written;
    a stale scope snapshot or a running turn refuses the edit.
    """
    values, validation_error = _review_request_values(cmd, include_path=True)
    session_id = values["session_id"]
    scope = values["scope"]
    path = values["path"]
    snapshot_id = values["snapshot_id"]
    hunk = cmd.get("hunk")
    from openprogram.interfaces.server import server as server
    if (validation_error or scope not in _REVIEW_SCOPES or scope == "staged"
            or not session_id or not path or not isinstance(hunk, dict)):
        # A staged hunk lives in the index, not the working file: unstage it.
        result = {"status": "error", "error_code": "INVALID_REQUEST", "error": validation_error or "invalid request"}
    elif await _run(lambda: server._is_run_active(session_id)):
        result = {"status": "blocked", "error_code": "RUN_ACTIVE", "error": "run_active"}
    else:
        try:
            category, query, sort = values["category"], values["query"], values["sort"]
            current_scope = await _run(lambda: _current_scope(
                session_id, scope, values["assistant_msg_id"], category=category, query=query, sort=sort,
            ))
            in_scope = any(row.get("path") == path for row in current_scope.get("files", []))
            if current_scope.get("status") != "ready" or current_scope.get("snapshot_id") != snapshot_id:
                result = {"status": "error", "error_code": "STALE_SNAPSHOT", "error": "STALE_SNAPSHOT"}
            elif not in_scope:
                result = {"status": "error", "error_code": "INVALID_REQUEST", "error": "path is not in review scope"}
            else:
                from .hunk import revert_review_hunk
                result = await _run(lambda: revert_review_hunk(
                    session_id, path, hunk, cmd.get("idempotency_key") or None,
                ))
        except Exception as exc:
            result = {"status": "error", "error_code": "UNAVAILABLE", "error": str(exc)}
    await ws.send_text(json.dumps({
        "type": "review_revert_hunk_result",
        "data": {
            "session_id": session_id, "scope": scope, "path": path, "snapshot_id": snapshot_id,
            "request_id": values["request_id"], **result, "action": "review_revert_hunk",
        },
    }, default=str))


async def handle_review_stage(ws, cmd: dict) -> None:
    """Stage (Unstaged scope) or unstage (Staged scope) one hunk, or a whole
    file when the request carries no hunk.

    Like Revert, only a file of the scope snapshot the client is showing
    can change, a newer snapshot or a running turn refuses, and a hunk the
    index no longer matches is a conflict.
    """
    values, validation_error = _review_request_values(cmd, include_path=True)
    session_id = values["session_id"]
    scope = values["scope"]
    path = values["path"]
    snapshot_id = values["snapshot_id"]
    hunk = cmd.get("hunk")
    from openprogram.interfaces.server import server as server
    if (validation_error or scope not in _WORKSPACE_SCOPES or not session_id or not path
            or (hunk is not None and not isinstance(hunk, dict))):
        result = {"status": "error", "error_code": "INVALID_REQUEST", "error": validation_error or "invalid request"}
    elif await _run(lambda: server._is_run_active(session_id)):
        result = {"status": "blocked", "error_code": "RUN_ACTIVE", "error": "run_active"}
    else:
        try:
            category, query, sort = values["category"], values["query"], values["sort"]
            current_scope = await _run(lambda: _current_scope(
                session_id, scope, "", category=category, query=query, sort=sort,
            ))
            member = next((row for row in current_scope.get("files", []) if row.get("path") == path), None)
            if current_scope.get("status") != "ready" or current_scope.get("snapshot_id") != snapshot_id:
                result = {"status": "error", "error_code": "STALE_SNAPSHOT", "error": "STALE_SNAPSHOT"}
            elif not current_scope.get("staging"):
                result = {"status": "error", "error_code": "UNAVAILABLE", "error": "staging is unavailable here"}
            elif member is None:
                result = {"status": "error", "error_code": "INVALID_REQUEST", "error": "path is not in review scope"}
            else:
                from pathlib import Path as _Path
                from .stage import stage_change
                root = _Path(current_scope.get("root") or "")
                result = await _run(lambda: stage_change(root, scope, member, hunk))
        except Exception as exc:
            result = {"status": "error", "error_code": "UNAVAILABLE", "error": str(exc)}
    await ws.send_text(json.dumps({
        "type": "review_stage_result",
        "data": {
            "session_id": session_id, "scope": scope, "path": path, "snapshot_id": snapshot_id,
            "request_id": values["request_id"], **result, "action": "review_stage",
        },
    }, default=str))


_AGENT_REVIEW_FILES = 60


def _scope_diff_text(session_id: str, scope: str, member: dict, snapshot: dict) -> str:
    """One file's whole diff in a review scope, page after page."""
    from .agent_review import MAX_REVIEW_CHARS
    pages: list[str] = []
    offset: int | None = 0
    used = 0
    while offset is not None and used < MAX_REVIEW_CHARS:
        if scope == "turn":
            page = _review_turn_file_diff(session_id, member, member["path"], offset)
        elif scope == "branch":
            page = _branch_file_diff(session_id, member["path"], offset)
        else:
            content = snapshot.get("workspace", {}).get("candidates", {}).get(member["path"])
            if content is None:
                return ""
            page = _render_workspace_content_diff(content, member.get("rel") or member["path"], offset, scope)
        if page.get("diff_state") != "available":
            break
        text = page.get("diff") or ""
        if offset and pages:
            # A continuation page opens with a synthetic, countless header.
            text = re.sub(r"\A@@ -\d+ \+\d+ @@\n", "", text)
        pages.append(text)
        used += len(text)
        offset = page.get("next_cursor") if isinstance(page.get("next_cursor"), int) else None
    return "".join(pages)


_agent_review_tasks: set[asyncio.Task] = set()
# Running reviews by the client's review_id, so Stop review can end one
# and a client whose socket dropped can wait for it again.
_agent_reviews_by_id: dict[str, asyncio.Task] = {}
# Finished answers by review_id. A review takes minutes; the socket that
# asked may be gone by then, so the answer waits here for the client to
# come back with review_agent_wait.
_agent_review_results: dict[str, tuple[float, dict]] = {}
_AGENT_REVIEW_RESULT_TTL = 3600.0
_AGENT_REVIEW_RESULTS_MAX = 64


def _keep_review_result(review_id: str, data: dict) -> None:
    now = time.monotonic()
    for key, (at, _data) in list(_agent_review_results.items()):
        if now - at > _AGENT_REVIEW_RESULT_TTL:
            del _agent_review_results[key]
    while len(_agent_review_results) >= _AGENT_REVIEW_RESULTS_MAX:
        del _agent_review_results[next(iter(_agent_review_results))]
    _agent_review_results[review_id] = (now, data)


def _review_id(cmd: dict) -> str | None:
    value = cmd.get("review_id")
    if isinstance(value, str) and value and len(value.encode("utf-8")) <= _MAX_REVIEW_TEXT_BYTES:
        return value
    return None


async def handle_review_agent(ws, cmd: dict) -> asyncio.Task:
    """Review with agent: the session's model reviews the shown scope's diff
    and answers with findings (path, line, severity, body).

    The model call takes a while, so it runs beside the connection's
    ordered frames and answers when it is done. ``review_agent_cancel``
    with the same ``review_id`` stops it."""
    task = asyncio.create_task(_review_agent(ws, cmd))
    _agent_review_tasks.add(task)
    task.add_done_callback(_agent_review_tasks.discard)
    review_id = _review_id(cmd)
    if review_id:
        _agent_reviews_by_id[review_id] = task

        def forget(done: asyncio.Task) -> None:
            if _agent_reviews_by_id.get(review_id) is done:
                del _agent_reviews_by_id[review_id]

        task.add_done_callback(forget)
    return task


async def handle_review_agent_wait(ws, cmd: dict) -> asyncio.Task:
    """A client whose socket dropped during a review asks for its answer
    again: at once when it is done, when it finishes while it runs, and
    UNKNOWN_REVIEW when this Worker never ran it (it restarted)."""
    task = asyncio.create_task(_review_agent_wait(ws, cmd))
    _agent_review_tasks.add(task)
    task.add_done_callback(_agent_review_tasks.discard)
    return task


async def _review_agent_wait(ws, cmd: dict) -> None:
    review_id = _review_id(cmd)
    running = _agent_reviews_by_id.get(review_id) if review_id else None
    if running is not None:
        try:
            # Shielded: this waiter going away must not cancel the review.
            await asyncio.shield(running)
        except asyncio.CancelledError:
            if not running.cancelled():
                raise
        except Exception:
            pass
    kept = _agent_review_results.get(review_id) if review_id else None
    data = dict(kept[1]) if kept else {
        "status": "error", "error_code": "UNKNOWN_REVIEW", "error": "UNKNOWN_REVIEW",
    }
    data.update({"request_id": cmd.get("request_id"), "review_id": review_id, "action": "review_agent_wait"})
    try:
        await ws.send_text(json.dumps({"type": "review_agent_result", "data": data}, default=str))
    except Exception:
        logging.getLogger(__name__).debug("review findings not delivered", exc_info=True)


async def handle_review_agent_cancel(ws, cmd: dict) -> None:
    """Stop review: cancel the running review and its model request. The
    client already stopped waiting, so nothing is sent back."""
    review_id = _review_id(cmd)
    task = _agent_reviews_by_id.pop(review_id, None) if review_id else None
    if task is not None:
        task.cancel()


async def _review_agent(ws, cmd: dict) -> None:
    values, validation_error = _review_request_values(cmd)
    session_id = values["session_id"]
    scope = values["scope"]
    turn_id = values["assistant_msg_id"]
    snapshot_id = values["snapshot_id"]
    if validation_error or scope not in _REVIEW_SCOPES or not session_id or not snapshot_id:
        result = {"status": "error", "error_code": "INVALID_REQUEST", "error": validation_error or "invalid request"}
    else:
        try:
            category, query, sort = values["category"], values["query"], values["sort"]
            current_scope = await _run(lambda: _current_scope(
                session_id, scope, turn_id, category=category, query=query, sort=sort,
            ))
            saved = _get_review_snapshot(snapshot_id)
            if (current_scope.get("status") != "ready" or current_scope.get("snapshot_id") != snapshot_id
                    or saved is None):
                result = {"status": "error", "error_code": "STALE_SNAPSHOT", "error": "STALE_SNAPSHOT"}
            else:
                from .agent_review import normalize_findings, review_context, run_agent_review
                members = [row for row in current_scope.get("files", [])
                           if row.get("diff_state") in {None, "available"} and not row.get("binary")]
                members = members[:_AGENT_REVIEW_FILES]

                def collect() -> list[tuple[str, str]]:
                    return [(row.get("rel") or row["path"], diff) for row in members
                            if (diff := _scope_diff_text(session_id, scope, row, saved))]

                diffs = await _run(collect)
                if not diffs:
                    result = {"status": "ready", "findings": []}
                else:
                    answer = await run_agent_review(session_id, review_context(diffs))
                    if answer is None:
                        result = {"status": "error", "error_code": "UNAVAILABLE", "error": "the model did not answer"}
                    else:
                        result = {"status": "ready",
                                  "findings": normalize_findings(answer, {rel for rel, _diff in diffs})}
        except Exception as exc:
            result = {"status": "error", "error_code": "UNAVAILABLE", "error": str(exc)}
    data = {"session_id": session_id, "scope": scope, "snapshot_id": snapshot_id, **result}
    review_id = _review_id(cmd)
    if review_id:
        _keep_review_result(review_id, data)
    try:
        await ws.send_text(json.dumps({
            "type": "review_agent_result",
            "data": {**data, "request_id": values["request_id"], "action": "review_agent"},
        }, default=str))
    except Exception:
        # The requesting socket closed; the findings were only for it.
        logging.getLogger(__name__).debug("review findings not delivered", exc_info=True)


ACTIONS = {
    "review_scope": handle_review_scope,
    "review_file_diff": handle_review_file_diff,
    "review_revert_hunk": handle_review_revert_hunk,
    "review_stage": handle_review_stage,
    "review_agent": handle_review_agent,
    "review_agent_cancel": handle_review_agent_cancel,
    "review_agent_wait": handle_review_agent_wait,
    "turn_history_state": handle_turn_history_state,
    "turn_operation_status": handle_turn_operation_status,
    "revert_turn": handle_revert_turn,
    "reapply_turn": handle_reapply_turn,
}
