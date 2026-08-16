from __future__ import annotations

import asyncio
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


def test_registry_supports_three_backends_and_freezes_one_per_session():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
        SUPPORTED_BACKENDS,
    )

    assert SUPPORTED_BACKENDS == (
        "playwright_mcp",
        "chrome_devtools_mcp",
        "open_claude_chrome",
    )
    adapters = {name: _Adapter(name) for name in SUPPORTED_BACKENDS}
    registry = ComputerUseSessionRegistry(
        adapters=adapters, binding_validator=_allow_binding,
    )

    observed = registry.execute(
        command="observe",
        backend="chrome_devtools_mcp",
        binding_id="binding-1",
        arguments={"detail": "interactive"},
    )
    session_id = observed["computer_session_id"]
    assert observed["backend"] == "chrome_devtools_mcp"

    mismatch = registry.execute(
        command="act",
        backend="playwright_mcp",
        computer_session_id=session_id,
        arguments={"action": "click", "ref": "e1"},
    )
    assert mismatch == {
        "ok": False,
        "reason_code": "backend_mismatch",
        "computer_session_id": session_id,
        "backend": "chrome_devtools_mcp",
    }
    assert adapters["playwright_mcp"].calls == []

    acted = registry.execute(
        command="act",
        computer_session_id=session_id,
        arguments={"action": "click", "ref": "e1"},
    )
    assert acted["ok"] is True
    assert acted["backend"] == "chrome_devtools_mcp"
    assert adapters["chrome_devtools_mcp"].calls == [
        ("observe", {"detail": "interactive"}),
        ("act", {"action": "click", "ref": "e1"}),
    ]


def test_registry_preserves_one_argument_binding_validator_compatibility():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapters = {
        name: _Adapter(name) for name in (
            "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
        )
    }
    registry = ComputerUseSessionRegistry(
        adapters=adapters,
        binding_validator=lambda _binding_id: {"ok": True},
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1",
    )

    acted = registry.execute(
        command="act", computer_session_id=observed["computer_session_id"],
        arguments={"action": "click"},
    )

    assert acted["ok"] is True


