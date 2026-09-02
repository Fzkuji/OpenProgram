from __future__ import annotations

import asyncio
import builtins
import json
import sys
import threading
from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import ModuleType

import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import McpError

from openprogram.agent.authority import AuthorityError
from openprogram.agent.session_config import PermissionRules
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.programs._runtime import function
from openprogram.mcp.server.service import MCPClientContext, MCPService
from openprogram.mcp.server.tools import json_result, to_mcp_content
from openprogram.providers.types import ImageContent, TextContent


def _tool(
    name: str,
    *,
    description: str,
    parameters: dict,
    cache_control: dict | None = None,
) -> AgentTool:
    async def execute(_call_id, _args, _cancel, _on_update):
        raise AssertionError("discovery must not execute Runtime tools")

    return AgentTool(
        name=name,
        description=description,
        parameters=parameters,
        cache_control=cache_control,
        label=name,
        execute=execute,
    )


def _runtime_tool(
    name: str,
    execute,
    *,
    parameters: dict | None = None,
) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"Execute {name}",
        parameters=parameters
        or {"type": "object", "properties": {}, "additionalProperties": False},
        label=name,
        execute=execute,
    )


def _payload(result: AgentToolResult):
    assert isinstance(result, AgentToolResult)
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return json.loads(result.content[0].text)


def _tool_call(service: MCPService, *args, **kwargs) -> AgentToolResult:
    return asyncio.run(service.tool_call(*args, **kwargs))


class FakeSessionDB:
    def __init__(self) -> None:
        self.rows = [
            {
                "id": "s-new",
                "agent_id": "main",
                "title": "新会话",
                "created_at": 10.0,
                "updated_at": 20.5,
                "source": "web",
                "head_id": "a1",
                "model": "provider/model",
                "extra_meta": {"secret": "not-public"},
            },
            {
                "id": "s-old",
                "agent_id": "main",
                "title": "Older",
                "created_at": 1.0,
                "updated_at": 2.0,
                "source": "cli",
                "head_id": "a0",
                "model": None,
                "extra_meta": None,
            },
        ]
        self.sessions = {row["id"]: row for row in self.rows}
        self.branches = {
            "s-new": [
                {
                    "id": "u1",
                    "session_id": "s-new",
                    "role": "user",
                    "content": "你好",
                    "timestamp": 11.0,
                    "predecessor": "",
                    "caller": "",
                    "authority_tier": "owner",
                    "api_key": "not-public",
                },
                {
                    "id": "a1",
                    "session_id": "s-new",
                    "role": "assistant",
                    "content": "hello",
                    "timestamp": 12.0,
                    "predecessor": "u1",
                    "caller": "",
                    "metadata": {"secret": "not-public"},
                },
            ]
        }
        self.calls: list[tuple] = []

    def list_sessions(self, *, limit: int):
        self.calls.append(("list_sessions", limit))
        return self.rows

    def get_session(self, session_id: str):
        self.calls.append(("get_session", session_id))
        return self.sessions.get(session_id)

    def get_branch(self, session_id: str):
        self.calls.append(("get_branch", session_id))
        return self.branches.get(session_id, [])


@pytest.fixture
def client_context(tmp_path, monkeypatch) -> MCPClientContext:
    from openprogram import paths
    from openprogram.agent import authority

    monkeypatch.setattr(paths, "get_state_dir", lambda: tmp_path / "state")
    authority._reset_owner_cache_for_tests()
    client_id = "0123456789abcdef"
    return MCPClientContext(client_id, authority.mcp_client_authority(client_id))


def _service(
    client_context: MCPClientContext,
    *,
    session_db=None,
    config=None,
    registry=None,
    exposed=None,
    web_use_dispatch=None,
    web_use_release_owner=None,
    web_use_release_pages=None,
) -> MCPService:
    registry = {} if registry is None else registry
    exposed = set(registry) if exposed is None else exposed
    return MCPService(
        client_context,
        session_db=session_db or FakeSessionDB(),
        config_getter=lambda: {} if config is None else config,
        registry_get=registry.get,
        registry_exposed_names=lambda: set(exposed),
        web_use_dispatch=web_use_dispatch,
        web_use_release_owner=(
            web_use_release_owner or (lambda _owner_id: None)
        ),
        web_use_release_pages=(
            web_use_release_pages or (lambda _owner_id, _tokens: None)
        ),
    )


