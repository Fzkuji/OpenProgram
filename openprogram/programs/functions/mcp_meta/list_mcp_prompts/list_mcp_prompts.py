"""list_mcp_prompts — enumerate MCP prompt templates.

Prompts are parameterised text templates the server returns when
asked. claude-code surfaces them as slash-commands; we expose them as
LLM-callable tools so the agent can use them in its own reasoning,
not just user-typed.
"""
from __future__ import annotations

from typing import Optional

from openprogram.programs._runtime import function
from openprogram.programs.functions.mcp_meta.shared import list_across_servers


_DESCRIPTION = """List available prompt templates from configured MCP servers.

Each prompt has a `name`, optional `description`, and an `arguments` schema describing parameters. Fetch a rendered prompt via `get_mcp_prompt(server=..., name=..., arguments={...})`.

Parameters:
- server (optional): a specific MCP server name. If omitted, queries every loaded server.

Servers without prompt support return nothing — they're skipped silently."""


@function(
    name="list_mcp_prompts",
    description=_DESCRIPTION,
    toolset=["core"],
)
async def list_mcp_prompts(server: Optional[str] = None) -> str:
    """Enumerate MCP prompt templates."""
    return await list_across_servers(server, "list_prompts")
