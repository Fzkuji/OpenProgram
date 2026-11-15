"""One agent waiting on a tab does not hold the browser driver for the others."""
from __future__ import annotations

import threading
import time

import pytest

from openprogram.programs.applications.browser import tabs


def test_one_agent_waiting_does_not_hold_the_browser_for_another(monkeypatch):
    driver = tabs.Driver()
    tab = tabs.Tab(id="t1", window_id="main", tab_id="a")
    monkeypatch.setattr(driver, "_shows", lambda tab, text: False)
    monkeypatch.setattr(driver, "_report", lambda tab, before_url: {"ok": True, "tab": tab.id})
    failures = []

    def wait():
        with pytest.raises(tabs.BrowserError) as missing:
            driver._wait(tab, {"text": "Done", "seconds": 1.5})
        failures.append(missing.value.reason_code)

    waiting = threading.Thread(target=wait)
    waiting.start()
    try:
        time.sleep(0.2)
        started = time.monotonic()
        assert driver._on_owner(lambda: "another agent's click") == "another agent's click"
        assert time.monotonic() - started < 0.5
    finally:
        waiting.join()
        driver._owner.shutdown()
    assert failures == ["text_not_found"]