def test_first_class_web_use_has_narrow_connection_owned_authority(
    client_context,
) -> None:
    calls = []

    def dispatch(arguments, *, owner_id):
        calls.append((deepcopy(arguments), owner_id))
        return {"ok": True, "owner_bound": True}

    service = _service(
        client_context,
        web_use_dispatch=dispatch,
    )
    result = asyncio.run(service.web_use_call(
        {"command": "list_pages"},
        call_id="web-call",
        cancel_event=asyncio.Event(),
    ))

    assert result.is_error is False
    assert _payload(result) == {"ok": True, "owner_bound": True}
    assert calls[0][0] == {"command": "list_pages"}
    assert calls[0][1].startswith(f"mcp:{client_context.client_id}:")
    from openprogram.agent.authority import decide_tool_authority
    assert decide_tool_authority(client_context.authority, "web_use").allowed is False


def test_mcp_connection_close_releases_worker_owned_web_use_sessions(
    client_context,
) -> None:
    released = []
    service = _service(
        client_context,
        web_use_release_owner=released.append,
    )
    owner_id = service._web_use_owner_id

    service.close()

    assert released == [owner_id]


def test_cancelled_web_use_observe_closes_late_session(client_context) -> None:
    started = threading.Event()
    finish = threading.Event()
    closed = threading.Event()
    sessions = set()
    calls = []

    def dispatch(arguments, *, owner_id):
        command = arguments["command"]
        calls.append((command, owner_id))
        if command == "observe":
            started.set()
            assert finish.wait(timeout=2)
            sessions.add("ws-late")
            return {"ok": True, "web_session_id": "ws-late", "frame_id": "f1"}
        if command == "close":
            sessions.discard(arguments["web_session_id"])
            closed.set()
            return {"ok": True, "closed": True}
        raise AssertionError(command)

    service = _service(client_context, web_use_dispatch=dispatch)

    async def scenario():
        task = asyncio.create_task(service.web_use_call(
            {"command": "observe", "page_context_token": "page-1"},
            call_id="web-cancel",
            cancel_event=asyncio.Event(),
        ))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(closed.wait, 2)

    asyncio.run(scenario())

    assert sessions == set()
    assert [command for command, _ in calls] == ["observe", "close"]


def test_connection_close_sweeps_web_use_session_created_by_late_dispatch(
    client_context,
) -> None:
    started = threading.Event()
    finish = threading.Event()
    sessions = set()
    released = []

    def dispatch(arguments, *, owner_id):
        if arguments["command"] == "observe":
            started.set()
            assert finish.wait(timeout=2)
            sessions.add("ws-after-close")
            return {
                "ok": True,
                "web_session_id": "ws-after-close",
                "frame_id": "f1",
            }
        sessions.discard(arguments["web_session_id"])
        return {"ok": True, "closed": True}

    def release(owner_id):
        released.append(owner_id)
        sessions.clear()

    service = _service(
        client_context,
        web_use_dispatch=dispatch,
        web_use_release_owner=release,
    )

    async def scenario():
        task = asyncio.create_task(service.web_use_call(
            {"command": "observe", "page_context_token": "page-1"},
            call_id="web-close-race",
            cancel_event=asyncio.Event(),
        ))
        assert await asyncio.to_thread(started.wait, 2)
        await service.aclose()
        finish.set()
        result = await task
        assert result.is_error is True

    asyncio.run(scenario())

    assert sessions == set()
    assert released == [service._web_use_owner_id, service._web_use_owner_id]


