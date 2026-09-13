"""Server broadcasts."""
from __future__ import annotations
from ... import server as state


def _broadcast(msg: str):
    """Send a message to all connected WebSocket clients."""
    if not state._ws_connections:
        return
    from openprogram.webui.ws_delivery import send_to_connection

    with state._ws_lock:
        conns = list(state._ws_connections)
    for ws in conns:
        send_to_connection(ws, msg, state._loop)


def _broadcast_to_principal(
    msg: str,
    principal_id: str,
    *,
    exclude=None,
) -> None:
    """Send one owner-scoped state transition to matching connections."""
    if not state._ws_connections:
        return
    from openprogram.webui.ws_delivery import send_to_connection
    from openprogram.webui.ws_errors import principal_id_for_websocket

    with state._ws_lock:
        conns = list(state._ws_connections)
    for ws in conns:
        if ws is exclude:
            continue
        try:
            matches = principal_id_for_websocket(ws) == principal_id
        except PermissionError:
            matches = False
        if matches:
            send_to_connection(ws, msg, state._loop)


def _log(text: str):
    """Webui server log line.

    Stdout print is gated on "are we actually running as the webui
    server right now?" — when ``start_server`` has booted, the
    ``_server_thread`` global is alive, and stdout is the server's
    terminal where logs belong. Without that guard, every CLI REPL
    call that just imports ``_runtime_management`` (which calls this
    via ``_log``) would pollute the chat transcript with "[probe] xxx
    unavailable", "[restore] ...", etc.

    ``OPENPROGRAM_DEBUG_RUNTIME=1`` mirrors lines to stderr regardless
    of mode for devs tracing CLI startup.
    """
    if state._server_thread is not None and state._server_thread.is_alive():
        print(text)
    else:
        import os as _os
        if _os.environ.get("OPENPROGRAM_DEBUG_RUNTIME", "").strip() in ("1", "true", "yes"):
            import sys as _sys
            print(text, file=_sys.stderr, flush=True)
