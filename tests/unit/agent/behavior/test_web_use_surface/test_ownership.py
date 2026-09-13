"""surface ownership tests."""
from __future__ import annotations
from ._support import (
    REPO_ROOT,
    _WS,
    asyncio,
    pytest,
    threading,
)


def test_disabled_surface_is_visible_to_model_but_has_no_preview_or_binding():
    from openprogram.agent import surface_context

    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "w:right",
        "region": "right",
        "access": "disabled",
        "title": "Example",
        "url": "https://example.com/private?q=secret",
    }, _WS())

    surface = context["surfaces"][0]
    assert surface["capabilities"] == []
    assert surface["preview_status"] == "disabled"
    assert "binding_id" not in surface
    assert surface_context.tool_enabled(context) is False
    rendered = surface_context.render_for_model(context)
    assert "Agent access is disabled" in rendered
    assert "q=secret" not in rendered



def test_surface_preview_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    owner = _WS()

    def disconnect_before_result(*_args, **_kwargs):
        webtab.release_connection(owner)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
            "geometry_revision": 1,
        }

    monkeypatch.setattr(webtab, "request_on_ws", disconnect_before_result)
    context = surface_context.capture({
        "version": 1,
        "window_id": "window-1",
        "tab_id": "tab-1",
        "region": "right",
        "access": "enabled",
        "geometry_revision": 1,
    }, owner)

    assert context["surfaces"][0]["preview_status"] == "unavailable"
    assert "binding_id" not in context["surfaces"][0]
    assert all(entry[0] is not owner for entry in webtab._bindings.values())



def test_active_page_capture_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])

    def disconnect_before_result(*_args, **_kwargs):
        webtab.release_connection(owner)
        return {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
        }

    monkeypatch.setattr(webtab, "request_on_ws", disconnect_before_result)
    with pytest.raises(RuntimeError, match="connection changed during Page binding"):
        surface_context.capture_active()
    assert all(entry[0] is not owner for entry in webtab._bindings.values())



def test_bound_webtab_request_uses_only_the_registered_socket(monkeypatch):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "w:right", "target-right",
    )
    seen = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: seen.append((ws, command)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": command.get("tab_id"),
            "target_id": "target-right",
        },
    )

    assert webtab.request_bound_tab(binding_id)["ok"] is True
    assert seen == [(owner, {
        "op": "activate", "window_id": "window-1", "tab_id": "w:right",
    })]
    webtab.release_binding(binding_id)
    assert webtab.request_bound_tab(binding_id)["ok"] is False



def test_bound_webtab_request_rejects_stale_expected_revision_before_ipc(
    monkeypatch,
):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1",
    )
    revisions = webtab.binding_revisions(binding_id)
    sent = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True},
    )
    try:
        result = webtab.request_bound_tab(
            binding_id,
            expected_page_revision=revisions["page_revision"],
            expected_access_revision=revisions["access_revision"] + 1,
        )
    finally:
        webtab.release_binding(binding_id)

    assert result["reason_code"] == "page_context_stale"
    assert sent == []



def test_bound_webtab_request_rejects_geometry_changed_during_activation(
    monkeypatch,
):
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    binding_id = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", geometry_revision=9,
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-1",
            "target_id": "target-1",
            "geometry_revision": 10,
        },
    )

    result = webtab.request_bound_tab(
        binding_id,
        expected_geometry_revision=9,
    )

    assert result["reason_code"] == "page_context_stale"
    assert binding_id not in webtab._bindings



def test_webtab_disconnect_revokes_owned_bindings_and_wakes_waiters():
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    other = _WS()
    owned = webtab.register_binding(owner, "window-1", "tab-1", "target-1")
    retained = webtab.register_binding(other, "window-2", "tab-2", "target-2")
    event = threading.Event()
    holder: dict = {}
    webtab._pending["owned-request"] = (event, holder, owner)
    try:
        webtab.release_connection(owner)

        assert owned not in webtab._bindings
        assert retained in webtab._bindings
        assert event.is_set()
        assert holder["result"] == {
            "ok": False,
            "error": "originating desktop connection disconnected",
        }
    finally:
        webtab._pending.pop("owned-request", None)
        webtab.release_binding(owned)
        webtab.release_binding(retained)



