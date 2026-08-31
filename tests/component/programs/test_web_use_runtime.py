from __future__ import annotations

import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest


class _Adapter:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[str, dict]] = []

    def observe(self, session, arguments):
        self.calls.append(("observe", dict(arguments)))
        return {"frame_id": "frame-1", "backend": self.name}

    def act(self, session, arguments):
        self.calls.append(("act", dict(arguments)))
        return {"ok": True, "backend": self.name}

    def verify(self, session, arguments):
        self.calls.append(("verify", dict(arguments)))
        return {"passed": True, "backend": self.name}

    def close(self, session):
        self.calls.append(("close", {}))


def _allow_binding(_binding_id):
    return {"ok": True}


def test_registry_releases_only_requested_unconsumed_page_capabilities():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        SUPPORTED_BACKENDS,
        WebUseSessionRegistry,
    )

    released = []
    registry = WebUseSessionRegistry(
        adapters={name: _Adapter(name) for name in SUPPORTED_BACKENDS},
        binding_validator=_allow_binding,
        release_context=lambda context: released.append(context["context_id"]),
    )
    listed = registry.list_pages(
        owner_id="owner-1",
        context={
            "context_id": "inventory-1",
            "surfaces": [
                {"binding_id": "b1", "surface_key": "p1"},
                {"binding_id": "b2", "surface_key": "p2"},
            ],
        },
    )
    first, second = [page["page_context_token"] for page in listed["pages"]]

    assert registry.release_page_capabilities(
        [first], owner_id="other-owner",
    ) == 0
    assert registry.release_page_capabilities(
        [first], owner_id="owner-1",
    ) == 1
    assert released == ["inventory-1"]

    observed = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="owner-1",
        page_context_token=second,
    )
    assert observed["web_session_id"]


def test_registry_supports_three_backends_and_freezes_one_per_session():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
        SUPPORTED_BACKENDS,
    )

    assert SUPPORTED_BACKENDS == (
        "playwright_mcp",
        "chrome_devtools_mcp",
        "open_claude_chrome",
    )
    adapters = {name: _Adapter(name) for name in SUPPORTED_BACKENDS}
    registry = WebUseSessionRegistry(
        adapters=adapters, binding_validator=_allow_binding,
    )

    observed = registry.execute(
        command="observe",
        backend="chrome_devtools_mcp",
        binding_id="binding-1",
        arguments={"detail": "interactive"},
    )
    session_id = observed["web_session_id"]
    assert observed["backend"] == "chrome_devtools_mcp"

    mismatch = registry.execute(
        command="act",
        backend="playwright_mcp",
        web_session_id=session_id,
        arguments={"action": "click", "ref": "e1"},
    )
    assert mismatch == {
        "ok": False,
        "reason_code": "backend_mismatch",
        "web_session_id": session_id,
        "backend": "chrome_devtools_mcp",
    }
    assert adapters["playwright_mcp"].calls == []

    acted = registry.execute(
        command="act",
        web_session_id=session_id,
        arguments={
            "action": "click", "expected_frame_id": "frame-1", "ref": "e1",
        },
    )
    assert acted["ok"] is True
    assert acted["backend"] == "chrome_devtools_mcp"
    assert adapters["chrome_devtools_mcp"].calls == [
        ("observe", {"detail": "interactive"}),
        ("act", {
            "action": "click", "expected_frame_id": "frame-1", "ref": "e1",
        }),
    ]


def test_registry_preserves_one_argument_binding_validator_compatibility():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapters = {
        name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )
    }
    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_validator=lambda _binding_id: {"ok": True},
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1",
    )

    acted = registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        arguments={"action": "click", "expected_frame_id": "frame-1"},
    )

    assert acted["ok"] is True


def test_registry_rejects_action_when_bound_page_revision_changes():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    revisions = {"page_revision": 11, "access_revision": 12}
    validations = []
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_revision_resolver=lambda _binding: dict(revisions),
        binding_validator=lambda binding: (
            validations.append(binding) or {"ok": True}
        ),
        release_context=lambda context: released.append(context["context_id"]),
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-1"},
    )

    revisions["access_revision"] = 13
    rejected = registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1", arguments={
            "action": "click", "expected_frame_id": "frame-1",
        },
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert validations == ["binding-1"]
    assert adapter.calls == [("observe", {}), ("close", {})]
    assert released == ["ctx-1"]
    assert registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1",
    )["reason_code"] == "web_session_not_found"


def test_registry_revalidates_visibility_before_each_existing_session_command():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    visible = {"ok": True}
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_revision_resolver=lambda _binding: {
            "page_revision": 21, "access_revision": 22,
        },
        binding_validator=lambda _binding: dict(visible),
        release_context=lambda context: released.append(context["context_id"]),
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-1"},
    )

    visible.update(ok=False, reason_code="page_context_stale")
    rejected = registry.execute(
        command="verify", web_session_id=observed["web_session_id"],
        owner_id="owner-1", arguments={"assertion": "text_contains"},
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert adapter.calls == [("observe", {}), ("close", {})]
    assert released == ["ctx-1"]


def test_page_capability_revisions_cross_the_session_validation_boundary(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )
    from openprogram.webui.ws_actions import webtab

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    validations = []

    def validate(binding_id, **expected):
        validations.append((binding_id, expected))
        return {"ok": True}

    monkeypatch.setattr(webtab, "request_bound_tab", validate)

    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_revision_resolver=lambda _binding: {},
    )
    context = {
        "context_id": "ctx-1",
        "surfaces": [{
            "surface_key": "p1",
            "binding_id": "binding-1",
            "page_key": "page:41",
            "page_revision": 41,
            "access_revision": 42,
            "geometry_revision": 43,
        }],
    }
    token = registry.list_pages(
        context=context, owner_id="owner-1",
    )["pages"][0]["page_context_token"]
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="owner-1", page_context_token=token,
    )

    acted = registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1", arguments={
            "action": "click", "expected_frame_id": "frame-1",
        },
    )

    assert acted["ok"] is True
    assert validations == [("binding-1", {
        "expected_page_revision": 41,
        "expected_access_revision": 42,
        "expected_geometry_revision": 43,
    }), ("binding-1", {
        "expected_page_revision": 41,
        "expected_access_revision": 42,
        "expected_geometry_revision": 43,
    })]


def test_first_observe_rejects_stale_geometry_before_backend(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )
    from openprogram.webui.ws_actions import webtab

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    commands = []
    owner = object()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", geometry_revision=9,
    )

    def request_on_ws(_ws, command, _timeout):
        commands.append(command)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
            "geometry_revision": 10,
        }

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    registry = WebUseSessionRegistry(adapters=adapters)

    rejected = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        binding_id=binding_id,
        page_context={
            "context_id": "ctx-first-observe",
            "surfaces": [{
                "surface_key": "s1",
                "binding_id": binding_id,
                "page_key": webtab.binding_page_key(binding_id),
                **webtab.binding_revisions(binding_id),
            }],
        },
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert commands == [{
        "op": "activate",
        "window_id": "window-1",
        "tab_id": "tab-1",
        "expected_geometry_revision": 9,
    }]
    assert adapter.calls == [("close", {})]


def test_geometry_changed_during_activation_blocks_backend_action(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )
    from openprogram.webui.ws_actions import webtab

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    owner = object()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", geometry_revision=9,
    )
    activations = iter((9, 10))
    monkeypatch.setattr(webtab, "request_on_ws", lambda *_args, **_kwargs: {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "target_id": "target-1",
        "geometry_revision": next(activations),
    })
    registry = WebUseSessionRegistry(adapters=adapters)
    context = {
        "context_id": "ctx-geometry",
        "surfaces": [{
            "surface_key": "s1",
            "binding_id": binding_id,
            "page_key": webtab.binding_page_key(binding_id),
            **webtab.binding_revisions(binding_id),
        }],
    }
    observed = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        binding_id=binding_id,
        page_context=context,
    )

    rejected = registry.execute(
        command="act",
        web_session_id=observed["web_session_id"],
        arguments={"action": "click", "expected_frame_id": "frame-1"},
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert adapter.calls == [("observe", {}), ("close", {})]


def test_registry_leases_one_exact_page_to_one_session_until_close():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    registry = WebUseSessionRegistry(
        adapters=adapters,
        page_key_resolver=lambda binding: {
            "binding-1": "page-1", "binding-2": "page-1",
        }[binding],
        binding_validator=_allow_binding,
    )

    first = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
    )
    second = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )

    assert second == {"ok": False, "reason_code": "page_in_use"}
    assert adapters["playwright_mcp"].calls == []

    registry.execute(
        command="close", web_session_id=first["web_session_id"],
        owner_id="owner-1",
    )
    third = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )
    assert third["frame_id"] == "frame-1"
    assert adapters["playwright_mcp"].calls == [
        ("observe", {}),
    ]


