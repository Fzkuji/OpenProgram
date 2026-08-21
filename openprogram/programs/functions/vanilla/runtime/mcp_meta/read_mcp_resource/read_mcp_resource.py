"""read_mcp_resource — fetch one MCP resource by URI.

Prompt text mirrors claude-code's ReadMcpResourceTool, lightly adapted
(we don't have the `myserver` shorthand — server name is always
required for read).
"""
from __future__ import annotations

import json

from openprogram.programs._runtime import function
from openprogram.programs.functions.vanilla.runtime.mcp_meta.shared import ready_client


_DESCRIPTION = """Read a specific resource from an MCP server, returning its contents.

Parameters:
- server (required): name of the MCP server to read from
- uri (required): the resource URI shown by `list_mcp_resources`

Returns the resource's content blocks (text or base64-encoded blob) as JSON."""


@function(
    name="read_mcp_resource",
    description=_DESCRIPTION,
    toolset=["core"],
)
async def read_mcp_resource(server: str, uri: str) -> str:
    """Fetch one MCP resource by URI."""
    client, err = ready_client(server)
    if err:
        return err
    try:
        contents = await client.read_resource(uri)
    except Exception as e:  # noqa: BLE001
        return f"Error reading {uri!r}: {type(e).__name__}: {e}"
    return json.dumps(contents, ensure_ascii=False, indent=2)
