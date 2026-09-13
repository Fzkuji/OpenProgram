"""web use page commands tests."""
from __future__ import annotations
from ._support import (
    SimpleNamespace,
    _Adapter,
    _NativeObserveAdapter,
    _allow_binding,
    _public_open_transport,
    asyncio,
    pytest,
)


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



@pytest.mark.parametrize(
    ("runtime_behavior", "teardown_raises", "expected_status", "expected_reason"),
    [
        ("verify", False, "succeeded", "verified"),
        ("raise", False, None, None),
        ("timeout", False, "failed", "timeout"),
        ("miss", False, "failed", "tool_not_executed"),
        ("observe_cancel", False, None, None),
        ("observe_cancel", True, None, None),
        ("screenshot_timeout", False, None, None),
        ("cancel", False, None, None),
        ("cancel", True, None, None),
        ("verify", True, None, None),
    ],
)
def test_browser_capability_without_page_opens_background_page(
    monkeypatch, runtime_behavior, teardown_raises,
    expected_status, expected_reason,
):
    from openprogram.agent import surface_context
    from openprogram.agentic_programming.function import CancelledError
    from openprogram.programs._execution_common import ToolReturn
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import DEFAULT_BACKEND
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
        def __init__(self):
            self.calls = []
            self.released_owners = []
            self.revoked = []

        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def execute(self, **kwargs):
            self.calls.append(kwargs)
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

        def revoke_screenshot(self, session_id):
            self.revoked.append(session_id)
            if runtime_behavior == "screenshot_timeout":
                raise RuntimeError("screenshot revoke failed")

        def release_owner(self, owner_id):
            self.released_owners.append(owner_id)
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

    registry = _Registry()
    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: registry)
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
        lambda value: closed.append(value) or {"ok": True},
    )

    call_kwargs = {
        "task": "inspect the page",
        "backend": DEFAULT_BACKEND,
        "max_steps": 150,
        "max_seconds": None,
        "runtime": _Runtime(),
    }
    if runtime_behavior in {"screenshot_timeout", "timeout"}:
        ticks = iter(
            (0.0, 0.0, 2.0)
            if runtime_behavior == "screenshot_timeout" else
            (0.0, 2.0)
        )
        release_screenshot = browser_module._release_screenshot_payload

        def fail_after_screenshot_release(content, result):
            release_screenshot(content, result)
            raise RuntimeError("screenshot payload release failed")

        monkeypatch.setattr(
            browser_module,
            "time",
            SimpleNamespace(monotonic=lambda: next(ticks, 2.0)),
        )
        if runtime_behavior == "screenshot_timeout":
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
        match = (
            "registry close failed|registry owner close failed"
            if runtime_behavior == "verify" and teardown_raises
            else "cancelled|model transport|screenshot payload"
        )
        with pytest.raises(expected_error, match=match):
            browser_module._run_browser_task_commands(**call_kwargs)
        result = None
    else:
        result = browser_module._run_browser_task_commands(**call_kwargs)

    if result is not None:
        assert result["status"] == expected_status
        assert result["reason_code"] == expected_reason
        assert result["backend"] == DEFAULT_BACKEND
    assert opens == [(
        "https://www.google.com/",
        {"window_id": "window-1", "background": True},
    )]
    assert closed == []
    assert context in released
    assert registry.released_owners
    if runtime_behavior != "observe_cancel":
        assert any(call["command"] == "close" for call in registry.calls)
    if runtime_behavior == "screenshot_timeout":
        assert registry.revoked



