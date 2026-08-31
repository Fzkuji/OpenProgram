from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import anyio
import mcp.types as mcp_types
import pytest
from mcp import ClientSession

from openprogram.agent.authority import mcp_client_authority
from openprogram.mcp.server.contracts import get_mcp_tools
from openprogram.mcp.server.service import MCPClientContext
from openprogram.mcp.server.tools import json_result


def _stdio_subprocess_environment(tmp_path, *, client: str = "a"):
    state = tmp_path / ".openprogram"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = f"stdio-{client}-secret-token"
    token_file = state / "mcp_server_token"
    token_file.write_text(token, encoding="ascii")
    token_file.chmod(0o600)
    fixture_path = tmp_path / "subprocess_fixture"
    fixture_path.mkdir()
    (fixture_path / "sitecustomize.py").write_text(
        """
import json
import os
import threading
from pathlib import Path

from openprogram.agent.dispatcher import TurnResult
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.events import get_event_bus, make_event
from openprogram.programs._runtime import register
from openprogram.providers.types import TextContent
import openprogram.mcp.server.service as mcp_service_module

evidence = Path(os.environ["OPENPROGRAM_MCP_TEST_EVIDENCE"])

def record(kind, **values):
    with evidence.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, **values}, sort_keys=True) + "\\n")

def web_use_dispatch(arguments, *, owner_id):
    record("web_use", arguments=arguments, owner_id=owner_id)
    return {"ok": True, "owner_bound": owner_id.startswith("mcp:")}

mcp_service_module._default_web_use_dispatch = web_use_dispatch

async def execute(call_id, arguments, cancel_event, on_update):
    record("execute", call_id=call_id, arguments=arguments,
           progress=on_update is not None)
    if on_update is not None:
        on_update("first")
        on_update("second")
    if arguments.get("failure"):
        raise RuntimeError("runtime-secret")
    return AgentToolResult(content=[TextContent(text="runtime-ok")])

for name in ("memory_status", "memory_search", "read", "bash", "memory_update"):
    properties = {"failure": {"type": "boolean"}}
    if name == "bash":
        properties["command"] = {"type": "string"}
    if name == "memory_update":
        properties["revision"] = {"type": "string"}
    schema = {"type": "object", "properties": properties,
              "additionalProperties": False}
    register(AgentTool(name=name, description="Fixture " + name,
                       parameters=schema, label=name, execute=execute))

from openprogram.agent.session_config import PermissionRules
import openprogram.programs.permission_rule as permission_module
permission_module.load_merged_rules = lambda _session_id: PermissionRules(
    deny=["memory_update"]
)

from openprogram.agent import dispatcher
from types import SimpleNamespace
def process_user_turn(request, *, cancel_event):
    record("turn", session_id=request.session_id, prompt=request.user_text,
           speaker_id=request.speaker_id)
    questions.pending["fixture-question"] = request.user_msg_id + "_reply"
    get_event_bus().emit(make_event("question.asked", "agent", {
        "id": "fixture-question", "session_id": request.session_id,
        "execution_id": request.user_msg_id + "_reply",
    }))
    if request.user_text == "wait-for-cancel":
        record("entered", session_id=request.session_id)
        while not cancel_event.is_set():
            threading.Event().wait(0.01)
        record("late-worker-finished", session_id=request.session_id)
    return TurnResult("fixture-result", "fixture-user", "fixture-assistant")
dispatcher.process_user_turn = process_user_turn

class Questions:
    pending = {}

    def resolve(self, question_id, outcome, value=None):
        record("question", question_id=question_id, outcome=outcome, value=value)
        return True
    def list_pending(self, session_id):
        return [SimpleNamespace(id=qid, execution_id=execution_id)
                for qid, execution_id in self.pending.items()]
    def cancel_execution(self, session_id, execution_id):
        record("question_cancel", session_id=session_id, execution_id=execution_id)

questions = Questions()
import openprogram.agent.questions as question_module
question_module.get_question_registry = lambda: questions
def audit(event):
    from openprogram.agent.run_control import current_token
    record("audit", payload=event.payload,
           run_control_cleared=current_token(event.payload["session_id"]) is None)
get_event_bus().subscribe(audit, types={"mcp.request.cancelled"})

from openprogram.agent.session_db import default_db
db = default_db()
if db.get_session("fixture-session") is None:
    db.create_session("fixture-session", "main", title="Fixture", source="mcp")
    db.append_message("fixture-session", {
        "id": "seed-message", "role": "user", "content": "seed",
        "timestamp": 1.0, "predecessor": "",
    })
""",
        encoding="utf-8",
    )
    state.joinpath("config.json").write_text(
        json.dumps(
            {
                "mcp_server": {
                    "exposed_tools": [
                        "memory_status",
                        "memory_search",
                        "read",
                        "bash",
                        "memory_update",
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    state.joinpath("config.json").chmod(0o600)
    environment = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parents[3])
    python_path = os.pathsep.join((str(fixture_path), repo_root))
    if environment.get("PYTHONPATH"):
        python_path += os.pathsep + environment["PYTHONPATH"]
    environment.update(
        {
            "HOME": str(tmp_path),
            "OPENPROGRAM_MCP_TOKEN": token,
            "OPENPROGRAM_MCP_TEST_EVIDENCE": str(tmp_path / "evidence.jsonl"),
            "PYTHONPATH": python_path,
        }
    )
    return environment


@asynccontextmanager
async def _stdio_sdk_client(environment, *, client_name="acceptance"):
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openprogram.cli", "mcp", "serve"],
        env=environment,
        cwd=os.getcwd(),
    )
    async with stdio_client(parameters) as streams:
        async with ClientSession(
            *streams,
            client_info=mcp_types.Implementation(name=client_name, version="1"),
        ) as session:
            await session.initialize()
            await session.list_tools()
            yield session


@pytest.mark.parametrize("client_name", ["acceptance-a", "acceptance-b"])
def test_real_stdio_subprocess_calls_all_wrappers(tmp_path, client_name):
    environment = _stdio_subprocess_environment(tmp_path)

    async def scenario():
        async with _stdio_sdk_client(environment, client_name=client_name) as session:
            listed = await session.list_tools()
            sessions = await session.call_tool("sessions_list", {})
            session_get = await session.call_tool(
                "session_get", {"session_id": "fixture-session"}
            )
            prompt = await session.call_tool(
                "prompt_send",
                {"prompt": "fixture-prompt", "session_id": "fixture-session"},
            )
            completed_cancel = await session.call_tool(
                "prompt_cancel", {"session_id": "fixture-session"}
            )
            tools = await session.call_tool("tools_list", {})
            runtime = await session.call_tool(
                "tool_call", {"name": "memory_status", "arguments": {}}
            )
            computer = await session.call_tool(
                "web_use", {"command": "list_pages"}
            )
        return listed, sessions, session_get, prompt, completed_cancel, tools, runtime, computer

    results = asyncio.run(asyncio.wait_for(scenario(), 10))
    listed, sessions, session_get, prompt, completed_cancel, tools, runtime, computer = results
    assert listed.tools == list(get_mcp_tools())
    session_rows = json.loads(sessions.content[0].text)
    assert len(session_rows) == 1
    assert session_rows[0]["id"] == "fixture-session"
    assert session_rows[0]["title"] == "Fixture"
    assert isinstance(session_rows[0]["updated_at"], (int, float))
    assert session_get.content[0].text == (
        '[{"content":"seed","id":"seed-message","role":"user","timestamp":1.0}]'
    )
    assert prompt.content[0].text == (
        '{"assistant_msg_id":"fixture-assistant","failed":false,'
        '"session_id":"fixture-session","text":"fixture-result"}'
    )
    assert completed_cancel.content[0].text == (
        '{"cancelled":false,"session_id":"fixture-session"}'
    )
    assert tools.content[0].text.startswith('[{"description":')
    assert runtime.content[0].text == "runtime-ok"
    assert json.loads(computer.content[0].text) == {
        "ok": True, "owner_bound": True,
    }
    evidence = [
        json.loads(line)
        for line in (tmp_path / "evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["outcome"] for item in evidence if item["kind"] == "question"] == [
        "declined"
    ]
    client_id = hashlib.sha256(
        environment["OPENPROGRAM_MCP_TOKEN"].encode("ascii")
    ).hexdigest()[:16]
    assert [item["speaker_id"] for item in evidence if item["kind"] == "turn"] == [
        f"mcp/{client_id}"
    ]


def test_real_stdio_subprocess_error_progress_and_concurrency_contract(tmp_path):
    environment = _stdio_subprocess_environment(tmp_path)

    async def scenario():
        progress = {"left": [], "right": []}

        async def observe(label, value, _total, message):
            progress[label].append((value, message))

        async with _stdio_sdk_client(environment) as session:
            with pytest.raises(Exception) as unknown_wrapper:
                await session.call_tool("unknown", {})
            with pytest.raises(Exception) as invalid_wrapper:
                await session.call_tool("session_get", {})
            underlying = []
            for name in ("missing-a", "missing-b"):
                with pytest.raises(Exception) as caught:
                    await session.call_tool(
                        "tool_call", {"name": name, "arguments": {}}
                    )
                underlying.append(str(caught.value))
            typed = [
                await session.call_tool("tool_call", {"name": "read", "arguments": {}}),
                await session.call_tool(
                    "tool_call",
                    {"name": "bash", "arguments": {"command": "pwd"}},
                ),
                await session.call_tool(
                    "tool_call",
                    {"name": "memory_update", "arguments": {"revision": "r1"}},
                ),
                await session.call_tool(
                    "tool_call", {"name": "memory_search", "arguments": {}}
                ),
                await session.call_tool(
                    "tool_call",
                    {"name": "memory_status", "arguments": {"failure": True}},
                ),
            ]
            no_progress = await session.call_tool(
                "tool_call", {"name": "memory_status", "arguments": {}}
            )
            left, right = await asyncio.gather(
                session.call_tool(
                    "tool_call",
                    {"name": "memory_status", "arguments": {}},
                    progress_callback=lambda v, t, m: observe("left", v, t, m),
                ),
                session.call_tool(
                    "tool_call",
                    {"name": "memory_status", "arguments": {}},
                    progress_callback=lambda v, t, m: observe("right", v, t, m),
                ),
            )
        return (
            unknown_wrapper.value,
            invalid_wrapper.value,
            underlying,
            typed,
            no_progress,
            left,
            right,
            progress,
        )

    result = asyncio.run(asyncio.wait_for(scenario(), 10))
    (
        unknown_wrapper,
        invalid_wrapper,
        underlying,
        typed,
        no_progress,
        left,
        right,
        progress,
    ) = result
    assert "unknown MCP wrapper tool" in str(unknown_wrapper)
    assert "invalid arguments" in str(invalid_wrapper)
    assert underlying[0] == underlying[1]
    assert "underlying Runtime tool not found" in underlying[0]
    assert [item.isError for item in typed] == [True] * 5
    assert [item.content[0].text for item in typed] == [
        "[denied] authority tier does not allow fs.read",
        "[denied] hard constraint",
        "[denied] blocked by permission rule",
        "[denied] approval unavailable for non-interactive MCP",
        "Runtime tool execution failed",
    ]
    assert no_progress.content[0].text == "runtime-ok"
    assert left.content[0].text == right.content[0].text == "runtime-ok"
    assert progress == {
        "left": [(1.0, "first"), (2.0, "second")],
        "right": [(1.0, "first"), (2.0, "second")],
    }
    evidence = [
        json.loads(line)
        for line in (tmp_path / "evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    executions = [item for item in evidence if item["kind"] == "execute"]
    assert len(executions) == 4
    assert len({item["call_id"] for item in executions}) == 4
    assert [item["progress"] for item in executions] == [False, False, True, True]


def test_real_stdio_subprocess_prompt_cancel_cleanup_and_foreign_ownership(tmp_path):
    environment_a = _stdio_subprocess_environment(tmp_path / "a", client="a")
    environment_b = _stdio_subprocess_environment(tmp_path / "b", client="b")

    async def wait_for_evidence(path, kind):
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            if path.exists() and any(
                json.loads(line)["kind"] == kind
                for line in path.read_text(encoding="utf-8").splitlines()
            ):
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"missing subprocess evidence: {kind}")

    async def scenario():
        async with (
            _stdio_sdk_client(environment_a, client_name="owner-a") as client_a,
            _stdio_sdk_client(environment_b, client_name="foreign-b") as client_b,
        ):
            prompt = asyncio.create_task(
                client_a.call_tool(
                    "prompt_send",
                    {"prompt": "wait-for-cancel", "session_id": "fixture-session"},
                )
            )
            await wait_for_evidence(tmp_path / "a" / "evidence.jsonl", "entered")
            foreign = await client_b.call_tool(
                "prompt_cancel", {"session_id": "fixture-session"}
            )
            same = await client_a.call_tool(
                "prompt_cancel", {"session_id": "fixture-session"}
            )
            with pytest.raises(Exception) as cancellation:
                await prompt
        async with _stdio_sdk_client(environment_a) as completed_client:
            completed = await completed_client.call_tool(
                "prompt_cancel", {"session_id": "fixture-session"}
            )
        return foreign, same, cancellation.value, completed

    foreign, same, cancellation, completed = asyncio.run(
        asyncio.wait_for(scenario(), 15)
    )
    assert foreign.content[0].text == (
        '{"cancelled":false,"session_id":"fixture-session"}'
    )
    assert same.content[0].text == ('{"cancelled":true,"session_id":"fixture-session"}')
    assert "cancel" in str(cancellation).lower()
    assert completed.content[0].text == (
        '{"cancelled":false,"session_id":"fixture-session"}'
    )
    evidence = [
        json.loads(line)
        for line in (tmp_path / "a" / "evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [item["outcome"] for item in evidence if item["kind"] == "question"] == [
        "declined"
    ]
    assert len([item for item in evidence if item["kind"] == "question_cancel"]) == 1
    audits = [item for item in evidence if item["kind"] == "audit"]
    assert len(audits) == 1
    assert audits[0]["payload"]["reason"] == "prompt_cancel"
    assert audits[0]["run_control_cleared"] is True
    assert (
        len([item for item in evidence if item["kind"] == "late-worker-finished"]) == 1
    )


def test_build_server_registers_exact_tools_and_explicit_call_handler():
    from openprogram.mcp.server.server import build_server

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
    process = subprocess.Popen(
        [sys.executable, "-m", "openprogram.cli", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=os.getcwd(),
        env=environment,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout_lines: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        for line in process.stdout:
            stdout_lines.put(line)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        process.stdin.write(requests + "\n")
        process.stdin.flush()
        lines = [stdout_lines.get(timeout=10) for _ in range(2)]
    finally:
        process.stdin.close()
        process.wait(timeout=10)
    reader.join(timeout=10)
    while not stdout_lines.empty():
        lines.append(stdout_lines.get_nowait())
    stderr = process.stderr.read()
    frames = [json.loads(line) for line in lines]
    assert process.returncode == 0
    assert [frame["id"] for frame in frames] == [1, 2]
    assert frames[1]["result"]["tools"] == [
        tool.model_dump(by_alias=True, mode="json", exclude_none=True)
        for tool in get_mcp_tools()
    ]
    combined = "".join(lines) + stderr
    assert token not in combined
    assert "secret-client-info" not in combined
    assert "Traceback" not in combined


@pytest.mark.parametrize("with_progress", [False, True])
def test_explicit_handler_routes_sdk_request_id_and_ordered_progress(with_progress):
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp.server.server import build_server
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
    from openprogram.mcp.server.server import build_server
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
    from openprogram.mcp.server.server import build_server

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

        def get_nodes(self, session_id):
            return []

    class Questions:
        def __init__(self):
            self.cancelled = []
            self.resolved = []
            self.pending = {}

        def cancel_execution(self, session_id, execution_id):
            self.cancelled.append((session_id, execution_id))

        def list_pending(self, session_id):
            return [SimpleNamespace(id=qid, execution_id=execution_id)
                    for qid, execution_id in self.pending.items()]

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
                questions.pending["general-question"] = record.execution_id
                bus.emit(
                    make_event(
                        "question.asked",
                        "agent",
                        {
                            "id": "general-question",
                            "session_id": session_id,
                            "execution_id": record.execution_id,
                        },
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
                assert questions.cancelled == [(session_id, record.execution_id)]
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

    from openprogram.mcp.server.server import build_server

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


@pytest.mark.parametrize(
    ("outcome", "expected_text", "expected_error"),
    [
        ("success", "done", False),
        ("is_error", "denied", True),
        ("failure", "MCP tool execution failed", True),
    ],
)
def test_blocked_progress_does_not_delay_fixed_tool_result(
    outcome,
    expected_text,
    expected_error,
):
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp.server.server import build_server
    from openprogram.providers.types import TextContent

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    calls = []
    notification_entered = asyncio.Event()

    async def tool_call(*_args, **kwargs):
        calls.append(kwargs["call_id"])
        kwargs["on_progress"]("blocked")
        if outcome == "failure":
            raise RuntimeError("service-secret")
        return AgentToolResult(
            content=[TextContent(text="denied" if outcome == "is_error" else "done")],
            is_error=outcome == "is_error",
        )

    service.tool_call = tool_call

    class Session:
        async def send_progress_notification(self, *_args, **_kwargs):
            notification_entered.set()
            await asyncio.Event().wait()

    context = RequestContext(
        request_id=92,
        meta=mcp_types.RequestParams.Meta(progressToken="blocked"),
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
            result = await asyncio.wait_for(
                server.request_handlers[mcp_types.CallToolRequest](request), 0.5
            )
            assert notification_entered.is_set()
            assert not any(
                "consume_progress" in repr(task.get_coro())
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            )
            return result
        finally:
            request_ctx.reset(token)

    try:
        result = asyncio.run(scenario()).root
    finally:
        service.close()
    assert result.content[0].text == expected_text
    assert result.isError is expected_error
    assert calls == ["92"]


def test_handler_cancel_during_progress_timeout_cleanup_is_not_swallowed():
    from mcp.server.lowlevel.server import RequestContext, request_ctx

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp.server.server import build_server
    from openprogram.providers.types import TextContent

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    calls = []
    first_cancel = asyncio.Event()
    second_cancel = asyncio.Event()

    async def tool_call(*_args, **kwargs):
        calls.append(kwargs["call_id"])
        kwargs["on_progress"]("blocked")
        return AgentToolResult(content=[TextContent(text="must-not-return")])

    service.tool_call = tool_call

    class Session:
        async def send_progress_notification(self, *_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                first_cancel.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    second_cancel.set()
                    raise

    context = RequestContext(
        request_id=93,
        meta=mcp_types.RequestParams.Meta(progressToken="blocked"),
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
            await asyncio.wait_for(first_cancel.wait(), 0.5)
            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(handler, 0.5)
            assert second_cancel.is_set()
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
    assert calls == ["93"]


def test_protocol_tool_error_matrix_and_concurrent_progress_are_isolated():
    from mcp.shared.exceptions import McpError

    from openprogram.agent.types import AgentToolResult
    from openprogram.mcp.server.server import build_server
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
    from openprogram.mcp.server.server import build_server

    server = build_server(
        MCPClientContext("0123456789abcdef", mcp_client_authority("0123456789abcdef"))
    )
    service = server._openprogram_service
    entered = threading.Event()
    release = threading.Event()

    class DB:
        def get_session(self, session_id):
            return {"id": session_id, "agent_id": "main"}

        def get_nodes(self, session_id):
            return []

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
    from openprogram.mcp.server.server import build_server

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
