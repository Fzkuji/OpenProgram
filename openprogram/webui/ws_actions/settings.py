"""Settings WS actions: read + write user settings over the worker socket.

The TUI settings screen talks to these two actions over the SAME worker
WebSocket it already uses for ``list_models`` / ``set_default_agent`` —
no new transport. Both delegate to ``openprogram.config_schema`` (the one
source of truth), so the TUI, the ``setup`` CLI sections, and the web
pages all read and write the same validated settings.
"""
from __future__ import annotations

import json


async def handle_get_settings(ws, cmd: dict):
    from openprogram.config_schema import get_settings
    try:
        rows = get_settings()
    except Exception as e:  # noqa: BLE001
        await ws.send_text(json.dumps({
            "type": "error", "data": {"message": f"get_settings: {e}"},
        }))
        return
    await ws.send_text(json.dumps({"type": "settings", "data": rows}, default=str))


async def handle_set_setting(ws, cmd: dict):
    from openprogram.config_schema import set_setting
    key = cmd.get("key") or ""
    value = cmd.get("value")
    try:
        res = set_setting(key, value)
    except Exception as e:  # noqa: BLE001
        res = {"error": str(e)}
    await ws.send_text(json.dumps({
        "type": "setting_result", "data": {"key": key, **res},
    }, default=str))


ACTIONS = {
    "get_settings": handle_get_settings,
    "set_setting": handle_set_setting,
}
