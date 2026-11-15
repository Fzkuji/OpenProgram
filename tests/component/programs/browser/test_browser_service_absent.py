"""Without the browser application, the core runs and says the browser is not enabled.

The core reaches the built-in browser only through the ``browser``
service. These tests withdraw that service, and what the application's
``setup`` registered, and check that each place the core used to import
the browser degrades instead of failing.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def no_browser(monkeypatch):
    from openprogram.runtime.context import components
    from openprogram.programs._host import services as _services

    _services.load_programs()
    monkeypatch.delitem(_services._provided, "browser")
    monkeypatch.setitem(components._REGISTRY, "L2", [
        component for component in components._REGISTRY["L2"]
        if not component.name.startswith("application:browser:")
    ])
    assert _services.lookup("browser") is None


_PAGE_CONTEXT = {
    "surfaces": [{
        "surface_key": "s1",
        "preview_status": "ready",
        "capabilities": ["observe"],
        "preview": {"visible_text_excerpt": "Example page"},
    }],
}


def test_the_service_names_its_members():
    from openprogram.programs._host import services as _services

    _services.load_programs()
    browser = _services.lookup("browser")
    assert browser is not None
    for member in (
        "pages", "tabs", "extension", "user_browser_names",
        "open_tab", "authorized_page", "browser_of",
    ):
        assert hasattr(browser, member), member


def test_the_call_gets_no_pages_section(no_browser):
    from openprogram.runtime.context.components import build_turn_sections

    assert build_turn_sections(
        {"id": "main"},
        {"surface_context": _PAGE_CONTEXT, "browser_enabled": True},
    ) == ""


def test_the_turn_gets_no_screen_tool_for_pages(no_browser):
    from types import SimpleNamespace

    from openprogram.runtime.conversation.turn_runtime.loop_runner import _configure_browser_tool

    tools = [SimpleNamespace(name="web_search")]
    configured, enabled = _configure_browser_tool(tools, _PAGE_CONTEXT, enabled=True)
    assert enabled is False
    assert [tool.name for tool in configured] == ["web_search"]


def test_permission_policy_sees_no_user_browser(no_browser):
    from openprogram.runtime.permissions import policy

    assert policy._user_browser({"args": {"browser": "chrome"}}) == ""


def test_computer_use_reports_the_browser_target_unavailable(no_browser):
    from openprogram.programs.tools.computer_use.computer_use_agent import tool as entry
    from openprogram.programs.tools.computer_use.computer_use_agent.targets import browser

    assert browser.available() is False
    assert browser.actions() == ()
    result = entry.computer_use_agent(target="browser", action="tabs")
    assert result.is_error
    assert "the browser is not enabled" in result.text


def test_native_page_commands_are_refused(no_browser):
    from openprogram.programs._host.framework import web_interface

    with pytest.raises(ValueError, match="browser_not_enabled"):
        with web_interface.authorized_page({}, "window-1"):
            pass