def test_cancelled_web_use_list_pages_revokes_only_late_capabilities(
    client_context,
) -> None:
    started = threading.Event()
    finish = threading.Event()
    capabilities = {"pct-existing"}
    released = []

    def dispatch(arguments, *, owner_id):
        assert arguments == {"command": "list_pages"}
        started.set()
        assert finish.wait(timeout=2)
        capabilities.add("pct-late")
        return {
            "ok": True,
            "pages": [{"page_context_token": "pct-late"}],
        }

    def release_pages(owner_id, tokens):
        released.append((owner_id, list(tokens)))
        capabilities.difference_update(tokens)

    service = _service(
        client_context,
        web_use_dispatch=dispatch,
        web_use_release_pages=release_pages,
    )

    async def scenario():
        task = asyncio.create_task(service.web_use_call(
            {"command": "list_pages"},
            call_id="web-list-cancel",
            cancel_event=asyncio.Event(),
        ))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(20):
            if released:
                break
            await asyncio.sleep(0)

    asyncio.run(scenario())

    assert capabilities == {"pct-existing"}
    assert released == [(service._web_use_owner_id, ["pct-late"])]


def test_web_use_does_not_reimport_tool_registry_for_normalized_result(
    client_context, monkeypatch,
) -> None:
    expected = json_result({"ok": True})
    service = _service(
        client_context,
        web_use_dispatch=lambda _arguments, *, owner_id: expected,
    )
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "openprogram.programs._runtime":
            raise AssertionError("normalized web_use result reimported Runtime")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = asyncio.run(service.web_use_call(
        {"command": "list_pages"},
        call_id="web-normalized",
        cancel_event=asyncio.Event(),
    ))

    assert result is expected


def test_sessions_list_preserves_db_order_and_exposes_only_public_fields(
    client_context,
) -> None:
    db = FakeSessionDB()
    result = _service(client_context, session_db=db).sessions_list()

    assert result.is_error is False
    assert _payload(result) == [
        {"id": "s-new", "title": "新会话", "updated_at": 20.5},
        {"id": "s-old", "title": "Older", "updated_at": 2.0},
    ]
    assert db.calls == [("list_sessions", 100)]
    assert "not-public" not in result.content[0].text


def test_session_get_reads_active_branch_and_exposes_only_message_fields(
    client_context,
) -> None:
    db = FakeSessionDB()
    result = _service(client_context, session_db=db).session_get("s-new")

    assert result.is_error is False
    assert _payload(result) == [
        {"id": "u1", "role": "user", "content": "你好", "timestamp": 11.0},
        {
            "id": "a1",
            "role": "assistant",
            "content": "hello",
            "timestamp": 12.0,
        },
    ]
    assert db.calls == [("get_session", "s-new"), ("get_branch", "s-new")]
    assert "not-public" not in result.content[0].text


def test_session_get_rejects_unknown_without_reading_a_branch(client_context) -> None:
    db = FakeSessionDB()
    result = _service(client_context, session_db=db).session_get("missing")

    assert result.is_error is True
    assert _payload(result) == {"error": "session not found"}
    assert db.calls == [("get_session", "missing")]


def test_session_get_projects_real_active_branch_without_store_metadata(
    client_context,
    tmp_path,
) -> None:
    from openprogram.store import SessionStore

    db = SessionStore(tmp_path / "sessions")
    db.create_session("real", "main", title="Real")
    db.append_message(
        "real",
        {
            "id": "u1",
            "role": "user",
            "content": "hello",
            "timestamp": 1.0,
            "predecessor": None,
            "authority_tier": "owner",
            "secret": "not-public",
        },
    )

    result = _service(client_context, session_db=db).session_get("real")

    assert result.is_error is False
    assert _payload(result) == [
        {"id": "u1", "role": "user", "content": "hello", "timestamp": 1.0}
    ]
    assert "not-public" not in result.content[0].text


@pytest.mark.parametrize("session_id", [".", "../outside", "not-a-session"])
def test_session_get_rejects_real_non_session_directories_without_leaking(
    client_context,
    tmp_path,
    session_id,
) -> None:
    from openprogram.store import SessionStore

    root = tmp_path / "sessions"
    db = SessionStore(root)
    (root / "not-a-session").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.txt").write_text("leaked-secret", encoding="utf-8")

    result = _service(client_context, session_db=db).session_get(session_id)

    assert result.is_error is True
    assert _payload(result) == {"error": "session not found"}
    assert "leaked-secret" not in result.content[0].text


