"""list_pages discovery across Desktop disconnects and App restarts."""
from __future__ import annotations
import asyncio
import pytest
from ._support import _WS


def _inventory(window_id: str, suffix: str) -> dict:
    return {
        "ok": True,
        "window_id": window_id,
        "inventory_revision": 1,
        "pages": [{
            "tab_id": f"tab-{suffix}",
            "target_id": f"target-{suffix}",
            "url": f"https://{suffix}.test/",
            "title": suffix.upper(),
            "visible": True,
            "focused": True,
            "region": "center",
        }],
    }


def test_listing_survives_disconnected_origin_window(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    secondary = _WS()
    monkeypatch.setattr(server, "_ws_connections", [secondary])
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    monkeypatch.setattr(
        webtab, "request_on_ws",
        lambda ws, command, timeout=5.0: _inventory("window-2", "b"),
    )
    origin_only = surface_context.window_context("window-1")
    try:
        # The strict capture keeps refusing the gone origin window ...
        with pytest.raises(surface_context.DesktopUnavailableError):
            surface_context.capture_pages(origin_only)
        # ... but discovery keeps every other registered window reachable.
        context = surface_context.capture_pages_for_listing(origin_only)
        assert [page["window_id"] for page in context["surfaces"]] == ["window-2"]
        surface_context.release_bindings(context)
    finally:
        webtab.release_connection(secondary)


def test_listing_waits_for_restarting_desktop_to_register(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    restarted = _WS()
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(surface_context, "DESKTOP_RECONNECT_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(surface_context, "_DESKTOP_RECONNECT_POLL_SECONDS", 0.0)
    monkeypatch.setattr(
        webtab, "request_on_ws",
        lambda ws, command, timeout=5.0: _inventory("main", "a"),
    )
    polls = []

    def reconnect_after_two_polls(seconds):
        polls.append(seconds)
        if len(polls) == 2:
            # The App reopens and its renderer registers the same window id.
            server._ws_connections.append(restarted)
            asyncio.run(webtab.handle_webtab_register(restarted, {
                "action": "webtab_register", "window_id": "main",
            }))

    monkeypatch.setattr(surface_context.time, "sleep", reconnect_after_two_polls)
    try:
        context = surface_context.capture_pages_for_listing(
            surface_context.window_context("main"),
        )
        assert len(polls) == 2
        assert context["window_id"] == "main"
        assert context["surfaces"][0]["tab_id"] == "tab-a"
        surface_context.release_bindings(context)
    finally:
        webtab.release_connection(restarted)


def test_listing_reports_desktop_unavailable_after_bounded_wait(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server

    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(surface_context, "DESKTOP_RECONNECT_WAIT_SECONDS", 0.0)
    with pytest.raises(surface_context.DesktopUnavailableError):
        surface_context.capture_pages_for_listing(
            surface_context.window_context("main"),
        )


def test_listing_does_not_promote_another_window_when_origin_inventory_fails(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import webtab

    primary = _WS()
    secondary = _WS()
    monkeypatch.setattr(server, "_ws_connections", [primary, secondary])
    asyncio.run(webtab.handle_webtab_register(primary, {
        "action": "webtab_register", "window_id": "window-1",
    }))
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    calls = []

    def inventory(ws, command, timeout=5.0):
        calls.append(command.get("window_id"))
        if ws is primary:
            return {"ok": False, "reason_code": "timeout"}
        return _inventory("window-2", "b")

    monkeypatch.setattr(webtab, "request_on_ws", inventory)
    try:
        with pytest.raises(RuntimeError, match="Page inventory is unavailable") as raised:
            surface_context.capture_pages_for_listing(
                surface_context.window_context("window-1"),
            )
        assert not isinstance(raised.value, surface_context.DesktopUnavailableError)
        assert calls == ["window-1"]
    finally:
        webtab.release_connection(primary)
        webtab.release_connection(secondary)


def test_workflow_subprocess_listing_stays_in_its_window(monkeypatch):
    from openprogram.runtime.conversation import surface_context
    from openprogram.interfaces.server.ws_actions import webtab

    monkeypatch.setenv("OPENPROGRAM_IN_AGENTIC_SUBPROCESS", "1")
    monkeypatch.setattr(surface_context, "DESKTOP_RECONNECT_WAIT_SECONDS", 5.0)
    monkeypatch.setattr(surface_context, "_DESKTOP_RECONNECT_POLL_SECONDS", 0.0)
    answers = iter([
        {"ok": False, "reason_code": "desktop_unavailable",
         "error": "DesktopUnavailableError: originating Desktop window is unavailable"},
        {"ok": True, "context": {"context_id": "ctx", "window_id": "main",
                                  "surfaces": []}},
    ])
    requests = []
    monkeypatch.setattr(
        webtab, "_request",
        lambda command, timeout: requests.append(dict(command)) or next(answers),
    )
    context = surface_context.capture_pages_for_listing(
        surface_context.window_context("main"),
    )
    assert context["window_id"] == "main"
    # Both attempts asked the parent for the same window; none widened it.
    assert [request.get("window_id") for request in requests] == ["main", "main"]
