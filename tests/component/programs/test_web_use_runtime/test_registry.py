"""web use registry tests."""
from __future__ import annotations
from ._support import (
    SimpleNamespace,
    _Adapter,
    _NativeObserveAdapter,
    _allow_binding,
    _public_open_transport,
    pytest,
    threading,
    time,
)


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
    assert rejected["closed"] is True
    assert rejected["web_session_id"] == observed["web_session_id"]
    assert validations == ["binding-1"]
    assert adapter.calls == [("observe", {}), ("close", {})]
    assert released == ["ctx-1"]
    assert registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1",
    )["reason_code"] == "web_session_not_found"
    listed = registry.list_pages(
        owner_id="owner-1",
        context={
            "context_id": "ctx-retry",
            "surfaces": [{
                "binding_id": "binding-1", "surface_key": "p1",
            }],
        },
    )
    recovered = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="owner-1",
        page_context_token=listed["pages"][0]["page_context_token"],
    )
    assert recovered.get("ok") is not False
    assert recovered["web_session_id"] != observed["web_session_id"]
    assert recovered.get("closed") is not True



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
    assert result["closed"] is True
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
        assert result.is_error is True
        assert result.json_data["reason_code"] == "page_in_use"
    assert released == [context]



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



def test_observe_with_url_session_only_act_uses_live_registry_session(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import (
        SUPPORTED_BACKENDS,
        WebUseSessionRegistry,
    )

    adapters = {name: _Adapter(name) for name in SUPPORTED_BACKENDS}
    registry = WebUseSessionRegistry(
        adapters=adapters,
        binding_validator=_allow_binding,
        release_context=lambda context: None,
    )
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "web_use_owner_id", lambda context=None: "owner-open",
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url: {
            "context_id": "page_ctx_opened",
            "window_id": "win",
            "surfaces": [{
                "binding_id": "binding-opened",
                "surface_key": "p1",
                "window_id": "win",
                "tab_id": "tab-1",
            }],
        },
    )

    result = module.web_use(
        command="observe",
        backend="open_claude_chrome",
        arguments={"url": "https://example.test/page/1"},
    )
    assert result.get("ok") is not False
    assert result["web_session_id"].startswith("cs_")
    assert result.get("frame_id") == "frame-1"
    assert "page_context_token" not in result
    assert result.get("closed") is not True

    acted = module.web_use(
        command="act",
        backend="open_claude_chrome",
        web_session_id=result["web_session_id"],
        arguments={"action": "click", "expected_frame_id": "frame-1"},
    )
    assert acted.get("ok") is not False
    assert acted["web_session_id"] == result["web_session_id"]
    assert acted.get("closed") is not True
    assert adapters["open_claude_chrome"].calls[-1][0] == "act"



@pytest.mark.parametrize("entry", ["web_use", "direct"])
def test_public_url_observe_releases_binding_when_frame_is_missing(
    monkeypatch, entry,
):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import (
        SUPPORTED_BACKENDS,
        WebUseSessionRegistry,
    )
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    class _NoFrame(_NativeObserveAdapter):
        def observe(self, session, arguments, *, before_dispatch=None):
            del session, arguments
            if before_dispatch is not None:
                before_dispatch()
            self.calls.append("observe")
            return {"title": "empty"}

    adapters = {name: _NoFrame(name) for name in SUPPORTED_BACKENDS}
    registry = WebUseSessionRegistry(
        adapters=adapters,
        release_context=surface_context.release_bindings,
    )
    owner = object()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(webtab, "request_on_ws", _public_open_transport(webtab))
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "web_use_owner_id", lambda context=None: "owner-native",
    )
    try:
        if entry == "web_use":
            observed = module.web_use(
                command="observe",
                backend="open_claude_chrome",
                arguments={"url": "http://127.0.0.1:62147/page/1"},
            )
        else:
            observed = module.execute_direct_web_use(
                {
                    "command": "observe",
                    "backend": "open_claude_chrome",
                    "arguments": {"url": "http://127.0.0.1:62147/page/1"},
                },
                owner_id="owner-native",
            )
        if entry == "web_use":
            assert observed.is_error is True
            observed = observed.json_data
        assert observed.get("ok") is False
        assert not webtab._bindings
        session_id = observed.get("web_session_id") or ""
        if session_id:
            again = registry.execute(
                command="act",
                backend="open_claude_chrome",
                web_session_id=session_id,
                owner_id="owner-native",
                arguments={
                    "action": "click",
                    "expected_frame_id": "frame_1_b6a848a6",
                },
            )
            assert again.get("reason_code") == "web_session_not_found"
            assert again.get("closed") is not True or again.get("ok") is False
    finally:
        for binding_id in list(webtab._bindings):
            webtab.release_binding(binding_id)
        webtab.release_connection(owner)
        registry.close_all()

