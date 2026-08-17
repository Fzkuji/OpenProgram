from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


pytestmark = pytest.mark.live


def _frontmost_application() -> str:
    return subprocess.check_output(
        [
            "osascript",
            "-e",
            'tell application "System Events" to get name of first '
            "application process whose frontmost is true",
        ],
        text=True,
    ).strip()


def _bounds(window: dict) -> tuple[int, int, int, int]:
    bounds = window.get("kCGWindowBounds") or {}
    return (
        int(bounds.get("X") or 0),
        int(bounds.get("Y") or 0),
        int(bounds.get("Width") or 0),
        int(bounds.get("Height") or 0),
    )


def _occlusion_snapshot(quartz, shell_page) -> dict:
    frontmost = _frontmost_application()
    if frontmost == "OpenProgram":
        pytest.skip("place another application over OpenProgram before the live test")

    screen_bounds = shell_page.evaluate(
        "() => ({ x: window.screenX, y: window.screenY, "
        "width: window.outerWidth, height: window.outerHeight })"
    )
    windows = quartz.CGWindowListCopyWindowInfo(
        quartz.kCGWindowListOptionOnScreenOnly,
        quartz.kCGNullWindowID,
    )
    candidates = [
        (index, window)
        for index, window in enumerate(windows)
        if int(window.get("kCGWindowLayer") or 0) == 0
        and _bounds(window)[2] >= 500
        and _bounds(window)[3] >= 400
    ]
    target = min(
        (
            item for item in candidates
            if item[1].get("kCGWindowOwnerName") == "OpenProgram"
        ),
        key=lambda item: sum(abs(value) for value in (
            _bounds(item[1])[0] - int(screen_bounds["x"]),
            _bounds(item[1])[1] - int(screen_bounds["y"]),
            _bounds(item[1])[2] - int(screen_bounds["width"]),
            _bounds(item[1])[3] - int(screen_bounds["height"]),
        )),
        default=None,
    )
    if target is None:
        pytest.skip("the installed OpenProgram window is not on screen")
    target_index, target_window = target
    tx, ty, tw, th = _bounds(target_window)

    # The 8 px inset excludes the native frame/shadow. The whole renderer
    # client area must be behind one higher z-order window from the frontmost
    # application before the test is allowed to touch the background Page.
    inset = 8
    covering = next((
        (index, window)
        for index, window in candidates
        if index < target_index
        and window.get("kCGWindowOwnerName") == frontmost
        and _bounds(window)[0] <= tx + inset
        and _bounds(window)[1] <= ty + inset
        and _bounds(window)[0] + _bounds(window)[2] >= tx + tw - inset
        and _bounds(window)[1] + _bounds(window)[3] >= ty + th - inset
    ), None)
    if covering is None:
        pytest.skip("OpenProgram is not fully occluded by the frontmost window")
    _, covering_window = covering
    return {
        "frontmost": frontmost,
        "cover_pid": int(covering_window.get("kCGWindowOwnerPID") or 0),
        "cover_window_id": int(covering_window.get("kCGWindowNumber") or 0),
        "cover_bounds": _bounds(covering_window),
        "target_window_id": int(target_window.get("kCGWindowNumber") or 0),
        "target_bounds": _bounds(target_window),
    }


def _assert_occlusion_unchanged(quartz, shell_page, expected: dict) -> None:
    assert _occlusion_snapshot(quartz, shell_page) == expected


class _FixtureHandler(BaseHTTPRequestHandler):
    marker = ""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        body = f"""<!doctype html>
<html><head><title>{self.marker}</title></head>
<body style="margin:0;background:#18324a;color:white;font:24px sans-serif">
  <main><h1>{self.marker}</h1>
    <button id="change" onclick="document.getElementById('status').textContent='changed'">Change state</button>
    <p id="status">ready</p>
  </main>
</body></html>""".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def _start_fixture() -> tuple[ThreadingHTTPServer, str, str]:
    marker = "OpenProgram occluded Web Use " + uuid.uuid4().hex
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
        (item.get("text") for item in result.get("content") or []
         if item.get("type") == "text"),
        "{}",
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


def _tab_ids(shell_page) -> list[str]:
    tabs = shell_page.locator('[role="tab"][data-tab-id]')
    return [
        str(tabs.nth(index).get_attribute("data-tab-id"))
        for index in range(tabs.count())
    ]


def _dispatch_dom_click(locator) -> None:
    locator.evaluate(
        "node => node.dispatchEvent(new MouseEvent('click', "
        "{ bubbles: true, cancelable: true, view: window }))"
    )


