"""Tests for the browser tool.

No real Playwright launch — we inject a fake ``sync_playwright`` into
the module under test so every verb round-trips through the session
table and the page API without touching the network or a browser.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openprogram.functions.tools.browser import browser as tool


@pytest.fixture(autouse=True)
def _clean_sessions():
    tool._sessions.clear()
    yield
    tool._sessions.clear()


class _FakePage:
    def __init__(self):
        self.url = ""
        self.calls: list[tuple[str, tuple]] = []
        self._title = "Fake Page"
        self._body_text = "Hello from fake browser."

    def set_default_timeout(self, ms): self.calls.append(("set_timeout", (ms,)))
    def goto(self, url):
        self.url = url
        self.calls.append(("goto", (url,)))
    def title(self): return self._title
    def click(self, selector): self.calls.append(("click", (selector,)))
    def fill(self, selector, text): self.calls.append(("fill", (selector, text)))
    def press(self, selector, key): self.calls.append(("press", (selector, key)))
    def screenshot(self, *, path, full_page):
        self.calls.append(("screenshot", (path, full_page)))
        # Touch the file so the assertion can verify it.
        with open(path, "wb") as f:
            f.write(b"\x89PNG fake\n")
    def inner_text(self, selector): return self._body_text
    def locator(self, selector):
        loc = MagicMock()
        loc.count.return_value = 2
        items = [MagicMock(), MagicMock()]
        items[0].inner_text.return_value = "match-0"
        items[1].inner_text.return_value = "match-1"
        loc.nth.side_effect = lambda i: items[i]
        return loc


class _FakeContext:
    def __init__(self): self.page = _FakePage()
    def new_page(self): return self.page
    def close(self): self.closed = True
    def add_init_script(self, *_a, **_kw): pass
    def storage_state(self, *, path): open(path, "w").write("{}")


class _FakeBrowser:
    def __init__(self): self.ctx = _FakeContext()
    def new_context(self, **_kw): return self.ctx
    def close(self): self.closed = True


class _FakeChromium:
    def __init__(self): self.browser = _FakeBrowser()
    def launch(self, **_kw): return self.browser


class _FakePlaywright:
    def __init__(self): self.chromium = _FakeChromium()
    def stop(self): self.stopped = True


class _FakeSyncPlaywrightCM:
    def __init__(self): self.pw = _FakePlaywright()
    def start(self): return self.pw


@pytest.fixture
def fake_playwright(monkeypatch):
    """Inject a fake ``sync_playwright`` for the tool to use, and pretend
    Playwright is importable so ``check_playwright`` returns True.

    Also disables the auto-bootstrap path (sidecar Chrome via CDP) so
    these unit tests exercise the launch-new-browser branch even when
    `engine="auto"` is the default — that branch is what the fake
    chromium fixture is built to exercise.
    """
    monkeypatch.setattr(tool, "check_playwright", lambda: True)

    # Disable bootstrap auto-detection: pretend no sidecar is running.
    from openprogram.functions.tools.browser import _chrome_bootstrap as boot
    monkeypatch.setattr(boot, "cdp_url_if_available", lambda: None)
    monkeypatch.setattr(boot, "launch_sidecar_chrome",
                        lambda port=9222, timeout_s=30.0: False)

    # browser.py imports sync_playwright lazily inside _open. Patch the
    # symbol at the stdlib module location since there's no static
    # attribute to replace on the tool module itself.
    import sys
    fake_module = MagicMock()
    fake_module.sync_playwright = _FakeSyncPlaywrightCM
    sys.modules.setdefault("playwright", MagicMock())
    sys.modules["playwright.sync_api"] = fake_module
    yield
    sys.modules.pop("playwright.sync_api", None)


def test_open_creates_session(fake_playwright):
    out = tool.execute(action="open")
    assert out.startswith("Opened browser session `br_")
    # Extract the id so the next test step can use it.
    sid = out.split("`")[1]
    assert sid in tool._sessions


def test_navigate_goto(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    out = tool.execute(action="navigate", session_id=sid, url="https://example.com")
    assert "Navigated" in out
    assert tool._sessions[sid]["page"].url == "https://example.com"


def test_navigate_requires_url(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    out = tool.execute(action="navigate", session_id=sid)
    assert "Error" in out and "url" in out


def test_click_and_type(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    assert "Clicked" in tool.execute(action="click", session_id=sid, selector="button.go")
    out = tool.execute(action="type", session_id=sid, selector="#q", text="hello", submit=True)
    assert "Typed 5 char" in out and "submitted" in out
    calls = tool._sessions[sid]["page"].calls
    assert ("click", ("button.go",)) in calls
    assert ("fill", ("#q", "hello")) in calls
    assert ("press", ("#q", "Enter")) in calls


def test_extract_whole_body(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    out = tool.execute(action="extract", session_id=sid)
    assert out == "Hello from fake browser."


def test_extract_with_selector(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    out = tool.execute(action="extract", session_id=sid, selector=".item")
    assert "2 match(es)" in out
    assert "match-0" in out and "match-1" in out


def test_screenshot_writes_file(tmp_path, fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    target = tmp_path / "shot.png"
    out = tool.execute(action="screenshot", session_id=sid, path=str(target))
    assert "Saved screenshot" in out
    assert target.exists()


def test_close_removes_session(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    out = tool.execute(action="close", session_id=sid)
    assert "Closed" in out
    assert sid not in tool._sessions


def test_list_reports_empty():
    out = tool.execute(action="list")
    assert "no open browser sessions" in out.lower()


def test_list_reports_open_sessions(fake_playwright):
    sid = tool.execute(action="open").split("`")[1]
    tool.execute(action="navigate", session_id=sid, url="https://example.org")
    out = tool.execute(action="list")
    assert sid in out and "example.org" in out


def test_unknown_session_id(fake_playwright):
    out = tool.execute(action="navigate", session_id="br_bogus", url="https://x")
    assert "no browser session" in out


def test_missing_action():
    assert "action" in tool.execute().lower()


def test_install_hint_when_playwright_missing(monkeypatch):
    # Simulate environment without playwright installed.
    monkeypatch.setattr(tool, "check_playwright", lambda: False)
    out = tool.execute(action="open")
    assert "pip install playwright" in out


# ---------------------------------------------------------------------------
# SPEC schema sanity (no fixture needed)
# ---------------------------------------------------------------------------


def test_spec_lists_all_verbs():
    expected = {
        "open", "navigate", "click", "type", "extract", "screenshot",
        "close", "list", "upload", "wait", "eval", "html",
        "hover", "select", "press", "accessibility", "screenshot_b64",
        "tabs", "new_tab", "switch_tab", "download", "cookies",
        "save_login", "frame_eval", "frames", "console", "block",
        "viewport",
    }
    enum = set(tool.SPEC["parameters"]["properties"]["action"]["enum"])
    assert enum == expected, (
        f"missing: {expected - enum}, extra: {enum - expected}"
    )


def test_spec_engine_includes_auto():
    engines = set(tool.SPEC["parameters"]["properties"]["engine"]["enum"])
    assert "auto" in engines
    assert {"chromium", "patchright", "camoufox"} <= engines


def test_unknown_action_clear_error():
    out = tool.execute(action="totally-not-a-thing")
    assert "unknown action" in out.lower()


# ---------------------------------------------------------------------------
# Bootstrap helpers — pure file/socket level, no browser launched
# ---------------------------------------------------------------------------


def test_bootstrap_chrome_binary_returns_path_or_none():
    import os as _os
    from openprogram.functions.tools.browser._chrome_bootstrap import chrome_binary
    result = chrome_binary()
    assert result is None or _os.path.isfile(result)


def test_bootstrap_real_user_data_dir_is_platform_specific():
    from openprogram.functions.tools.browser._chrome_bootstrap import real_user_data_dir
    p = real_user_data_dir()
    # macOS: ``…/Library/Application Support/Google/Chrome`` (capital C).
    # Linux: ``~/.config/google-chrome`` (lowercase, hyphenated).
    # Windows: ``…/Google/Chrome/User Data``. Lower-case both sides so
    # the assertion holds across all three runners.
    lp = p.lower()
    assert "chrome" in lp or "chromium" in lp


def test_bootstrap_sidecar_dir_under_dot_openprogram():
    from openprogram.functions.tools.browser._chrome_bootstrap import sidecar_dir
    p = sidecar_dir()
    assert ".openprogram" in str(p)
    assert "chrome-profile" in p.name


def test_bootstrap_port_file_under_dot_openprogram():
    from openprogram.functions.tools.browser._chrome_bootstrap import port_file
    p = port_file()
    assert ".openprogram" in str(p)
    assert "browser-cdp-port" in p.name


def test_bootstrap_is_port_listening_negative():
    from openprogram.functions.tools.browser._chrome_bootstrap import is_port_listening
    assert not is_port_listening(1)


def test_bootstrap_is_port_listening_positive():
    """Bind a throwaway TCP socket and confirm detection."""
    import socket
    from openprogram.functions.tools.browser._chrome_bootstrap import is_port_listening
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert is_port_listening(port)
    finally:
        sock.close()


def test_bootstrap_read_last_used_profile_default(tmp_path):
    from openprogram.functions.tools.browser._chrome_bootstrap import read_last_used_profile
    assert read_last_used_profile(str(tmp_path)) == "Default"


def test_bootstrap_read_last_used_profile_from_local_state(tmp_path):
    import json
    from openprogram.functions.tools.browser._chrome_bootstrap import read_last_used_profile
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"last_used": "Profile 5"}})
    )
    assert read_last_used_profile(str(tmp_path)) == "Profile 5"


def test_bootstrap_read_last_used_profile_falls_back_on_bad_json(tmp_path):
    from openprogram.functions.tools.browser._chrome_bootstrap import read_last_used_profile
    (tmp_path / "Local State").write_text("{ this is not valid json")
    assert read_last_used_profile(str(tmp_path)) == "Default"


def test_bootstrap_cdp_url_if_available_returns_none(monkeypatch, tmp_path):
    from openprogram.functions.tools.browser import _chrome_bootstrap as boot
    monkeypatch.setattr(boot, "port_file", lambda: tmp_path / "browser-cdp-port")
    monkeypatch.setattr(boot, "is_port_listening", lambda port, **kw: False)
    assert boot.cdp_url_if_available() is None


def test_bootstrap_cdp_url_if_available_finds_running_port(monkeypatch, tmp_path):
    from openprogram.functions.tools.browser import _chrome_bootstrap as boot
    pf = tmp_path / "browser-cdp-port"
    pf.write_text("9234")
    monkeypatch.setattr(boot, "port_file", lambda: pf)
    monkeypatch.setattr(boot, "is_port_listening", lambda port, **kw: port == 9234)
    assert boot.cdp_url_if_available() == "http://localhost:9234"


def test_bootstrap_lock_path_under_dot_openprogram():
    from openprogram.functions.tools.browser._chrome_bootstrap import _bootstrap_lock_path
    p = _bootstrap_lock_path()
    assert ".openprogram" in str(p)
    assert "browser-cdp.lock" in p.name
