from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import anyio
import mcp.types as mcp_types
import pytest
from mcp import ClientSession

from openprogram.agent.authority import mcp_client_authority
from openprogram.mcp_server.contracts import get_mcp_tools
from openprogram.mcp_server.service import MCPClientContext
from openprogram.mcp_server.tools import json_result


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


@pytest.mark.parametrize("client_name,client_version", [("alpha", "1"), ("beta", "9")])
def test_authenticated_stdio_subprocess_initializes_and_lists_frozen_tools(
    tmp_path, client_name, client_version
):
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
            async with ClientSession(
                read_stream,
                write_stream,
                client_info=mcp_types.Implementation(
                    name=client_name, version=client_version
                ),
            ) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
        return initialized, listed

    initialized, listed = asyncio.run(asyncio.wait_for(scenario(), timeout=10))
    assert initialized.protocolVersion == "2025-11-25"
    assert listed.tools == list(get_mcp_tools())


@pytest.mark.parametrize(
    "case", ["missing_file", "wrong_mode", "missing_env", "mismatch"]
)
def test_real_cli_auth_failure_matrix_never_enters_stdio(tmp_path, case):
    state = tmp_path / ".openprogram"
    state.mkdir(mode=0o700)
    token_file = state / "mcp_server_token"
    if case != "missing_file":
        token_file.write_text("stored-secret", encoding="ascii")
        token_file.chmod(0o644 if case == "wrong_mode" else 0o600)
    environment = dict(os.environ)
    environment["HOME"] = str(tmp_path)
    if case != "missing_env":
        environment["OPENPROGRAM_MCP_TOKEN"] = (
            "wrong-secret" if case == "mismatch" else "stored-secret"
        )
    else:
        environment.pop("OPENPROGRAM_MCP_TOKEN", None)

    completed = subprocess.run(
        [sys.executable, "-m", "openprogram.cli", "mcp", "serve"],
        input="stdin-must-not-be-read\n",
        text=True,
        capture_output=True,
        cwd=os.getcwd(),
        env=environment,
        timeout=5,
    )
    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == "Error: MCP server authentication failed\n"
    assert "secret" not in completed.stderr


def test_successful_stdio_stdout_contains_only_jsonrpc_frames(tmp_path):
    import json

    state = tmp_path / ".openprogram"
    state.mkdir(mode=0o700)
    token = "stdout-secret-token"
    token_file = state / "mcp_server_token"
    token_file.write_text(token, encoding="ascii")
    token_file.chmod(0o600)
    environment = dict(os.environ)
    environment.update({"HOME": str(tmp_path), "OPENPROGRAM_MCP_TOKEN": token})
    requests = "\n".join(
        json.dumps(frame, separators=(",", ":"))
        for frame in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "secret-client-info", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    )
    completed = subprocess.run(
        [sys.executable, "-m", "openprogram.cli", "mcp", "serve"],
        input=requests + "\n",
        text=True,
        capture_output=True,
        cwd=os.getcwd(),
        env=environment,
        timeout=10,
    )
    frames = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 0
    assert [frame["id"] for frame in frames] == [1, 2]
    assert frames[1]["result"]["tools"] == [
        tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        for tool in get_mcp_tools()
    ]
    combined = completed.stdout + completed.stderr
    assert token not in combined
    assert "secret-client-info" not in combined
    assert "Traceback" not in combined


@pytest.mark.parametrize("with_progress", [False, True])
def test_explicit_handler_routes_sdk_request_id_and_ordered_progress(with_progress):
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
        captured.append(
            (name, arguments, kwargs["call_id"], kwargs["on_progress"] is not None)
        )
        if kwargs["on_progress"] is not None:
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
        meta=(
            mcp_types.RequestParams.Meta(progressToken="pt") if with_progress else None
        ),
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
    assert captured == [("demo", {}, "73", with_progress)]
    assert session.progress == (
        [("pt", 1.0, "one", "73"), ("pt", 2.0, "two", "73")] if with_progress else []
    )
    service.close()


