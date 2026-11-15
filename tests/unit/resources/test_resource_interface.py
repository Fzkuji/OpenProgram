"""Provider dispatch preserves native arguments and rejects undeclared effects."""
import pytest

from openprogram.host.resources import ResourceProvider, ResourceRegistry


@pytest.fixture(autouse=True)
def _standalone_resource_tool_policy():
    """Resource aliases run outside a resolved agent turn in these tests."""
    from openprogram.programs._host.runtime import _allowed_tool_names

    token = _allowed_tool_names.set(None)
    try:
        yield
    finally:
        _allowed_tool_names.reset(token)
        # A prior test may have left a turn policy in this worker context.
        # Never carry it into the next resource-interface test.
        _allowed_tool_names.set(None)


def test_declared_operation_is_dispatched_without_rewriting_identity():
    calls = []
    registry = ResourceRegistry()
    registry.register(ResourceProvider("test", "Test environment", {
        "observe": {"type": "object", "properties": {"id": {"type": "string"}},
                    "required": ["id"], "additionalProperties": False},
    }, lambda action, arguments: calls.append((action, arguments)) or {"ok": True, "id": arguments["id"]}))
    assert registry.invoke("test", "observe", {"id": "exact"}) == {"ok": True, "id": "exact"}
    assert calls == [("observe", {"id": "exact"})]
    with pytest.raises(ValueError, match="unsupported_resource_action"):
        registry.invoke("test", "close", {"id": "exact"})
    assert len(calls) == 1


def test_invalid_arguments_are_rejected_before_provider_effects():
    registry = ResourceRegistry()
    calls = []
    registry.register(ResourceProvider("test", "Test", {"list": {
        "type": "object", "properties": {}, "additionalProperties": False,
    }}, lambda *args: calls.append(args)))
    with pytest.raises(ValueError, match="invalid_resource_arguments"):
        registry.invoke("test", "list", {"owner_id": "invented"})
    assert calls == []


def test_descriptors_are_detached_and_provider_names_cannot_be_replaced():
    registry = ResourceRegistry()
    provider = ResourceProvider("test", "Test", {"list": {"type": "object"}}, lambda *_: {})
    registry.register(provider)
    descriptor = registry.describe("test")
    descriptor["actions"].clear()
    assert "list" in registry.describe("test")["actions"]
    with pytest.raises(ValueError, match="resource_provider_exists"):
        registry.register(provider)
    with pytest.raises(ValueError, match="unknown_resource_provider"):
        registry.invoke("missing", "list", {})


def test_builtin_terminal_dispatch_preserves_native_guards(monkeypatch):
    from openprogram.host.resources import registry
    calls = []
    monkeypatch.setattr("openprogram.host.resources.terminal.execute", lambda action, **arguments:
                        calls.append((action, arguments)) or {"ok": False, "error": "observe_required"})
    values = {"terminal_id": "exact", "generation": "g", "binding_id": "b",
              "expected_input_revision": 4, "operation": "input", "data": "pwd\r"}
    assert registry.invoke("terminal", "act", values)["error"] == "observe_required"
    assert calls == [("input", {key: value for key, value in values.items() if key != "operation"})]
    with pytest.raises(ValueError, match="invalid_resource_arguments"):
        registry.invoke("terminal", "act", {"terminal_id": "exact", "operation": "input"})
    assert len(calls) == 1


def test_alias_does_not_reenable_provider_excluded_by_turn_policy(monkeypatch):
    from openprogram.programs._host.runtime import _allowed_tool_names
    from openprogram.host.resources import registry
    calls = []
    monkeypatch.setattr("openprogram.host.resources.terminal.execute", lambda *a, **kw: calls.append(a))
    token = _allowed_tool_names.set({"resource"})
    try:
        with pytest.raises(PermissionError, match="resource_provider_disabled"):
            registry.invoke("terminal", "list", {})
    finally:
        _allowed_tool_names.reset(token)
    assert calls == []