def test_page_lease_remains_held_until_close_cleanup_finishes():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    entered = threading.Event()
    finish = threading.Event()

    def release_context(_context):
        entered.set()
        assert finish.wait(2)

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=release_context,
        page_key_resolver=lambda _binding: "page-1",
        binding_validator=_allow_binding,
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-1"},
    )
    errors = []

    def close_session():
        try:
            registry.execute(
                command="close",
                web_session_id=observed["web_session_id"],
                owner_id="owner-1",
            )
        except Exception as exc:  # pragma: no cover - assertion reports detail
            errors.append(exc)

    thread = threading.Thread(target=close_session)
    thread.start()
    assert entered.wait(2)
    assert registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )["reason_code"] == "page_in_use"
    finish.set()
    thread.join(2)
    assert not thread.is_alive()
    assert errors == []
    assert registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )["frame_id"] == "frame-1"


@pytest.mark.parametrize("cleanup", ["release_owner", "close_all"])
def test_cleanup_waits_for_inflight_action_before_releasing_page(cleanup):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    class _BlockingAdapter(_Adapter):
        def __init__(self, name):
            super().__init__(name)
            self.entered = threading.Event()
            self.finish = threading.Event()

        def act(self, session, arguments):
            self.entered.set()
            assert self.finish.wait(2)
            return super().act(session, arguments)

    blocking = _BlockingAdapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": blocking,
    }
    registry = WebUseSessionRegistry(
        adapters=adapters,
        page_key_resolver=lambda _binding: "page-1",
        binding_validator=_allow_binding,
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
    )
    action_thread = threading.Thread(target=lambda: registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1", arguments={
            "action": "click", "expected_frame_id": "frame-1",
        },
    ))
    action_thread.start()
    assert blocking.entered.wait(2)
    cleanup_thread = threading.Thread(
        target=registry.release_owner if cleanup == "release_owner" else registry.close_all,
        args=("owner-1",) if cleanup == "release_owner" else (),
    )
    cleanup_thread.start()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        with registry._lock:
            started = registry._closing_all or "owner-1" in registry._closing_owners
        if started:
            break
        time.sleep(0.01)
    assert started
    blocked_owner = "owner-2" if cleanup == "close_all" else "owner-1"
    assert registry.list_pages(
        context={
            "context_id": "ctx-new",
            "surfaces": [{"surface_key": "p1", "binding_id": "binding-3"}],
        },
        owner_id=blocked_owner,
    )["reason_code"] == "owner_closing"
    rejected = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )
    assert rejected["reason_code"] in {"page_in_use", "owner_closing"}
    blocking.finish.set()
    action_thread.join(2)
    cleanup_thread.join(2)
    assert not action_thread.is_alive()
    assert not cleanup_thread.is_alive()
    assert registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", owner_id="owner-2",
    )["frame_id"] == "frame-1"


def test_close_failure_still_releases_page_lease_and_context():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    class _CloseFails(_Adapter):
        def close(self, session):
            super().close(session)
            raise RuntimeError("close failed")

    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": _CloseFails("open_claude_chrome"),
    }
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-1"},
    )

    with pytest.raises(RuntimeError, match="close failed"):
        registry.execute(
            command="close",
            web_session_id=observed["web_session_id"],
            owner_id="owner-1",
        )

    retry = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-1", owner_id="owner-2",
    )
    assert retry["frame_id"] == "frame-1"
    assert released == ["ctx-1"]


def test_close_all_releases_sessions_capabilities_and_page_leases():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-session"},
    )
    context = {
        "context_id": "ctx-capability",
        "surfaces": [{"surface_key": "p1", "binding_id": "binding-2"}],
    }
    token = registry.list_pages(
        context=context, owner_id="owner-2",
    )["pages"][0]["page_context_token"]

    registry.close_all()

    assert set(released) == {"ctx-session", "ctx-capability"}
    assert registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-1", owner_id="owner-3",
    )["frame_id"] == "frame-1"
    assert registry.execute(
        command="observe", backend="playwright_mcp",
        owner_id="owner-2", page_context_token=token,
    )["reason_code"] == "page_context_not_found"


def test_public_web_use_schema_is_command_based_and_legacy_name_is_hidden():
    from openprogram.providers.types import ToolCall
    from openprogram.providers.utils.validation import validate_tool_arguments
    from openprogram.programs import agent_tools

    names = {item.name for item in agent_tools(names=["web_use", "computer_use"])}
    assert names == {"web_use"}
    tool = next(
        item for item in agent_tools(names=["web_use"])
        if item.name == "web_use"
    )
    properties = tool.parameters["properties"]
    assert properties["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert properties["backend"]["enum"] == [
        "", "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]
    assert "task" not in properties
    assert "web_session_id" in properties
    assert "computer_session_id" not in properties
    assert tool.parameters["required"] == ["command"]
    act_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "act"
    )
    act_arguments = act_rule["properties"]["arguments"]
    assert "web_session_id" not in (act_rule.get("required") or [])
    assert "action" in properties
    assert "expected_frame_id" not in act_arguments.get("required", [])
    assert act_arguments["additionalProperties"] is False
    verify_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "verify"
    )
    assert verify_rule["required"] == ["web_session_id"]
    assert verify_rule["properties"]["arguments"]["required"] == [
        "assertion", "value",
    ]
    close_rule = next(
        rule["then"] for rule in tool.parameters["allOf"]
        if rule["if"]["properties"]["command"].get("const") == "close"
    )
    assert close_rule["required"] == ["web_session_id"]

    retried_list_pages = {
        "command": "list_pages",
        "backend": "",
        "page": "",
        "page_context_token": "",
        "web_session_id": "",
    }
    assert validate_tool_arguments(
        tool,
        ToolCall(
            id="call-list-pages-retry",
            name="web_use",
            arguments=retried_list_pages,
        ),
    ) == retried_list_pages

    session_act = {
        "command": "act",
        "backend": "playwright_mcp",
        "page": "",
        "page_context_token": "page_ctx_9516a306add9441fbae27d4e394a153c",
        "web_session_id": "pending",
        "arguments": {},
    }
    assert validate_tool_arguments(
        tool,
        ToolCall(id="call-act-pending", name="web_use", arguments=session_act),
    ) == session_act

    lifted = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-act-top-level",
            name="web_use",
            arguments={
                "command": "act",
                "web_session_id": "cs_1",
                "action": "navigate",
                "url": "https://example.test/",
            },
        ),
    )
    assert lifted["arguments"]["action"] == "navigate"
    assert lifted["arguments"]["url"] == "https://example.test/"
    assert "action" not in lifted or lifted.get("action") is None

    opened = validate_tool_arguments(
        tool,
        ToolCall(
            id="call-act-open",
            name="web_use",
            arguments={
                "command": "act",
                "action": "navigate",
                "url": "https://example.test/",
            },
        ),
    )
    assert opened["arguments"]["action"] == "navigate"
    assert opened["arguments"]["url"] == "https://example.test/"
    assert "url" not in lifted


