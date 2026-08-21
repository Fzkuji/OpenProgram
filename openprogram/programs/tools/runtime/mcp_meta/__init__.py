"""Meta tools that expose the rest of the MCP protocol — resources +
prompts — to the LLM. The MCP protocol's other two primitives besides
``tools``:

  * Resources — content the server exposes as readable items (think
    "files" or "API responses"), addressed by URI. Discovered via
    ``resources/list``, fetched via ``resources/read``.
  * Prompts — parameterised text templates the server hands back when
    asked via ``prompts/get``. Useful as canned LLM tasks.

Mirrors claude-code's :file:`src/tools/{ListMcpResourcesTool,
ReadMcpResourceTool}` and its prompts-as-slash-commands surface, but
keeps everything as four straightforward LLM-callable tools — one per
subdirectory, sharing client plumbing in ``shared.py``.
"""
from .list_mcp_prompts import list_mcp_prompts
from .get_mcp_prompt import get_mcp_prompt
from .list_mcp_resources import list_mcp_resources
from .read_mcp_resource import read_mcp_resource

__all__ = [
    "list_mcp_prompts",
    "get_mcp_prompt",
    "list_mcp_resources",
    "read_mcp_resource",
]