@pytest.mark.parametrize(
    ("method", "malformed"),
    [
        ("sessions_list", {"title": "leaked-secret", "updated_at": 1}),
        (
            "session_get",
            {
                "id": "m1",
                "role": "user",
                "content": {"secret": "leaked-secret"},
                "timestamp": 1,
            },
        ),
    ],
)
def test_malformed_session_data_fails_closed_without_echoing_values(
    client_context,
    method,
    malformed,
) -> None:
    db = FakeSessionDB()
    if method == "sessions_list":
        db.rows = [malformed]
        result = _service(client_context, session_db=db).sessions_list()
    else:
        db.branches["s-new"] = [malformed]
        result = _service(client_context, session_db=db).session_get("s-new")

    assert result.is_error is True
    assert _payload(result) == {"error": "session data unavailable"}
    assert "leaked-secret" not in result.content[0].text


def test_json_result_is_compact_deterministic_unicode_and_first_class_error() -> None:
    first = json_result({"z": "中文", "a": [1, True]})
    second = json_result({"a": [1, True], "z": "中文"})
    error = json_result({"error": "denied"}, is_error=True)

    assert first.content[0].text == second.content[0].text
    assert first.content[0].text == '{"a":[1,true],"z":"中文"}'
    assert first.is_error is False
    assert error.is_error is True
    assert _payload(error) == {"error": "denied"}


def test_tools_list_defaults_empty_even_when_registry_has_tools(client_context) -> None:
    registry = {
        "memory_status": _tool(
            "memory_status",
            description="Read memory status",
            parameters={"type": "object", "properties": {}},
        )
    }
    service = _service(client_context, registry=registry)

    assert service.exposed_runtime_tools() == ()
    result = service.tools_list()
    assert result.is_error is False
    assert _payload(result) == []


def test_tools_list_is_config_registry_exposure_and_paired_intersection(
    client_context,
) -> None:
    memory_schema = {
        "type": "object",
        "properties": {"revision": {"type": "string"}},
        "required": ["revision"],
    }
    registry = {
        "memory_update": _tool(
            "memory_update",
            description="Append memory",
            parameters=memory_schema,
        ),
        "memory_status": _tool(
            "memory_status",
            description="Read memory status",
            parameters={"type": "object", "properties": {}},
        ),
        "read": _tool(
            "read",
            description="Read a file",
            parameters={"type": "object", "properties": {"path": {}}},
        ),
        "hidden": _tool(
            "hidden",
            description="Internal helper",
            parameters={"type": "object"},
        ),
    }
    configured = [
        "memory_update",
        "missing",
        "read",
        "memory_update",
        "hidden",
        "memory_status",
    ]
    config = {"mcp_server": {"exposed_tools": configured}}
    service = _service(
        client_context,
        config=config,
        registry=registry,
        exposed={"memory_update", "memory_status", "read"},
    )

    assert [tool.name for tool in service.exposed_runtime_tools()] == [
        "memory_update",
        "memory_status",
    ]
    result = service.tools_list()
    assert result.is_error is False
    assert _payload(result) == [
        {
            "name": "memory_update",
            "description": "Append memory",
            "inputSchema": memory_schema,
        },
        {
            "name": "memory_status",
            "description": "Read memory status",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    assert configured == [
        "memory_update",
        "missing",
        "read",
        "memory_update",
        "hidden",
        "memory_status",
    ]


def test_tools_list_output_mutation_cannot_mutate_registry_or_later_results(
    client_context,
) -> None:
    schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    tool = _tool("memory_status", description="Status", parameters=schema)
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": tool},
    )

    first = _payload(service.tools_list())
    first[0]["name"] = "mutated"
    first[0]["inputSchema"]["properties"]["query"]["type"] = "number"

    assert tool.name == "memory_status"
    assert tool.parameters == schema
    assert _payload(service.tools_list()) == [
        {
            "name": "memory_status",
            "description": "Status",
            "inputSchema": schema,
        }
    ]


def test_exposed_runtime_tools_returns_detached_deep_copies(client_context) -> None:
    tool = _tool(
        "memory_status",
        description="Status",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        cache_control={"metadata": {"scope": ["registry"]}},
    )
    setattr(tool, "_discovery_metadata", {"tags": ["registry"]})
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": tool},
    )

    returned = service.exposed_runtime_tools()[0]
    returned.parameters["properties"]["query"]["type"] = "number"
    returned.cache_control["metadata"]["scope"].append("caller")
    returned._discovery_metadata["tags"].append("caller")

    assert returned is not tool
    assert tool.parameters["properties"]["query"]["type"] == "string"
    assert tool.cache_control == {"metadata": {"scope": ["registry"]}}
    assert tool._discovery_metadata == {"tags": ["registry"]}
    again = service.exposed_runtime_tools()[0]
    assert again.parameters["properties"]["query"]["type"] == "string"
    assert again.cache_control == {"metadata": {"scope": ["registry"]}}
    assert again._discovery_metadata == {"tags": ["registry"]}


