"""get_mcp_prompt — render one MCP prompt template with arguments."""
from __future__ import annotations

import json
from typing import Any, Optional

from openprogram.programs._runtime import function
from openprogram.programs.functions.mcp_meta.shared import ready_client


_DESCRIPTION = """Render a specific MCP prompt template with arguments and return the resulting messages.

Parameters:
- server (required): name of the MCP server
- name (required): prompt template name from `list_mcp_prompts`
- arguments (optional): dict of parameter values for the template

Returns the rendered prompt as a JSON list of message objects."""


@function(
    name="get_mcp_prompt",
    description=_DESCRIPTION,
    toolset=["core"],
)
async def get_mcp_prompt(server: str, name: str,
                         arguments: Optional[dict[str, Any]] = None) -> str:
    """Render an MCP prompt template."""
    client, err = ready_client(server)
    if err:
        return err
    try:
        rendered = await client.get_prompt(name, arguments)
    except Exception as e:  # noqa: BLE001
        return f"Error getting prompt {name!r}: {type(e).__name__}: {e}"
    return json.dumps(rendered, ensure_ascii=False, indent=2)
