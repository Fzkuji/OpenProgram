import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def test_document_window_preview_edit_autosave_history(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    bundle = tmp_path / "document-window.js"
    subprocess.run(["node", "--no-warnings", "apps/web/tests/build-document-window-browser.mjs", str(bundle)], check=True)
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: pytest.fail(f"document window page error: {error}"))
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(tmp_path), **kwargs)
            def log_message(self, *_args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        (tmp_path / "index.html").write_text(f'<div id="root"></div><script src="/document-window.js"></script>')
        writes = []
        def content(route):
            if route.request.method == "GET":
                route.fulfill(status=200, headers={"content-type": "application/octet-stream", "x-document-revision": "a" * 64}, body=b"old")
            else:
                writes.append(route.request.post_data)
                route.fulfill(status=200, content_type="application/json", body='{"ok":true,"status":"committed","revision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}')
                page.evaluate("window.__writesReady = true")
        page.route("**/api/documents/content?*", content)
        page.route("**/api/documents/history?*", lambda route: route.fulfill(status=200, content_type="application/json", body='{"entries":[{"version_id":"a"}]}'))
        page.route("**/api/documents/history/content?*", lambda route: route.fulfill(status=200, body=b"history"))
        page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded", timeout=10000)
        assert page.get_by_role("button", name="Preview").get_attribute("aria-pressed") == "true"
        assert page.locator("textarea").count() == 0
        assert page.get_by_role("button", name="Save").count() == 0
        page.get_by_role("button", name="Edit").click()
        editor = page.locator("textarea")
        editor.fill("new")
        page.wait_for_function("() => window.__writesReady === true", timeout=5000)
        assert writes and "new" in writes[-1]
        page.get_by_role("button", name="History").click()
        assert page.get_by_role("button", name="Preview").count() >= 2
        page.get_by_role("button", name="Preview").nth(1).click()
        assert page.get_by_text("history").count() == 1
        browser.close()
        server.shutdown()
