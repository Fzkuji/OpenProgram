"""list_mcp_resources — enumerate MCP resources across servers.

Resources are the protocol's read-only addressable items (files, API
results, snapshots). Servers list them by URI; the LLM picks one and
calls ``read_mcp_resource`` to pull contents.

Prompt text mirrors claude-code's ListMcpResourcesTool, lightly
adapted.
"""
from __future__ import annotations

from typing import Optional

from openprogram.functions._runtime import function
from openprogram.functions.tools.mcp_meta.shared import list_across_servers


_DESCRIPTION = """List available resources from configured MCP servers.

Each returned resource is the standard MCP resource shape plus a `server` field naming which server it came from. Resources are read-only addressable items (think "files" the MCP server exposes) — fetch one via `read_mcp_resource(server=..., uri=...)`.

Parameters:
- server (optional): name of a specific MCP server. If omitted, queries every loaded server and returns the union.

Servers that don't support resources return nothing — they're skipped silently."""


@function(
    name="list_mcp_resources",
    description=_DESCRIPTION,
    toolset=["core"],
)
async def list_mcp_resources(server: Optional[str] = None) -> str:
    """Enumerate resources across loaded MCP servers."""
    return await list_across_servers(server, "list_resources")
