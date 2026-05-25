"""list_mcp_resources / read_mcp_resource — MCP "resources" surface.

Resources are the protocol's read-only addressable items (files, API
results, snapshots). Servers list them by URI; the LLM picks one and
calls ``read_mcp_resource`` to pull contents.

Prompts mirror claude-code's ListMcpResourcesTool / ReadMcpResourceTool
texts, lightly adapted (we don't have the `myserver` shorthand —
server name is always required for read).
"""
from __future__ import annotations

import json
from typing import Optional

from openprogram.functions._runtime import function


_LIST_DESCRIPTION = """List available resources from configured MCP servers.

Each returned resource is the standard MCP resource shape plus a `server` field naming which server it came from. Resources are read-only addressable items (think "files" the MCP server exposes) — fetch one via `read_mcp_resource(server=..., uri=...)`.

Parameters:
- server (optional): name of a specific MCP server. If omitted, queries every loaded server and returns the union.

Servers that don't support resources return nothing — they're skipped silently."""


@function(
    name="list_mcp_resources",
    description=_LIST_DESCRIPTION,
    toolset=["core"],
)
async def list_mcp_resources(server: Optional[str] = None) -> str:
    """Enumerate resources across loaded MCP servers."""
    from openprogram.mcp.registry import get_client, list_clients

    if server:
        client = get_client(server)
        if client is None:
            return f"Error: no MCP server named {server!r}"
        if not client.is_ready:
            return (f"Error: MCP server {server!r} not ready "
                    f"({client.error or 'no session'})")
        items = await client.list_resources()
        out = [{**r, "server": server} for r in items]
    else:
        out = []
        for client in list_clients():
            if not client.is_ready:
                continue
            try:
                items = await client.list_resources()
            except Exception as e:  # noqa: BLE001
                out.append({"server": client.config.name,
                            "_error": f"{type(e).__name__}: {e}"})
                continue
            out.extend({**r, "server": client.config.name} for r in items)
    return json.dumps(out, ensure_ascii=False, indent=2)


_READ_DESCRIPTION = """Read a specific resource from an MCP server, returning its contents.

Parameters:
- server (required): name of the MCP server to read from
- uri (required): the resource URI shown by `list_mcp_resources`

Returns the resource's content blocks (text or base64-encoded blob) as JSON."""


@function(
    name="read_mcp_resource",
    description=_READ_DESCRIPTION,
    toolset=["core"],
)
async def read_mcp_resource(server: str, uri: str) -> str:
    """Fetch one MCP resource by URI."""
    from openprogram.mcp.registry import get_client

    client = get_client(server)
    if client is None:
        return f"Error: no MCP server named {server!r}"
    if not client.is_ready:
        return (f"Error: MCP server {server!r} not ready "
                f"({client.error or 'no session'})")
    try:
        contents = await client.read_resource(uri)
    except Exception as e:  # noqa: BLE001
        return f"Error reading {uri!r}: {type(e).__name__}: {e}"
    return json.dumps(contents, ensure_ascii=False, indent=2)
