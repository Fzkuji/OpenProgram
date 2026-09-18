"""Adapters for the existing Web and Terminal control contracts."""
import copy
from ..registry import ResourceProvider, ResourceRegistry

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
    if action == "close":
        from .web_lifecycle import close_page
        return close_page(arguments["web_session_id"])
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


def register_builtins(registry: ResourceRegistry) -> None:
    from openprogram.web_use_contract import web_use_parameters
    terminal_fields = {
        "terminal_id": {"type": "string"}, "generation": {"type": "string"},
        "binding_id": {"type": "string"}, "cursor": {"type": "integer", "minimum": 0},
        "expected_input_revision": {"type": "integer", "minimum": 0},
        "shared": {"type": "boolean"}, "cols": {"type": "integer", "minimum": 20, "maximum": 500},
        "rows": {"type": "integer", "minimum": 5, "maximum": 200},
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
        "share": terminal_schema([*mutation, "shared"], [*mutation, "shared"]),
        "resize": terminal_schema([*mutation, "cols", "rows"], [*mutation, "cols", "rows"]),
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
    actions["close"] = {"type": "object", "properties": {"web_session_id": {"type": "string", "minLength": 1}},
                        "required": ["web_session_id"], "additionalProperties": False}
    actions["open"] = {"type": "object", "properties": {
        "url": {"type": "string", "pattern": "^https?://"},
        "backend": web_schema["properties"]["backend"],
    }, "required": ["url"], "additionalProperties": False}
    registry.register(ResourceProvider("web", "Built-in browser Page", actions, _web))