def test_registry_rejects_action_when_bound_page_revision_changes():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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
    registry = ComputerUseSessionRegistry(
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
        command="act", computer_session_id=observed["computer_session_id"],
        owner_id="owner-1", arguments={"action": "click"},
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert validations == []
    assert adapter.calls == [("observe", {}), ("close", {})]
    assert released == ["ctx-1"]
    assert registry.execute(
        command="act", computer_session_id=observed["computer_session_id"],
        owner_id="owner-1",
    )["reason_code"] == "computer_session_not_found"


def test_registry_revalidates_visibility_before_each_existing_session_command():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapter = _Adapter("open_claude_chrome")
    adapters = {
        "playwright_mcp": _Adapter("playwright_mcp"),
        "chrome_devtools_mcp": _Adapter("chrome_devtools_mcp"),
        "open_claude_chrome": adapter,
    }
    visible = {"ok": True}
    released = []
    registry = ComputerUseSessionRegistry(
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
        command="verify", computer_session_id=observed["computer_session_id"],
        owner_id="owner-1", arguments={"assertion": "text_contains"},
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert adapter.calls == [("observe", {}), ("close", {})]
    assert released == ["ctx-1"]


def test_page_capability_revisions_cross_the_session_validation_boundary(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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

    registry = ComputerUseSessionRegistry(
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
        command="act", computer_session_id=observed["computer_session_id"],
        owner_id="owner-1", arguments={"action": "click"},
    )

    assert acted["ok"] is True
    assert validations == [("binding-1", {
        "expected_page_revision": 41,
        "expected_access_revision": 42,
        "expected_geometry_revision": 43,
    })]


def test_geometry_changed_during_activation_blocks_backend_action(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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
    monkeypatch.setattr(webtab, "request_on_ws", lambda *_args, **_kwargs: {
        "ok": True,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "target_id": "target-1",
        "geometry_revision": 10,
    })
    registry = ComputerUseSessionRegistry(adapters=adapters)
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
        computer_session_id=observed["computer_session_id"],
        arguments={"action": "click"},
    )

    assert rejected["reason_code"] == "page_context_stale"
    assert adapter.calls == [("observe", {}), ("close", {})]


def test_registry_leases_one_exact_page_to_one_session_until_close():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    registry = ComputerUseSessionRegistry(
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
        command="close", computer_session_id=first["computer_session_id"],
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
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    entered = threading.Event()
    finish = threading.Event()

    def release_context(_context):
        entered.set()
        assert finish.wait(2)

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    registry = ComputerUseSessionRegistry(
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
                computer_session_id=observed["computer_session_id"],
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
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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
    registry = ComputerUseSessionRegistry(
        adapters=adapters,
        page_key_resolver=lambda _binding: "page-1",
        binding_validator=_allow_binding,
    )
    observed = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
    )
    action_thread = threading.Thread(target=lambda: registry.execute(
        command="act", computer_session_id=observed["computer_session_id"],
        owner_id="owner-1", arguments={"action": "click"},
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
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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
    registry = ComputerUseSessionRegistry(
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
            computer_session_id=observed["computer_session_id"],
            owner_id="owner-1",
        )

    retry = registry.execute(
        command="observe", backend="playwright_mcp",
        binding_id="binding-1", owner_id="owner-2",
    )
    assert retry["frame_id"] == "frame-1"
    assert released == ["ctx-1"]


def test_close_all_releases_sessions_capabilities_and_page_leases():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    released = []
    registry = ComputerUseSessionRegistry(
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


def test_public_computer_use_schema_is_command_based():
    from openprogram.programs import agent_tools

    tool = next(
        item for item in agent_tools(names=["computer_use"])
        if item.name == "computer_use"
    )
    properties = tool.parameters["properties"]
    assert properties["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]
    assert properties["backend"]["enum"] == [
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]
    assert "task" not in properties
    assert tool.parameters["required"] == ["command"]


def test_openprogram_mcp_exposes_computer_use_as_a_first_class_tool():
    from openprogram.mcp_server.contracts import get_mcp_tools

    tools = {tool.name: tool for tool in get_mcp_tools()}
    assert "computer_use" in tools
    schema = tools["computer_use"].inputSchema
    assert schema["properties"]["command"]["enum"] == [
        "list_pages", "observe", "act", "verify", "close",
    ]


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
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSession,
    )
    from openprogram.programs.agentic_functions.browser_agent.mcp_backends import (
        OfficialMCPPageBackend,
    )
    from openprogram.programs.functions.browser import _chrome_bootstrap

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    monkeypatch.setattr(_chrome_bootstrap, "desktop_app_ws_url", lambda: "ws://cdp")
    commands = []
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda command: commands.append(command) or client,
    )
    session = ComputerUseSession("cs-1", "playwright_mcp", "binding-1")

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
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in (
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    )}
    released = []
    registry = ComputerUseSessionRegistry(
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
    session_id = observed["computer_session_id"]
    reused = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="mcp:connection-a", page_context_token=token,
    )
    assert reused["reason_code"] == "page_context_consumed"
    wrong_owner = registry.execute(
        command="act", computer_session_id=session_id,
        owner_id="mcp:connection-b", arguments={"action": "click"},
    )
    assert wrong_owner["reason_code"] == "computer_session_owner_mismatch"
    closed = registry.execute(
        command="close", computer_session_id=session_id,
        owner_id="mcp:connection-a",
    )
    assert closed["closed"] is True
    assert released == ["ctx-1"]


def test_failed_first_observe_releases_session_and_page_context():
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSessionRegistry,
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
    registry = ComputerUseSessionRegistry(
        adapters=adapters,
        release_context=lambda context: released.append(context["context_id"]),
        binding_validator=_allow_binding,
    )
    result = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-failed"},
    )
    session_id = result["computer_session_id"]

    assert result["reason_code"] == "target_lost"
    assert registry.execute(
        command="act", computer_session_id=session_id, owner_id="owner-1",
    )["reason_code"] == "computer_session_not_found"
    retry = registry.execute(
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-retry"},
    )
    assert retry["reason_code"] == "target_lost"
    assert adapter.calls == [("close", {}), ("close", {})]
    assert released == ["ctx-failed", "ctx-retry"]


def test_sync_mcp_client_cleans_thread_when_start_fails(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent import mcp_backends

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
        thread.name == "computer-use-mcp" and thread.ident not in before
        for thread in threading.enumerate()
    ):
        time.sleep(0.01)
    assert instances[0].stopped is True
    assert not any(
        thread.name == "computer-use-mcp" and thread.ident not in before
        for thread in threading.enumerate()
    )


def test_registered_gui_agent_can_select_computer_use_backend(monkeypatch):
    from openprogram.programs import _runtime
    from openprogram.programs.agentic_functions import browser_agent as browser_module
    from openprogram.programs.gui_harness_bridge import (
        install_gui_harness_computer_use,
    )

    calls = []

    def original(**kwargs):
        calls.append(("original", kwargs))
        return {"mode": "desktop"}

    monkeypatch.setattr(
        browser_module,
        "_run_browser_task_commands",
        lambda **kwargs: calls.append(("computer_use", kwargs)) or {
            "mode": "computer_use", "backend": kwargs["backend"],
        },
    )
    wrapped = install_gui_harness_computer_use(original)
    tool = _runtime.get("gui_agent")
    assert tool is not None
    assert tool.parameters["properties"]["backend"]["enum"] == [
        "playwright_mcp", "chrome_devtools_mcp", "open_claude_chrome",
    ]

    result = wrapped(
        task="click Save", backend="chrome_devtools_mcp",
        runtime=SimpleNamespace(),
    )
    assert result == {"mode": "computer_use", "backend": "chrome_devtools_mcp"}
    assert calls[0][0] == "computer_use"


def test_direct_list_pages_releases_capture_when_registry_rejects(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    context = {"context_id": "ctx-rejected", "surfaces": []}
    released = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": False, "reason_code": "owner_closing", "pages": []}

    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "capture_active", lambda: context)
    monkeypatch.setattr(
        surface_context, "release_bindings", lambda value: released.append(value),
    )

    result = module.execute_direct_computer_use(
        {"command": "list_pages"}, owner_id="mcp:closing",
    )
    assert result["reason_code"] == "owner_closing"
    assert released == [context]


@pytest.mark.parametrize("route", ["harness", "public"])
def test_temporary_page_capture_is_released_when_lease_rejects(monkeypatch, route):
    from openprogram.agent import surface_context
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    context = {"context_id": "ctx-temp", "surfaces": [{}]}
    released = []

    class _Registry:
        def execute(self, **_kwargs):
            return {"ok": False, "reason_code": "page_in_use"}

    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "capture_active", lambda: context)
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
        result = module.computer_use(
            command="observe", backend="playwright_mcp",
        )
        assert result["reason_code"] == "page_in_use"
    assert released == [context]


@pytest.mark.parametrize("route", ["harness", "public"])
def test_temporary_page_capture_is_released_when_binding_resolution_fails(
    monkeypatch, route,
):
    from openprogram.agent import surface_context
    from openprogram.programs.agentic_functions import browser_agent as module

    context = {"context_id": "ctx-temp", "surfaces": [{}]}
    released = []
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(surface_context, "capture_active", lambda: context)
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
            module.computer_use(command="observe", backend="playwright_mcp")
    assert released == [context]


def test_official_backend_rejects_stale_frame_before_upstream_call(monkeypatch):
    from openprogram.programs.agentic_functions.browser_agent.computer_use_runtime import (
        ComputerUseSession,
    )
    from openprogram.programs.agentic_functions.browser_agent.mcp_backends import (
        OfficialMCPPageBackend,
    )

    controller = _Controller()
    client = _PlaywrightClient(controller.page)
    adapter = OfficialMCPPageBackend(
        "playwright_mcp", lambda: controller,
        client_factory=lambda _command: client,
    )
    session = ComputerUseSession("cs-1", "playwright_mcp", "binding-1")
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
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    class _Registry:
        def __init__(self) -> None:
            self.calls = []

        def execute(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["command"] == "observe":
                return {
                    "ok": True, "frame_id": "frame-1",
                    "computer_session_id": "cs-1",
                    "backend": kwargs.get("backend") or "chrome_devtools_mcp",
                }
            if kwargs["command"] == "verify":
                return {"ok": True, "passed": True, "backend": "chrome_devtools_mcp"}
            return {"ok": True}

    registry = _Registry()
    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: registry)
    context = {"context_id": "ctx-1", "surfaces": [{}]}
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
    assert registry.calls[0] == {
        "command": "observe",
        "backend": "chrome_devtools_mcp",
        "binding_id": "binding-1",
        "page_key": "page-1",
        "owner_id": "harness:ctx-1",
        "page_context": context,
    }
    assert registry.calls[-1] == {
        "command": "close", "computer_session_id": "cs-1",
        "owner_id": "harness:ctx-1",
    }


def test_gui_harness_screenshot_capability_is_one_request_only(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs import ToolReturn
    from openprogram.programs.agentic_functions import browser_agent as module
    from openprogram.programs.agentic_functions.browser_agent import (
        computer_use_runtime,
    )

    class _Registry:
        def __init__(self):
            self.revoked = 0

        def execute(self, **kwargs):
            command = kwargs["command"]
            if command == "observe":
                return {"frame_id": "f1", "computer_session_id": "cs1"}
            if command == "act" and kwargs["arguments"]["action"] == "screenshot":
                return ToolReturn(images=[b"png"], json_data={"frame_id": "f1"})
            if command == "verify":
                return {"passed": True}
            return {"ok": True}

        def revoke_screenshot(self, _session_id):
            self.revoked += 1

    registry = _Registry()
    monkeypatch.setattr(computer_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id": "ctx"})
    monkeypatch.setattr(surface_context, "resolve_binding", lambda _page="": "b1")
    monkeypatch.setattr(surface_context, "resolve_page_key", lambda _page="": "p1")

    class _Runtime:
        def __init__(self):
            self.requests = []

        def exec(self, **kwargs):
            self.requests.append([block["type"] for block in kwargs["content"]])
            index = len(self.requests)
            if index == 1:
                asyncio.run(kwargs["tools"][0].execute(
                    "c1", {"action": "screenshot", "expected_frame_id": "f1"},
                    asyncio.Event(), None,
                ))
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
    assert registry.revoked == 1