def test_openprogram_mcp_exposes_only_web_use_as_browser_control_tool():
    from openprogram.mcp.server.contracts import get_mcp_tools

    tools = {tool.name: tool for tool in get_mcp_tools()}
    assert "web_use" in tools
    assert "computer_use" not in tools
    schema = tools["web_use"].inputSchema
    assert schema["properties"]["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert "web_session_id" in schema["properties"]


class _Page:
    def __init__(self) -> None:
        self.marker_name = ""
        self.marker_value = ""

    def evaluate(self, script, arg=None):
        if "Object.defineProperty" in script:
            self.marker_name, self.marker_value = arg
        return None


class _Controller:
    def __init__(self) -> None:
        self.binding_id = ""
        self.page = _Page()
        self.frame = {
            "frame_id": "frame-1", "url": "https://example.test/",
            "target": {"tab_id": "tab-1", "target_id": "target-1"},
        }
        self.invalidated = 0
        self.closed = 0

    def _page(self):
        return self.page

    def evaluate_bound_page(self, script, arg=None):
        return self.page.evaluate(script, arg)

    def execute(self, **params):
        if params["action"] == "observe":
            return dict(self.frame)
        return {"passed": True}

    def _require_fresh(self, frame_id):
        return None if frame_id == self.frame["frame_id"] else {
            "ok": False, "reason_code": "stale_observation",
        }

    def prepare_external_action(self, arguments):
        rejected = self._require_fresh(arguments.get("expected_frame_id"))
        return rejected if rejected is not None else self._write_allowed()

    def _invalidate_frame(self):
        self.invalidated += 1

    def invalidate_external_frame(self):
        return self._invalidate_frame()

    def _write_allowed(self):
        return None

    def _mutated(self, detail):
        self.invalidated += 1
        return {"ok": True, "detail": detail, "observe_required": True}

    def record_external_mutation(self, detail):
        return self._mutated(detail)

    def close(self):
        self.closed += 1


class _Result:
    def __init__(self, text="", structured=None, error=False) -> None:
        self.content = [SimpleNamespace(text=text)] if text else []
        self.structuredContent = structured
        self.isError = error


class _PlaywrightClient:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.selected = 0
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "browser_snapshot":
            return _Result('- button "Save" [ref=e7]')
        return _Result("done")

    def close(self):
        pass


def test_official_playwright_adapter_binds_marker_and_routes_one_action(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSession,
    )
    from openprogram.programs.workflow.browser.mcp_backends import (
        OfficialMCPPageBackend,
    )
    from openprogram.programs.tools.web.browser import _chrome_bootstrap

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    monkeypatch.setattr(_chrome_bootstrap, "desktop_app_ws_url", lambda: "ws://cdp")
    commands = []
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda command: commands.append(command) or client,
    )
    session = WebUseSession("cs-1", "playwright_mcp", "binding-1")

    observed = adapter.observe(session, {})
    assert observed["aria_snapshot"] == '- button "Save" [ref=e7]'
    assert session.state["upstream_page"] == 0
    assert session.state["target_id"] == "target-1"
    assert all(name != "browser_tabs" for name, _ in client.calls)
    assert "target-1" in commands[0]
    assert not any("bringToFront" in part for part in commands[0])
    acted = adapter.act(session, {
        "action": "click", "expected_frame_id": "frame-1", "ref": "e7",
    })
    assert acted["ok"] is True
    assert ("browser_click", {"target": "e7"}) in client.calls
    assert controller.invalidated == 1


def test_registry_binds_session_to_owner_and_consumes_exact_page_capability():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    context = {
        "context_id": "ctx-1",
        "surfaces": [{
            "surface_key": "p1", "binding_id": "binding-1",
            "capabilities": ["observe", "interact"],
        }],
    }
    pages = registry.list_pages(context=context, owner_id="mcp:connection-a")
    token = pages["pages"][0]["page_context_token"]

    denied = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-b", page_context_token=token,
    )
    assert denied["reason_code"] == "page_context_owner_mismatch"

    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-a", page_context_token=token,
    )
    session_id = observed["web_session_id"]
    reused = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-a", page_context_token=token,
    )
    assert reused["reason_code"] == "page_context_consumed"
    wrong_owner = registry.execute(
        command="act", web_session_id=session_id,
        owner_id="mcp:connection-b", arguments={"action": "click"},
    )
    assert wrong_owner["reason_code"] == "web_session_owner_mismatch"
    closed = registry.execute(
        command="close", web_session_id=session_id,
        owner_id="mcp:connection-a",
    )
    assert closed["closed"] is True
    assert released == ["ctx-1"]


def test_list_pages_returns_group_aware_snapshot_with_page_tokens():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    registry = WebUseSessionRegistry(
        adapters={name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )},
        binding_validator=_allow_binding,
    )
    context = {
        "context_id": "ctx-pages",
        "window_id": "window-1",
        "primary_surface_key": "p4",
        "inventory_revision": 9,
        "active_tab_entry_id": "group:g3",
        "focused_page": "p4",
        "tab_entries": [{
            "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
            "split": {
                "axis": "horizontal", "ratio": 0.5,
                "panes": [
                    {"pane_id": "pane:g3:0", "order": 0, "page": "p3"},
                    {"pane_id": "pane:g3:1", "order": 1, "page": "p4"},
                ],
            },
        }],
        "windows": [{
            "window_id": "window-1", "inventory_revision": 9,
            "active_tab_entry_id": "group:g3", "focused_page": "p4",
            "tab_entries": [{
                "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
            }],
            "pages": ["p3", "p4"],
        }],
        "surfaces": [
            {"surface_key": "p3", "window_id": "window-1", "binding_id": "binding-3", "tab_entry_id": "group:g3", "placement": {"mode": "split", "pane_id": "pane:g3:0", "order": 0}},
            {"surface_key": "p4", "window_id": "window-1", "binding_id": "binding-4", "tab_entry_id": "group:g3", "placement": {"mode": "split", "pane_id": "pane:g3:1", "order": 1}, "focused": True},
        ],
    }

    result = registry.list_pages(context=context, owner_id="owner-1")

    assert result["browser_context_id"] == "ctx-pages"
    assert result["window_id"] == "window-1"
    assert result["inventory_revision"] == 9
    assert result["active_tab_entry_id"] == "group:g3"
    assert result["focused_page"] == "p4"
    assert result["tab_entries"] == context["tab_entries"]
    assert result["windows"] == context["windows"]
    assert [page["page"] for page in result["pages"]] == ["p4", "p3"]
    assert [page["window_id"] for page in result["pages"]] == [
        "window-1", "window-1",
    ]
    assert all(page["page_context_token"].startswith("pct_") for page in result["pages"])
    assert result["pages"][0]["placement"] == {
        "mode": "split", "pane_id": "pane:g3:1", "order": 1,
    }


def test_closing_one_page_session_keeps_sibling_page_binding_alive(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )
    from openprogram.webui.ws_actions import webtab

    owner = object()
    binding_1 = webtab.register_binding(owner, "window-1", "tab-1", "target-1")
    binding_2 = webtab.register_binding(owner, "window-1", "tab-2", "target-2")
    monkeypatch.setattr(webtab, "request_on_ws", lambda _ws, command, _timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "tab_id": command["tab_id"],
        "target_id": "target-1" if command["tab_id"] == "tab-1" else "target-2",
    })
    registry = WebUseSessionRegistry(
        adapters={name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )},
    )
    context = {
        "context_id": "ctx-pages",
        "surfaces": [
            {
                "surface_key": "p1", "aliases": ["p1"],
                "binding_id": binding_1, "tab_id": "tab-1",
                "page_key": webtab.binding_page_key(binding_1),
                **webtab.binding_revisions(binding_1),
            },
            {
                "surface_key": "p2", "aliases": ["p2"],
                "binding_id": binding_2, "tab_id": "tab-2",
                "page_key": webtab.binding_page_key(binding_2),
                **webtab.binding_revisions(binding_2),
            },
        ],
    }
    listed = registry.list_pages(context=context, owner_id="owner-1")
    first = registry.execute(
        command="observe", backend="open_claude_chrome", owner_id="owner-1",
        page_context_token=listed["pages"][0]["page_context_token"],
    )
    second = registry.execute(
        command="observe", backend="open_claude_chrome", owner_id="owner-1",
        page_context_token=listed["pages"][1]["page_context_token"],
    )

    registry.execute(
        command="close", web_session_id=first["web_session_id"],
        owner_id="owner-1",
    )
    acted = registry.execute(
        command="act", web_session_id=second["web_session_id"],
        owner_id="owner-1", arguments={
            "action": "click", "expected_frame_id": "frame-1",
        },
    )

    assert acted["ok"] is True
    assert webtab.binding_revisions(binding_1) == {}
    assert webtab.binding_revisions(binding_2)
    registry.release_owner("owner-1")


def test_failed_first_observe_releases_session_and_page_context():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    class _FailingAdapter(_Adapter):
        def observe(self, session, arguments):
            return {"ok": False, "reason_code": "target_lost"}

    adapter = _FailingAdapter("open_claude_chrome")
    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp",
    )}
    adapters["open_claude_chrome"] = adapter
    released = []
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    result = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-failed"},
    )
    session_id = result["web_session_id"]

    assert result["reason_code"] == "target_lost"
    assert registry.execute(
        command="act", web_session_id=session_id, owner_id="owner-1",
    )["reason_code"] == "web_session_not_found"
    retry = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-retry"},
    )
    assert retry["reason_code"] == "target_lost"
    assert adapter.calls == [("close", {}), ("close", {})]
    assert released == ["ctx-failed", "ctx-retry"]


