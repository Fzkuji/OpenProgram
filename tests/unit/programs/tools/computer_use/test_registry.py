"""Which targets exist now: what each device's connection method yields, this computer first."""
from __future__ import annotations

import pytest

from openprogram.host import machines
from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.routing import registry

from tests.support.screen_targets import PIXELS, FakeProvider, install_machines, local, machine, user_browsers
from openprogram.programs.tools.computer_use.computer_use.providers.screen import shots as screen_shots


@pytest.fixture
def fleet(monkeypatch):
    app = FakeProvider("window")
    browser = FakeProvider("browser", extras=("list_tabs", "navigate", "list_panes"))
    chrome = FakeProvider("browser", app="chrome", extras=("list_tabs", "navigate"),
                          claims=lambda window: "its tabs" if window.get("app") == "Google Chrome" else "")
    lab = FakeProvider("screen", "lab", capabilities=PIXELS)
    devbox = FakeProvider("window", "devbox")
    listed = [local(),
              machine("devbox"),
              machine("lab"),
              machine("off", enabled=False)]
    install_machines(monkeypatch, listed, {"local": [app, browser, chrome], "devbox": [devbox], "lab": [lab],
                                  "off": [FakeProvider("screen", "off")]})
    return {"app": app, "browser": browser, "chrome": chrome, "lab": lab, "devbox": devbox}


def test_targets_are_listed_by_machine_this_computer_first(fleet):
    rows = registry.listing()
    assert [row.machine.id for row in rows] == ["local", "devbox", "lab"]
    assert [provider.target for provider in rows[0].providers] == ["window", "browser", "browser chrome"]
    assert list(registry.providers()) == ["window", "browser", "browser chrome", "devbox window", "lab screen"]


def test_the_machines_registry_puts_this_computer_first():
    found = machines.list_machines({"machines": {"items": [
        {"id": "lab", "connection": {"kind": "osworld", "url": "http://10.0.0.2:5000"}}]}})
    assert [item.id for item in found] == ["local", "lab"]
    assert found[0].is_local and found[0].method == "local" and found[1].method == "osworld"


@pytest.fixture
def real_kinds(monkeypatch):
    """The real openers, with this computer's backends, screen driver and browser service faked; the
    configuration is ``config``."""
    from types import SimpleNamespace
    from openprogram.programs.tools.computer_use.computer_use.providers.browser import built_in as built_in_browser
    from openprogram.programs.tools.computer_use.computer_use.providers.window import platforms as window_platforms
    monkeypatch.setattr(window_platforms, "supported", lambda: True)
    monkeypatch.setattr(screen_shots, "_driver_for", lambda system: object)
    browser = SimpleNamespace(extension=user_browsers())
    monkeypatch.setattr(built_in_browser, "service", lambda: browser)
    config: dict = {}
    monkeypatch.setattr(machines.user_config, "read_config_value", lambda key: config.get(key))
    monkeypatch.setattr(registry.user_config, "read_config_value", lambda key: config.get(key))
    return config


def test_this_computer_lists_windows_and_its_browser_and_the_screen_only_when_on(real_kinds):
    assert list(registry.providers("conv")) == ["window", "browser"]
    real_kinds["computer_use"] = {"whole_screen": True}
    assert list(registry.providers("conv")) == ["window", "screen", "browser"]


def _targets(config) -> dict[str, list[str]]:
    return {row.machine.id: [provider.target for provider in row.providers] for row in registry.listing("conv")}


def test_computer_use_decides_what_each_connection_method_offers(real_kinds):
    """osworld gives the VM's screen; openprogram the other computer's window, screen and browser; ssh and
    docker run commands only, so they offer computer_use nothing."""
    real_kinds["machines"] = {"items": [
        {"id": "lab", "connection": {"kind": "osworld", "url": "http://10.0.0.2:5000"}},
        {"id": "studio", "name": "Studio", "connection": {"kind": "openprogram", "url": "http://10.0.0.9:18100"}},
        {"id": "devbox", "connection": {"kind": "ssh", "target": "me@devbox"}},
        {"id": "box", "connection": {"kind": "docker"}}]}
    rows = _targets(real_kinds)
    assert rows["lab"] == ["lab screen"]
    assert rows["studio"] == ["studio window", "studio screen", "studio browser"]
    assert rows["devbox"] == [] and rows["box"] == []


