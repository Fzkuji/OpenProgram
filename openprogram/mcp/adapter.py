"""Translate between MCP wire types and our AgentTool framework types.

Pure helpers — no global state, no I/O beyond what's already opened
by the caller. Three concerns live here:

  * **Tool name mangling** — :func:`namespace_tool_name` prefixes a
    remote tool with its server name so cross-server collisions
    don't clobber the registry.
  * **Input schema normalisation** — :func:`normalize_input_schema`
    coerces the JSON Schema MCP gives us into the shape our
    framework expects on ``AgentTool.parameters``.
  * **Result conversion** — :func:`convert_call_result` maps an
    MCP :class:`~mcp.types.CallToolResult` (text / image / resource
    blocks) into our :class:`~openprogram.agent.types.AgentToolResult`.

Why split from :mod:`.registry`: lifecycle (load / shutdown /
``_main_loop`` capture) is the manager's job; format translation is
stateless. Two files each focused on one concern keeps either side
short and testable without dragging the other in.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent as MCPImageContent,
    TextContent as MCPTextContent,
    Tool,
)

from openprogram.agent.types import AgentToolResult
from openprogram.providers.types import ImageContent, TextContent

from openprogram.functions._runtime import _build_and_register_tool

from .client import MCPClient


def register_remote_tool(client: MCPClient, tool: Tool) -> Optional[str]:
    """Wrap one MCP tool as an AgentTool + register globally.

    Returns the registered AgentTool name, or ``None`` if the tool's
    input schema couldn't be coerced into a usable parameters dict.
    """
    namespaced = namespace_tool_name(client.config.name, tool.name)
    description = (tool.description or tool.title or namespaced).strip()

    parameters = normalize_input_schema(tool.inputSchema)
    if parameters is None:
        print(f"[mcp] tool '{namespaced}' has unusable inputSchema "
              f"— skipping", file=sys.stderr)
        return None

    # Capture client + raw tool name in the closure. The wrapper
    # adheres to the AgentTool execute contract: (call_id, args,
    # cancel_event, on_update_cb) -> AgentToolResult.
    async def _execute(call_id: str,
                        args: dict[str, Any],
                        cancel_event,
                        on_update_cb) -> AgentToolResult:
        import asyncio
        try:
            result = await client.call_tool(tool.name, args)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return AgentToolResult(
                content=[TextContent(text=(
                    f"[mcp error] {client.config.name}__{tool.name}: "
                    f"{type(e).__name__}: {e}"
                ))],
                details={"is_error": True,
                         "mcp_server": client.config.name},
            )
        return convert_call_result(result, server=client.config.name,
                                    tool_name=tool.name)

    _build_and_register_tool(
        name=namespaced,
        description=description,
        parameters=parameters,
        label=namespaced,
        execute=_execute,
        toolsets=["mcp", f"mcp:{client.config.name}"],
    )
    return namespaced


def namespace_tool_name(server: str, tool: str) -> str:
    """Compose ``{server}__{tool}`` with simple sanitisation.

    Replaces anything outside ``[A-Za-z0-9_-]`` with ``_`` so the
    final name passes OpenAI's and Anthropic's tool-name regex.
    Truncates to 64 chars (OpenAI's limit) keeping the tool suffix
    intact — collisions there are the user's problem.
    """
    def _clean(s: str) -> str:
        return "".join(c if c.isalnum() or c in "_-" else "_" for c in s)
    name = f"{_clean(server)}__{_clean(tool)}"
    if len(name) <= 64:
        return name
    keep_tail = 64 - len(_clean(server)) - 2  # room for "{server}__"
    if keep_tail < 8:
        # Pathological server name length — just trim end.
        return name[:64]
    return f"{_clean(server)}__{_clean(tool)[-keep_tail:]}"


def normalize_input_schema(schema: Optional[dict]) -> Optional[dict]:
    """Coerce an MCP tool's ``inputSchema`` into the AgentTool
    parameters format.

    MCP tools provide a JSON-schema object directly. We require it to
    be either an object schema (``type: "object"``) or an empty
    no-args descriptor. Anything else (mainly bad servers) is
    rejected — returning ``None`` makes :func:`register_remote_tool`
    skip the tool with a warning.
    """
    if schema is None:
        return {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        return None
    if schema.get("type", "object") != "object":
        return None
    # Force a clean copy so later mutation can't leak across tools.
    out: dict[str, Any] = {"type": "object"}
    props = schema.get("properties")
    if isinstance(props, dict):
        out["properties"] = props
    else:
        out["properties"] = {}
    required = schema.get("required")
    if isinstance(required, list):
        out["required"] = [str(x) for x in required]
    return out


def convert_call_result(result: CallToolResult,
                         *, server: str,
                         tool_name: str) -> AgentToolResult:
    """Translate MCP content blocks → AgentToolResult content.

    MCP supports ``text`` / ``image`` / ``resource`` content blocks
    plus an ``isError`` flag. Map text and image straight through;
    summarise resources as text (we don't ship the raw resource
    bytes through the chat history).
    """
    out_content: list[Any] = []
    for block in result.content:
        if isinstance(block, MCPTextContent):
            out_content.append(TextContent(text=block.text))
        elif isinstance(block, MCPImageContent):
            out_content.append(ImageContent(
                data=block.data,
                media_type=block.mimeType or "image/png",
            ))
        elif isinstance(block, EmbeddedResource):
            ref = getattr(getattr(block, "resource", None), "uri", "?")
            out_content.append(TextContent(
                text=f"[resource: {ref}]"
            ))
        else:
            # Forward-compatible: any new MCP content type renders as
            # its repr so the LLM at least sees that something was
            # returned. Better than silently dropping it.
            out_content.append(TextContent(text=repr(block)))

    if not out_content:
        out_content.append(TextContent(text=""))

    details: dict[str, Any] = {"mcp_server": server, "mcp_tool": tool_name}
    if result.isError:
        details["is_error"] = True
    return AgentToolResult(content=out_content, details=details)