def test_sync_mcp_client_cleans_thread_when_start_fails(monkeypatch):
    from openprogram.programs.workflow.browser import mcp_backends

    instances = []

    class _FailingClient:
        error = None

        def __init__(self, _config):
            self.stopped = False
            instances.append(self)

        async def start(self):
            raise RuntimeError("start failed")

        async def stop(self):
            self.stopped = True

    monkeypatch.setattr(mcp_backends, "MCPClient", _FailingClient)
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(RuntimeError, match="start failed"):
        mcp_backends._SyncMCPClient(["missing"], timeout=0.1)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and any(
        thread.name == "web-use-mcp" and thread.ident not in before
        for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert instances[0].stopped is True
    assert not any(
        thread.name == "web-use-mcp" and thread.ident not in before
        for thread in threading.enumerate()
    )


def test_registered_gui_agent_can_select_computer_use_backend(monkeypatch):
    from openprogram.programs import _runtime
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(("original", kwargs))
        return {"mode": "desktop"}

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **kwargs: calls.append(("web_use", kwargs)) or {
            "status": "succeeded", "mode": "web_use",
            "backend": kwargs["backend"],
        },
    )
    wrapped = install_gui_harness_web_use(original)
    tool = _runtime.get("gui_agent")
    assert tool is not None

    result = wrapped(
        task="click Save", backend="chrome_devtools_mcp",
        runtime=SimpleNamespace(),
    )
    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert result["infeasible_declared"] is False
    assert result["backend"] == "chrome_devtools_mcp"
    assert calls[0][0] == "web_use"


def test_programs_cli_resolves_registered_gui_agent(monkeypatch, capsys):
    from openprogram.agentic_programming.function import _registry
    from openprogram.cli.commands.programs import _cmd_run
    from openprogram.programs import gui_harness_bridge
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    monkeypatch.setitem(_registry, "gui_agent", _registry.get("gui_agent"))
    monkeypatch.setattr(
        gui_harness_bridge,
        "gui_agent",
        getattr(gui_harness_bridge, "gui_agent", None),
        raising=False,
    )

    wrapped = install_gui_harness_web_use(
        lambda **_kwargs: {
            "status": "succeeded",
            "success": True,
            "summary": "inspected",
        },
    )

    _cmd_run("gui_agent", ["task=inspect"])

    assert gui_harness_bridge.gui_agent is wrapped
    assert "'status': 'succeeded'" in capsys.readouterr().out


def test_registered_gui_agent_browser_surface_uses_default_backend(monkeypatch):
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser.web_use_runtime import DEFAULT_BACKEND
    from openprogram.programs.gui_harness_bridge import (
        DEFAULT_MAX_STEPS,
        install_gui_harness_web_use,
    )

    calls = []

    def original(**_kwargs):
        raise AssertionError("browser surface must not call desktop harness")

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **kwargs: calls.append(kwargs) or {
            "status": "succeeded",
            "reason_code": "verified",
            "summary": "done",
            "backend": kwargs["backend"],
        },
    )

    runtime = object()
    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="inspect the page", surface="browser", runtime=runtime)

    assert result["success"] is True
    assert result["backend"] == DEFAULT_BACKEND
    assert calls == [{
        "task": "inspect the page",
        "backend": DEFAULT_BACKEND,
        "max_steps": DEFAULT_MAX_STEPS,
        "max_seconds": None,
        "runtime": runtime,
    }]


@pytest.mark.parametrize(
    (
        "close_result", "runtime_behavior", "teardown_raises",
        "expected_status", "expected_success",
    ),
    [
        ({"ok": True}, "verify", False, "succeeded", True),
        ({"ok": True}, "raise", False, None, None),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "verify",
            False,
            "infeasible",
            False,
        ),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "miss",
            False,
            "infeasible",
            False,
        ),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "raise",
            False,
            "infeasible",
            False,
        ),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "observe_cancel",
            True,
            "infeasible",
            False,
        ),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "screenshot_timeout",
            True,
            "infeasible",
            False,
        ),
        (
            {"ok": True},
            "observe_cancel",
            True,
            None,
            None,
        ),
        (
            {
                "ok": False,
                "reason_code": "desktop_unavailable",
                "error": "the background Page could not be closed",
            },
            "cancel",
            True,
            "infeasible",
            False,
        ),
        ({"ok": True}, "cancel", True, None, None),
    ],
)
def test_registered_gui_agent_without_page_opens_background_page(
    monkeypatch, close_result, runtime_behavior, teardown_raises,
    expected_status, expected_success,
):
    from openprogram.agent import surface_context
    from openprogram.agentic_programming.function import CancelledError
    from openprogram.programs._execution_common import ToolReturn
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import DEFAULT_BACKEND
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )
    from openprogram.providers.utils.errors import ExecInterrupt

    context = {
        "context_id": "ctx-empty",
        "window_id": "window-1",
        "surfaces": [],
    }
    opened_context = {
        "context_id": "ctx-opened",
        "window_id": "window-1",
        "surfaces": [{
            "binding_id": "surface-opened",
            "page_key": "page-opened",
            "capabilities": ["observe", "interact", "navigate"],
        }],
    }
    opens = []
    closed = []
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                if runtime_behavior == "observe_cancel":
                    raise CancelledError("cancelled during first observe")
                return {"frame_id": "f1", "web_session_id": "cs-opened"}
            if kwargs["command"] == "verify":
                return {"passed": True}
            if (
                kwargs["command"] == "act"
                and kwargs.get("arguments", {}).get("action") == "screenshot"
            ):
                return ToolReturn(
                    images=[b"png"],
                    json_data={"frame_id": "f1"},
                )
            if kwargs["command"] == "close" and teardown_raises:
                raise RuntimeError("registry close failed")
            return {"ok": True, "closed": True}

        def revoke_screenshot(self, _session_id):
            if runtime_behavior == "screenshot_timeout":
                raise RuntimeError("screenshot revoke failed")

        def release_owner(self, _owner_id):
            if teardown_raises:
                raise RuntimeError("registry owner close failed")
            return None

    class _Runtime:
        def exec(self, **kwargs):
            if runtime_behavior == "raise":
                raise RuntimeError("model transport failed")
            if runtime_behavior == "cancel":
                raise ExecInterrupt("cancelled during model execution")
            if runtime_behavior == "screenshot_timeout":
                asyncio.run(kwargs["tools"][0].execute(
                    "call-1",
                    {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(),
                    None,
                ))
                return "The screenshot was captured."
            if runtime_behavior == "verify":
                asyncio.run(kwargs["tools"][0].execute(
                    "call-1",
                    {
                        "action": "verify",
                        "expected_frame_id": "f1",
                        "assertion": "title_contains",
                        "value": "Google",
                    },
                    asyncio.Event(),
                    None,
                ))
            return "The background Page title is Google."

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: context,
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url, **kwargs: opens.append((url, kwargs)) or opened_context,
    )
    monkeypatch.setattr(
        surface_context, "resolve_binding", lambda _page="": "surface-opened",
    )
    monkeypatch.setattr(
        surface_context, "resolve_page_key", lambda _page="": "page-opened",
    )
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )
    monkeypatch.setattr(
        surface_context,
        "close_page",
        lambda value: closed.append(value) or close_result,
    )

    wrapped = install_gui_harness_web_use(
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser surface must not call desktop harness")
        )
    )
    call_kwargs = {
        "task": "inspect the page",
        "surface": "browser",
        "runtime": _Runtime(),
    }
    if runtime_behavior == "screenshot_timeout":
        ticks = iter((0.0, 0.0, 2.0))
        release_screenshot = browser_module._release_screenshot_payload

        def fail_after_screenshot_release(content, result):
            release_screenshot(content, result)
            raise RuntimeError("screenshot payload release failed")

        monkeypatch.setattr(
            browser_module,
            "time",
            SimpleNamespace(monotonic=lambda: next(ticks, 2.0)),
        )
        monkeypatch.setattr(
            browser_module,
            "_release_screenshot_payload",
            fail_after_screenshot_release,
        )
        call_kwargs["max_seconds"] = 1
    if expected_status is None:
        expected_error = (
            CancelledError
            if runtime_behavior == "observe_cancel"
            else ExecInterrupt
            if runtime_behavior == "cancel"
            else RuntimeError
        )
        with pytest.raises(expected_error, match="cancelled|model transport"):
            wrapped(**call_kwargs)
        result = None
    else:
        result = wrapped(**call_kwargs)

    if result is not None:
        assert result["status"] == expected_status
        assert result["success"] is expected_success
        assert result["backend"] == DEFAULT_BACKEND
    assert opens == [(
        "https://www.google.com/",
        {"window_id": "window-1", "background": True},
    )]
    assert closed == [opened_context]
    assert context in released
    if expected_success is False:
        assert result["reason_code"] == "page_cleanup_failed"
        assert result["infeasible_declared"] is True
        assert "Close the remaining background Page" in result[
            "handoff_instruction"
        ]


