from __future__ import annotations

import base64
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


pytestmark = pytest.mark.live


class _FixtureHandler(BaseHTTPRequestHandler):
    marker = ""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = f"""<!doctype html>
<html><head><title>{self.marker}</title></head>
<body><h1>{self.marker}</h1>
<button id="change">Change state</button>
<p id="status">ready</p>
<script>
document.getElementById("change").addEventListener("click", () => {{
  const cursor = document.querySelector("[data-openprogram-agent-cursor]");
  document.getElementById("status").textContent =
    "changed cursor=" + Boolean(cursor);
}});
</script></body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def _start_fixture() -> tuple[ThreadingHTTPServer, str, str]:
    marker = "OpenProgram background Web Use " + uuid.uuid4().hex
    handler = type("FixtureHandler", (_FixtureHandler,), {"marker": marker})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/", marker


def _tool_json(payload: dict) -> dict:
    result = payload["result"]
    details = result.get("details") or {}
    if isinstance(details.get("json"), dict):
        return details["json"]
    text = next(
        item["text"] for item in result.get("content") or []
        if item.get("type") == "text"
    )
    value = json.loads(text)
    assert isinstance(value, dict)
    return value


def _dispatch(arguments: dict, owner_id: str) -> tuple[dict, dict]:
    from openprogram.mcp.server.service import _worker_web_use_request

    payload = _worker_web_use_request(
        "/api/web-use",
        {"arguments": arguments, "owner_id": owner_id},
        timeout=30,
    )
    return payload, _tool_json(payload)


def _release_owner(owner_id: str) -> None:
    from openprogram.mcp.server.service import _worker_web_use_request

    _worker_web_use_request(
        "/api/web-use/release-owner",
        {"owner_id": owner_id},
        timeout=5,
    )


def _tab_ids(shell_page) -> set[str]:
    tabs = shell_page.locator('[role="tab"][data-tab-id]')
    return {
        str(tabs.nth(index).get_attribute("data-tab-id"))
        for index in range(tabs.count())
    }


def _page_inventory(shell_page) -> dict[str, dict]:
    pages = shell_page.evaluate(
        "async () => { const result = []; const inspect = "
        "window.openprogramDesktop?.webTab?.inspect; if (!inspect) return result; "
        "for (const node of document.querySelectorAll('[role=tab][data-tab-id]')) { "
        "const tabId = node.dataset.tabId; const page = await inspect(tabId); "
        "if (page) result.push({ tab_id: tabId, ...page }); } return result; }"
    )
    return {
        str(page["tab_id"]): page
        for page in pages
        if isinstance(page, dict) and page.get("tab_id")
    }


def _active_tab_id(shell_page) -> str:
    return str(shell_page.locator(
        '[role="tab"][data-tab-id][aria-selected="true"]'
    ).first.get_attribute("data-tab-id"))


def _dom_click(locator) -> None:
    locator.evaluate(
        "node => node.dispatchEvent(new MouseEvent('click', "
        "{ bubbles: true, cancelable: true, view: window }))"
    )


def _close_tabs(shell_page, tab_ids: set[str]) -> None:
    for tab_id in tab_ids:
        tab = shell_page.locator(f'[role="tab"][data-tab-id="{tab_id}"]')
        if not tab.count():
            continue
        close = tab.locator("xpath=..").locator(
            'button[aria-label="Close tab"], button[aria-label="关闭标签"]'
        )
        if close.count():
            _dom_click(close)


def _close_owned_page(shell_page, window_id: str, tab_id: str) -> None:
    shell_page.evaluate(
        "({ windowId, tabId, reqId }) => window.dispatchEvent(new CustomEvent("
        "'op:ws-message', { detail: { type: 'webtab.command', data: {"
        "op: 'close', window_id: windowId, tab_id: tabId, req_id: reqId"
        "} } }))",
        {
            "windowId": window_id,
            "tabId": tab_id,
            "reqId": "live-close-" + uuid.uuid4().hex,
        },
    )
    shell_page.wait_for_function(
        "tabId => !document.querySelector("
        "`[role=tab][data-tab-id=\"${CSS.escape(tabId)}\"]`)",
        arg=tab_id,
        timeout=15_000,
    )


def _create_background_page(shell_page, url: str) -> tuple[str, str]:
    before = _page_inventory(shell_page)
    original = _active_tab_id(shell_page)
    created_tab = ""
    try:
        shell_page.evaluate(
            "({ url, reqId }) => window.dispatchEvent(new CustomEvent("
            "'op:ws-message', { detail: { type: 'webtab.command', data: {"
            "op: 'open', url, background: true, req_id: reqId"
            "} } }))",
            {"url": url, "reqId": "live-background-" + uuid.uuid4().hex},
        )
        shell_page.wait_for_function(
            "before => [...document.querySelectorAll('[role=tab][data-tab-id]')]"
            ".some(node => !before.includes(node.dataset.tabId))",
            arg=list(before),
        )
        created = set(_page_inventory(shell_page)) - set(before)
        assert len(created) == 1
        created_tab = created.pop()
        _assert_unchanged(shell_page, original)
        return original, created_tab
    except Exception:
        if not created_tab:
            candidates = [
                tab_id
                for tab_id, page in _page_inventory(shell_page).items()
                if tab_id not in before and page.get("url") == url
            ]
            if len(candidates) == 1:
                created_tab = candidates[0]
        if created_tab:
            window_id = shell_page.evaluate(
                "() => window.openprogramDesktop?.windowId || ''"
            )
            _close_owned_page(shell_page, window_id, created_tab)
        raise


def _assert_unchanged(shell_page, original_tab: str) -> None:
    assert _active_tab_id(shell_page) == original_tab


def _frontmost_application() -> str:
    result = subprocess.run(
        [
            "osascript",
            "-e",
            "tell application \"System Events\" to get name of first "
            "application process whose frontmost is true",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip()


def _assert_openprogram_not_frontmost() -> None:
    assert _frontmost_application() != "OpenProgram"


def _fetch_json(shell_page, path: str, body: dict | None = None) -> dict:
    return shell_page.evaluate(
        "async ({ path, body }) => { const response = await fetch(path, {"
        "method: body ? 'POST' : 'GET', headers: body ? "
        "{'Content-Type': 'application/json'} : undefined, "
        "body: body ? JSON.stringify(body) : undefined }); "
        "return { status: response.status, data: await response.json() }; }",
        {"path": path, "body": body},
    )


def _wait_execution_output(
    session_id: str,
    execution_id: str,
    timeout: float,
) -> dict:
    from openprogram.agent.session_db import default_db

    deadline = time.monotonic() + timeout
    db = default_db()
    while time.monotonic() < deadline:
        db.invalidate_cache(session_id)
        node = next((
            item for item in db.get_nodes(session_id)
            if item.id == execution_id
        ), None)
        output = node.output if node is not None else None
        if isinstance(output, dict) and output.get("status") in {
            "succeeded", "infeasible", "failed", "cancelled",
        }:
            return output
        time.sleep(0.2)
    raise AssertionError("GUI Agent did not reach a terminal state")


def test_web_use_captures_and_controls_a_hidden_internal_page():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("set OPENPROGRAM_TEST_LIVE=1 to inspect the installed App")

    playwright = pytest.importorskip("playwright.sync_api")
    from openprogram.programs.tools.web.browser._chrome_bootstrap import (
        desktop_app_ws_url,
    )

    cdp_url = desktop_app_ws_url(timeout=2)
    if not cdp_url:
        pytest.skip("the installed /Applications/OpenProgram.app is not running")

    server, fixture_url, marker = _start_fixture()
    owner_id = "mcp:live-background:" + uuid.uuid4().hex
    web_session_id = ""
    original_tab = ""
    temporary_tab = ""
    try:
        with playwright.sync_playwright() as runtime:
            # Stopping Playwright disconnects its transport. browser.close()
            # is forbidden because it closes the attached Electron process.
            browser = runtime.chromium.connect_over_cdp(cdp_url, timeout=15_000)
            shell_page = next((
                page
                for context in browser.contexts
                for page in context.pages
                if page.url.startswith("http://127.0.0.1:18100")
                or page.url.startswith("http://localhost:18100")
            ), None)
            assert shell_page is not None
            try:
                original_tab, temporary_tab = _create_background_page(
                    shell_page, fixture_url,
                )
                _assert_unchanged(shell_page, original_tab)

                deadline = time.monotonic() + 15
                selected = None
                while selected is None and time.monotonic() < deadline:
                    _, listed = _dispatch({"command": "list_pages"}, owner_id)
                    selected = next((
                        page for page in listed.get("pages") or []
                        if page.get("tab_id") == temporary_tab
                        and page.get("title") == marker
                    ), None)
                    if selected is None:
                        time.sleep(0.1)
                assert selected is not None
                assert selected["visible"] is False

                _, observed = _dispatch({
                    "command": "observe",
                    "backend": "open_claude_chrome",
                    "page_context_token": selected["page_context_token"],
                    "arguments": {"detail": "interactive"},
                }, owner_id)
                assert observed["web_session_id"]
                assert observed["frame_id"]
                assert observed["title"] == marker
                assert marker in observed["text"]
                web_session_id = observed["web_session_id"]
                frame_id = observed["frame_id"]
                _assert_unchanged(shell_page, original_tab)

                screenshot_payload, screenshot = _dispatch({
                    "command": "act",
                    "web_session_id": web_session_id,
                    "arguments": {
                        "action": "screenshot",
                        "expected_frame_id": frame_id,
                    },
                }, owner_id)
                images = [
                    item for item in screenshot_payload["result"]["content"]
                    if item.get("type") == "image"
                ]
                assert len(images) == 1
                assert images[0]["mime_type"] == "image/png"
                png = base64.b64decode(images[0]["data"])
                assert png.startswith(b"\x89PNG\r\n\x1a\n")
                assert int.from_bytes(png[16:20], "big") > 0
                assert int.from_bytes(png[20:24], "big") > 0
                assert screenshot["frame_id"] == frame_id
                _assert_unchanged(shell_page, original_tab)

                change = next(
                    element for element in observed["elements"]
                    if element.get("name") == "Change state"
                )
                _, clicked = _dispatch({
                    "command": "act",
                    "web_session_id": web_session_id,
                    "arguments": {
                        "action": "click",
                        "expected_frame_id": frame_id,
                        "ref": change["ref"],
                    },
                }, owner_id)
                assert clicked["ok"] is True

                _, observed_after = _dispatch({
                    "command": "observe",
                    "web_session_id": web_session_id,
                    "arguments": {"detail": "interactive"},
                }, owner_id)
                assert "changed cursor=true" in observed_after["text"]
                _, verified = _dispatch({
                    "command": "verify",
                    "web_session_id": web_session_id,
                    "arguments": {
                        "expected_frame_id": observed_after["frame_id"],
                        "assertion": "text_contains",
                        "value": "changed cursor=true",
                    },
                }, owner_id)
                assert verified["passed"] is True
                _assert_unchanged(shell_page, original_tab)

                _, closed = _dispatch({
                    "command": "close", "web_session_id": web_session_id,
                }, owner_id)
                assert closed["closed"] is True
                web_session_id = ""
            finally:
                if temporary_tab:
                    _close_tabs(shell_page, {temporary_tab})
                if original_tab and _active_tab_id(shell_page) != original_tab:
                    _dom_click(shell_page.locator(
                        f'[role="tab"][data-tab-id="{original_tab}"]'
                    ))
    finally:
        if web_session_id:
            try:
                _dispatch({
                    "command": "close", "web_session_id": web_session_id,
                }, owner_id)
            except Exception:
                pass
        try:
            _release_owner(owner_id)
        except Exception:
            pass
        server.shutdown()
        server.server_close()


def test_public_gui_agent_creates_and_cleans_its_background_page():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("set OPENPROGRAM_TEST_LIVE=1 to inspect the installed App")
    if os.environ.get("OPENPROGRAM_TEST_REAL_HOME") != "1":
        pytest.skip("set OPENPROGRAM_TEST_REAL_HOME=1 to use the default profile")

    playwright = pytest.importorskip("playwright.sync_api")
    from openprogram.programs.tools.web.browser._chrome_bootstrap import (
        desktop_app_ws_url,
    )

    cdp_url = desktop_app_ws_url(timeout=2)
    if not cdp_url:
        pytest.skip("the installed /Applications/OpenProgram.app is not running")

    server, fixture_url, marker = _start_fixture()
    session_id = ""
    execution_id = ""
    shell_page = None
    created_tab = ""
    window_id = ""
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.connect_over_cdp(cdp_url, timeout=15_000)
            shell_page = next((
                page
                for context in browser.contexts
                for page in context.pages
                if page.url.startswith("http://127.0.0.1:18100")
                or page.url.startswith("http://localhost:18100")
            ), None)
            assert shell_page is not None
            before_tabs = _tab_ids(shell_page)
            before_pages = _page_inventory(shell_page)
            if before_pages:
                pytest.skip(
                    "safe automatic-create acceptance requires no existing Page "
                    "in the originating App window"
                )
            active_tab = _active_tab_id(shell_page)
            _assert_openprogram_not_frontmost()
            window_id = shell_page.evaluate(
                "() => window.openprogramDesktop?.windowId || ''"
            )
            assert window_id

            response = _fetch_json(shell_page, "/api/function/gui_agent", {
                "window_id": window_id,
                "kwargs": {
                    "task": (
                        f"Navigate to {fixture_url} and verify that the Page "
                        f"title contains {marker!r}."
                    ),
                    "surface": "browser",
                    "backend": "open_claude_chrome",
                    "max_steps": 3,
                    "max_seconds": 90,
                },
            })
            assert response["status"] == 200, response
            session_id = str(response["data"]["session_id"])
            execution_id = str(response["data"]["execution_id"])

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                new_pages = {
                    tab_id: page
                    for tab_id, page in _page_inventory(shell_page).items()
                    if tab_id not in before_pages
                }
                matching = [
                    tab_id for tab_id, page in new_pages.items()
                    if str(page.get("url") or "") in {
                        "https://www.google.com/", fixture_url,
                    }
                ]
                if len(matching) == 1:
                    created_tab = matching[0]
                    break
                time.sleep(0.1)
            assert created_tab, "GUI Agent did not create its background Page"
            _assert_unchanged(shell_page, active_tab)
            _assert_openprogram_not_frontmost()

            output = _wait_execution_output(session_id, execution_id, 120)

            shell_page.wait_for_function(
                "tabId => !document.querySelector("
                "`[role=tab][data-tab-id=\"${CSS.escape(tabId)}\"]`)",
                arg=created_tab,
                timeout=15_000,
            )
            created_tab = ""
            assert before_tabs.issubset(_tab_ids(shell_page))
            _assert_unchanged(shell_page, active_tab)
            _assert_openprogram_not_frontmost()

            assert output.get("status") == "succeeded", output
            assert output.get("success") is True
            assert output.get("infeasible_declared") is False
    finally:
        if shell_page is not None and session_id and execution_id:
            try:
                _fetch_json(shell_page, "/api/stop", {
                    "session_id": session_id,
                    "execution_id": execution_id,
                })
            except Exception:
                pass
        if shell_page is not None and window_id and created_tab:
            try:
                _close_owned_page(shell_page, window_id, created_tab)
            except Exception:
                pass
        server.shutdown()
        server.server_close()
