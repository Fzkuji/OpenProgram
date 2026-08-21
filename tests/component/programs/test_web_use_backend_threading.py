from __future__ import annotations

import threading
from types import SimpleNamespace


class _Page:
    def __init__(self) -> None:
        self.owner_thread = threading.get_ident()
        self.marker_value = ""

    def evaluate(self, script, argument=None):
        assert threading.get_ident() == self.owner_thread
        if "Object.defineProperty" in script:
            _name, self.marker_value = argument
        if "delete globalThis" in script:
            self.marker_value = ""
        return None


class _BrowserAPI:
    def __init__(self) -> None:
        self._sessions = {}

    def execute(self, **arguments):
        if arguments["action"] == "open":
            self._sessions["br_thread"] = {
                "page": _Page(),
                "app_tab_id": "tab-1",
                "app_target_id": "target-1",
            }
            return "Opened browser session `br_thread`."
        self._sessions.pop(arguments.get("session_id"), None)
        return "closed"


class _Client:
    def __init__(self, page: _Page) -> None:
        self.page = page

    def call(self, name, _arguments):
        if name == "list_pages":
            return SimpleNamespace(
                content=[], structuredContent={"pages": [{"id": 7}]},
            )
        return SimpleNamespace(
            content=[SimpleNamespace(text=self.page.marker_value)],
            structuredContent={},
        )

    def close(self):
        return None


def test_chrome_backend_marker_stays_on_playwright_owner_thread(monkeypatch):
    from openprogram.programs.functions.agentic.workflow.browser import (
        BrowserPageController,
    )
    from openprogram.programs.functions.agentic.workflow.browser.web_use_runtime import (
        WebUseSession,
    )
    from openprogram.programs.functions.agentic.workflow.browser.mcp_backends import (
        OfficialMCPPageBackend,
    )
    from openprogram.programs.functions.vanilla.web.browser import _chrome_bootstrap

    controller = BrowserPageController(_BrowserAPI())
    controller.evaluate_bound_page("() => null")
    page = controller._owner.submit(controller._page).result()
    monkeypatch.setattr(
        _chrome_bootstrap, "desktop_app_ws_url", lambda: "ws://desktop",
    )
    adapter = OfficialMCPPageBackend(
        "chrome_devtools_mcp",
        lambda: controller,
        client_factory=lambda _command: _Client(page),
    )
    session = WebUseSession(
        id="cs-thread", backend="chrome_devtools_mcp", binding_id="binding-1",
    )

    try:
        assert adapter._ensure_bound(session) is not None
        assert session.state["upstream_page"] == 7
    finally:
        adapter.close(session)


def test_official_backend_action_uses_controller_thread_boundary():
    from openprogram.programs.functions.agentic.workflow.browser.web_use_runtime import (
        WebUseSession,
    )
    from openprogram.programs.functions.agentic.workflow.browser.mcp_backends import (
        OfficialMCPPageBackend,
    )

    class Controller:
        def _require_fresh(self, _frame_id):
            raise AssertionError("private freshness check escaped owner boundary")

        def prepare_external_action(self, arguments):
            assert arguments["expected_frame_id"] == "frame-1"
            return None

        def record_external_mutation(self, detail):
            return {"ok": True, "detail": detail, "observe_required": True}

    class Client:
        def call(self, name, arguments):
            assert name == "browser_navigate"
            assert arguments == {"url": "https://example.com"}
            return SimpleNamespace(content=[], structuredContent={})

    controller = Controller()
    adapter = OfficialMCPPageBackend("playwright_mcp", lambda: controller)
    session = WebUseSession(
        id="cs-action-thread",
        backend="playwright_mcp",
        binding_id="binding-1",
        controller=controller,
    )
    session.state["mcp_client"] = Client()

    result = adapter.act(session, {
        "action": "navigate",
        "url": "https://example.com",
        "expected_frame_id": "frame-1",
    })

    assert result["ok"] is True
    assert result["observe_required"] is True
