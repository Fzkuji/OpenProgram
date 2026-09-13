"""web use adapters tests."""
from __future__ import annotations
from ._support import (
    _Controller,
    _PlaywrightClient,
    pytest,
    threading,
    time,
)


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

