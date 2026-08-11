from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from openprogram.agent.authority import decide_tool_authority, mcp_client_authority
from openprogram.agent.session_db import SessionDB, default_db
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.mcp_server.tools import json_result


@dataclass(frozen=True)
class MCPClientContext:
    client_id: str
    authority: Mapping[str, Any]

    def __post_init__(self) -> None:
        expected = mcp_client_authority(self.client_id)
        if dict(self.authority) != expected:
            raise ValueError("invalid MCP client authority")
        object.__setattr__(self, "authority", MappingProxyType(expected))


def _default_config() -> Mapping[str, Any]:
    from openprogram.setup import _read_config

    return _read_config()


def _default_registry_get(name: str) -> AgentTool | None:
    from openprogram.functions._runtime import get

    return get(name)


def _default_registry_exposed_names() -> set[str]:
    from openprogram.functions._runtime import exposed_names

    return exposed_names()


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _session_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError
    session_id = row.get("id")
    title = row.get("title")
    updated_at = row.get("updated_at")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(title, str)
        or not _number(updated_at)
    ):
        raise ValueError
    return {"id": session_id, "title": title, "updated_at": updated_at}


def _message_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError
    message_id = row.get("id")
    role = row.get("role")
    content = row.get("content")
    timestamp = row.get("timestamp")
    if (
        not isinstance(message_id, str)
        or not message_id
        or not isinstance(role, str)
        or not role
        or not isinstance(content, str)
        or not _number(timestamp)
    ):
        raise ValueError
    return {
        "id": message_id,
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }


class MCPService:
    def __init__(
        self,
        context: MCPClientContext,
        *,
        session_db: SessionDB | None = None,
        config_getter: Callable[[], Mapping[str, Any]] | None = None,
        registry_get: Callable[[str], AgentTool | None] | None = None,
        registry_exposed_names: Callable[[], set[str]] | None = None,
    ) -> None:
        self.context = context
        self._session_db = session_db or default_db()
        self._config_getter = config_getter or _default_config
        self._registry_get = registry_get or _default_registry_get
        self._registry_exposed_names = (
            registry_exposed_names or _default_registry_exposed_names
        )

    def sessions_list(self) -> AgentToolResult:
        try:
            rows = self._session_db.list_sessions(limit=100)
            if not isinstance(rows, list):
                raise ValueError
            payload = [_session_row(row) for row in rows]
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        return json_result(payload)

    def session_get(self, session_id: str) -> AgentToolResult:
        try:
            session = self._session_db.get_session(session_id)
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        if session is None:
            return json_result({"error": "session not found"}, is_error=True)
        if not isinstance(session, Mapping) or session.get("id") != session_id:
            return json_result({"error": "session data unavailable"}, is_error=True)
        try:
            rows = self._session_db.get_branch(session_id)
            if not isinstance(rows, list):
                raise ValueError
            payload = [_message_row(row) for row in rows]
        except Exception:
            return json_result({"error": "session data unavailable"}, is_error=True)
        return json_result(payload)

    def exposed_runtime_tools(self) -> tuple[AgentTool, ...]:
        try:
            config = self._config_getter()
            server = config.get("mcp_server", {})
            configured = server.get("exposed_tools", [])
            if not isinstance(configured, list) or not all(
                isinstance(name, str) for name in configured
            ):
                return ()
            exposed = self._registry_exposed_names()
        except Exception:
            return ()

        tools: list[AgentTool] = []
        seen: set[str] = set()
        for name in tuple(configured):
            if name in seen:
                continue
            seen.add(name)
            if name not in exposed:
                continue
            try:
                tool = self._registry_get(name)
                allowed = decide_tool_authority(self.context.authority, name).allowed
            except Exception:
                continue
            if tool is not None and tool.name == name and allowed:
                try:
                    tools.append(tool.model_copy(deep=True))
                except Exception:
                    continue
        return tuple(tools)

    def tools_list(self) -> AgentToolResult:
        return json_result(
            [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.parameters,
                }
                for tool in self.exposed_runtime_tools()
            ]
        )


__all__ = ["MCPClientContext", "MCPService"]
