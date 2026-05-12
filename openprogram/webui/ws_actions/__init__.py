"""WebSocket action handlers, split out from server._handle_ws_command.

Each module exports ``async def handle_<name>(ws, cmd)`` functions.
The main dispatcher in server.py routes ``cmd['action']`` to the
appropriate handler via a dict in ``server.WS_ACTIONS``.

State / helpers come from ``openprogram.webui.server`` via a lazy
``from openprogram.webui import server as _s`` inside each handler.
"""
