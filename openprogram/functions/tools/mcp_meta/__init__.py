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
keeps everything as four straightforward LLM-callable tools.
"""
from . import resources as _resources_self_register  # noqa: F401
from . import prompts as _prompts_self_register  # noqa: F401