def test_open_page_uses_registered_binding_revisions_when_native_geometry_is_set(
    monkeypatch,
):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    native_revision = webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "window-1"
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=15.0: {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "w:https://example.test/",
            "target_id": "target-opened",
            "url": "https://example.test/",
            "title": "Example",
            "geometry_revision": 7,
            "page_revision": native_revision,
            "access_revision": native_revision,
            "created": True,
            "reused": False,
        },
    )
    context = {}
    try:
        context = surface_context.open_page("https://example.test/")
        surface = context["surfaces"][0]
        owned = webtab.binding_revisions(surface["binding_id"])
        assert owned["geometry_revision"] == 7
        assert surface["geometry_revision"] == owned["geometry_revision"]
        assert surface["page_revision"] == owned["page_revision"]
        assert surface["access_revision"] == owned["access_revision"]
        assert surface["page_revision"] != native_revision
        assert surface["access_revision"] != native_revision
        assert owned["page_revision"] > 0
        assert owned["access_revision"] > 0
    finally:
        for surface in context.get("surfaces") or []:
            webtab.release_binding(surface["binding_id"])
        webtab.release_connection(owner)



def test_open_page_rolls_back_when_binding_registration_fails(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    sent = []
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )

    def request(_ws, command, timeout=15.0):
        sent.append((command, timeout))
        if command["op"] == "open":
            return {
                "ok": True,
                "window_id": "window-1",
                "tab_id": "tab-created",
                "target_id": "target-created",
                "created": True,
                "reused": False,
            }
        return {"ok": True}

    monkeypatch.setattr(webtab, "request_on_ws", request)
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("connection changed")
        ),
    )

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1", background=True,
    )

    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert sent[-1] == ({
        "op": "close",
        "window_id": "window-1",
        "tab_id": "tab-created",
    }, 15.0)



@pytest.mark.parametrize("ownership", [
    {"created": False, "reused": True},
    {"created": True},
    {"created": True, "reused": True},
    {"created": False, "reused": False},
    {"created": 1, "reused": False},
    {"created": True, "reused": 0},
])
def test_open_page_does_not_close_unowned_page_when_binding_fails(
    monkeypatch, ownership,
):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    sent = []
    monkeypatch.setattr(server, "_ws_connections", [owner])
    monkeypatch.setattr(
        webtab,
        "registered_desktop_windows",
        lambda: [(owner, "window-1", 7)],
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda _ws, command, timeout=15.0: sent.append((command, timeout)) or {
            "ok": True,
            "window_id": "window-1",
            "tab_id": "tab-user",
            "target_id": "target-user",
            **ownership,
        },
    )
    monkeypatch.setattr(
        webtab,
        "register_binding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("connection changed")
        ),
    )

    result = surface_context.open_page(
        "https://example.test/", window_id="window-1",
    )

    assert result["ok"] is False
    assert result["reason_code"] == "page_context_stale"
    assert sent == [({
        "op": "open",
        "url": "https://example.test/",
        "window_id": "window-1",
    }, 15.0)]



def test_close_page_only_releases_reused_page_binding(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui.ws_actions import webtab

    released = []
    monkeypatch.setattr(
        webtab,
        "release_binding",
        lambda binding_id: released.append(binding_id),
    )
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a reused Page must not be closed")
        ),
    )

    result = surface_context.close_page({
        "window_id": "window-1",
        "surfaces": [{
            "window_id": "window-1",
            "tab_id": "tab-user",
            "binding_id": "surface-user",
            "agent_owned": False,
        }],
    })

    assert result == {
        "ok": True,
        "closed": False,
        "released": True,
        "borrowed": True,
    }
    assert released == ["surface-user"]