def test_real_sdk_in_memory_maps_all_six_wrappers_and_method_errors():
    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp_server.server import build_server
    from openprogram.providers.types import TextContent

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    request_ids = []
    service.sessions_list = lambda: json_result([{"id": "s"}])
    service.session_get = lambda session_id: json_result({"session_id": session_id})

    async def prompt_send(prompt, **kwargs):
        request_ids.append(kwargs["request_id"])
        return json_result({"prompt": prompt, "session_id": kwargs["session_id"]})

    service.prompt_send = prompt_send
    service.prompt_cancel = lambda session_id: json_result(
        {"session_id": session_id, "cancelled": False}
    )
    service.tools_list = lambda: json_result([])

    async def tool_call(name, arguments, **kwargs):
        request_ids.append(kwargs["call_id"])
        return AgentToolResult(content=[TextContent(text=f"{name}:{arguments['x']}")])

    service.tool_call = tool_call

    async def scenario():
        client_send, server_read = anyio.create_memory_object_stream(0)
        server_write, client_read = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(
                server.run,
                server_read,
                server_write,
                server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_send) as session:
                await session.initialize()
                results = [
                    await session.call_tool("sessions_list", {}),
                    await session.call_tool("session_get", {"session_id": "s"}),
                    await session.call_tool(
                        "prompt_send", {"prompt": "p", "session_id": "s"}
                    ),
                    await session.call_tool("prompt_cancel", {"session_id": "s"}),
                    await session.call_tool("tools_list", {}),
                    await session.call_tool(
                        "tool_call", {"name": "demo", "arguments": {"x": 7}}
                    ),
                ]
                with pytest.raises(Exception) as unknown:
                    await session.call_tool("unknown", {})
                with pytest.raises(Exception) as invalid:
                    await session.call_tool("session_get", {})
            await client_send.aclose()
            group.cancel_scope.cancel()
        return results, unknown.value, invalid.value

    try:
        results, unknown, invalid = asyncio.run(asyncio.wait_for(scenario(), 5))
    finally:
        service.close()
    assert [item.isError for item in results] == [False] * 6
    assert [item.content[0].text for item in results] == [
        '[{"id":"s"}]',
        '{"session_id":"s"}',
        '{"prompt":"p","session_id":"s"}',
        '{"cancelled":false,"session_id":"s"}',
        "[]",
        "demo:7",
    ]
    assert request_ids == ["4", "7"]
    assert "unknown MCP wrapper tool" in str(unknown)
    assert "invalid arguments" in str(invalid)


def test_sdk_cancellation_reaches_prompt_handler_without_application_result():
    from openprogram.agent.run_control import current_token
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.events import create_event_bus, make_event
    from openprogram.mcp_server.server import build_server

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    entered = threading.Event()
    release = threading.Event()
    audit = []

    class DB:
        def __init__(self):
            self.rows = {}

        def get_session(self, session_id):
            return self.rows.get(session_id)

        def create_session(self, session_id, agent_id, **_kwargs):
            self.rows[session_id] = {"id": session_id, "agent_id": agent_id}

    class Questions:
        def __init__(self):
            self.cancelled = []
            self.resolved = []

        def cancel_session(self, session_id):
            self.cancelled.append(session_id)

        def resolve(self, question_id, outcome, value=None):
            self.resolved.append((question_id, outcome, value))
            return True

    questions = Questions()
    bus = create_event_bus()
    bus.subscribe(lambda event: audit.append(event), types={"mcp.request.cancelled"})

    def process(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    service._session_db = DB()
    service._process_user_turn = process
    service._question_registry_getter = lambda: questions
    service._unsubscribe_questions()
    service._event_bus = bus
    service._unsubscribe_questions = bus.subscribe(
        service._on_question_asked, types={"question.asked"}
    )

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
                await asyncio.to_thread(entered.wait, 1)
                record = tuple(service._active_by_request.values())[0]
                session_id = record.session_id
                bus.emit(
                    make_event(
                        "question.asked",
                        "agent",
                        {"id": "general-question", "session_id": session_id},
                    )
                )
                assert questions.resolved == [("general-question", "declined", None)]
                await session.send_notification(
                    mcp_types.CancelledNotification(
                        params=mcp_types.CancelledNotificationParams(requestId=1)
                    )
                )
                with pytest.raises(Exception) as caught:
                    await call
                assert "cancel" in str(caught.value).lower()
                assert service._active_by_request == {}
                assert current_token(session_id) is None
                assert questions.cancelled == [session_id]
                assert len(audit) == 1
            await client_to_server_send.aclose()
            group.cancel_scope.cancel()

    try:
        asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    finally:
        release.set()
        service.close()


@pytest.mark.parametrize("with_progress", [False, True])
def test_tool_call_wire_cancellation_sets_exact_event_and_stops_progress(
    with_progress,
):
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from openprogram.mcp_server.server import build_server

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    entered = asyncio.Event()
    captured = {}
    notification_entered = asyncio.Event()

    async def tool_call(*_args, **kwargs):
        captured["event"] = kwargs["cancel_event"]
        if kwargs["on_progress"] is not None:
            kwargs["on_progress"]("blocked")
        entered.set()
        await asyncio.Event().wait()

    service.tool_call = tool_call

    class Session:
        async def send_progress_notification(self, *_args, **_kwargs):
            notification_entered.set()
            await asyncio.Event().wait()

    context = RequestContext(
        request_id=91,
        meta=(
            mcp_types.RequestParams.Meta(progressToken="blocked")
            if with_progress
            else None
        ),
        session=Session(),
        lifespan_context=None,
    )
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params={"name": "tool_call", "arguments": {"name": "demo"}},
    )

    async def scenario():
        token = request_ctx.set(context)
        try:
            handler = asyncio.create_task(
                server.request_handlers[mcp_types.CallToolRequest](request)
            )
            await asyncio.wait_for(entered.wait(), 1)
            if with_progress:
                await asyncio.wait_for(notification_entered.wait(), 1)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(handler, 0.5)
            assert captured["event"].is_set()
            assert not any(
                "consume_progress" in repr(task.get_coro())
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            )
        finally:
            request_ctx.reset(token)

    try:
        asyncio.run(scenario())
    finally:
        service.close()


