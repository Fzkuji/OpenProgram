from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import mcp.types as mcp_types
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import McpError

from openprogram.agent.authority import (
    decide_tool_authority,
    mcp_client_authority,
)
from openprogram.agent.session_db import SessionDB, default_db
from openprogram.agent.types import AgentTool, AgentToolResult
from openprogram.mcp_server.tools import json_result, to_mcp_content
from openprogram.providers.types import TextContent


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


_APPROVAL_GATE_DENIAL_TEXT = {
    "HARD_CONSTRAINT_DENIED": "[denied] hard constraint",
    "PERMISSION_RULE_DENY": "[denied] blocked by permission rule",
    "APPROVAL_UNAVAILABLE_NON_INTERACTIVE": (
        "[denied] approval unavailable for non-interactive MCP"
    ),
}


def _trusted_approval_denial(
    result: AgentToolResult,
    *,
    request: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> AgentToolResult | None:
    details = result.details
    if not isinstance(details, dict):
        return None
    reason_code = details.get("reason_code")
    if reason_code == "AUTHORITY_CAPABILITY_DENIED":
        decision = decide_tool_authority(request, tool_name, arguments)
        if decision.allowed or decision.reason_code != reason_code:
            return None
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        f"[denied] authority tier does not allow {decision.capability}"
                    )
                )
            ],
            details={
                "denied": True,
                "reason_code": reason_code,
                "capability": decision.capability,
            },
            is_error=True,
        )
    text = _APPROVAL_GATE_DENIAL_TEXT.get(reason_code)
    if text is None:
        return None
    return AgentToolResult(
        content=[TextContent(text=text)],
        details={"denied": True, "reason_code": reason_code},
        is_error=True,
    )


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


def _mcp_error(code: int, message: str) -> McpError:
    return McpError(mcp_types.ErrorData(code=code, message=message))


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        copied = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError
            copied[key] = _copy_json(item)
        return copied
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise TypeError


def _execution_error() -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text="Runtime tool execution failed")],
        details={"reason_code": "RUNTIME_TOOL_EXECUTION_FAILED"},
        is_error=True,
    )


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

    async def tool_call(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        call_id: str,
        cancel_event: asyncio.Event,
        on_progress: Callable[[str], None] | None,
    ) -> AgentToolResult:
        """Execute one currently exposed Runtime tool under fixed MCP authority."""
        tool: AgentTool | None = None
        try:
            config = self._config_getter()
            server = config.get("mcp_server", {})
            configured = server.get("exposed_tools", [])
            exposed = self._registry_exposed_names()
            if (
                not isinstance(configured, list)
                or not all(isinstance(item, str) for item in configured)
                or name not in configured
                or name not in exposed
            ):
                raise LookupError
            tool = self._registry_get(name)
            if tool is None or tool.name != name:
                raise LookupError
        except Exception:
            tool = None
        if tool is None:
            raise _mcp_error(
                mcp_types.METHOD_NOT_FOUND,
                "underlying Runtime tool not found",
            )

        invalid_arguments = False
        try:
            copied_arguments = _copy_json(arguments)
            if not isinstance(copied_arguments, dict):
                raise TypeError
            Draft202012Validator.check_schema(tool.parameters)
            validator = Draft202012Validator(tool.parameters)
            if next(validator.iter_errors(copied_arguments), None) is not None:
                raise ValueError
        except Exception:
            invalid_arguments = True
        if invalid_arguments:
            raise _mcp_error(
                mcp_types.INVALID_PARAMS,
                "invalid underlying Runtime tool arguments",
            )

        from openprogram.agent.dispatcher import TurnRequest
        from openprogram.agent.internals._approval import wrap_with_approval
        from openprogram.functions.permission_rule import load_merged_rules

        setup_failed = False
        try:
            req = TurnRequest(
                session_id="",
                user_text="",
                agent_id="main",
                source="mcp",
                permission_mode="ask",
                permission_rules=load_merged_rules(""),
                **dict(self.context.authority),
            )
            gated = wrap_with_approval(tool, req, lambda _event: None)
        except Exception:
            setup_failed = True
        if setup_failed:
            return _execution_error()

        update_callback = None
        if on_progress is not None:

            def update_callback(update: Any) -> None:
                if not isinstance(update, str):
                    return
                try:
                    on_progress(update)
                except Exception:
                    return

        execution_failed = False
        try:
            result = await gated.execute(
                call_id,
                copied_arguments,
                cancel_event,
                update_callback,
            )
            if not isinstance(result, AgentToolResult):
                raise TypeError
            detached = result.model_copy(deep=True)
            to_mcp_content(detached)
            if detached.is_error:
                detached = (
                    _trusted_approval_denial(
                        detached,
                        request=req,
                        tool_name=name,
                        arguments=copied_arguments,
                    )
                    or _execution_error()
                )
        except Exception:
            execution_failed = True
        if execution_failed:
            return _execution_error()
        return detached


__all__ = ["MCPClientContext", "MCPService"]
