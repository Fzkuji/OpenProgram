"""Durable user-error recovery and exact acknowledgement actions."""

from __future__ import annotations

import json
import time

from openprogram.webui.user_errors import (
    MAX_PAGE_SIZE,
    is_user_error_cursor,
)
from openprogram.webui.ws_errors import (
    OperationError,
    default_user_error_store,
    is_error_id,
    operation_recovered_frame,
    principal_id_for_websocket,
    safe_operation_metadata,
    utc_timestamp,
)


def _request_id(command: dict) -> str:
    request_id = safe_operation_metadata(command.get("request_id"))
    if request_id is None:
        raise OperationError("invalid_request", scope="system")
    return request_id


def _page(command: dict) -> tuple[str | None, int]:
    cursor = command.get("cursor")
    if cursor is not None and not is_user_error_cursor(cursor):
        raise OperationError("invalid_request", scope="system")
    limit = command.get("limit", MAX_PAGE_SIZE)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_PAGE_SIZE
    ):
        raise OperationError("invalid_request", scope="system")
    return cursor, limit


async def handle_list_user_errors(ws, command: dict) -> None:
    request_id = _request_id(command)
    cursor, limit = _page(command)
    principal_id = principal_id_for_websocket(ws)
    page = default_user_error_store().list_open(
        principal_id,
        cursor=cursor,
        limit=limit,
    )
    await ws.send_text(json.dumps({
        "type": "user_errors_list",
        "data": {
            "request_id": request_id,
            "errors": [record.wire_data() for record in page.records],
            "next_cursor": page.next_cursor,
        },
    }))


async def handle_acknowledge_user_error(ws, command: dict) -> None:
    request_id = _request_id(command)
    error_id = command.get("error_id")
    if not is_error_id(error_id):
        raise OperationError("invalid_request", scope="system")
    principal_id = principal_id_for_websocket(ws)
    epoch = time.time()
    store = default_user_error_store()
    record = store.acknowledge(
        principal_id,
        error_id,
        utc_timestamp(epoch),
        epoch,
    )
    frame = operation_recovered_frame(
        error_id,
        scope=record.scope if record is not None else "system",
        operation_id=record.operation_id if record is not None else None,
        occurred_at_epoch=epoch,
    )
    from openprogram.webui import server as _server

    payload = json.dumps(frame)
    _server._broadcast_to_principal(payload, principal_id, exclude=ws)
    await ws.send_text(json.dumps({
        "type": "user_error_acknowledged",
        "data": {"request_id": request_id, "error_id": error_id},
    }))
    await ws.send_text(payload)


ACTIONS = {
    "list_user_errors": handle_list_user_errors,
    "acknowledge_user_error": handle_acknowledge_user_error,
}
