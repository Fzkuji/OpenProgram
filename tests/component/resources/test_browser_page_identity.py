"""Exact native Page identity through public web_use / retained projection."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from openprogram.webui.ws_actions import webtab


NATIVE_TAB_ID = "fcb18270-5f88-4f5f-ae23-efb5ed0f4b61"
OTHER_TAB_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
FORGED_TAB_ID = "00000000-1111-2222-3333-444444444444"


class _FrameController:
    """BrowserPageController observe/_mutated shape. target.tab_id is untrusted."""

    supports_operation_guard = True

    def __init__(self, *, tab_id, target_id, url, title=""):
        self.binding_id = ""
        self.page_revision = 0
        self.access_revision = 0
        self.geometry_revision = 0
        self.tab_id = tab_id
        self.target_id = target_id
        self.url = url
        self.title = title
        self._frame = None

    def _origin(self):
        parsed = urlsplit(self.url)
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port}"

    def execute(self, action="observe", url="", **kwargs):
        del kwargs
        forged_target = {
            "kind": "web_tab",
            "tab_id": FORGED_TAB_ID,
            "target_id": self.target_id,
        }
        if action == "observe":
            self._frame = {
                "frame_id": "frame_1_native",
                "url": self.url,
                "origin": self._origin(),
                "title": self.title,
                "target": forged_target,
                "viewport": {"width": 1280, "height": 800},
            }
            return dict(self._frame)
        if action == "navigate":
            self.url = url or self.url
            self.title = "Resource test PAGE"
            self._frame = None
            return {
                "ok": True,
                "detail": f"navigated to {self.url}",
                "observe_required": True,
                "url": self.url,
                "title": self.title,
                "target": forged_target,
            }
        return {"ok": True, "observe_required": True, "target": forged_target}

    def close(self):
        self._frame = None


class _FrameAdapter:
    supports_operation_guard = True

    def __init__(self, factory):
        self._factory = factory

    def _controller(self, session):
        if session.controller is None:
            controller = self._factory()
            controller.binding_id = session.binding_id
            controller.page_revision = session.page_revision
            controller.access_revision = session.access_revision
            controller.geometry_revision = session.geometry_revision
            session.controller = controller
        return session.controller

    def observe(self, session, arguments, *, before_dispatch=None):
        del arguments
        if before_dispatch is not None:
            before_dispatch()
        return self._controller(session).execute(action="observe")

    def act(self, session, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        return self._controller(session).execute(**dict(arguments))

    def verify(self, session, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        return {"ok": True, "passed": True}

    def close(self, session):
        if session.controller is not None:
            session.controller.close()


@pytest.fixture(autouse=True)
def _clean_webtab(monkeypatch):
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()

    def request_on_ws(_ws, command, timeout=5.0):
        del timeout
        window_id = command.get("window_id")
        tab_id = command.get("tab_id")
        for entry in webtab._bindings.values():
            if entry[1] == window_id and entry[2] == tab_id:
                return {
                    "ok": True,
                    "window_id": entry[1],
                    "tab_id": entry[2],
                    "target_id": entry[3],
                    "geometry_revision": entry[7],
                }
        return {"ok": False, "reason_code": "page_context_stale"}

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    yield
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()


def _bind_owner(window_id, tab_id, target_id):
    owner = object()
    webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = window_id
    binding_id = webtab.register_binding(owner, window_id, tab_id, target_id)
    return owner, binding_id, webtab.binding_page_key(binding_id)


def _binding_generation(binding_id):
    return int(
        webtab.binding_page_descriptor(binding_id).get("connection_generation") or 0
    )


def _own_execution(monkeypatch, session_id="parent", execution_id="exec-native"):
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_execution_id",
        lambda: execution_id,
    )
    monkeypatch.setattr(
        "openprogram.agent.run_control.get_current_session_id",
        lambda: session_id,
    )
    monkeypatch.setattr(
        "openprogram.processes.current_owner",
        lambda: (session_id, execution_id, None),
    )
    monkeypatch.setattr(
        "openprogram.execution.default_store",
        lambda: SimpleNamespace(
            get_execution_input=lambda _eid: SimpleNamespace(
                user_message_id="u1", assistant_message_id="a1",
            ),
            get_execution=lambda _eid: None,
        ),
    )


def _registry(factory):
    from openprogram.programs.workflow.browser.web_use_runtime import (
        SUPPORTED_BACKENDS,
        WebUseSessionRegistry,
    )

    adapter = _FrameAdapter(factory)
    return WebUseSessionRegistry(
        adapters={name: adapter for name in SUPPORTED_BACKENDS},
        release_context=lambda context: None,
    )


def _forged_context(binding_id, page_key):
    return {
        "context_id": "page_ctx_forged",
        "window_id": "win-other",
        "surfaces": [
            {
                "binding_id": binding_id,
                "window_id": "win-other",
                "tab_id": OTHER_TAB_ID,
                "title": "Forged",
                "page_key": page_key,
            },
        ],
    }


def test_attribute_operating_page_does_not_wipe_binding_identity(tmp_path, monkeypatch):
    from openprogram.browser_resources import (
        BrowserResourceStore, attribute_operating_page,
    )

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    del owner
    store = BrowserResourceStore()
    store.update_display(
        page_key, title="Example Domain", target="https://example.com",
        connection_generation=_binding_generation(binding_id),
    )
    attribute_operating_page(page_key)
    row = store.get_resource(page_key)
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"
    assert row["title"] == "Example Domain"
    assert row["target"] == "https://example.com"
    webtab.release_binding(binding_id)


def test_dead_binding_and_forged_result_cannot_fill_or_unfreeze(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    store = BrowserResourceStore()
    store.retain(
        page_key="page:blank", window_id="", tab_id="", title="Old",
        target="https://example.com", connection_generation=0,
        session_id="parent", conversation_session_id="parent",
        execution_id="exec-native", live=True,
    )
    registry = _registry(lambda: _FrameController(
        tab_id=FORGED_TAB_ID, target_id="target-x",
        url="http://127.0.0.1:9/forged", title="Forged",
    ))
    observed = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="owner-native",
        binding_id="surface_dead",
        page_key="page:blank",
        page_context=_forged_context("surface_dead", "page:blank"),
    )
    assert observed.get("ok") is False
    blank = store.get_resource("page:blank")
    assert blank["tab_id"] == ""
    assert blank["window_id"] == ""
    assert blank["title"] == "Old"
    assert blank["target"] == "https://example.com"

    store.mark_unavailable("page:blank")
    frozen = store.get_resource("page:blank")
    assert frozen["lifecycle"] == "unavailable"
    store.update_display(
        "page:blank", title="Hacked", target="http://evil.test/",
        connection_generation=99,
    )
    store.retain(
        page_key="page:blank", window_id="win-other", tab_id=OTHER_TAB_ID,
        title="Hacked", target="http://evil.test/", connection_generation=99,
        session_id="parent", conversation_session_id="parent", live=True,
    )
    registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="owner-native",
        binding_id="surface_dead",
        page_key="page:blank",
        page_context=_forged_context("surface_dead", "page:blank"),
    )
    still = store.get_resource("page:blank")
    assert still["lifecycle"] == "unavailable"
    assert int(still["live"] or 0) == 0
    assert still["tab_id"] == ""
    assert still["window_id"] == ""
    assert still["title"] == "Old"
    assert still["target"] == "https://example.com"
    registry.close_all()


def test_valid_binding_projects_controller_display_after_navigate(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore
    from openprogram.store import SessionNodeWriter
    from openprogram.store.session.session_store import SessionStore
    from openprogram.context.nodes import Call

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    fixture_url = "http://127.0.0.1:62147/page/4"
    controller = _FrameController(
        tab_id=NATIVE_TAB_ID, target_id="target-1",
        url="https://example.com", title="Example Domain",
    )
    registry = _registry(lambda: controller)
    page_context = {
        "context_id": "page_ctx_native",
        "window_id": "win",
        "surfaces": [
            {
                "binding_id": "surface_other",
                "window_id": "win",
                "tab_id": OTHER_TAB_ID,
                "title": "Other",
                "page_key": "page:other",
            },
            {
                "binding_id": binding_id,
                "window_id": "win",
                "tab_id": NATIVE_TAB_ID,
                "title": "",
                "page_key": page_key,
            },
        ],
    }

    observed = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="owner-native",
        binding_id=binding_id,
        page_key=page_key,
        page_context=page_context,
    )
    assert observed.get("ok") is not False
    store = BrowserResourceStore()
    row = store.get_resource(page_key)
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"
    assert row["tab_id"] != FORGED_TAB_ID
    assert row["tab_id"] != OTHER_TAB_ID
    assert row["target"] == "https://example.com"
    assert row["title"] == "Example Domain"

    listed = registry.list_pages(owner_id="owner-native", context=page_context)
    token = next(
        page["page_context_token"]
        for page in listed["pages"]
        if page["tab_id"] == NATIVE_TAB_ID
    )
    reused = registry.execute(
        command="observe",
        backend="open_claude_chrome",
        owner_id="owner-native",
        page_context_token=token,
    )
    assert reused["web_session_id"] == observed["web_session_id"]
    assert reused.get("session_reused") is True

    acted = registry.execute(
        command="act",
        backend="open_claude_chrome",
        owner_id="owner-native",
        web_session_id=observed["web_session_id"],
        arguments={
            "action": "navigate",
            "url": fixture_url,
            "expected_frame_id": "frame_1_native",
        },
    )
    assert acted.get("ok") is not False
    after_nav = store.get_resource(page_key)
    assert after_nav["tab_id"] == NATIVE_TAB_ID
    assert after_nav["window_id"] == "win"
    assert after_nav["target"] == fixture_url
    assert after_nav["title"] == "Resource test PAGE"

    webtab.release_binding(binding_id)
    released = store.get_resource(page_key)
    assert int(released["live"] or 0) == 1
    assert released["tab_id"] == NATIVE_TAB_ID
    assert released["target"] == fixture_url
    rebound = webtab.register_binding(owner, "win", NATIVE_TAB_ID, "target-1")
    assert webtab.binding_page_key(rebound) == page_key
    restored = store.get_resource(page_key)
    assert restored["tab_id"] == NATIVE_TAB_ID
    assert restored["window_id"] == "win"
    assert restored["target"] == fixture_url

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    writer.append(Call(id="a1", role="llm", predecessor="u1", seq=2))
    db.set_head("parent", "a1")
    public = store.list_rows(
        "parent",
        executions=[SimpleNamespace(
            execution_id="exec-native", session_id="parent",
            parent_execution_id=None,
            status=SimpleNamespace(value="running"),
        )],
        parents={"exec-native": None},
        session_store=db,
        execution_inputs={
            "exec-native": SimpleNamespace(
                user_message_id="u1", assistant_message_id="a1",
            ),
        },
    )
    projected = next(item for item in public if item["resource_id"] == page_key)
    assert projected["tab_id"] == NATIVE_TAB_ID
    assert projected["window_id"] == "win"
    assert projected["target"] == fixture_url
    assert projected["title"] == "Resource test PAGE"
    registry.close_all()


def test_closed_row_display_stays_frozen_after_later_epoch(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    owner, binding_id, page_key = _bind_owner("win", NATIVE_TAB_ID, "target-1")
    store = BrowserResourceStore()
    store.update_display(
        page_key, title="Kept", target="https://example.com",
        connection_generation=_binding_generation(binding_id),
    )
    webtab.release_binding(binding_id)
    store.mark_closed(page_key)
    closed = store.get_resource(page_key)
    assert closed["lifecycle"] == "closed"
    store.update_display(
        page_key, title="Hacked", target="http://evil.test/",
        connection_generation=99,
    )
    store.retain(
        page_key=page_key, window_id="win-other", tab_id=OTHER_TAB_ID,
        title="Hacked", target="http://evil.test/", connection_generation=99,
        session_id="parent", live=True,
    )
    row = store.get_resource(page_key)
    assert row["lifecycle"] == "closed"
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"
    assert row["title"] == "Kept"
    assert row["target"] == "https://example.com"
    rebound = webtab.register_binding(owner, "win", NATIVE_TAB_ID, "target-1")
    assert webtab.binding_page_key(rebound) == page_key
    still = store.get_resource(page_key)
    assert still["lifecycle"] == "closed"
    assert int(still["live"] or 0) == 0
    assert still["title"] == "Kept"
    assert still["tab_id"] == NATIVE_TAB_ID


def test_same_url_pages_keep_exact_binding_identity(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    _own_execution(monkeypatch)
    _owner_a, binding_a, page_a = _bind_owner("win", NATIVE_TAB_ID, "target-a")
    _owner_b, binding_b, page_b = _bind_owner("win", OTHER_TAB_ID, "target-b")
    assert page_a != page_b
    url = "https://example.com"
    registry = _registry(lambda: _FrameController(
        tab_id=NATIVE_TAB_ID, target_id="target-a", url=url, title="A",
    ))
    other = _registry(lambda: _FrameController(
        tab_id=OTHER_TAB_ID, target_id="target-b", url=url, title="B",
    ))
    first = registry.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="owner-a", binding_id=binding_a, page_key=page_a,
        page_context={
            "surfaces": [
                {"binding_id": binding_b, "window_id": "win", "tab_id": OTHER_TAB_ID},
                {"binding_id": binding_a, "window_id": "win", "tab_id": NATIVE_TAB_ID},
            ],
        },
    )
    second = other.execute(
        command="observe", backend="open_claude_chrome",
        owner_id="owner-b", binding_id=binding_b, page_key=page_b,
        page_context={
            "surfaces": [
                {"binding_id": binding_a, "window_id": "win", "tab_id": NATIVE_TAB_ID},
                {"binding_id": binding_b, "window_id": "win", "tab_id": OTHER_TAB_ID},
            ],
        },
    )
    assert first.get("ok") is not False
    assert second.get("ok") is not False
    store = BrowserResourceStore()
    row_a = store.get_resource(page_a)
    row_b = store.get_resource(page_b)
    assert row_a["tab_id"] == NATIVE_TAB_ID
    assert row_b["tab_id"] == OTHER_TAB_ID
    assert row_a["target"] == url
    assert row_b["target"] == url
    registry.close_all()
    other.close_all()


@pytest.mark.parametrize("stale_generation", [0, 1])
def test_retain_zero_or_older_epoch_cannot_overwrite_display(
    tmp_path, monkeypatch, stale_generation,
):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:epoch", window_id="win", tab_id=NATIVE_TAB_ID,
        title="Current", target="https://current.test/",
        connection_generation=4, live=True,
    )
    store.retain(
        page_key="page:epoch", window_id="win-stale", tab_id=OTHER_TAB_ID,
        title="Stale", target="https://stale.test/",
        connection_generation=stale_generation, live=True,
    )
    row = store.get_resource("page:epoch")
    assert int(row["connection_generation"] or 0) == 4
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/"
    assert row["tab_id"] == NATIVE_TAB_ID
    assert row["window_id"] == "win"


def test_update_display_omitted_zero_or_older_cannot_overwrite_known_epoch(
    tmp_path, monkeypatch,
):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(
        page_key="page:epoch", window_id="win", tab_id=NATIVE_TAB_ID,
        title="Current", target="https://current.test/",
        connection_generation=4, live=True,
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
        connection_generation=0,
    )
    store.update_display(
        "page:epoch", title="Stale", target="https://stale.test/",
        connection_generation=1,
    )
    row = store.get_resource("page:epoch")
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/"
    store.update_display(
        "page:epoch", title="Newer", target="https://newer.test/",
        connection_generation=4,
    )
    row = store.get_resource("page:epoch")
    assert row["title"] == "Newer"
    assert row["target"] == "https://newer.test/"


def test_blank_epoch_display_initialization_still_allowed(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    store = BrowserResourceStore(tmp_path / "session-resources.db")
    store.retain(page_key="page:blank", live=True)
    store.update_display(
        "page:blank", title="Initial", target="https://init.test/",
    )
    row = store.get_resource("page:blank")
    assert row["title"] == "Initial"
    assert row["target"] == "https://init.test/"
    store.retain(
        page_key="page:blank", title="Later", target="https://later.test/",
        connection_generation=3, live=True,
    )
    row = store.get_resource("page:blank")
    assert int(row["connection_generation"] or 0) == 3
    assert row["title"] == "Later"
    store.update_display(
        "page:blank", title="Stale", target="https://stale.test/",
    )
    row = store.get_resource("page:blank")
    assert row["title"] == "Later"


def test_surface_context_capture_carries_binding_epoch(tmp_path, monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.browser_resources import BrowserResourceStore

    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)

    def request_on_ws(_ws, command, timeout=5.0):
        del command, timeout
        return {
            "ok": True,
            "window_id": "win",
            "tab_id": NATIVE_TAB_ID,
            "target_id": "target-1",
            "url": "https://current.test/page",
            "title": "Current",
        }

    monkeypatch.setattr(webtab, "request_on_ws", request_on_ws)
    owner = object()
    context = surface_context.capture({
        "version": 1,
        "window_id": "win",
        "tab_id": NATIVE_TAB_ID,
        "region": "center",
        "access": "enabled",
    }, owner)
    surface = context["surfaces"][0]
    page_key = surface["page_key"]
    binding_id = surface["binding_id"]
    generation = _binding_generation(binding_id)
    assert generation > 0
    store = BrowserResourceStore()
    row = store.get_resource(page_key)
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/page"
    assert int(row["connection_generation"] or 0) == generation
    store.retain(
        page_key=page_key, title="Stale", target="https://stale.test/",
        connection_generation=0, live=True,
    )
    store.retain(
        page_key=page_key, title="Stale", target="https://stale.test/",
        connection_generation=1, live=True,
    )
    store.update_display(
        page_key, title="Stale", target="https://stale.test/",
    )
    row = store.get_resource(page_key)
    assert row["title"] == "Current"
    assert row["target"] == "https://current.test/page"
    assert int(row["connection_generation"] or 0) == generation
    webtab.release_binding(binding_id)
