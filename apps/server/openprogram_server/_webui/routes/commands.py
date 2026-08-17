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

        For MCP-sourced commands the rendering happens on the remote
        server: dispatch returns ``kind="mcp_prompt"`` plus the
        server / prompt name and we round-trip through the MCP client
        to fetch the message blocks, flattening them into plain text
        before returning.
        """
        from openprogram.commands.dispatch import invoke
        text = str(body.get("text") or "").strip()
        session_id = str(body.get("session_id") or "")
        cwd = body.get("cwd")
        res = invoke(text, session_id=session_id, cwd=cwd if isinstance(cwd, str) else None)

        rendered = res.rendered
        if res.ok and res.kind == "mcp_prompt":
            info = res.local_handler or {}
            rendered = await _render_mcp_prompt(info)
            kind = "prompt"   # caller treats it like any other prompt
        else:
            kind = res.kind

        return JSONResponse(content={
            "ok": res.ok and bool(rendered or res.kind != "mcp_prompt"),
            "kind": kind,
            "rendered": rendered,
            "context": res.context,
            "agent": res.agent,
            "model": res.model,
            "effort": res.effort,
            "allowed_tools": res.allowed_tools,
            "error": res.error,
            "command": res.command_name,
            "source": res.source,
        })


async def _render_mcp_prompt(info: dict[str, Any]) -> str:
    """Call the live MCP client's get_prompt and flatten the result
    into one plain-text body suitable for dropping into the textarea.
    """
    server = (info or {}).get("server")
    prompt = (info or {}).get("prompt")
    raw_args = (info or {}).get("raw_args") or ""
    declared = (info or {}).get("declared") or []
    if not server or not prompt:
        return ""

    from openprogram.mcp.registry import get_client
    from openprogram.commands.template import parse_args

    client = get_client(server)
    if client is None or not getattr(client, "is_ready", False):
        return ""

    positional = parse_args(raw_args)
    args: dict[str, Any] = {}
    for i, spec in enumerate(declared):
        name = spec.get("name") if isinstance(spec, dict) else None
        if not name:
            continue
        if i < len(positional):
            args[name] = positional[i]

    try:
        result = await client.get_prompt(prompt, args)
    except Exception as e:  # noqa: BLE001
        return f"[mcp-prompt error: {type(e).__name__}: {e}]"

    return _flatten_prompt_messages(result)


def _flatten_prompt_messages(result: dict[str, Any]) -> str:
    """Best-effort: walk the ``messages`` array and pull out text
    blocks. MCP message content can be a string, a dict with a
    ``text`` field, or a list of such blocks."""
    messages = (result or {}).get("messages") or []
    out: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, str):
            out.append(content)
            continue
        if isinstance(content, dict):
            content = [content]
        if isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and isinstance(blk.get("text"), str):
                    out.append(blk["text"])
                elif isinstance(blk, str):
                    out.append(blk)
    return "\n\n".join(s for s in out if s)
