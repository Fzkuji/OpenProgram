"""Whether a browser with no window can be started here, and which one is tried."""
from openprogram.host.resources import background_browser


def test_a_browser_is_there_when_the_runtime_ships_one_or_the_user_has_chrome(_no_browser_of_its_own, monkeypatch, tmp_path):
    available = _no_browser_of_its_own
    monkeypatch.setattr(background_browser, "_found", None)
    monkeypatch.setattr(background_browser, "_installed", lambda: False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert available() is False
    # A miss is looked for again: the runtime's browsers can arrive after the process started.
    (tmp_path / "chromium-1223").mkdir()
    assert available() is True
    monkeypatch.setattr(background_browser, "_found", None)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "none"))
    monkeypatch.setattr(background_browser, "_installed", lambda: True)
    assert available() is True


def test_the_shipped_browser_is_tried_before_the_users_and_the_last_failure_is_raised():
    tried = []

    class _Chromium:
        def launch(self, headless, **options):
            tried.append((headless, options.get("channel", "")))
            if options.get("channel") != "chrome":
                raise RuntimeError("missing")
            return "browser"

    playwright = type("P", (), {"chromium": _Chromium()})()
    assert background_browser.launch(playwright) == "browser"
    assert tried == [(True, "chromium"), (True, ""), (True, "chrome")]

    class _Never:
        def launch(self, headless, **options):
            raise RuntimeError(options.get("channel", "bundled"))

    try:
        background_browser.launch(type("P", (), {"chromium": _Never()})())
    except RuntimeError as exc:
        assert str(exc) == "msedge"
    else:
        raise AssertionError("no browser started, yet no failure")