def test_registered_gui_agent_reuses_existing_origin_page(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    window_context = surface_context.window_context("window-1")
    existing_context = {
        "context_id": "ctx-existing",
        "window_id": "window-1",
        "primary_surface_key": "p1",
        "surfaces": [{
            "surface_key": "p1",
            "window_id": "window-1",
            "tab_id": "tab-existing",
            "binding_id": "surface-existing",
            "page_key": "page-existing",
            "capabilities": ["observe", "interact", "navigate"],
        }],
    }
    captures = []
    opened = []

    class _Registry:
        def list_pages(self, **kwargs):
            assert kwargs["context"] is existing_context
            return {
                "ok": True,
                "pages": [
                    {
                        "window_id": "window-1",
                        "tab_id": "tab-existing",
                        "title": "Existing",
                        "visible": True,
                        "focused": False,
                        "page_context_token": "pct-existing",
                    },
                    {
                        "window_id": "window-1",
                        "tab_id": "tab-other",
                        "title": "Other",
                        "visible": True,
                        "focused": True,
                        "page_context_token": "pct-other",
                    },
                ],
            }

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                assert kwargs["page_context_token"] == "pct-existing"
                return {"frame_id": "f1", "web_session_id": "cs-existing"}
            if kwargs["command"] == "verify":
                return {"passed": True}
            return {"ok": True, "closed": True}

        def release_owner(self, _owner_id):
            return None

    class _Runtime:
        def exec(self, **kwargs):
            asyncio.run(kwargs["tools"][0].execute(
                "call-1",
                {
                    "action": "verify",
                    "expected_frame_id": "f1",
                    "assertion": "title_contains",
                    "value": "Existing",
                },
                asyncio.Event(),
                None,
            ))
            return "The existing Page was verified."

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: window_context)

    def capture(context=None):
        captures.append(context)
        return existing_context

    monkeypatch.setattr(surface_context, "capture_pages", capture)
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda *_args, **_kwargs: opened.append((_args, _kwargs)) or {},
    )

    result = install_gui_harness_web_use(
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser surface must not call desktop harness")
        )
    )(
        task="inspect the page", surface="browser", runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert captures[0] is window_context
    assert opened == []


def test_gui_agent_inventory_failure_does_not_open_another_page(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    context = surface_context.window_context("window-1")
    opened = []

    class _Registry:
        def release_owner(self, _owner_id):
            return None

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: context)
    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda _context=None: (_ for _ in ()).throw(
            RuntimeError("Page inventory transport failed")
        ),
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda *_args, **_kwargs: opened.append((_args, _kwargs)) or {},
    )

    result = module._run_browser_task_commands(
        task="inspect", backend="playwright_mcp",
        max_steps=1, max_seconds=10, runtime=SimpleNamespace(),
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "page_context_stale"
    assert "inventory" in result["summary"].lower()
    assert result["handoff_instruction"]
    assert opened == []


def test_gui_agent_preserves_background_open_timeout_handoff(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.gui_harness_bridge import install_gui_harness_web_use
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.webui.ws_actions import webtab

    context = surface_context.window_context("window-1")

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def release_owner(self, _owner_id):
            return None

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: context)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: context,
    )
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(webtab, "_request", lambda *_args, **_kwargs: {
        "ok": False,
        "reason_code": webtab.RESPONSE_TIMEOUT_REASON_CODE,
        "error": "timeout: no desktop shell replied within 15s",
    })

    wrapped = install_gui_harness_web_use(
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser surface must not call desktop harness")
        )
    )
    result = wrapped(
        task="inspect", surface="browser", max_steps=1, max_seconds=10,
        runtime=SimpleNamespace(),
    )

    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result[
        "handoff_instruction"
    ]


def test_gui_agent_does_not_release_a_borrowed_empty_context(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    borrowed = {"context_id": "ctx-borrowed-empty", "surfaces": []}
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def release_owner(self, _owner_id):
            return None

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: borrowed)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: borrowed,
    )
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda *_args, **_kwargs: {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "desktop app unavailable",
        },
    )

    result = module._run_browser_task_commands(
        task="inspect", backend="playwright_mcp",
        max_steps=1, max_seconds=10, runtime=SimpleNamespace(),
    )

    assert result["status"] == "infeasible"
    assert result["reason_code"] == "desktop_unavailable"
    assert released == []


def test_registered_gui_agent_without_desktop_returns_infeasible_handoff(
    monkeypatch,
):
    from openprogram.agent import surface_context
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("desktop unavailable")
        ),
    )
    opens = []
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda *_args, **_kwargs: opens.append((_args, _kwargs)) or {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": "OpenProgram desktop app is not connected.",
        },
    )
    wrapped = install_gui_harness_web_use(
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("browser surface must not call desktop harness")
        )
    )

    result = wrapped(
        task="inspect the page", surface="browser", runtime=SimpleNamespace(),
    )

    assert result["status"] == "infeasible"
    assert result["success"] is False
    assert result["infeasible_declared"] is True
    assert result["reason_code"] == "desktop_unavailable"
    assert "Launch or reconnect" in result["handoff_instruction"]
    assert opens == []