def test_protocol_tool_error_matrix_and_concurrent_progress_are_isolated():
    from mcp.shared.exceptions import McpError

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp_server.server import build_server
    from openprogram.providers.types import TextContent

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    started = []

    async def tool_call(name, arguments, **kwargs):
        if name in {"missing-a", "missing-b"}:
            raise McpError(
                mcp_types.ErrorData(
                    code=mcp_types.METHOD_NOT_FOUND,
                    message="underlying Runtime tool not found",
                )
            )
        if name in {"paired", "hard", "rule", "approval", "runtime"}:
            return AgentToolResult(
                content=[TextContent(text="fixed denial")], is_error=True
            )
        started.append((name, kwargs["call_id"]))
        kwargs["on_progress"](f"{name}-one")
        await asyncio.sleep(0)
        kwargs["on_progress"](f"{name}-two")
        return AgentToolResult(content=[TextContent(text=name)])

    service.tool_call = tool_call

    async def scenario():
        client_send, server_read = anyio.create_memory_object_stream(0)
        server_write, client_read = anyio.create_memory_object_stream(0)
        observed = {"left": [], "right": []}

        async def progress(label, value, total, message):
            observed[label].append((value, message))

        async with anyio.create_task_group() as group:
            group.start_soon(
                server.run,
                server_read,
                server_write,
                server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_send) as session:
                await session.initialize()
                typed = [
                    await session.call_tool(
                        "tool_call", {"name": name, "arguments": {}}
                    )
                    for name in ("paired", "hard", "rule", "approval", "runtime")
                ]
                errors = []
                for name in ("missing-a", "missing-b"):
                    with pytest.raises(McpError) as caught:
                        await session.call_tool(
                            "tool_call", {"name": name, "arguments": {}}
                        )
                    errors.append((caught.value.error.code, caught.value.error.message))
                left, right = await asyncio.gather(
                    session.call_tool(
                        "tool_call",
                        {"name": "left", "arguments": {}},
                        progress_callback=lambda v, t, m: progress("left", v, t, m),
                    ),
                    session.call_tool(
                        "tool_call",
                        {"name": "right", "arguments": {}},
                        progress_callback=lambda v, t, m: progress("right", v, t, m),
                    ),
                )
            await client_send.aclose()
            group.cancel_scope.cancel()
        return typed, errors, left, right, observed

    try:
        typed, errors, left, right, observed = asyncio.run(
            asyncio.wait_for(scenario(), 5)
        )
    finally:
        service.close()
    assert [result.isError for result in typed] == [True] * 5
    assert errors == [
        (mcp_types.METHOD_NOT_FOUND, "underlying Runtime tool not found"),
        (mcp_types.METHOD_NOT_FOUND, "underlying Runtime tool not found"),
    ]
    assert left.content[0].text == "left"
    assert right.content[0].text == "right"
    assert observed == {
        "left": [(1.0, "left-one"), (2.0, "left-two")],
        "right": [(1.0, "right-one"), (2.0, "right-two")],
    }
    assert len({call_id for _, call_id in started}) == 2