def test_a_paired_device_takes_any_browser_and_forwards_it(real_kinds):
    real_kinds["machines"] = {"items": [
        {"id": "studio", "connection": {"kind": "openprogram", "url": "http://10.0.0.9:18100"}}]}
    found = registry.resolve({"device": "studio", "type": "browser", "app": "Google Chrome"})
    assert found.forwards and found.machine == "studio" and found.app == "chrome"
    assert found.target == "studio browser chrome"
    assert registry.resolve({"device": "studio", "type": "window", "app": "Safari"}).app == ""


def test_a_switched_off_machine_yields_nothing(fleet):
    assert "off" not in [row.machine.id for row in registry.listing()]
    assert "off screen" not in registry.providers()


def test_a_kind_that_fails_to_open_leaves_the_others(monkeypatch):
    listed = [local(), machine("broken"), machine("lab")]
    install_machines(monkeypatch, listed, {"local": [FakeProvider("window")],
                                  "lab": [FakeProvider("screen", "lab")]}, failing=("broken",))
    assert list(registry.providers()) == ["window", "lab screen"]
    assert [row.providers for row in registry.listing() if row.machine.id == "broken"] == [()]


def test_a_connection_whose_kind_is_not_loaded_yields_nothing(monkeypatch):
    install_machines(monkeypatch, [local(), machine("vnc-box", "vnc-box", "no_such_kind")],
            {"local": [FakeProvider("window")]})
    assert list(registry.providers()) == ["window"]


def test_a_call_is_resolved_by_device_type_and_app_without_regard_to_case(fleet):
    assert registry.resolve({"device": "LAB", "type": "Screen"}) is fleet["lab"]
    assert registry.resolve({"type": "browser", "app": " Google Chrome "}) is fleet["chrome"]
    assert registry.resolve({"type": "browser"}) is fleet["browser"]
    assert registry.resolve({"type": "browser", "app": "built_in"}) is fleet["browser"]
    assert registry.find({"type": "WINDOW"}) is fleet["app"]
    assert registry.find({"device": "this computer", "type": "window"}) is fleet["app"]
    assert registry.find({"device": "devbox", "type": "window"}) is fleet["devbox"]


def test_a_call_is_never_rewritten_to_another_device_or_type(fleet):
    for written in ({"type": "screen"}, {"device": "lab", "type": "window"}, {"type": "vm"},
                    {"device": "devbox", "type": "browser"}, {"device": "lab"}, {"type": "browser", "app": "edge"}):
        with pytest.raises(errors.UnknownTarget):
            registry.resolve(written)
        assert registry.find(written) is None


def test_an_unknown_type_lists_the_targets_set_up_now(fleet):
    with pytest.raises(errors.UnknownTarget) as caught:
        registry.resolve({"type": "vm"})
    assert caught.value.text() == (
        "UNKNOWN_TARGET: type is one of window, screen, browser, not 'vm'. Targets now: type=\"window\"; "
        "type=\"browser\"; type=\"browser\", app=\"chrome\"; device=\"devbox\", type=\"window\"; "
        "device=\"lab\", type=\"screen\".")


def test_an_unknown_device_lists_the_targets_set_up_now(fleet):
    with pytest.raises(errors.UnknownTarget) as caught:
        registry.resolve({"device": "vm", "type": "screen"})
    assert caught.value.text().startswith("UNKNOWN_TARGET: nothing on device 'vm' can be operated now. Targets now: ")


def test_a_type_the_device_lacks_lists_that_devices_targets(fleet):
    with pytest.raises(errors.UnknownTarget) as caught:
        registry.resolve({"type": "screen"})
    assert caught.value.text() == ("UNKNOWN_TARGET: type='screen' is not available here. Targets here: "
                                   'type="window"; type="browser"; type="browser", app="chrome".')


