"""Adapters for the existing Terminal control contract."""
from ..registry import ResourceProvider, ResourceRegistry

def _require_tool(name: str) -> None:
    # Resource aliases must not re-enable a provider excluded by this turn's
    # resolved tool policy. Standalone trusted Python calls have no frozen set.
    from openprogram.host import runtime_facts
    allowed = runtime_facts.allowed_tool_names()
    if allowed is not None and name not in allowed:
        raise PermissionError("resource_provider_disabled")


def _terminal(action: str, arguments: dict):
    _require_tool("terminal_use")
    from openprogram.host.resources.terminal import execute
    values = dict(arguments)
    native_action = values.pop("operation", action) if action == "act" else action
    return execute(native_action, **values)


def register_builtins(registry: ResourceRegistry) -> None:
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
    # The ``web`` provider belongs to the browser application, which adds it
    # when it loads (applications/browser/resource.py).