def test_client_context_is_immutable_and_discovery_never_uses_owner_or_client_info(
    client_context,
    monkeypatch,
) -> None:
    from openprogram.agent import authority

    monkeypatch.setattr(
        authority,
        "local_owner_authority",
        lambda: (_ for _ in ()).throw(AssertionError("must not upgrade authority")),
    )

    class PoisonWebAuth(ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"must not access Web owner auth: {name}")

    monkeypatch.setitem(
        sys.modules,
        "openprogram.webui.owner_auth",
        PoisonWebAuth("openprogram.webui.owner_auth"),
    )
    with pytest.raises((FrozenInstanceError, AttributeError)):
        client_context.client_id = "fedcba9876543210"
    with pytest.raises(TypeError):
        client_context.authority["authority_tier"] = "owner"
    assert not hasattr(client_context, "clientInfo")

    read = _tool(
        "read",
        description="Read file",
        parameters={"type": "object", "properties": {}},
    )
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["read"]}},
        registry={"read": read},
    )
    assert _payload(service.tools_list()) == []


@pytest.mark.parametrize(
    "client_id",
    [
        "",
        "0123456789abcde",
        "0123456789abcdef0",
        "0123456789ABCDEF",
        "0123456789ABCDEG",
    ],
)
def test_client_context_rejects_non_fingerprint_client_ids(
    client_context,
    client_id,
) -> None:
    authority = dict(client_context.authority)
    authority["speaker_id"] = f"mcp/{client_id}"

    with pytest.raises(AuthorityError, match="MCP client ID is invalid"):
        MCPClientContext(client_id, authority)


def test_client_context_rejects_forged_principal(client_context) -> None:
    authority = dict(client_context.authority)
    authority["principal_id"] = "owner/install/ffffffffffffffff"

    with pytest.raises(ValueError, match="invalid MCP client authority"):
        MCPClientContext(client_context.client_id, authority)


def test_client_context_rejects_extra_authority_fields(client_context) -> None:
    authority = {**client_context.authority, "caller_authority": "owner"}

    with pytest.raises(ValueError, match="invalid MCP client authority"):
        MCPClientContext(client_context.client_id, authority)


def test_client_context_rejects_nested_authority_alias(client_context) -> None:
    nested = {"roles": ["owner"]}
    authority = {**client_context.authority, "metadata": nested}

    with pytest.raises(ValueError, match="invalid MCP client authority"):
        MCPClientContext(client_context.client_id, authority)
    nested["roles"].append("paired")


def test_client_context_stores_only_detached_fixed_scalar_authority(
    client_context,
) -> None:
    source = dict(client_context.authority)
    context = MCPClientContext(client_context.client_id, source)
    source["speaker_display"] = "mutated"

    assert dict(context.authority) == dict(client_context.authority)
    assert set(context.authority) == {
        "speaker_kind",
        "speaker_id",
        "speaker_display",
        "principal_id",
        "authority_tier",
        "interaction",
    }
    assert all(isinstance(value, str) for value in context.authority.values())


