"""App lookups for computer_use rules, approvals and the @ menu."""
from __future__ import annotations

import asyncio
import json
import platform


_MAX_APP_LOOKUPS = 50


def _computer_use_apps(queries: list[str]) -> list[dict]:
    """Display name, bundle id and icon for the apps computer_use rules and approvals name,
    and the worker's platform, which decides what a step does to the user's own input."""
    host = platform.system().lower()
    try:
        from openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends import backend
        from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import ComputerUseError
        computer = backend()
    except Exception:
        return [{"query": q, "name": q, "bundle_id": "", "icon": "", "installed": False, "platform": host}
                for q in queries]
    out = []
    for query in queries:
        try:
            out.append({**computer.app_info(query), "platform": host})
        except ComputerUseError:
            out.append({"query": query, "name": query, "bundle_id": "", "icon": "", "installed": False,
                        "platform": host})
    return out


def _computer_use_running_apps() -> list[dict]:
    """The running apps the composer's @ menu offers; empty where there is no backend."""
    try:
        from openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends import backend
        from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import ComputerUseError
        return backend().running_apps()
    except (ComputerUseError, ImportError, OSError):
        return []


async def handle_computer_use_apps(ws, cmd: dict):
    """Look up apps by the names the model used, or with ``running`` list the
    running ones; launches nothing."""
    raw = cmd.get("apps")
    response = {"request_id": cmd.get("request_id"), "action": cmd.get("action"), "status": "ok"}
    if cmd.get("running") is True:
        response["apps"] = await asyncio.to_thread(_computer_use_running_apps)
    elif not isinstance(raw, list) or not all(isinstance(q, str) for q in raw) or len(raw) > _MAX_APP_LOOKUPS:
        response.update(status="error", error=f"apps must be a list of at most {_MAX_APP_LOOKUPS} names")
    else:
        queries = list(dict.fromkeys(q.strip()[:200] for q in raw if q.strip()))
        response["apps"] = await asyncio.to_thread(_computer_use_apps, queries)
    await ws.send_text(json.dumps({"type": "computer_use_apps", "data": response}))