def test_gui_agent_failed_first_observe_releases_its_owner(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    context = {
        "context_id": "ctx-first-observe",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe"],
        }],
    }
    inventory = {
        "context_id": "ctx-first-inventory",
        "surfaces": [{
            "binding_id": "binding-2",
            "capabilities": ["observe"],
        }],
    }
    released = []

    class _Registry:
        def __init__(self):
            self.calls = []
            self.released_owners = []

        def list_pages(self, **_kwargs):
            return {
                "ok": True,
                "pages": [{
                    "page_context_token": "pct-first",
                    "focused": True,
                }],
            }

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe":
                return {
                    "web_session_id": "cs-first",
                    "reason_code": "target_lost",
                }
            return {"ok": True, "closed": True}

        def release_owner(self, owner_id):
            self.released_owners.append(owner_id)

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "capture_pages",
        lambda current=None: context if current is None else inventory,
    )
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    result = module._run_browser_task_commands(
        task="observe", backend="playwright_mcp",
        max_steps=1, max_seconds=10, runtime=SimpleNamespace(),
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "target_lost"
    assert any(call["command"] == "close" for call in registry.calls)
    assert registry.released_owners == ["harness:ctx-first-observe"]
    assert released == [context]


def test_gui_agent_close_error_still_releases_its_owner(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    context = {
        "context_id": "ctx-close-error",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe"],
        }],
    }

    class _Registry:
        def __init__(self):
            self.released_owners = []

        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                return {"frame_id": "f1", "web_session_id": "cs-close"}
            if kwargs["command"] == "verify":
                return {"passed": True}
            if kwargs["command"] == "close":
                raise RuntimeError("close failed")
            return {"ok": True}

        def release_owner(self, owner_id):
            self.released_owners.append(owner_id)

    class _Runtime:
        def exec(self, **kwargs):
            asyncio.run(kwargs["tools"][0].execute(
                "call-1",
                {
                    "action": "verify",
                    "expected_frame_id": "f1",
                    "assertion": "text_contains",
                    "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: context)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: context,
    )
    monkeypatch.setattr(
        surface_context, "resolve_binding", lambda _page="": "binding-1",
    )
    monkeypatch.setattr(
        surface_context, "resolve_page_key", lambda _page="": "page-1",
    )

    with pytest.raises(RuntimeError, match="close failed"):
        module._run_browser_task_commands(
            task="verify", backend="playwright_mcp",
            max_steps=1, max_seconds=10, runtime=_Runtime(),
        )

    assert registry.released_owners == ["harness:ctx-close-error"]


def test_gui_agent_releases_only_the_failed_inventory_refresh(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    borrowed = {
        "context_id": "ctx-borrowed",
        "surfaces": [{
            "binding_id": "binding-borrowed",
            "capabilities": ["observe"],
        }],
    }
    inventory = {
        "context_id": "ctx-inventory",
        "surfaces": [{
            "binding_id": "binding-inventory",
            "capabilities": ["observe"],
        }],
    }
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            raise RuntimeError("inventory registration failed")

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                return {"frame_id": "f1", "web_session_id": "cs-refresh"}
            if kwargs["command"] == "verify":
                return {"passed": True}
            return {"ok": True, "closed": True}

        def release_owner(self, _owner_id):
            return None

    class _Runtime:
        def exec(self, **kwargs):
            asyncio.run(kwargs["tools"][0].execute(
                "call-1",
                {
                    "action": "verify",
                    "expected_frame_id": "f1",
                    "assertion": "text_contains",
                    "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: borrowed)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: inventory,
    )
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda context: released.append(context),
    )
    monkeypatch.setattr(
        surface_context, "resolve_binding", lambda _page="": "binding-borrowed",
    )
    monkeypatch.setattr(
        surface_context, "resolve_page_key", lambda _page="": "page-borrowed",
    )

    result = module._run_browser_task_commands(
        task="verify", backend="playwright_mcp",
        max_steps=1, max_seconds=10, runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert released == [inventory]


def test_gui_agent_app_name_does_not_select_browser_surface(monkeypatch):
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(kwargs)
        return {"status": "succeeded", "summary": "desktop"}

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("app_name must not select the browser route")
        ),
    )

    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="inspect", app_name="browser", runtime=object())

    assert result["success"] is True
    assert calls[0]["app_name"] == "browser"


def test_gui_agent_wrapper_resolves_step_budget():
    from openprogram.programs.gui_harness_bridge import (
        DEFAULT_MAX_STEPS,
        install_gui_harness_web_use,
    )

    seen = []

    def original(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    wrapped = install_gui_harness_web_use(original)
    wrapped(task="t")
    assert seen[-1]["max_steps"] == DEFAULT_MAX_STEPS
    wrapped(task="t", max_steps=0)
    assert seen[-1]["max_steps"] == 0
    wrapped(task="t", max_steps=-3)
    assert seen[-1]["max_steps"] == 0
    wrapped(task="t", max_steps=20)
    assert seen[-1]["max_steps"] == 20


def test_gui_agent_wrapper_forces_success_false_when_infeasible_declared():
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    def original(**kwargs):
        return {
            "status": "succeeded",
            "infeasible_declared": True,
            "success": True,
            "summary": "Human must log in and retry.",
        }

    wrapped = install_gui_harness_web_use(original)
    result = wrapped(task="t")
    assert result["success"] is False
    assert result["status"] == "infeasible"
    assert result["infeasible_declared"] is True
    assert result["handoff_instruction"] == "Human must log in and retry."
    assert result["summary"] == "Human must log in and retry."


def test_gui_agent_wrapper_calls_raw_harness_function_once():
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )

    calls = []

    def raw_harness(**kwargs):
        calls.append(kwargs)
        return {"success": True, "summary": "done"}

    def decorated_harness(**_kwargs):
        raise AssertionError("bridge called the decorated harness wrapper")

    decorated_harness.__wrapped__ = raw_harness
    wrapped = install_gui_harness_web_use(decorated_harness)

    result = wrapped(task="t")
    assert result["status"] == "succeeded"
    assert result["success"] is True
    assert result["summary"] == "done"
    assert len(calls) == 1


def test_gui_agent_wrapper_records_one_public_gui_agent_node(tmp_path):
    from openprogram.agentic_programming.function import agentic_function
    from openprogram.agentic_programming.runtime import Runtime
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_web_use,
    )
    from openprogram.store import SessionNodeWriter, SessionStore, _store

    @agentic_function
    def gui_step(task, runtime=None):
        return {"task": task, "success": True, "summary": "done"}

    @agentic_function
    def gui_agent(task, runtime=None, **_kwargs):
        return gui_step(task, runtime=runtime)

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s1", agent_id="main")
    writer = SessionNodeWriter(store, "s1")
    token = _store.set(writer)
    try:
        wrapped = install_gui_harness_web_use(gui_agent)
        wrapped(task="t", runtime=Runtime(call=lambda *_a, **_k: "", model="dummy"))
    finally:
        _store.reset(token)

    names = [node.name for node in writer.load() if node.is_code()]
    assert names.count("gui_agent") == 1
    assert names.count("gui_step") == 1


def test_direct_list_pages_releases_capture_when_registry_rejects(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    context = {"context_id": "ctx-rejected", "surfaces": []}
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": False, "reason_code": "owner_closing", "pages": []}

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "capture_pages", lambda: context)
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    result = module.execute_direct_web_use(
        {"command": "list_pages"}, owner_id="mcp:closing",
    )
    assert result["reason_code"] == "owner_closing"
    assert released == [context]


def test_direct_list_pages_returns_empty_inventory_without_mounted_page(monkeypatch):
    from openprogram.programs.workflow import browser as module
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    class _Owner:
        pass

    owner = _Owner()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    asyncio.run(webtab.handle_webtab_register(owner, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "pages": [],
    })

    result = module.execute_direct_web_use(
        {"command": "list_pages"}, owner_id="mcp:empty-window",
    )

    assert result["ok"] is True
    assert result["pages"] == []
    assert result["tab_entries"] == []
    assert result["active_tab_entry_id"] == ""
    assert result["focused_page"] == ""

    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, timeout=5.0: {
        "ok": True,
        "window_id": "window-1",
        "pages": [{}],
    })
    with pytest.raises(RuntimeError, match="no valid Page"):
        module.execute_direct_web_use(
            {"command": "list_pages"}, owner_id="mcp:invalid-window",
        )
    webtab.release_connection(owner)


def test_public_page_token_keeps_the_turn_owner_across_calls(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.agent.run_control import (
        reset_current_session_id, set_current_session_id,
    )
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )
    from openprogram.store import _current_turn_id

    turn_context = {"context_id": "turn-1", "surfaces": []}
    inventory_context = {"context_id": "inventory-1", "surfaces": []}

    class _Registry:
        def __init__(self):
            self.owners = []

        def list_pages(self, **kwargs):
            self.owners.append(kwargs["owner_id"])
            return {"ok": True, "pages": [{"page_context_token": "pct-popup"}]}

        def execute(self, **kwargs):
            self.owners.append(kwargs["owner_id"])
            return {"frame_id": "frame-popup", "web_session_id": "cs-popup"}

    registry = _Registry()
    captures = []
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: turn_context)

    def capture_pages(context=None):
        captures.append(context)
        return inventory_context

    monkeypatch.setattr(surface_context, "capture_pages", capture_pages)

    session_token = set_current_session_id("chat-1")
    turn_token = _current_turn_id.set("turn-1")
    try:
        listed = module.web_use(command="list_pages")
        observed = module.web_use(
            command="observe", backend="playwright_mcp",
            page_context_token=listed["pages"][0]["page_context_token"],
        )
    finally:
        _current_turn_id.reset(turn_token)
        reset_current_session_id(session_token)

    assert observed["web_session_id"] == "cs-popup"
    assert registry.owners == ["turn:chat-1:turn-1", "turn:chat-1:turn-1"]
    assert captures == [None]


@pytest.mark.parametrize("route", ["harness", "public"])
def test_temporary_page_capture_is_released_when_lease_rejects(monkeypatch, route):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    context = {
        "context_id": "ctx-temp",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe"],
        }],
    }
    released = []

    class _Registry:
        def execute(self, **_kwargs):
            return {"ok": False, "reason_code": "page_in_use"}

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "capture_pages" if route == "harness" else "capture_active",
        lambda *_args: context,
    )
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "binding-1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "page-1")
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    if route == "harness":
        result = module._run_browser_task_commands(
            task="observe", backend="playwright_mcp",
            max_steps=1, max_seconds=10, runtime=SimpleNamespace(),
        )
        assert result["reason_code"] == "page_in_use"
    else:
        result = module.web_use(
            command="observe", backend="playwright_mcp",
        )
        assert result["reason_code"] == "page_in_use"
    assert released == [context]


def test_same_owner_repeated_observe_reuses_exact_page_session():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    released = []
    registry = WebUseSessionRegistry(
        adapters={name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )},
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    first = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-1", page_key="page-1", owner_id="turn:one",
        page_context={"context_id": "ctx-first"},
    )
    repeated = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-2", page_key="page-1", owner_id="turn:one",
        page_context={"context_id": "ctx-unused"},
    )
    other_owner = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-3", page_key="page-1", owner_id="turn:two",
        page_context={"context_id": "ctx-other"},
    )

    assert repeated["web_session_id"] == first["web_session_id"]
    assert repeated["session_reused"] is True
    assert other_owner == {"ok": False, "reason_code": "page_in_use"}
    registry.execute(
        command="close", web_session_id=first["web_session_id"],
        owner_id="turn:one",
    )
    assert released == ["ctx-first"]


def test_public_repeated_observe_releases_unused_capture(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    context = {"context_id": "ctx-unused", "surfaces": [{}]}
    released = []

    class _Registry:
        def execute(self, **_kwargs):
            return {
                "ok": True, "frame_id": "frame-2",
                "web_session_id": "cs-existing", "session_reused": True,
            }

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "capture_active", lambda: context)
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "binding-2")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "page-1")
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    result = module.web_use(
        command="observe", backend="playwright_mcp", page="p1",
    )

    assert result["web_session_id"] == "cs-existing"
    assert released == [context]