@pytest.mark.parametrize("name", ["unknown", "memory_status"])
def test_tool_call_unknown_and_unexposed_are_indistinguishable(
    client_context,
    name,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": []}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    with pytest.raises(McpError) as caught:
        _tool_call(
            service,
            name,
            {},
            call_id="call-1",
            cancel_event=asyncio.Event(),
            on_progress=None,
        )

    assert caught.value.error.code == mcp_types.METHOD_NOT_FOUND
    assert caught.value.error.message == "underlying Runtime tool not found"
    assert calls == []


@pytest.mark.parametrize("registered", [False, True])
def test_tool_call_configured_tool_must_be_registered_and_live(
    client_context,
    registered,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    registry = (
        {"memory_status": _runtime_tool("memory_status", execute)} if registered else {}
    )
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry=registry,
        exposed=set(),
    )

    with pytest.raises(McpError) as caught:
        _tool_call(
            service,
            "memory_status",
            {},
            call_id="call-1",
            cancel_event=asyncio.Event(),
            on_progress=None,
        )

    assert caught.value.error.code == mcp_types.METHOD_NOT_FOUND
    assert caught.value.error.message == "underlying Runtime tool not found"
    assert calls == []


def test_tool_call_rechecks_live_registry_at_execution_time(client_context) -> None:
    calls = []

    async def old_execute(*_args):
        raise AssertionError("detached discovery handle must not execute")

    async def new_execute(call_id, arguments, cancel_event, on_update):
        calls.append((call_id, deepcopy(arguments), cancel_event, on_update))
        return AgentToolResult(content=[TextContent(text="new")])

    registry = {"memory_status": _runtime_tool("memory_status", old_execute)}
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry=registry,
    )
    discovered = service.exposed_runtime_tools()[0]
    discovered.name = "mutated"
    registry["memory_status"] = _runtime_tool("memory_status", new_execute)
    cancel = asyncio.Event()

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="fresh-call",
        cancel_event=cancel,
        on_progress=None,
    )

    assert result.is_error is False
    assert result.content[0].text == "new"
    assert calls == [("fresh-call", {}, cancel, None)]


def test_tool_call_missing_paired_capability_is_typed_and_does_not_invoke(
    client_context,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["read"]}},
        registry={"read": _runtime_tool("read", execute)},
    )

    result = _tool_call(
        service,
        "read",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].type == "text"
    assert result.content[0].text == "[denied] authority tier does not allow fs.read"
    assert result.details == {
        "denied": True,
        "reason_code": "AUTHORITY_CAPABILITY_DENIED",
        "capability": "fs.read",
    }
    assert calls == []


@pytest.mark.parametrize(
    ("name", "arguments", "rules", "reason_code", "text"),
    [
        (
            "bash",
            {"command": "pwd"},
            None,
            "HARD_CONSTRAINT_DENIED",
            "[denied] hard constraint",
        ),
        (
            "memory_update",
            {"revision": "r1"},
            PermissionRules(deny=["memory_update"]),
            "PERMISSION_RULE_DENY",
            "[denied] blocked by permission rule",
        ),
        (
            "memory_search",
            {"query": "x"},
            None,
            "APPROVAL_UNAVAILABLE_NON_INTERACTIVE",
            "[denied] approval unavailable for non-interactive MCP",
        ),
    ],
)
def test_tool_call_approval_gate_denials_are_typed_before_invocation(
    client_context,
    name,
    arguments,
    rules,
    reason_code,
    text,
    monkeypatch,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    async def approval_must_not_wait(**_kwargs):
        raise AssertionError("MCP approval must not wait")

    monkeypatch.setattr(
        "openprogram.agent.internals._approval.await_user_approval",
        approval_must_not_wait,
    )
    tool = _runtime_tool(
        name,
        execute,
        parameters={
            "type": "object",
            "properties": {key: {"type": "string"} for key in arguments},
            "required": list(arguments),
            "additionalProperties": False,
        },
    )
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": [name]}},
        registry={name: tool},
    )
    monkeypatch.setattr(
        "openprogram.programs.permission_rule.load_merged_rules",
        lambda _session_id: rules or PermissionRules(),
    )

    result = _tool_call(
        service,
        name,
        arguments,
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].text == text
    assert result.details == {"denied": True, "reason_code": reason_code}
    assert calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": 7},
        {"query": "ok", "authority_tier": "owner"},
    ],
)
def test_tool_call_invalid_runtime_arguments_are_invalid_params(
    client_context,
    arguments,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={
            "memory_status": _runtime_tool(
                "memory_status",
                execute,
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            )
        },
    )

    with pytest.raises(McpError) as caught:
        _tool_call(
            service,
            "memory_status",
            arguments,
            call_id="call-1",
            cancel_event=asyncio.Event(),
            on_progress=None,
        )

    assert caught.value.error.code == mcp_types.INVALID_PARAMS
    assert caught.value.error.message == "invalid underlying Runtime tool arguments"
    assert calls == []