def test_protocol_prompt_cancel_is_same_connection_only_and_completed_is_false():
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.mcp_server.server import build_server

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    entered = threading.Event()
    release = threading.Event()

    class DB:
        def get_session(self, session_id):
            return {"id": session_id, "agent_id": "main"}

    def process(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return TurnResult("late", "u", "a")

    service._session_db = DB()
    service._process_user_turn = process

    async def scenario():
        client_send, server_read = anyio.create_memory_object_stream(0)
        server_write, client_read = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(
                server.run,
                server_read,
                server_write,
                server.create_initialization_options(),
            )
            async with ClientSession(client_read, client_send) as session:
                await session.initialize()
                prompt = asyncio.create_task(
                    session.call_tool(
                        "prompt_send", {"prompt": "p", "session_id": "shared"}
                    )
                )
                await asyncio.to_thread(entered.wait, 1)
                same = await session.call_tool(
                    "prompt_cancel", {"session_id": "shared"}
                )
                with pytest.raises(Exception):
                    await prompt
            await client_send.aclose()
            group.cancel_scope.cancel()
        return same

    try:
        same = asyncio.run(asyncio.wait_for(scenario(), 5))
        completed = service.prompt_cancel("shared")
    finally:
        release.set()
        service.close()
    assert same.content[0].text == '{"cancelled":true,"session_id":"shared"}'
    assert completed.content[0].text == ('{"cancelled":false,"session_id":"shared"}')


def test_foreign_sdk_connection_cannot_cancel_active_prompt():
    from openprogram.agent.dispatcher import TurnResult
    from openprogram.mcp_server.server import build_server

    contexts = (
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef")),
        MCPClientContext("fedcba9876543210", mcp_client_authority("fedcba9876543210")),
    )
    server_a, server_b = (build_server(context) for context in contexts)
    service_a = server_a._openprogram_service
    service_b = server_b._openprogram_service
    entered = threading.Event()
    release = threading.Event()

    class DB:
        def get_session(self, session_id):
            return {"id": session_id, "agent_id": "main"}

    def process(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return TurnResult("done", "u", "a")

    service_a._session_db = DB()
    service_a._process_user_turn = process
    service_b._session_db = DB()

    async def scenario():
        a_send, a_read_server = anyio.create_memory_object_stream(0)
        a_write_server, a_read = anyio.create_memory_object_stream(0)
        b_send, b_read_server = anyio.create_memory_object_stream(0)
        b_write_server, b_read = anyio.create_memory_object_stream(0)
        async with anyio.create_task_group() as group:
            group.start_soon(
                server_a.run,
                a_read_server,
                a_write_server,
                server_a.create_initialization_options(),
            )
            group.start_soon(
                server_b.run,
                b_read_server,
                b_write_server,
                server_b.create_initialization_options(),
            )
            async with (
                ClientSession(a_read, a_send) as client_a,
                ClientSession(b_read, b_send) as client_b,
            ):
                await client_a.initialize()
                await client_b.initialize()
                prompt = asyncio.create_task(
                    client_a.call_tool(
                        "prompt_send", {"prompt": "p", "session_id": "foreign"}
                    )
                )
                await asyncio.to_thread(entered.wait, 1)
                record = tuple(service_a._active_by_request.values())[0]
                foreign = await client_b.call_tool(
                    "prompt_cancel", {"session_id": "foreign"}
                )
                assert foreign.content[0].text == (
                    '{"cancelled":false,"session_id":"foreign"}'
                )
                assert not record.thread_cancel.is_set()
                release.set()
                result = await prompt
            await a_send.aclose()
            await b_send.aclose()
            group.cancel_scope.cancel()
        return result

    try:
        result = asyncio.run(asyncio.wait_for(scenario(), 5))
    finally:
        release.set()
        service_a.close()
        service_b.close()
    assert result.content[0].text.endswith('"text":"done"}')
