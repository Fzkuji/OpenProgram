from __future__ import annotations

import base64
import json
import os
import re
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
    from openprogram.mcp_server.service import _worker_web_use_request

    payload = _worker_web_use_request(
        "/api/web-use",
        {"arguments": arguments, "owner_id": owner_id},
        timeout=30,
    )
    return payload, _tool_json(payload)


def _release_owner(owner_id: str) -> None:
    from openprogram.mcp_server.service import _worker_web_use_request

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


def _create_background_page(shell_page, url: str, marker: str) -> tuple[str, str]:
    before = _tab_ids(shell_page)
    original = _active_tab_id(shell_page)
    try:
        _dom_click(shell_page.get_by_role(
            "button", name=re.compile(r"^(New tab|新标签页)$")
        ).last)
        shell_page.wait_for_function(
            "before => [...document.querySelectorAll('[role=tab][data-tab-id]')]"
            ".some(node => !before.includes(node.dataset.tabId))",
            arg=list(before),
        )
        browser_button = shell_page.get_by_role(
            "button", name=re.compile(r"^(Browser|浏览器)$"), exact=True,
        )
        browser_button.wait_for(state="attached")
        _dom_click(browser_button)
        address = shell_page.get_by_role(
            "textbox", name=re.compile(r"^(Address|地址)$"), exact=True,
        )
        address.fill(url)
        address.press("Enter")
        expected_tab = f"w:{url}"
        shell_page.wait_for_function(
            "tabId => document.querySelector("
            "`[role=tab][data-tab-id=\"${tabId}\"]`)?.ariaSelected === 'true'",
            arg=expected_tab,
            timeout=10_000,
        )
        created = _tab_ids(shell_page) - before
        assert created == {expected_tab}

        _dom_click(shell_page.locator(
            f'[role="tab"][data-tab-id="{original}"]'
        ))
        shell_page.wait_for_function(
            "tabId => document.querySelector("
            "`[role=tab][data-tab-id=\"${tabId}\"]`)?.ariaSelected === 'true'",
            arg=original,
        )
        shell_page.wait_for_timeout(100)
        return original, expected_tab
    except Exception:
        _close_tabs(shell_page, _tab_ids(shell_page) - before)
        raise


def _assert_unchanged(shell_page, original_tab: str) -> None:
    assert _active_tab_id(shell_page) == original_tab


def test_web_use_captures_and_controls_a_hidden_internal_page():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("set OPENPROGRAM_TEST_LIVE=1 to inspect the installed App")

    playwright = pytest.importorskip("playwright.sync_api")
    from openprogram.programs.functions.browser._chrome_bootstrap import (
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
                    shell_page, fixture_url, marker,
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