def test_tool_call_uses_fixed_turn_request_and_detached_arguments(
    client_context,
    monkeypatch,
) -> None:
    captured = []

    async def execute(call_id, arguments, cancel_event, on_update):
        captured.append((call_id, arguments, cancel_event, on_update))
        arguments["query"] = "tool-mutated"
        return AgentToolResult(content=[TextContent(text="ok")])

    def capture_wrap(tool, req, on_event):
        captured.append((req, on_event))
        return tool

    monkeypatch.setattr(
        "openprogram.agent.internals._approval.wrap_with_approval", capture_wrap
    )
    arguments = {
        "query": "caller",
        "source": "web",
        "permission_mode": "bypass",
        "authority_tier": "owner",
        "interaction": "interactive",
    }
    schema = {
        "type": "object",
        "properties": {key: {"type": "string"} for key in arguments},
        "required": list(arguments),
        "additionalProperties": False,
    }
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={
            "memory_status": _runtime_tool("memory_status", execute, parameters=schema)
        },
    )
    cancel = asyncio.Event()

    result = _tool_call(
        service,
        "memory_status",
        arguments,
        call_id="fixed",
        cancel_event=cancel,
        on_progress=None,
    )

    req, on_event = captured[0]
    assert req.source == "mcp"
    assert req.permission_mode == "ask"
    assert req.authority_tier == "paired"
    assert req.interaction == "non-interactive"
    assert {key: getattr(req, key) for key in client_context.authority} == dict(
        client_context.authority
    )
    assert callable(on_event)
    assert captured[1][0] == "fixed"
    assert captured[1][2:] == (cancel, None)
    assert arguments["query"] == "caller"
    assert result.is_error is False


def test_tool_call_content_conversion_preserves_text_and_image() -> None:
    result = AgentToolResult(
        content=[
            TextContent(text="hello"),
            ImageContent(data="aGVsbG8=", mime_type="image/png"),
        ]
    )

    converted = to_mcp_content(result)

    assert [
        block.model_dump(by_alias=True, exclude_none=True) for block in converted
    ] == [
        {"type": "text", "text": "hello"},
        {"type": "image", "data": "aGVsbG8=", "mimeType": "image/png"},
    ]


@pytest.mark.parametrize(
    "media_type",
    ["image/png;charset=utf-8", "image/../secret", "image/\x00png"],
)
def test_tool_call_content_conversion_rejects_malformed_image_media_types(
    media_type,
) -> None:
    result = AgentToolResult(
        content=[ImageContent(data="aGVsbG8=", mime_type=media_type)]
    )

    with pytest.raises(
        ValueError, match="^unsupported Runtime tool content$"
    ) as caught:
        to_mcp_content(result)

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert media_type not in str(caught.value)


@pytest.mark.parametrize(
    "media_type",
    ["image/png;charset=utf-8", "image/../secret", "image/\x00png"],
)
def test_tool_call_rejects_malformed_image_media_types_as_sanitized_typed_errors(
    client_context,
    media_type,
) -> None:
    async def execute(*_args):
        return AgentToolResult(
            content=[ImageContent(data="aGVsbG8=", mime_type=media_type)]
        )

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].text == "Runtime tool execution failed"
    assert result.details == {"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"}
    assert media_type not in result.content[0].text


