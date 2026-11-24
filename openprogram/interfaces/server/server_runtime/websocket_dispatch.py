"""Server websocket dispatch."""
from __future__ import annotations

import logging
import queue
import time
import uuid

from .. import server as state

_log = logging.getLogger(__name__)

#: An action that takes this long is logged by name.
SLOW_ACTION_SECONDS = 2.0

#: Read-only actions whose reply the client matches by its own request id.
#: They run beside the connection's ordered lane, so a slow one never delays
#: a later ``chat`` or ``stop``. Every other action keeps the connection's
#: order: it starts only after the frames before it finish. A read still
#: sees every ordered action sent before it (it starts after them), and two
#: frames of the same read action answer in the order sent.
#: ``list_projects`` stays ordered: the web matches ``projects_list`` by
#: type, so a listing begun before a ``create_project`` and answered after
#: it would be taken as the reply to the re-list that follows the create.
#: ``load_session`` runs here too: the web matches ``session_loaded`` and
#: ``session_history_page`` by request id, a turn's row frames for the
#: conversation are parked until the read's frame is queued
#: (``ReadParking``), and the run frames that follow carry ``run_seq``, so a
#: chat or stop sent after a slow first read of a long conversation starts
#: at once instead of waiting for it.
CONCURRENT_ACTIONS = frozenset({
    "load_session",
    "project_folder_size",
    "project_file_info",
    "project_file_tree",
    "project_file_search",
})

