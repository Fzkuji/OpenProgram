"""list_mcp_prompts / get_mcp_prompt — MCP "prompts" surface.

Prompts are parameterised text templates the server returns when
asked. claude-code surfaces them as slash-commands; we expose them as
LLM-callable tools so the agent can use them in its own reasoning,
not just user-typed.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from openprogram.functions._runtime import function


_LIST_DESCRIPTION = """List available prompt templates from configured MCP servers.

Each prompt has a `name`, optional `description`, and an `arguments` schema describing parameters. Fetch a rendered prompt via `get_mcp_prompt(server=..., name=..., arguments={...})`.

Parameters:
- server (optional): a specific MCP server name. If omitted, queries every loaded server.

Servers without prompt support return nothing — they're skipped silently."""


@function(
    name="list_mcp_prompts",
    description=_LIST_DESCRIPTION,
    toolset=["core"],
)
async def list_mcp_prompts(server: Optional[str] = None) -> str:
    """Enumerate MCP prompt templates."""
    from openprogram.mcp.registry import get_client, list_clients

    if server:
        client = get_client(server)
        if client is None:
            return f"Error: no MCP server named {server!r}"
        if not client.is_ready:
            return (f"Error: MCP server {server!r} not ready "
                    f"({client.error or 'no session'})")
        items = await client.list_prompts()
        out = [{**p, "server": server} for p in items]
    else:
        out = []
        for client in list_clients():
            if not client.is_ready:
                continue
            try:
                items = await client.list_prompts()
            except Exception as e:  # noqa: BLE001
                out.append({"server": client.config.name,
                            "_error": f"{type(e).__name__}: {e}"})
                continue
            out.extend({**p, "server": client.config.name} for p in items)
    return json.dumps(out, ensure_ascii=False, indent=2)


_GET_DESCRIPTION = """Render a specific MCP prompt template with arguments and return the resulting messages.

Parameters:
- server (required): name of the MCP server
- name (required): prompt template name from `list_mcp_prompts`
- arguments (optional): dict of parameter values for the template

Returns the rendered prompt as a JSON list of message objects."""


@function(
    name="get_mcp_prompt",
    description=_GET_DESCRIPTION,
    toolset=["core"],
)
async def get_mcp_prompt(server: str, name: str,
                         arguments: Optional[dict[str, Any]] = None) -> str:
    """Render an MCP prompt template."""
    from openprogram.mcp.registry import get_client

    client = get_client(server)
    if client is None:
        return f"Error: no MCP server named {server!r}"
    if not client.is_ready:
        return (f"Error: MCP server {server!r} not ready "
                f"({client.error or 'no session'})")
    try:
        rendered = await client.get_prompt(name, arguments)
    except Exception as e:  # noqa: BLE001
        return f"Error getting prompt {name!r}: {type(e).__name__}: {e}"
    return json.dumps(rendered, ensure_ascii=False, indent=2)