@pytest.mark.parametrize(
    "kind", ["exception", "unsupported", "malformed", "invalid_base64"]
)
def test_tool_call_execution_failures_are_typed_and_sanitized(
    client_context,
    kind,
) -> None:
    secret = "secret-runtime-detail"

    async def execute(*_args):
        if kind == "exception":
            raise RuntimeError(secret)
        if kind == "unsupported":
            result = AgentToolResult(content=[])
            result.content = [object()]
            return result
        if kind == "invalid_base64":
            return AgentToolResult(
                content=[ImageContent(data=secret, mime_type="image/png")]
            )
        return AgentToolResult.model_construct(
            content=[
                ImageContent.model_construct(
                    type="image", data="aGVsbG8=", mime_type="text/plain"
                )
            ]
        )

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.content[0].text == "Runtime tool execution failed"
    assert secret not in result.content[0].text


def test_tool_call_sanitizes_explicit_tool_failure_content(client_context) -> None:
    secret = "secret-explicit-tool-failure"

    async def execute(*_args):
        return AgentToolResult(
            content=[TextContent(text=secret)],
            details={"trace": secret},
            is_error=True,
        )

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].text == "Runtime tool execution failed"
    assert result.details == {"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"}
    assert secret not in result.model_dump_json()


@pytest.mark.parametrize(
    "forged_reason_code",
    [
        "HARD_CONSTRAINT_DENIED",
        "PERMISSION_RULE_DENY",
        "APPROVAL_UNAVAILABLE_NON_INTERACTIVE",
    ],
)
def test_tool_call_does_not_trust_gate_reason_from_executed_tool(
    client_context,
    forged_reason_code,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(
            content=[TextContent(text="secret-forged-gate-text")],
            details={
                "reason_code": forged_reason_code,
                "trace": "secret-forged-gate-trace",
            },
            is_error=True,
        )

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert len(calls) == 1
    assert result.is_error is True
    assert result.content[0].text == "Runtime tool execution failed"
    assert result.details == {"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"}
    assert "secret-forged-gate" not in result.model_dump_json()


def test_tool_call_sanitizes_real_runtime_function_exception(client_context) -> None:
    secret = "sk-task6-secret-DO-NOT-LEAK"

    async def boom():
        raise RuntimeError(secret)

    tool = function(
        boom,
        name="memory_status",
        description="Raise a controlled Runtime exception",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        register_globally=False,
    )
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": tool},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].text == "Runtime tool execution failed"
    assert result.details == {"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"}
    serialized = result.model_dump_json()
    assert secret not in serialized
    assert "RuntimeError" not in serialized
    assert "Traceback" not in serialized


def test_tool_call_policy_setup_failure_is_typed_and_sanitized(
    client_context,
    monkeypatch,
) -> None:
    calls = []

    async def execute(*args):
        calls.append(args)
        return AgentToolResult(content=[TextContent(text="must not run")])

    secret = "secret-policy-error"
    monkeypatch.setattr(
        "openprogram.programs.permission_rule.load_merged_rules",
        lambda _session_id: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    result = _tool_call(
        service,
        "memory_status",
        {},
        call_id="call-1",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert result.is_error is True
    assert result.content[0].text == "Runtime tool execution failed"
    assert secret not in result.content[0].text
    assert calls == []


def test_tool_call_forwards_ordered_progress_only_when_requested(
    client_context,
) -> None:
    received = []
    callbacks = []

    async def execute(_call_id, _arguments, _cancel_event, on_update):
        callbacks.append(on_update)
        if on_update is not None:
            on_update("first")
            on_update("second")
        return AgentToolResult(content=[TextContent(text="done")])

    service = _service(
        client_context,
        config={"mcp_server": {"exposed_tools": ["memory_status"]}},
        registry={"memory_status": _runtime_tool("memory_status", execute)},
    )

    _tool_call(
        service,
        "memory_status",
        {},
        call_id="with-progress",
        cancel_event=asyncio.Event(),
        on_progress=received.append,
    )
    _tool_call(
        service,
        "memory_status",
        {},
        call_id="without-progress",
        cancel_event=asyncio.Event(),
        on_progress=None,
    )

    assert received == ["first", "second"]
    assert callbacks[0] is not None
    assert callbacks[1] is None
