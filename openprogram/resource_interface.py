"""Capability-described resource dispatch for trusted, installed integrations.

This registry does not own environments or grant access. Providers retain their
native identity, authorization, observation and lifecycle checks. Registration
is a Python integration API, never a renderer or model-supplied code loader.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Callable

from jsonschema import Draft202012Validator, ValidationError


@dataclass(frozen=True)
class ResourceProvider:
    name: str
    title: str
    actions: dict[str, dict]
    invoke: Callable[[str, dict], Any]


class ResourceRegistry:
    def __init__(self):
        self._providers: dict[str, ResourceProvider] = {}

    def register(self, provider: ResourceProvider) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", provider.name):
            raise ValueError("invalid_resource_provider")
        if provider.name in self._providers:
            raise ValueError("resource_provider_exists")
        for schema in provider.actions.values():
            Draft202012Validator.check_schema(schema)
        self._providers[provider.name] = ResourceProvider(
            provider.name, provider.title, copy.deepcopy(provider.actions), provider.invoke,
        )

    def _provider(self, name: str) -> ResourceProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise ValueError("unknown_resource_provider") from exc

    def describe(self, name: str = "") -> dict:
        if not name:
            return {"ok": True, "providers": [self.describe(key) for key in self._providers]}
        provider = self._provider(name)
        return {"provider": provider.name, "title": provider.title,
                "actions": copy.deepcopy(provider.actions)}

    def invoke(self, name: str, action: str, arguments: dict) -> Any:
        provider = self._provider(name)
        schema = provider.actions.get(action)
        if schema is None:
            raise ValueError("unsupported_resource_action")
        try:
            Draft202012Validator(schema).validate(arguments)
        except ValidationError as exc:
            raise ValueError("invalid_resource_arguments: " + exc.message) from exc
        return provider.invoke(action, copy.deepcopy(arguments))


def _require_tool(name: str) -> None:
    # Resource aliases must not re-enable a provider excluded by this turn's
    # resolved tool policy. Standalone trusted Python calls have no frozen set.
    from openprogram.programs._runtime import _allowed_tool_names
    allowed = _allowed_tool_names.get()
    if allowed is not None and name not in allowed:
        raise PermissionError("resource_provider_disabled")


def _terminal(action: str, arguments: dict):
    _require_tool("terminal_use")
    from openprogram.terminal_resources import execute
    values = dict(arguments)
    native_action = values.pop("operation", action) if action == "act" else action
    return execute(native_action, **values)


def _web(action: str, arguments: dict):
    _require_tool("web_use")
    from openprogram.programs.workflow.browser._runtime.web_use import _execute_web_use
    if action == "open":
        from openprogram.agent import surface_context
        from openprogram.programs.workflow.browser._runtime.page_recovery import (
            _open_page_error, _start_session_on_opened_page,
        )
        opened = surface_context.open_page(arguments["url"])
        if "surfaces" not in opened:
            return _open_page_error(opened)
        try:
            return _start_session_on_opened_page(
                context=opened, owner_id=surface_context.web_use_owner_id(),
                backend=arguments.get("backend", ""), arguments={},
            )
        except BaseException:
            surface_context.release_bindings(opened)
            raise
    # web_use.close releases its control session; it does not close the Page.
    command = {"list": "list_pages", "release": "close"}.get(action, action)
    return _execute_web_use(command, **arguments)


def _builtins() -> ResourceRegistry:
    from openprogram.web_use_contract import web_use_parameters
    registry = ResourceRegistry()
    terminal_fields = {
        "terminal_id": {"type": "string"}, "generation": {"type": "string"},
        "binding_id": {"type": "string"}, "cursor": {"type": "integer", "minimum": 0},
        "expected_input_revision": {"type": "integer", "minimum": 0},
        "data": {"type": "string"}, "workdir": {"type": "string"},
        "operation": {"type": "string", "enum": ["input", "interrupt"]},
    }
    def terminal_schema(fields, required=()):
        return {"type": "object", "properties": {key: terminal_fields[key] for key in fields},
                "required": list(required), "additionalProperties": False}
    identity = ["terminal_id", "generation"]
    binding = [*identity, "binding_id"]
    mutation = [*binding, "expected_input_revision"]
    registry.register(ResourceProvider("terminal", "Persistent Desktop terminal", {
        "list": terminal_schema([]), "open": terminal_schema(["workdir"]),
        "observe": terminal_schema([*identity, "cursor"], identity),
        "act": terminal_schema([*mutation, "operation", "data"], [*mutation, "operation"]),
        "release": terminal_schema(binding, binding), "close": terminal_schema(mutation, mutation),
    }, _terminal))
    web_schema = web_use_parameters()
    actions = {}
    for action, command in (("list", "list_pages"), ("observe", "observe"), ("act", "act"),
                            ("verify", "verify"), ("release", "close")):
        # Specialize the existing command-conditioned schema without weakening it.
        schema = copy.deepcopy(web_schema)
        # The command is injected by the adapter, not accepted from the caller.
        conditions = schema.pop("allOf", [])
        schema["properties"].pop("command")
        schema["required"] = []
        for condition in conditions:
            if condition["if"]["properties"]["command"]["const"] == command:
                schema["properties"].update(condition["then"].get("properties", {}))
                schema["required"] = condition["then"].get("required", [])
        schema["additionalProperties"] = False
        actions[action] = schema
    actions["open"] = {"type": "object", "properties": {
        "url": {"type": "string", "pattern": "^https?://"},
        "backend": web_schema["properties"]["backend"],
    }, "required": ["url"], "additionalProperties": False}
    registry.register(ResourceProvider("web", "Built-in browser Page", actions, _web))
    return registry


registry = _builtins()