#: At most this many side reads in flight per connection. A client that
#: sends more waits in the receive loop until one finishes, so a flood of
#: reads is held back by the socket instead of piling up tasks.
MAX_SIDE_TASKS = 32


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
    from openprogram.interfaces.server.ws_errors import (
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
    logger = logging.getLogger("openprogram.interfaces.server")
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
            # The socket may already be gone; the disconnect below ends the
            # handler.
            _log.debug(
                "websocket close after persistence failure failed", exc_info=True)
        raise WebSocketDisconnect(1011) from store_exc
    await ws.send_text(state.json.dumps(frame))


def _socket_closed_by_server(ws) -> bool:
    """Whether this side already closed the socket (so receiving is over)."""
    if getattr(ws, "_closing", False):
        return True
    application_state = getattr(ws, "application_state", None)
    return application_state is not None and getattr(application_state, "name", "") != "CONNECTED"


async def _run_command(ws, cmd: dict) -> None:
    """Run one frame's action and answer its failure on the wire."""
    from starlette.websockets import WebSocketDisconnect
    from openprogram.interfaces.server.ws_errors import OperationError

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


async def _run_side_command(ws, cmd: dict, previous) -> None:
    """A read-only frame beside the ordered lane, after the previous frame
    of the same action. A closed socket ends it quietly: the receive loop
    sees the close and tears the connection down."""
    from starlette.websockets import WebSocketDisconnect

    if previous is not None:
        await state.asyncio.wait([previous])
    try:
        await _run_command(ws, cmd)
    except WebSocketDisconnect:
        pass
    except Exception:
        # Answering the failure itself failed; the frame has nowhere to go.
        _log.debug("side websocket action %r failed", cmd.get("action"), exc_info=True)


async def _websocket_handler(ws):
    """WebSocket endpoint for real-time chat streaming."""
    from starlette.websockets import WebSocketDisconnect

    await ws.accept()

    from openprogram.interfaces.server.ws_delivery import QueuedWebSocket

    ws = QueuedWebSocket(ws, state.asyncio.get_running_loop())
    ws.start()

    with state._ws_lock:
        state._ws_connections.append(ws)
    # Read-only actions in flight beside the ordered lane: the last task per
    # action (the next frame of that action waits for it) and all of them
    # (cancelled when the connection ends).
    side_lanes: dict = {}
    side_tasks: set = set()
    try:
        # Off the event loop: a page connecting must not stall the others.
        functions = await state.asyncio.to_thread(state._discover_functions)
        await ws.send_text(state.json.dumps(
            {"type": "functions_list", "data": functions}, default=str
        ))
        # Send current provider info
        await ws.send_text(state.json.dumps(
            {"type": "provider_info", "data": state._get_provider_info()}, default=str
        ))
        # A page reconnecting to a worker that serves another export reloads.
        from openprogram.interfaces.server.frontend import export_id
        await ws.send_text(state.json.dumps(
            {"type": "frontend_build", "data": {"id": export_id()}}
        ))
        # What a client may rely on this Worker for (a stop that is never
        # refused as stale, a stop that names a send); a client keeps its
        # fallbacks for a Worker that does not say so. ``server`` says who
        # the Worker is and every capability it has (``handshake``).
        from openprogram.interfaces.server.handshake import server_identity
        from openprogram.runtime.execution.control.submission import CONTROL_CAPABILITIES
        await ws.send_text(state.json.dumps(
            {"type": "worker_capabilities",
             "data": {name: True for name in CONTROL_CAPABILITIES},
             "server": server_identity()}
        ))

        # Keep alive — receive pings/messages
        while True:
            try:
                data = await ws.receive_text()
            except RuntimeError as exc:
                # Starlette refuses to receive on a socket this side already
                # closed (a queue overflow, a send failure): that is the end
                # of the connection, not an error of the handler.
                if _socket_closed_by_server(ws):
                    state._log(f"[ws] connection closed by server ({getattr(ws, 'disconnect_reason', None)})")
                    break
                raise exc
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
                action = cmd.get("action")
                if isinstance(action, str) and action in CONCURRENT_ACTIONS:
                    # The connection's protocol is known before any later
                    # frame runs, as it would be for an ordered action.
                    _note_history_protocol(ws, cmd)
                    # Chained behind the previous frame of the same action
                    # only; the receive loop goes on to the next frame.
                    while len(side_tasks) >= MAX_SIDE_TASKS:
                        await state.asyncio.wait(
                            set(side_tasks), return_when=state.asyncio.FIRST_COMPLETED)
                    previous = side_lanes.get(action)
                    task = state.asyncio.create_task(
                        _run_side_command(ws, cmd, previous))
                    side_lanes[action] = task
                    side_tasks.add(task)
                    task.add_done_callback(side_tasks.discard)
                    continue
                await _run_command(ws, cmd)

    except WebSocketDisconnect as e:
        # Normal client departure (refresh/close, codes 1000/1001/1005) —
        # one quiet line, no stack; a traceback here buries real errors.
        state._log(f"[ws] client disconnected ({e.code})")
    except Exception:
        import logging
        # structured + carries the traceback; never dumps a raw trace to stdout
        logging.getLogger("openprogram.interfaces.server").exception("[ws] connection error")
    finally:
        for task in list(side_tasks):
            task.cancel()
        if side_tasks:
            await state.asyncio.gather(*side_tasks, return_exceptions=True)
        from openprogram.programs._host.framework.interface import release
        release(ws)
        from openprogram.interfaces.server.ws_actions.webtab import release_connection

        release_connection(ws)
        from openprogram.interfaces.server.transcript import publisher

        publisher.release(ws)
        from openprogram.interfaces.server.remote_machines.forward import release as release_forwarded

        await release_forwarded(ws)
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
                except queue.Full:
                    pass
        await ws.stop()


def _build_ws_action_registry() -> dict:
    """Lazy-build the action → handler dispatch table.

    Done at module import time but populated from ws_actions/* modules
    that internally `from openprogram.interfaces.server import server as _s` — safe
    because lookup only happens when an action fires at WS-message time,
    well after server.py has finished loading.
    """
    from openprogram.interfaces.server.ws_actions import (
        agent as _ws_agent,
        branch as _ws_branch,
        dag_nodes as _ws_dag_nodes,
        embedded_window as _ws_embedded_window,
        channel as _ws_channel,
        machines as _ws_machines,
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
        folder_git as _ws_folder_git,
        project as _ws_project,
        settings as _ws_settings,
        user_error as _ws_user_error,
        webtab as _ws_webtab,
        interface as _ws_interface,
    )
    table: dict = {}
    table.update(_ws_branch.ACTIONS)
    table.update(_ws_dag_nodes.ACTIONS)
    table.update(_ws_session.ACTIONS)
    table.update(_ws_permissions.ACTIONS)
    table.update(_ws_agent.ACTIONS)
    table.update(_ws_channel.ACTIONS)
    table.update(_ws_machines.ACTIONS)
    table.update(_ws_runtime.ACTIONS)
    table.update(_ws_chat.ACTIONS)
    table.update(_ws_commits.ACTIONS)
    table.update(_ws_turn_files.ACTIONS)
    table.update(_ws_files.ACTIONS)
    table.update(_ws_sub_agent.ACTIONS)
    table.update(_ws_merge.ACTIONS)
    table.update(_ws_job.ACTIONS)
    table.update(_ws_worktree.ACTIONS)
    table.update(_ws_folder_git.ACTIONS)
    table.update(_ws_project.ACTIONS)
    table.update(_ws_settings.ACTIONS)
    table.update(_ws_user_error.ACTIONS)
    table.update(_ws_webtab.ACTIONS)
    table.update(_ws_interface.ACTIONS)
    table.update(_ws_embedded_window.ACTIONS)
    return table


def _note_history_protocol(ws, cmd: dict) -> None:
    """The web reads turns (history protocol 3); every other client gets plain rows."""
    if cmd.get("action") in ("list_sessions", "load_session") and cmd.get("history_version") == 3:
        ws._history_protocol = 3


def _valid_uuid_request_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _validate_file_request(cmd: dict, action: str) -> None:
    """Reject file requests before a handler can start filesystem work."""
    from openprogram.interfaces.server.ws_errors import OperationError

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
    from openprogram.operations.self_update.verification.ui_checks import permits_ws_command
    if not permits_ws_command(ws, cmd):
        await ws.send_text(state.json.dumps({
            "type": "action_error",
            "data": {"code": "ui_verification_active"},
        }))
        return
    from openprogram.interfaces.server.ws_errors import safe_operation_metadata

    action = safe_operation_metadata(cmd.get("action"))
    if action is None:
        from openprogram.interfaces.server.ws_errors import OperationError

        raise OperationError("invalid_request", scope="system")
    state._validate_file_request(cmd, action)
    _note_history_protocol(ws, cmd)
    # A client that says who it is (``handshake.client_identity``), kept for
    # the connection's log lines.
    if "client" in cmd and getattr(ws, "_client_identity", None) is None:
        from openprogram.interfaces.server.handshake import client_identity

        identity = client_identity(cmd)
        if identity is not None:
            ws._client_identity = identity
            _log.debug("ws client %s %s", identity.get("name"), identity.get("version"))
    _log.debug("ws command received: action=%s", action)

    # An action on a conversation that lives on another computer goes there.
    from openprogram.interfaces.server.remote_machines.forward import forward
    if await forward(ws, cmd):
        return

    h = state.WS_ACTIONS.get(action)
    if h is not None:
        started = time.monotonic()
        try:
            await h(ws, cmd)
        finally:
            # An ordered action holds the connection, so its next frame (a
            # chat send included) waited this long. Naming the slow action
            # traces a send acknowledged late to what held the socket.
            elapsed = time.monotonic() - started
            if elapsed >= SLOW_ACTION_SECONDS:
                _log.warning(
                    "ws action %s took %.1fs (session %s); %s",
                    action, elapsed, cmd.get("session_id") or "-",
                    "it ran beside the connection's other frames"
                    if action in CONCURRENT_ACTIONS
                    else "later frames on this connection waited")
        return

    # Unknown action. Silently dropping these is how a frontend command
    # that names a handler nobody wrote (or renamed) looks exactly like a
    # backend that is merely slow — no error, no log, no clue. Say so on
    # both channels. ``apps/web/scripts/ui/check-ws-actions.mjs`` is the guard
    # that keeps this branch unreachable in practice.
    await state._send_operation_error(ws, cmd, code="unknown_action")
