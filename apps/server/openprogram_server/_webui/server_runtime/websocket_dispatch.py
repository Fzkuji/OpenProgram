"""Server websocket dispatch."""
from __future__ import annotations
from ... import server as state


async def _send_operation_error(
    ws,
    cmd: object,
    *,
    code: str,
    retryable: bool = False,
    scope: str | None = None,
    severity: str = "error",
    exc=None,
) -> None:
    """Persist one safe command failure before enqueueing its wire frame."""
    from starlette.websockets import WebSocketDisconnect
    from openprogram.webui.ws_errors import (
        operation_error_frame,
        persist_operation_error_frame,
    )

    frame = operation_error_frame(
        cmd,
        code=code,
        retryable=retryable,
        scope=scope,
        severity=severity,
    )
    metadata = frame["data"]
    import logging
    logger = logging.getLogger("openprogram.webui")
    if exc is None:
        logger.warning(
            "[ws] command failed correlation_id=%s action=%r session_id=%r "
            "code=%s",
            metadata["correlation_id"],
            metadata["action"],
            metadata["session_id"],
            metadata["code"],
        )
    else:
        logger.error(
            "[ws] action failed correlation_id=%s action=%r session_id=%r "
            "error_type=%s",
            metadata["correlation_id"],
            metadata["action"],
            metadata["session_id"],
            type(exc).__name__,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
    try:
        persist_operation_error_frame(ws, frame)
    except Exception as store_exc:
        logger.critical(
            "[ws] user error persistence failed correlation_id=%s "
            "error_type=%s",
            metadata["correlation_id"],
            type(store_exc).__name__,
            exc_info=(type(store_exc), store_exc, store_exc.__traceback__),
        )
        try:
            await ws.close(code=1011, reason="state_recovery_required")
        except Exception:
            pass
        raise WebSocketDisconnect(1011) from store_exc
    await ws.send_text(state.json.dumps(frame))


async def _websocket_handler(ws):
    """WebSocket endpoint for real-time chat streaming."""
    from starlette.websockets import WebSocketDisconnect
    from openprogram.webui.ws_errors import OperationError

    await ws.accept()

    from openprogram.webui.ws_delivery import QueuedWebSocket

    ws = QueuedWebSocket(ws, state.asyncio.get_running_loop())
    ws.start()

    with state._ws_lock:
        state._ws_connections.append(ws)
    try:
        functions = state._discover_functions()
        await ws.send_text(state.json.dumps(
            {"type": "functions_list", "data": functions}, default=str
        ))
        # Send current provider info
        await ws.send_text(state.json.dumps(
            {"type": "provider_info", "data": state._get_provider_info()}, default=str
        ))

        # Keep alive — receive pings/messages
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text(state.json.dumps({"type": "pong"}))
            else:
                try:
                    cmd = state.json.loads(data)
                except state.json.JSONDecodeError:
                    await state._send_operation_error(
                        ws,
                        {},
                        code="invalid_request",
                        scope="system",
                    )
                    continue
                if not isinstance(cmd, dict):
                    await state._send_operation_error(
                        ws,
                        {},
                        code="invalid_request",
                        scope="system",
                    )
                    continue
                try:
                    await state._handle_ws_command(ws, cmd)
                except WebSocketDisconnect:
                    raise
                except OperationError as operation_error:
                    await state._send_operation_error(
                        ws,
                        cmd,
                        code=operation_error.code,
                        retryable=operation_error.retryable,
                        scope=operation_error.scope,
                        severity=operation_error.severity,
                    )
                except Exception as exc:
                    await state._send_operation_error(
                        ws,
                        cmd,
                        code="handler_error",
                        exc=exc,
                    )

    except WebSocketDisconnect as e:
        # Normal client departure (refresh/close, codes 1000/1001/1005) —
        # one quiet line, no stack; a traceback here buries real errors.
        state._log(f"[ws] client disconnected ({e.code})")
    except Exception:
        import logging
        # structured + carries the traceback; never dumps a raw trace to stdout
        logging.getLogger("openprogram.webui").exception("[ws] connection error")
    finally:
        from openprogram.webui.ws_actions.webtab import release_connection

        release_connection(ws)
        with state._ws_lock:
            try:
                state._ws_connections.remove(ws)
            except ValueError:
                pass
            focused_session_id = getattr(ws, "_focused_session_id", None)
            has_other_observer = bool(focused_session_id) and any(
                getattr(conn, "_focused_session_id", None) == focused_session_id
                for conn in state._ws_connections
            )
        if focused_session_id and not has_other_observer:
            with state._follow_up_lock:
                follow_up_queue = state._follow_up_queues.get(focused_session_id)
            if follow_up_queue is not None:
                try:
                    follow_up_queue.put_nowait(state._FOLLOW_UP_DISCONNECTED)
                except state.queue.Full:
                    pass
        await ws.stop()


def _build_ws_action_registry() -> dict:
    """Lazy-build the action → handler dispatch table.

    Done at module import time but populated from ws_actions/* modules
    that internally `from openprogram.webui import server as _s` — safe
    because lookup only happens when an action fires at WS-message time,
    well after server.py has finished loading.
    """
    from openprogram.webui.ws_actions import (
        agent as _ws_agent,
        branch as _ws_branch,
        channel as _ws_channel,
        chat as _ws_chat,
        runtime as _ws_runtime,
        session as _ws_session,
        permissions as _ws_permissions,
        context_commits as _ws_commits,
        turn_files as _ws_turn_files,
        files as _ws_files,
        sub_agent as _ws_sub_agent,
        merge as _ws_merge,
        job as _ws_job,
        worktree as _ws_worktree,
        project as _ws_project,
        settings as _ws_settings,
        user_error as _ws_user_error,
        webtab as _ws_webtab,
    )
    table: dict = {}
    table.update(_ws_branch.ACTIONS)
    table.update(_ws_session.ACTIONS)
    table.update(_ws_permissions.ACTIONS)
    table.update(_ws_agent.ACTIONS)
    table.update(_ws_channel.ACTIONS)
    table.update(_ws_runtime.ACTIONS)
    table.update(_ws_chat.ACTIONS)
    table.update(_ws_commits.ACTIONS)
    table.update(_ws_turn_files.ACTIONS)
    table.update(_ws_files.ACTIONS)
    table.update(_ws_sub_agent.ACTIONS)
    table.update(_ws_merge.ACTIONS)
    table.update(_ws_job.ACTIONS)
    table.update(_ws_worktree.ACTIONS)
    table.update(_ws_project.ACTIONS)
    table.update(_ws_settings.ACTIONS)
    table.update(_ws_user_error.ACTIONS)
    table.update(_ws_webtab.ACTIONS)
    return table


def _valid_uuid_request_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(state.uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _validate_file_request(cmd: dict, action: str) -> None:
    """Reject file requests before a handler can start filesystem work."""
    from openprogram.webui.ws_errors import OperationError

    if action not in state._FILE_REQUEST_ACTIONS:
        return
    if not state._valid_uuid_request_id(cmd.get("request_id")):
        raise OperationError("invalid_request", scope="system")
    if action in state._FILE_MUTATION_ACTIONS:
        key = cmd.get("idempotency_key")
        if not state._valid_uuid_request_id(key):
            raise OperationError("invalid_request", scope="system")


async def _handle_ws_command(ws, cmd: dict):
    """Handle a WebSocket command from the client."""
    if state._server_stopping.is_set():
        await ws.send_text(state.json.dumps({"type": "action_error", "data": {
            "code": "worker_stopping", "message": "OpenProgram is restarting. Reconnect before continuing.",
        }}))
        return
    from openprogram.self_update.verification.ui_checks import permits_ws_command
    if not permits_ws_command(ws, cmd):
        await ws.send_text(state.json.dumps({
            "type": "action_error",
            "data": {"code": "ui_verification_active"},
        }))
        return
    from openprogram.webui.ws_errors import safe_operation_metadata

    action = safe_operation_metadata(cmd.get("action"))
    if action is None:
        from openprogram.webui.ws_errors import OperationError

        raise OperationError("invalid_request", scope="system")
    state._validate_file_request(cmd, action)
    if action == "list_sessions" and cmd.get("history_version") == 1:
        ws._history_protocol = 1
    print(f"[ws] command received: action={action}")

    h = state.WS_ACTIONS.get(action)
    if h is not None:
        # load_session cannot be moved wholesale to asyncio.to_thread: it
        # awaits session_loaded, running_task, and question replay frames at
        # different points. Its blocking models.dev lookup is SWR, and cold
        # context accounting is offloaded inside the handler. The remaining
        # DB/graph hydration stays synchronous until computation and sends can
        # be separated without changing the frame contract.
        await h(ws, cmd)
        return

    # Unknown action. Silently dropping these is how a frontend command
    # that names a handler nobody wrote (or renamed) looks exactly like a
    # backend that is merely slow — no error, no log, no clue. Say so on
    # both channels. ``apps/web/scripts/ui/check-ws-actions.mjs`` is the guard
    # that keeps this branch unreachable in practice.
    await state._send_operation_error(ws, cmd, code="unknown_action")
