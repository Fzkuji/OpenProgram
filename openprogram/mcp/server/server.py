"""Authenticated local MCP stdio server."""

from __future__ import annotations

import asyncio
import sys
import threading

import anyio
import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

from openprogram.agent.authority import mcp_client_authority
from openprogram.mcp.server.auth import MCPTokenError, authenticate_from_environment
from openprogram.mcp.server.contracts import get_mcp_tools, validate_tool_call
from openprogram.mcp.server.service import MCPClientContext, MCPService
from openprogram.mcp.server.tools import to_mcp_content


_PROGRESS_DRAIN_TIMEOUT_S = 0.1


def _wire_result(result) -> mcp_types.ServerResult:
    try:
        content = to_mcp_content(result)
        converted = mcp_types.CallToolResult(content=content, isError=result.is_error)
    except Exception:
        converted = mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(type="text", text="MCP tool execution failed")
            ],
            isError=True,
        )
    return mcp_types.ServerResult(converted)


def _application_error() -> mcp_types.ServerResult:
    return mcp_types.ServerResult(
        mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(type="text", text="MCP tool execution failed")
            ],
            isError=True,
        )
    )


def build_server(context: MCPClientContext) -> Server:
    server = Server("openprogram", version="1")
    service = MCPService(context)
    setattr(server, "_openprogram_service", service)

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return list(get_mcp_tools())

    async def call_tool(request: mcp_types.CallToolRequest) -> mcp_types.ServerResult:
        name = request.params.name
        arguments = validate_tool_call(name, request.params.arguments)
        request_context = server.request_context
        request_id = str(request_context.request_id)
        cancel_event = asyncio.Event()
        progress = None
        progress_task = None
        progress_queue = None
        meta = request_context.meta
        progress_token = getattr(meta, "progressToken", None) if meta else None
        if progress_token is not None:
            counter = 0
            counter_lock = threading.Lock()
            loop = asyncio.get_running_loop()
            progress_queue = asyncio.Queue()

            async def consume_progress() -> None:
                while True:
                    item = await progress_queue.get()
                    if item is None:
                        return
                    value, update = item
                    try:
                        await request_context.session.send_progress_notification(
                            progress_token,
                            value,
                            message=update,
                            related_request_id=request_id,
                        )
                    except Exception:
                        continue

            progress_task = asyncio.create_task(consume_progress())

            def progress(update: str) -> None:
                nonlocal counter
                with counter_lock:
                    counter += 1
                    value = float(counter)
                loop.call_soon_threadsafe(progress_queue.put_nowait, (value, update))

        failed = False
        cancelled = False
        try:
            if name == "sessions_list":
                result = service.sessions_list()
            elif name == "session_get":
                result = service.session_get(arguments["session_id"])
            elif name == "prompt_send":
                result = await service.prompt_send(
                    arguments["prompt"],
                    session_id=arguments.get("session_id"),
                    request_id=request_id,
                )
            elif name == "prompt_cancel":
                result = await service.prompt_cancel(arguments["session_id"])
            elif name == "tools_list":
                result = service.tools_list()
            elif name == "web_use":
                result = await service.web_use_call(
                    arguments,
                    call_id=request_id,
                    cancel_event=cancel_event,
                )
            else:
                result = await service.tool_call(
                    arguments["name"],
                    arguments.get("arguments", {}),
                    call_id=request_id,
                    cancel_event=cancel_event,
                    on_progress=progress,
                )
        except asyncio.CancelledError:
            cancelled = True
            cancel_event.set()
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            raise McpError(
                mcp_types.ErrorData(code=0, message="Request cancelled", data=None)
            ) from None
        except McpError:
            raise
        except Exception:
            failed = True
        finally:
            if progress_task is not None:
                if cancelled:
                    progress_task.cancel()
                try:
                    if cancelled:
                        await progress_task
                    else:
                        await asyncio.sleep(0)
                        progress_queue.put_nowait(None)
                        await asyncio.wait_for(
                            asyncio.shield(progress_task),
                            timeout=_PROGRESS_DRAIN_TIMEOUT_S,
                        )
                except TimeoutError:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        task = asyncio.current_task()
                        if task is not None and task.cancelling():
                            raise
                except asyncio.CancelledError:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except asyncio.CancelledError:
                        pass
                    raise
        if failed:
            return _application_error()
        return _wire_result(result)

    server.request_handlers[mcp_types.CallToolRequest] = call_tool
    return server


async def serve_stdio(context: MCPClientContext) -> None:
    server = build_server(context)
    service: MCPService = getattr(server, "_openprogram_service")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await service.aclose()


def serve() -> int:
    try:
        client_id = authenticate_from_environment()
        context = MCPClientContext(client_id, mcp_client_authority(client_id))
        anyio.run(serve_stdio, context)
    except MCPTokenError:
        print("Error: MCP server authentication failed", file=sys.stderr)
        return 1
    except Exception:
        print("Error: MCP server setup failed", file=sys.stderr)
        return 1
    return 0


__all__ = ["build_server", "serve_stdio", "serve"]