def test_a_browser_that_is_not_set_up_says_which_ones_can_be(fleet):
    with pytest.raises(errors.UnknownTarget) as caught:
        registry.resolve({"type": "browser", "app": "edge"})
    assert caught.value.text().startswith("UNKNOWN_TARGET: there is no browser 'edge' to operate here: app is "
                                          "one of the user's browsers listed below")
    assert caught.value.text().endswith('Targets here: type="window"; type="browser"; type="browser", app="chrome".')


@pytest.mark.parametrize("written, meant", [
    ("Google Chrome", "chrome"), ("chrome", "chrome"), ("Chrome Beta", "chrome-beta"), ("chrome_canary", "chrome-canary"),
    ("Microsoft Edge Dev", "edge-dev"), ("msedge", "edge"), ("Brave Browser", "brave"), ("com.brave.Browser", "brave"),
    ("Vivaldi", "vivaldi"), ("Opera", "opera"), ("Arc", "arc"), ("Chromium", "chromium"), ("Safari", "safari"),
    ("com.apple.Safari", "safari"), ("Mozilla Firefox", "firefox"), ("FIREFOX", "firefox"), ("built-in", ""),
    ("netscape", "netscape")])
def test_a_browser_is_named_in_lower_case_whatever_the_model_writes(written, meant):
    assert registry.browser_name(written) == meant


def test_a_call_to_safari_resolves_to_the_safari_target(fleet, monkeypatch):
    from tests.support.screen_targets import FakeProvider
    safari = FakeProvider("browser", app="safari")
    install_machines(monkeypatch, [local()], {"local": [safari]})
    assert registry.resolve({"type": "browser", "app": "Safari"}) is safari


def test_with_nothing_set_up_an_unknown_target_says_none(monkeypatch):
    install_machines(monkeypatch, [local()], {})
    with pytest.raises(errors.UnknownTarget, match="Targets now: none."):
        registry.resolve({"type": "window"})


def test_an_unknown_action_finds_the_targets_of_the_same_machine_that_have_it(fleet):
    elsewhere = registry.elsewhere(fleet["app"])
    assert elsewhere("navigate") == ['type="browser"', 'type="browser", app="chrome"']
    assert elsewhere("list_panes") == ['type="browser"']
    # Another machine's targets are never offered for this one.
    assert registry.elsewhere(fleet["lab"])("navigate") == []


def test_an_unknown_action_finds_the_targets_that_take_it_under_a_carried_over_name(fleet):
    elsewhere = registry.elsewhere(fleet["app"])
    assert elsewhere("goto") == ['type="browser"', 'type="browser", app="chrome"']
    assert elsewhere("new_tab") == []
    assert elsewhere("go_back") == []


def test_a_generic_word_is_never_sent_to_a_target_that_reads_it_as_something_else(monkeypatch):
    # A browser reads open and close as its tabs; on a window they mean an application or a window.
    app = FakeProvider("window", extras=("open_file",))
    browser = FakeProvider("browser", extras=("create_tab", "close_tab", "navigate", "back"))
    install_machines(monkeypatch, [local()], {"local": [app, browser]})
    elsewhere = registry.elsewhere(app)
    assert elsewhere("open") == [] and elsewhere("close") == []
    assert elsewhere("new_tab") == ['type="browser"'] and elsewhere("go_back") == ['type="browser"']


def test_a_window_a_target_operates_itself_is_claimed(fleet):
    assert registry.claim({"app": "Google Chrome", "title": "Inbox"}) == ('type="browser", app="chrome"', "its tabs")
    assert registry.claim({"app": "Notes"}) is None


def test_a_claim_is_asked_for_the_conversation(monkeypatch):
    asked = []

    class Chrome(FakeProvider):
        def claims(self, window, conversation=""):
            asked.append(conversation)
            return "its tabs"

    install_machines(monkeypatch, [local()], {"local": [Chrome("browser", app="chrome")]})
    assert registry.claim({"app": "Google Chrome"}, "c1") == ('type="browser", app="chrome"', "its tabs")
    assert asked == ["c1"]


def test_a_claim_that_fails_claims_nothing(monkeypatch):
    def broken(window):
        raise RuntimeError("no")

    install_machines(monkeypatch, [local()], {"local": [FakeProvider("browser", app="chrome", claims=broken)]})
    assert registry.claim({"app": "Google Chrome"}) is None