def test_context_page_inventory_rejects_originating_window_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    primary = _WS()
    secondary = _WS()
    monkeypatch.setattr(server, "_ws_connections", [primary, secondary])
    binding = webtab.register_binding(
        primary, "window-1", "tab-primary", "target-primary",
        allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "surfaces": [{"binding_id": binding}],
    }
    asyncio.run(webtab.handle_webtab_register(secondary, {
        "action": "webtab_register", "window_id": "window-2",
    }))
    webtab.release_connection(primary)
    monkeypatch.setattr(server, "_ws_connections", [secondary])

    calls = []
    monkeypatch.setattr(
        webtab,
        "request_on_ws",
        lambda ws, command, timeout=5.0: calls.append((ws, command)) or {
            "ok": True,
            "window_id": "window-2",
            "pages": [{
                "tab_id": "tab-secondary",
                "target_id": "target-secondary",
                "url": "https://secondary.test/",
                "title": "Secondary",
                "visible": True,
                "focused": True,
                "region": "center",
            }],
        },
    )

    with pytest.raises(RuntimeError, match="accepted Page binding is unavailable"):
        surface_context.capture_pages(accepted)
    assert calls == []
    webtab.release_connection(secondary)



def test_page_inventory_cannot_recreate_binding_after_disconnect(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.webui import server
    from openprogram.webui.ws_actions import webtab

    owner = _WS()
    monkeypatch.setattr(server, "_ws_connections", [owner])
    binding = webtab.register_binding(
        owner, "window-1", "tab-1", "target-1", allow_background=True,
    )
    accepted = {
        "context_id": "accepted",
        "surfaces": [{"binding_id": binding}],
    }
    monkeypatch.setattr(
        webtab,
        "request_page_inventory",
        lambda binding_id: {
            "ok": True,
            "window_id": "window-1",
            "pages": [{
                "tab_id": "tab-1",
                "target_id": "target-1",
                "url": "https://example.test/",
                "title": "Example",
                "visible": True,
                "focused": True,
                "region": "center",
            }],
        },
    )
    original_owner_revision = webtab.binding_owner_revision

    def disconnect_after_owner_read(binding_id):
        result = original_owner_revision(binding_id)
        webtab.release_connection(owner)
        return result

    monkeypatch.setattr(
        webtab, "binding_owner_revision", disconnect_after_owner_read,
    )

    with pytest.raises(RuntimeError, match="connection changed during Page binding"):
        surface_context.capture_pages(accepted)
    assert all(entry[0] is not owner for entry in webtab._bindings.values())



def test_bound_browser_task_bypasses_only_the_nested_default_ask(monkeypatch):
    from types import SimpleNamespace

    from openprogram.agent.dispatcher import TurnRequest
    from openprogram.agent.turn_request_context import (
        get_turn_request,
        reset_turn_request,
        set_turn_request,
    )
    from openprogram.programs.workflow import browser as module

    class _Controller:
        tool = SimpleNamespace(name="browser_page")
        initial_url = ""
        binding_id = ""
        max_steps = 0
        _terminal_reason = ""
        _frame = {"frame_id": "frame-1", "url": "http://localhost/"}
        _last_result = None

        def execute(self, **_kwargs):
            return self._frame

        def tool_for_actions(self, _actions):
            return self.tool

        def final_result(self, *, summary: str, reason_code: str | None = None):
            return {
                "status": "failed",
                "reason_code": reason_code or "verification_missing",
                "summary": summary,
            }

        def close(self):
            return None

    controller = _Controller()
    monkeypatch.setattr(module, "_new_controller", lambda: controller)

    seen_modes = []

    class _Runtime:
        def exec(self, **_kwargs):
            seen_modes.append(get_turn_request().permission_mode)
            return "not verified"

    outer = TurnRequest(
        session_id="session-1",
        user_text="",
        agent_id="main",
        source="web",
        permission_mode="ask",
    )
    token = set_turn_request(outer)
    try:
        module._run_browser_task(
            task="Click the link",
            url="",
            max_steps=3,
            max_seconds=30,
            runtime=_Runtime(),
            binding_id="binding-1",
        )
        assert len(seen_modes) == 12
        assert set(seen_modes) == {"bypass"}
        assert get_turn_request() is outer
    finally:
        reset_turn_request(token)



def test_chat_query_owner_always_releases_captured_surface_bindings():
    source = (
        REPO_ROOT
        / "apps/server/openprogram_server/_webui/_execute/chat.py"
    ).read_text(encoding="utf-8")
    capture_at = source.index("surface_context = _capture_surface")
    finally_at = source.index("finally:", capture_at)
    release_at = source.index("_release_surface_bindings(surface_context)", finally_at)
    finish_at = source.index("_s._finish_owned_run", finally_at)

    assert capture_at < finally_at < release_at < finish_at

