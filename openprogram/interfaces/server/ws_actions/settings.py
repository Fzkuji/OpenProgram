"""Settings WS actions: read + write user settings over the worker socket.

The TUI settings screen talks to these two actions over the SAME worker
WebSocket it already uses for ``list_models`` / ``set_default_agent`` —
no new transport. Both delegate to ``openprogram.state.settings`` (the one
source of truth), so the TUI, the ``setup`` CLI sections, and the web
pages all read and write the same validated settings.
"""
from __future__ import annotations

import asyncio
import json


async def handle_get_settings(ws, cmd: dict):
    session_id = (cmd.get("session_id") or "").strip() or None
    # A conversation's own limits are read under its lock: off the loop.
    rows = await asyncio.to_thread(_settings_rows, session_id)
    await ws.send_text(json.dumps({"type": "settings", "data": rows}, default=str))


def _settings_rows(session_id: str | None):
    from openprogram.state.settings import get_settings
    resolved = None
    if session_id:
        from openprogram.runtime.jobs.resource_governance import (
            global_resource_limits,
            resolve_resource_limits,
            session_resource_limits,
        )
        resolved = resolve_resource_limits(
            global_resource_limits(),
            session=session_resource_limits(session_id),
        )
    return get_settings(resolved_resources=resolved)


async def handle_set_setting(ws, cmd: dict):
    from openprogram.operations.worker.ports import with_port_owner_note
    from openprogram.state.settings import set_setting
    key = cmd.get("key") or ""
    value = cmd.get("value")
    res = with_port_owner_note(key, set_setting(key, value))
    await ws.send_text(json.dumps({
        "type": "setting_result", "data": {"key": key, **res},
    }, default=str))


ACTIONS = {
    "get_settings": handle_get_settings,
    "set_setting": handle_set_setting,
}