def test_malformed_auto_open_still_closes_unusable_page(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import DEFAULT_BACKEND

    context = {"context_id": "ctx-empty", "window_id": "window-1", "surfaces": []}
    opened_context = {
        "context_id": "ctx-malformed",
        "window_id": "window-1",
        "surfaces": [{"tab_id": "tab-opened"}],
    }
    closed = []
    released_owners = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def release_owner(self, owner_id):
            released_owners.append(owner_id)

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: context,
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url, **kwargs: opened_context,
    )
    monkeypatch.setattr(
        surface_context,
        "close_page",
        lambda value: closed.append(value) or {"ok": True},
    )

    result = browser_module._run_browser_task_commands(
        task="inspect the page",
        backend=DEFAULT_BACKEND,
        max_steps=1,
        max_seconds=10,
        runtime=SimpleNamespace(),
    )

    assert closed == [opened_context]
    assert result["status"] == "failed"
    assert result["reason_code"] == "page_unavailable"
    assert released_owners



def test_malformed_auto_open_reports_close_failure(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser import web_use_runtime
    from openprogram.programs.workflow.browser.web_use_runtime import DEFAULT_BACKEND

    context = {"context_id": "ctx-empty", "window_id": "window-1", "surfaces": []}
    opened_context = {
        "context_id": "ctx-malformed-fail",
        "window_id": "window-1",
        "surfaces": [{"tab_id": "tab-opened"}],
    }
    closed = []

    class _Registry:
        def list_pages(self, **_kwargs):
            return {"ok": True, "pages": []}

        def release_owner(self, _owner_id):
            return None

    monkeypatch.setattr(web_use_runtime, "get_registry", lambda: _Registry())
    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setattr(
        surface_context, "capture_pages", lambda _context=None: context,
    )
    monkeypatch.setattr(
        surface_context,
        "open_page",
        lambda url, **kwargs: opened_context,
    )
    monkeypatch.setattr(
        surface_context,
        "close_page",
        lambda value: closed.append(value) or {
            "ok": False,
            "error": "the background Page could not be closed",
        },
    )

    result = browser_module._run_browser_task_commands(
        task="inspect the page",
        backend=DEFAULT_BACKEND,
        max_steps=1,
        max_seconds=10,
        runtime=SimpleNamespace(),
    )

    assert closed == [opened_context]
    assert result["reason_code"] == "page_cleanup_failed"
    assert "Close the remaining background Page" in result["handoff_instruction"]



def test_browser_capability_reuses_existing_origin_page(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as browser_module
    from openprogram.programs.workflow.browser import web_use_runtime

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

    result = browser_module._run_browser_task_commands(
        task="inspect the page", backend="playwright_mcp",
        max_steps=150, max_seconds=None, runtime=_Runtime(),
    )

    assert result["status"] == "succeeded"
    assert captures[0] is window_context
    assert opened == []



def test_browser_capability_without_desktop_returns_infeasible_handoff(
    monkeypatch,
):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow import browser as browser_module

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
    result = browser_module._run_browser_task_commands(
        task="inspect the page", backend="playwright_mcp",
        max_steps=150, max_seconds=None, runtime=SimpleNamespace(),
    )

    assert result["status"] == "infeasible"
    assert result["reason_code"] == "desktop_unavailable"
    assert "Launch or reconnect" in result["handoff_instruction"]
    assert opens == []



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
    assert result.is_error is True
    result = result.json_data
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
    assert result["frame_id"] == "frame-1"
    assert "page_context_token" not in result

    opens.clear()
    acted = module.web_use(
        command="act",
        web_session_id=result["web_session_id"],
        arguments={"action": "click", "expected_frame_id": "frame-1"},
    )
    assert opens == []
    assert acted["ok"] is True
    assert acted["web_session_id"] == "cs_opened"
    assert "page_context_token" not in acted
    assert acted.get("closed") is not True



@pytest.mark.parametrize("entry", ["web_use", "direct"])
def test_public_url_observe_keeps_binding_for_first_session_act(
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

    adapters = {name: _NativeObserveAdapter(name) for name in SUPPORTED_BACKENDS}
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
    url = "http://127.0.0.1:62147/page/1"
    try:
        if entry == "web_use":
            observed = module.web_use(
                command="observe",
                backend="open_claude_chrome",
                arguments={"url": url},
            )
        else:
            observed = module.execute_direct_web_use(
                {
                    "command": "observe",
                    "backend": "open_claude_chrome",
                    "arguments": {"url": url},
                },
                owner_id="owner-native",
            )
        assert "ok" not in observed or observed.get("ok") is not False
        assert observed.get("ok") is not False
        assert observed["frame_id"] == "frame_1_b6a848a6"
        assert observed["web_session_id"].startswith("cs_")
        assert observed.get("closed") is not True
        assert "page_context_token" not in observed
        assert webtab._bindings
        binding_id = next(iter(webtab._bindings))
        assert webtab.request_bound_tab(binding_id).get("ok") is True

        if entry == "web_use":
            acted = module.web_use(
                command="act",
                backend="open_claude_chrome",
                web_session_id=observed["web_session_id"],
                arguments={
                    "action": "click",
                    "expected_frame_id": observed["frame_id"],
                },
            )
        else:
            acted = module.execute_direct_web_use(
                {
                    "command": "act",
                    "backend": "open_claude_chrome",
                    "web_session_id": observed["web_session_id"],
                    "arguments": {
                        "action": "click",
                        "expected_frame_id": observed["frame_id"],
                    },
                },
                owner_id="owner-native",
            )
        assert acted.get("ok") is not False
        assert acted.get("closed") is not True
        assert acted["web_session_id"] == observed["web_session_id"]
        assert adapters["open_claude_chrome"].calls[-1][0] == "act"
        assert webtab._bindings
    finally:
        for binding_id in list(webtab._bindings):
            webtab.release_binding(binding_id)
        webtab.release_connection(owner)
        registry.close_all()



def test_invalid_arguments_keep_live_session_unmarked_closed():
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
        command="observe", backend="open_claude_chrome",
        binding_id="binding-1", owner_id="owner-1",
        page_context={"context_id": "ctx-1"},
    )
    rejected = registry.execute(
        command="act", web_session_id=observed["web_session_id"],
        owner_id="owner-1", arguments={},
    )
    assert rejected["reason_code"] == "invalid_arguments"
    assert rejected.get("closed") is not True
    assert rejected["web_session_id"] == observed["web_session_id"]
    still = registry.execute(
        command="observe", web_session_id=observed["web_session_id"],
        owner_id="owner-1",
    )
    assert still.get("ok") is not False
    assert still.get("closed") is not True



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
    assert result.is_error is True
    result = result.json_data
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
    assert result.is_error is True
    result = result.json_data
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

    if entry == "web_use":
        assert result.is_error is True
        result = result.json_data
    assert result == cleanup



@pytest.mark.parametrize("subprocess_mode", [False, True])
@pytest.mark.parametrize("session_id", ["resource-owner-session", ""])
def test_public_web_use_open_carries_trusted_resource_owner(monkeypatch, subprocess_mode, session_id):
    from openprogram.agent import surface_context
    from openprogram.agent.run_control import set_current_session_id, reset_current_session_id
    from openprogram.programs.workflow import browser as module
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    monkeypatch.setattr(surface_context, "current", lambda: None)
    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1" if subprocess_mode else "0")
    ws = object()
    monkeypatch.setattr(server, "_ws_connections", {ws})
    monkeypatch.setattr(webtab, "registered_desktop_windows", lambda: [(ws, "main", 1)])
    commands = []
    def request(command, *args, **kwargs):
        commands.append(command)
        return {"ok": False, "error": "test transport stopped after capture"}
    monkeypatch.setattr(webtab, "_request", request)
    monkeypatch.setattr(webtab, "request_on_ws", lambda ws, command, **kw: request(command))
    token = set_current_session_id(session_id)
    try:
        module.web_use(command="observe", arguments={"url": "https://example.test/resource-owner"})
    finally:
        reset_current_session_id(token)
    assert len(commands) == 1
    assert commands[0]["op"] == "open"
    assert commands[0].get("session_id") == (session_id or None)

