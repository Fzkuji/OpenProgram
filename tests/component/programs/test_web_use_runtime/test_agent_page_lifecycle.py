"""web use agent page lifecycle tests."""
from __future__ import annotations
from ._support import (
    SimpleNamespace,
    asyncio,
    pytest,
)


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
    from openprogram.programs.workflow import browser as browser_module
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

    result = browser_module._run_browser_task_commands(
        task="inspect", backend="playwright_mcp", max_steps=1, max_seconds=10,
        runtime=SimpleNamespace(),
    )

    assert result["status"] == "infeasible"
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