@pytest.mark.parametrize("route", ["harness", "public"])
def test_temporary_page_capture_is_released_when_binding_resolution_fails(
    monkeypatch, route,
):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    context = {
        "context_id": "ctx-temp",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe"],
        }],
    }
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def execute(self, **_kwargs):
            raise AssertionError("binding resolution must happen first")

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "capture_pages" if route == "harness" else "capture_active",
        lambda *_args: context,
    )
    monkeypatch.setattr(
        surface_context, "resolve_binding",
        lambda _page="": (_ for _ in ()).throw(RuntimeError("binding failed")),
    )
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    with pytest.raises(RuntimeError, match="binding failed"):
        if route == "harness":
            module._run_browser_task_commands(
                task="observe", backend="playwright_mcp",
                max_steps=1, max_seconds=10, runtime=SimpleNamespace(),
            )
        else:
            module.web_use(command="observe", backend="playwright_mcp")
    assert released == [context]


def test_official_backend_rejects_stale_frame_before_upstream_call(monkeypatch):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSession,
    )
    from openprogram.programs.workflow.browser.mcp_backends import (
        OfficialMCPPageBackend,
    )

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda _command: client,
    )
    session = WebUseSession("cs-1", "playwright_mcp", "binding-1")
    session.controller = controller
    session.state["mcp_client"] = client
    before = list(client.calls)
    result = adapter.act(session, {
        "action": "click", "expected_frame_id": "old", "ref": "e7",
    })
    assert result["reason_code"] == "stale_observation"
    assert client.calls == before


def test_gui_agent_harness_uses_selected_computer_use_backend(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    class _Registry:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "web_session_id": "cs-1",
                    "backend": kwargs.get("backend") or "chrome_devtools_mcp",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True, "backend": "chrome_devtools_mcp"}
            return {"ok": True}

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    context = {
        "context_id": "ctx-1",
        "surfaces": [{
            "binding_id": "binding-1",
            "capabilities": ["observe", "interact", "navigate"],
        }],
    }
    monkeypatch.setattr(surface_context, "current", lambda: context)
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "binding-1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "page-1")

    class _Runtime:
        def exec(self, **kwargs):
            tool = kwargs["tools"][0]
            asyncio.run(tool.execute(
                "call-1",
                {
                    "action": "verify", "expected_frame_id": "frame-1",
                    "page_context_token": "pct-current",
                    "assertion": "text_contains", "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    result = module.browser_agent(
        task="Verify the page",
        backend="chrome_devtools_mcp",
        runtime=_Runtime(),
    )
    assert result["status"] == "succeeded"
    verify_call = next(
        call for call in registry.calls if call["command"] == "verify"
    )
    assert "page_context_token" not in verify_call["arguments"]
    assert registry.calls[0] == {
        "command": "observe",
        "backend": "chrome_devtools_mcp",
        "binding_id": "binding-1",
        "page_key": "page-1",
        "owner_id": "harness:ctx-1",
        "page_context": context,
    }
    assert registry.calls[-1] == {
        "command": "close", "web_session_id": "cs-1",
        "owner_id": "harness:ctx-1",
    }


def test_gui_agent_prompt_receives_group_aware_page_inventory(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    inventory_context = {
        "context_id": "ctx-pages",
        "window_id": "window-1",
        "inventory_revision": 9,
        "active_tab_entry_id": "group:g3",
        "focused_page": "p4",
        "tab_entries": [{
            "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
        }],
        "windows": [
            {
                "window_id": "window-1", "inventory_revision": 9,
                "active_tab_entry_id": "group:g3", "focused_page": "p4",
                "tab_entries": [{
                    "id": "group:g3", "mode": "split", "pages": ["p3", "p4"],
                }],
                "pages": ["p3", "p4"],
            },
            {
                "window_id": "window-2", "inventory_revision": 4,
                "active_tab_entry_id": "tab:tab-d", "focused_page": "p5",
                "tab_entries": [{
                    "id": "tab:tab-d", "mode": "single", "pages": ["p5"],
                }],
                "pages": ["p5"],
            },
        ],
        "surfaces": [],
    }

    class _Registry:
        def list_pages(self, **_kwargs):
            return {
                "ok": True,
                "browser_context_id": "ctx-pages",
                "window_id": "window-1",
                "inventory_revision": 9,
                "active_tab_entry_id": "group:g3",
                "focused_page": "p4",
                "tab_entries": inventory_context["tab_entries"],
                "windows": inventory_context["windows"],
                "pages": [
                    {"page": "p3", "window_id": "window-1", "tab_id": "tab-c", "visible": True, "focused": False, "page_context_token": "pct_3"},
                    {"page": "p4", "window_id": "window-1", "tab_id": "tab-d", "visible": True, "focused": True, "page_context_token": "pct_4"},
                    {"page": "p5", "window_id": "window-2", "tab_id": "tab-d", "visible": True, "focused": True, "page_context_token": "pct_5"},
                ],
            }

        def execute(self, **kwargs):
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "web_session_id": "cs-1",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True}
            return {"ok": True, "closed": True}

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: inventory_context)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: inventory_context,
    )
    prompts = []

    class _Runtime:
        def exec(self, **kwargs):
            prompts.append(kwargs["content"][0]["text"])
            asyncio.run(kwargs["tools"][0].execute(
                "call-1",
                {
                    "action": "verify", "expected_frame_id": "frame-1",
                    "assertion": "text_contains", "value": "done",
                },
                asyncio.Event(),
                None,
            ))
            return "verified"

    result = module._run_browser_task_commands(
        task="Verify the split page", backend="chrome_devtools_mcp",
        max_steps=2, max_seconds=30, runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert '"active_tab_entry_id": "group:g3"' in prompts[0]
    assert '"pages": ["p3", "p4"]' in prompts[0]
    assert '"page": "p4"' in prompts[0]
    assert '"window_id": "window-2"' in prompts[0]
    assert '"page": "p5"' in prompts[0]
    assert prompts[0].count('"bound": true') == 1


def test_gui_agent_discovers_popup_and_switches_by_exact_page_token(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    initial = {"context_id": "ctx-pages", "surfaces": [{"surface_key": "p1"}]}
    popup = {"context_id": "ctx-popup", "surfaces": [{"surface_key": "p2"}]}
    captures = []
    released = []

    def capture_pages(context=None):
        captures.append(context)
        return initial if len(captures) <= 2 else popup

    class _Registry:
        def __init__(self):
            self.calls = []
            self.inventory = 0

        def list_pages(self, **kwargs):
            self.inventory += 1
            return {
                "ok": True,
                "pages": ([{
                    "page": "p1", "title": "Opener", "focused": True,
                    "visible": True, "page_context_token": "pct-a",
                }] if self.inventory == 1 else [{
                    "page": "p1", "title": "Opener", "focused": False,
                    "visible": False, "page_context_token": "pct-a2",
                }, {
                    "page": "p2", "title": "Popup", "focused": True,
                    "visible": True, "opener_tab_id": "tab-a",
                    "page_context_token": "pct-c",
                }]),
            }

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe" and kwargs.get("page_context_token") == "pct-c":
                return {"frame_id": "frame-c", "web_session_id": "cs-c"}
            if kwargs["command"] == "observe" and not kwargs.get("web_session_id"):
                return {"frame_id": "frame-a", "web_session_id": "cs-a"}
            if kwargs["command"] == "observe":
                return {"frame_id": "frame-a2", "web_session_id": "cs-a"}
            if kwargs["command"] == "act":
                return {"ok": True, "observe_required": True}
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True}
            return {"ok": True, "closed": True}

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "capture_pages", capture_pages)
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda context: released.append(context),
    )

    class _Runtime:
        calls = 0

        def exec(self, **kwargs):
            self.calls += 1
            tool = kwargs["tools"][0]
            if self.calls == 1:
                args = {"action": "click", "expected_frame_id": "frame-a", "ref": "e1"}
            elif self.calls == 2:
                assert "Popup" in kwargs["content"][0]["text"]
                args = {"action": "switch_page", "page_context_token": "pct-c"}
            else:
                args = {
                    "action": "verify", "expected_frame_id": "frame-c",
                    "assertion": "text_contains", "value": "done",
                }
            asyncio.run(tool.execute("call", args, asyncio.Event(), None))
            return ""

    result = module._run_browser_task_commands(
        task="Open the popup", backend="playwright_mcp",
        max_steps=3, max_seconds=30, runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert any(
        call.get("page_context_token") == "pct-c"
        for call in registry.calls
    )
    assert any(
        call["command"] == "close" and call.get("web_session_id") == "cs-a"
        for call in registry.calls
    )
    assert captures[:2] == [None, initial]
    assert released == [initial]


def test_gui_harness_screenshot_capability_is_one_request_only(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    class _Registry:
        def __init__(self):
            self.revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act" and kwargs["arguments"]["action"] == "screenshot":
                return ToolReturn(images=[b"png"], json_data={"frame_id": "f1"})
            if command == "verify":
                return {"passed": True}
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        def __init__(self):
            self.requests = []
            self.contents = []
            self.tool_results = []

        def exec(self, **kwargs):
            self.requests.append([block["type"] for block in kwargs["content"]])
            self.contents.append(kwargs["content"])
            index = len(self.requests)
            if index == 1:
                self.tool_results.append(asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                )))
            elif index == 2:
                assert "b'png'" not in kwargs["content"][0]["text"]
            elif index == 3:
                asyncio.run(kwargs["tools"][0].execute(
                    "c3", {
                        "action": "verify", "expected_frame_id": "f1",
                        "assertion": "text_contains", "value": "done",
                    }, asyncio.Event(), None,
                ))
            return ""

    runtime = _Runtime()
    result = module._run_browser_task_commands(
        task="visual task", backend="open_claude_chrome",
        max_steps=1, max_seconds=30, runtime=runtime,
    )
    assert result["status"] == "succeeded"
    assert runtime.requests == [["text"], ["text", "image"], ["text"]]
    assert [block["type"] for block in runtime.contents[1]] == ["text"]
    assert [block.type for block in runtime.tool_results[0].content] == ["text"]
    assert set(json.loads(runtime.tool_results[0].content[0].text)) == {
        "frame_id", "image_attached",
    }
    assert registry.revoked == 1


