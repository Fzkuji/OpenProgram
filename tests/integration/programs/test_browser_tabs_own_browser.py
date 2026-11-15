"""The tab driver with no App window: a page opens in the browser the driver starts itself."""
import http.server
import threading

import pytest

from openprogram.host.resources import background_browser
from openprogram.programs.applications.browser import pages, tabs
from openprogram.runtime.conversation import surface_context

pytestmark = pytest.mark.browser

_FIRST = b'<title>First</title><a href="/second">Next</a>'
_SECOND = b"<title>Second</title><p>arrived</p>"


class _Pages(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the handler's name
        body = _SECOND if self.path == "/second" else _FIRST
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def driver(monkeypatch, tmp_path):
    pytest.importorskip("playwright.sync_api")
    value = tabs.Driver()
    monkeypatch.setattr(pages, "resource_db_path", lambda: tmp_path / "resources.db")
    # No App: nothing to open the page in, and nothing to list.
    monkeypatch.setattr(surface_context, "open_page", lambda url, background=True: {
        "ok": False, "reason_code": "desktop_unavailable", "error": "no App"})
    monkeypatch.setattr(value, "_app_may_come_back", lambda: False)
    monkeypatch.setattr(background_browser, "available", lambda: True)
    monkeypatch.setattr(tabs, "driver", lambda: value)
    yield value
    value._on_owner(value._own_stop)
    value._owner.shutdown()


def test_with_no_app_window_a_page_opens_is_operated_and_closes_in_the_drivers_own_browser(driver, site):
    opened = driver.run("open", {"url": site}, "s1")
    assert opened["ok"] and opened["title"] == "First" and "sign-ins" in opened["note"]
    listed = driver.run("tabs", {}, "s1")["tabs"]
    assert [(row["tab"], row["shown"], row.get("current")) for row in listed] == [(opened["tab"], "background", True)]
    # Another conversation does not list it as its own.
    assert driver.run("tabs", {}, "s2") == {"ok": True, "other_tabs": 1, "tabs": []}

    clicked = driver.run("click", {"role": "link", "name": "Next"}, "s1")
    assert clicked["ok"] and clicked["title"] == "Second" and "arrived" in clicked["page"]
    shot = driver.run("screenshot", {}, "s1")
    assert shot["ok"] and shot["_images"][0][:8] == b"\x89PNG\r\n\x1a\n"

    assert tabs.close_conversation_tabs("s1") == 1
    # The last tab took the browser with it, and its record.
    assert driver._own is None and driver._playwright is None
    assert pages.BrowserResourceStore().tabs_opened_by("s1") == set()
    assert driver.run("read", {}, "s1")["reason_code"] == "tab_not_found"


def test_a_page_that_does_not_open_leaves_no_tab_and_no_browser_behind(driver):
    failed = driver.run("open", {"url": "http://127.0.0.1:9/"}, "s1")
    assert failed["ok"] is False and failed["reason_code"] == "open_failed"
    assert driver._own is None and driver.run("tabs", {"all": True}, "s1")["tabs"] == []


def test_the_drivers_own_browser_closes_once_its_tabs_were_idle(driver, site, monkeypatch):
    opened = driver.run("open", {"url": site}, "s1")
    assert opened["ok"]
    monkeypatch.setattr(tabs, "OWN_IDLE_SECONDS", 0.0)
    driver._on_owner(driver._own_sweep)
    assert driver._own is None
    gone = driver.run("read", {"tab": opened["tab"]}, "s1")
    assert gone["ok"] is False and gone["reason_code"] in {"tab_closed", "tab_not_found"}