def _open_background_fixture_page(
    shell_page, url: str, marker: str,
) -> tuple[str, set[str]]:
    before = set(_tab_ids(shell_page))
    original = str(shell_page.locator(
        '[role="tab"][data-tab-id][aria-selected="true"]'
    ).first.get_attribute("data-tab-id"))
    try:
        new_tab = shell_page.get_by_role(
            "button", name=re.compile(r"^(New tab|新标签页)$")
        ).last
        _dispatch_dom_click(new_tab)
        shell_page.wait_for_function(
            "before => [...document.querySelectorAll('[role=tab][data-tab-id]')]"
            ".some(node => !before.includes(node.dataset.tabId))",
            arg=list(before),
        )
        browser_button = shell_page.get_by_role(
            "button", name=re.compile(r"^(Browser|浏览器)$"), exact=True,
        )
        browser_button.wait_for(state="attached")
        _dispatch_dom_click(browser_button)
        address = shell_page.get_by_role(
            "textbox", name=re.compile(r"^(Address|地址)$"), exact=True,
        )
        address.wait_for(state="attached")
        address.fill(url)
        address.press("Enter")
        shell_page.wait_for_function(
            "([before, expectedId]) => { const active = document.querySelector("
            "'[role=tab][data-tab-id][aria-selected=true]'); "
            "return active && !before.includes(active.dataset.tabId) "
            "&& active.dataset.tabId === expectedId; }",
            arg=[list(before), f"w:{url}"],
            timeout=10_000,
        )
        temporary = set(_tab_ids(shell_page)) - before
        assert len(temporary) == 1
        temporary_id = next(iter(temporary))
        shell_page.wait_for_function(
            "([tabId, title]) => document.querySelector("
            "`[role=tab][data-tab-id=\"${tabId}\"]`)?.getAttribute('title') "
            "=== title",
            arg=[temporary_id, marker],
            timeout=10_000,
        )

        original_tab = shell_page.locator(
            f'[role="tab"][data-tab-id="{original}"]'
        )
        assert original_tab.count() == 1
        _dispatch_dom_click(original_tab)
        shell_page.wait_for_function(
            "tabId => document.querySelector("
            "`[role=tab][data-tab-id=\"${tabId}\"]`)?.getAttribute("
            "'aria-selected') === 'true'",
            arg=original,
        )
        shell_page.wait_for_timeout(100)
        return original, temporary
    except Exception:
        _close_temporary_tabs(shell_page, original, set(_tab_ids(shell_page)) - before)
        raise


def _close_temporary_tabs(shell_page, original: str, temporary: set[str]) -> None:
    for tab_id in temporary:
        tab = shell_page.locator(f'[role="tab"][data-tab-id="{tab_id}"]')
        if tab.count():
            close = tab.locator("xpath=..").locator(
                'button[aria-label="Close tab"], button[aria-label="关闭标签"]'
            )
            if close.count():
                _dispatch_dom_click(close)
    original_tab = shell_page.locator(
        f'[role="tab"][data-tab-id="{original}"]'
    )
    if original_tab.count():
        _dispatch_dom_click(original_tab)


def test_web_use_reads_screenshots_and_acts_while_app_is_fully_occluded():
    if os.environ.get("OPENPROGRAM_TEST_LIVE") != "1":
        pytest.skip("set OPENPROGRAM_TEST_LIVE=1 to inspect the installed App")
    if os.uname().sysname != "Darwin":
        pytest.skip("the independent occlusion assertion currently uses Quartz")

    quartz = pytest.importorskip("Quartz")
    playwright = pytest.importorskip("playwright.sync_api")
    from openprogram.programs.functions.browser._chrome_bootstrap import (
        desktop_app_ws_url,
    )

    cdp_url = desktop_app_ws_url(timeout=2)
    if not cdp_url:
        pytest.skip("the installed /Applications/OpenProgram.app is not running")

    server, fixture_url, marker = _start_fixture()
    owner_id = "mcp:live-occluded:" + uuid.uuid4().hex
    web_session_id = ""
    original = ""
    temporary: set[str] = set()
    try:
        with playwright.sync_playwright() as runtime:
            # Do not call browser.close(): on a CDP-attached Electron instance
            # it closes the application instead of merely disconnecting.
            browser = runtime.chromium.connect_over_cdp(cdp_url, timeout=15_000)
            shell_page = next((
                page
                for context in browser.contexts
                for page in context.pages
                if page.url.startswith("http://127.0.0.1:18100")
                or page.url.startswith("http://localhost:18100")
            ), None)
            assert shell_page is not None
            occlusion = _occlusion_snapshot(quartz, shell_page)

            try:
                original, temporary = _open_background_fixture_page(
                    shell_page, fixture_url, marker,
                )
                _assert_occlusion_unchanged(quartz, shell_page, occlusion)

                _, listed = _dispatch({"command": "list_pages"}, owner_id)
                selected = next(
                    page for page in listed.get("pages") or []
                    if page.get("title") == marker
                )
                assert selected["visible"] is False

                _, observed = _dispatch({
                    "command": "observe",
                    "backend": "open_claude_chrome",
                    "page_context_token": selected["page_context_token"],
                    "arguments": {"detail": "interactive"},
                }, owner_id)
                assert observed["ok"] is True
                assert observed["title"] == marker
                assert marker in observed["text"]
                web_session_id = observed["web_session_id"]
                frame_id = observed["frame_id"]

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
                _assert_occlusion_unchanged(quartz, shell_page, occlusion)

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
                assert clicked["observe_required"] is True

                _, observed_after = _dispatch({
                    "command": "observe",
                    "web_session_id": web_session_id,
                    "arguments": {"detail": "interactive"},
                }, owner_id)
                assert "changed" in observed_after["text"]
                _, verified = _dispatch({
                    "command": "verify",
                    "web_session_id": web_session_id,
                    "arguments": {
                        "expected_frame_id": observed_after["frame_id"],
                        "assertion": "text_contains",
                        "value": "changed",
                    },
                }, owner_id)
                assert verified["passed"] is True
                _assert_occlusion_unchanged(quartz, shell_page, occlusion)

                _, closed = _dispatch({
                    "command": "close", "web_session_id": web_session_id,
                }, owner_id)
                assert closed["closed"] is True
                web_session_id = ""
            finally:
                if original:
                    _close_temporary_tabs(shell_page, original, temporary)
                    temporary.clear()
                    _assert_occlusion_unchanged(quartz, shell_page, occlusion)
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