def test_gui_harness_releases_unsent_final_screenshot(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )

    captured = {}

    class _Registry:
        revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act":
                captured["result"] = ToolReturn(
                    images=[b"png"], json_data={"frame_id": "f1"},
                )
                return captured["result"]
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        calls = 0

        def exec(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                ))
            return ""

    result = module._run_browser_task_commands(
        task="visual task", backend="open_claude_chrome",
        max_steps=1, max_seconds=30, runtime=_Runtime(),
    )

    assert result["reason_code"] == "tool_not_executed"
    assert result["summary"] == (
        "The model did not execute the required browser_page tool call."
    )
    assert captured["result"].images == []
    assert registry.revoked == 1


@pytest.mark.parametrize("cancelled", [False, True])
def test_gui_harness_releases_same_request_screenshot_on_runtime_error(
    monkeypatch, cancelled,
):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import (
        web_use_runtime,
    )
    from openprogram.providers.utils.errors import ExecInterrupt

    captured = {}

    class _Registry:
        revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "web_session_id": "cs1"}
            if command == "act":
                captured["result"] = ToolReturn(
                    images=[b"png"], json_data={"frame_id": "f1"},
                )
                return captured["result"]
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {
        "context_id": "ctx",
        "surfaces": [{
            "binding_id": "b1",
            "capabilities": ["observe"],
        }],
    })
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        def exec(self, **kwargs):
            asyncio.run(kwargs["tools"][0].execute(
                "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                asyncio.Event(), None,
            ))
            if cancelled:
                raise ExecInterrupt("cancelled after tool execution")
            raise RuntimeError("provider failed after tool execution")

    expected_error = ExecInterrupt if cancelled else RuntimeError
    with pytest.raises(expected_error, match="cancelled|provider failed"):
        module._run_browser_task_commands(
            task="visual task", backend="open_claude_chrome",
            max_steps=1, max_seconds=30, runtime=_Runtime(),
        )

    assert captured["result"].images == []
    assert registry.revoked == 1


def test_act_fills_expected_frame_id_and_resolves_pending_session():
    from openprogram.programs.workflow.browser.web_use_runtime import (
        WebUseSessionRegistry,
    )

    adapters = {
        name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )
    }
    registry = WebUseSessionRegistry(
        adapters=adapters, binding_validator=_allow_binding,
    )
    observed = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-1", owner_id="owner-1",
    )
    acted = registry.execute(
        command="act", web_session_id="pending", owner_id="owner-1",
        arguments={"action": "click", "ref": "e1"},
    )
    assert acted["ok"] is True
    assert acted["web_session_id"] == observed["web_session_id"]
    assert adapters["playwright_mcp"].calls[-1] == (
        "act",
        {"action": "click", "expected_frame_id": "frame-1", "ref": "e1"},
    )
    assert registry.execute(
        command="act", web_session_id="pending", owner_id="owner-missing",
        arguments={"action": "click", "ref": "e1"},
    )["reason_code"] == "web_session_not_found"


def test_observe_with_page_token_does_not_capture_active(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    captures = []

    class _Registry:
        def execute(self, **kwargs):
            return {"ok": False, "reason_code": "page_context_not_found"}

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "capture_active",
        lambda: captures.append("active") or {"context_id": "ctx"},
    )
    monkeypatch.setattr(
        surface_context, "capture_pages",
        lambda *_args, **_kwargs: captures.append("pages") or {"context_id": "ctx"},
    )

    result = module.web_use(
        command="observe",
        backend="playwright_mcp",
        page_context_token="page_ctx_deadbeef",
    )
    assert result["reason_code"] == "page_context_not_found"
    assert captures == []


def test_observe_with_url_opens_desktop_tab_when_no_page(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime

    opens = []

    class _Registry:
        def list_pages(self, **kwargs):
            return {
                "ok": True,
                "pages": [{"page_context_token": "pct_opened"}],
            }

        def execute(self, **kwargs):
            return {
                "ok": True,
                "web_session_id": "cs_opened",
                "frame_id": "frame-1",
                "command": kwargs["command"],
            }

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url: opens.append(url) or {
            "context_id": "page_ctx_opened",
            "surfaces": [{"binding_id": "surface_opened"}],
        },
    )
    monkeypatch.setattr(
        surface_context, "capture_active",
        lambda: (_ for _ in ()).throw(AssertionError("should open, not capture")),
    )

    result = module.web_use(
        command="observe",
        backend="playwright_mcp",
        arguments={"url": "https://example.test/form"},
    )
    assert opens == ["https://example.test/form"]
    assert result["ok"] is True
    assert result["web_session_id"] == "cs_opened"
    assert result["page_context_token"] == "pct_opened"

    opens.clear()
    acted = module.web_use(
        command="act",
        arguments={"action": "navigate", "url": "https://example.test/form"},
    )
    assert opens == ["https://example.test/form"]
    assert acted["ok"] is True
    assert acted["web_session_id"] == "cs_opened"
    assert acted["page_context_token"] == "pct_opened"


def test_act_with_url_rejects_non_http_scheme(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module

    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "open_page",
        surface_context.open_page,
    )
    result = module.web_use(
        command="act",
        arguments={"action": "navigate", "url": "file:///etc/passwd"},
    )
    assert result["ok"] is False
    assert result["reason_code"] == "unsupported_url"
    assert "SCHEME_FORBIDDEN" in result["error"]


def test_act_with_url_reports_desktop_unavailable(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module

    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url: {
            "ok": False,
            "reason_code": "desktop_unavailable",
            "error": surface_context.DESKTOP_UNAVAILABLE_ERROR,
        },
    )
    result = module.web_use(
        command="act",
        arguments={"action": "navigate", "url": "https://example.test/"},
    )
    assert result["ok"] is False
    assert result["reason_code"] == "desktop_unavailable"
    assert "Launch the desktop app" in result["error"]
    assert "background web tab" in result["error"]


@pytest.mark.parametrize("entry", ["web_use", "direct"])
def test_open_page_cleanup_contract_reaches_public_web_use_entries(
    monkeypatch, entry,
):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module

    cleanup = {
        "ok": False,
        "status": "infeasible",
        "success": False,
        "infeasible_declared": True,
        "reason_code": "page_cleanup_failed",
        "error": "close rejected",
        "summary": "The background Page could not be closed.",
        "handoff_instruction": "Close the remaining background Page.",
    }
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "open_page", lambda _url: cleanup)
    arguments = {
        "command": "act",
        "arguments": {"action": "navigate", "url": "https://example.test/"},
    }

    result = (
        module.web_use(**arguments)
        if entry == "web_use"
        else module.execute_direct_web_use(arguments, owner_id="owner-test")
    )

    assert result == cleanup
