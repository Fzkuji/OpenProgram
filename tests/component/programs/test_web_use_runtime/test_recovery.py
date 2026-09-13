"""web use recovery tests."""
from __future__ import annotations
from ._support import (
    SimpleNamespace,
    _Adapter,
    pytest,
)


def test_closed_target_recovery_observes_without_replaying_click(monkeypatch):
    from openprogram.programs.workflow import browser
    from openprogram.agent import surface_context
    calls = []
    monkeypatch.setattr(browser, "_execute_web_use", lambda *args: {
        "ok": False, "reason_code": "target_lost", "recovery_url": "https://example.com/task",
    })
    monkeypatch.setattr(surface_context, "open_page", lambda url: calls.append(url) or {"surfaces": [{}]})
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id": "owner"})
    monkeypatch.setattr(browser, "_start_session_on_opened_page", lambda **kwargs: {
        "ok": True, "frame_id": "fresh", "web_session_id": "new",
    })
    result = browser.web_use(command="act", arguments={"action": "click", "ref": "old"})
    assert calls == ["https://example.com/task"]
    assert result["recovered_page"] is True
    assert result["frame_id"] == "fresh"



def test_closed_page_retains_url_after_successful_mutation(monkeypatch):
    from types import SimpleNamespace
    from openprogram.programs.workflow.browser import web_use_runtime as module
    valid = True
    class Adapter(_Adapter):
        def act(self, session, arguments):
            session.controller = SimpleNamespace(_frame=None)
            return {"ok": True, "observe_required": True, "url": "https://example.com/after"}
    registry = module.WebUseSessionRegistry(adapters={name: Adapter(name) for name in module.SUPPORTED_BACKENDS},
        binding_validator=lambda _: {"ok": valid, "reason_code": "page_context_stale"})
    seen = registry.execute(command="observe", owner_id="owner", binding_id="page")
    registry.execute(command="act", owner_id="owner", web_session_id=seen["web_session_id"], arguments={"action":"click", "ref":"e1"})
    valid = False
    result = registry.execute(command="observe", owner_id="owner", web_session_id=seen["web_session_id"])
    assert result["recovery_url"] == "https://example.com/after"
    registry.close_all()



def test_existing_changed_page_is_observed_without_opening_duplicate(monkeypatch):
    from openprogram.programs.workflow import browser
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    calls = []
    class Registry:
        def list_pages(self, **kwargs): return {"ok": True, "pages": [{"tab_id":"tab", "window_id":"win", "page_context_token":"fresh"}]}
        def release_page_capabilities(self, *args, **kwargs): pass
        def execute(self, **kwargs): calls.append(kwargs); return {"ok":True, "url":"https://example.com/current"}
    monkeypatch.setattr(web_use_runtime, "get_registry", Registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id":"owner"})
    monkeypatch.setattr(surface_context, "capture_pages", lambda: {"context_id":"inventory"})
    monkeypatch.setattr(surface_context, "open_page", lambda _: pytest.fail("must not open duplicate"))
    result = browser._recover_web_use_page({"recovery_url":"https://example.com/old", "recovery_tab_id":"tab", "recovery_window_id":"win"}, backend="playwright_mcp")
    assert result["url"] == "https://example.com/current"
    assert calls[0]["command"] == "observe"



def test_page_recovery_releases_unconsumed_capability_on_failure(monkeypatch):
    from openprogram.programs.workflow import browser
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    released = []
    class Registry:
        def list_pages(self, **kwargs): return {"ok": True, "pages": [{"tab_id":"tab", "window_id":"win", "page_context_token":"fresh"}]}
        def release_page_capabilities(self, tokens, **kwargs): released.extend(tokens)
        def execute(self, **kwargs): return {"ok":False, "reason_code":"page_in_use"}
    monkeypatch.setattr(web_use_runtime, "get_registry", Registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id":"owner"})
    monkeypatch.setattr(surface_context, "capture_pages", lambda: {"context_id":"inventory"})
    result = browser._recover_web_use_page({"recovery_url":"https://example.com/old", "recovery_tab_id":"tab", "recovery_window_id":"win"}, backend="playwright_mcp")
    assert result["reason_code"] == "page_in_use"
    assert released == ["fresh"]



def test_closed_page_reopens_in_original_window(monkeypatch):
    from openprogram.programs.workflow import browser
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import web_use_runtime
    calls = []
    class Registry:
        def list_pages(self, **kwargs): return {"ok": True, "pages": []}
        def release_page_capabilities(self, *args, **kwargs): pass
    monkeypatch.setattr(web_use_runtime, "get_registry", Registry)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id":"owner"})
    monkeypatch.setattr(surface_context, "capture_pages", lambda: {"context_id":"inventory", "windows":[{"window_id":"win"},{"window_id":"other"}]})
    monkeypatch.setattr(surface_context, "release_bindings", lambda _: None)
    monkeypatch.setattr(surface_context, "open_page", lambda url, **kwargs: calls.append((url, kwargs)) or {"surfaces":[{}]})
    monkeypatch.setattr(browser, "_start_session_on_opened_page", lambda **kwargs: {"ok":True})
    result = browser._recover_web_use_page({"recovery_url":"https://example.com/old", "recovery_tab_id":"tab", "recovery_window_id":"win"}, backend="playwright_mcp")
    assert result["ok"] is True
    assert calls == [("https://example.com/old", {"window_id":"win"})]



def test_recovery_cancelled_during_inventory_does_not_open_page(monkeypatch):
    from openprogram.programs.workflow import browser
    from openprogram.agent import surface_context, run_control
    from openprogram.agentic_programming.function import CancelledError
    cancelled = False
    def check():
        if cancelled: raise CancelledError("stopped")
    def inventory():
        nonlocal cancelled
        cancelled = True
        return {"context_id": "inventory"}
    released = []
    monkeypatch.setattr(run_control, "check_cancelled", check)
    monkeypatch.setattr(surface_context, "current", lambda: {"context_id":"owner"})
    monkeypatch.setattr(surface_context, "capture_pages", inventory)
    monkeypatch.setattr(surface_context, "release_bindings", released.append)
    monkeypatch.setattr(surface_context, "open_page", lambda *args, **kwargs: pytest.fail("cancelled recovery opened page"))
    with pytest.raises(CancelledError):
        browser._recover_web_use_page({"recovery_url":"https://example.com/old", "recovery_tab_id":"tab", "recovery_window_id":"win"}, backend="playwright_mcp")
    assert released == [{"context_id":"inventory"}]

