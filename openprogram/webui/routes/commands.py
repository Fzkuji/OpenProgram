"""Unified slash-command HTTP API.

Replaces / supersedes the older ``/api/plugins/commands`` endpoint
(kept as a thin redirect in ``routes/plugins.py``). Backs the web
composer's slash menu and the CLI's slash dispatcher.
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/commands")
    async def list_commands_api(source: str | None = None,
                                include_hidden: bool = False):
        """Flattened, override-resolved list of every command the
        system knows about. ``?source=plugin`` filters to one bucket.
        """
        from openprogram import commands as _cmd
        items = _cmd.list_all(include_hidden=include_hidden)
        if source:
            items = [s for s in items if s.source == source]
        return JSONResponse(content={
            "commands": [s.to_view() for s in items],
            "sources": _cmd.SOURCE_ORDER,
        })

    @app.get("/api/commands/{name}")
    async def get_command_api(name: str):
        from openprogram import commands as _cmd
        spec = _cmd.get(name)
        if spec is None:
            return JSONResponse(content={"error": f"unknown: {name}"},
                                status_code=404)
        view = spec.to_view()
        view["body"] = spec.raw.body if spec.raw else ""
        return JSONResponse(content=view)

    @app.post("/api/commands/reload")
    async def reload_commands_api():
        from openprogram import commands as _cmd
        _cmd.reload()
        return JSONResponse(content={"ok": True,
                                     "count": len(_cmd.list_all(include_hidden=True))})

    @app.get("/api/commands/conflicts")
    async def conflicts_api():
        from openprogram.commands import registry as _reg
        return JSONResponse(content={"conflicts": _reg.conflicts()})

    @app.post("/api/commands/invoke")
    async def invoke_command_api(body: dict[str, Any]):
        """Render a command body for a given args string. Caller (web
        ws_actions) is responsible for actually posting the rendered
        text to the agent — this endpoint only resolves + renders.
        """
        from openprogram.commands.dispatch import invoke
        text = str(body.get("text") or "").strip()
        session_id = str(body.get("session_id") or "")
        cwd = body.get("cwd")
        res = invoke(text, session_id=session_id, cwd=cwd if isinstance(cwd, str) else None)
        return JSONResponse(content={
            "ok": res.ok,
            "kind": res.kind,
            "rendered": res.rendered,
            "context": res.context,
            "agent": res.agent,
            "model": res.model,
            "effort": res.effort,
            "allowed_tools": res.allowed_tools,
            "error": res.error,
            "command": res.command_name,
            "source": res.source,
        })
