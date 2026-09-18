"""Provider dispatch preserves native arguments and rejects undeclared effects."""
import pytest

from openprogram.resource_interface import ResourceProvider, ResourceRegistry


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
    from openprogram.resource_interface import registry
    calls = []
    monkeypatch.setattr("openprogram.terminal_resources.execute", lambda action, **arguments:
                        calls.append((action, arguments)) or {"ok": False, "error": "observe_required"})
    values = {"terminal_id": "exact", "generation": "g", "binding_id": "b",
              "expected_input_revision": 4, "operation": "input", "data": "pwd\r"}
    assert registry.invoke("terminal", "act", values)["error"] == "observe_required"
    assert calls == [("input", {key: value for key, value in values.items() if key != "operation"})]
    with pytest.raises(ValueError, match="invalid_resource_arguments"):
        registry.invoke("terminal", "act", {"terminal_id": "exact", "operation": "input"})
    assert len(calls) == 1


def test_web_release_requires_exact_binding_and_is_not_page_close(monkeypatch):
    from openprogram.resource_interface import registry
    calls = []
    monkeypatch.setattr("openprogram.programs.workflow.browser._runtime.web_use._execute_web_use",
                        lambda command, **arguments: calls.append((command, arguments)) or {"ok": True})
    registry.invoke("web", "release", {"web_session_id": "observed"})
    assert calls == [("close", {"web_session_id": "observed"})]
    with pytest.raises(ValueError, match="invalid_resource_arguments"):
        registry.invoke("web", "release", {})
    assert "close" in registry.describe("web")["actions"]
    with pytest.raises(ValueError, match="invalid_resource_arguments"):
        registry.invoke("web", "act", {"arguments": {"executable": "anything"}})
    assert len(calls) == 1


def test_alias_does_not_reenable_provider_excluded_by_turn_policy(monkeypatch):
    from openprogram.programs._runtime import _allowed_tool_names
    from openprogram.resource_interface import registry
    calls = []
    monkeypatch.setattr("openprogram.terminal_resources.execute", lambda *a, **kw: calls.append(a))
    token = _allowed_tool_names.set({"resource"})
    try:
        with pytest.raises(PermissionError, match="resource_provider_disabled"):
            registry.invoke("terminal", "list", {})
        with pytest.raises(PermissionError, match="resource_provider_disabled"):
            registry.invoke("web", "list", {})
    finally:
        _allowed_tool_names.reset(token)
    assert calls == []


def test_web_close_uses_exact_owned_live_binding(monkeypatch):
    from types import SimpleNamespace
    from threading import RLock
    from openprogram.resources import registry
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.webui.ws_actions import webtab
    from openprogram.agent import surface_context
    calls = []
    session = SimpleNamespace(operation_lock=RLock(), closed=False, closing=False,
        owner_id='owner', binding_id='binding', page_key='page')
    native = SimpleNamespace(_lock=RLock(), _sessions={'observed': session},
        execute=lambda *a, **kw: calls.append(('release', a, kw)))
    monkeypatch.setattr(web_use_runtime, 'get_registry', lambda: native)
    monkeypatch.setattr(surface_context, 'web_use_owner_id', lambda: 'owner')
    monkeypatch.setattr(webtab, 'binding_page_descriptor', lambda _: {'page_key': 'page'})
    monkeypatch.setattr(webtab, 'request_close_tab', lambda binding: calls.append(('close', binding)) or {'ok': True})
    assert registry.invoke('web', 'close', {'web_session_id': 'observed'})['ok']
    assert calls[0] == ('close', 'binding')
    session.owner_id = 'other'
    assert not registry.invoke('web', 'close', {'web_session_id': 'observed'})['ok']
    assert len(calls) == 2
