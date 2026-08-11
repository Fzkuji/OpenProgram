from __future__ import annotations

import json
import sys
from types import ModuleType
from dataclasses import FrozenInstanceError

import pytest

from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.mcp_server.service import MCPClientContext, MCPService
from openprogram.mcp_server.tools import json_result


def _tool(name: str, *, description: str, parameters: dict) -> AgentTool:
    async def execute(_call_id, _args, _cancel, _on_update):
        raise AssertionError("discovery must not execute Runtime tools")

    return AgentTool(
        name=name,
        description=description,
        parameters=parameters,
        label=name,
        execute=execute,
    )


def _payload(result: AgentToolResult):
    assert isinstance(result, AgentToolResult)
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return json.loads(result.content[0].text)


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
) -> MCPService:
    registry = {} if registry is None else registry
    exposed = set(registry) if exposed is None else exposed
    return MCPService(
        client_context,
        session_db=session_db or FakeSessionDB(),
        config_getter=lambda: {} if config is None else config,
        registry_get=registry.get,
        registry_exposed_names=lambda: set(exposed),
    )


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
