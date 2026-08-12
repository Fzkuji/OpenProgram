from __future__ import annotations

import asyncio
import os
import sys
import threading

import anyio
import mcp.types as mcp_types
import pytest
from mcp import ClientSession

from openprogram.agent.authority import mcp_client_authority
from openprogram.mcp_server.contracts import get_mcp_tools
from openprogram.mcp_server.service import MCPClientContext


def test_build_server_registers_exact_tools_and_explicit_call_handler():
    from openprogram.mcp_server.server import build_server

    context = MCPClientContext(
        "0123456789abcdef", mcp_client_authority("0123456789abcdef")
    )
    server = build_server(context)

    async def listed():
        request = mcp_types.ListToolsRequest(method="tools/list")
        return await server.request_handlers[mcp_types.ListToolsRequest](request)

    result = asyncio.run(listed()).root
    assert result.tools == list(get_mcp_tools())
    assert mcp_types.CallToolRequest in server.request_handlers


def test_authenticated_stdio_subprocess_initializes_and_lists_frozen_tools(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    state = tmp_path / ".openprogram"
    state.mkdir(mode=0o700)
    token = "subprocess-secret-token"
    token_file = state / "mcp_server_token"
    token_file.write_text(token, encoding="ascii")
    token_file.chmod(0o600)
    environment = dict(os.environ)
    environment.update({"HOME": str(tmp_path), "OPENPROGRAM_MCP_TOKEN": token})

    async def scenario():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "openprogram.cli", "mcp", "serve"],
            env=environment,
            cwd=os.getcwd(),
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
        return initialized, listed

    initialized, listed = asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    assert initialized.protocolVersion == "2025-11-25"
    assert listed.tools == list(get_mcp_tools())


def test_explicit_handler_routes_sdk_request_id_and_ordered_progress():
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp_server.server import build_server
    from openprogram.providers.types import TextContent

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    captured = []

    async def tool_call(name, arguments, **kwargs):
        captured.append((name, arguments, kwargs["call_id"]))
        producer = threading.Thread(
            target=lambda: [kwargs["on_progress"](item) for item in ("one", "two")]
        )
        producer.start()
        producer.join()
        return AgentToolResult(content=[TextContent(text="done")])

    service.tool_call = tool_call

    class Session:
        def __init__(self):
            self.progress = []

        async def send_progress_notification(self, token, value, **kwargs):
            self.progress.append(
                (token, value, kwargs["message"], kwargs["related_request_id"])
            )

    session = Session()
    context = RequestContext(
        request_id=73,
        meta=mcp_types.RequestParams.Meta(progressToken="pt"),
        session=session,
        lifespan_context=None,
    )
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params={"name": "tool_call", "arguments": {"name": "demo", "arguments": {}}},
    )

    async def scenario():
        token = request_ctx.set(context)
        try:
            return await server.request_handlers[mcp_types.CallToolRequest](request)
        finally:
            request_ctx.reset(token)

    result = asyncio.run(scenario()).root
    assert result.isError is False
    assert captured == [("demo", {}, "73")]
    assert session.progress == [("pt", 1.0, "one", "73"), ("pt", 2.0, "two", "73")]
    service.close()


def test_sdk_cancellation_reaches_prompt_handler_without_application_result():
    from openprogram.mcp_server.server import build_server

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def prompt_send(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    service.prompt_send = prompt_send

    async def scenario():
        client_to_server_send, server_read = anyio.create_memory_object_stream(0)
        server_write, client_from_server_recv = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(
                server.run,
                server_read,
                server_write,
                server.create_initialization_options(),
            )
            async with ClientSession(
                client_from_server_recv, client_to_server_send
            ) as session:
                await session.initialize()
                call = asyncio.create_task(
                    session.call_tool("prompt_send", {"prompt": "cancel me"})
                )
                await asyncio.wait_for(entered.wait(), 1)
                await session.send_notification(
                    mcp_types.CancelledNotification(
                        params=mcp_types.CancelledNotificationParams(requestId=1)
                    )
                )
                with pytest.raises(Exception) as caught:
                    await call
                await asyncio.wait_for(cancelled.wait(), 1)
                assert "cancel" in str(caught.value).lower()
            await client_to_server_send.aclose()
            group.cancel_scope.cancel()

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    finally:
        service.close()
